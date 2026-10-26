from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from . import auth, distribution, onboarding, scan, settings_file
from .api import create_app
from .config import (
    CONFIGURED,
    DATABASE_PATH,
    MDNS_NAME,
    MIGRATIONS_DIR,
    REPLICATION_ENABLED,
    SERVE_HOST,
    SERVE_PORT,
    SETTINGS_ERROR,
    SHARED_DATABASE_PATH,
    STATE_DIR,
    CLOUDFLARED_BINARY,
    TUNNEL_ENABLED,
    TUNNEL_HOSTNAME,
    TUNNEL_MODE,
    TUNNEL_TOKEN,
    PeachSettings,
)
from .migrations import plan, upgrade
from .platform import location_mounts
from .scripting import open_readonly
from .sync import LedgerSync, device_id
from .sync import plan as sync_plan
from .sync import writer_device
from . import tunnel


DEFAULT_DB = DATABASE_PATH

#: 只监听这些地址时，服务在局域网上不可达。
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def _is_loopback(host: str) -> bool:
    return (host or "").strip().lower() in _LOOPBACK_HOSTS


def _require_readable_settings() -> None:
    """设置文件在但读不出来时，明确拒绝。

    这种情况下 `config` 里那份是退回的内建默认——Peach 数据目录仍然对（发现顺序不看文件内容），
    但端口、mDNS 名、复制坐标全是通用值。拿它启动会得到一个「看起来正常」的服务，
    比直接失败难查得多。
    """
    if SETTINGS_ERROR is not None:
        raise SystemExit(
            f"{SETTINGS_ERROR}\n修好这个文件，或者用 `peach init --force` 重新生成。")


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    _require_readable_settings()
    if args.redirect_origin:
        from .redirect import create_redirect_app
        if args.ssl_certfile or args.ssl_keyfile:
            raise SystemExit("redirect listener must use HTTP")
        uvicorn.run(create_redirect_app(args.redirect_origin), host=args.host,
                    port=args.port, workers=1)
        return 0
    if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
        raise SystemExit("--ssl-certfile and --ssl-keyfile must be provided together")
    for path in (args.ssl_certfile, args.ssl_keyfile):
        if path is not None and not path.is_file():
            raise SystemExit(f"TLS file not found: {path}")
    tls_enabled = args.ssl_certfile is not None
    # 绑在回环上就不能发布 mDNS：广播出去的是本机的局域网地址，而那个地址上没有
    # 任何东西在监听，`peach.local` 于是变成一个必然连不上的名字。更糟的是开发机
    # 会就此抢占生产用的 `peach.local`，把同一局域网里的真实实例挤掉。
    publish_mdns = not args.no_mdns and not _is_loopback(args.host)
    if not args.no_mdns and not publish_mdns:
        print(
            f"mDNS 未发布：--host {args.host} 只允许本机访问，"
            f"发布 peach.local 会指向一个连不上的地址。"
            f"需要局域网访问就用 --host 0.0.0.0。"
        )
    # `--setup` 是托盘首启拉起的引导服务：只提供首次运行表单，不认发现到的 Peach 数据目录，
    # 也不要口令。口令刻意置空而不是读文件——表单一提交就会生成一份口令，那之后要是
    # 有人重启这条引导服务，读到新口令的它会把还没看到口令的用户关在门外。
    # 安全边界由绑定地址给：这条服务只监听回环（`build_setup_service_specs`）。
    tunnel_lan_address = None
    if not distribution.standalone() and not _is_loopback(args.host) and (publish_mdns or TUNNEL_ENABLED):
        from .mdns import lan_ipv4
        tunnel_lan_address = args.mdns_address or lan_ipv4()
    settings = PeachSettings(
        db_path=args.db,
        host=args.host,
        port=args.port,
        token="" if args.setup else _serve_token(args),
        access_path=settings_file.active().directory("secrets") / "access.json",
        configured=CONFIGURED and not args.setup,
        docs_enabled=args.docs,
        mdns_enabled=publish_mdns,
        mdns_name=args.mdns_name,
        mdns_port=args.port,
        mdns_address=args.mdns_address,
        tls_enabled=tls_enabled,
        tunnel_enabled=TUNNEL_ENABLED,
        tunnel_binary=CLOUDFLARED_BINARY,
        tunnel_mode=TUNNEL_MODE,
        tunnel_token=TUNNEL_TOKEN,
        tunnel_hostname=TUNNEL_HOSTNAME,
        tunnel_standalone=distribution.standalone(),
        tunnel_lan_address=tunnel_lan_address,
        tunnel_origin_port=(args.port if distribution.standalone() or tls_enabled else None),
        tunnel_state_root=settings_file.active().directory("state"),
        tunnel_log_root=settings_file.active().directory("logs"),
    )
    sync = _build_sync(args, settings)
    uvicorn.run(
        create_app(settings, sync), host=args.host, port=args.port, workers=1,
        ssl_certfile=str(args.ssl_certfile) if args.ssl_certfile else None,
        ssl_keyfile=str(args.ssl_keyfile) if args.ssl_keyfile else None,
    )
    return 0


def _serve_token(args: argparse.Namespace) -> str:
    """取这次启动要用的口令，并在绑局域网却没有口令时拒绝启动。

    拒绝而不是告警：托盘在后台起服务，告警不会有人看见，服务却已经把整个馆藏和写接口
    摆在同网段上了。回环地址不拦——那时只有本机进程够得着，与文件系统同级。

    `--no-auth` 让本机开发跳过登录页，同一条回环判据照样拦：它只影响这次启动，
    不读也不改口令文件，所以托盘起的那份服务仍然要口令。
    """
    if getattr(args, "no_auth", False):
        if not _is_loopback(args.host):
            raise SystemExit(
                f"拒绝启动：--no-auth 仅允许监听本机地址时使用，而 --host {args.host} 不是。\n"
                f"在局域网地址上关掉登录，等于把整个馆藏和写接口交给同网段的任何设备。"
            )
        # 直接写而不是 setdefault：走到这里就是这次启动确实要开调试口径，
        # 继承来的旧值不该把它顶掉，反过来也不该由它决定别的进程算不算调试。
        os.environ["PEACH_DEV"] = "1"
        return ""
    secrets_dir = settings_file.active().directory("secrets")
    token = auth.resolve_token(args.token, secrets_dir)
    if token or _is_loopback(args.host):
        return token
    raise SystemExit(
        f"拒绝启动：--host {args.host} 允许其他设备访问，而这台机器还没有访问口令。\n"
        f"这样起服务，同网段的任何设备都能读整个馆藏并调用写接口。\n"
        f"先跑 `peach token` 生成并查看口令（存在 {auth.token_path(secrets_dir)}），"
        f"再重新启动；只给本机用就改成 --host 127.0.0.1。"
    )


def _build_sync(args: argparse.Namespace, settings: PeachSettings) -> LedgerSync | None:
    """建立只读角色观察器；服务启动与浏览绝不触发跨机复制。

    冲突不自动挑边：两台机器都写过之后没有安全的合并规则，服务照常起但转只读，
    由人选一边（把要保留的那份复制成另一份，或删掉一侧的 `.sync.json` 重新播种）。

    `replication.enabled = false` 时整条链路不装配：不建观察器、不探测共享目录。
    没有第二台机器就没有「读者」可言，服务按独立写者跑——写接口全开，只读闸门不生效
    （`sync is None` 时 `/healthz` 的 `ledger_sync` 是 `disabled`，不是 `writer`）。
    """
    if not REPLICATION_ENABLED:
        print("数据库角色：disabled · replication.enabled = false，允许本机独立修改数据库")
        return None
    sync = LedgerSync(
        settings.db_path, args.shared_db, device_id(STATE_DIR),
        interval=0,
    )
    decision = sync.observe()
    print(f"数据库角色：{sync.status} · {sync.detail}")
    if decision.conflict:
        print(
            "  服务转只读。请人工选定一份数据库："
            f"本地 {settings.db_path}，共享 {args.shared_db}。"
        )
    return sync

def _migrate(args: argparse.Namespace) -> int:
    _require_readable_settings()
    all_migrations, pending = plan(args.db, MIGRATIONS_DIR)
    print(f"database: {args.db}")
    print(f"migrations: {len(all_migrations)}, pending: {len(pending)}")
    for migration in pending:
        print(f"PENDING {migration.version} {migration.name}")
    if args.action == "status" or not pending:
        return 0
    if args.db.resolve() == DEFAULT_DB.resolve() and not args.yes:
        raise SystemExit("refusing to modify the real ledger without --yes")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = args.db.with_name(f"{args.db.stem}.pre-migrate-{stamp}{args.db.suffix}")
    done = upgrade(args.db, MIGRATIONS_DIR, backup)
    print(f"backup: {backup}")
    print("applied: " + ", ".join(item.version for item in done))
    return 0


#: 「加工进度」的分母：能落到本机的视频。在线资产没有文件，探测和九宫格对它们
#: 不适用，混进分母只会让每一行的百分比永远到不了 100。
_PROCESSED_SCOPE = "medium='video' AND location!='online'"
_PROCESSED_MEASURES = (
    ("有时长 (ffprobe)", "duration IS NOT NULL"),
    ("有九宫格", "snapshot_path IS NOT NULL"),
    ("有哈希", "hash IS NOT NULL"),
    ("有创作者归属", "creator IS NOT NULL AND creator!=''"),
    ("有消费记录", "play_count>0 OR rating IS NOT NULL"),
)


def _status(args: argparse.Namespace) -> int:
    """一屏看完账本、迁移和同步状态。只读，随时可跑。

    这里刻意不报后台任务：进程名和日志 mtime 都不是存活判据（长跑批处理各自持有
    pid 锁与进度文件），`scripts/job_status.py` 才是那件事的正式表面。
    """
    _require_readable_settings()
    if not args.db.is_file():
        print(f"数据库不存在：{args.db}")
        if not args.db.parent.is_dir():
            print("这台机器还没初始化过，先跑 `peach init`。")
        return 4
    connection = open_readonly(args.db)
    try:
        one = lambda sql: connection.execute(sql).fetchone()[0]      # noqa: E731
        print(f"数据库 {args.db}")
        for location, count, size in connection.execute(
                "SELECT location,COUNT(*),COALESCE(SUM(size),0) FROM asset "
                "GROUP BY 1 ORDER BY 3 DESC"):
            print(f"  {location or '?':<10}{count:>9,} 条{size / 1024 ** 4:>10.2f} TB")
        print(f"  {'合计':<10}{one('SELECT COUNT(*) FROM asset'):>9,} 条"
              f"{one('SELECT COALESCE(SUM(size),0) FROM asset') / 1024 ** 4:>10.2f} TB")

        print("\n  媒介构成：")
        for medium, count, size in connection.execute(
                "SELECT medium,COUNT(*),COALESCE(SUM(size),0) FROM asset "
                "GROUP BY 1 ORDER BY 2 DESC"):
            print(f"    {medium or '?':<14}{count:>8,}{size / 1024 ** 3:>11.1f} GB")

        total = one(f"SELECT COUNT(*) FROM asset WHERE {_PROCESSED_SCOPE}")
        print(f"\n  加工进度（本机视频 {total:,} 条）：")
        for label, condition in _PROCESSED_MEASURES:
            done = one(f"SELECT COUNT(*) FROM asset WHERE {_PROCESSED_SCOPE} "
                       f"AND ({condition})")
            percent = done / total * 100 if total else 0.0
            print(f"    {label:<18}{done:>8,} / {total:,}  {percent:5.1f}%  "
                  f"{'█' * int(percent / 4)}")

        print("\n  情境层：")
        for column, label in (("ctx_length", "时长档"), ("ctx_orient", "屏向"),
                              ("ctx_quality", "画质")):
            rows = connection.execute(
                f"SELECT {column},COUNT(*) FROM asset WHERE {column} IS NOT NULL "
                "GROUP BY 1 ORDER BY 2 DESC").fetchall()
            if rows:
                print(f"    {label:<8}"
                      + "  ".join(f"{key}={value:,}" for key, value in rows))
    finally:
        connection.close()

    all_migrations, pending = plan(args.db, MIGRATIONS_DIR)
    print(f"\n  迁移：共 {len(all_migrations)} 个，待应用 {len(pending)} 个")
    for migration in pending:
        print(f"    PENDING {migration.version} {migration.name}")

    decision = sync_plan(args.db, args.shared_db)
    owner = writer_device(args.db, args.shared_db)
    # 只读取已有的 device-id，不像 `device_id()` 那样在缺失时顺手生成一个：
    # 状态命令生成标识会让「这一代是谁写的」多出一个从没写过库的名字。
    local_device = ""
    try:
        local_device = (STATE_DIR / "device-id").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    whose = "本机" if owner and owner == local_device else (owner or "未确定")
    print(f"  同步：{decision.action} · {decision.reason}；写入端 {whose}")
    return 0


def _ledger_sync(args: argparse.Namespace) -> int:
    sync = LedgerSync(args.db, args.shared_db, device_id(STATE_DIR), interval=0)
    decision = sync.take_ownership() if args.take_ownership else sync.synchronize_now()
    completed = {
        "pull": "已从共享副本拉取",
        "push": "已推送到共享副本",
        "local-ahead": "已推送到共享副本",
        "in-sync": "两侧已经一致",
        "take-ownership": "本机已成为唯一写入端",
        "disabled": decision.reason,
    }
    print(f"数据库同步：{decision.action} · {completed.get(decision.action, decision.reason)}")
    return {"conflict": 2, "offline": 3, "missing": 4}.get(decision.action, 0)


def _parse_mounts(
    raw: list[str] | None, locations: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, tuple[str, ...]]:
    """`--mount local=/Volumes/Media/media` 形式的挂载点；同一来源重复给就按顺序
    对应它的几个数据库路径前缀。

    键是 `asset.location`（`[media.locations]` 里声明过的来源 ID），不是盘符：
    打错一个 ID 如果静默收下，那个来源就会安静地进脱盘模式，所以这里直接拒绝。
    """
    known = dict(locations or {})
    mounts: dict[str, list[str]] = {}
    for chunk in raw or ():
        key, separator, value = chunk.partition("=")
        key = key.strip()
        if not separator or not key:
            raise SystemExit(f"--mount 要写成 来源ID=路径，收到：{chunk}")
        if known and key not in known:
            raise SystemExit(
                f"--mount 的来源 ID 未在 [media.locations] 声明：{key}"
                f"（已知：{'、'.join(sorted(known))}）"
            )
        mounts.setdefault(key, []).append(value.strip())
    return {key: tuple(item for item in values if item) for key, values in mounts.items()}


def _resolved_config(
    data_root: Path | None,
) -> tuple[settings_file.PeachConfig, settings_file.SettingsFileError | None]:
    """按当前环境解析一份配置，外加「旧文件读不出来」时的原始错误。

    `--data-root` 走和运行期同一条发现路径，不另开分支。读取用 `strict=False`
    （`init --force` 是坏文件的唯一自救入口），但错误必须留下来：静默退回内建默认
    再写回去，等于把旧文件里的坐标悄悄抹平。
    """
    return onboarding.resolve_config(data_root)


def _init(args: argparse.Namespace) -> int:
    """首次运行：建目录、建库、写设置文件、生成本机 CA。

    `--from-existing` 是给已经在跑的机器用的另一条路径：不建目录、不建库、不生成
    证书，只把当前生效的配置落成设置文件。现有部署的坐标散在环境变量和运行现场里，
    进程看不见的那几项（局域网地址、钥匙串账号名）由命令行显式补，落不下的会在末尾
    逐条列出来，绝不拿猜的值填。

    一个参数都没给、stdin 又是终端时进问答（`_init_interactive`）；给了任何参数、
    加了 `--no-input` 或 stdin 不是终端，都走下面这条非交互路径。
    """
    if _wants_interview(args):
        return _init_interactive()
    config, broken = _resolved_config(args.data_root)
    if config.path.exists() and not args.force:
        print(f"设置文件已存在：{config.path}")
        print("要重新生成就加 --force；它会覆盖这个文件，不动数据。")
        return 3
    if broken is not None:
        # 第一阶段的 `[media] R = ...` 在第二阶段是硬错误，于是 `--from-existing` 读到的
        # 「当前生效配置」其实是内建默认。不说这一句，重写出来的文件会安静地丢掉旧文件
        # 里的 mDNS 名、writer 地址和 SMB 坐标——那正是这条命令声称要保下来的东西。
        print(f"注意：{broken}")
        print("下面这份是按内建默认加环境变量重建的，**没有**继承旧文件里的值。")
        print("旧文件里自定义过的坐标要在这次命令里用对应参数重新给一遍。")

    overrides: dict[str, object] = {
        "host": args.host, "port": args.port, "mdns_name": args.mdns_name,
        "review_writer_origin": args.writer_origin,
        "review_writer_proxy": args.writer_proxy,
        "shared_root": str(args.shared_root) if args.shared_root else None,
        "smb_host": args.smb_host, "smb_share": args.smb_share,
        "smb_user": args.smb_user,
    }
    mounts = dict(config.mounts)
    mounts.update(_parse_mounts(args.mount, config.locations))
    prepared = settings_file.capture_existing(
        config, replication_enabled=args.from_existing,
        overrides=overrides, mounts=mounts,
    )

    if not args.from_existing:
        _create_data_tree(prepared)
    else:
        _announce_token(prepared)
    path = settings_file.write(prepared, force=args.force)
    print(f"设置文件：{path}")
    _report_blanks(prepared)
    _print_next_steps(prepared, from_existing=args.from_existing)
    return 0


def _wants_interview(args: argparse.Namespace) -> bool:
    """`peach init` 不带任何参数、stdin 是终端，才进问答。

    「不带任何参数」按 parser 的默认值判，不另抄一份参数名：加一个开关就多一处要同步。
    `--no-input` 本身就是一个参数，所以它天然落到非交互路径。
    """
    bare = build_parser().parse_args(["init"])
    return vars(args) == vars(bare) and onboarding.is_interactive(sys.stdin)


def _init_interactive(
    ask: onboarding.Ask = onboarding.console_ask, *,
    windows: bool | None = None, home: Path | None = None,
) -> int:
    """首次运行问答。题目、校验与落盘全在 `peach.onboarding`，这里只是终端前端。

    `ask`、`windows`、`home` 可注入：测试用脚本化答案驱动，不碰真实 stdin，也不看
    测试机自己是什么系统。托盘的设置页复用同一组函数，不走这个入口。
    """
    windows = os.name == "nt" if windows is None else windows
    config, _broken = _resolved_config(None)
    print("首次运行问答：每一题直接回车就接受方括号里的默认值。")
    collected: dict[str, object] = {}
    try:
        for index, (key, value) in enumerate(
                onboarding.collect(config, ask, windows=windows, home=home)):
            collected[key] = value
            if index == 0:
                # Peach 数据目录定了，设置文件在哪也就定了。这一题之后就判，别让人把剩下四题
                # 答完才听到「文件已存在」——那四个答案会被整份丢掉。
                config, _broken = _resolved_config(collected["data_root"])
                if config.path.exists():
                    print(f"设置文件已存在：{config.path}")
                    print("要重新生成就跑 `peach init --force`；它会覆盖这个文件，不动数据。")
                    return 3
    except onboarding.OnboardingAborted as exc:
        raise SystemExit(f"{exc}已退出，没有写任何文件。") from None
    answers = onboarding.Answers(**collected)  # type: ignore[arg-type]
    applied = onboarding.apply(config, answers, windows=windows)
    prepared = applied.config
    print(f"Peach 数据目录：{prepared.data_root}")
    _report_data_tree(applied.tree)
    print(f"设置文件：{applied.settings_path}")
    if not windows:
        print(onboarding.mounts_explanation(answers.media_dirs))

    summaries: list[str] = []
    try:
        scan_now = onboarding.ask_until_valid(onboarding.scan_question(answers.media_dirs), ask)
    except onboarding.OnboardingAborted as exc:
        print(f"{exc}跳过扫描，之后可以跑 `peach scan local`。")
        scan_now = False
    if scan_now:
        for root in onboarding.scan_roots(prepared):
            result = scan.scan_location(
                prepared.directory("database") / "ledger.db", onboarding.LOCAL_LOCATION,
                root, declared_roots=prepared.locations,
                mounts=prepared.mounts, report=print, windows=windows,
            )
            summaries.append(result.summary())
    _print_next_steps(prepared, from_existing=False)
    for summary in summaries:
        print(f"  扫描结果：{summary}")
    return 0


def _scan(args: argparse.Namespace) -> int:
    """`peach scan <来源ID> [根目录]`：根目录省略时逐个扫该来源的全部数据库路径前缀（本机目录按挂载表取）。"""
    _require_readable_settings()
    if not args.db.is_file():
        print(f"数据库不存在：{args.db}")
        print("这台机器还没初始化过，先跑 `peach init`。")
        return 4
    config = settings_file.active()
    mounts = {location: tuple(str(mount) for mount in points)
              for location, points in location_mounts().items()}
    try:
        if args.location == "configured" and args.root is None:
            from .platform import root_online, translate_ledger_path
            for location, declared in config.locations.items():
                for root in declared:
                    if not root_online(translate_ledger_path(root)):
                        print(f"{location}：挂载点离线，未扫描", flush=True)
                        continue
                    scan.scan_location(args.db, location, root, declared_roots=config.locations,
                                       mounts=mounts, report=lambda line: print(line, flush=True))
            return 0
        if args.root is None:
            if args.location not in config.locations:
                known = "、".join(sorted(config.locations)) or "（设置文件里一个都没有）"
                raise scan.ScanTargetError(
                    f"✗ 未声明的来源 {args.location!r}；[media.locations] 里已知：{known}")
            roots = tuple(config.locations[args.location])
        else:
            roots = (scan.ledger_root_for(
                args.location, args.root, declared_roots=config.locations, mounts=mounts),)
        for root in roots:
            scan.scan_location(args.db, args.location, root, declared_roots=config.locations,
                               mounts=mounts, report=lambda line: print(line, flush=True))
    except scan.ScanTargetError as exc:
        raise SystemExit(str(exc)) from None
    return 0


def _process_library(args: argparse.Namespace) -> int:
    """命令行跑资料处理，和网页那一轮一样记进任务中心、结算时派发声明的后继。

    后继写成 `pending` 行，由服务的后继通道执行：服务下次派发后继或重启时会领走。
    """
    from .library_processing import process_library
    from .repository import LedgerDatabase
    from .task_runs import cli_run, declared_rows
    _require_readable_settings()
    config = settings_file.active()
    with cli_run('library-processing', args.db, label='资料处理') as handle:
        result = process_library(config, args.db, config.directory('generated'),
                                 config.directory('generated') / 'covers', location=args.location,
                                 database=LedgerDatabase(args.db),
                                 report=lambda state: print(f"{state['stage']}：{state['checked']}/{state['total']}", flush=True))
        rows = declared_rows(result.get('followups'))
        queued = handle.store.enqueue_followups(handle.run_id, rows)
        if rows:
            print(f"后继：声明 {len(rows)} 条，入队 {len(queued['queued'])} 条，"
                  f"已在排队 {queued['duplicates']} 条", flush=True)
        complete = result['status'] == 'complete'
        handle.finish('succeeded' if complete else 'failed',
                      summary={'followups': len(queued['queued'])},
                      error='' if complete else str(result.get('error') or '资料处理未完成'))
    return 0 if complete else 1


def _create_data_tree(config: settings_file.PeachConfig) -> None:
    """建目录、把库迁到最新、生成本机 CA。事情由 `onboarding.create_data_tree` 做，
    这里只把它做过的事说出来——托盘的设置页调同一个函数，两条前端不会各做一套。
    """
    print(f"Peach 数据目录：{config.data_root}")
    _report_data_tree(onboarding.create_data_tree(config))


def _report_data_tree(tree: onboarding.DataTree) -> None:
    if tree.ledger_existed:
        print(f"数据库已存在，跳过建库：{tree.database}")
        print("要为现有部署生成设置文件，用 `peach init --from-existing`。")
    else:
        print(f"数据库：{tree.database}（已应用 {tree.migrations} 个迁移）")
    if tree.ca_cert is not None:
        print(f"本机 CA：{tree.ca_cert}")
    else:
        print(f"未生成本机 CA（{tree.ca_error}）。装好 openssl 后重跑 `peach init --force` 补上。")
    _report_token(tree)


def _report_token(tree: onboarding.DataTree) -> None:
    """口令文件的路径。口令本身不打印，用 `peach token` 单独看。

    终端输出会进日志、截图和 issue，口令不该顺着这条路走出去。
    """
    print(f"访问口令：{tree.token_path}" + ("" if tree.token_created else "（沿用已有的那份）"))
    print("绑局域网地址的服务必须读得到它，`peach token` 看内容。")


def _announce_token(config: settings_file.PeachConfig) -> None:
    """`--from-existing` 不建目录也不迁库，但口令要补：现有部署正是靠托盘绑局域网
    起服务的，没有这个文件下一次重启就会被 `_serve_token` 拒掉。
    """
    secrets_dir = config.directory("secrets")
    _token, created = auth.ensure_token(secrets_dir)
    print(f"访问口令：{auth.token_path(secrets_dir)}" + ("" if created else "（沿用已有的那份）"))
    print("绑局域网地址的服务必须读得到它，`peach token` 看内容。")


def _token(args: argparse.Namespace) -> int:
    """`peach token` 打印口令，没有就补一份；`--rotate` 换一个。

    手机和别的设备第一次访问会跳登录页，把这里打印的那串贴进去，之后走 cookie。
    """
    _require_readable_settings()
    secrets_dir = settings_file.active().directory("secrets")
    if args.rotate:
        token, fresh = auth.write_token(secrets_dir), True
    else:
        token, fresh = auth.ensure_token(secrets_dir)
    print(f"口令文件：{auth.token_path(secrets_dir)}")
    print(f"口令：{token}")
    if fresh:
        print("已经登录过的浏览器要再登录一次；正在跑的服务要重启才会读到这份口令。")
    print("两台机器互取复核结果时发的是自己的口令，reader 要用和 writer 相同的这份文件。")
    return 0


def _tunnel(args: argparse.Namespace) -> int:
    """按设置文件的模式运行一个前台隧道；托盘/配置页使用同一模块的生命周期管理。"""
    _require_readable_settings()
    config = settings_file.load_config()
    if not config.configured:
        raise SystemExit("这台机器还没初始化过，先跑 `peach init`。")
    state_dir = config.directory("state")
    log_dir = config.directory("logs")
    if args.action == "status":
        snapshot = tunnel.saved_snapshot(state_dir)
        print(f"状态：{snapshot.state}")
        if snapshot.url:
            print(f"地址：{snapshot.url}")
        if snapshot.error:
            print(f"原因：{snapshot.error}")
        return 0 if snapshot.state not in {"error"} else 1

    token = auth.resolve_token("", config.directory("secrets"))
    lan_address = args.lan_address
    if not distribution.standalone() and not lan_address:
        from .mdns import lan_ipv4
        lan_address = lan_ipv4()
    origin_port = getattr(args, "https_port", None)
    if origin_port is None:
        origin_port = (
            config.server.port if distribution.standalone()
            else (8443 if sys.platform == "darwin" else 443)
        )
    plan = tunnel.plan_for_config(
        config,
        access_path=config.directory("secrets") / "access.json",
        token=token,
        standalone_mode=distribution.standalone(),
        lan_address=lan_address,
        https_port=origin_port,
    )
    manager = tunnel.TunnelManager(state_dir, log_dir, wait_timeout=args.timeout)
    # 启动握手本身也要在 try 里：Ctrl-C 打在等待边缘连接的那几十秒上时，子进程已经
    # 起来了，只有走到 finally 才收得掉。
    try:
        snapshot = manager.start(plan)
        print(f"公网入口：{snapshot.url}", flush=True)
        while manager.snapshot().state == "running":
            time.sleep(1)
    except KeyboardInterrupt:
        print("正在停止临时链接…", flush=True)
    finally:
        manager.stop()
    return 0


#: 留空就会改变行为的坐标。进程猜不出来，只能让人在命令行给。
_BLANK_HINTS = (
    ("server.review_writer_origin", "--writer-origin https://<writer 的地址>",
     "reader 不做复核镜像"),
    ("replication.smb_host", "--smb-host <writer 的主机名>", "不挂载共享传输点"),
    ("replication.smb_user", "--smb-user <钥匙串里的账号>", "不挂载共享传输点"),
)


def _report_blanks(config: settings_file.PeachConfig) -> None:
    blanks = []
    for dotted, flag, effect in _BLANK_HINTS:
        table, _, name = dotted.partition(".")
        if not getattr(getattr(config, table), name):
            blanks.append(f"  {dotted} 留空 → {effect}；要填就重跑并加 {flag}")
    if not config.mounts and os.name != "nt":
        # Windows 上盘符本身就是挂载点，空表是正常的，提示它只会让人去填没用的值。
        known = "、".join(sorted(config.locations)) or "local"
        blanks.append(
            f"  [media.mounts] 为空 → 无法读取媒体来源的目录；"
            f"可用 --mount <媒体来源ID>=<媒体目录> 指定目录（已知来源：{known}）"
        )
    if blanks:
        print("以下坐标是空的（这不是错误，单机部署本来就该空）：")
        print("\n".join(blanks))


def _print_next_steps(config: settings_file.PeachConfig, *, from_existing: bool) -> None:
    print("\n下一步：")
    if from_existing:
        print("  1. 打开上面那个文件核对一遍，尤其是 [media.mounts] 和 [replication]。")
        print("  2. 重启服务让它生效。这一步之前，运行行为和现在完全一样。")
        print("  3. `peach token` 取口令：绑局域网地址的服务读不到它就起不来，"
              "已经在用的设备也要重新登录一次。")
        return
    print(f"  1. peach serve --host {config.server.host} --port {config.server.port}")
    print("  2. 浏览器打开 http://127.0.0.1:%d/ 确认页面可用。" % config.server.port)
    if _is_loopback(config.server.host):
        print("  3. 要在局域网访问就改 --host 0.0.0.0，并把本机 CA 装进各设备的信任列表。"
              "访问密码为可选项，在配置页设置。")
    else:
        print("  3. 局域网设备要装本机 CA。访问密码为可选项，在配置页设置。")


def _doctor(args: argparse.Namespace) -> int:
    import json
    from . import diagnostics
    from .mount_reachability import source_roots

    config = settings_file.active()
    host, port = diagnostics.doctor_endpoint(config)
    settings = PeachSettings(db_path=args.db, host=args.host or host, port=port if args.port is None else args.port,
                             token=auth.read_token(config.directory("secrets")),
                             tunnel_enabled=config.tunnel.enabled,
                             access_path=config.directory("secrets") / "access.json")
    snapshot = {"state": "checking", "sources": [], "warnings": []}
    if diagnostics.configuration(config, settings.configured)["status"] != "failed":
        snapshot = diagnostics.probe_mounts(source_roots())
    result = diagnostics.report(settings, snapshot, config=config)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        for check in result["checks"].values():
            print(f"{check['label']} [{check['status']}] {check['reason']}")
            if check["action"]:
                print(f"  {check['action']}")
    return 1 if result["status"] == "failed" else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="peach")
    commands = parser.add_subparsers(dest="command", required=True)

    from .local_poster import main as local_image_main
    local_image = commands.add_parser('local-image', help='读取单张配套图片')
    local_image.add_argument('source', type=Path)
    local_image.add_argument('output', type=Path)
    local_image.set_defaults(handler=lambda args: local_image_main([str(args.source), str(args.output)]))

    init = commands.add_parser(
        "init", help="首次运行：不带参数在终端里问答；带任何参数或 --no-input 走非交互")
    init.add_argument("--no-input", action="store_true",
                      help="不进入问答，按参数与内建默认直接生成")
    init.add_argument("--data-root", type=Path, help="Peach 数据目录；默认沿用当前发现结果")
    init.add_argument("--from-existing", action="store_true",
                      help="只为已在运行的部署生成设置文件，不建目录、不建库")
    init.add_argument("--force", action="store_true", help="覆盖已存在的设置文件")
    init.add_argument("--host", help="服务监听地址")
    init.add_argument("--port", type=int, help="服务端口")
    init.add_argument("--mdns-name", help="本机在局域网发布的名字（<name>.local）")
    init.add_argument("--writer-origin", help="reader 复核镜像要读的 writer 源")
    init.add_argument("--writer-proxy", help="只对 writer 源生效的 HTTP 代理")
    init.add_argument("--shared-root", type=Path, help="ledger 复制的共享传输点")
    init.add_argument("--smb-host", help="共享传输点所在主机")
    init.add_argument("--smb-share", help="共享名")
    init.add_argument("--smb-user", help="挂载共享用的账号")
    init.add_argument("--mount", action="append", metavar="来源ID=路径",
                      help="媒体来源 ID 与本机媒体目录的对应关系，例如 local=/mnt/media；可重复指定")
    init.set_defaults(handler=_init)

    serve = commands.add_parser("serve", help="run the FastAPI application")
    serve.add_argument("--host", default=SERVE_HOST)
    serve.add_argument("--redirect-origin", default="", help="HTTP navigation to one HTTPS origin")
    serve.add_argument("--port", type=int, default=SERVE_PORT)
    serve.add_argument("--db", type=Path, default=DEFAULT_DB)
    serve.add_argument("--token", default="")
    serve.add_argument("--docs", action="store_true")
    serve.add_argument("--setup", action="store_true",
                       help="只提供首次运行表单：忽略发现到的 Peach 数据目录，也不要口令")
    serve.add_argument("--no-auth", action="store_true",
                       help="跳过登录页；仅允许本机访问时可用，不读取或修改口令文件")
    serve.add_argument("--no-mdns", action="store_true")
    serve.add_argument("--shared-db", type=Path, default=SHARED_DATABASE_PATH)
    serve.add_argument("--no-ledger-sync", action="store_true")
    serve.add_argument("--ledger-sync-seconds", type=float, default=0.0,
                       help=argparse.SUPPRESS)
    serve.add_argument("--mdns-name", default=MDNS_NAME)
    serve.add_argument("--mdns-address", help="explicit LAN IPv4 to publish")
    serve.add_argument("--ssl-certfile", type=Path)
    serve.add_argument("--ssl-keyfile", type=Path)
    serve.set_defaults(handler=_serve)

    scan_parser = commands.add_parser(
        "scan", help="扫描一个媒体来源的目录，将文件信息保存到数据库")
    scan_parser.add_argument("location", help="来源 ID（asset.location），要在 [media.locations] 里声明过")
    scan_parser.add_argument("root", nargs="?",
                             help="要扫描的媒体目录；省略时读取该来源的配置（非 Windows 系统使用 [media.mounts]）")
    scan_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    scan_parser.set_defaults(handler=_scan)
    process_parser = commands.add_parser('process', help='扫描文件并补全馆藏资料')
    process_parser.add_argument('location', nargs='?', default='configured')
    process_parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    process_parser.set_defaults(handler=_process_library)

    token = commands.add_parser("token", help="查看局域网访问口令，没有就生成一份")
    token.add_argument("--rotate", action="store_true",
                       help="换一个新口令，已签发的 cookie 立即失效")
    token.set_defaults(handler=_token)

    tunnel_parser = commands.add_parser(
        "tunnel", help="运行或查看 Cloudflare Tunnel（默认要求已设置访问密码）",
    )
    tunnel_parser.add_argument("action", choices=("start", "status"), nargs="?", default="start")
    tunnel_parser.add_argument("--lan-address", help=argparse.SUPPRESS)
    tunnel_parser.add_argument(
        "--https-port", type=int, help="源码服务的实际 HTTPS 端口（macOS 默认 8443，其他平台默认 443）",
    )
    tunnel_parser.add_argument("--timeout", type=float, default=30.0)
    tunnel_parser.set_defaults(handler=_tunnel)

    migrate = commands.add_parser("migrate", help="inspect or apply SQLite migrations")
    migrate.add_argument("action", choices=("status", "upgrade"), nargs="?", default="status")
    migrate.add_argument("--db", type=Path, default=DEFAULT_DB)
    migrate.add_argument("--yes", action="store_true")
    migrate.set_defaults(handler=_migrate)

    status = commands.add_parser("status", help="read-only ledger, migration and sync state")
    status.add_argument("--db", type=Path, default=DEFAULT_DB)
    status.add_argument("--shared-db", type=Path, default=SHARED_DATABASE_PATH)
    status.set_defaults(handler=_status)

    doctor = commands.add_parser("doctor", help="逐项诊断本机配置、数据库、工具与挂载，不执行修复")
    doctor.add_argument("--json", action="store_true", help="输出脱敏 JSON 报告")
    doctor.add_argument("--db", type=Path, default=DEFAULT_DB)
    doctor.add_argument("--host", help="诊断指定的服务监听地址")
    doctor.add_argument("--port", type=int, help="诊断指定的服务端口")
    doctor.set_defaults(handler=_doctor)

    ledger_sync = commands.add_parser("ledger-sync", help="synchronize the local ledger now")
    ledger_sync.add_argument("--db", type=Path, default=DEFAULT_DB)
    ledger_sync.add_argument("--shared-db", type=Path, default=SHARED_DATABASE_PATH)
    ledger_sync.add_argument("--take-ownership", action="store_true",
                             help="claim the single-writer role after both copies are in sync")
    ledger_sync.set_defaults(handler=_ledger_sync)

    return parser


def subcommands() -> frozenset[str]:
    """`peach` 现有的子命令名。

    打包入口靠它决定「第一个参数是子命令还是托盘参数」。这个集合必须从 parser 现算，
    不能在别处抄一份常量：抄过一次，结果是 `ledger-sync` 和 `status` 在 EXE 里一直
    不可达，而且不报错——参数被当成托盘参数吞掉了。

    `_subparsers` 是 argparse 私有属性，argparse 没有公开的「列出子命令」接口；私有
    访问只留在这一处。
    """
    names: set[str] = set()
    container = build_parser()._subparsers
    for action in getattr(container, "_group_actions", ()):
        names.update(getattr(action, "choices", ()) or ())
    return frozenset(names)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)
