"""核对 Python、HTML 与文档文案；JS/TS 由前端语法检查器负责。"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import sys
import tokenize

ROOT = Path(__file__).resolve().parents[1]
DISABLE_MARKER = "copy-lint-disable-line"
RULES = json.loads((ROOT / "scripts/copy-rules.json").read_text(encoding="utf-8"))
SELF_DESCRIBING = frozenset({"scripts/check_copy_final_state.py", "tests/test_copy_final_state.py"})
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    term: str
    message: str
    severity: str = "error"

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.severity}: 「{self.term}」 — {self.message}"


def _scan_text(path: str, line_no: int, text: str, *, group: str = "prose") -> list[Finding]:
    if DISABLE_MARKER in text:
        return []
    found = [Finding(path, line_no, match.group(0), message, severity)
             for pattern, message, severity in RULES[group]
             for match in re.finditer(pattern, text, re.IGNORECASE)]
    # 同一段明确违规只报错误，避免同一位置出现重复提示。
    return [item for item in found if item.severity == "error"] or found


def scan_lines(path: str, source: str, *, skip_code_fences: bool) -> list[Finding]:
    """Markdown 围栏代码块保留命令与输出原文。"""
    found: list[Finding] = []
    fence = None
    for line_no, raw in enumerate(source.splitlines(), start=1):
        match = FENCE_RE.match(raw) if skip_code_fences else None
        if match:
            delimiter = match.group(1)
            if fence is None:
                fence = delimiter
            elif delimiter[0] == fence[0] and len(delimiter) >= len(fence):
                fence = None
            continue
        if fence is None:
            found.extend(_scan_text(path, line_no, raw))
    return found


def scan_python(path: str, source: str) -> list[Finding]:
    """读取注释和 AST 字符串，包括 f-string 的静态片段。"""
    found: list[Finding] = []
    lines = source.splitlines()

    def scan(text, start, group="prose", end=None):
        for offset, piece in enumerate(text.splitlines()):
            number = min(start + offset, end) if end is not None else start + offset
            if number <= len(lines) and DISABLE_MARKER not in lines[number - 1]:
                found.extend(_scan_text(path, number, piece, group=group))

    try:
        tree = ast.parse(source)
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                scan(token.string, token.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError) as error:
        return [Finding(path, getattr(error, "lineno", None) or 1, "syntax", "无法解析 Python 文案")]
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            scan(node.value, node.lineno, end=node.end_lineno)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            scan(node.name, node.lineno, "test_names")
    return found


def scan_html(path: str, source: str) -> list[Finding]:
    """HTML 只读取正文、注释与用户可见属性。"""
    found = []
    lines = source.splitlines()

    class ProseParser(HTMLParser):
        hidden = 0

        def emit(self, text):
            for offset, part in enumerate(text.splitlines()):
                line = self.getpos()[0] + offset
                if line <= len(lines) and DISABLE_MARKER not in lines[line - 1]:
                    found.extend(_scan_text(path, line, part))

        def handle_starttag(self, tag, attrs):
            if tag in {"script", "style"}:
                self.hidden += 1
            for key, value in attrs:
                if key in {"title", "alt", "placeholder", "aria-label"} and value:
                    self.emit(value)

        def handle_endtag(self, tag):
            if tag in {"script", "style"}:
                self.hidden = max(0, self.hidden - 1)

        def handle_data(self, data):
            if not self.hidden:
                self.emit(data)

        def handle_comment(self, data):
            self.emit(data)

    ProseParser().feed(source)
    return found


def targets(root: Path) -> list[Path]:
    picked = [root / name for name in ("AGENTS.md", "README.md", "README.en.md")]
    picked.extend(sorted((root / "docs").glob("*.md")))
    picked.extend(sorted((root / ".claude/skills").glob("*/SKILL.md")))
    picked.extend(sorted((root / "web").glob("*.html")))
    for directory in ("src", "scripts", "tests"):
        picked.extend(sorted((root / directory).rglob("*.py")))
    return list(dict.fromkeys(path.resolve() for path in picked if path.is_file()))


def scan_repo(root: Path = ROOT) -> list[Finding]:
    found = []
    for path in targets(root):
        rel = path.relative_to(root).as_posix()
        if rel in SELF_DESCRIBING:
            continue
        source = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            found.extend(scan_python(rel, source))
        elif path.suffix == ".html":
            found.extend(scan_html(rel, source))
        else:
            found.extend(scan_lines(rel, source, skip_code_fences=True))
    return sorted(found, key=lambda item: (item.path, item.line, item.term))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    findings = scan_repo(Path(parser.parse_args(argv).root).resolve())
    for finding in findings:
        print(finding)
    errors = sum(item.severity == "error" for item in findings)
    print(f"文案检查：{errors} 处错误，{len(findings) - errors} 处待人工核对。")
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
