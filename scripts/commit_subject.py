"""核对交付分支每个提交的主题行：`type(scope)!: 描述`，变更日志靠它起草。"""

from __future__ import annotations

import re
from pathlib import Path

from .check_readme_impact import git, unpublished

#: 允许的提交类型，封闭清单。前三个进变更日志（`scripts/changelog.py` 的 TYPE_GROUPS），
#: 其余只改开发过程。要加新类型先想清它属于哪一组，不是随手起个词。
TYPES = frozenset({"feat", "fix", "perf", "refactor", "docs", "test", "chore",
                   "build", "ci", "style", "revert"})

#: 主题形状。scope 可省，省了就等人在变更日志里定区域标签；冒号后必须有一个空格，
#: `fix(web):保留` 这种历史写法解析得出来，但两种写法并存就没法 grep。
FORM = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^()\s]+)\))?(?P<bang>!)?: (?P<text>\S.*)$")

EXAMPLE = "fix(web): 补齐图标声明与兜底路径"


def subjects(repo: Path, base: str, head: str = "HEAD") -> list[str]:
    """分支自己的提交主题，不含从目标分支合进来的 merge，也不含已发布到远端的提交。"""
    output = git(repo, "log", "--no-merges", "--format=%s", f"{base}..{head}", *unpublished(repo))
    return [line for line in output.splitlines() if line.strip()]


def check(repo: Path, base: str, head: str = "HEAD") -> list[str]:
    """每个提交主题都要是 Conventional Commits 形状，类型在清单内。

    `changelog.py` 按类型分组、按 scope 贴区域标签起草变更日志，主题写歪的提交不是
    报错而是静默漏掉或错归组，等人定版时才发现少了一条。所以在 `ready` / `integrate`
    就拒收：形状不对、类型不在清单、描述为空，三种都算。
    """
    problems = []
    for subject in subjects(repo, base, head):
        parsed = FORM.match(subject)
        if parsed is None:
            problems.append(f"提交主题「{subject}」须为 type(scope): 描述，如 {EXAMPLE}")
        elif parsed["type"] not in TYPES:
            problems.append(f"提交主题「{subject}」的类型「{parsed['type']}」不在清单，"
                            "允许：" + "、".join(sorted(TYPES)))
    return problems
