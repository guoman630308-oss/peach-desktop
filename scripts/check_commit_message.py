"""agent/* 分支提交时的 trailer 形态检查。"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "scripts"
from . import check_readme_impact, co_author


def check(repo: Path, message: str) -> list[str]:
    parsed = check_readme_impact.trailers(repo, message)
    problems = co_author.problems("待提交", parsed.get("co-authored-by", []))
    for name in ("Co-Authored-By", "README-Impact", "README-Reviewed"):
        mentioned = sum(line.partition(":")[0].casefold() == name.casefold()
                        for line in message.splitlines())
        values = parsed.get(name.casefold(), [])
        if mentioned != len(values):
            problems.append(f"{name} 须连续放在提交消息末尾的同一块，中间不要留空行。")
        if name == "README-Impact" and (mentioned or values):
            problems.extend(check_readme_impact.declaration(values)[1])
        if name == "README-Reviewed" and (mentioned or values):
            problems.extend(check_readme_impact.reviewed_declaration(values))
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message_file", type=Path)
    args = parser.parse_args(argv)
    repo = Path.cwd()
    try:
        branch = check_readme_impact.git(repo, "branch", "--show-current").strip()
        merge = check_readme_impact.git(repo, "rev-parse", "--git-path", "MERGE_HEAD").strip()
        if not branch.startswith("agent/") or (repo / merge).exists():
            return 0
        problems = check(repo, args.message_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, subprocess.CalledProcessError) as error:
        print(f"未取得提交校验结果：{error}", file=sys.stderr)
        return 1
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
