"""把本机已取得的厂牌标识收进 `resources/marks/`，随仓库分发（ADR-0026）。

只搬运本机 `peach-data/generated/logos/` 里已经有的字节，不联网、不解析、不抓取。
收录判据是 ADR-0026 那五条，缺一条就留在本机：

1. provenance 里有 `source_url`；没有来源证据的不收，不靠文件名推断归属。
2. 归属是法人实体。本目录按定义只装厂牌标识，自然人头像在 `avatars/`，不在输入范围。
3. 用途是标识：`variant` 为 `icon`、`logo` 或没有变体的裸文件。
4. 单文件不超过 256 KB。
5. 总量不超过 8 MB。

输出形态和缓存目录不同，这是收录的重点：文件名带真实扩展名（`Attackers.png`），
扩展名按字节嗅探定而不是照抄源地址——源地址的后缀经常和内容对不上，`.jpg` 里装 PNG
在这批里就有。provenance 集中进一份 `manifest.json`，不为每个图放边车。

可重复执行：同一份输入两次运行产出逐字节一致的清单和文件，不产生无变化的改动。清单
按落盘名排序，时间戳取自源边车的 `imported_at` 而不是运行时刻——用 `now()` 的话每次
运行都会刷出一份全新的 diff。

默认只报告，`--apply` 才写盘。写盘会先清掉目标目录里清单不再登记的文件，否则厂牌改名
后旧名字那份会永远留在仓库里。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach import brand_marks
from peach.config import GENERATED_DIR
from peach.follow_assets import sniff
from peach.previews import LOGO_VARIANTS

#: 嗅探出的 MIME → 落盘扩展名。`brand_marks.CONTENT_TYPES` 是它的反查表，两边必须
#: 一一对应，否则收得进去、运行时认不出类型。
EXTENSIONS = {mime: ext for ext, mime in brand_marks.CONTENT_TYPES.items()}


def variant_of(stem: str) -> str:
    """`Attackers.icon` → `icon`；裸文件返回空串。"""
    for variant in LOGO_VARIANTS:
        if stem.endswith(f".{variant}"):
            return variant
    return ""


def collect(source: Path) -> tuple[list[dict], list[dict]]:
    """扫源目录，返回（收录条目, 落选条目）。两边都带原因，落选的要能解释。"""
    taken: list[dict] = []
    skipped: list[dict] = []
    for path in sorted(source.glob("*.img")):
        stem = path.name[:-len(".img")]
        sidecar = path.with_name(f"{path.name}.provenance.json")
        meta = {}
        if sidecar.is_file():
            try:
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
        url = meta.get("source_url") or meta.get("url") or meta.get("resolved_url")
        if not url:
            skipped.append({"name": stem, "reason": "no_source_url"})
            continue
        data = path.read_bytes()
        if len(data) > brand_marks.MAX_FILE_BYTES:
            skipped.append({"name": stem, "reason": "too_large", "bytes": len(data)})
            continue
        mime = sniff(data)
        extension = EXTENSIONS.get(mime or "")
        if not extension:
            skipped.append({"name": stem, "reason": "unknown_format", "sniffed": mime})
            continue
        taken.append({
            "file": f"{stem}{extension}",
            "studio": stem.rsplit(".", 1)[0] if variant_of(stem) else stem,
            "variant": variant_of(stem),
            "source": meta.get("source", ""),
            "source_url": url,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "content_type": mime,
            "imported_at": meta.get("imported_at") or meta.get("cached_at", ""),
            "_data": data,
        })
    taken, same = _drop_copies_of_base(taken)
    skipped.extend(same)
    taken.sort(key=lambda row: row["file"])
    return taken, skipped


def _drop_copies_of_base(taken: list[dict]) -> tuple[list[dict], list[dict]]:
    """和同名裸文件字节相同的 `icon`／`logo` 不收。

    指定来源表一张图管两位，装的时候大位、小位和裸文件各落一份同样的字节；2026-09-23
    量过，按内容去重前 9.9 MB，去重后 5.2 MB。`PreviewService.logo` 找不到变体就回落到
    裸文件，所以少了这份副本，每个位置取到的仍是同一张图。
    """
    base_sha = {row["studio"]: row["sha256"] for row in taken if not row["variant"]}
    kept, same = [], []
    for row in taken:
        if row["variant"] and base_sha.get(row["studio"]) == row["sha256"]:
            same.append({"name": row["file"], "reason": "same_as_base"})
        else:
            kept.append(row)
    return kept, same


def write(taken: list[dict], target: Path) -> dict:
    """落盘并返回统计。清单不再登记的文件一并清掉。"""
    studios = target / "studios"
    studios.mkdir(parents=True, exist_ok=True)
    wanted = {row["file"] for row in taken}
    removed = []
    for path in sorted(studios.iterdir()):
        if path.is_file() and path.name not in wanted:
            path.unlink()
            removed.append(path.name)
    written = 0
    for row in taken:
        path = studios / row["file"]
        data = row["_data"]
        # 内容相同就不重写：让重复执行不产生 mtime 抖动，也不在 diff 里留空条目。
        if not path.is_file() or path.read_bytes() != data:
            path.write_bytes(data)
            written += 1
    manifest = {
        "version": 1,
        "note": "ADR-0026：法人实体标识随仓库分发。由 scripts/sync_brand_marks.py 生成。",
        "marks": [{key: row[key] for key in row if key != "_data"} for row in taken],
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8", newline="\n")
    return {"written": written, "removed": removed}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=GENERATED_DIR / "logos")
    parser.add_argument("--target", type=Path, default=brand_marks.MARKS_ROOT)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.source.is_dir():
        print(json.dumps({"ok": False, "error": "source missing",
                          "source": str(args.source)}, ensure_ascii=False))
        return 1
    taken, skipped = collect(args.source)
    total = sum(row["bytes"] for row in taken)
    result = {
        "ok": True,
        "applied": bool(args.apply),
        "taken": len(taken),
        "bytes": total,
        "skipped": len(skipped),
        "reasons": sorted({row["reason"] for row in skipped}),
    }
    if total > brand_marks.MAX_TOTAL_BYTES:
        # 判据 5。越过上限说明混进了不该收的东西，调高上限不是修法。
        result.update(ok=False, error="total exceeds budget",
                      budget=brand_marks.MAX_TOTAL_BYTES)
        print(json.dumps(result, ensure_ascii=False))
        return 1
    if args.apply:
        result.update(write(taken, args.target))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
