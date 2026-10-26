"""源码检出的改字工具：定位静态文案，核对文件摘要后保存。"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re


def files(root: Path):
    yield root / "web/index.html"
    yield root / "src/peach/library_sharing.py"
    yield from sorted((root / "frontend/src").rglob("*.js"))
    yield from sorted((root / "frontend/src").rglob("*.tsx"))
    yield from sorted((root / "frontend/src").rglob("*.ts"))


def candidates(root: Path, original: str) -> list[dict]:
    if not isinstance(original, str) or not original.strip() or len(original) > 2000:
        raise ValueError("请选择不超过 2000 字的静态文案")
    found = []
    for path in files(root):
        if not path.is_file() or ".test." in path.name:
            continue
        source = path.read_text(encoding="utf-8")
        if len(source) > 2_000_000:
            continue
        # 只定位完整文字节点或完整字符串，避免改到标识和代码的一部分。
        pattern = re.compile(r"(?P<quote>['\"`])" + re.escape(original) + r"(?P=quote)|>\s*(?P<jsx>" + re.escape(original) + r")\s*<")
        for match in pattern.finditer(source):
            start = match.start("jsx") if match.group("jsx") else match.start() + 1
            end = start + len(original)
            found.append({"file": path.relative_to(root).as_posix(), "start": start, "end": end,
                          "hash": hashlib.sha256(source.encode()).hexdigest(),
                          "line": source[:start].count("\n") + 1,
                          "quote": match.group("quote") or ""})
    return found[:100]


def save(root: Path, body: dict) -> dict:
    if not isinstance(body, dict):
        raise ValueError("修改内容必须是对象")
    original, replacement = body.get("original", ""), body.get("replacement", "")
    if not isinstance(replacement, str) or not replacement.strip() or len(replacement) > 2000:
        raise ValueError("新文案不能为空，且不能超过 2000 字")
    selected = body.get("candidate")
    if selected not in candidates(root, original):
        raise ValueError("源文件已变化，请重新选择文字")
    path = (root / selected["file"]).resolve()
    if path not in {p.resolve() for p in files(root)}:
        raise ValueError("不能修改这个文件")
    source = path.read_text(encoding="utf-8")
    quote = selected["quote"]
    if quote:
        escaped = replacement.replace("\\", "\\\\").replace(quote, "\\" + quote).replace("\n", "\\n").replace("\r", "\\r")
        if quote == "`":
            escaped = escaped.replace("${", "\\${")
    else:
        escaped = replacement.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if path.suffix == ".tsx":
            escaped = escaped.replace("{", "&#123;").replace("}", "&#125;")
    backup = root / "build/copy-editor/backups" / selected["hash"] / selected["file"]
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        backup.write_text(source, encoding="utf-8")
    path.write_text(source[:selected["start"]] + escaped + source[selected["end"]:], encoding="utf-8")
    record = root / "build/copy-editor/edits.json"
    edits = json.loads(record.read_text(encoding="utf-8")) if record.exists() else {}
    edits[original] = replacement
    record.write_text(json.dumps(edits, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "file": selected["file"], "needs_build": selected["file"].startswith("frontend/")}
