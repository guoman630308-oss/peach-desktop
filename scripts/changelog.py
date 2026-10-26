"""变更日志：从 Conventional Commits 起草，发布时把未发布节定版。

格式遵循 Keep a Changelog 1.1.0（https://keepachangelog.com/zh-CN/1.1.0/），版本号遵循
Semantic Versioning 2.0.0（https://semver.org/lang/zh-CN/spec/v2.0.0.html）：`## [未发布]`
常在，每个发布一节 `## [X.Y.Z] - YYYY-MM-DD`，分组标题固定，底部给 compare 链接。

草稿按提交主题分组，措辞要人改成使用者读得懂的话——规范的第一条原则就是变更日志写给
人看，不是 `git log` 的转储。只改开发过程的提交（`docs`、`test`、`chore`、`refactor`）
不进日志。定版时未发布节里人润色过的内容整段搬进版本节，那一节空着才用草稿兜底。

每个条目再带一个区域标签（`- **播放**：…`）：分组标题按变化性质分，标签按使用者看到的
区域分。认得的 scope 自动换成标签，草稿里还留着英文 scope 的条目就是等人定标签的。

    python scripts/changelog.py                                # 起草未发布区间
    python scripts/changelog.py --range v0.16.0..v0.27.1       # 起草任意区间
    python scripts/changelog.py --release 0.30.0 --apply       # 把未发布节定版为 0.30.0
    python scripts/changelog.py --notes 0.30.0 --to NOTES.md   # 生成这一版的 Release 正文
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "scripts"
from . import version_bump

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = "CHANGELOG.md"

#: 未发布节的标题。Keep a Changelog 要求它常驻，让人随时知道主线上攒了什么。
UNRELEASED = "未发布"

#: Release 页正文里跟在变更日志后面的安装提示。每一版都一样，所以只写在这里一处，
#: 工作流不再自己抄一份。
INSTALL_NOTE = ("Windows x64，支持本机和局域网浏览器访问；无需另装 Python 或媒体工具。"
                "安装与使用见 [Windows 使用说明](https://github.com/peach-mitao/peach/blob/master/docs/TESTING_DESKTOP.md)，"
                "macOS 见 [源码运行](https://github.com/peach-mitao/peach/blob/master/docs/OPERATIONS.md#从源码运行windowsmacos)。")

#: Conventional Commits 的主题形状：`type(scope)!: 描述`。
SUBJECT = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<text>.+)$")

#: 破坏性变化的脚注写法，与主题里的 `!` 等效。
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE:\s*(?P<text>.+)$", re.M)

#: 提交类型到分组的映射。落在这里之外的类型只改开发过程，不进日志。
TYPE_GROUPS = {"feat": "新增", "feature": "新增", "fix": "修复", "perf": "变更"}

#: 这些 scope 下的改动只动开发过程，即使打成 `fix` 也不进面向使用者的日志。
SILENT_SCOPES = frozenset({"tests", "test", "docs", "doc", "ci", "skills", "agents"})

#: 两个分组标题，`due()` 单独看它们：这两类拖着不发，代价落在使用者身上。
BREAKING = "破坏性变化"
SECURITY_GROUP = "安全"

#: 分组的渲染顺序。空分组不渲染；「弃用」「移除」「安全」由人按需补，脚本不猜。
GROUP_ORDER = (BREAKING, "新增", "变更", "弃用", "移除", "修复", SECURITY_GROUP)

#: 提交主题里点名 security 的那个词，与上面的分组标题不是一回事：这个是 type/scope。
SECURITY = "security"

#: 不等周期的两组：破坏性变化让人踩坑，安全问题让人暴露，都是越晚发代价越大。
URGENT = (BREAKING, SECURITY_GROUP)

#: 发布节奏：每周一次，攒够就提前，`URGENT` 那两组不等周期。
#:
#: 时间那一半有先例可依——Firefox 四周、Ubuntu 与 GNOME 半年都是把「要不要发」交给
#: 日历，到点看有没有面向使用者的变化，有就发。条数那一半没有可靠样本：已发四版各带
#: 6、8、6、9 条，可那是「每次集成推一格」时期的产物，反映的是那三天写了多少代码，
#: 不是「多少变化值得让人下载一次」。10 是没有样本时的保守起点，比历史单次量高一档，
#: 因为周期从一天放宽到了一周。`due()` 每次都报出实际攒了多少，几次真实发布之后拿那
#: 几个数回来校准这一行，别再拿旧机制的数字当依据。
DUE_DAYS = 7
DUE_ENTRIES = 10

#: 条目前缀用的区域标签，说的是使用者在哪儿看到这个变化。分组标题按变化性质
#: 分（规范这么定），标签按使用者看到的区域分，两维叠起来才既合规范又找得到东西。
#: 这个清单是封闭的：`tests/test_changelog.py` 拒绝清单外的标签，免得各写一套近义词。
AREAS = ("界面", "播放", "馆藏", "采集", "复核", "关注", "配置", "更新", "桌面", "账本")

#: scope 到区域标签的映射，只收没有歧义的。`web` 故意不映射——它占了近八成提交，
#: 机械挂上「界面」等于给每条都贴同一个标签，标签就不再有区分力，留给人按条目内容定。
AREA_BY_SCOPE = {
    "ui": "界面", "cards": "界面", "detail": "界面",
    "media": "播放", "player": "播放",
    "library": "馆藏", "catalog": "馆藏", "names": "馆藏",
    "metadata": "采集", "sourcing": "采集", "avatars": "采集",
    "studios": "采集", "links": "采集",
    "review": "复核",
    "follow": "关注",
    "setup": "配置", "config": "配置", "auth": "配置",
    "update": "更新",
    "tray": "桌面", "desktop": "桌面", "ops": "桌面",
    "ledger": "账本", "migrations": "账本",
}

#: 未发布节：标题到下一节标题或底部链接块之间的全部内容。
UNRELEASED_BLOCK = re.compile(
    rf"^## \[{UNRELEASED}\]\n(?P<body>.*?)(?=^## \[|^\[{UNRELEASED}\]:|\Z)", re.M | re.S)

#: 底部那条未发布链接。定版时它跟着指向新标签，新版本的链接紧跟其后。
UNRELEASED_LINK = re.compile(rf"^\[{UNRELEASED}\]:.*$", re.M)


@dataclass(frozen=True)
class Commit:
    subject: str
    body: str


def read_commits(root: Path, spec: str) -> list[Commit]:
    raw = version_bump.git(root, "log", "--no-merges", "--format=%s%x1f%b%x00", spec)
    commits = []
    for record in raw.split("\0"):
        if not record.strip():
            continue
        subject, _, body = record.strip("\n").partition("\x1f")
        commits.append(Commit(subject.strip(), body))
    return commits


def entry_for(commit: Commit) -> tuple[str, str] | None:
    """一个提交对应的（分组，条目文本）；不进日志的返回 None。"""
    match = SUBJECT.match(commit.subject)
    if match is None:
        return None
    kind, scope = match["type"], (match["scope"] or "").strip()
    if scope in SILENT_SCOPES:
        return None
    text = match["text"].strip().rstrip("。")
    footer = BREAKING_FOOTER.search(commit.body or "")
    if match["bang"] or footer:
        return BREAKING, _label(scope, footer["text"].strip() if footer else text)
    if SECURITY in (kind, scope):
        return SECURITY_GROUP, _label(scope if scope != SECURITY else "", text)
    group = TYPE_GROUPS.get(kind)
    return (group, _label(scope, text)) if group else None


def _label(scope: str, text: str) -> str:
    """条目前缀。认得的 scope 换成区域标签，认不出的原样留着英文等人定。"""
    area = AREA_BY_SCOPE.get(scope)
    if area:
        return f"**{area}**：{text}"
    return f"{scope}：{text}" if scope else text


def group_entries(commits) -> dict[str, list[str]]:
    """按分组收条目，保留首次出现的顺序并去掉重复措辞。"""
    grouped: dict[str, list[str]] = {}
    for commit in commits:
        found = entry_for(commit)
        if found is None:
            continue
        group, text = found
        bucket = grouped.setdefault(group, [])
        if text not in bucket:
            bucket.append(text)
    return {group: grouped[group] for group in GROUP_ORDER if group in grouped}


def render_body(grouped: dict[str, list[str]]) -> str:
    """分组与条目，不含节标题。"""
    parts: list[str] = []
    for group, entries in grouped.items():
        parts.extend((f"### {group}", ""))
        parts.extend(f"- {entry}" for entry in entries)
        parts.append("")
    return "\n".join(parts).rstrip("\n")


def draft(root: Path, spec: str) -> str:
    return render_body(group_entries(read_commits(root, spec)))


def _waiting_days(root: Path) -> int:
    """上一个版本标签到现在过了几天；还没有标签时从最早那个提交算。"""
    tag = version_bump.last_tag(root)
    ref = tag or version_bump.git(root, "rev-list", "--max-parents=0", "HEAD").split()[-1]
    stamp = int(version_bump.git(root, "log", "-1", "--format=%ct", ref))
    return max(0, int((time.time() - stamp) // 86400))


def due(root: Path) -> dict:
    """该不该发下一版，以及为什么。

    只看使用者那一侧：他们看得见几条变化、等了几天、里头有没有等不得的。提交数不作
    判据——一百个重构提交对使用者是零，那正是这份判据要跟「集成了多少次」分开的地方。
    """
    grouped = group_entries(read_commits(root, version_bump.release_range(root)))
    entries = sum(len(items) for items in grouped.values())
    days = _waiting_days(root)
    why = []
    if entries and days >= DUE_DAYS:
        why.append(f"距上一版 {days} 天，攒了 {entries} 条面向使用者的变化")
    if entries >= DUE_ENTRIES:
        why.append(f"{entries} 条已经超出一周的常量，不必等满周期")
    urgent = [group for group in URGENT if group in grouped]
    if urgent:
        why.append("有" + "、".join(urgent) + "，这类不等周期")
    return {"entries": entries, "days": days, "due": bool(why), "why": why,
            "groups": {group: len(items) for group, items in grouped.items()}}


def compare_link(repo: str, previous: str | None, tag: str) -> str:
    base = f"https://github.com/{repo}"
    return f"{base}/compare/{previous}...{tag}" if previous else f"{base}/releases/tag/{tag}"


def has_section(document: str, version: str) -> bool:
    return re.search(rf"^## \[{re.escape(version)}\]", document, re.M) is not None


def section_of(document: str, version: str) -> str:
    """某个版本那一节的正文，不含标题；没有这一节时是空串。

    发布前要确认的就是这段文字，把它取出来带在命令输出里，确认的人不用再去翻文件。
    """
    found = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(?P<body>.*?)(?=^## \[|^\[|\Z)",
                      document, re.M | re.S)
    return found["body"].strip("\n") if found else ""


def require_labeled_entries(document: str, version: str) -> None:
    """定版条目必须使用封闭的中文区域标签，不能把提交 scope 直接发给使用者。"""
    section = section_of(document, version)
    allowed = "|".join(re.escape(area) for area in AREAS)
    labeled = re.compile(rf"^- \*\*(?:{allowed})\*\*：")
    invalid = [line for line in section.splitlines()
               if line.startswith("- ") and labeled.match(line) is None]
    if invalid:
        preview = "；".join(invalid[:3])
        remainder = f"（另有 {len(invalid) - 3} 条）" if len(invalid) > 3 else ""
        raise version_bump.VersionError(
            f"{CHANGELOG} 的 {version} 一节还有未归类条目：{preview}{remainder}")


def notes(document: str, version: str) -> str:
    """Release 页的正文：这一版在 `CHANGELOG.md` 里的那一节，后面跟安装提示。

    使用者在 GitHub 上读到的就是这段，所以那一节缺了不能静默发一段固定文案顶上，
    直接拒绝，让发布链停在这里。
    """
    section = section_of(document, version)
    if not section:
        raise version_bump.VersionError(f"{CHANGELOG} 里没有 {version} 这一节，Release 正文无从生成")
    return f"{section}\n\n{INSTALL_NOTE}\n"


def promote(document: str, version: str, *, date: str, repo: str, previous: str | None,
            fallback: str = "") -> str:
    """把未发布节定版成 `version` 一节，并在它上面留一个空的未发布节。

    人在未发布节里润色过的措辞整段搬下去；那一节没有条目时才用 `fallback` 兜底。
    """
    if has_section(document, version):
        raise version_bump.VersionError(f"{CHANGELOG} 里已经有 {version} 这一节")
    match = UNRELEASED_BLOCK.search(document)
    if match is None:
        raise version_bump.VersionError(f"{CHANGELOG} 缺少 `## [{UNRELEASED}]` 这一节")
    body = match["body"].strip("\n")
    if "- " not in body:
        body = fallback.strip("\n")
    if not body.strip():
        raise version_bump.VersionError(f"{UNRELEASED}节是空的，这次发布没有可记录的变化")
    block = f"## [{UNRELEASED}]\n\n## [{version}] - {date}\n\n{body}\n\n"
    updated = document[:match.start()] + block + document[match.end():]
    return _relink(updated, repo=repo, version=version, previous=previous)


def _relink(document: str, *, repo: str, version: str, previous: str | None) -> str:
    tag = f"v{version}"
    if not UNRELEASED_LINK.search(document):
        raise version_bump.VersionError(f"{CHANGELOG} 缺少 `[{UNRELEASED}]:` 那条链接")
    document = UNRELEASED_LINK.sub(f"[{UNRELEASED}]: {compare_link(repo, tag, 'HEAD')}",
                                   document, count=1)
    definition = f"[{version}]: {compare_link(repo, previous, tag)}"
    return UNRELEASED_LINK.sub(lambda found: f"{found[0]}\n{definition}", document, count=1)


def release(root: Path, version: str, *, repo: str, spec: str, today: str | None = None) -> None:
    """把 `CHANGELOG.md` 的未发布节定版并落盘。"""
    path = root / CHANGELOG
    previous = spec.split("..")[0] if ".." in spec else version_bump.last_tag(root)
    updated = promote(path.read_text(encoding="utf-8"), version,
                      date=today or dt.date.today().isoformat(), repo=repo,
                      previous=previous, fallback=draft(root, spec))
    path.write_bytes(updated.encode("utf-8"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default="peach-mitao/peach")
    parser.add_argument("--range", dest="spec", help="提交区间，默认上一个版本标签到 HEAD")
    parser.add_argument("--release", help=f"把{UNRELEASED}节定版成这个版本号")
    parser.add_argument("--apply", action="store_true", help=f"写进 {CHANGELOG}")
    parser.add_argument("--notes", metavar="VERSION", help="输出这一版的 Release 正文")
    parser.add_argument("--to", metavar="PATH", help="把 --notes 的正文以 UTF-8 写进这个文件")
    args = parser.parse_args(argv)
    try:
        if args.notes:
            text = notes((ROOT / CHANGELOG).read_text(encoding="utf-8"), args.notes)
            if args.to:
                Path(args.to).write_bytes(text.encode("utf-8"))
            else:
                print(text, end="")
            return 0
        spec = args.spec or version_bump.release_range(ROOT)
        if not args.apply:
            heading = args.release or UNRELEASED
            body = draft(ROOT, spec) or "暂无面向使用者的变化。"
            print(f"## [{heading}]\n\n{body}")
            return 0
        if not args.release:
            raise version_bump.VersionError("--apply 必须同时给 --release")
        release(ROOT, args.release, repo=args.repo, spec=spec)
        print(f"{CHANGELOG} 的{UNRELEASED}节已定版成 {args.release}，请核对措辞。")
        return 0
    except version_bump.VersionError as error:
        # 两条路各说自己没做成的事：起草或定版是「变更日志未更新」，--notes 是「Release 正文未生成」。
        what = "Release 正文未生成" if args.notes else "变更日志未更新"
        print(f"{what}：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
