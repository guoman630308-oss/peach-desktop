"""核对交付提交的署名：哪个工具、哪个模型写的这一段代码。"""

from __future__ import annotations

import re
from pathlib import Path

from .check_readme_impact import git, trailers, unpublished

#: 已登记的工具与它厂商的 noreply 地址。换用别的智能体就在这里加一行，别散着写。
VENDORS = {
    "Claude Code": "noreply@anthropic.com",
    "Codex": "noreply@openai.com",
    "opencode": "noreply@opencode.ai",
}

#: 署名形态：`工具 (模型 版本) <厂商 noreply>`。括号里那一段是重点——同一个工具换代
#: 模型，写出来的代码差别比换工具本身还大，事后翻这一行是要知道哪个模型写的。
FORM = re.compile(r"(?P<tool>[^()<>]+) \((?P<model>[^()]+)\) <(?P<mail>[^<>\s]+)>")
HUMAN_FORM = re.compile(r"(?P<name>[^()<>\r\n]+) <(?P<mail>[^<>\s@]+@[^<>\s@]+)>")

EXAMPLE = "Co-Authored-By: Claude Code (Opus 5) <noreply@anthropic.com>"


def values(repo: Path, head: str = "HEAD") -> list[str]:
    """交付提交末尾那一块里的 Co-Authored-By，原样取值。"""
    message = git(repo, "show", "-s", "--format=%B", head)
    return trailers(repo, message).get("co-authored-by", [])


def commits(repo: Path, base: str, head: str = "HEAD") -> list[tuple[str, str]]:
    """分支自己的提交：(取值用的 hash, 给人看的 `短 hash 主题`)，不含合进来的 merge 与已发布到远端的。"""
    output = git(repo, "log", "--no-merges", "--format=%H%x00%h %s", f"{base}..{head}", *unpublished(repo))
    return [(line.partition("\0")[0], line.partition("\0")[2])
            for line in output.splitlines() if line.strip()]


def problems(label: str, found: list[str]) -> list[str]:
    if not found:
        return [f"提交「{label}」须有 Co-Authored-By 写明工具与模型，如 {EXAMPLE}"]
    problems = []
    has_agent = False
    for value in found:
        parsed = FORM.fullmatch(value)
        human = HUMAN_FORM.fullmatch(value)
        if (parsed is None and human is not None and human["name"].strip()
                and human["name"].strip().casefold() not in {tool.casefold() for tool in VENDORS}
                and human["mail"].casefold() not in VENDORS.values()):
            continue
        if parsed is None:
            problems.append(f"提交「{label}」的署名「{value}」形态不对，须为 工具 (模型 版本) "
                            f"<厂商 noreply>，如 {EXAMPLE}")
        elif parsed["tool"] not in VENDORS:
            problems.append(f"提交「{label}」的署名里，工具「{parsed['tool']}」未登记，"
                            "已登记：" + "、".join(VENDORS))
        elif parsed["mail"] != VENDORS[parsed["tool"]]:
            problems.append(f"提交「{label}」里 {parsed['tool']} 的署名地址须是 "
                            f"{VENDORS[parsed['tool']]}，不是 {parsed['mail']}")
        else:
            has_agent = True
    if not problems and not has_agent:
        problems.append(f"提交「{label}」须有 Co-Authored-By 写明工具与模型，如 {EXAMPLE}")
    return problems


def check(repo: Path, base: str, head: str = "HEAD") -> list[str]:
    """分支上每个非 merge 提交都要有署名，且每一条都合规。

    署名是事后追责唯一的入手处：出问题的那一行是哪个工具的哪个模型写的，只能从这里看。
    每个提交须有合规的智能体署名。人类共同作者使用「姓名 <邮箱>」，可以与智能体并列；
    智能体的模型、工具登记和厂商地址仍逐条检查。

    判据落在每个提交上，不是落在分支尖端：一次交付常常是好几个提交，只看最后一个
    等于只要收尾那次签对了，前面写代码的几次签成谁都放行——而要追的恰恰是写出那
    一行的提交。合进来的 merge 不算分支自己的产出，跳过。
    """
    found_problems = []
    for commit, label in commits(repo, base, head):
        found_problems += problems(label, values(repo, commit))
    return found_problems
