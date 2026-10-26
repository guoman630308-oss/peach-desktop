"""外链圆标：只在有把握时上色，其余原样放行。"""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach import link_marks   # noqa: E402


def ico(pixels, size=32, mode="RGBA"):
    image = Image.new(mode, (size, size), (0, 0, 0, 0) if mode == "RGBA" else (255, 255, 255))
    painter = image.load()
    for (x, y), colour in pixels.items():
        painter[x, y] = colour
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def glyph(colour, size=32, opaque_background=None):
    """中间一块实心方块当字形；`opaque_background` 不为 None 时整张图不透明。"""
    pixels = {}
    if opaque_background is not None:
        for x in range(size):
            for y in range(size):
                pixels[(x, y)] = opaque_background + (255,)
    for x in range(8, 24):
        for y in range(8, 24):
            pixels[(x, y)] = colour + (255,)
    return ico(pixels, size)


class RenderTests(unittest.TestCase):
    def test_a_single_colour_glyph_with_transparency_is_recoloured(self):
        """T-POWERS 就是这个形态：48×48、带透明、不透明像素只有一种绿。

        alpha 直接就是遮罩，所以能稳稳做出「品牌色底 + 白色主体」。
        """
        made = link_marks.render_mark(glyph((72, 168, 24)))
        self.assertIsNotNone(made)
        image = Image.open(io.BytesIO(made)).convert("RGBA")
        self.assertEqual(image.size, (link_marks.MARK_SIZE, link_marks.MARK_SIZE))
        centre = image.getpixel((image.width // 2, image.height // 2))
        self.assertEqual(centre[:3], (255, 255, 255), "主体应当是白的")
        edge = image.getpixel((image.width // 2, 3))
        self.assertGreater(edge[1], edge[0], "圆底应当是那个绿")

    def test_a_black_glyph_on_transparency_gets_a_neutral_disc(self):
        """ONE'S DOUBLE 的页脚字标是透明底纯黑字：原样贴在深色页面上看不见，做成深灰圆底白字。"""
        made = link_marks.render_mark(glyph((0, 0, 0)))
        image = Image.open(io.BytesIO(made)).convert("RGBA")
        self.assertEqual(image.getpixel((image.width // 2, image.height // 2))[:3],
                         (255, 255, 255))
        self.assertEqual(image.getpixel((image.width // 2, 3))[:3], link_marks.NEUTRAL_DISC)

    def test_a_black_and_white_glyph_is_not_flattened(self):
        """黑白两色都有的字形不是一种深浅：压成一色白字会把里面那层白抹掉。"""
        pixels = {(x, y): (0, 0, 0, 255) if x < 16 else (255, 255, 255, 255)
                  for x in range(8, 24) for y in range(8, 24)}
        self.assertIsNone(link_marks.glyph_mark(Image.open(io.BytesIO(ico(pixels))).convert("RGBA")))

    def test_an_opaque_favicon_is_left_alone(self):
        """HEYZO 是白底 + 多色字形。

        全不透明时只能拿四角猜主体，实测这条路会把白字反转成一团糟——
        半吊子的抠图比原图更难看，所以直接不做。
        """
        self.assertIsNone(link_marks.render_mark(glyph((240, 0, 96), opaque_background=(240, 240, 240))))

    def test_a_multi_hue_glyph_is_left_alone(self):
        """多色 logo 没有单一品牌色可取，硬取会挑出其中一块，看起来像认错了牌子。"""
        pixels = {}
        for x in range(8, 24):
            for y in range(8, 24):
                pixels[(x, y)] = ((240, 0, 0, 255) if x < 12 else
                                  (0, 200, 0, 255) if x < 16 else
                                  (0, 0, 240, 255) if x < 20 else (240, 200, 0, 255))
        self.assertIsNone(link_marks.render_mark(ico(pixels)))

    def test_a_broken_image_yields_nothing_instead_of_raising(self):
        """MOODYZ 的 favicon 根本不是有效图片。

        端点在页面加载时被调用，抛异常会变成 500；返回 None 让它回落到地球图标。
        """
        self.assertIsNone(link_marks.render_mark(b"<html>not an image</html>"))
        self.assertIsNone(link_marks.plain_mark(b"<html>not an image</html>"))

    def test_the_fallback_still_produces_a_png_at_the_mark_size(self):
        made = link_marks.plain_mark(glyph((240, 0, 96), opaque_background=(240, 240, 240)))
        self.assertIsNotNone(made)
        self.assertEqual(Image.open(io.BytesIO(made)).size,
                         (link_marks.MARK_SIZE, link_marks.MARK_SIZE))


class SmoothnessTests(unittest.TestCase):
    """2026-09-02 用户指出 `/link-mark?id=753`（FANZA）的「F」边缘全是台阶。

    原因有三层：遮罩是 `alpha >= 128` 的二值图（把源图自带的抗锯齿中间值一刀砍光）、
    这张二值图在源分辨率 48×48 上生成再拉到 64（把台阶一起放大）、字形没有被圆裁
    （白像素溢到圆外，圆边被啃出缺口）。少修一层都还是毛的，所以这里三条都要守住。
    """

    def test_the_circle_edge_carries_intermediate_alpha(self):
        """二值遮罩只会有 0 和 255。圆边上出现中间值，才说明超采样真的在起作用。"""
        made = link_marks.render_mark(glyph((72, 168, 24)))
        image = Image.open(io.BytesIO(made)).convert("RGBA")
        partial = [v for v in image.getchannel("A").tobytes() if 0 < v < 255]
        self.assertGreater(len(partial), 200, "圆边应当是渐变的，不是一刀切的")

    def test_the_glyph_edge_blends_into_the_brand_colour(self):
        """字形边同理：白与品牌色之间要有过渡色，否则字形本身仍是锯齿的。"""
        made = link_marks.render_mark(glyph((72, 168, 24)))
        image = Image.open(io.BytesIO(made)).convert("RGBA")
        column = [image.getpixel((image.width // 2, y)) for y in range(image.height)]
        blended = [p for p in column if p[3] == 255
                   and p[:3] != (255, 255, 255) and 120 < p[0] < 250]
        self.assertTrue(blended, f"字形边应当有过渡色，实得 {set(column)}")

    def test_nothing_is_painted_outside_the_circle(self):
        """四角必须是全透明的。旧版把字形直接贴上去，白像素会溢出圆外。"""
        made = link_marks.render_mark(glyph((72, 168, 24)))
        image = Image.open(io.BytesIO(made)).convert("RGBA")
        last = image.width - 1
        for corner in ((0, 0), (last, 0), (0, last), (last, last)):
            self.assertEqual(image.getpixel(corner)[3], 0, f"{corner} 不该有像素")

    def test_the_mark_is_large_enough_for_a_3x_screen(self):
        """容器是 32 px CSS。3x 屏要 96 px，改动前的 64 在高分屏上本来就不够。"""
        self.assertGreaterEqual(link_marks.MARK_SIZE, 96)


class ChannelTests(unittest.TestCase):
    """成品图标与字形 favicon 走两条路，别混。"""

    def test_a_designed_app_icon_passes_through_unrecoloured(self):
        """threads 那枚黑底圆角白字是设计好的方形标识。

        CSS 的 `.entitylinkicon` 已经是 `border-radius:50%` + `overflow:hidden`，
        圆是浏览器裁的；服务端再画一次圆只会把人家设计好的底色换掉。
        """
        source = Image.new("RGBA", (200, 200), (18, 18, 20, 255))
        painter = source.load()
        for x in range(60, 140):
            for y in range(60, 140):
                painter[x, y] = (240, 60, 120, 255) if x < 100 else (60, 200, 240, 255)
        buffer = io.BytesIO()
        source.save(buffer, format="PNG")

        made = link_marks.render_mark(buffer.getvalue())
        self.assertIsNotNone(made)
        image = Image.open(io.BytesIO(made)).convert("RGBA")
        self.assertEqual(image.size, (link_marks.MARK_SIZE, link_marks.MARK_SIZE))
        self.assertEqual(image.getpixel((2, 2)), (18, 18, 20, 255), "底色不该被换掉")

    def test_a_bitmap_too_small_to_be_a_designed_icon_is_declined(self):
        """32×32 的多色 favicon 两条通道都不适用：抠不出字形，放大又糊。

        端点会退回 `plain_mark`，那是另一回事——这里只确认它不冒充成品图标。
        """
        pixels = {(x, y): ((240, 0, 0, 255) if x < 16 else (0, 0, 240, 255))
                  for x in range(32) for y in range(32)}
        self.assertIsNone(link_marks.render_mark(ico(pixels, size=32)))

    def test_a_wide_wordmark_is_left_for_the_studio_logo_surface(self):
        """FANZA 的 `apple-touch-icon/fanza.png` 是 200×200 画布装一条约 4:1 的字标。

        按分辨率排它赢过 48×48 的「F」，可塞进 32 px 圆里就是一条糊掉的红杠。
        闸门看的是内容外接框，不是画布：字标接近 4:1，字形接近 1:1。
        """
        wordmark = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        painter = wordmark.load()
        for x in range(10, 190):
            for y in range(90, 110):
                painter[x, y] = (200, 20, 20, 255)
        buffer = io.BytesIO()
        wordmark.save(buffer, format="PNG")

        self.assertGreater(link_marks.content_aspect(wordmark), link_marks.MAX_CONTENT_ASPECT)
        self.assertIsNone(link_marks.render_mark(buffer.getvalue()))

    def test_a_compact_glyph_on_the_same_canvas_passes_the_aspect_gate(self):
        """同样 200×200 的画布，内容是方的就该放行——闸门看内容，不看画布。"""
        compact = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        painter = compact.load()
        for x in range(70, 130):
            for y in range(60, 140):
                painter[x, y] = (200, 20, 20, 255)
        buffer = io.BytesIO()
        compact.save(buffer, format="PNG")

        self.assertLess(link_marks.content_aspect(compact), link_marks.MAX_CONTENT_ASPECT)
        self.assertIsNotNone(link_marks.render_mark(buffer.getvalue()))

    def test_an_empty_image_is_declined_instead_of_dividing_by_zero(self):
        self.assertEqual(link_marks.content_aspect(Image.new("RGBA", (32, 32), (0, 0, 0, 0))), 0.0)
        self.assertIsNone(link_marks.render_mark(ico({})))


class VectorTests(unittest.TestCase):
    """SVG 一般是站点最清晰的那份资产（threads 就只有 SVG 是成品图标）。"""

    def test_an_svg_is_rasterised_and_rendered(self):
        svg = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
               b'<rect width="100" height="100" fill="#101014"/>'
               b'<circle cx="50" cy="50" r="30" fill="#f0f0f0"/></svg>')
        made = link_marks.render_mark(svg, content_type="image/svg+xml")
        self.assertIsNotNone(made, "resvg_py 缺席时这条会红——它是 pyproject 里的固定依赖")
        self.assertEqual(Image.open(io.BytesIO(made)).size,
                         (link_marks.MARK_SIZE, link_marks.MARK_SIZE))

    def test_svg_is_recognised_by_content_when_the_header_lies(self):
        """不少 CDN 把 SVG 发成 `application/octet-stream`，只能看字节。"""
        self.assertTrue(link_marks._looks_like_svg(b'<svg viewBox="0 0 1 1"></svg>'))
        self.assertTrue(link_marks._looks_like_svg(
            b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"></svg>'))
        self.assertFalse(link_marks._looks_like_svg(b"\x89PNG\r\n\x1a\n"))

    def test_a_broken_svg_yields_nothing_instead_of_raising(self):
        self.assertIsNone(link_marks.rasterize_svg(b"<svg>unclosed"))
        self.assertIsNone(link_marks.render_mark(b"<svg>unclosed", content_type="image/svg+xml"))


class CacheTests(unittest.TestCase):
    def test_the_cache_key_is_per_host_not_per_link(self):
        """同一个站的每条链接共用一枚图标；按完整地址缓存会为同一张图存几十份。"""
        self.assertEqual(link_marks.cache_key("https://www.t-powers.co.jp/talent/a/"),
                         link_marks.cache_key("http://t-powers.co.jp/talent/b/"))
        self.assertNotEqual(link_marks.cache_key("https://t-powers.co.jp/"),
                            link_marks.cache_key("https://x.com/"))

    def test_bumping_the_render_version_invalidates_every_cached_mark(self):
        """缓存保鲜期是 30 天。换了渲染规则却不换键，用户就得等一个月才看到新的。"""
        url = "https://t-powers.co.jp/"
        before = link_marks.cache_key(url)
        original = link_marks.RENDER_VERSION
        try:
            link_marks.RENDER_VERSION = original + 1
            self.assertNotEqual(link_marks.cache_key(url), before)
        finally:
            link_marks.RENDER_VERSION = original

    def test_a_url_without_a_host_has_no_cache_key(self):
        self.assertEqual(link_marks.cache_key("not a url"), "")
        self.assertIsNone(link_marks.cached_path(Path("/tmp"), "not a url"))

    def test_freshness_expires_and_a_missing_file_is_never_fresh(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory).resolve() / "mark.png"
            self.assertFalse(link_marks.is_fresh(tmp))
            tmp.write_bytes(b"x")
            modified = tmp.stat().st_mtime
            with patch.object(link_marks.time, "time", return_value=modified):
                self.assertTrue(link_marks.is_fresh(tmp))
            self.assertFalse(link_marks.is_fresh(tmp, now=modified - 1))
            self.assertTrue(link_marks.is_fresh(tmp, now=modified + link_marks.CACHE_TTL - 1))
            self.assertFalse(link_marks.is_fresh(tmp, now=modified + link_marks.CACHE_TTL))
            # 设置里选「从不」时 ttl 是 None：文件在就算新鲜；选了别的天数就按那个数。
            self.assertTrue(link_marks.is_fresh(tmp, now=modified + 10 ** 9, ttl=None))
            self.assertTrue(link_marks.is_fresh(tmp, now=modified + 6, ttl=7))
            self.assertFalse(link_marks.is_fresh(tmp, now=modified + 7, ttl=7))


if __name__ == "__main__":
    unittest.main()
