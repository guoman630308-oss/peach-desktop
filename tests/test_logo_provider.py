import csv
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from peach.images import bake_square
from peach.logo_provider import LogoCandidateCache, inspect_logo


ROOT = Path(__file__).resolve().parents[1]


def load_script():
    path = ROOT / "scripts" / "fetch_studio_avatar_candidates.py"
    spec = importlib.util.spec_from_file_location("fetch_studio_avatar_candidates_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def png_bytes(color=(20, 40, 60), size=(400, 400)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def pattern_png(kind="vertical", size=(400, 400)):
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    if kind == "vertical":
        draw.rectangle((0, 0, size[0] // 2, size[1]), fill="black")
    else:
        draw.rectangle((0, 0, size[0], size[1] // 2), fill="black")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


class Response:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status
        self.headers = {}


class FakeTransport:
    def __init__(self, images):
        self.images = list(images)
        self.calls = []

    def __call__(self, request, _timeout, _max_bytes):
        self.calls.append(request.url)
        if "unavatar.io" in request.url:
            return Response(json.dumps({"url": "https://pbs.example/logo.png"}).encode())
        return Response(self.images.pop(0))


class TwimgTransport:
    """unavatar 回缩略图地址，只有某一档真的存在——用来验证按档位下退。"""

    def __init__(self, resolved, available):
        self.resolved = resolved
        self.available = dict(available)
        self.calls = []

    def __call__(self, request, _timeout, _max_bytes):
        self.calls.append(request.url)
        if "unavatar.io" in request.url:
            return Response(json.dumps({"url": self.resolved}).encode())
        body = self.available.get(request.url)
        return Response(body) if body else Response(b"", 404)


class LogoProviderTests(unittest.TestCase):
    def test_content_cache_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = LogoCandidateCache(Path(tmp))
            body = png_bytes()
            raster = inspect_logo(body)
            path = cache.store("https://x/logo.png", body, raster)
            self.assertEqual(cache.lookup("https://x/logo.png"), body)
            path.write_bytes(b"tampered")
            self.assertIsNone(cache.lookup("https://x/logo.png"))


class StudioLogoScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.module = load_script()
        self.source = self.root / "missing.csv"
        self.handles = self.root / "handles.csv"
        self.out = self.root / "studio-logo-candidate-test.csv"
        self.health = self.root / "studio-logo-health-test.csv"
        self.images = self.root / "studio-logos"
        self.cache = self.root / "cache"
        self.installed = self.root / "installed"
        self.installed.mkdir()
        self.source.write_text("studio\nStudio A\n", encoding="utf-8-sig")
        self.handles.write_text("studio,handle\nStudio A,studio_a\n", encoding="utf-8-sig")

    def args(self, *extra):
        return [
            "--input", str(self.source), "--output", str(self.out),
            "--handles", str(self.handles), "--image-dir", str(self.images),
            "--cache-dir", str(self.cache), "--installed-dir", str(self.installed),
            "--health", str(self.health), "--interval", "0", *extra,
        ]

    def rows(self):
        with self.out.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_new_candidate_has_cache_provenance_hash_and_health(self):
        transport = FakeTransport([png_bytes()])
        self.assertEqual(self.module.main(self.args(), transport=transport), 0)
        row = self.rows()[0]
        self.assertEqual(row["content_state"], "new")
        self.assertEqual(row["accepted"], "True")
        self.assertEqual(len(row["sha256"]), 64)
        self.assertTrue((self.images / row["saved"]).is_file())
        self.assertTrue((self.cache / "evidence" / row["provenance_key"]).is_file())
        self.assertNotIn(str(self.root), row["saved"])
        with self.health.open(encoding="utf-8-sig", newline="") as handle:
            health = next(csv.DictReader(handle))
        self.assertEqual(health["new"], "1")
        self.assertEqual(health["errors"], "0")

    def test_same_installed_content_is_mechanical_unchanged(self):
        # 装着的那张也过 `bake_square`：目录里的文件都是这么来的，拿没归一的原图
        # 当在位图，比的就不是「同一张图换了尺寸」而是归一本身带来的差别。
        (self.installed / "Studio_A.img").write_bytes(
            bake_square(pattern_png(size=(400, 400))))
        refreshed = pattern_png(size=(200, 200))
        self.assertEqual(self.module.main(self.args(), transport=FakeTransport([refreshed])), 0)
        row = self.rows()[0]
        self.assertEqual(row["content_state"], "unchanged")
        self.assertEqual(row["accepted"], "False")
        self.assertNotEqual(row["sha256"], "")
        self.assertLessEqual(int(row["visual_distance"]), 4)

    def test_japanese_studios_get_their_own_file_names(self):
        """只留 ASCII 时这两家同为 `_____`，同一批里后取的那张盖掉先取的。"""
        from peach.previews import logo_key

        names = [self.module.safe_name(studio) for studio in ("セレブの友", "親父の個撮")]
        self.assertEqual(names, [logo_key("セレブの友"), logo_key("親父の個撮")])
        self.assertNotEqual(names[0], names[1])

    def test_an_installed_japanese_studio_mark_is_recognised(self):
        """已装标识按 `logo_key` 落盘；候选拿另一套名字去比，同一张图也会被当成新图。"""
        self.source.write_text("studio\nセレブの友\n", encoding="utf-8-sig")
        self.handles.write_text("studio,handle\nセレブの友,webhakusui\n", encoding="utf-8-sig")
        (self.installed / "セレブの友.img").write_bytes(
            bake_square(pattern_png(size=(400, 400))))
        refreshed = pattern_png(size=(200, 200))
        self.assertEqual(self.module.main(self.args(), transport=FakeTransport([refreshed])), 0)
        row = self.rows()[0]
        self.assertEqual(row["saved"].rsplit(".", 1)[0], "セレブの友")
        self.assertEqual(row["content_state"], "unchanged")

    def test_upload_original_is_preferred_over_the_resolver_thumbnail(self):
        """unavatar 会给缩小档：セレブの友 拿到的是 200×200，原图其实 242×242。"""
        stem = "https://pbs.twimg.com/profile_images/562462783950700545/abc"
        original = png_bytes(size=(242, 242))
        transport = TwimgTransport(f"{stem}_200x200.jpg", {
            f"{stem}.jpg": original,
            f"{stem}_400x400.jpg": png_bytes(size=(400, 400)),
            f"{stem}_200x200.jpg": png_bytes(size=(200, 200)),
        })
        self.assertEqual(self.module.main(self.args(), transport=transport), 0)
        row = self.rows()[0]
        self.assertEqual(row["resolved_url"], f"{stem}.jpg")
        self.assertEqual((row["width"], row["height"]), ("242", "242"))
        self.assertEqual(transport.calls[1], f"{stem}.jpg")

    def test_missing_original_falls_back_to_the_next_tier(self):
        """原图那一份不是每个账号都还在，缺了就退一档，不是整个厂牌取图失败。"""
        stem = "https://pbs.twimg.com/profile_images/562462783950700545/abc"
        transport = TwimgTransport(f"{stem}_400x400.jpg", {
            f"{stem}_200x200.jpg": png_bytes(size=(200, 200)),
        })
        self.assertEqual(self.module.main(self.args(), transport=transport), 0)
        row = self.rows()[0]
        self.assertEqual(row["resolved_url"], f"{stem}_200x200.jpg")
        self.assertEqual(row["content_state"], "new")
        self.assertEqual(row["accepted"], "True")
        self.assertEqual(transport.calls[1:], [
            f"{stem}.jpg", f"{stem}_400x400.jpg", f"{stem}_200x200.jpg",
        ])

    def test_every_tier_missing_is_an_error_not_a_silent_pass(self):
        stem = "https://pbs.twimg.com/profile_images/562462783950700545/abc"
        transport = TwimgTransport(f"{stem}_200x200.jpg", {})
        self.assertEqual(self.module.main(self.args(), transport=transport), 0)
        row = self.rows()[0]
        self.assertEqual(row["content_state"], "error")
        self.assertEqual(row["saved"], "")
        self.assertEqual(row["accepted"], "False")
        with self.health.open(encoding="utf-8-sig", newline="") as handle:
            health = next(csv.DictReader(handle))
        self.assertEqual(health["errors"], "1")
        self.assertEqual(health["last_error_message"], "HTTP 404")

    def test_refresh_surfaces_changed_upstream_content(self):
        (self.installed / "Studio_A.img").write_bytes(pattern_png("vertical"))
        fresh = pattern_png("horizontal")
        self.assertEqual(
            self.module.main(self.args("--refresh"), transport=FakeTransport([fresh])), 0,
        )
        row = self.rows()[0]
        self.assertEqual(row["content_state"], "changed")
        self.assertEqual(row["accepted"], "True")


if __name__ == "__main__":
    unittest.main()
