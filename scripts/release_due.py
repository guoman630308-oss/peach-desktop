#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""会话收尾时问一次：该发下一版了吗。不该发就一个字都不说。

判据本身住在 `changelog.due()`，这里只管「什么时候问、问给谁听、问几次」。挂在会话
结束这个时刻，是因为发版的时机没有任何自动触发点：它既不属于某次提交，也不属于某次
集成，全靠人某天想起来去数变更日志。`integrate` 的输出里已经带了同一份判断，但那要
恰好有分支落地才会出现，一周没集成的那几天正是最容易忘的时候。

四道门先挡住不该出声的场合，任何一道不成立就静默退出：

1. **不在主检出**：隔离工作树里的工作者不发版，提醒他等于提醒错人。
2. **不在 master**：发布只从主线走，站在别的分支上收到提醒也动不了手。
3. **工作区不干净**：此刻发不了版；等这轮改动落地、下一次收尾时再问。
4. **这一版已经问过**：闩住 master 的 sha，同一个 sha 只问一次。master 前进了就是
   又攒了新东西，那时再问一次是对的。

用法（`--hook-event` 是给 Claude 与 Codex 的 Stop 钩子用的，人手查用不带参数那条）:

    python scripts/release_due.py                  # 打印判断，读-only，不动闩
    python scripts/release_due.py --hook-event     # 从 stdin 收 hook JSON，回 systemMessage
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "scripts"

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from peach.config import PROJECT_ROOT, STATE_DIR

from . import changelog

#: 发布只从主线走，`release_tag.py` 也只认这一条。
MAIN_BRANCH = "master"

#: 闩：上一次问过的 master sha。落在 `peach-data/state/`，不进 Git——它是这台机器
#: 的记忆，跟进度产物一样每次现写，进了 Git 只会让工作区永远是 modified。
LATCH = STATE_DIR / "release-due.json"

ADVICE = ("起草：`python scripts/release_tag.py --bump auto --apply`，"
          "措辞定了再 `--ship --apply`。这次不发就不用管，master 前进后会再问一次。")


def git(root: Path, *args: str) -> str | None:
    """仓库里的一条只读 git；命令不成立时是 None，调用方一律当作「说不清」。"""
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    return done.stdout.strip() if done.returncode == 0 else None


def is_main_checkout(root: Path) -> bool:
    """主检出的 git 目录就是共用的那个；隔离工作树的在 `.git/worktrees/<名字>` 下。

    判据不看目录名：工作树被回收后目录会原地留着，名字仍然像个工作树，里面的 git 却
    已经作用于主检出，只按名字判会把两种相反的情形认成同一种。
    """
    own = git(root, "rev-parse", "--git-dir")
    shared = git(root, "rev-parse", "--git-common-dir")
    if own is None or shared is None:
        return False
    return (root / own).resolve() == (root / shared).resolve()


def spoken(latch: Path) -> str:
    """闩里记着的那个 sha；文件不在、读不出、内容不认得，都当作没问过。"""
    try:
        remembered = json.loads(latch.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(remembered.get("sha") or "") if isinstance(remembered, dict) else ""


def remember(latch: Path, sha: str) -> None:
    latch.parent.mkdir(parents=True, exist_ok=True)
    latch.write_text(json.dumps(
        {"sha": sha, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verdict(root: Path, latch: Path) -> dict:
    """这一轮该说什么。`say` 为空就是不出声，`sha` 是要闩住的那个提交。"""
    quiet = {"say": "", "sha": ""}
    if not is_main_checkout(root):
        return quiet
    if git(root, "rev-parse", "--abbrev-ref", "HEAD") != MAIN_BRANCH:
        return quiet
    if git(root, "status", "--porcelain") != "":
        return quiet
    head = git(root, "rev-parse", "HEAD")
    if not head or spoken(latch) == head:
        return quiet
    reasons = changelog.due(root)
    if not reasons["due"]:
        return quiet
    return {"say": "该发下一版了：" + "；".join(reasons["why"]) + "。" + ADVICE,
            "sha": head}


def payload() -> dict:
    """钩子从 stdin 递来的那个对象；两家的形状一致，都带 `cwd`。"""
    try:
        received = json.load(sys.stdin)
    except (ValueError, OSError):
        return {}
    return received if isinstance(received, dict) else {}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hook-event", action="store_true",
                        help="从 stdin 收 Claude 或 Codex 的 hook JSON，按钩子约定回话")
    parser.add_argument("--repo", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--latch", type=Path, default=LATCH)
    args = parser.parse_args(argv)

    try:
        root = args.repo
        if args.hook_event:
            # 钩子的 `cwd` 才是这次会话真正待的地方：工作树里的会话由它认出来。
            root = Path(payload().get("cwd") or root)
        answer = verdict(root, args.latch)
        if not answer["say"]:
            return 0
        if args.hook_event:
            # 只有钩子问过才动闩。人手查是只读的，不该顺手把钩子的嘴堵上。
            remember(args.latch, answer["sha"])
            print(json.dumps({"systemMessage": answer["say"]}, ensure_ascii=False))
        else:
            print(answer["say"])
        return 0
    except Exception as exc:  # noqa: BLE001 —— 钩子里出的错要看得见，但不该挡住收尾
        print(f"发版时机检查失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
