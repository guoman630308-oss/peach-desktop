"""作者头像与来源图标的本地缓存：地址只从固定表拼、字节先认成图再落盘、到期重取失败继续用旧的。"""
import re
import sys
import tempfile
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach import follow_assets   # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
ICO = b"\x00\x00\x01\x00\x01\x00" + b"\x00" * 24
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16
SVG = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"/>'
HTML = b"<!doctype html><html><body>Just a moment...</body></html>"


class SniffTests(unittest.TestCase):
    def test_common_icon_and_avatar_formats_are_recognised(self):
        self.assertEqual(follow_assets.sniff(PNG), "image/png")
        self.assertEqual(follow_assets.sniff(b"\xff\xd8\xff\xe0" + b"\x00" * 8), "image/jpeg")
        self.assertEqual(follow_assets.sniff(ICO), "image/x-icon")
        self.assertEqual(follow_assets.sniff(WEBP), "image/webp")
        self.assertEqual(follow_assets.sniff(SVG), "image/svg+xml")
        self.assertEqual(follow_assets.sniff(b"GIF89a" + b"\x00" * 8), "image/gif")

    def test_challenge_pages_and_empty_bodies_are_not_images(self):
        """站点回的机器人质询页是 HTML，落盘了就是一枚永远碎的图标。"""
        self.assertIsNone(follow_assets.sniff(HTML))
        self.assertIsNone(follow_assets.sniff(b""))
        self.assertIsNone(follow_assets.sniff(b'{"error":"blocked"}'))


class CachePathTests(unittest.TestCase):
    def test_paths_are_stable_and_separated_by_kind(self):
        root = Path("/cache")
        first = follow_assets.cache_path(root, "avatars", "mirror:kemono:fanbox/1")
        self.assertEqual(first, follow_assets.cache_path(root, "avatars", "mirror:kemono:fanbox/1"))
        self.assertEqual(first.parent, root / "avatars")
        self.assertNotEqual(first, follow_assets.cache_path(root, "icons", "mirror:kemono:fanbox/1"))
        self.assertTrue(re.fullmatch(r"[0-9a-f]{32}\.img", first.name), first.name)

    def test_never_refresh_means_a_present_file_is_always_fresh(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.img"
            self.assertFalse(follow_assets.is_fresh(path, None))
            path.write_bytes(PNG)
            modified = path.stat().st_mtime
            self.assertTrue(follow_assets.is_fresh(path, None, now=modified + 10 ** 9))
            self.assertTrue(follow_assets.is_fresh(path, 100, now=modified + 99))
            self.assertFalse(follow_assets.is_fresh(path, 100, now=modified + 100))


class CachedImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.calls = 0
        self.body = PNG

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self):
        self.calls += 1
        return self.body

    def test_the_first_request_fetches_and_later_ones_use_the_file(self):
        path = follow_assets.cached_image(self.root, "icons", "kemono", 3600, self.fetch)
        self.assertEqual(path.read_bytes(), PNG)
        self.assertEqual(follow_assets.content_type(path), "image/png")
        again = follow_assets.cached_image(self.root, "icons", "kemono", 3600, self.fetch)
        self.assertEqual(again, path)
        self.assertEqual(self.calls, 1, "新鲜的缓存不该再出网")

    def test_an_expired_file_is_refetched_and_replaced(self):
        path = follow_assets.cached_image(self.root, "icons", "kemono", 3600, self.fetch)
        modified = path.stat().st_mtime
        self.body = ICO
        refreshed = follow_assets.cached_image(
            self.root, "icons", "kemono", 3600, self.fetch, now=modified + 3600)
        self.assertEqual(refreshed.read_bytes(), ICO)
        self.assertEqual(self.calls, 2)

    def test_a_failed_refresh_keeps_the_old_image_and_backs_off(self):
        """站点挂了不能满屏碎图，也不能每次重绘都去打它几十枪。"""
        path = follow_assets.cached_image(self.root, "icons", "kemono", 3600, self.fetch)
        modified = path.stat().st_mtime
        self.body = None
        stale = follow_assets.cached_image(
            self.root, "icons", "kemono", 3600, self.fetch, now=modified + 3600)
        self.assertEqual(stale, path)
        self.assertEqual(stale.read_bytes(), PNG, "旧图要留着")
        self.assertEqual(self.calls, 2)
        soon = follow_assets.cached_image(
            self.root, "icons", "kemono", 3600, self.fetch,
            now=modified + 3600 + follow_assets.RETRY_SECONDS - 1)
        self.assertEqual(soon, path)
        self.assertEqual(self.calls, 2, "退避期内不再出网")
        self.body = ICO
        later = follow_assets.cached_image(
            self.root, "icons", "kemono", 3600, self.fetch,
            now=modified + 3600 + follow_assets.RETRY_SECONDS + 1)
        self.assertEqual(later.read_bytes(), ICO)
        self.assertEqual(self.calls, 3)
        self.assertFalse(path.with_suffix(".failed").exists(), "成功后清掉失败标记")

    def test_never_fetched_and_unreachable_is_none_until_the_backoff_passes(self):
        self.body = None
        self.assertIsNone(follow_assets.cached_image(self.root, "icons", "kemono", 3600, self.fetch))
        self.assertIsNone(follow_assets.cached_image(self.root, "icons", "kemono", 3600, self.fetch))
        self.assertEqual(self.calls, 1, "第一次失败后退避期内不再试")

    def test_never_refresh_serves_the_file_forever(self):
        path = follow_assets.cached_image(self.root, "icons", "kemono", None, self.fetch)
        modified = path.stat().st_mtime
        follow_assets.cached_image(self.root, "icons", "kemono", None, self.fetch,
                                   now=modified + 10 ** 9)
        self.assertEqual(self.calls, 1)


class FetchImageTests(unittest.TestCase):
    def _client(self, handler):
        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_only_a_200_image_body_counts(self):
        seen = []

        def upstream(request):
            seen.append(request)
            status = int(request.url.params.get("status", "200"))
            body = {"png": PNG, "html": HTML, "big": b"\x89PNG\r\n\x1a\n" + b"\x00" * follow_assets.MAX_BYTES}[
                request.url.params.get("body", "png")]
            return httpx.Response(status, content=body, request=request,
                                  headers={"content-type": "image/png"})

        with self._client(upstream) as client:
            self.assertEqual(follow_assets.fetch_image(client, "https://icons.test/a"), PNG)
            self.assertIsNone(follow_assets.fetch_image(client, "https://icons.test/a?status=404"))
            self.assertIsNone(follow_assets.fetch_image(client, "https://icons.test/a?body=html"),
                              "content-type 说是图也不信，字节认不出就不是图")
            self.assertIsNone(follow_assets.fetch_image(client, "https://icons.test/a?body=big"))
        for request in seen:
            self.assertNotIn("cookie", {k.lower() for k in request.headers})
            self.assertNotIn("authorization", {k.lower() for k in request.headers})

    def test_network_errors_are_a_miss_not_a_crash(self):
        def upstream(request):
            raise httpx.ConnectError("down", request=request)

        with self._client(upstream) as client:
            self.assertIsNone(follow_assets.fetch_image(client, "https://icons.test/a"))


class ShrinkImageTests(unittest.TestCase):
    """取图和显示图要的尺寸不是一个数：高清那份只用来检脸，落盘的按圆标存。"""

    def _jpeg(self, width, height):
        import cv2
        import numpy

        canvas = numpy.zeros((height, width, 3), dtype=numpy.uint8)
        canvas[:, :] = (30, 90, 200)
        ok, buffer = cv2.imencode(".jpg", canvas)
        self.assertTrue(ok)
        return bytes(buffer)

    def _size(self, payload):
        from peach.face_detect import decode

        image = decode(payload)
        height, width = image.shape[:2]
        return width, height

    def test_a_cover_is_stored_at_icon_size_with_its_shape_kept(self):
        """一张 4096 宽的封面按圆标那一档存，长宽比不动。

        存原图的话那一排八十多枚就是首屏几十兆。比例要留住：取景是按比例算的，
        压扁一次脸就挪到别处去了。
        """
        small = follow_assets.shrink_image(self._jpeg(4096, 2304))
        self.assertEqual(follow_assets.sniff(small), "image/jpeg")
        self.assertEqual(self._size(small),
                         (follow_assets.ICON_SIDE, follow_assets.ICON_SIDE * 9 // 16))

    def test_an_image_already_small_enough_is_stored_as_it_came(self):
        """本来就够小的不再编码一遍：重压一次只会掉画质。"""
        payload = self._jpeg(250, 141)
        self.assertIs(follow_assets.shrink_image(payload), payload)

    def test_something_that_will_not_decode_is_passed_through(self):
        """解不开的字节原样返回，由 `sniff` 那道闸去拦，不在这里判图片真假。"""
        self.assertEqual(follow_assets.shrink_image(HTML), HTML)


class IconSideTests(unittest.TestCase):
    """存多大由「这张脸占画面多少」定：页面能放大到哪，上限之一就是落盘那份还剩多少脸像素。"""

    def test_a_face_that_fills_the_frame_needs_no_more_than_the_base_size(self):
        record = {"face": {"w": 0.33}, "px": [1920, 1920]}
        self.assertEqual(follow_assets.icon_side(record), follow_assets.ICON_SIDE)

    def test_a_far_shot_is_stored_larger_so_the_face_survives_the_shrink(self):
        """脸占画面宽 4.5% 的 16:9 封面，存成 512px 时脸框只剩 23px。

        28px 的圆在双倍屏上放到六成要 33.6px 的脸——差这一截，页面就只能在「放不大」
        和「放糊」之间选一个。存大一点是唯一不牺牲画质的解法。
        """
        record = {"face": {"w": 0.045}, "px": [1920, 1080]}
        side = follow_assets.icon_side(record)
        self.assertGreater(side, follow_assets.ICON_SIDE)
        self.assertGreaterEqual(0.045 * side, follow_assets.FACE_PX_IN_STORE)

    def test_a_portrait_converts_between_the_two_axes(self):
        """脸框宽按图宽归一化，`side` 说的是长边：竖图不换算就会存得不够大。

        9:16 那张脸占宽的 8%，不换算算出来是 425px、还不到基准档，于是存成 512px、
        脸框只剩 23px；换算过来要 756px，脸框才够 34px。
        """
        record = {"face": {"w": 0.08}, "px": [1080, 1920]}
        side = follow_assets.icon_side(record)
        self.assertGreater(side, follow_assets.ICON_SIDE)
        self.assertGreaterEqual(0.08 * side * 1080 / 1920, follow_assets.FACE_PX_IN_STORE)

    def test_a_speck_of_a_face_stops_at_the_ceiling(self):
        """画面里只有一点点脸的图按算式要存到两千像素，那是一枚两百 KB 的圆标。"""
        self.assertEqual(follow_assets.icon_side({"face": {"w": 0.01}, "px": [1920, 1080]}),
                         follow_assets.MAX_ICON_SIDE)

    def test_records_without_a_face_take_the_base_size(self):
        for record in (None, {}, {"face": {}}, {"face": {"w": 0.045}},
                       {"face": {"w": 0}, "px": [1920, 1080]}, "nonsense"):
            self.assertEqual(follow_assets.icon_side(record), follow_assets.ICON_SIDE, record)


class TrimLetterboxTests(unittest.TestCase):
    """站点上的 3D 封面常把 21:9 的画面压进 16:9 的帧里，上下各留一道纯黑。"""

    def _framed(self, width, height, bar):
        import cv2
        import numpy

        canvas = numpy.zeros((height, width, 3), dtype=numpy.uint8)
        canvas[bar:height - bar, :] = (40, 110, 210)
        ok, buffer = cv2.imencode(".jpg", canvas)
        self.assertTrue(ok)
        return bytes(buffer)

    def _size(self, payload):
        from peach.face_detect import decode

        image = decode(payload)
        height, width = image.shape[:2]
        return width, height

    def test_the_black_bars_come_off_and_the_picture_does_not(self):
        """黑边跟着进圆标是两重损失：圆里露出黑条，画面还被撑高、脸被 cover 缩得更小。"""
        trimmed = follow_assets.trim_letterbox(self._framed(512, 288, 40))
        width, height = self._size(trimmed)
        self.assertEqual(width, 512)
        self.assertAlmostEqual(height, 288 - 80, delta=4)

    def test_an_image_without_bars_is_handed_back_untouched(self):
        """没有黑边就一个字节都不动：重编码一次只会掉画质。"""
        payload = self._framed(512, 288, 0)
        self.assertIs(follow_assets.trim_letterbox(payload), payload)

    def test_an_all_black_image_is_left_alone(self):
        """整幅都黑的话「黑边」这个判据认不出任何东西，裁下去就只剩一条线。"""
        import cv2
        import numpy

        ok, buffer = cv2.imencode(".jpg", numpy.zeros((288, 512, 3), dtype=numpy.uint8))
        self.assertTrue(ok)
        payload = bytes(buffer)
        self.assertIs(follow_assets.trim_letterbox(payload), payload)

    def test_a_mostly_dark_picture_is_not_mistaken_for_bars(self):
        """夜景和暗调渲染整片都压得很暗，按平均亮度一刀切会把画面当黑边裁掉。"""
        import cv2
        import numpy

        canvas = numpy.full((288, 512, 3), 14, dtype=numpy.uint8)
        ok, buffer = cv2.imencode(".jpg", canvas)
        self.assertTrue(ok)
        payload = bytes(buffer)
        self.assertIs(follow_assets.trim_letterbox(payload), payload)

    def test_something_that_will_not_decode_is_passed_through(self):
        self.assertEqual(follow_assets.trim_letterbox(HTML), HTML)


class MirrorAvatarTests(unittest.TestCase):
    def test_avatars_only_come_from_providers_that_actually_serve_one(self):
        """按 peach-reference-evidence：实测拿得到才给，取不到写「未取得」。

        2026-08-27 实测 `https://kemono.cr/icons/fanbox/30917150` → 302 →
        `img.kemono.cr`，200 `image/webp` 160×160；pawchive.pw 同路径 200。
        rule34 系没有可用样本可测，所以不给 URL——不猜一个路径。
        """
        self.assertEqual(follow_assets.mirror_avatar_url("kemono", "fanbox/30917150"),
                         "https://kemono.cr/icons/fanbox/30917150")
        self.assertEqual(follow_assets.mirror_avatar_url("pawchive", "fanbox/30917150"),
                         "https://pawchive.pw/icons/fanbox/30917150")
        for provider, ref in (("rule34video", "1290582"),
                              ("rule34xxx", "lazyprocrastinator"),
                              ("f95zone", "50685"),
                              ("kemono", "no-slash")):
            self.assertIsNone(follow_assets.mirror_avatar_url(provider, ref),
                              f"{provider} 没有实测过的头像来源，不该猜一个")


class SourceIconTableTests(unittest.TestCase):
    def test_icon_addresses_are_https_and_the_page_lists_the_same_providers(self):
        """页面只认名单、服务端只认地址：两边各写一份就会漂，漂了就是一格空白。"""
        for provider, url in follow_assets.SOURCE_ICON_URLS.items():
            self.assertTrue(url.startswith("https://"), (provider, url))
        root = Path(__file__).resolve().parents[1]
        listing = (root / "frontend" / "src" / "react" / "follow-manage" / "follow-manage.ts").read_text(encoding="utf-8")
        raw = re.search(r"export const SOURCE_ICON_PROVIDERS = new Set\(\[(.*?)\]\);", listing, re.S).group(1)
        listed = {item.strip().strip("'") for item in raw.split(",") if item.strip()}
        self.assertEqual(listed, set(follow_assets.SOURCE_ICON_URLS))
        # 页面里没有任何一条站点图标的远端地址：图标全部经 Peach 落盘后再给页面，
        # 图标地址怎么拼在 `frontend/test/react/follow-marks.test.ts`。
        for path in sorted((root / "frontend" / "src").rglob("*")):
            if path.suffix in {".js", ".ts", ".tsx"}:
                with self.subTest(path=path):
                    self.assertNotIn("favicon.ico'", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
