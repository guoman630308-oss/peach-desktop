"""厂牌标识的 icon / logo 两变体：文件解析、页面取用位置、补图脚本。

分岔的前提是这个厂牌真的存了两份。只有一份时两个变体都必须回落到那一份，
否则 129 个厂牌里绝大多数会在小地方变成 404。
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import sqlite3
import tempfile
import unittest
import httpx
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from PIL import Image

from peach import web_review
from peach.previews import (
    LOGO_VARIANTS, RING_MIN_EDGE, PreviewService, PreviewUnavailable, logo_key,
)
from peach.web_state import WebContract


ROOT = Path(__file__).resolve().parents[1]


def load(name, *parts):
    spec = importlib.util.spec_from_file_location(name, ROOT.joinpath(*parts))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: 判据模块的一份独立副本：测试往它身上改的表和打的补丁不串进别的测试文件。
MODULE = load("peach._studio_icons_under_test", "src", "peach", "studio_icons.py")
#: 命令行那一趟，判据换成上面那份副本。
SCRIPT = load("harvest_studio_icons_test", "scripts", "harvest_studio_icons.py")
SCRIPT.icons = MODULE


def png_bytes(size=(256, 256), color=(30, 90, 160)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def block_png(size, inset=4, color=(210, 30, 30)):
    """透明底 + 一块不透明色块。

    内容比看的是不透明像素的外接框，所以底必须真透明：整幅纯色的图 `content_aspect`
    会回 0（四角当背景，主体成了空集），那样测不到任何闸门。
    """
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    for x in range(inset, size[0] - inset):
        for y in range(inset, size[1] - inset):
            image.putpixel((x, y), color + (255,))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


# 烤成方图后的边长。`block_png` 的不透明外接框每边比给的尺寸少一个 inset，
# 烤底再让内容占边长 `PLATE_CONTENT_RATIO`，所以两个数都由常量算出来。
PLATE_CONTENT_EDGE = 298 - 4 * 2
PLATE_SIDE = round(PLATE_CONTENT_EDGE / MODULE.images.PLATE_CONTENT_RATIO)
PLATE_SIZE = f"{PLATE_SIDE}x{PLATE_SIDE}"


class VariantResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.logos = root / "logos"
        self.logos.mkdir(parents=True)
        # `marks_root` 和 `logo_root` 一样必须显式给：默认是仓库里随发行分发的那 198 个
        # 标识（ADR-0026），落进来就会把「这个厂牌没装图」的场景变成「装了」。
        self.service = PreviewService(
            SimpleNamespace(), SimpleNamespace(), root / "snapshots", root / "posters",
            root / "avatars", self.logos, marks_root=root / "marks")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name: str, body: bytes = b"x", content_type: str = "") -> Path:
        path = self.logos / name
        path.write_bytes(body)
        if content_type:
            Path(f"{path}.ct").write_text(content_type, encoding="utf-8")
        return path

    def test_no_variant_keeps_the_old_file(self):
        """不带 variant 的请求必须和加这个参数之前一模一样。"""
        base = self.write("Fitch.img", content_type="image/png")
        self.assertEqual(self.service.logo("Fitch"), (base, "image/png"))

    def test_the_only_installed_file_serves_both_variants(self):
        """只有一份图的厂牌占绝大多数，两个位置都得照常拿到它。"""
        base = self.write("Fitch.img")
        for variant in ("icon", "logo"):
            with self.subTest(variant=variant):
                self.assertEqual(self.service.logo("Fitch", variant)[0], base)

    def test_icon_prefers_the_icon_file(self):
        self.write("Fitch.img")
        icon = self.write("Fitch.icon.img", content_type="image/png")
        self.assertEqual(self.service.logo("Fitch", "icon"), (icon, "image/png"))

    def test_logo_ignores_the_icon_file(self):
        """大位要的是完整字标。存了小标不代表大位就该改用小标。"""
        base = self.write("Fitch.img")
        self.write("Fitch.icon.img")
        self.assertEqual(self.service.logo("Fitch", "logo")[0], base)

    def test_logo_prefers_the_logo_file(self):
        self.write("Fitch.img")
        wordmark = self.write("Fitch.logo.img")
        self.assertEqual(self.service.logo("Fitch", "logo")[0], wordmark)

    def test_large_picks_the_sharpest_file_whatever_its_suffix_says(self):
        """大位按文件的像素量选，不按后缀猜清晰度。

        实测就有后缀与清晰度对不上的：Prestige 的 `icon` 只有 42 px，裸文件却有
        632 px。按后缀取等于把最糊的那张摆进最大的格子。
        """
        base = self.write("Prestige.img", png_bytes((632, 632)), "image/png")
        self.write("Prestige.logo.img", png_bytes((413, 413)), "image/png")
        self.write("Prestige.icon.img", png_bytes((42, 42)), "image/png")
        self.assertEqual(self.service.logo("Prestige", "large"), (base, "image/png"))

    def test_large_keeps_the_wordmark_when_the_files_are_equally_sharp(self):
        """一样清晰时仍拿字标：大位本来就是它的位置，同分不构成换掉它的理由。"""
        self.write("ACT.img", png_bytes((400, 400)), "image/png")
        wordmark = self.write("ACT.logo.img", png_bytes((400, 400)), "image/png")
        self.write("ACT.icon.img", png_bytes((400, 400)), "image/png")
        self.assertEqual(self.service.logo("ACT", "large")[0], wordmark)

    def test_large_takes_the_vector_file_over_any_bitmap(self):
        """矢量放多大都清晰，位图再大也有到头的时候。"""
        svg = self.write("VirtualTaboo.logo.img",
                         b"<svg xmlns='http://www.w3.org/2000/svg' width='216' height='80'/>",
                         "image/svg+xml")
        self.write("VirtualTaboo.img", png_bytes((1024, 1024)), "image/png")
        self.assertEqual(self.service.logo("VirtualTaboo", "large"), (svg, "image/svg+xml"))

    def test_large_still_serves_a_file_that_cannot_be_measured(self):
        """量不出尺寸的照样出图：浏览器认得的格式比 Pillow 多，这一步只排序。"""
        base = self.write("Fitch.img")
        self.assertEqual(self.service.logo("Fitch", "large")[0], base)

    def test_large_remeasures_once_the_file_is_replaced(self):
        """复核批准会原地换掉标识文件。度量记在 (路径, mtime, 字节数) 上，换了文件
        就是新键，不会一直按旧尺寸挑。"""
        self.write("Fitch.img", png_bytes((120, 120)), "image/png")
        icon = self.write("Fitch.icon.img", png_bytes((64, 64)), "image/png")
        self.assertEqual(self.service.logo("Fitch", "large")[0], self.logos / "Fitch.img")
        self.write("Fitch.icon.img", png_bytes((900, 900)), "image/png")
        self.assertEqual(self.service.logo("Fitch", "large")[0], icon)

    def test_ring_keeps_an_icon_big_enough_to_fill_the_circle(self):
        """索引圆框要的是方标。够大的方标比更大的字标更贴合圆框，不该被换掉。"""
        self.write("ACT.logo.img", png_bytes((800, 800)), "image/png")
        icon = self.write("ACT.icon.img", png_bytes((RING_MIN_EDGE, RING_MIN_EDGE)), "image/png")
        self.assertEqual(self.service.logo("ACT", "ring")[0], icon)

    def test_ring_swaps_a_stamp_sized_icon_for_the_sharpest_file(self):
        """Prestige 的方标 42 px，按原尺寸摆在圆心只剩一枚邮票，圆框改拿最清晰的那份。"""
        base = self.write("Prestige.img", png_bytes((632, 632)), "image/png")
        self.write("Prestige.logo.img", png_bytes((413, 413)), "image/png")
        self.write("Prestige.icon.img", png_bytes((42, 42)), "image/png")
        self.assertEqual(self.service.logo("Prestige", "ring"), (base, "image/png"))

    def test_ring_without_an_icon_serves_what_the_studio_has(self):
        """绝大多数厂牌只有一份裸文件，圆框照旧拿到它。"""
        base = self.write("Fitch.img")
        self.assertEqual(self.service.logo("Fitch", "ring")[0], base)

    def test_an_unknown_variant_falls_back_instead_of_404(self):
        """缓存下来的旧页面可能带着别的参数；那也该出图，不该把厂牌页开出个空位。"""
        base = self.write("Fitch.img")
        self.assertEqual(self.service.logo("Fitch", "banner")[0], base)

    def test_a_studio_with_no_file_at_all_still_raises(self):
        for variant in ("", "icon", "logo", "large"):
            with self.subTest(variant=variant):
                with self.assertRaises(PreviewUnavailable):
                    self.service.logo("Nobody", variant)

    def test_the_variant_file_name_matches_the_scripts_rule(self):
        """脚本安装的文件名和这里解析的必须是同一套，否则装了也读不到。"""
        self.assertEqual(MODULE.safe_name("Idea Pocket"), "Idea_Pocket")
        self.write("Idea_Pocket.img")
        icon = self.write(f'{MODULE.safe_name("Idea Pocket")}.icon.img')
        self.assertEqual(self.service.logo("Idea Pocket", "icon")[0], icon)


class LogoAvailabilityTests(unittest.TestCase):
    """「这个厂牌有没有标识」必须和 `/logo` 真正的取图判据逐字一致。

    页面据此决定输出 `<img>` 还是直接首字母垫底。判得松一格就是「说有图但取回
    404」——那个位置画出一张碎图；紧一格就是「明明装了却永远只显示首字母」。所以
    两边在同一个目录上对照着测，不各测一半。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.logos = root / "logos"
        self.logos.mkdir(parents=True)
        # `marks_root` 和 `logo_root` 一样必须显式给：默认是仓库里随发行分发的那 198 个
        # 标识（ADR-0026），落进来就会把「这个厂牌没装图」的场景变成「装了」。
        self.service = PreviewService(
            SimpleNamespace(), SimpleNamespace(), root / "snapshots", root / "posters",
            root / "avatars", self.logos, marks_root=root / "marks")
        # 库文件不必存在：可用性判定只扫目录，一个字节都不查库。但 `logo_root` 和
        # `marks_root` 必须显式给临时目录，默认值分别是本机真实的 generated 树和仓库
        # 里随发行分发的那批标识。
        self.contract = WebContract(root / "ledger.db", logo_root=self.logos,
                                    marks_root=root / "marks")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name: str) -> Path:
        path = self.logos / name
        path.write_bytes(b"x")
        return path

    def resolves(self, studio: str, variant: str = "icon") -> bool:
        try:
            self.service.logo(studio, variant)
        except PreviewUnavailable:
            return False
        return True

    def assertAgrees(self, studio: str):
        """可用性判定和取图在同一个目录上给出同一个答案。

        只对页面真会发的两个 variant 成立：三处取图位都带 `variant=`，裸 `/logo`
        由 `test_web_ui` 那条页面源测试拦住。
        """
        available = self.contract.has_logo(studio)
        for variant in LOGO_VARIANTS:
            with self.subTest(studio=studio, variant=variant):
                self.assertEqual(
                    available, self.resolves(studio, variant),
                    f"{studio!r}：可用性说 {available}，取图不同意")

    def test_a_studio_with_a_file_is_available_and_one_without_is_not(self):
        self.write("Fitch.img")
        self.assertAgrees("Fitch")
        self.assertAgrees("Nobody")

    def test_the_installed_shape_agrees_on_both_variants(self):
        """`--install` 落的就是这个形状：底图一份加两个变体，两边都得说有图。"""
        for name in ("Fitch.img", "Fitch.icon.img", "Fitch.logo.img"):
            self.write(name)
        self.assertAgrees("Fitch")

    def test_a_variant_only_studio_still_counts_as_available(self):
        """只有变体文件、没有底图时仍算有图：那个变体确实取得到。

        另一个变体会落空，但那条路径本来就有兜底（厂牌大位退到实体图，小圆片退到
        首字母），而 `--install` 总会补上底图，这个形状只可能是手工摆出来的。判成
        「没有图」反而更糟：装上的那张图从此永远不显示。
        """
        self.write("Fitch.icon.img")
        self.assertTrue(self.contract.has_logo("Fitch"))
        self.assertTrue(self.resolves("Fitch", "icon"))

    def test_the_key_rule_survives_spaces_and_punctuation(self):
        """页面上的厂牌名带空格、`&`、`/` 的比不带的多，落盘名全归一成下划线。"""
        studios = ("V&R PRODUCE", "Kahanshin Tigers /Fetika", "M Girls' Lab")
        for studio in studios:
            self.write(f"{logo_key(studio)}.img")
        for studio in studios:
            self.assertAgrees(studio)

    def test_two_japanese_studios_do_not_share_one_file(self):
        """只留 ASCII 时非 ASCII 的每个字都换成一个下划线，落盘名只剩「几个字」。

        账本里 129 个厂牌有 12 组这样撞在一起，`プレステージ` 和 `ムーディーズ` 同为
        `______`。撞了不报错，后装的盖掉先装的，PRESTIGE 的位置就挂上 MOODYZ 的牌子。
        """
        self.write(f"{logo_key('プレステージ')}.img")
        self.assertTrue(self.contract.has_logo("プレステージ"))
        self.assertFalse(self.contract.has_logo("ムーディーズ"))
        self.assertAgrees("ムーディーズ")
        self.assertNotEqual(logo_key("シロウトTV"), logo_key("ラグジュTV"))

    def test_the_ascii_file_names_already_on_disk_keep_their_key(self):
        """已经装好的 60 张都是 ASCII 名，改名规则不能让它们从此取不到。"""
        expected = {"Fitch": "Fitch", "FC2-PPV": "FC2-PPV", "Idea Pocket": "Idea_Pocket",
                    "S1 NO.1 STYLE": "S1_NO_1_STYLE", "kira*kira": "kira_kira"}
        for studio, key in expected.items():
            with self.subTest(studio=studio):
                self.assertEqual(logo_key(studio), key)

    def test_availability_is_case_insensitive_like_the_resolver(self):
        """`logo()` 对大小写不敏感（Windows 与 macOS 的默认文件系统就是这样），
        索引不能顺手把这层容错丢了。"""
        self.write("fitch.img")
        self.assertTrue(self.contract.has_logo("FITCH"))
        self.assertAgrees("FITCH")

    def test_a_sidecar_or_a_vector_original_is_not_a_logo(self):
        """`.ct` 边车和留档的 SVG 都不是 `/logo` 会取的文件，不算这个厂牌有图。"""
        self.write("Fitch.img.ct")
        self.write("Fitch.svg")
        self.assertFalse(self.contract.has_logo("Fitch"))
        self.assertAgrees("Fitch")

    def test_an_empty_studio_name_is_never_available(self):
        """裸 `/logo`（没有 studio）在服务端也是 404，页面不该发得出来。"""
        for studio in (None, "", "   "):
            with self.subTest(studio=studio):
                self.assertFalse(self.contract.has_logo(studio))

    def test_a_missing_logo_directory_without_bundled_marks_is_an_empty_index(self):
        """两个目录都没有时全部退回首字母，不是报错。"""
        root = Path(self.tmp.name).resolve()
        contract = WebContract(root / "ledger.db", logo_root=root / "nowhere",
                               marks_root=root / "marks")
        self.assertEqual(dict(contract.logo_index()), {})
        self.assertFalse(contract.has_logo("Fitch"))

    def test_bundled_marks_cover_a_clean_data_directory(self):
        """干净数据目录靠随仓库分发的那批显示图标，这是 ADR-0026 收录它们的理由。

        取图和可用性判定必须一起认内置资源：只改取图那一侧，页面判「没图」就不出
        `<img>`，收进仓库的 198 个一张也不会显示，而且不报错。
        """
        root = Path(self.tmp.name).resolve()
        marks = root / "bundled"
        marks.mkdir()
        (marks / "Fitch.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (marks / "Attackers.icon.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        contract = WebContract(root / "ledger.db", logo_root=root / "nowhere",
                               marks_root=marks)
        self.assertTrue(contract.has_logo("Fitch"))
        self.assertTrue(contract.has_logo("Attackers"), "变体后缀要剥掉再判")
        self.assertFalse(contract.has_logo("NotBundled"))

    def test_a_newly_installed_logo_shows_up_after_the_review_cache_bust(self):
        """索引带 TTL，但复核批准会 `cache_bust()`：用户自己装上的图立刻可见。"""
        self.assertFalse(self.contract.has_logo("Fitch"))
        self.write("Fitch.img")
        self.contract.cache_bust()
        self.assertTrue(self.contract.has_logo("Fitch"))

    def test_the_install_path_and_the_resolver_share_one_key_rule(self):
        """批准落地写的文件名和取图找的文件名只能有一个实现。"""
        self.assertIs(web_review.studio_logo_key, logo_key)


class PageSourceTests(unittest.TestCase):
    """页面按位置取变体：小地方 icon，厂牌页那个 160px 大位 logo。"""

    @classmethod
    def setUpClass(cls):
        cls.application = ROOT / 'frontend/src/application'

    def test_every_small_surface_asks_for_the_icon(self):
        source = (self.application / 'catalog.js').read_text(encoding='utf-8')
        for snippet in (
            "logoUrl(x.k,'icon',x.logo_version)",
        ):
            # 壳里的小位只剩这一处厂牌标识，作品详情出演区的厂牌格画在 React 岛里，由
            # `frontend/test/react/item-detail.test.tsx` 钉住；网盘来源角标走 `MEDIA_SOURCE_ICONS` 那份站标，
            # 不进这条链。`.entityfavicon` 的模板不写 `data-studio`，图片回退是
            # 声明式的，不存在按 `img.dataset.studio` 换图的第三处。「不许漏 variant」
            # 由下面那条按行扫描的断言守。
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, source)

    def test_media_sources_do_not_request_studio_logos(self):
        """网盘使用来源标识，实际图形由前端渲染回归核对；厂牌字标链不接收网盘名称。"""
        for path in sorted((ROOT / 'frontend/src').rglob('*')):
            if path.suffix in {'.js', '.ts', '.tsx'}:
                self.assertNotIn("studio=115", path.read_text(encoding='utf-8'))

    def test_no_logo_request_is_left_without_a_variant(self):
        """漏掉一处就会在那个位置继续显示补白字标，而且没人会注意到。"""
        bare = [(path.relative_to(ROOT).as_posix(), line.strip())
                for path in sorted((ROOT / 'frontend/src').rglob('*')) if path.suffix in {'.js', '.ts', '.tsx'}
                for line in path.read_text(encoding='utf-8').splitlines()
                if "/logo?studio=" in line and "variant=" not in line]
        self.assertEqual(bare, [])


class PaddedStudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logos = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def sidecar(self, safe: str, action: str = "pad-to-square", **extra):
        payload = {"action": action, "original_width": 130, "original_height": 43}
        payload.update(extra)
        (self.logos / f"{safe}.img").write_bytes(b"x")
        (self.logos / f"{safe}.img.normalization.json").write_text(
            json.dumps(payload), encoding="utf-8")

    def test_only_padded_files_are_candidates(self):
        """补白之后每一张都是方的，从像素上再也看不出源图是条状——只能认边车。"""
        self.sidecar("FC2-PPV")
        self.sidecar("Alice_JAPAN", action="keep")
        (self.logos / "S1.img").write_bytes(b"x")
        self.assertEqual(sorted(MODULE.padded_studios(self.logos)), ["FC2-PPV"])

    def test_the_original_size_is_carried_through(self):
        self.sidecar("FC2-PPV")
        self.assertEqual(MODULE.padded_studios(self.logos)["FC2-PPV"],
                         {"width": 130, "height": 43})

    def test_a_corrupt_sidecar_is_skipped_not_fatal(self):
        (self.logos / "Broken.img.normalization.json").write_text("{", encoding="utf-8")
        self.assertEqual(MODULE.padded_studios(self.logos), {})


class HarvestTargetTests(unittest.TestCase):
    """目标集 = 补白过的 ∪ 装着的方标太小的 ∪ 有链接但一张图都没有的。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logos = Path(self.tmp.name).resolve()
        (self.logos / "Fitch.img").write_bytes(b"x")
        self.padded = {"Fitch": {"width": 130, "height": 43}}
        self.links = {"Fitch": [{"url": "https://fitch-av.com/"}],
                      "Hon_Naka": [{"url": "https://honnaka.jp/"}],
                      "S1": [{"url": "https://s1s1s1.com/"}]}
        # 三张指定来源表加起来三十多条，留着会盖住这里每一条判据。要用的测试自己往里放。
        self.sources = dict(MODULE.LOGO_SOURCES_BY_SAFE)
        self.wordmarks = dict(MODULE.WORDMARK_SOURCES_BY_SAFE)
        self.icons = dict(MODULE.ICON_SOURCES_BY_SAFE)
        for table in (MODULE.LOGO_SOURCES_BY_SAFE, MODULE.WORDMARK_SOURCES_BY_SAFE,
                      MODULE.ICON_SOURCES_BY_SAFE):
            table.clear()

    def tearDown(self):
        for table, saved in ((MODULE.LOGO_SOURCES_BY_SAFE, self.sources),
                             (MODULE.WORDMARK_SOURCES_BY_SAFE, self.wordmarks),
                             (MODULE.ICON_SOURCES_BY_SAFE, self.icons)):
            table.clear()
            table.update(saved)
        self.tmp.cleanup()

    def pin(self, safe, variant, installed_url, size=(160, 160)):
        """装着一份图并留下边车，边车里记的地址是 `installed_url`。"""
        (self.logos / f"{safe}.img").write_bytes(b"x")
        (self.logos / f"{safe}.{variant}.img").write_bytes(png_bytes(size=size))
        (self.logos / f"{safe}.{variant}.img.provenance.json").write_text(
            json.dumps({"source_url": installed_url}), encoding="utf-8")

    def test_a_designated_wordmark_source_is_reason_enough(self):
        """字标来源那批在账本里连一条链接都没有，按「有链接」收目标一条都收不到。"""
        MODULE.WORDMARK_SOURCES_BY_SAFE["Jackson"] = "https://static.example/jackson.jpg"
        self.assertIn("Jackson", MODULE.harvest_targets({}, {}, self.logos))

    def test_a_studio_with_no_image_at_all_is_included(self):
        """Hon Naka 不在补白名单里——它没有可补白的文件。可它两个位置一样是空的。"""
        (self.logos / "S1.img").write_bytes(b"x")
        targets = MODULE.harvest_targets(self.padded, self.links, self.logos)
        self.assertEqual(sorted(targets), ["Fitch", "Hon_Naka"])

    def test_the_padded_studio_keeps_its_original_size(self):
        targets = MODULE.harvest_targets(self.padded, self.links, self.logos)
        self.assertEqual(targets["Fitch"],
                         {"original_size": "130x43", "installed": "Fitch.img"})

    def test_a_studio_with_no_image_leaves_the_size_columns_empty(self):
        """没有原图就没有量到的尺寸。写 `x` 或 `0x0` 会被当成量出来的结果。"""
        targets = MODULE.harvest_targets(self.padded, self.links, self.logos)
        self.assertEqual(targets["Hon_Naka"],
                         {"original_size": "", "installed": ""})

    def test_a_studio_with_neither_image_nor_link_is_not_a_target(self):
        """没链接、没图、也没指定来源的厂牌进来只会在复核件上多一行「无官网链接」。"""
        targets = MODULE.harvest_targets({}, {}, self.logos)
        self.assertEqual(targets, {})

    def test_a_designated_logo_source_is_reason_enough(self):
        """jae.tokyo 那批在账本里连一条链接都没有，按「有链接」收目标一条都收不到。"""
        MODULE.LOGO_SOURCES_BY_SAFE["Dogma"] = "http://logos.example/dogma.png"
        self.assertEqual(MODULE.harvest_targets({}, {}, self.logos),
                         {"Dogma": {"original_size": "", "installed": ""}})

    def test_a_designated_source_does_not_reopen_a_studio_that_has_its_image(self):
        """已经装好的一张都不动：这个脚本的入口只补空位。"""
        MODULE.LOGO_SOURCES_BY_SAFE["Fitch"] = "http://logos.example/fitch.png"
        self.assertNotIn("Fitch", MODULE.harvest_targets({}, {}, self.logos))

    def test_an_installed_mark_too_small_for_the_round_slot_is_asked_again(self):
        """小圆片是 32 CSS px、2 倍屏 64 实像素，装着 57 的那一位是在被放大。"""
        (self.logos / "DorcelClub.img").write_bytes(png_bytes(size=(57, 57)))
        targets = MODULE.harvest_targets({}, {}, self.logos)
        self.assertEqual(targets["DorcelClub"],
                         {"original_size": "57x57", "installed": "DorcelClub.img"})

    def test_a_mark_that_already_fills_the_round_slot_is_left_alone(self):
        """够 64 就不再敲别人的门：每多问一趟就多一次对外请求。"""
        (self.logos / "Hunter.img").write_bytes(png_bytes(size=(64, 64)))
        self.assertNotIn("Hunter", MODULE.harvest_targets({}, {}, self.logos))

    def test_the_small_slots_own_file_decides_not_the_big_sibling(self):
        """小位取的是 `.icon.img`，那一份太小就该再问，`<safe>.img` 多大都不顶用。"""
        (self.logos / "HEYZO.icon.img").write_bytes(png_bytes(size=(32, 32)))
        (self.logos / "HEYZO.img").write_bytes(png_bytes(size=(446, 446)))
        targets = MODULE.harvest_targets({}, {}, self.logos)
        self.assertEqual(targets["HEYZO"],
                         {"original_size": "32x32", "installed": "HEYZO.icon.img"})

    def test_the_hero_variant_alone_does_not_make_a_studio_a_target(self):
        """`.logo.img` 归 160 px 大位，小位的尺寸不由它决定。"""
        (self.logos / "KRONE.logo.img").write_bytes(png_bytes(size=(40, 40)))
        (self.logos / "KRONE.img").write_bytes(png_bytes(size=(253, 253)))
        self.assertNotIn("KRONE", MODULE.harvest_targets({}, {}, self.logos))

    def test_a_padded_studio_keeps_the_size_of_the_original_it_was_padded_from(self):
        """两条判据都命中时留补白那一条：那个尺寸说明的是「源图是条状字标」。"""
        (self.logos / "Fitch.img").write_bytes(png_bytes(size=(40, 40)))
        targets = MODULE.harvest_targets(self.padded, {}, self.logos)
        self.assertEqual(targets["Fitch"],
                         {"original_size": "130x43", "installed": "Fitch.img"})

    def test_an_unreadable_installed_file_is_skipped_not_fatal(self):
        """量不出尺寸的不当成「小」：那是坏文件或 Pillow 不认的格式，不是判据。"""
        self.assertEqual(MODULE.small_installed_marks(self.logos), {})

    def test_a_restated_source_reopens_a_studio_that_already_has_its_image(self):
        """改掉表里的地址，图还装着——前三条判据一条都收不到它，复核件上连行都没有。"""
        MODULE.WORDMARK_SOURCES_BY_SAFE["Jackson"] = "https://static.example/new.jpg"
        self.pin("Jackson", MODULE.LOGO, "https://static.example/old.gif")
        self.assertEqual(MODULE.harvest_targets({}, {}, self.logos)["Jackson"],
                         {"original_size": "160x160", "installed": "Jackson.logo.img"})

    def test_a_source_that_matches_the_sidecar_stays_out(self):
        """装完边车就和表一致，它自己退出目标集——重跑不会反复敲同一扇门。"""
        url = "https://static.example/jackson.gif"
        MODULE.WORDMARK_SOURCES_BY_SAFE["Jackson"] = url
        self.pin("Jackson", MODULE.LOGO, url)
        self.assertEqual(MODULE.harvest_targets({}, {}, self.logos), {})

    def test_an_image_without_a_sidecar_is_left_alone(self):
        """来源无从比对。收进来等于拿指定表去盖掉另一条线装的图。"""
        MODULE.WORDMARK_SOURCES_BY_SAFE["Jackson"] = "https://static.example/new.jpg"
        (self.logos / "Jackson.img").write_bytes(b"x")
        (self.logos / "Jackson.logo.img").write_bytes(png_bytes(size=(160, 160)))
        self.assertEqual(MODULE.harvest_targets({}, {}, self.logos), {})

    def test_the_icon_table_is_compared_against_the_icon_slot(self):
        """`ICON_SOURCES` 只管小位。拿大位的边车去比，换过的会被判成没换过。"""
        MODULE.ICON_SOURCES_BY_SAFE["シロウトTV"] = "https://static.example/square.jpg"
        self.pin("シロウトTV", MODULE.ICON, "http://expo.example/034.png", (414, 414))
        self.assertIn("シロウトTV", MODULE.harvest_targets({}, {}, self.logos))
        self.assertEqual(MODULE.installed_source(self.logos, "シロウトTV", MODULE.LOGO),
                         None, "大位没有边车，这一条不该把它也算成换过")


class LogoSourceTests(unittest.TestCase):
    """`logo` 位的指定来源。这一位不过内容比闸门——大位要的本来就是完整字标。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.candidates = self.root / "candidates"
        self.target = {"original_size": "130x43", "installed": "Fitch.img"}
        self.entries = [{"entity_id": 5591, "studio": "Fitch",
                         "link_kind": "official", "url": "https://fitch-av.com/"}]
        self.url = "https://logos.example/fitch.png"
        self.original = dict(MODULE.LOGO_SOURCES_BY_SAFE)
        MODULE.LOGO_SOURCES_BY_SAFE["Fitch"] = self.url

    def tearDown(self):
        MODULE.LOGO_SOURCES_BY_SAFE.clear()
        MODULE.LOGO_SOURCES_BY_SAFE.update(self.original)
        self.tmp.cleanup()

    def row(self, payload=None, reachable=True):
        pages = {self.url: payload} if payload is not None else {}
        return MODULE.logo_row("Fitch", self.target, self.entries,
                               Fetch(pages, reachable=reachable), self.candidates)

    def test_every_registered_source_is_reachable_by_its_file_name(self):
        """反向索引按同一条命名规则建，键对不上时复核件上的名字会退成一排下划线。

        它同时覆盖 logo 与字标两张来源表：只认前一张的话，字标来源那批在复核件上
        会认不出是谁。
        """
        registered = {**MODULE.LOGO_SOURCES, **MODULE.WORDMARK_SOURCES,
                      **MODULE.ICON_SOURCES}
        self.assertEqual(sorted(MODULE.LOGO_SOURCE_NAMES),
                         sorted(set(self.original) | set(MODULE.WORDMARK_SOURCES_BY_SAFE)
                                | set(MODULE.ICON_SOURCES_BY_SAFE)))
        for safe, studio in MODULE.LOGO_SOURCE_NAMES.items():
            with self.subTest(studio=studio):
                self.assertEqual(MODULE.safe_name(studio), safe)
                self.assertIn(studio, registered)

    def test_only_the_two_tables_that_split_the_slots_may_share_a_studio(self):
        """`WORDMARK_SOURCES` 把两个位置都占了，和另外两张都不能有交集。

        `LOGO_SOURCES` 和 `ICON_SOURCES` 相反，重合正是它们的用途：一家厂牌的大位
        取展会那张横条字标、小位取名录里并排的那枚方标，只有分开指才对得上。
        """
        self.assertEqual(set(self.original) & set(MODULE.WORDMARK_SOURCES_BY_SAFE), set())
        self.assertEqual(
            set(MODULE.ICON_SOURCES_BY_SAFE) & set(MODULE.WORDMARK_SOURCES_BY_SAFE), set())
        self.assertIn("シロウトTV", MODULE.ICON_SOURCES)
        self.assertIn("シロウトTV", MODULE.LOGO_SOURCES)

    def test_no_two_keys_collapse_onto_one_file_name(self):
        """键写的是账本 canonical_name。同一家的别名写法再写一条，`..._BY_SAFE` 会把
        两条折成一个键，谁盖谁全看字典顺序，而两条指向的 URL 未必是同一张图。"""
        for table in (MODULE.LOGO_SOURCES, MODULE.WORDMARK_SOURCES, MODULE.ICON_SOURCES):
            with self.subTest(table=len(table)):
                keys = [MODULE.safe_name(name) for name in table]
                self.assertEqual(sorted(keys), sorted(set(keys)))

    def test_the_expo_directory_is_the_source_for_the_studios_with_no_image(self):
        """用户 2026-09-04 指定 jae.tokyo：名录每届各带一套片商自己交的 logo。

        2016 那届只有图没有名字，认不出是谁家的，所以表里没有 jae2016。Prestige 用官网
        header 的矢量字标，不从名录取。
        """
        expo = [url for url in MODULE.LOGO_SOURCES.values() if "jae.tokyo" in url]
        self.assertEqual(len(expo), 19)
        self.assertEqual([url for url in expo if "jae2016" in url], [])
        for url in expo:
            with self.subTest(url=url):
                self.assertRegex(url, r"^http://www\.jae\.tokyo/jae201[457]/")

    def test_the_mousouzoku_directory_supplies_square_logos_not_wordmarks(self):
        """妄想族名录给的是 200×200 真方标，所以进这一张表而不是字标那张。

        `AVS collector's` 的弯撇号写法是别名，过 `safe_name` 落到同一个键。
        用户 2026-09-23 点名补齐 ティーチャー、下半身タイガース、ゆりえっち、山と空 四家；
        2026-09-24 按 ADR-0051 补上中间那一级 素人ホイホイ。
        """
        directory = [url for url in MODULE.LOGO_SOURCES.values() if "mousouzoku-av" in url]
        self.assertEqual(len(directory), 8)
        for url in directory:
            with self.subTest(url=url):
                self.assertRegex(
                    url, r"^https://www\.mousouzoku-av\.com/contents/maker/id\d+/logo_l\.jpg$")
        self.assertEqual(MODULE.safe_name("AVS collector's"),
                         MODULE.safe_name("AVS collector’s"))

    def test_fc2_is_the_registered_logo_source(self):
        """用户 2026-09-03 指定的是 seeklogo 的 429409（600×600 方形锁定图）。

        同站那份 2000×662 是横向字标，不是这一位要的东西。
        """
        self.assertIn("fc2-logo-png_seeklogo-429409",
                      self.original[MODULE.safe_name("FC2-PPV")])

    def test_a_studio_without_a_registered_source_gets_no_logo_row(self):
        self.assertIsNone(MODULE.logo_row("HEYZO", self.target, self.entries,
                                          Fetch(), self.candidates))

    def test_a_wide_wordmark_is_accepted_here(self):
        """`icon` 位退回的形状正是这一位要的。两个位置本来就要两种东西。"""
        row = self.row(block_png((600, 200)))
        self.assertEqual((row["verdict"], row["variant"]), (MODULE.OK, MODULE.LOGO))
        self.assertEqual(row["mark_size"], "600x200")
        self.assertTrue(str(row["candidate"]).endswith("Fitch.logo.png"))
        self.assertEqual(row["url"], self.url, "记的是取图那一份的地址，不是官网")

    def test_the_recorded_sha256_matches_the_stored_file(self):
        row = self.row(block_png((600, 200)))
        stored = Path(str(row["candidate"])).read_bytes()
        self.assertEqual(hashlib.sha256(stored).hexdigest(), row["sha256"])

    def test_a_thumbnail_sized_logo_is_refused(self):
        """大位是 160 px、2x 屏 320 px。短边 64 说明取到的是缩略图不是标识资产。"""
        row = self.row(block_png((64, 64)))
        self.assertEqual(row["verdict"], MODULE.TOOSMALL)
        self.assertEqual(row["candidate"], "")

    def test_a_source_that_cannot_be_fetched_is_未取得(self):
        row = self.row(reachable=False)
        self.assertEqual(row["verdict"], MODULE.MISSING)

    def test_a_source_that_is_not_an_image_is_未取得(self):
        row = self.row(b"<html>404</html>")
        self.assertEqual(row["verdict"], MODULE.MISSING)
        self.assertIn("解不开", row["evidence"])


class WordmarkSourceTests(unittest.TestCase):
    """指定字标来源：宽扁图只有烤成方图这一条用法，两位装同一张。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.candidates = Path(self.tmp.name).resolve() / "candidates"
        self.target = {"original_size": "", "installed": ""}
        self.url = "https://static.example/marks/jackson.jpg"
        self.original = dict(MODULE.WORDMARK_SOURCES_BY_SAFE)
        MODULE.WORDMARK_SOURCES_BY_SAFE["Jackson"] = self.url

    def tearDown(self):
        MODULE.WORDMARK_SOURCES_BY_SAFE.clear()
        MODULE.WORDMARK_SOURCES_BY_SAFE.update(self.original)
        self.tmp.cleanup()

    def rows(self, payload=None, reachable=True):
        pages = {self.url: payload} if payload is not None else {}
        return MODULE.wordmark_source_rows(
            "Jackson", self.target, Fetch(pages, reachable=reachable), self.candidates)

    def test_a_wide_wordmark_fills_both_slots_from_one_square_plate(self):
        """400×80 的官方字标烤成方图，小位记 `字标补白`、大位记 `ok`。

        大位不能装原图：`.entityportrait` 是 `aspect-ratio:1` + `object-fit:cover`
        的方框，宽扁图进去只剩正中间那几个字母。
        """
        icon, logo = self.rows(block_png((400, 80)))
        self.assertEqual((icon["variant"], icon["verdict"]), (MODULE.ICON, MODULE.PADDED))
        self.assertEqual((logo["variant"], logo["verdict"]), (MODULE.LOGO, MODULE.OK))
        self.assertEqual(icon["mark_size"], logo["mark_size"])
        self.assertEqual(icon["sha256"], logo["sha256"], "两位装的必须是同一张")
        self.assertEqual(icon["mark_size"].split("x")[0], icon["mark_size"].split("x")[1])
        self.assertIn("400x80", icon["evidence"], "复核件要能看出源图多宽")

    def test_both_rows_are_installable_and_stored_under_their_own_names(self):
        icon, logo = self.rows(block_png((400, 80)))
        self.assertIn(icon["verdict"], MODULE.INSTALLABLE)
        self.assertIn(logo["verdict"], MODULE.INSTALLABLE)
        self.assertTrue(str(icon["candidate"]).endswith("Jackson.png"))
        self.assertTrue(str(logo["candidate"]).endswith("Jackson.logo.png"))
        for row in (icon, logo):
            stored = Path(str(row["candidate"])).read_bytes()
            self.assertEqual(hashlib.sha256(stored).hexdigest(), row["sha256"])

    def test_the_recorded_url_is_the_mark_not_a_website(self):
        """这批厂牌账本里一条链接都没有，`url` 列写的只能是取图那一份的地址。"""
        icon, logo = self.rows(block_png((400, 80)))
        self.assertEqual((icon["url"], logo["url"]), (self.url, self.url))
        self.assertEqual(icon["link_kind"], "wordmark-source")
        self.assertEqual(icon["studio"], "Jackson", "复核件上要认得出是谁")

    def test_a_studio_without_a_registered_wordmark_gets_no_rows(self):
        self.assertEqual(
            MODULE.wordmark_source_rows("HEYZO", self.target, Fetch(), self.candidates),
            (None, None))

    def test_a_mark_below_the_icon_floor_is_refused_without_a_candidate(self):
        """短边 32 是小位的下限。缩到 28 px 的筛选片上，比它小的只是一团糊。"""
        icon, logo = self.rows(block_png((90, 20)))
        self.assertEqual(icon["verdict"], MODULE.TOOSMALL)
        self.assertEqual(icon["candidate"], "")
        self.assertIsNone(logo)

    def test_an_unreachable_or_undecodable_source_is_未取得(self):
        for payload, reachable in ((None, False), (b"<html>404</html>", True)):
            with self.subTest(reachable=reachable):
                icon, logo = self.rows(payload, reachable=reachable)
                self.assertEqual(icon["verdict"], MODULE.MISSING)
                self.assertEqual(icon["candidate"], "")
                self.assertIsNone(logo)

    def test_a_registered_wordmark_studio_never_falls_back_to_link_discovery(self):
        """指定字标来源就是答案。放它去走发现流程只会多记一行「无官网链接」。"""
        fetch = Fetch({self.url: block_png((400, 80))})
        rows = MODULE.harvest({"Jackson": self.target}, {}, fetch, self.candidates)
        self.assertEqual([row["verdict"] for row in rows], [MODULE.PADDED, MODULE.OK])
        self.assertEqual(fetch.asked, [self.url])

    def test_an_agency_pass_reads_only_the_agency_table(self):
        """同名的事务所不能拿到厂牌指好的图：两边共用 `logo_key` 落盘名。"""
        agency_url = "https://agency.example/logo.svg"
        fetch = Fetch({self.url: block_png((400, 80)), agency_url: block_png((400, 80))})
        rows = MODULE.harvest({"Jackson": self.target}, {}, fetch, self.candidates,
                              wordmarks={"Jackson": agency_url})
        self.assertEqual({row["url"] for row in rows}, {agency_url})
        self.assertNotIn(self.url, fetch.asked)

    def test_the_agency_table_keys_are_ledger_names_on_their_own_file_names(self):
        keys = [MODULE.safe_name(name) for name in MODULE.AGENCY_WORDMARK_SOURCES]
        self.assertEqual(sorted(keys), sorted(MODULE.AGENCY_WORDMARK_SOURCES_BY_SAFE))
        self.assertIn("Diaz Group", MODULE.AGENCY_WORDMARK_SOURCES)

    #: Wayback 快照里的原件：`id_` 让存档站原样吐出当年那张图，不带它注入的工具栏。
    ARCHIVE_ORIGINAL = r"^https://web\.archive\.org/web/\d{14}id_/https?://"

    def test_a_logo_taken_from_a_snapshot_is_the_raw_original(self):
        """官网只剩快照的公司，标识从快照里取。少了 `id_`，取回来的是存档站包了一层的
        页面或改写过地址的图，落盘的就不是那家当年挂的那张。
        """
        for table in (MODULE.WORDMARK_SOURCES, MODULE.AGENCY_WORDMARK_SOURCES,
                      MODULE.LOGO_SOURCES, MODULE.ICON_SOURCES):
            for name, url in table.items():
                if "web.archive.org" in url:
                    with self.subTest(name=name):
                        self.assertRegex(url, self.ARCHIVE_ORIGINAL)

    #: `harvest_maker_directories.py` 那三个名录各自的地址形态，加上官网只剩快照时的
    #: 存档原件。表里的每一条都得落在这里面，或者落在下面那份点名清单里。
    DIRECTORY_URLS = (
        r"^https://static\.mgstage\.com/mgs/img/pc/",
        r"^https://www\.prestige-av\.com/api/media/maker/",
        r"^https://www\.km-produce\.com/img2018/label/[^/]+/logo\.svg$",
        ARCHIVE_ORIGINAL,
    )

    #: 名录之外的来源，各有各的理由，逐条另有用例。整表只允许这几家走别的地址：
    #: 漏写一家就意味着有人往表里加了一条没人解释过的来源。
    NAMED_EXCEPTIONS = {"えむっ娘ラボ", "妄想族", "K M Produce", "変態紳士倶楽部",
                        "Prestige", "一本道", "Deep's", "アロマ企画", "本中", "E-BODY"}

    def test_every_wordmark_comes_from_a_maker_directory_or_a_named_exception(self):
        """用户 2026-09-04 与 09-22 指定的是那三个厂牌名录，不是随便哪张网图。"""
        self.assertTrue(self.original, "字标来源表不能是空的")
        off_directory = {
            studio for studio, url in MODULE.WORDMARK_SOURCES.items()
            if not any(re.match(pattern, url) for pattern in self.DIRECTORY_URLS)}
        self.assertEqual(off_directory, self.NAMED_EXCEPTIONS)

    def test_no_studio_takes_its_wordmark_and_its_square_logo_from_two_tables(self):
        """这一张表把两个位置都占了，所以和 `LOGO_SOURCES` 不能有交集——同一个厂牌
        落进两张表时，谁盖谁全看 `harvest` 里的代码顺序，那是看不出来的差别。
        """
        for studio in MODULE.WORDMARK_SOURCES:
            with self.subTest(studio=studio):
                self.assertNotIn(studio, MODULE.LOGO_SOURCES)
                self.assertNotIn(studio, MODULE.ICON_SOURCES)

    def test_a_studio_whose_own_site_carries_the_wordmark_is_pinned_to_that_site(self):
        """自动发现给 えむっ娘ラボ 小位挑的是它 X 账号那张项圈照片：400×400、内容比
        1.04，够大够方，里面没有脸所以人像闸也过。「照片不是标识」闸门判不出来，
        只能指定官网 header 里那张字标，两个位置都从它烤。
        """
        self.assertRegex(MODULE.WORDMARK_SOURCES["えむっ娘ラボ"],
                         r"^https://cdn\.up-timely\.com/image/16/site_design/")

    def test_a_maker_whose_works_all_sit_on_its_labels_takes_its_own_site_header(self):
        """妄想族是片商实体，作品全挂在旗下 label 上（ADR-0049）：展会名录没有它，
        它自家的 `maker/list` 列的是旗下 label。门面上那张字标只在官网 header 里。
        """
        self.assertRegex(MODULE.WORDMARK_SOURCES["妄想族"],
                         r"^https://www\.mousouzoku-av\.com/pc/images/pages/common/")

    def test_studios_the_user_named_take_the_wordmark_from_their_own_site_header(self):
        """用户 2026-09-25 点名：KMP 母公司官网 header 是矢量字标，装着的却是 X 头像位图；
        `変態紳士倶楽部` 官网 header 挂着字标，盘上一张图都没有。两家都指定官网那张。
        """
        self.assertRegex(MODULE.WORDMARK_SOURCES["K M Produce"],
                         r"^https://www\.km-produce\.com/wp-content/themes/[^/]+/img/logo\.svg$")
        self.assertRegex(MODULE.WORDMARK_SOURCES["変態紳士倶楽部"],
                         r"^https://cdn\.up-timely\.com/image/22/site_design/")

    def test_studios_swapped_after_the_audit_take_the_official_header_vector_first(self):
        """2026-09-25 全库审计后用户按推荐换的六家都取官网 header，有矢量的必须是矢量：
        同一枚标识换成位图版，等于把「能 svg 就 svg」退回去。
        """
        official = {"Prestige": "www.prestige-av.com", "一本道": "www.1pondo.tv",
                    "Deep's": "deeps.net", "アロマ企画": "www.aroma-p.com",
                    "本中": "cdn.up-timely.com", "E-BODY": "cdn.up-timely.com"}
        for studio, host in official.items():
            with self.subTest(studio=studio):
                self.assertEqual(urlsplit(MODULE.WORDMARK_SOURCES[studio]).hostname, host)
        for studio in ("Prestige", "一本道", "Deep's"):
            with self.subTest(studio=studio):
                self.assertTrue(MODULE.WORDMARK_SOURCES[studio].endswith(".svg"))

    def test_a_label_without_a_site_of_its_own_is_pinned_to_its_parent_directory(self):
        """ナンパTV 自己没有站，账本里也一条链接都没有——自动发现那四条链全都从
        链接出发，对它一条都启动不了。母公司 Prestige 的名录里有它的字标。
        """
        self.assertRegex(MODULE.WORDMARK_SOURCES["ナンパTV"],
                         r"^https://www\.prestige-av\.com/api/media/maker/")

    def test_the_labels_that_replaced_an_expo_logo_are_the_five_the_user_picked(self):
        """用户 2026-09-22 逐张看过对比图后点名这五家换走名录版，其余已有图的不收。

        名录版未必更大：`ラグジュTV` 落地 200×200，展会那份是 413×413。这条约束防的
        是往表里顺手多塞一家——那一家没人比过两版，装上去可能是降级。
        `S級素人`、`Real Works` 没有展会版可比，用户 2026-09-23 点名取 KMP 名录字标；
        `K M Produce` 与 `Prestige` 自己那一枚用户 2026-09-25 点名取官网 header。
        """
        picked = {"Jackson", "ラグジュTV", "million", "BAZOOKA", "俺の素人"}
        from_parent = {studio for studio, url in MODULE.WORDMARK_SOURCES.items()
                       if "prestige-av.com" in url or "km-produce.com" in url}
        self.assertEqual(from_parent, picked | {"ナンパTV", "S級素人", "Real Works",
                                                "K M Produce", "Prestige"})


class IconSourceTests(unittest.TestCase):
    """指定方标来源：只管小位，大位照旧由 `logo_row` 或发现流程出。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.candidates = Path(self.tmp.name).resolve() / "candidates"
        self.target = {"original_size": "", "installed": ""}
        self.url = "https://static.example/maker/banner-shiroutotv.jpg"
        self.original = dict(MODULE.ICON_SOURCES_BY_SAFE)
        MODULE.ICON_SOURCES_BY_SAFE["シロウトTV"] = self.url

    def tearDown(self):
        MODULE.ICON_SOURCES_BY_SAFE.clear()
        MODULE.ICON_SOURCES_BY_SAFE.update(self.original)
        self.tmp.cleanup()

    def row(self, payload=None, reachable=True):
        pages = {self.url: payload} if payload is not None else {}
        return MODULE.icon_source_row("シロウトTV", self.target,
                                      Fetch(pages, reachable=reachable), self.candidates)

    def test_the_square_mark_is_cut_out_of_a_wide_source_image(self):
        """名录那张 200×55 里方标和字标并排，落地的该是方标那一块。

        `bake_square` 的 `refit_plate` 按内容裁一遍，所以落地边长由标识本身决定，
        不是源图那 200。
        """
        row = self.row(block_png((60, 60)))
        self.assertEqual((row["variant"], row["verdict"]), (MODULE.ICON, MODULE.OK))
        self.assertEqual(row["link_kind"], "icon-source")
        width, height = (int(value) for value in str(row["mark_size"]).split("x"))
        self.assertEqual(width, height)
        self.assertLess(float(str(row["content_aspect"])), MODULE.link_marks.MAX_CONTENT_ASPECT)

    def test_the_candidate_file_matches_the_recorded_hash(self):
        row = self.row(block_png((60, 60)))
        stored = Path(str(row["candidate"])).read_bytes()
        self.assertEqual(hashlib.sha256(stored).hexdigest(), row["sha256"])
        self.assertTrue(str(row["candidate"]).endswith("シロウトTV.png"))

    def test_a_studio_without_a_registered_square_source_gets_no_row(self):
        self.assertIsNone(MODULE.icon_source_row("HEYZO", self.target, Fetch(),
                                                 self.candidates))

    def test_a_mark_that_shrinks_below_the_icon_floor_is_refused(self):
        """闸门量的是裁出来那一块，不是源图：源图够大、标识只占中间一丁点也不算数。"""
        row = self.row(block_png((20, 20)))
        self.assertEqual(row["verdict"], MODULE.TOOSMALL)
        self.assertEqual(row["candidate"], "")

    def test_an_unreachable_or_undecodable_source_is_未取得(self):
        for payload, reachable in ((None, False), (b"<html>404</html>", True)):
            with self.subTest(reachable=reachable):
                row = self.row(payload, reachable=reachable)
                self.assertEqual(row["verdict"], MODULE.MISSING)
                self.assertEqual(row["candidate"], "")

    def test_the_big_slot_keeps_its_own_source(self):
        """两位分开指才有这张表。小位走名录、大位仍走 `LOGO_SOURCES` 那张展会图。"""
        expo = "http://expo.example/034.png"
        saved = dict(MODULE.LOGO_SOURCES_BY_SAFE)
        MODULE.LOGO_SOURCES_BY_SAFE["シロウトTV"] = expo
        try:
            fetch = Fetch({self.url: block_png((60, 60)),
                           expo: block_png((320, 320))})
            rows = MODULE.harvest({"シロウトTV": self.target}, {}, fetch, self.candidates)
        finally:
            MODULE.LOGO_SOURCES_BY_SAFE.clear()
            MODULE.LOGO_SOURCES_BY_SAFE.update(saved)
        self.assertEqual([(row["variant"], row["url"]) for row in rows],
                         [(MODULE.ICON, self.url), (MODULE.LOGO, expo)])

    def test_the_square_source_keeps_link_discovery_out_of_the_small_slot(self):
        """指定就是结论。放它去走发现流程只会把指好的那一枚挤掉。"""
        saved = dict(MODULE.LOGO_SOURCES_BY_SAFE)
        MODULE.LOGO_SOURCES_BY_SAFE.pop("シロウトTV", None)
        try:
            fetch = Fetch({self.url: block_png((60, 60))})
            rows = MODULE.harvest({"シロウトTV": self.target}, {}, fetch, self.candidates)
        finally:
            MODULE.LOGO_SOURCES_BY_SAFE.clear()
            MODULE.LOGO_SOURCES_BY_SAFE.update(saved)
        self.assertEqual([row["variant"] for row in rows], [MODULE.ICON])
        self.assertEqual(fetch.asked, [self.url])


class Fetch:
    """`fetch.fetched` 是 harvest 用来分辨「站点不可达」和「取到了但都是字标」的
    唯一依据，所以替身也得照着记数，不能只当个哑函数。"""

    def __init__(self, pages=None, reachable=True):
        self.pages = pages or {}
        self.reachable = reachable
        self.fetched = 0
        self.asked: list[str] = []

    def __call__(self, url):
        self.asked.append(url)
        if not self.reachable:
            return None
        if self.pages:
            got = self.pages.get(url)
            if got is None:
                return None
            self.fetched += 1
            return got, "image/png"
        self.fetched += 1
        return b"payload", "image/png"


class HarvestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.candidates = self.root / "candidates"
        self.targets = {"Fitch": {"original_size": "130x43", "installed": "Fitch.img"}}
        self.links = {"Fitch": [{"entity_id": 5591, "studio": "Fitch",
                                 "link_kind": "official", "url": "https://fitch-av.com/"}]}

    def tearDown(self):
        self.tmp.cleanup()

    def run_harvest(self, mark, targets=None, links=None, reachable=True, fetch=None):
        fetch = fetch if fetch is not None else Fetch(reachable=reachable)
        original = MODULE.site_icons.best_mark

        def stub(url, fetcher, render, fallback=None, accept=None):
            fetcher(url)
            return mark

        MODULE.site_icons.best_mark = stub
        try:
            return MODULE.harvest(self.targets if targets is None else targets,
                                  self.links if links is None else links,
                                  fetch, self.candidates)
        finally:
            MODULE.site_icons.best_mark = original

    def test_a_square_mark_becomes_an_ok_row_with_a_file(self):
        rows = self.run_harvest(png_bytes())
        self.assertEqual(rows[0]["verdict"], MODULE.OK)
        self.assertTrue(Path(str(rows[0]["candidate"])).is_file())
        self.assertEqual(rows[0]["safe"], "Fitch")

    def test_a_studio_with_no_official_link_is_skipped_not_failed(self):
        """没链接是「这条路走不通」，不是取证失败，两者的下一步动作不同。"""
        rows = self.run_harvest(png_bytes(), links={})
        self.assertEqual(rows[0]["verdict"], MODULE.SKIP)
        self.assertEqual(rows[0]["url"], "")

    def test_another_wordmark_is_rejected_not_stored(self):
        """闸门退回来就该空手而归：把同一条字标缩小再存一遍等于什么都没做。"""
        rows = self.run_harvest(None)
        self.assertEqual(rows[0]["verdict"], MODULE.WORDMARK)
        self.assertEqual(rows[0]["candidate"], "")
        self.assertFalse(self.candidates.exists())

    def test_an_unreachable_site_is_未取得_not_a_conclusion(self):
        """Idea Pocket 和 MOODYZ 的首页实测握手就断。那是「还没查到」，
        不是「这个厂牌只有字标」——写成后者会让人再也不去重试。"""
        rows = self.run_harvest(None, reachable=False)
        self.assertEqual(rows[0]["verdict"], MODULE.MISSING)
        self.assertIn("一份字节都没取回来", rows[0]["evidence"])

    def test_the_evidence_names_the_url_that_was_tried(self):
        rows = self.run_harvest(None)
        self.assertIn("https://fitch-av.com/", rows[0]["evidence"])

    def test_only_small_icons_is_its_own_verdict(self):
        """FC2 全站只有 16×16。那不是「只有字标」，下一步是找更大的资产。
        两者混成一个结论，就再没人会去找了。"""
        rows = self.harvest_with_reasons(["只有 16x16"])
        self.assertEqual(rows[0]["verdict"], MODULE.TOOSMALL)

    def harvest_with_reasons(self, reasons):
        original = MODULE.site_icons.best_mark

        def stub(url, fetcher, policy, fallback=None, accept=None):
            fetcher(url)
            policy.reasons.extend(reasons)
            return None

        MODULE.site_icons.best_mark = stub
        try:
            return MODULE.harvest(self.targets, self.links, Fetch(), self.candidates)
        finally:
            MODULE.site_icons.best_mark = original

    def test_every_row_declares_which_variant_it_is(self):
        """CSV 里 icon 行和 logo 行长得一样，不写 variant 就分不出装到哪个位置。"""
        rows = self.run_harvest(png_bytes())
        self.assertEqual([row["variant"] for row in rows], [MODULE.ICON])

    def test_a_rejected_wordmark_is_baked_into_a_plate_and_installed_anyway(self):
        """用户 2026-09-03 的口径：不是 icon 也可以装 icon，尽量不要落入无图。"""
        original = MODULE.site_icons.best_mark

        def stub(url, fetcher, policy, fallback=None, accept=None):
            fetcher(url)
            policy(block_png((298, 50)))
            return None

        MODULE.site_icons.best_mark = stub
        try:
            rows = MODULE.harvest(self.targets, self.links, Fetch(), self.candidates)
        finally:
            MODULE.site_icons.best_mark = original
        row = rows[0]
        self.assertEqual(row["verdict"], MODULE.PADDED)
        self.assertIn(MODULE.PADDED, MODULE.INSTALLABLE)
        with Image.open(io.BytesIO(Path(str(row["candidate"])).read_bytes())) as image:
            self.assertEqual(image.size, (PLATE_SIDE, PLATE_SIDE), "烤出来必须是方的")
            self.assertNotIn("A", image.getbands(), "装进去的文件必须不透明")
        self.assertEqual(row["mark_size"], PLATE_SIZE)
        self.assertGreater(float(row["content_aspect"]),
                           MODULE.link_marks.MAX_CONTENT_ASPECT,
                           "内容比照记：那个数就是「这枚其实是字标」的提示")
        self.assertIn("方形标识未取得，装的是字标", row["evidence"])

    def run_wordmark_harvest(self, targets=None, links=None, fetch=None):
        original = MODULE.site_icons.best_mark

        def stub(url, fetcher, policy, fallback=None, accept=None):
            fetcher(url)
            policy(block_png((298, 50)))
            return None

        MODULE.site_icons.best_mark = stub
        try:
            return MODULE.harvest(self.targets if targets is None else targets,
                                  self.links if links is None else links,
                                  fetch or Fetch(), self.candidates)
        finally:
            MODULE.site_icons.best_mark = original

    def test_the_wordmark_plate_also_fills_the_logo_slot(self):
        """只装 icon 位，BangBus 页顶上会回落到母品牌的 `BangBus.img`（BANGBROS 字标）：
        小位对了，大位挂着别家的牌子。"""
        rows = self.run_wordmark_harvest()
        self.assertEqual([row["variant"] for row in rows], [MODULE.ICON, MODULE.LOGO])
        logo = rows[1]
        self.assertEqual(logo["verdict"], MODULE.OK)
        self.assertEqual(logo["safe"], rows[0]["safe"])
        payload = Path(str(logo["candidate"])).read_bytes()
        self.assertEqual(logo["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(logo["content_aspect"], rows[0]["content_aspect"])
        self.assertIn("298x50", logo["evidence"], "原字标尺寸留在证据里供复核")
        self.assertNotEqual(logo["candidate"], rows[0]["candidate"],
                            "两份候选文件不能互相覆盖")

    def test_the_logo_slot_gets_an_opaque_square(self):
        """厂牌页大位是 160px 的 `object-fit: cover` 方框，宽条装上去只剩中间两个字母。

        两位共用同一份烤好的方图，字节一致才能保证两个位置显示同一枚标识。
        """
        rows = self.run_wordmark_harvest()
        logo = rows[1]
        with Image.open(io.BytesIO(Path(str(logo["candidate"])).read_bytes())) as image:
            self.assertEqual(image.size, (PLATE_SIDE, PLATE_SIDE))
            self.assertNotIn("A", image.getbands())
        self.assertEqual(logo["mark_size"], PLATE_SIZE)
        self.assertEqual(logo["sha256"], rows[0]["sha256"], "与 icon 位同一份字节")

    def test_a_wordmark_logo_installs_into_the_logo_file(self):
        rows = self.run_wordmark_harvest()
        logos = self.root / "logos"
        written = MODULE.install(rows, logos)
        self.assertEqual(written, ["Fitch.icon.img", "Fitch.img", "Fitch.logo.img"])
        for name in ("Fitch.logo.img", "Fitch.icon.img", "Fitch.img"):
            with self.subTest(name=name), Image.open(logos / name) as image:
                self.assertEqual(image.size, (PLATE_SIDE, PLATE_SIDE))
                self.assertNotIn("A", image.getbands())

    def test_install_bakes_every_candidate_into_an_opaque_square(self):
        """装盘的字节过同一个烤底入口，所以 logo 目录里天然没有透明底和长条。

        入口只有这一个，`normalize_studio_logos.py` 是同一条规则对历史文件的回溯。
        """
        rows = self.run_wordmark_harvest()
        logos = self.root / "logos"
        # 候选换成一枚透明底的方标：过得了内容比闸门，可它还是透明的。
        transparent = block_png((256, 256), inset=32)
        candidate = self.candidates / "transparent.png"
        candidate.write_bytes(transparent)
        rows[0]["candidate"] = str(candidate)
        rows[0]["sha256"] = hashlib.sha256(transparent).hexdigest()
        MODULE.install([rows[0]], logos)
        with Image.open(logos / "Fitch.icon.img") as image:
            self.assertNotIn("A", image.getbands(), "透明底的方标也要烤上白底")
            self.assertEqual(image.size[0], image.size[1])
        provenance = json.loads(
            (logos / "Fitch.icon.img.provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["sha256"], rows[0]["sha256"], "候选自己的哈希")
        self.assertEqual(
            provenance["installed_sha256"],
            hashlib.sha256((logos / "Fitch.icon.img").read_bytes()).hexdigest(),
            "真正写进目录的那一份另记一列，否则复核时对不上文件")

    def test_a_working_designated_logo_source_wins_over_the_wordmark(self):
        targets = {"FC2-PPV": {"original_size": "", "installed": ""}}
        links = {"FC2-PPV": [{"entity_id": 1, "studio": "FC2-PPV",
                              "link_kind": "official", "url": "https://fc2.com/"}]}
        source = MODULE.LOGO_SOURCES_BY_SAFE["FC2-PPV"]
        rows = self.run_wordmark_harvest(targets, links, Fetch(pages={source: png_bytes()}))
        logos = [row for row in rows if row["variant"] == MODULE.LOGO]
        self.assertEqual(len(logos), 1, "两行 logo 装进同一个文件，后写的会盖掉先写的")
        self.assertEqual(logos[0]["link_kind"], "logo-source")

    def test_a_failed_designated_source_keeps_its_row_and_the_wordmark_logo_follows(self):
        targets = {"FC2-PPV": {"original_size": "", "installed": ""}}
        links = {"FC2-PPV": [{"entity_id": 1, "studio": "FC2-PPV",
                              "link_kind": "official", "url": "https://fc2.com/"}]}
        rows = self.run_wordmark_harvest(targets, links, Fetch(reachable=False))
        logos = [row for row in rows if row["variant"] == MODULE.LOGO]
        self.assertEqual([row["verdict"] for row in logos], [MODULE.MISSING, MODULE.OK])
        self.assertEqual(logos[0]["candidate"], "", "失败行没有候选，安装时不会落地")
        self.assertTrue(logos[1]["candidate"])

    def designated_source_harvest(self, studio, payload):
        """只有指定 logo 来源、账本里一条链接都没有的厂牌：`best_mark` 不会被调。

        另外两张表里同名的条目要摘掉：它们各自走别的分支，留着测的就不是这一条路了。
        """
        safe = MODULE.safe_name(studio)
        url = f"http://logos.example/{safe}.png"
        original = dict(MODULE.LOGO_SOURCES_BY_SAFE)
        others = {id(table): dict(table) for table in
                  (MODULE.WORDMARK_SOURCES_BY_SAFE, MODULE.ICON_SOURCES_BY_SAFE)}
        names = dict(MODULE.LOGO_SOURCE_NAMES)
        MODULE.LOGO_SOURCES_BY_SAFE[safe] = url
        MODULE.LOGO_SOURCE_NAMES[safe] = studio
        for table in (MODULE.WORDMARK_SOURCES_BY_SAFE, MODULE.ICON_SOURCES_BY_SAFE):
            table.pop(safe, None)
        try:
            return MODULE.harvest({safe: {"original_size": "", "installed": ""}}, {},
                                  Fetch(pages={url: payload}), self.candidates)
        finally:
            MODULE.LOGO_SOURCES_BY_SAFE.clear()
            MODULE.LOGO_SOURCES_BY_SAFE.update(original)
            for table in (MODULE.WORDMARK_SOURCES_BY_SAFE, MODULE.ICON_SOURCES_BY_SAFE):
                table.clear()
                table.update(others[id(table)])
            MODULE.LOGO_SOURCE_NAMES.clear()
            MODULE.LOGO_SOURCE_NAMES.update(names)

    def test_a_link_less_studio_takes_its_icon_from_the_designated_logo(self):
        """小位空着不是因为找不到图，是因为发现流程从官网出发，而它没有官网。

        大位这张是厂牌自己在名录里交的标识资产，比任何 favicon 都准，两位共用它。
        """
        rows = self.designated_source_harvest("俺の素人", png_bytes((320, 320)))
        icon, logo = rows[0], rows[1]
        self.assertEqual([row["variant"] for row in rows], [MODULE.ICON, MODULE.LOGO])
        self.assertEqual(icon["verdict"], MODULE.OK, "本来就是方图，一个像素都没动")
        self.assertEqual(icon["link_kind"], "logo-source")
        self.assertEqual(icon["url"], logo["url"], "小位记的是取图那一份的地址")
        self.assertEqual(icon["mark_size"], "320x320")
        self.assertIn("与 logo 位同一份指定来源", icon["evidence"])
        stored = Path(str(icon["candidate"])).read_bytes()
        self.assertEqual(hashlib.sha256(stored).hexdigest(), icon["sha256"])
        self.assertNotEqual(icon["candidate"], logo["candidate"],
                            "两份候选文件不能互相覆盖")

    def test_the_review_row_names_the_studio_a_japanese_name_cannot_survive(self):
        """`俺の素人` 的安全文件名是一排下划线，复核件上照那个反推认不出是谁家的。"""
        rows = self.designated_source_harvest("俺の素人", png_bytes((320, 320)))
        self.assertEqual(rows[0]["studio"], "俺の素人")
        self.assertEqual(rows[0]["safe"], MODULE.safe_name("俺の素人"))

    def test_a_wide_designated_logo_is_padded_for_the_small_slot(self):
        """名录里 2014 那届是 270×180 的横向字标。补白装上去也比露出无图强。"""
        rows = self.designated_source_harvest("TEPPAN", png_bytes((270, 180)))
        icon = rows[0]
        self.assertEqual(icon["verdict"], MODULE.PADDED)
        self.assertIn(MODULE.PADDED, MODULE.INSTALLABLE)
        with Image.open(io.BytesIO(Path(str(icon["candidate"])).read_bytes())) as image:
            self.assertEqual(image.size[0], image.size[1], "小位装的必须是方图")
            if "A" in image.getbands():
                # 名录 2014 那届是整幅不透明的 jpg，补白按边缘主色填，不该留出透明边。
                self.assertEqual(image.getchannel("A").getextrema(), (255, 255))

    def test_a_designated_source_fills_both_files_for_a_link_less_studio(self):
        rows = self.designated_source_harvest("Dogma", png_bytes((320, 320)))
        logos = self.root / "logos"
        self.assertEqual(MODULE.install(rows, logos),
                         ["Dogma.icon.img", "Dogma.img", "Dogma.logo.img"])

    def test_a_studio_with_a_working_icon_keeps_it_over_the_designated_logo(self):
        """官网做出方标时不动小位：那一枚才是这个站自己声明的图标。"""
        url = "http://logos.example/Fitch.png"
        original = dict(MODULE.LOGO_SOURCES_BY_SAFE)
        MODULE.LOGO_SOURCES_BY_SAFE["Fitch"] = url
        try:
            rows = self.run_harvest(png_bytes(), fetch=Fetch(pages={url: png_bytes()}))
        finally:
            MODULE.LOGO_SOURCES_BY_SAFE.clear()
            MODULE.LOGO_SOURCES_BY_SAFE.update(original)
        self.assertEqual(rows[0]["link_kind"], "official")
        self.assertEqual(rows[0]["verdict"], MODULE.OK)

    def test_a_wordmark_too_small_to_pad_is_still_a_wordmark_verdict(self):
        """16 px 高的条状 favicon 补白也救不回来，装上去只是一条糊线。"""
        original = MODULE.site_icons.best_mark

        def stub(url, fetcher, policy, fallback=None, accept=None):
            fetcher(url)
            policy(block_png((64, 16), inset=2))
            return None

        MODULE.site_icons.best_mark = stub
        try:
            rows = MODULE.harvest(self.targets, self.links, Fetch(), self.candidates)
        finally:
            MODULE.site_icons.best_mark = original
        self.assertEqual(rows[0]["verdict"], MODULE.WORDMARK)
        self.assertEqual(rows[0]["candidate"], "")

    def test_a_platform_wide_favicon_gets_its_own_verdict(self):
        """MonstersOfCock 的实测形态：主机级 favicon 过了所有闸门，仍然不是它的标识。

        判成「仍是字标」会让人以为查过了这个频道，其实查的是整个 bangbros.com。
        """
        links = {"MonstersOfCock": [{
            "entity_id": 5622, "studio": "MonstersOfCock", "link_kind": "official",
            "url": "https://network.example/websites/MonstersOfCock"}]}
        targets = {"MonstersOfCock": {"original_size": "296x82",
                                      "installed": "MonstersOfCock.img"}}
        template = block_png((64, 64), inset=6)
        fetch = Fetch({
            "https://network.example": b'<link rel="icon" sizes="64x64" href="/f.png">',
            "https://network.example/f.png": template,
            "https://network.example/favicon.ico": template,
            "https://network.example/apple-touch-icon.png": template})
        rows = MODULE.harvest(targets, links, fetch, self.candidates)
        self.assertEqual(rows[0]["verdict"], MODULE.SHARED)
        self.assertEqual(rows[0]["candidate"], "")
        self.assertIn("network.example", rows[0]["evidence"])
        self.assertIn(hashlib.sha256(template).hexdigest()[:8], rows[0]["evidence"],
                      "证据要指名取到的是哪一份，不然没法复查")

    def test_a_root_link_is_not_subject_to_the_shared_host_guard(self):
        """`https://honnaka.jp/` 指的就是整个主机，它的 favicon 就是它的标识。"""
        icon = block_png((128, 128), inset=10)
        fetch = Fetch({
            "https://honnaka.jp": b'<link rel="icon" sizes="128x128" href="/f.png">',
            "https://honnaka.jp/f.png": icon})
        rows = MODULE.harvest(
            {"Hon_Naka": {"original_size": "", "installed": ""}},
            {"Hon_Naka": [{"entity_id": 5599, "studio": "Hon Naka",
                           "link_kind": "official", "url": "https://honnaka.jp/"}]},
            fetch, self.candidates)
        self.assertEqual(rows[0]["verdict"], MODULE.OK)
        self.assertEqual(rows[0]["mark_size"], "128x128")


class SquareMarkTests(unittest.TestCase):
    """判「是不是方标」的那一层。这里判错过一次，代价是六个厂牌被误记成只有字标。"""

    def png(self, size, box=None, color=(220, 30, 90)):
        image = Image.new("RGB", size, "white")
        left, top, right, bottom = box or (0, 0, size[0], size[1])
        for x in range(left, right):
            for y in range(top, bottom):
                image.putpixel((x, y), color)
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        return buffer.getvalue()

    def test_a_square_favicon_passes_at_its_native_size(self):
        """32×32 的 favicon 顶 28px 的筛选片绰绰有余。`link_marks.render_mark`
        的 MIN_DESIGNED_SIZE=96 是给 128px 圆标定的，套到这里会把它们全退掉。"""
        policy = MODULE.SquareMark()
        made = policy(self.png((32, 32), (4, 4, 28, 28)))
        self.assertIsNotNone(made)
        self.assertEqual(policy.size, "32x32")
        with Image.open(io.BytesIO(made)) as image:
            self.assertEqual(image.size, (32, 32), "不放大：插值只会更糊")

    def test_a_wide_wordmark_is_refused(self):
        policy = MODULE.SquareMark()
        self.assertIsNone(policy(self.png((256, 64), (4, 24, 252, 40))))
        self.assertTrue(any("字标" in reason for reason in policy.reasons))

    def test_a_tiny_icon_is_refused_with_its_size(self):
        policy = MODULE.SquareMark()
        self.assertIsNone(policy(self.png((16, 16), (2, 2, 14, 14))))
        self.assertEqual(policy.reasons, ["只有 16x16"])

    def test_the_passing_aspect_is_recorded_for_review(self):
        """Fitch 以 1.84 压线过闸，其余六个都在 1.15 以下。复核要看得见这个数。"""
        policy = MODULE.SquareMark()
        policy(self.png((64, 64), (2, 18, 62, 46)))
        self.assertTrue(policy.aspect)
        self.assertGreater(float(policy.aspect), 1.0)

    def test_an_octet_stream_png_is_still_a_png(self):
        """用户指定的 FC2 图标那台服务器回 `application/octet-stream`。

        按 content-type 决定能不能解会把它整枚丢掉；`link_marks.decode` 靠 PIL 嗅探。
        """
        policy = MODULE.SquareMark()
        made = policy(block_png((400, 400), inset=20),
                      content_type="application/octet-stream")
        self.assertIsNotNone(made)
        self.assertEqual(policy.size, "400x400")

    def test_a_refused_wordmark_is_kept_for_the_padding_fallback(self):
        """退回的理由要留，退回的**字节**也要留：字标现在是可装的回落。"""
        policy = MODULE.SquareMark()
        self.assertIsNone(policy(block_png((298, 50))))
        self.assertIsNotNone(policy.wordmark)
        self.assertEqual(policy.wordmark_size, "298x50")
        self.assertGreater(float(policy.wordmark_aspect),
                           MODULE.link_marks.MAX_CONTENT_ASPECT)

    def test_the_first_refused_wordmark_wins_not_the_last(self):
        """`best_mark` 的遍历顺序已经是「覆盖表 → 声明 → 根路径猜测」。

        留最后一份等于让根路径的猜测盖掉覆盖表里指定的那一份。
        """
        policy = MODULE.SquareMark()
        policy(block_png((298, 50)))
        policy(block_png((512, 64)))
        self.assertEqual(policy.wordmark_size, "298x50")

    def test_a_wordmark_below_the_short_edge_is_not_kept(self):
        policy = MODULE.SquareMark()
        self.assertIsNone(policy(block_png((64, 16), inset=2)))
        self.assertIsNone(policy.wordmark)


class RunTests(unittest.TestCase):
    """整条链路：账本 + 已安装目录 → 复核 CSV，`--install` 才落盘。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.logos = self.root / "logos"
        self.logos.mkdir()
        for safe in ("Fitch", "HEYZO", "kawaii"):
            (self.logos / f"{safe}.img").write_bytes(b"padded wordmark")
            (self.logos / f"{safe}.img.normalization.json").write_text(
                json.dumps({"action": "pad-to-square",
                            "original_width": 130, "original_height": 43}),
                encoding="utf-8")
        self.database = self.root / "ledger.db"
        connection = sqlite3.connect(self.database)
        connection.executescript(
            "CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT);"
            "CREATE TABLE entity_link(id INTEGER PRIMARY KEY, entity_id INTEGER,"
            " link_kind TEXT, label TEXT, url TEXT);")
        connection.executemany(
            "INSERT INTO entity(id, kind, canonical_name) VALUES(?,?,?)",
            [(1, "studio", "Fitch"), (2, "studio", "HEYZO"), (3, "studio", "kawaii"),
             (4, "studio", "Hon Naka"), (5, "agency", "T-POWERS")])
        connection.executemany(
            "INSERT INTO entity_link(entity_id, link_kind, label, url) VALUES(?,?,?,?)",
            [(1, "official", "官方网站", "https://fitch-av.com/"),
             (2, "official", "官方网站", "https://www.heyzo.com/"),
             # 有链接、一张图都没有：账本里 Hon Naka 就是这一例。
             (4, "official", "官方网站", "https://honnaka.jp/"),
             (5, "official", "T-POWERS", "https://t-powers.co.jp/")])
        connection.commit()
        connection.close()
        # 两张指定来源表都指向站外地址，留着这一整套测试就会去联网取图。
        self.sources = dict(MODULE.LOGO_SOURCES_BY_SAFE)
        self.wordmarks = dict(MODULE.WORDMARK_SOURCES_BY_SAFE)
        MODULE.LOGO_SOURCES_BY_SAFE.clear()
        MODULE.WORDMARK_SOURCES_BY_SAFE.clear()
        # `run` 自己造 Fetcher，替身挡不到它：站点自己那两条后手会真的去敲 fitch-av.com。
        self.assets = MODULE.site_assets
        MODULE.site_assets = lambda url, fetch: ([], [])

    def tearDown(self):
        MODULE.LOGO_SOURCES_BY_SAFE.clear()
        MODULE.LOGO_SOURCES_BY_SAFE.update(self.sources)
        MODULE.WORDMARK_SOURCES_BY_SAFE.clear()
        MODULE.WORDMARK_SOURCES_BY_SAFE.update(self.wordmarks)
        MODULE.site_assets = self.assets
        self.tmp.cleanup()

    def invoke(self, install=False, marks=None):
        """只有 Fitch 能做出方标；kawaii 根本没链接。

        替身不调 `fetcher`——真调下去就是往 fitch-av.com 发请求，测试不联网。
        `run` 自己造 Fetcher，所以站点自己那两条后手也要在这一层挡住；人脸闸同理，
        开着它会去下模型。
        """
        marks = marks if marks is not None else {"https://fitch-av.com/": png_bytes()}
        original = MODULE.site_icons.best_mark
        MODULE.site_icons.best_mark = (
            lambda url, fetcher, render, fallback=None, accept=None: marks.get(url))
        try:
            return SCRIPT.run(SimpleNamespace(
                database=self.database, output=self.root / "studio-icons.csv",
                logo_root=self.logos, candidate_dir=self.root / "candidates",
                only=[], kind="studio", interval=0.0, timeout=1.0, install=install,
                no_face_gate=True, avatars=self.root / "avatars.json"))
        finally:
            MODULE.site_icons.best_mark = original

    def read_csv(self):
        from peach.review_csv import read_rows
        return list(read_rows(self.root / "studio-icons.csv"))

    def test_an_agency_run_looks_only_at_agencies(self):
        """事务所和厂牌是同一件事的两批公司，只是这一趟收谁由 kind 定。"""
        original = MODULE.site_icons.best_mark
        MODULE.site_icons.best_mark = (
            lambda url, fetcher, render, fallback=None, accept=None: None)
        try:
            SCRIPT.run(SimpleNamespace(
                database=self.database, output=self.root / "agency-icons.csv",
                logo_root=self.logos, candidate_dir=self.root / "candidates",
                only=[], kind="agency", interval=0.0, timeout=1.0, install=False,
                no_face_gate=True, avatars=self.root / "avatars.json"))
        finally:
            MODULE.site_icons.best_mark = original
        from peach.review_csv import read_rows
        rows = list(read_rows(self.root / "agency-icons.csv"))
        self.assertEqual({row["safe"] for row in rows}, {"T-POWERS"})

    def test_an_agency_run_skips_the_studio_only_backlog(self):
        """补白名单和那两张指定来源表都是厂牌那一趟的，按名字撞上就会装错图。"""
        MODULE.LOGO_SOURCES_BY_SAFE["Fitch"] = "https://example.invalid/fitch.png"
        original = MODULE.site_icons.best_mark
        MODULE.site_icons.best_mark = (
            lambda url, fetcher, render, fallback=None, accept=None: None)
        try:
            SCRIPT.run(SimpleNamespace(
                database=self.database, output=self.root / "agency-icons.csv",
                logo_root=self.logos, candidate_dir=self.root / "candidates",
                only=[], kind="agency", interval=0.0, timeout=1.0, install=False,
                no_face_gate=True, avatars=self.root / "avatars.json"))
        finally:
            MODULE.site_icons.best_mark = original
        from peach.review_csv import read_rows
        self.assertEqual({row["safe"] for row in read_rows(self.root / "agency-icons.csv")},
                         {"T-POWERS"})

    def test_the_csv_covers_every_target_studio(self):
        stats = self.invoke()
        self.assertEqual(stats["复核行"], 4)
        self.assertEqual({row["safe"] for row in self.read_csv()},
                         {"Fitch", "HEYZO", "kawaii", "Hon_Naka"})

    def test_ok_rows_come_first(self):
        """人工复核从上往下看，能用的那几行不该埋在一堆「无官网链接」下面。"""
        self.invoke()
        self.assertEqual(self.read_csv()[0]["safe"], "Fitch")
        self.assertEqual(self.read_csv()[0]["verdict"], MODULE.OK)

    def test_a_studio_with_no_link_is_counted_separately(self):
        stats = self.invoke()
        self.assertEqual((stats["ok"], stats["无官网链接"]), (1, 1))

    def test_reporting_never_touches_the_installed_directory(self):
        """默认只出复核件。装图必须是显式的第二步。"""
        self.invoke()
        self.assertEqual(sorted(path.name for path in self.logos.glob("*.icon.img")), [])

    def test_install_writes_only_the_new_variant_files(self):
        stats = self.invoke(install=True)
        self.assertEqual(stats["已安装"], ["Fitch.icon.img"])
        self.assertEqual(sorted(path.name for path in self.logos.glob("*.logo.img")), [])

    def test_only_narrows_the_batch(self):
        original = MODULE.site_icons.best_mark
        MODULE.site_icons.best_mark = (
            lambda url, fetcher, render, fallback=None, accept=None: None)
        try:
            stats = SCRIPT.run(SimpleNamespace(
                database=self.database, output=self.root / "studio-icons.csv",
                logo_root=self.logos, candidate_dir=self.root / "candidates",
                only=["HEYZO"], kind="studio", interval=0.0, timeout=1.0, install=False,
                no_face_gate=True, avatars=self.root / "avatars.json"))
        finally:
            MODULE.site_icons.best_mark = original
        self.assertEqual(stats["复核行"], 1)
        self.assertEqual(self.read_csv()[0]["safe"], "HEYZO")

    def test_a_studio_with_a_link_but_no_image_joins_the_batch(self):
        """账本里 Hon Naka 就是这一例：不在补白名单里，两个位置照样是空的。"""
        stats = self.invoke()
        self.assertIn("Hon_Naka", {row["safe"] for row in self.read_csv()})
        self.assertEqual(stats["目标厂牌"], 4)
        empty = [row for row in self.read_csv() if row["safe"] == "Hon_Naka"][0]
        self.assertEqual((empty["installed"], empty["original_size"]), ("", ""))

    def test_install_also_writes_the_plain_file_for_a_studio_with_no_image(self):
        """`<safe>.img` 是不带 variant 和认不出的 variant 的回落。

        只写 `<safe>.icon.img` 的话，厂牌页那个 160 px 大位仍然 404。
        """
        stats = self.invoke(install=True,
                            marks={"https://honnaka.jp/": png_bytes()})
        self.assertEqual(sorted(stats["已安装"]),
                         ["Hon_Naka.icon.img", "Hon_Naka.img"])
        self.assertTrue((self.logos / "Hon_Naka.img.ct").is_file())

    def test_install_never_overwrites_an_existing_plain_file(self):
        stats = self.invoke(install=True)
        self.assertEqual(stats["已安装"], ["Fitch.icon.img"])
        self.assertEqual((self.logos / "Fitch.img").read_bytes(), b"padded wordmark")


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.logos = self.root / "logos"
        self.logos.mkdir()
        (self.logos / "Fitch.img").write_bytes(b"the padded wordmark")
        self.payload = png_bytes()
        candidate = self.root / "Fitch.png"
        candidate.write_bytes(self.payload)
        self.row = {"verdict": MODULE.OK, "safe": "Fitch", "candidate": str(candidate),
                    "sha256": hashlib.sha256(self.payload).hexdigest(),
                    "url": "https://fitch-av.com/"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_icon_lands_beside_the_existing_file(self):
        """加的是新文件名。已安装的 `<safe>.img` 一个字节都不能动。"""
        written = MODULE.install([self.row], self.logos)
        self.assertEqual(written, ["Fitch.icon.img"])
        self.assertEqual((self.logos / "Fitch.icon.img").read_bytes(), self.payload)
        self.assertEqual((self.logos / "Fitch.img").read_bytes(), b"the padded wordmark")

    def test_the_content_type_sidecar_is_written(self):
        MODULE.install([self.row], self.logos)
        self.assertEqual((self.logos / "Fitch.icon.img.ct").read_text(encoding="utf-8"),
                         "image/png")

    def test_provenance_records_the_source_url(self):
        MODULE.install([self.row], self.logos)
        data = json.loads((self.logos / "Fitch.icon.img.provenance.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(data["source_url"], "https://fitch-av.com/")
        self.assertEqual(data["variant"], "icon")

    def test_a_tampered_candidate_is_refused(self):
        Path(str(self.row["candidate"])).write_bytes(b"something else")
        with self.assertRaises(ValueError):
            MODULE.install([self.row], self.logos)
        self.assertFalse((self.logos / "Fitch.icon.img").exists())

    def test_rows_that_did_not_pass_install_nothing(self):
        for verdict in (MODULE.WORDMARK, MODULE.SHARED, MODULE.TOOSMALL, MODULE.MISSING):
            with self.subTest(verdict=verdict):
                MODULE.install([dict(self.row, verdict=verdict)], self.logos)
                self.assertFalse((self.logos / "Fitch.icon.img").exists())

    def test_a_smaller_mark_never_replaces_a_bigger_installed_one(self):
        """取数抖一下这一趟就只剩声明的小图标，装上去等于用一次网络故障换掉好图。"""
        big = png_bytes((968, 968))
        (self.logos / "Fitch.icon.img").write_bytes(big)
        self.assertEqual(MODULE.install([self.row], self.logos), [])
        self.assertEqual((self.logos / "Fitch.icon.img").read_bytes(), big)

    def test_a_bigger_mark_takes_the_slot(self):
        (self.logos / "Fitch.icon.img").write_bytes(png_bytes((64, 64)))
        self.assertEqual(MODULE.install([self.row], self.logos), ["Fitch.icon.img"])
        self.assertEqual((self.logos / "Fitch.icon.img").read_bytes(), self.payload)

    def test_a_designated_source_replaces_a_bigger_one(self):
        """那道守卫防的是自动发现的抖动。指定表里的地址是人逐张看过写进去的，
        换上一张小的也是想要的结果——`シロウトTV` 的小位就是 414 换成 64。
        """
        for kind in MODULE.PINNED_KINDS:
            with self.subTest(kind=kind):
                (self.logos / "Fitch.icon.img").write_bytes(png_bytes((968, 968)))
                row = dict(self.row, link_kind=kind)
                self.assertEqual(MODULE.install([row], self.logos), ["Fitch.icon.img"])
                self.assertEqual((self.logos / "Fitch.icon.img").read_bytes(),
                                 self.payload)

    def test_an_unreadable_installed_file_is_not_treated_as_bigger(self):
        """`<safe>.img` 里躺着的不一定是图；读不出尺寸就按空位办，别把大位卡死。"""
        (self.logos / "Fitch.icon.img").write_bytes(b"not an image")
        self.assertEqual(MODULE.install([self.row], self.logos), ["Fitch.icon.img"])

    def test_a_padded_wordmark_installs_too(self):
        """用户的口径是「不是 icon 也可以装 icon」，所以这个判词必须真的会落盘。"""
        written = MODULE.install([dict(self.row, verdict=MODULE.PADDED)], self.logos)
        self.assertEqual(written, ["Fitch.icon.img"])

    def test_the_logo_variant_lands_in_its_own_file(self):
        row = dict(self.row, variant=MODULE.LOGO)
        self.assertEqual(MODULE.install([row], self.logos), ["Fitch.logo.img"])
        self.assertEqual((self.logos / "Fitch.logo.img").read_bytes(), self.payload)
        self.assertEqual((self.logos / "Fitch.logo.img.ct").read_text(encoding="utf-8"),
                         "image/png")
        data = json.loads((self.logos / "Fitch.logo.img.provenance.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(data["variant"], MODULE.LOGO)

    def test_a_row_without_a_variant_still_installs_as_the_icon(self):
        """老复核件没有这一列。读到 `None` 就不装等于把上一批全废掉。"""
        self.row.pop("variant", None)
        self.assertEqual(MODULE.install([self.row], self.logos), ["Fitch.icon.img"])

    def test_the_provenance_records_the_verdict(self):
        """装上去的那一枚是方标还是补白字标，事后只能从这里看出来。"""
        MODULE.install([dict(self.row, verdict=MODULE.PADDED)], self.logos)
        data = json.loads((self.logos / "Fitch.icon.img.provenance.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(data["verdict"], MODULE.PADDED)


class StudioLinkTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            "CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT);"
            "CREATE TABLE entity_link(id INTEGER PRIMARY KEY, entity_id INTEGER,"
            " link_kind TEXT, label TEXT, url TEXT);")
        self.connection.executemany(
            "INSERT INTO entity(id, kind, canonical_name) VALUES(?,?,?)",
            [(1, "studio", "Idea Pocket"), (2, "studio", "Fitch"), (3, "performer", "某人"),
             (4, "studio", "FC2-PPV"), (5, "studio", "EST")])
        self.connection.executemany(
            "INSERT INTO entity_link(entity_id, link_kind, label, url) VALUES(?,?,?,?)",
            [(1, "official", "官方网站", "https://ideapocket.com/"),
             (1, "social", "X", "https://x.com/ideapocket"),
             (3, "official", "官方网站", "https://example.com/"),
             (4, "catalog", "FC2-PPV", "https://adult.contents.fc2.com/"),
             (5, "social", "X", "https://x.com/EST_prod")])

    def tearDown(self):
        self.connection.close()

    def test_the_official_link_pushes_the_social_one_aside(self):
        """有官网时社媒不进来：那 453 条绝大多数挂在艺人身上，取回来是运营的自拍。"""
        links = MODULE.studio_links(self.connection)
        self.assertEqual([item["url"] for item in links["Idea_Pocket"]],
                         ["https://ideapocket.com/"])
        self.assertNotIn("某人", links, "performer 的链接不该进厂牌图标线")

    def test_a_company_with_only_social_links_still_has_a_source(self):
        """刚拆分出来的事务所先有 X 账号、官网还没建，那个账号就是它唯一的门面。"""
        links = MODULE.studio_links(self.connection)
        self.assertEqual([item["url"] for item in links["EST"]],
                         ["https://x.com/EST_prod"])
        self.assertEqual(links["EST"][0]["link_kind"], MODULE.FALLBACK_LINK_KIND)

    def test_an_archived_official_site_is_not_an_icon_source(self):
        """存档快照上取到的是存档站的图标；只剩存档时照样轮到社媒顶上。"""
        self.connection.executemany(
            "INSERT INTO entity_link(entity_id, link_kind, label, url) VALUES(?,?,?,?)",
            [(2, "official", "官网存档（2015-01）",
              "https://web.archive.org/web/20150101000000/http://www.fitch-av.com/"),
             (2, "social", "X", "https://x.com/fitch_av")])
        links = MODULE.studio_links(self.connection)
        self.assertEqual([item["url"] for item in links["Fitch"]], ["https://x.com/fitch_av"])

    def test_a_company_left_with_only_an_archive_stays_on_the_list(self):
        """关门的公司官网只剩存档快照：链接不当图源，公司照样在名单里，指定字标按名字取。"""
        self.connection.execute(
            "INSERT INTO entity_link(entity_id, link_kind, label, url) VALUES(?,?,?,?)",
            (2, "official", "官网存档（2015-01）",
             "https://web.archive.org/web/20150101000000/http://www.fitch-av.com/"))
        self.assertEqual(MODULE.studio_links(self.connection)["Fitch"], [])

    def test_a_platform_keeps_its_catalog_link_as_an_icon_source(self):
        """发行平台按 `docs/SOURCING.md` 不登记 official——它不是厂牌，没有厂牌官网。

        可它照样占着筛选片和卡片徽标那几个位置。只认 official 的话，FC2-PPV 会在复核件上
        记成「无官网链接」，看起来像漏采，其实是查都没查。
        """
        links = MODULE.studio_links(self.connection)
        self.assertEqual(sorted(links), ["EST", "FC2-PPV", "Idea_Pocket"])
        self.assertEqual([item["url"] for item in links["FC2-PPV"]],
                         ["https://adult.contents.fc2.com/"])

    def test_the_key_is_the_installed_file_name(self):
        """归拢键必须是文件名，否则和 `padded_studios` 对不上。"""
        self.assertIn("Idea_Pocket", MODULE.studio_links(self.connection))


def a_face(score=0.934):
    return MODULE.face_detect.Face(cx=0.5, cy=0.4, width=0.3, height=0.3, score=score)


class SiteOwnAssetTests(unittest.TestCase):
    """站点声明的那一枚不合用时的两条后手：header 里的 `<img>`、页面上的 X 账号头像。

    样本按 bambi.ne.jp 2026-09-05 实测：只声明一枚 16×16 的 `favicon.ico`，header 那张
    是 187×57 的字标，而它 X 账号的头像是 119×119 的方标。
    """

    HOME = "https://bambi.ne.jp/"
    MARK = "https://bambi.ne.jp/images/_/header_logo.png"
    PROFILE = "https://x.com/BambiPromotion"
    AVATAR = "https://pbs.twimg.com/profile_images/1/7N42mRCO.jpg"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.candidates = Path(self.tmp.name).resolve() / "candidates"
        self.targets = {"Bambi_Promotion": {"original_size": "", "installed": ""}}
        self.links = {"Bambi_Promotion": [
            {"entity_id": 9001, "studio": "Bambi Promotion",
             "link_kind": "official", "url": self.HOME}]}

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self, **overrides):
        page = (f'<header><img src="images/_/header_logo.png" alt="logo"></header>'
                f'<a href="{self.PROFILE}">X</a>')
        profile = ('<meta property="og:image" content="https://pbs.twimg.com/'
                   'profile_images/1/7N42mRCO_400x400.jpg">')
        pages = {self.HOME: page.encode(), self.MARK: block_png((187, 57)),
                 self.PROFILE: profile.encode(), self.AVATAR: block_png((119, 119))}
        pages.update(overrides)
        return Fetch(pages)

    def harvest(self, fetch=None, faces=None, declared=None, targets=None, avatars=None):
        original = MODULE.site_icons.best_mark

        def stub(url, fetcher, policy, fallback=None, accept=None):
            fetcher(url)
            if declared is not None:
                return policy(declared)
            policy.reasons.append("只有 16x16")
            return None

        MODULE.site_icons.best_mark = stub
        try:
            return MODULE.harvest(self.targets if targets is None else targets,
                                  self.links, fetch or self.fetch(),
                                  self.candidates, faces, avatars)
        finally:
            MODULE.site_icons.best_mark = original

    def test_the_avatar_is_the_mark_when_the_site_only_declares_a_16px_icon(self):
        """声明的那一枚够不着，header 那张是字标，头像才是这家唯一的方标。"""
        icon = self.harvest()[0]
        self.assertEqual(icon["verdict"], MODULE.OK)
        self.assertEqual(icon["url"], self.AVATAR)
        self.assertIn("的头像", icon["evidence"])

    def test_the_site_mark_is_preferred_over_the_avatar(self):
        """站点自己那张更正式。同样合格时用它，头像只是后手。"""
        icon = self.harvest(fetch=self.fetch(**{self.MARK: block_png((256, 256))}))[0]
        self.assertEqual(icon["verdict"], MODULE.OK)
        self.assertEqual(icon["url"], self.MARK)
        self.assertIn("header", icon["evidence"])

    def test_the_avatar_also_fills_the_hero_slot_when_the_wordmark_is_too_small(self):
        """用户 2026-09-05 的口径：站点自己那几张都不清楚、头像反而清楚时，
        清楚的那张两个位置都顶。187×57 缩进 160 px 的大位是一条糊字。"""
        icon, logo = self.harvest()
        self.assertEqual((logo["variant"], logo["verdict"]), (MODULE.LOGO, MODULE.OK))
        self.assertEqual(logo["url"], icon["url"], "两个位置装的是同一枚")
        self.assertIn("与 icon 位同一枚方标", logo["evidence"])
        self.assertTrue(str(logo["candidate"]).endswith("Bambi_Promotion.logo.png"))

    def test_the_hero_still_looks_at_the_header_when_the_declared_icon_won(self):
        """SO MODEL AGENT 声明的 favicon 只有 114×114，header 里挂着 600×150 的完整字标。
        小位归声明那一枚，大位仍然该是那张字标——小位定了就不再翻首页的话，
        大位只能拿那枚 114 顶着。"""
        icon, logo = self.harvest(fetch=self.fetch(**{self.MARK: block_png((600, 150))}),
                                  declared=block_png((114, 114)))
        self.assertEqual((icon["evidence"], icon["mark_size"]), ("官网声明的图标", "114x114"))
        self.assertIn("站点自己的字标（600x150）", logo["evidence"])
        self.assertEqual(logo["url"], self.MARK)

    def test_a_big_enough_site_wordmark_takes_the_hero_slot(self):
        """大位要的是完整字标，宽扁是它应有的形状。够大就轮不到那枚方标。"""
        icon, logo = self.harvest(fetch=self.fetch(**{self.MARK: block_png((600, 200))}))
        self.assertEqual(icon["url"], self.AVATAR, "小位仍然是那枚方标")
        self.assertEqual(logo["verdict"], MODULE.OK)
        self.assertIn("站点自己的字标（600x200）", logo["evidence"])
        with Image.open(io.BytesIO(Path(str(logo["candidate"])).read_bytes())) as image:
            self.assertEqual(image.size[0], image.size[1], "装盘的一律是方图")

    def test_a_much_bigger_avatar_beats_the_declared_icon(self):
        """bambi 声明的是 119×119，它 Instagram 的头像是 1000×1000。

        「第一枚合格的就用」会把那张糊的装上去，而公司格是 180 px 宽、2 倍屏 360 实像素，
        119 缩放上去一眼就看得出。
        """
        icon = self.harvest(
            fetch=self.fetch(**{self.AVATAR: block_png((1000, 1000))}),
            declared=block_png((119, 119)))[0]
        self.assertEqual(icon["mark_size"], "1000x1000")
        self.assertIn("的头像", icon["evidence"])

    def test_a_marginally_bigger_avatar_does_not_displace_the_declared_icon(self):
        """119 和 114 谁大不该决定用谁的标：那点差别在页面上看不出来。"""
        icon = self.harvest(declared=block_png((114, 114)))[0]
        self.assertEqual((icon["evidence"], icon["mark_size"]),
                         ("官网声明的图标", "114x114"))

    def test_a_wider_candidate_never_takes_the_small_slot(self):
        """eltra.jp 实测：声明的是 300×300 纯皇冠，header 那张是 800×536 皇冠加字。

        短边 528 比 292 大出 1.8 倍，可小位是 28 px 的方框，1.50 比的那张装进去只剩
        一行认不出的字母；300 那枚本来就是照着这个尺寸画的。
        """
        icon = self.harvest(fetch=self.fetch(**{self.MARK: block_png((800, 536))}),
                            declared=block_png((300, 300)))[0]
        self.assertEqual((icon["evidence"], icon["mark_size"]),
                         ("官网声明的图标", "300x300"))

    def test_the_hero_slot_takes_the_one_the_small_slot_refused(self):
        """大位要的正是那张被小位嫌宽的完整标识，两位各挑各的。"""
        rows = self.harvest(fetch=self.fetch(**{self.MARK: block_png((800, 536))}),
                            declared=block_png((300, 300)))
        logo = [row for row in rows if row["variant"] == MODULE.LOGO][0]
        self.assertEqual(logo["verdict"], MODULE.OK)
        self.assertEqual(logo["url"], self.MARK)
        self.assertIn("这一趟取到的最大一枚", logo["evidence"])

    def test_a_big_enough_declared_icon_stops_the_walk(self):
        """够大就不再敲别人的门。多问一个来源就多一次请求，而结果不会更好。"""
        fetch = self.fetch()
        self.harvest(fetch=fetch, declared=block_png((512, 512)),
                     targets={"Bambi_Promotion": {"original_size": "187x57",
                                                  "installed": "Bambi_Promotion.img"}})
        self.assertNotIn(self.PROFILE, fetch.asked)
        self.assertNotIn(self.AVATAR, fetch.asked)

    def test_a_named_avatar_is_tried_before_the_x_account(self):
        """人指定的那一个是判断过的：`krone_official__` 是公司号，`miyu_krone` 是艺人号，
        形状上分不开，所以这一步不交给脚本猜。"""
        named = "https://scontent.cdninstagram.com/v/t51/bambi_1000.jpg"
        fetch = self.fetch(**{named: block_png((1000, 1000))})
        icon = self.harvest(fetch=fetch, avatars={"Bambi Promotion": named})[0]
        self.assertEqual(icon["url"], named)
        self.assertIn("人指定的社媒头像", icon["evidence"])

    def test_a_named_avatar_that_cannot_be_fetched_falls_back(self):
        """签名地址会过期。过期就是取不回来，不能拿别的图冒充它。"""
        icon = self.harvest(avatars={"Bambi Promotion": "https://scontent/expired.jpg"})[0]
        self.assertEqual(icon["url"], self.AVATAR)

    def test_an_installed_image_keeps_the_hero_slot(self):
        """`<safe>.img` 多半正是这家的完整字标，而 `.logo.img` 排在它前面。
        出这一行等于拿一枚 28 px 用的方标把大位上本来对的那张顶掉。"""
        targets = {"Bambi_Promotion": {"original_size": "187x57",
                                       "installed": "Bambi_Promotion.img"}}
        self.assertEqual([row["variant"] for row in self.harvest(targets=targets)],
                         [MODULE.ICON])

    def test_a_portrait_is_refused_even_when_the_site_declares_it(self):
        """ACT 的官网 `<link rel=icon>` 指着一张艺人照片（2026-09-05 实测）。
        够大、也够方，两道闸门都过，装上去事务所页顶着一个人的脸。"""
        rows = self.harvest(fetch=Fetch({self.HOME: b"<html></html>"}),
                            faces=lambda payload: a_face(),
                            declared=block_png((256, 256)))
        self.assertEqual([row["verdict"] for row in rows], [MODULE.PORTRAIT])
        self.assertEqual(rows[0]["candidate"], "")
        self.assertIn("检出一张脸（0.934）", rows[0]["evidence"])
        self.assertNotIn(MODULE.PORTRAIT, MODULE.INSTALLABLE)

    def test_the_gate_does_not_touch_a_source_the_user_named(self):
        """指定就是结论。用户指着的那一张不再拿模型去否决他。"""
        url = "https://logos.example/bambi.png"
        original = dict(MODULE.LOGO_SOURCES_BY_SAFE)
        MODULE.LOGO_SOURCES_BY_SAFE["Bambi_Promotion"] = url
        try:
            rows = self.harvest(fetch=self.fetch(**{url: block_png((600, 200))}),
                                faces=lambda payload: a_face())
        finally:
            MODULE.LOGO_SOURCES_BY_SAFE.clear()
            MODULE.LOGO_SOURCES_BY_SAFE.update(original)
        logo = [row for row in rows if row["variant"] == MODULE.LOGO][0]
        self.assertEqual((logo["verdict"], logo["url"]), (MODULE.OK, url))

    def test_a_share_button_is_not_asked_for_an_avatar(self):
        page = (f'<a href="https://x.com/intent/tweet?url={self.HOME}">分享</a>'
                f'<a href="{self.PROFILE}">X</a>')
        fetch = self.fetch(**{self.HOME: page.encode()})
        self.harvest(fetch=fetch)
        self.assertNotIn("intent/tweet", " ".join(fetch.asked))


class SocialOnlyCompanyTests(unittest.TestCase):
    """一条官网都没有的公司：那个 X 账号就是它的门面。

    样本按 x.com/EST_prod 2026-09-05 实测：LIGHT 拆出来的 EST 还没建站，账本里只登记
    得到一条 `social`，而它的头像原图是 1134×1129——这里用同样形状的小样本，
    `block_png` 是逐像素画的。
    """

    PROFILE = "https://x.com/EST_prod"
    AVATAR = "https://pbs.twimg.com/profile_images/2094369416905650176/GyqWkfql.jpg"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.candidates = Path(self.tmp.name).resolve() / "candidates"
        self.targets = {"EST": {"original_size": "", "installed": ""}}
        self.links = {"EST": [{"entity_id": 8710, "studio": "EST",
                               "link_kind": "social", "url": self.PROFILE}]}

    def fetch(self, **overrides):
        profile = (f'<meta property="og:image" content="'
                   f'{self.AVATAR[:-4]}_400x400.jpg">')
        pages = {self.PROFILE: profile.encode(), self.AVATAR: block_png((404, 402))}
        pages.update(overrides)
        return Fetch(pages)

    def harvest(self, fetch=None):
        original = MODULE.site_icons.best_mark

        def refuse(url, fetcher, policy, fallback=None, accept=None):
            raise AssertionError("账号主页没有「站点声明的图标」可问")

        MODULE.site_icons.best_mark = refuse
        try:
            return MODULE.harvest(self.targets, self.links,
                                  fetch or self.fetch(), self.candidates)
        finally:
            MODULE.site_icons.best_mark = original

    def test_the_registered_account_avatar_becomes_the_mark(self):
        """没有官网不等于没有标识，之前它只会停在「无官网链接」。"""
        icon = self.harvest()[0]
        self.assertEqual(icon["verdict"], MODULE.OK)
        self.assertEqual(icon["url"], self.AVATAR)
        self.assertEqual(icon["evidence"], "数据库里登记的社媒头像")

    def test_the_same_avatar_fills_the_hero_slot(self):
        """大位没有别的候选可挑，站点自己那张字标根本不存在。"""
        logo = [row for row in self.harvest() if row["variant"] == MODULE.LOGO][0]
        self.assertEqual(logo["verdict"], MODULE.OK)
        self.assertTrue(logo["candidate"].endswith(".logo.png"))

    def test_only_the_profile_and_the_avatar_are_asked_for(self):
        """账号主页上没有 header，也没有别人的账号可跟。"""
        fetch = self.fetch()
        self.harvest(fetch=fetch)
        self.assertEqual(fetch.asked, [self.PROFILE, self.AVATAR])

    def test_an_unreachable_account_is_未取得_not_a_conclusion(self):
        """账号打不开时下一步是换个时间再试，不是断定这家没有标识。"""
        icon = self.harvest(fetch=Fetch(reachable=False))[0]
        self.assertEqual(icon["verdict"], MODULE.MISSING)
        self.assertIn(self.PROFILE, icon["evidence"])


class NamedAvatarTests(unittest.TestCase):
    """人在登录态解出来的头像地址，按账本名读进来。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name).resolve() / "agency-avatars.json"

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")
        return MODULE.named_avatars(self.path)

    def test_a_missing_file_is_simply_empty(self):
        """这一份是可选的补充。没有它整轮照跑，不该报错也不该停。"""
        self.assertEqual(MODULE.named_avatars(self.path), {})

    def test_the_url_may_carry_a_note_beside_it(self):
        """地址是签名过的一长串，边上要能写清它是谁的号、什么时候解的。"""
        found = self.write('{"Bambi Promotion": {"url": "https://cdn/x.jpg",'
                           ' "note": "instagram @bambi.hajimero 1000x1000"}}')
        self.assertEqual(found, {"Bambi Promotion": "https://cdn/x.jpg"})

    def test_a_bare_string_is_accepted_too(self):
        self.assertEqual(self.write('{"LINX": "https://cdn/linx.jpg"}'),
                         {"LINX": "https://cdn/linx.jpg"})

    def test_junk_entries_are_dropped_instead_of_failing_the_run(self):
        found = self.write('{"A": {"note": "还没解"}, "B": "", "C": "https://cdn/c.jpg"}')
        self.assertEqual(found, {"C": "https://cdn/c.jpg"})

    def test_a_broken_file_does_not_take_the_harvest_down_with_it(self):
        self.assertEqual(self.write("{不是 JSON"), {})


class FetcherCacheTests(unittest.TestCase):
    """同一份首页这一趟要被问三次：声明、header 里的 `<img>`、页面上的账号。"""

    class Client:
        def __init__(self, status=200):
            self.status = status
            self.calls: list[str] = []

        def stream(self, method, url, **kwargs):
            from contextlib import contextmanager
            @contextmanager
            def response():
                self.calls.append(url)
                result = httpx.Response(self.status, content=b"<html></html>",
                                        headers={"content-type": "text/html"},
                                        request=httpx.Request(method, url))
                yield result
                result.close()
            return response()

    def test_the_same_page_is_only_fetched_once(self):
        client = self.Client()
        fetch = MODULE.Fetcher(client, timeout=1.0, interval=0.0)
        self.assertEqual(fetch("https://x.jp/"), fetch("https://x.jp/"))
        self.assertEqual(client.calls, ["https://x.jp/"])
        self.assertEqual(fetch.fetched, 2, "缓存命中也算取到了：这个站是可达的")

    def test_a_refusal_is_remembered_too(self):
        """404 问三遍还是 404，可每问一遍都要等一个 interval。"""
        client = self.Client(status=404)
        fetch = MODULE.Fetcher(client, timeout=1.0, interval=0.0)
        self.assertIsNone(fetch("https://x.jp/"))
        self.assertIsNone(fetch("https://x.jp/"))
        self.assertEqual(client.calls, ["https://x.jp/"])
        self.assertEqual(fetch.fetched, 0)


class FaceGateTests(unittest.TestCase):
    """模型这一层的降级行为。检不了不能等于「都不是照片」。"""

    def setUp(self):
        self.detector = MODULE.face_detect.FaceDetector
        self.detect = MODULE.face_detect.shows_a_face

    def tearDown(self):
        MODULE.face_detect.FaceDetector = self.detector
        MODULE.face_detect.shows_a_face = self.detect

    def test_a_disabled_gate_never_builds_a_model(self):
        MODULE.face_detect.FaceDetector = lambda: self.fail("不该构造模型")
        gate = MODULE.FaceGate(enabled=False)
        self.assertIsNone(gate(png_bytes()))
        self.assertEqual(gate.rejected, 0)

    def test_a_missing_model_disables_the_gate_and_says_so(self):
        calls = []

        def refuse():
            calls.append(1)
            raise MODULE.face_detect.FaceModelUnavailable("缺少人脸模型：yunet.onnx")

        MODULE.face_detect.FaceDetector = refuse
        gate = MODULE.FaceGate()
        self.assertIsNone(gate(png_bytes()))
        self.assertIsNone(gate(png_bytes()))
        self.assertIn("缺少人脸模型", gate.unavailable)
        self.assertEqual(calls, [1], "取不到就别每张图再试一遍")

    def test_a_detected_face_is_counted_for_the_report(self):
        MODULE.face_detect.FaceDetector = lambda: object()
        MODULE.face_detect.shows_a_face = lambda payload, detector: a_face()
        gate = MODULE.FaceGate()
        self.assertEqual(gate(png_bytes()).score, 0.934)
        self.assertEqual((gate.rejected, gate.unavailable), (1, ""))


if __name__ == "__main__":
    unittest.main()
