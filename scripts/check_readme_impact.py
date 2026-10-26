"""核对一次分支交付的 README 影响声明。"""

from __future__ import annotations

import subprocess
from pathlib import Path

READMES = {"README.md", "README.en.md"}
PREFIXES = ("src/peach/", "web/", "frontend/", "migrations/", "resources/")
FILES = {
    "pyproject.toml", "package.json", "package-lock.json",
    ".github/workflows/release.yml", "docs/FRONTEND.md",
    "docs/TESTING_DESKTOP.md", "docs/OPERATIONS.md", "docs/SOURCING.md",
    "scripts/build_windows.ps1", "scripts/build_app_entry.py",
}


#: 已经推到 GitHub 的提交。它的署名与主题改不了：改写 hash 只能强推覆盖远端。
PUBLISHED_REF = "origin/master"


def git(repo: Path, *args: str, message: str | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], input=message, capture_output=True,
        text=True, encoding="utf-8", check=True,
    ).stdout


def unpublished(repo: Path) -> list[str]:
    """`git log` 用的排除参数：`PUBLISHED_REF` 存在就排掉它能到达的提交，没有远端就什么都不排。

    逐提交的门槛（署名、主题）只能落在还没发布的提交上：网页上直接改 README 的那条已经在
    远端 master 上，分支把它并回来时它不可能再补署名。
    """
    try:
        git(repo, "rev-parse", "--verify", "--quiet", f"{PUBLISHED_REF}^{{commit}}")
    except subprocess.CalledProcessError:
        return []
    return [f"^{PUBLISHED_REF}"]


def trailers(repo: Path, message: str) -> dict[str, list[str]]:
    """Git 解析提交末尾的 trailer；每个字段保留全部取值。"""
    result: dict[str, list[str]] = {}
    trailers = git(repo, "interpret-trailers", "--parse", message=message)
    for line in trailers.splitlines():
        name, _, value = line.partition(":")
        result.setdefault(name.casefold(), []).append(value.strip())
    return result


def declaration(values: list[str]) -> tuple[str, list[str]]:
    """检查声明形态；与 README 文件差异的关系由交付检查核对。"""
    if len(values) != 1:
        return "", ["交付提交须有唯一 README-Impact: updated; 说明 或 README-Impact: none; 原因"]
    status, separator, reason = values[0].partition(";")
    if status not in {"updated", "none"} or not separator or not reason.strip():
        return "", ["README-Impact 需使用 updated/none，并在英文分号后写具体原因"]
    return status, []


def reviewed_declaration(values: list[str], counterpart: str | None = None) -> list[str]:
    """另一语言的核对声明必须唯一，指明文件及无需修改的原因。"""
    if len(values) != 1:
        return ["README-Reviewed 须有唯一的未改 README 文件名和原因"]
    name, separator, reason = values[0].partition(";")
    allowed = {counterpart} if counterpart else READMES
    if name.strip() not in allowed or not separator or not reason.strip():
        return ["README-Reviewed 须填写未改 README 文件名，并在英文分号后写明无需修改的原因"]
    return []


def check(repo: Path, base: str, head: str = "HEAD") -> list[str]:
    """以实际交付差异和最后提交的 Git trailer 为依据。"""
    paths = set(git(repo, "diff", "--name-only", "--no-renames", "-z",
                    base, head).rstrip("\0").split("\0")) - {""}
    touched = paths & READMES
    relevant = any(path in FILES or path.startswith(PREFIXES) for path in paths)
    if not touched and not relevant:
        return []
    message = git(repo, "show", "-s", "--format=%B", head)
    status, problems = declaration(trailers(repo, message).get("readme-impact", []))
    if problems:
        return problems
    if touched and touched != READMES:
        reviewed = trailers(repo, message).get("readme-reviewed", [])
        other = next(iter(READMES - touched))
        if problems := reviewed_declaration(reviewed, other):
            return problems
    if status == "updated" and not touched:
        return ["README-Impact 声明 updated，但交付差异未包含 README"]
    if status == "none" and touched:
        return ["README-Impact 声明 none，但交付差异包含 README"]
    return []
