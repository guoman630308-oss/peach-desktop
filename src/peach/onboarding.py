"""首次运行问答：问什么、怎么校验、写出什么样的设置文件。

这里只有纯逻辑，不碰终端：`peach init` 的命令行问答是它的第一个前端，托盘首启打开的
设置页（`routes_pages`）是第二个，两边都调同一组函数——`questions()` 给出题目、默认值
和校验器，`interview()` 用注入的 `ask` 把题目走一遍，`configure()` 把答案落成
`PeachConfig`，`apply()` 把它变成一台能直接起服务的机器（目录、账本、CA、口令、设置
文件）。前端只负责把字串送进来、把结果和错误显示出去。

终端问答收集 Peach 数据目录、本地媒体目录、监听范围、端口和局域网名字。网页引导另按每个目录收集
媒体来源与实际目录，校验和内部路径配置由 `media_configuration` 负责。复制、writer
镜像与 SMB 保持关闭或留空，设置文件只写用户声明过的来源。

数据库路径统一使用 Windows 格式，配置区分数据库路径与实际目录：

- Windows：盘符本身就是挂载点，`[media.locations] local = <这些目录>`，`[media.mounts]` 留空。
- macOS、Linux：`[media.locations]` 保存数据库路径前缀，`[media.mounts]` 保存实际目录，
  两表按来源和顺序对应。`peach.platform` 在读取文件前转换路径，`peach.scan` 在登记时反向转换。
  网页保存时自动补齐内部路径；终端本地目录使用 `R:\\media`、`R:\\media2` 等前缀。

终端问答的目录归 `local`；网页引导用 `Answers.media_sources` 保存逐行选择。
"""
from __future__ import annotations

import os
import re
import socket
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath

from . import auth, certs, settings_file
from .migrations import upgrade
from .platform import is_windows_path
from .settings_file import PeachConfig

#: 提问函数：(题目, 默认值) -> 用户输入的原文。空串表示接受默认值。
Ask = Callable[[str, str], str]

LOOPBACK_HOST = "127.0.0.1"
LAN_HOST = "0.0.0.0"
#: 每题最多问几次。三次都不是有效值就放弃，不能让人困在一个死循环里。
MAX_ATTEMPTS = 3
#: 非 Windows 终端设置使用的数据库路径前缀；实际目录保存在 `[media.mounts] local`。
#: 第二个目录起在后面加序号：`R:\media2`。
POSIX_LOCAL_DECLARED_ROOT = r"R:\media"
#: 本地来源的 ID。所有本地媒体目录都是 `asset.location = 'local'`。
LOCAL_LOCATION = "local"
#: 命令行里追加目录的题面：空答案就是「不加了」。
MORE_MEDIA_PROMPT = "再加一个媒体目录（不加就直接回车）"
#: 迁移随代码走，不随数据走。这里不 import `peach.config`：那个模块的常量在进程启动
#: 那一刻就定型了，而首次设置正是要改变它们指向的 Peach 数据目录。
MIGRATIONS_DIR = settings_file.PROJECT_ROOT / "migrations"
#: 「现在扫描」的一次性标记，落在 `<Peach 数据目录>/state/`。设置页写它、托盘消费它：
#: 设置页所在的引导服务在设置完成的那一刻就会被托盘停掉，扫描不能跑在它的进程里。
SCAN_REQUEST_NAME = "first-scan.request"
RELOAD_NAME = "configuration-reload.request"

#: 监听范围的两个选项：(提交值, 说明)。题目文本和设置页的下拉都由它生成，
#: 所以两处永远是同一批选项，改一个不会漏掉另一个。
HOST_OPTIONS = (("1", "只有这台电脑"), ("2", "同一局域网的设备"))
#: 首扫题的题面。CLI 在后面接 `(Y/n)`，设置页把它当勾选框的标签。
SCAN_PROMPT = "现在扫描 {target}？"

_DNS_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_HOST_CHOICES = {
    "1": LOOPBACK_HOST, "本机": LOOPBACK_HOST, LOOPBACK_HOST: LOOPBACK_HOST,
    "2": LAN_HOST, "局域网": LAN_HOST, LAN_HOST: LAN_HOST,
}
_YES = {"", "y", "yes", "是"}
_NO = {"n", "no", "否"}


class OnboardingAborted(RuntimeError):
    """一题没有问出有效答案：连续给错、或者输入结束。消息以句号结尾，前端接着说后果。"""


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str
    default: str
    #: 原文 -> 规范值；不合格就抛 `ValueError`，消息直接给人看。
    validate: Callable[[str], object]


@dataclass(frozen=True)
class Answers:
    data_root: Path
    #: 至少一个，都是已经解析过的绝对路径；互不重复，也互不包含。
    media_dirs: tuple[Path, ...]
    host: str
    port: int
    mdns_name: str
    media_sources: list[dict[str, str]] | None = None


# --------------------------------------------------------------------------- 校验

def validate_data_root(raw: str) -> Path:
    text = raw.strip()
    if not text:
        raise ValueError("Peach 数据目录不能为空")
    path = Path(text).expanduser()
    if path.is_file():
        raise ValueError(f"{path} 是一个文件，Peach 数据目录要是目录")
    return path


def media_dir_validator(*, windows: bool) -> Callable[[str], Path]:
    def validate(raw: str) -> Path:
        text = raw.strip()
        if not text:
            raise ValueError("媒体目录不能为空")
        path = Path(text).expanduser()
        if not path.is_dir():
            raise ValueError(f"目录不存在，或者不是目录：{path}。先建好再填。")
        # Windows 媒体目录保留盘符；挂载盘的最终路径可能是 UNC。
        resolved = Path(os.path.abspath(path)) if windows else path.resolve()
        if windows and not is_windows_path(str(resolved)):
            raise ValueError(f"{resolved} 不是盘符路径；网络位置先映射成盘符再来")
        return resolved
    return validate


def check_media_dirs(dirs: Sequence[Path]) -> dict[int, str]:
    """几个媒体目录之间不能重复、不能一个套在另一个里面。

    返回「下标 → 原因」，两个前端各自决定错误写在哪一行。套着的目录会让同一个文件被
    登记两次，所以和重复一样拒绝，而不是悄悄合并。
    """
    problems: dict[int, str] = {}
    for index, path in enumerate(dirs):
        for other_index, other in enumerate(dirs):
            if other_index == index or index in problems:
                continue
            if path == other:
                if other_index < index:
                    problems[index] = f"和上面的 {other} 重复了"
            elif path.is_relative_to(other):
                problems[index] = f"已经在 {other} 里面，不用再加"
    return problems


def read_media_dirs(
    raws: Sequence[str], *, validate: Callable[[str], Path], default: str = "",
) -> tuple[list[Path], list[str]]:
    """页面表单里的几行媒体目录：逐行校验，再查行与行之间的关系。

    第一行留空取默认值，后面的空行当没填。返回 `(合格的路径, 每行的错误)`；错误列表与
    提交的行一一对应，全对时为空列表，页面据此把原因写回出错的那一行底下。
    """
    rows = list(raws) or [""]
    kept = [(index, raw) for index, raw in enumerate(rows) if index == 0 or raw.strip()]
    paths: list[Path] = []
    problems = [""] * len(rows)
    failed = False
    for index, raw in kept:
        try:
            paths.append(validate(raw if raw.strip() else default))
        except ValueError as exc:
            problems[index] = str(exc)
            failed = True
    if not failed:
        for offset, why in check_media_dirs(paths).items():
            problems[kept[offset][0]] = why
            failed = True
    return paths, (problems if failed else [])


def more_media_dir_validator(
    *, windows: bool, taken: Sequence[Path],
) -> Callable[[str], Path | None]:
    """追加目录：空答案表示不加了，否则和第一个目录同一套校验，再查与已有目录的关系。"""
    single = media_dir_validator(windows=windows)

    def validate(raw: str) -> Path | None:
        if not raw.strip():
            return None
        path = single(raw)
        problems = check_media_dirs((*taken, path))
        if problems:
            raise ValueError(problems[len(taken)])
        return path
    return validate


def validate_host(raw: str) -> str:
    key = raw.strip().lower()
    try:
        return _HOST_CHOICES[key]
    except KeyError:
        raise ValueError("填 1（只有这台电脑）或 2（同一局域网的设备）") from None


def validate_port(raw: str) -> int:
    text = raw.strip()
    if not text.isdigit() or not 1 <= int(text) <= 65535:
        raise ValueError("端口要是 1 到 65535 之间的整数")
    return int(text)


def check_available_port(port: int, current: int) -> None:
    if port == current:
        return
    try:
        with socket.socket() as candidate:
            candidate.bind(("127.0.0.1", port))
    except OSError as exc:
        raise ValueError("这个端口已被占用，请换一个端口") from exc


def validate_mdns_name(raw: str) -> str:
    text = raw.strip()
    if not _DNS_LABEL.match(text):
        raise ValueError("名字只能用字母、数字和连字符，不能以连字符开头或结尾")
    return text


def validate_yes_no(raw: str) -> bool:
    key = raw.strip().lower()
    if key in _YES:
        return True
    if key in _NO:
        return False
    raise ValueError("填 y 或 n")


# --------------------------------------------------------------------------- 题目

def default_media_dir(home: Path, *, windows: bool) -> Path | None:
    """系统自带的视频目录存在就拿来当默认值；不存在就没有默认，必须手填。"""
    candidate = home / ("Videos" if windows else "Movies")
    return candidate if candidate.is_dir() else None


def questions(
    config: PeachConfig, *, windows: bool, home: Path | None = None,
) -> tuple[Question, ...]:
    """按顺序给出全部题目。默认值来自当前解析到的配置，和非交互 `peach init` 同一口径。"""
    home = Path.home() if home is None else home
    media_default = default_media_dir(home, windows=windows)
    return (
        Question("data_root", "数据目录（Peach 数据库、缓存和设置文件都放在这里）",
                 str(config.data_root), validate_data_root),
        Question("media_dir", "媒体目录（必须已经存在，可以在外置硬盘上）",
                 str(media_default) if media_default else "",
                 media_dir_validator(windows=windows)),
        Question("host",
                 "谁可以访问：" + "，".join(f"{value} = {label}" for value, label in HOST_OPTIONS),
                 "2", validate_host),
        Question("port", "端口", str(config.server.port), validate_port),
        Question("mdns_name", "局域网访问地址（<名字>.local，只在允许局域网访问时发布）",
                 config.server.mdns_name, validate_mdns_name),
    )


def scan_target_text(media_dirs: Sequence[Path | str]) -> str:
    """首扫题里的对象：一个目录就写它的路径，几个就写数目。"""
    if len(media_dirs) == 1:
        return str(media_dirs[0])
    return f"这 {len(media_dirs)} 个文件夹"


def scan_question(media_dirs: Sequence[Path | str]) -> Question:
    return Question("scan_now", SCAN_PROMPT.format(target=scan_target_text(media_dirs)) + "(Y/n)",
                    "Y", validate_yes_no)


def more_media_dir_question(*, windows: bool, taken: Sequence[Path]) -> Question:
    return Question("media_dir", MORE_MEDIA_PROMPT, "",
                    more_media_dir_validator(windows=windows, taken=taken))


def ask_until_valid(
    question: Question, ask: Ask, *, report: Callable[[str], None] = print,
    max_attempts: int = MAX_ATTEMPTS,
) -> object:
    """问到拿到合格答案为止，最多 `max_attempts` 次；空输入取默认值。"""
    for attempt in range(1, max_attempts + 1):
        raw = ask(question.prompt, question.default)
        candidate = raw if raw.strip() else question.default
        try:
            return question.validate(candidate)
        except ValueError as exc:
            remaining = max_attempts - attempt
            report(f"  {exc}" + (f"（还可以再试 {remaining} 次）" if remaining else ""))
    raise OnboardingAborted(f"「{question.prompt}」连续 {max_attempts} 次没有拿到有效值。")


def collect(
    config: PeachConfig, ask: Ask, *, windows: bool, home: Path | None = None,
    report: Callable[[str], None] = print, max_attempts: int = MAX_ATTEMPTS,
) -> Iterator[tuple[str, object]]:
    """逐题产出 `(Answers 的字段名, 值)`。`ask` 由前端注入，测试用脚本化答案驱动。

    媒体目录问完第一个之后接着问「再加一个」，回车即结束，整组目录作为 `media_dirs`
    一次产出；页面上对应的是「添加文件夹」。做成生成器是为了让命令行在 Peach 数据目录那一题
    之后就能判「设置文件已存在」并退出，不必等剩下的题答完。
    """
    for question in questions(config, windows=windows, home=home):
        value = ask_until_valid(question, ask, report=report, max_attempts=max_attempts)
        if question.key != "media_dir":
            yield question.key, value
            continue
        media_dirs: list[Path] = [value]  # type: ignore[list-item]
        while True:
            more = ask_until_valid(more_media_dir_question(windows=windows, taken=media_dirs),
                                   ask, report=report, max_attempts=max_attempts)
            if more is None:
                break
            media_dirs.append(more)  # type: ignore[arg-type]
        yield "media_dirs", tuple(media_dirs)


def interview(
    config: PeachConfig, ask: Ask, *, windows: bool, home: Path | None = None,
    report: Callable[[str], None] = print, max_attempts: int = MAX_ATTEMPTS,
) -> Answers:
    """把全部题目走一遍，落成 `Answers`。"""
    return Answers(**dict(collect(  # type: ignore[arg-type]
        config, ask, windows=windows, home=home, report=report, max_attempts=max_attempts)))


# --------------------------------------------------------------------------- 落盘

def configure(config: PeachConfig, answers: Answers, *, windows: bool) -> PeachConfig:
    """把答案落成一份可写回的设置。`config` 要按 `answers.data_root` 重新解析过。

    只写用户声明过的来源；网页答案包含逐行来源及映射，终端答案按本地目录处理。
    复制保持关闭，writer 镜像与 SMB 坐标留空，目录键全部落成显式值——和非交互
    `peach init` 同一形状。
    """
    if windows:
        locations = {LOCAL_LOCATION: tuple(str(path) for path in answers.media_dirs)}
        mounts: dict[str, tuple[str, ...]] = {}
    else:
        locations = {LOCAL_LOCATION: posix_declared_roots(len(answers.media_dirs))}
        mounts = {LOCAL_LOCATION: tuple(str(path) for path in answers.media_dirs)}
    if answers.media_sources is not None:
        from . import media_configuration
        sources = media_configuration.prepare_sources(answers.media_sources, windows=windows, config=config)
        locations, mounts, errors = media_configuration.validate(sources, windows=windows, config=config)
        if errors:
            raise ValueError("；".join(error for error in errors if error))
    prepared = settings_file.capture_existing(
        config, replication_enabled=False,
        overrides={"host": answers.host, "port": answers.port,
                   "mdns_name": answers.mdns_name},
        mounts=mounts,
    )
    library_names = dict(config.library_names)
    if answers.media_sources is not None:
        for row in sources:
            if row.get("library"):
                library_names[str(PureWindowsPath(row["path"] if windows else row["root"]))] = str(row["library"])
    return replace(prepared, locations=locations, library_names=library_names,
                   media_costs=(media_configuration.costs(sources, config, windows=windows)
                                if answers.media_sources is not None else dict(config.media_costs)))


@dataclass(frozen=True)
class DataTree:
    """`create_data_tree()` 做了什么。前端自己决定这些事实怎么说出来。"""

    data_root: Path
    database: Path
    #: 这次应用了几个迁移。账本本来就在时是 0。
    migrations: int
    ledger_existed: bool
    token_path: Path
    token_created: bool
    #: 没有 openssl 时 CA 生成失败，`ca_cert` 为 None、`ca_error` 是原因。
    ca_cert: Path | None
    ca_error: str | None


@dataclass(frozen=True)
class Applied:
    """一次完整的首次设置：目录、账本、CA、口令、设置文件都已落盘。"""

    config: PeachConfig
    settings_path: Path
    tree: DataTree


def resolve_config(
    data_root: Path | None = None, environ: dict[str, str] | None = None,
) -> tuple[PeachConfig, settings_file.SettingsFileError | None]:
    """按给定 Peach 数据目录重新解析设置；文件坏了就退回内建默认并把错误一并交出。

    `peach init` 与设置页都要在拿到 Peach 数据目录之后重新解析一次：Peach 数据目录决定设置文件在哪，
    而进程启动时那一份是按发现顺序算的，用户完全可以填一个别的目录。
    """
    environ = dict(os.environ if environ is None else environ)
    if data_root is not None:
        environ["PEACH_DATA_ROOT"] = str(data_root)
    try:
        return settings_file.load_config(environ=environ), None
    except settings_file.SettingsFileError as exc:
        return settings_file.load_config(environ=environ, strict=False), exc


def create_data_tree(config: PeachConfig) -> DataTree:
    """建目录、把库迁到最新、生成本机 CA、确保口令存在。已经有账本就不碰它。

    只做事、不打印：`peach init` 把结果写到终端，设置页把同一批结果写进成功页。
    """
    for key in settings_file.DIRECTORY_KEYS:
        config.directory(key).mkdir(parents=True, exist_ok=True)
    tls_dir = config.directory("secrets") / "tls"
    tls_dir.mkdir(parents=True, exist_ok=True)

    database = config.directory("database") / "ledger.db"
    existed = database.is_file()
    # 已有账本却在跑全新 init：不迁移、不备份、不覆盖，让人自己决定。
    applied = 0 if existed else len(upgrade(database, MIGRATIONS_DIR))

    ca_cert: Path | None = None
    ca_error: str | None = None
    try:
        ca_cert = certs.bootstrap_certificates(
            tls_dir, config.server.mdns_name + ".local").ca_cert
    except (RuntimeError, OSError) as exc:
        # 没有 openssl 不该挡住初始化：HTTP 本地访问照样能用，装 openssl 后重跑即可。
        ca_error = str(exc)

    secrets_dir = config.directory("secrets")
    _token, created = auth.ensure_token(secrets_dir)
    return DataTree(
        data_root=config.data_root, database=database, migrations=applied,
        ledger_existed=existed, token_path=auth.token_path(secrets_dir),
        token_created=created, ca_cert=ca_cert, ca_error=ca_error,
    )


def apply(
    config: PeachConfig, answers: Answers, *, windows: bool, force: bool = False,
    access_password: str = "",
) -> Applied:
    """把答案变成一台可以直接起服务的机器：目录、账本、CA、口令、设置文件。

    `config` 要按 `answers.data_root` 重新解析过（`resolve_config`）。CLI 问答和托盘的
    设置页都只调这一个函数，两条前端因此不可能在「首次运行到底做了什么」上分叉。
    """
    prepared = configure(config, answers, windows=windows)
    tree = create_data_tree(prepared)
    from . import access
    access_path = prepared.directory("secrets") / "access.json"
    if not config.present and not access_path.exists():
        access.save(access_path, access_password)
    path = settings_file.write(prepared, force=force)
    return Applied(config=prepared, settings_path=path, tree=tree)


def scan_request_path(config: PeachConfig) -> Path:
    return config.directory("state") / SCAN_REQUEST_NAME


def request_first_scan(config: PeachConfig, location: str = LOCAL_LOCATION) -> Path:
    """记下「用户要求首扫」。内容是来源 ID，托盘据此拉起 `peach scan <来源>`。"""
    path = scan_request_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(location, encoding="utf-8")
    return path


def take_first_scan_request(config: PeachConfig) -> str | None:
    """读走首扫标记并删掉它，没有就返回 None。只消费一次：删除在读取之后立刻发生，
    托盘的健康轮询每几秒跑一轮，留着它等于每一轮都再扫一遍整个媒体目录。
    """
    path = scan_request_path(config)
    try:
        location = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        path.unlink()
    except OSError:
        return None
    return location or None


def posix_declared_roots(count: int) -> tuple[str, ...]:
    """为非 Windows 终端设置的本地目录生成数据库路径前缀：`R:\\media`、`R:\\media2`……"""
    return tuple(POSIX_LOCAL_DECLARED_ROOT + ("" if index == 0 else str(index + 1))
                 for index in range(count))


def scan_roots(config: PeachConfig) -> tuple[str, ...]:
    """取得本地来源的全部数据库路径前缀；扫描时由 `peach.scan` 转成实际目录。"""
    return tuple(config.locations[LOCAL_LOCATION])


def mounts_explanation(media_dirs: Sequence[Path | str] | Path | str) -> str:
    """说明非 Windows 终端设置保存的两种路径。"""
    if isinstance(media_dirs, (Path, str)):
        media_dirs = (media_dirs,)
    shown = "、".join(str(path) for path in media_dirs) or "（还没填）"
    roots = "、".join(posix_declared_roots(max(len(media_dirs), 1)))
    return (f"Peach 数据库用 Windows 格式保存影片路径，前缀为 {roots}；"
            f"实际媒体目录为 {shown}，按相同顺序保存在 [media.mounts] local，读取文件前自动转换。")


def console_ask(prompt: str, default: str) -> str:
    """终端前端：把默认值放在方括号里，回车即接受；输入结束视为放弃。"""
    suffix = f" [{default}]" if default else "（必填）"
    try:
        return input(f"{prompt}{suffix}: ")
    except EOFError:
        raise OnboardingAborted("输入已结束。") from None


def is_interactive(stdin) -> bool:
    """stdin 是不是一个真的终端。管道、重定向和没有 stdin 的打包进程都不是。"""
    try:
        return bool(stdin is not None and stdin.isatty())
    except (AttributeError, ValueError, OSError):
        return False

