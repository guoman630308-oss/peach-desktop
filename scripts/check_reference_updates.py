#!/usr/bin/env python3
"""检查可变外部 Markdown 与 Peach 锁定快照之间的漂移。

每个来源按 `cadence` 分档：`weekly` 是 Peach 代码直接消费或逐字复制的上游，随每周
Dependabot 接管一起查；`monthly` 是设计准则与算法参考；`quarterly` 是同类产品 README，
留给成轮调研；`manual` 只在 `--source` 点名时查。`volatile` 标记每次取回字节都不同的
网页，这类来源只认 Git revision。`pins` 是按 40 位 sha 钉在清单里的运行依赖，
Dependabot 推不动，由这里报出上游是否前移。
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from peach.user_agent import USER_AGENT
DEFAULT_REGISTRY = ROOT / "docs" / "reference-sources.json"
CADENCES = ("weekly", "monthly", "quarterly", "manual")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def load_registry(path: Path = DEFAULT_REGISTRY) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_bytes(url: str, *, timeout: float = 20.0) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/markdown,text/plain;q=0.9,*/*;q=0.1",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def resolve_git_revision(source: dict, *, timeout: float = 20.0) -> str | None:
    git = source.get("git")
    if not git:
        return None
    return ls_remote(source["id"], git["repository"], git["ref"], timeout=timeout)


def ls_remote(label: str, repository: str, ref: str, *, timeout: float = 20.0) -> str:
    result = subprocess.run(
        ["git", "ls-remote", repository, ref],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=timeout,
    )
    lines = result.stdout.strip().splitlines()
    if len(lines) != 1:
        raise RuntimeError(f"{label} 无法唯一解析 {ref}")
    return lines[0].split()[0]


def pinned_revision(root: Path, pin: dict) -> str:
    """清单里钉住的那个 40 位 sha；找不到或不止一个都算登记错误。"""
    web = pin["repository"].removesuffix(".git")
    pattern = re.escape(web) + r"@([0-9a-f]{40})\b"
    found = set(re.findall(pattern, (root / pin["manifest"]).read_text(encoding="utf-8")))
    if len(found) != 1:
        raise ValueError(f"{pin['id']} 在 {pin['manifest']} 里应恰好钉住一个 sha，实际 {len(found)} 个")
    return found.pop()


def inspect_pin(
    root: Path,
    pin: dict,
    *,
    revision_resolver: Callable[[dict], str] = lambda pin: ls_remote(
        pin["id"], pin["repository"], pin["ref"]
    ),
) -> dict:
    pinned = pinned_revision(root, pin)
    live = revision_resolver(pin)
    return {
        "id": pin["id"],
        "changed": pinned != live,
        "pinned_revision": pinned,
        "live_revision": live,
        "compare": f"{pin['repository'].removesuffix('.git')}/compare/{pinned}...{live}",
    }


def validate_registry(root: Path, registry: dict) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for source in registry.get("sources", []):
        source_id = source.get("id", "<missing>")
        if source_id in seen:
            problems.append(f"来源 id 重复：{source_id}")
        seen.add(source_id)
        snapshot = root / source.get("snapshot", "")
        if not snapshot.is_file():
            problems.append(f"{source_id} 缺少锁定快照：{snapshot}")
            continue
        actual = sha256_bytes(snapshot.read_bytes())
        if actual != source.get("sha256"):
            problems.append(
                f"{source_id} 快照校验失败：登记 {source.get('sha256')}，实际 {actual}"
            )
        git = source.get("git")
        if git and len(git.get("revision", "")) != 40:
            problems.append(f"{source_id} 的 Git revision 不是完整 40 位提交")
        if source.get("cadence") not in CADENCES:
            problems.append(f"{source_id} 的 cadence 必须是 {'、'.join(CADENCES)} 之一")
        if source.get("volatile") and not git and source.get("cadence") != "manual":
            problems.append(f"{source_id} 每次取回都不同又没有 Git revision，只能是 manual")
    for pin in registry.get("pins", []):
        if pin.get("cadence") not in CADENCES:
            problems.append(f"{pin.get('id')} 的 cadence 必须是 {'、'.join(CADENCES)} 之一")
        try:
            pinned_revision(root, pin)
        except (OSError, KeyError, ValueError) as exc:
            problems.append(f"钉住的依赖登记有误：{exc}")
    return problems


def unified_diff(source: dict, old: bytes, new: bytes) -> str:
    old_lines = old.decode("utf-8", errors="replace").splitlines(keepends=True)
    new_lines = new.decode("utf-8", errors="replace").splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"{source['id']}@locked",
            tofile=f"{source['id']}@live",
        )
    )


def inspect_source(
    root: Path,
    source: dict,
    *,
    fetcher: Callable[[str], bytes] = fetch_bytes,
    revision_resolver: Callable[[dict], str | None] = resolve_git_revision,
) -> dict:
    snapshot = (root / source["snapshot"]).read_bytes()
    live = fetcher(source["url"])
    live_sha = sha256_bytes(live)
    live_revision = revision_resolver(source)
    locked_revision = (source.get("git") or {}).get("revision")
    revision_moved = live_revision != locked_revision
    # 内容没变、仓库往前走了，不算这份证据有更新；每次取回都不同的网页反过来只认 revision。
    if source.get("volatile") and live_revision is not None:
        changed = revision_moved
    else:
        changed = live_sha != source["sha256"]
    return {
        "id": source["id"],
        "changed": changed,
        "revision_moved": revision_moved,
        "pinned_sha256": source["sha256"],
        "live_sha256": live_sha,
        "pinned_revision": locked_revision,
        "live_revision": live_revision,
        "diff": unified_diff(source, snapshot, live) if changed else "",
        "live": live,
    }


def selected_sources(
    registry: dict, source_id: str | None, cadence: str | None = None, *, key: str = "sources"
) -> list[dict]:
    """点名优先；否则按档位取，不给档位就取 manual 以外的全部。"""
    sources = registry.get(key, [])
    if source_id is not None:
        return [source for source in sources if source.get("id") == source_id]
    if cadence is not None:
        return [source for source in sources if source.get("cadence") == cadence]
    return [source for source in sources if source.get("cadence") != "manual"]


def command_check(args: argparse.Namespace) -> int:
    registry_path = Path(args.registry).resolve()
    root = registry_path.parents[1]
    registry = load_registry(registry_path)
    problems = validate_registry(root, registry)
    if problems:
        for problem in problems:
            print(f"错误：{problem}", file=sys.stderr)
        return 2

    pins = selected_sources(registry, args.source, args.cadence, key="pins")
    sources = selected_sources(registry, args.source, args.cadence)
    if args.source is not None and not pins and not sources:
        raise ValueError(f"未知来源：{args.source}")
    changed = False
    for pin in pins:
        result = inspect_pin(root, pin)
        if result["changed"]:
            changed = True
            print(
                f"上游已前移：{result['id']}\n"
                f"  钉住 {result['pinned_revision']}，上游 {result['live_revision']}\n"
                f"  差异 {result['compare']}"
            )
        else:
            print(f"未变化：{result['id']}，钉住的就是上游 {result['live_revision']}")
    for source in sources:
        result = inspect_source(root, source)
        if result["changed"]:
            changed = True
            print(
                f"有更新：{result['id']}\n"
                f"  SHA-256 {result['pinned_sha256']} -> {result['live_sha256']}"
            )
            if result["pinned_revision"] is not None:
                print(
                    f"  revision {result['pinned_revision']} -> {result['live_revision']}"
                )
            if args.diff:
                print(result["diff"], end="" if result["diff"].endswith("\n") else "\n")
        else:
            suffix = ""
            if result["revision_moved"]:
                suffix = f"，内容相同，revision 已前移到 {result['live_revision']}"
            elif result["live_revision"] is not None:
                suffix = f"，revision {result['live_revision']}"
            print(f"未变化：{result['id']}，SHA-256 {result['live_sha256']}{suffix}")
    return 1 if changed else 0


def command_accept(args: argparse.Namespace) -> int:
    registry_path = Path(args.registry).resolve()
    root = registry_path.parents[1]
    registry = load_registry(registry_path)
    selected = selected_sources(registry, args.source)
    if not selected:
        raise ValueError(f"未知来源：{args.source}；钉住的依赖改清单升级，不走 accept")
    source = selected[0]
    result = inspect_source(root, source)
    if result["live_sha256"] != args.expected_sha256.lower():
        print("拒绝接受：线上 SHA-256 与 --expected-sha256 不一致", file=sys.stderr)
        return 2
    if result["live_revision"] is not None:
        if result["live_revision"] != args.expected_revision:
            print("拒绝接受：线上 revision 与 --expected-revision 不一致", file=sys.stderr)
            return 2

    (root / source["snapshot"]).write_bytes(result["live"])
    source["sha256"] = result["live_sha256"]
    source["checked_on"] = dt.date.today().isoformat()
    if result["live_revision"] is not None:
        source["git"]["revision"] = result["live_revision"]
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"已锁定 {source['id']}；这一步没有修改任何 Peach 实现。")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    sub = result.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="只读检查上游是否变化")
    check.add_argument("--source")
    check.add_argument("--cadence", choices=CADENCES, help="只查这一档；缺省查 manual 以外的全部")
    check.add_argument("--diff", action="store_true", help="显示与锁定快照的差异")
    check.set_defaults(func=command_check)
    accept = sub.add_parser("accept", help="在人工审阅后更新快照和锁文件")
    accept.add_argument("--source", required=True)
    accept.add_argument("--expected-sha256", required=True)
    accept.add_argument("--expected-revision")
    accept.set_defaults(func=command_accept)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        return args.func(args)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        print(f"检查失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
