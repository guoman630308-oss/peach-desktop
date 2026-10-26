"""Content-addressed evidence cache for external studio Logo candidates."""
from __future__ import annotations

import hashlib
import io
import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .fsutil import atomic_write_bytes


POLICY_VERSION = "studio-logo-provider-v1"
FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}


@dataclass(frozen=True)
class LogoRaster:
    width: int
    height: int
    mime_type: str
    extension: str
    sha256: str
    perceptual_hash: str


@dataclass(frozen=True)
class LogoProvenance:
    studio: str
    handle: str
    platform: str
    resolver_url: str
    source_url: str
    width: int
    height: int
    mime_type: str
    sha256: str
    perceptual_hash: str
    object_name: str
    fetched_at: str
    policy_version: str = POLICY_VERSION


def inspect_logo(data: bytes) -> LogoRaster | None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            image_format = (image.format or "").upper()
            image.load()
            gray = image.convert("RGBA").convert("L").resize(
                (9, 8), Image.Resampling.LANCZOS,
            )
            pixels = list(gray.getdata())
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        return None
    media = FORMATS.get(image_format)
    if media is None:
        return None
    bits = 0
    for y in range(8):
        for x in range(8):
            bits = (bits << 1) | int(pixels[y * 9 + x] < pixels[y * 9 + x + 1])
    return LogoRaster(
        width, height, media[0], media[1], hashlib.sha256(data).hexdigest(),
        f"{bits:016x}",
    )


class LogoCandidateCache:
    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()

    def _request_path(self, url: str) -> Path:
        return self.root / "requests" / (
            hashlib.sha256(url.encode("utf-8")).hexdigest() + ".json"
        )

    def lookup(self, url: str) -> bytes | None:
        try:
            request = json.loads(self._request_path(url).read_text(encoding="utf-8"))
            data = (self.root / "objects" / request["object_name"]).read_bytes()
            return data if hashlib.sha256(data).hexdigest() == request["sha256"] else None
        except (OSError, KeyError, TypeError, ValueError):
            return None

    def store(self, url: str, data: bytes, raster: LogoRaster) -> Path:
        object_path = self.root / "objects" / f"{raster.sha256}{raster.extension}"
        with self._lock:
            if not object_path.is_file():
                atomic_write_bytes(object_path, data)
            request = {
                "url": url, "sha256": raster.sha256,
                "object_name": object_path.name, "mime_type": raster.mime_type,
                "width": raster.width, "height": raster.height,
                "perceptual_hash": raster.perceptual_hash,
            }
            atomic_write_bytes(
                self._request_path(url),
                (json.dumps(request, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            )
        return object_path

    def provenance(self, record: LogoProvenance) -> Path:
        key = hashlib.sha256(record.studio.encode("utf-8")).hexdigest()[:16]
        path = self.root / "evidence" / f"{key}-{record.sha256}.json"
        with self._lock:
            if not path.is_file():
                atomic_write_bytes(
                    path,
                    (json.dumps(asdict(record), ensure_ascii=False, indent=2) + "\n").encode(
                        "utf-8"
                    ),
                )
        return path


def provenance_now(**values) -> LogoProvenance:
    return LogoProvenance(
        **values,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def hash_distance(left: str, right: str) -> int:
    if not left or not right:
        return 64
    return (int(left, 16) ^ int(right, 16)).bit_count()


def installed_logo_hashes(
    root: Path,
) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    by_key: dict[str, tuple[str, str]] = {}
    by_hash: dict[str, str] = {}
    for path in sorted(root.glob("*.img")):
        try:
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            raster = inspect_logo(data)
        except OSError:
            continue
        by_key[path.stem] = (digest, raster.perceptual_hash if raster else "")
        by_hash.setdefault(digest, path.stem)
    return by_key, by_hash
