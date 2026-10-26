"""从这个人自己的作品封面上截一张脸当头像：图库给不出认得准的人像时的那一档。

整张封面装进圆框只剩一块背景（FC2 是十六比九的剧照，JAV 是双联封套），所以这里
不装封面，装的是封面上那张脸周围的一块方图——和挑图弹层里「作品画面」那一路人手
框出来的是同一种东西，只是框由 YuNet 的脸框定。

**挑哪一张封面按脸有多少像素，不按封面多大。** 同一个人的封面有 3360×1890 的官方原图，
也有 276×154 的缩略图；原图上戴着面具检不出脸时，那张原图对头像毫无用处。脸宽的像素
数同时回答了两件事：这张图上有没有能认的脸，放大进圆框之后清不清楚。缩略图上检出的
脸天然最窄，所以它只在别的封面都检不出脸时才轮得到——这一档最差就是缩略图。

只看单人作品：一部片挂着两个演员时，封面上那张脸是谁机器答不出来。单人作品也只是
「多半是她」，不是核实过的身份，所以来源记录标 `identity_verified: false`，这张图
永远排在图库与名录人像之后，出现那一档就被换掉（`avatar_followup`）。

个人博主（过 `work_portrait_predicate` 的发布账号）走 `sheet_faces`：作品没有番号封面，
画面取自接触印相九格，截法与来源记录同上（ADR-0099）。
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import images
from .avatar_face import face_px_width
from .western_artwork import artwork_cast_size, artwork_key

PROVIDER = "cover-face"
SOURCE_KIND = "cover_face_crop"
#: 批处理从整张封面装上的头像。它们同样没核实身份，还多半只剩一块背景，
#: 截出任何一张脸都比它强。
WHOLE_COVER_PROVIDERS = ("cover", "cover-fallback", "poster-fallback")
#: 方框边长是脸宽的几倍。YuNet 的框只框到额头和下巴，放到 2.4 倍才装得下头发和下巴
#: 下面一点；圆框再切掉四角，脸不贴边。
FACE_SPAN = 2.4
#: 接触印相是三行三列（`PreviewService.poster` 按同一个网格切格）。
SHEET_GRID = 3
#: 个人博主一次最多看这么多部作品的印相：一部九格，每格检两次脸。
MAX_SHEET_WORKS = 24


@dataclass(frozen=True)
class CoverFace:
    """一部作品的封面，连同在它上面检出的那张脸。"""

    asset_id: int
    code: str
    body: bytes
    width: int
    height: int
    record: dict
    #: 接触印相的第几格（`sheet_faces`）；封面上截的是 None。
    cell: int | None = None

    @property
    def face_px(self) -> int:
        return face_px_width(self.record)


def single_works(connection, entity_id: int,
                 role: str = "performer") -> list[tuple[int, str, str]]:
    """这个人以 `role` 独占的作品，`(asset_id, 番号, 接触印相路径)`，回收站里的不算。

    女优按 `performer` 取，同一部片不挂别的演员；个人博主按 `creator` 取，同一部片不挂
    别的发布账号。
    """
    return [(int(asset_id), str(code or ''), str(snapshot or ''))
            for asset_id, code, snapshot in connection.execute(
        "SELECT a.id,a.code,a.snapshot_path FROM asset a JOIN asset_entity ae ON ae.asset_id=a.id "
        "WHERE ae.entity_id=? AND ae.role=? "
        "AND a.disposal IS NULL "
        "AND NOT EXISTS(SELECT 1 FROM asset_entity other WHERE other.asset_id=a.id "
        "AND other.role=ae.role AND other.entity_id<>ae.entity_id) "
        "ORDER BY a.id", (int(entity_id), role))]


def faces(connection, cover_root: Path, entity_id: int, probe) -> list[CoverFace]:
    """检得出脸的那些封面，脸最宽的在前；脸一样宽时像素多的在前。

    `probe` 是 `avatar_face.FaceProbe`：`on_bytes` 给出带 `px` 与脸框的记录。同一个番号
    分几段、或在两个目录各有一份时封面只有一张，只检一次。

    面具、眼罩、脸贴照样算脸：梨奈的四张高清封面都戴着，YuNet 在默认门槛上全认了出来。
    没认出来的那几张不靠降门槛去捞——同几张图上 0.2 到 0.45 分的框落在手、胸口和
    身体上，比真脸还宽，按脸宽排序会排到最前面。

    截出来的那块方图上也要再检得出脸（`readable_cut`）。卖家自己打了模糊的商品图，
    整张缩小着看检得出一张很宽的脸，截出来放大就只剩一团色块：石川祐奈的
    `FC2-PPV-3202758` 脸宽 616 像素排在最前，装上的头像认不出是谁，同一个人另两部片的
    封面却清清楚楚。
    """
    found: list[CoverFace] = []
    seen: set[str] = set()
    for asset_id, code, _snapshot in single_works(connection, entity_id):
        key = artwork_key(asset_id, code)
        if not key or key in seen:
            continue
        seen.add(key)
        cover = Path(cover_root) / f'{key}.jpg'
        if artwork_cast_size(cover, 1) > 1:
            continue
        try:
            body = cover.read_bytes()
        except OSError:
            continue
        record = probe.on_bytes(body)
        px = (record or {}).get("px") or [0, 0]
        candidate = CoverFace(asset_id, key, body, int(px[0]), int(px[1]), record or {})
        if candidate.face_px > 0 and readable_cut(candidate, probe):
            found.append(candidate)
    found.sort(key=lambda face: (face.face_px, face.width * face.height), reverse=True)
    return found


def sheet_cells(body: bytes) -> list[tuple[int, bytes]]:
    """接触印相切成九格，`(格号, 字节)`。格号与 `/poster` 的 `cell` 同一个编号：行优先，0 起。"""
    size = images.measure_image_size(body)
    if size is None:
        return []
    width, height = size
    cells = []
    for cell in range(SHEET_GRID * SHEET_GRID):
        col, row = cell % SHEET_GRID, cell // SHEET_GRID
        box = (width * col // SHEET_GRID, height * row // SHEET_GRID,
               width * (col + 1) // SHEET_GRID, height * (row + 1) // SHEET_GRID)
        if (piece := images.crop_to_box(body, box)) is not None:
            cells.append((cell, piece))
    return cells


def sheet_faces(connection, cover_root: Path, entity_id: int, probe,
                snapshot_file: Callable[[str], Path | None], *,
                role: str = "creator") -> list[CoverFace]:
    """个人博主独占的作品里检得出脸的那些画面，一部作品一张，脸最宽的在前。

    博主作品大多没有番号，封面目录里没有它们的封面（`artwork_key` 落到 `ASSET-ID-<id>`，
    多数没有那张图）；画面来自接触印相的九格，有封面的作品封面也算一格。每部作品只留
    脸最宽、截出来还检得出脸的那一格：互证数的是作品，同一部片的两格不算两份证据。

    `snapshot_file` 把账本里的印相路径换成本机文件，取不到是 None（`WebContract.snapshot_file`）。
    最多看 `MAX_SHEET_WORKS` 部作品。
    """
    found: list[CoverFace] = []
    seen: set[str] = set()
    for asset_id, code, snapshot in single_works(connection, entity_id, role):
        if len(seen) >= MAX_SHEET_WORKS:
            break
        key = artwork_key(asset_id, code)
        if key in seen:
            continue
        pictures: list[tuple[int | None, bytes]] = []
        cover = Path(cover_root) / f"{key}.jpg"
        if artwork_cast_size(cover, 1) <= 1:
            try:
                pictures.append((None, cover.read_bytes()))
            except OSError:
                pass
        sheet = snapshot_file(snapshot) if snapshot else None
        if sheet is not None:
            try:
                pictures += sheet_cells(sheet.read_bytes())
            except OSError:
                pass
        if not pictures:
            continue
        seen.add(key)
        best: CoverFace | None = None
        for cell, body in pictures:
            record = probe.on_bytes(body) or {}
            px = record.get("px") or [0, 0]
            candidate = CoverFace(asset_id, key, body, int(px[0]), int(px[1]), record, cell)
            if (candidate.face_px > (best.face_px if best else 0)
                    and readable_cut(candidate, probe)):
                best = candidate
        if best is not None:
            found.append(best)
    found.sort(key=lambda face: (face.face_px, face.width * face.height), reverse=True)
    return found


def face_square(record: dict | None, width: int, height: int) -> tuple[int, int, int, int] | None:
    """一份人脸记录在 `width`×`height` 那张图上框出的方图；没有脸，或记录说的是另一张图，
    就是 None。

    批处理截头像和挑图弹层给封面预设的框都走这里：两边框得不一样，人在弹层里看到的
    「这张封面截出来的样子」就不是批处理真会装上去的那一块。记录里的 `px` 对不上这张
    图时不用它——封面会被更大的那张原子替换，旧记录的脸心落在新图上是一块错位的区域。
    """
    detail = (record or {}).get("face")
    px = (record or {}).get("px") or [0, 0]
    face_px = face_px_width(record)
    if not isinstance(detail, dict) or face_px <= 0 or list(px) != [width, height]:
        return None
    side = max(1, min(round(face_px * FACE_SPAN), width, height))
    left = round(float(detail["cx"]) * width - side / 2)
    top = round(float(detail["cy"]) * height - side / 2)
    left = max(0, min(left, width - side))
    top = max(0, min(top, height - side))
    return left, top, left + side, top + side


def crop_box(face: CoverFace) -> tuple[int, int, int, int]:
    """脸周围那块方图在封面上的像素框。边长夹在封面短边以内，整块推回画面里。"""
    return face_square(face.record, face.width, face.height) or (0, 0, face.width, face.height)


def cut(face: CoverFace) -> tuple[bytes, dict] | None:
    """切出方图，连同一份来源记录（形状与 `avatar_picker.install` 的 `origin` 一致）。"""
    box = crop_box(face)
    body = images.crop_to_box(face.body, box)
    if body is None:
        return None
    sheet = {"sheet_cell": face.cell} if face.cell is not None else {}
    return body, {"source": "cover face", "provider": PROVIDER, "source_kind": SOURCE_KIND,
                  "external_id": face.code, "upstream_url": f"peach:cover-face/{face.code}",
                  "asset_id": face.asset_id,
                  "asset_code": face.code, "crop_box": list(box),
                  "crop_source_px": [face.width, face.height],
                  "face_px": face.face_px, "identity_verified": False, **sheet}


def _has_face(probe, body: bytes | None) -> bool:
    return body is not None and isinstance((probe.on_bytes(body) or {}).get("face"), dict)


def readable_cut(face: CoverFace, probe) -> bool:
    """这张封面截出来的那块方图上还检得出脸。"""
    cropped = cut(face)
    return cropped is not None and _has_face(probe, cropped[0])


def installed_face_readable(avatar_root: Path, kind: str, entity_id: int, probe) -> bool:
    """装着的那张头像上检得出脸。检不出的封面截图不管当时脸多宽，任何一张认得出的脸都比它强。"""
    from .previews import entity_image_key

    try:
        body = (Path(avatar_root) / f"{entity_image_key(kind, int(entity_id))}.img").read_bytes()
    except OSError:
        return False
    return _has_face(probe, body)


def installed_face_px(avatar_root: Path, kind: str, entity_id: int) -> int | None:
    """装着的那张如果是这一档截的，返回它当时那张脸的像素宽；别的来源（或没有）是 None。

    只有从封面来的图才会被这一档换掉：整张封面装上的那种按 0 算，任何一张脸都比它宽。
    人挑的、图库装的，一律不碰。
    """
    from .previews import entity_image_key

    path = Path(avatar_root) / f"{entity_image_key(kind, int(entity_id))}.img.provenance.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    if record.get("provider") in WHOLE_COVER_PROVIDERS:
        return 0
    if record.get("provider") != PROVIDER:
        return None
    try:
        return int(record.get("face_px") or 0)
    except (TypeError, ValueError):
        return 0
