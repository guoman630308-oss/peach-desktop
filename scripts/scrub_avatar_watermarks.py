r"""把头像上的站点水印裁掉或修掉，原图归档。

用法（默认只看不写）：

    python scripts/scrub_avatar_watermarks.py
    python scripts/scrub_avatar_watermarks.py --apply
    python scripts/scrub_avatar_watermarks.py --marks my-boxes.csv --apply

**这一步是半自动的，别指望跑一遍就干净。** 检出走 PP-OCRv3 的 DB 文字检测头，
实心水印（`PRIVATE.com`、`TEAMSKEET.COM`、角落的 `DogFart (SM)`）一抓就中；
叠在皮肤上的半透明水印（`NUBILES.NET`、`MATTIEDOLL`）它给不出框，判据和局限写在
`peach.avatar_watermark` 的模块说明里。所以默认这一趟只写两样东西：一份候选 CSV，
和一叠画了框的标注图。人看图确认，检出漏掉的自己往 CSV 里补一行框，再带 `--marks`
跑 `--apply`。

`--apply` 做的事：原图连同 `.ct`、`.provenance.json`、`.face.json` 三个边车整套
搬进 `avatars-superseded/`，处理后的图写回原路径，provenance 补一段
`watermark_scrubbed` 记下裁了哪里、修了几处和新的哈希，取景 sidecar 按新图重算。
留原图是因为这一步不可逆：裁切改了构图，JPEG 重编码改了每一个像素。

JPEG 重编码这件事值得说明：裁切本身能做到无损，但那要 `jpegtran` 级别的工具链，
而这批图的用途是页面上一张 320px 的头像。这里按质量 95 重编码，换来的是不引入
新的外部依赖。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach import avatar_face, avatar_watermark, face_detect  # noqa: E402
from peach.config import GENERATED_DIR  # noqa: E402

#: 处理记录写在 provenance 的这个键下。有这个键就是已经处理过，不再重复处理。
STAMP = "watermark_scrubbed"
#: mime → OpenCV 编码器认的扩展名。`.img` 文件本身不带格式，格式在 `.ct` 边车里。
ENCODERS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
            "image/bmp": ".bmp"}
JPEG_QUALITY = 95
CSV_FIELDS = ("file", "x", "y", "w", "h", "score", "origin", "plan", "note")


def sidecars(image: Path) -> tuple[Path, Path, Path]:
    return (image.with_name(f"{image.name}.ct"),
            image.with_name(f"{image.name}.provenance.json"),
            avatar_face.sidecar_path(image))


def provenance(image: Path) -> dict:
    _, path, _ = sidecars(image)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def mime_of(image: Path) -> str:
    ct, _, _ = sidecars(image)
    try:
        return ct.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def face_box(detector, image):
    """主角那张脸在原图里的像素框，用来卡住裁切线。检不出脸就不设这项约束。"""
    faces = detector.detect(image)
    if not faces:
        return None
    best = face_detect.main_face(faces)
    height, width = image.shape[:2]
    return (int(best.cx * width - best.width * width / 2),
            int(best.cy * height - best.height * height / 2),
            int(best.width * width), int(best.height * height))


def read_marks(path: Path | None) -> dict[str, list[avatar_watermark.Mark]]:
    """人工确认或补写的框。只读 file 与四个坐标，其余列随便写什么都不影响。"""
    if path is None:
        return {}
    out: dict[str, list[avatar_watermark.Mark]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("file") or "").strip()
            if not name:
                continue
            try:
                box = [int(float(row[key])) for key in ("x", "y", "w", "h")]
            except (KeyError, TypeError, ValueError):
                continue
            if box[2] > 0 and box[3] > 0:
                out.setdefault(name, []).append(avatar_watermark.Mark(*box))
    return out


def encode(image, mime: str):
    """按原格式重新编码。认不出格式的不处理——猜一个格式等于悄悄换掉文件类型。"""
    import cv2

    suffix = ENCODERS.get(mime)
    if suffix is None:
        return None
    params = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY] if suffix == ".jpg" else []
    ok, buffer = cv2.imencode(suffix, image, params)
    return buffer.tobytes() if ok else None


def archive(image: Path, target: Path) -> None:
    """原图连边车整套搬走。搬不是复制：原路径马上要被处理后的图占用。"""
    target.mkdir(parents=True, exist_ok=True)
    for source in (image, *sidecars(image)):
        if source.exists():
            os.replace(source, target / source.name)


def install(image: Path, data: bytes, record: dict, info: dict,
            detector) -> None:
    """处理后的图落回原路径，provenance 补一段处理记录，取景 sidecar 按新图重算。"""
    staging = image.with_name(f"{image.name}.{uuid.uuid4().hex}.tmp")
    staging.write_bytes(data)
    os.replace(staging, image)
    width, height = info["px"]
    record[STAMP] = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "marks": info["marks"],
        "crop": info["crop"],
        "inpainted": info["inpainted"],
        "model": avatar_watermark.MODEL_PATH.name,
    }
    record["sha256"] = hashlib.sha256(data).hexdigest()
    record["width"], record["height"] = width, height
    _, provenance_path, face_path = sidecars(image)
    provenance_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    # 旧 sidecar 记的是裁切前的坐标。留着它，页面会拿旧框给新图取景，放大到一个
    # 空位置上——而那在界面上和「这张图本来就该这么显示」看不出区别。
    face_path.unlink(missing_ok=True)
    fresh = avatar_face.face_record(image, detector)
    if fresh:
        avatar_face.write_sidecar(image, fresh)


def annotate(image, marks, result, target: Path) -> None:
    """左右对照图：左边画着框的原图，右边处理后的结果。人就看这一张判断。"""
    import cv2
    import numpy as np

    marked = image.copy()
    for mark in marks:
        cv2.rectangle(marked, (mark.x, mark.y),
                      (mark.x + mark.w, mark.y + mark.h), (0, 0, 255), 3)
        cv2.putText(marked, f"{mark.score:.2f}", (mark.x, max(14, mark.y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
    panels = []
    for panel in (marked, result):
        scale = 380 / panel.shape[1]
        panels.append(cv2.resize(panel, (380, int(panel.shape[0] * scale))))
    tall = max(panel.shape[0] for panel in panels)
    pair = np.hstack([np.pad(panel, ((0, tall - panel.shape[0]), (0, 0), (0, 0)))
                      for panel in panels])
    target.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(target), pair)


def plan_of(info: dict) -> str:
    if info["crop"] and info["inpainted"]:
        return "crop+inpaint"
    if info["crop"]:
        return "crop"
    if info["inpainted"]:
        return "inpaint"
    return "none"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="头像去水印")
    parser.add_argument("--source", type=Path, default=GENERATED_DIR / "avatars")
    parser.add_argument("--archive", type=Path,
                        default=GENERATED_DIR / "avatars-superseded")
    parser.add_argument("--review", type=Path,
                        default=GENERATED_DIR / "watermark-review")
    parser.add_argument("--candidates", type=Path,
                        default=GENERATED_DIR / "watermark-candidates.csv")
    parser.add_argument("--marks", type=Path,
                        help="人工确认或补写的框，CSV，列 file,x,y,w,h")
    parser.add_argument("--model", type=Path, help="文字检测模型，默认按固定 URL 取")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true",
                        help="已经处理过的也重新处理")
    parser.add_argument("--apply", action="store_true", help="真的写盘")
    return parser


def main(argv: list[str] | None = None) -> int:
    import cv2

    args = build_parser().parse_args(argv)
    manual = read_marks(args.marks)
    detector = avatar_watermark.MarkDetector(args.model)
    faces = face_detect.FaceDetector()
    rows: list[dict] = []
    summary = {"ok": True, "seen": 0, "with_marks": 0, "content": 0,
               "written": 0, "skipped": 0, "reasons": []}
    reasons: set[str] = set()
    for image_path in sorted(args.source.glob("*.img")):
        if args.limit and summary["seen"] >= args.limit:
            break
        summary["seen"] += 1
        record = provenance(image_path)
        if STAMP in record and not args.force:
            summary["skipped"] += 1
            reasons.add("already_scrubbed")
            continue
        image = cv2.imread(str(image_path))
        if image is None:
            summary["skipped"] += 1
            reasons.add("unreadable")
            continue
        height, width = image.shape[:2]
        found = [mark for mark in detector.detect(image)
                 if avatar_watermark.on_edge(mark, width, height)]
        hand = manual.get(image_path.name, [])
        marks = found + hand
        if not marks:
            continue
        summary["with_marks"] += 1
        # 「这是画面文字不是水印」只用来质疑检出器。人手写的框是已经看过图的判断，
        # 不该被同一条数量判据推翻——补框的场合本来就是检出器判错了。
        content = not hand and avatar_watermark.looks_like_content(found)
        if content:
            summary["content"] += 1
            reasons.add("looks_like_content")
            result, info = image, {"crop": None, "inpainted": 0, "marks": len(marks),
                                   "px": [width, height]}
        else:
            face = face_box(faces, image)
            result, info = avatar_watermark.scrub(image, marks, face)
        plan = "content" if content else plan_of(info)
        # 来源按框是从哪一边来的记，不从分数反推：检出的分数也可以正好是 1.0。
        tagged = [(mark, "detected") for mark in found]
        tagged += [(mark, "manual") for mark in hand]
        for mark, origin in tagged:
            rows.append({
                "file": image_path.name, "x": mark.x, "y": mark.y,
                "w": mark.w, "h": mark.h, "score": mark.score,
                "origin": origin, "plan": plan,
                "note": f"{width}x{height}",
            })
        annotate(image, marks, result, args.review / f"{image_path.stem}.png")
        if content or not args.apply:
            continue
        mime = mime_of(image_path)
        data = encode(result, mime)
        if data is None:
            summary["skipped"] += 1
            reasons.add("unsupported_format")
            continue
        archive(image_path, args.archive)
        # 格式没变，所以 `.ct` 的内容照抄；它刚跟着原图被搬进归档目录，得补回来。
        image_path.with_name(f"{image_path.name}.ct").write_text(
            mime, encoding="utf-8")
        install(image_path, data, record, info, faces)
        summary["written"] += 1
    args.candidates.parent.mkdir(parents=True, exist_ok=True)
    with args.candidates.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    summary["reasons"] = sorted(reasons)
    summary["candidates"] = str(args.candidates)
    summary["review"] = str(args.review)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
