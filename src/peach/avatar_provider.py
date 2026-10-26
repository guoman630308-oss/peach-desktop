"""Candidate-only cache and evidence rules for external performer avatars."""
from __future__ import annotations

import hashlib
import io
import json
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .avatar_face import FaceProbe, drop_sidecar, write_sidecar
from .fsutil import atomic_write_bytes


POLICY_VERSION = "performer-avatar-provider-v1"
SUPPORTED_FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
}


@dataclass(frozen=True)
class InspectedAvatar:
    width: int
    height: int
    mime_type: str
    extension: str
    sha256: str


@dataclass(frozen=True)
class AvatarProvenance:
    entity_id: int
    provider: str
    source_kind: str
    matched_name: str
    name_source: str
    external_id: str
    upstream_url: str
    width: int
    height: int
    mime_type: str
    sha256: str
    cache_path: str
    cached_at: str
    policy_version: str = POLICY_VERSION


def inspect_avatar(data: bytes) -> InspectedAvatar | None:
    """Decode the whole raster and derive type from bytes, never URL suffixes."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            image_format = (image.format or "").upper()
            image.verify()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        return None
    media = SUPPORTED_FORMATS.get(image_format)
    if media is None:
        return None
    mime_type, extension = media
    return InspectedAvatar(
        width=width,
        height=height,
        mime_type=mime_type,
        extension=extension,
        sha256=hashlib.sha256(data).hexdigest(),
    )


#: 自动装图的尺寸下限。竖构图人像不能套方图的短边门槛，所以长短边各判一次。
#: 批量补图的脚本与刮削派出的补头像后继用同一档；散成两份，总有一边先漂。
MIN_LONG_SIDE, MIN_SHORT_SIDE = 500, 300


def acceptable_avatar(avatar: InspectedAvatar, min_long: int, min_short: int) -> bool:
    return max(avatar.width, avatar.height) >= min_long and min(
        avatar.width, avatar.height
    ) >= min_short


def file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def install_entity_avatar(avatar_root: Path, kind: str, entity_id: int, body: bytes,
                          content_type: str, provenance: dict,
                          face: dict | None = None, *, probe_face: bool = True) -> Path:
    """把一张图装成 `{kind}-{id}.img` 那一套四个文件，`/entity-image` 真正读的位置。

    四样东西必须一起换，少换一样都在界面上表现成别的毛病：图本身、`.ct` 里的 MIME、
    `.provenance.json` 里这张图打哪来、`.face.json` 里的取景。取景那一样最容易漏——
    留着上一张图的脸框，页面会拿它给这一张取景、放大到一个空位置上，而那在界面上
    和「这张图本来就该这么显示」看不出区别。所以检不出脸时是**删掉** sidecar，
    不是留着不动。

    `probe_face=False` 给已经检过脸的调用方：挑图时按脸宽比过一轮的批处理，没有
    理由为同一张图再跑一次模型。
    """
    from .previews import entity_image_key

    destination = atomic_write_bytes(
        avatar_root / f"{entity_image_key(kind, entity_id)}.img", body)
    Path(f"{destination}.ct").write_text(content_type, encoding="utf-8")
    record = FaceProbe()(destination) if probe_face and face is None else face
    if record:
        write_sidecar(destination, record)
    else:
        drop_sidecar(destination)
    Path(f"{destination}.provenance.json").write_text(
        json.dumps({**provenance,
                    "imported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "purpose": "local performer identity cache"},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    return destination


class AvatarCandidateCache:
    """URL snapshots and immutable content objects outside the installed avatar tree."""

    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()

    def _request_path(self, url: str) -> Path:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.root / "requests" / f"{key}.json"

    def lookup(self, url: str) -> bytes | None:
        try:
            request = json.loads(self._request_path(url).read_text(encoding="utf-8"))
            content = self.root / "objects" / request["object_name"]
            data = content.read_bytes()
            if hashlib.sha256(data).hexdigest() != request["sha256"]:
                return None
            return data
        except (OSError, KeyError, TypeError, ValueError):
            return None

    def describe(self, url: str) -> dict | None:
        """这个地址取过什么：内容哈希、尺寸和类型，不打开图片本体。

        「这两个候选是不是同一张图」「它是不是正装着的那张」都只要哈希，而列一屏
        候选要问几十遍——每问一遍读一张几百 KB 的图，代价全落在点开弹层那一下。
        对象已经不在了就当没取过：记录还在不等于图还在。
        """
        try:
            request = json.loads(self._request_path(url).read_text(encoding="utf-8"))
            if not (self.root / "objects" / request["object_name"]).is_file():
                return None
            return request
        except (OSError, KeyError, TypeError, ValueError):
            return None

    def store(self, url: str, data: bytes, avatar: InspectedAvatar) -> Path:
        object_path = self.root / "objects" / f"{avatar.sha256}{avatar.extension}"
        request_path = self._request_path(url)
        with self._lock:
            if not object_path.is_file():
                atomic_write_bytes(object_path, data)
            request = {
                "url": url,
                "sha256": avatar.sha256,
                "object_name": object_path.name,
                "mime_type": avatar.mime_type,
                "width": avatar.width,
                "height": avatar.height,
            }
            atomic_write_bytes(
                request_path,
                (json.dumps(request, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            )
        return object_path

    def store_provenance(self, provenance: AvatarProvenance) -> Path:
        path = self.root / "evidence" / (
            f"performer-{provenance.entity_id}-{provenance.sha256}.json"
        )
        with self._lock:
            # entity + content hash makes this evidence immutable. Cache reuse must not
            # rewrite cached_at and make identical upstream evidence look like a new fact.
            if not path.is_file():
                atomic_write_bytes(
                    path,
                    (json.dumps(asdict(provenance), ensure_ascii=False, indent=2) + "\n").encode(
                        "utf-8"
                    ),
                )
        return path


def provenance_now(**values) -> AvatarProvenance:
    return AvatarProvenance(
        **values,
        cached_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def installed_avatar_hashes(
    avatar_dir: Path, live_entity_ids: set[int] | None = None,
) -> dict[str, int]:
    hashes: dict[str, int] = {}
    for path in sorted(avatar_dir.glob("performer-*.img")):
        try:
            entity_id = int(path.stem.split("-", 1)[1])
            if live_entity_ids is not None and entity_id not in live_entity_ids:
                continue
            hashes.setdefault(file_sha256(path), entity_id)
        except (OSError, ValueError):
            continue
    return hashes


def mark_duplicate_candidates(rows: list[dict], installed: dict[str, int]) -> None:
    """Reject exact byte duplicates deterministically; never guess perceptual identity."""
    owners = dict(installed)
    for row in rows:
        if row.get("section") != "missing" or row.get("verdict") != "ok":
            continue
        digest = str(row.get("sha256") or "")
        if not digest:
            row["verdict"] = "rejected"
            row["note"] = "缺少内容哈希，不能进入复核候选"
            continue
        owner = owners.get(digest)
        if owner is not None and owner != int(row["entity_id"]):
            row["verdict"] = "duplicate"
            row["duplicate_of_entity_id"] = owner
            row["note"] = f"与 performer-{owner} 的头像内容完全相同"
            continue
        owners[digest] = int(row["entity_id"])
