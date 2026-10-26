# -*- coding: utf-8 -*-
"""横版封套里正封取景框的判据。

图都是 Pillow 当场合成的，不依赖真实封面：折痕是一条画上去的竖线，位置已知，
所以「找到的那一列对不对」有确定答案可比。
"""
from __future__ import annotations

import functools
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from PIL import Image

from peach import images, jav_poster_crop
from peach.jav_poster_crop import CENTER, FOLD, MANUAL, MANUAL_SOURCE, NONE, RATIO


class CompressedSpineTests(unittest.TestCase):
    def test_a_face_left_of_the_cut_keeps_its_right_side(self):
        face = {'cx': .5, 'cy': .2, 'w': .1, 'h': .2, 'score': .9}
        box = jav_poster_crop.crop_record('STARS-350', 840, 563, [0.] * 840, face=face)['box']
        self.assertEqual(box, {'x0': 0, 'y0': 0, 'x1': 840, 'y1': 563, 'method': NONE})

    def test_blurred_padding_around_a_centered_front_is_excluded(self):
        profile = jav_poster_crop.ColumnProfile([.02] * 224 + [.2] * 394 + [.02] * 222, [0.] * 840)
        profile.seams[224] = profile.seams[618] = .95
        face = {'cx': .487, 'cy': .231, 'w': .135, 'h': .242, 'score': .93}
        box = jav_poster_crop.crop_record('HHL-018', 840, 563, profile, face=face)['box']
        self.assertEqual(box, {'x0': 225, 'y0': 0, 'x1': 618, 'y1': 563, 'method': CENTER})

    def test_busy_side_pictures_do_not_form_blurred_padding(self):
        profile = jav_poster_crop.ColumnProfile([.2] * 840, [0.] * 840)
        profile.seams[224] = profile.seams[618] = .95
        self.assertIsNone(jav_poster_crop.padded_center_panel(840, 563, profile))

    def test_both_boundaries_are_required_for_blurred_padding(self):
        profile = jav_poster_crop.ColumnProfile([.02] * 224 + [.2] * 394 + [.02] * 222, [0.] * 840)
        profile.seams[224] = .95
        self.assertIsNone(jav_poster_crop.padded_center_panel(840, 563, profile))

    def test_soft_spine_edge_is_separated_from_the_lettering(self):
        record = json.loads((Path(__file__).parent / 'fixtures/cover-profiles/ipzz-477.json').read_text(encoding='utf-8'))
        profile = jav_poster_crop.ColumnProfile(record['gradient'], record['seams'])
        box = jav_poster_crop.front_panel_box(*record['px'], profile, code='IPZZ-477')
        self.assertEqual(box['method'], FOLD)
        self.assertGreaterEqual(box['x0'], 422)
        self.assertLessEqual(box['x0'], 423)


def sleeve(width: int, height: int, fold: int | None) -> Image.Image:
    """一张封套：左边是背面，右边是正面，中间可选一条书脊折痕。"""
    image = Image.new("RGB", (width, height), (200, 200, 200))
    if fold is None:
        return image
    for x in range(fold, min(fold + 3, width)):
        for y in range(height):
            image.putpixel((x, y), (0, 0, 0))
    for x in range(min(fold + 3, width), width):
        for y in range(height):
            image.putpixel((x, y), (150, 150, 150))
    return image


def centred(width: int, height: int, x0: int, x1: int,
            right: tuple[int, int, int] = (60, 60, 60)) -> Image.Image:
    """16:9 拼图：左右两块剧照，正中 `x0`～`x1` 是正封。"""
    image = Image.new("RGB", (width, height), (90, 90, 90))
    image.paste((200, 200, 200), (x0, 0, x1, height))
    image.paste(right, (x1, 0, width, height))
    return image


def edge_left(width: int, height: int) -> Image.Image:
    """折痕带之外有一条强边，带内平整：这张图没有可采信的折痕。"""
    image = Image.new("RGB", (width, height), (200, 200, 200))
    for x in range(0, int(width * 0.1)):
        for y in range(height):
            image.putpixel((x, y), (10, 10, 10))
    return image


def gradient_of(image: Image.Image):
    import cv2
    import numpy

    array = cv2.cvtColor(numpy.array(image), cv2.COLOR_RGB2BGR)
    return jav_poster_crop.column_gradient(array)


def opencv_available() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        return False
    return True


@functools.lru_cache(maxsize=3)
def batch_script(name='poster_crop_boxes'):
    """按文件路径载入封面维护脚本。"""
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        name, root / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CropShapeTests(unittest.TestCase):
    """框的形状：从折痕一路到源图的右下角，满高，中间不另取子区域。"""

    def assert_reaches_the_corner(self, box: dict, width: int, height: int):
        self.assertEqual((box["x1"], box["y0"], box["y1"]), (width, 0, height),
                         f"框不是「折痕到右缘、满高」：{box}")
        self.assertGreater(box["x1"], box["x0"])
        self.assertGreaterEqual(box["x0"], 0)

    def test_the_box_runs_from_the_fold_to_the_bottom_right_corner(self):
        for width, height in ((800, 539), (2184, 1464), (1000, 674), (760, 600)):
            with self.subTest(size=(width, height)):
                box = jav_poster_crop.front_panel_box(width, height)
                self.assertEqual(box["method"], RATIO)
                self.assert_reaches_the_corner(box, width, height)

    def test_the_prior_puts_the_fold_where_the_front_panel_has_its_real_shape(self):
        """没有梯度可用时，正封的形状就是先验本身——按高度从右缘量回去。"""
        for width, height in ((800, 539), (2184, 1464)):
            with self.subTest(size=(width, height)):
                box = jav_poster_crop.front_panel_box(width, height, [0.0] * width)
                self.assertEqual(box["method"], RATIO)
                self.assertAlmostEqual((box["x1"] - box["x0"]) / height,
                                       jav_poster_crop.PANEL_ASPECT, places=2)

    def test_a_degenerate_size_gives_an_empty_box_and_no_method(self):
        self.assertEqual(jav_poster_crop.front_panel_box(0, 0),
                         {"x0": 0, "y0": 0, "x1": 0, "y1": 0, "method": NONE})


class MethodTests(unittest.TestCase):
    """各档各自的触发条件。"""

    @unittest.skipUnless(opencv_available(), "缺 vision 依赖组")
    def test_a_visible_spine_is_found_and_the_front_starts_there(self):
        width, height, fold = 800, 540, 420
        profile = gradient_of(sleeve(width, height, fold))
        self.assertIsNotNone(profile)
        found = jav_poster_crop.fold_column(width, height, profile)
        self.assertIsNotNone(found, "画上去的折痕没被找到")
        # 那条三列宽的黑线整条留在框外，正封从它右边开始。
        self.assertGreaterEqual(found, fold + 3, found)
        self.assertLessEqual(found - (fold + 3), 2, found)
        box = jav_poster_crop.front_panel_box(width, height, profile)
        self.assertEqual(box["method"], FOLD)
        self.assertEqual((box["x0"], box["x1"], box["y0"], box["y1"]),
                         (found, width, 0, height))

    @unittest.skipUnless(opencv_available(), "缺 vision 依赖组")
    def test_without_a_spine_the_front_falls_back_to_the_prior_shape(self):
        width, height = 800, 540
        profile = gradient_of(edge_left(width, height))
        self.assertIsNone(jav_poster_crop.fold_column(width, height, profile))
        box = jav_poster_crop.front_panel_box(width, height, profile)
        self.assertEqual(box["method"], RATIO)
        self.assertEqual(box["x0"],
                         round(width - jav_poster_crop.PANEL_ASPECT * height))

    def test_a_missing_gradient_falls_back_to_the_prior_shape(self):
        """OpenCV 不在时取景仍然给得出框，只是退化成先验几何。"""
        box = jav_poster_crop.front_panel_box(800, 540, None)
        self.assertEqual(box["method"], RATIO)

    def test_a_portrait_or_square_image_is_not_cropped(self):
        for width, height in ((600, 900), (800, 800), (900, 800)):
            with self.subTest(size=(width, height)):
                box = jav_poster_crop.front_panel_box(width, height)
                self.assertEqual(box, {"x0": 0, "y0": 0, "x1": width, "y1": height,
                                       "method": NONE})

    def test_a_sixteen_by_nine_still_is_not_cropped(self):
        """整幅都是画面的官方剧照没有「正面那一块」，按先验切会裁出半张背景。"""
        box = jav_poster_crop.front_panel_box(1920, 1080)
        self.assertEqual(box["method"], NONE)

    @unittest.skipUnless(opencv_available(), "缺 vision 依赖组")
    def test_a_centred_front_panel_in_a_still_is_framed_between_its_seams(self):
        """「剧照 | 正封 | 剧照」的 16:9 拼图：框在两条满高拼接缝之间，满高。"""
        width, height = 1348, 758
        box = jav_poster_crop.front_panel_box(
            width, height, gradient_of(centred(width, height, 408, 940)))
        self.assertEqual(box["method"], CENTER)
        self.assertEqual((box["y0"], box["y1"]), (0, height))
        self.assertLessEqual(abs(box["x0"] - 408), 1, box)
        self.assertLessEqual(abs(box["x1"] - 940), 1, box)

    @unittest.skipUnless(opencv_available(), "缺 vision 依赖组")
    def test_one_full_height_seam_is_enough_and_the_other_side_mirrors_it(self):
        """一侧剧照压暗后和正封边缘同色，那一侧没有缝；居中是版式本身，按对称补上。"""
        width, height = 1348, 758
        box = jav_poster_crop.front_panel_box(
            width, height, gradient_of(centred(width, height, 408, 940, right=(200, 200, 200))))
        self.assertEqual(box["method"], CENTER)
        self.assertEqual(box["x1"], width - box["x0"])

    @unittest.skipUnless(opencv_available(), "缺 vision 依赖组")
    def test_a_strong_edge_that_stops_short_is_content_not_a_seam(self):
        """边位上的强边只占半截高度：那是画面里的人或物，不是拼接缝。"""
        width, height = 1348, 758
        image = Image.new("RGB", (width, height), (200, 200, 200))
        image.paste((20, 20, 20), (0, 0, 408, height // 2))
        box = jav_poster_crop.front_panel_box(width, height, gradient_of(image))
        self.assertEqual(box["method"], NONE)

    def test_a_still_without_seam_evidence_is_not_cropped(self):
        """只给列梯度、没有接缝覆盖率的调用方，16:9 那一档照旧不给框。"""
        profile = [0.0] * 1348
        profile[408] = 1.0
        self.assertEqual(jav_poster_crop.front_panel_box(1348, 758, profile)["method"], NONE)

    def test_the_window_peak_must_be_a_real_cliff(self):
        """窗里总有一个最大值，它不够陡就只是噪声的最高点，按它切会切进画面。"""
        profile = [0.0] * 800
        profile[80] = 1.0
        profile[425] = 0.1
        self.assertIsNone(jav_poster_crop.fold_column(800, 540, profile))
        profile[425] = 0.9
        self.assertIsNotNone(jav_poster_crop.fold_column(800, 540, profile))

    def test_a_stronger_edge_outside_the_window_never_wins(self):
        """窗外那道边正是误判的来路：画面里的强边、书脊的另一条边都在窗外。"""
        profile = [0.0] * 800
        profile[330] = 1.0                      # 正封会连着整条书脊
        profile[470] = 1.0                      # 切进正封，大标题被削掉一截
        self.assertIsNone(jav_poster_crop.fold_column(800, 539, profile))
        profile[418] = 0.5                      # 一列宽的峭壁，斜坡下一列就落回基线
        self.assertEqual(jav_poster_crop.fold_column(800, 539, profile), 419)

    def test_the_window_is_measured_off_the_height_not_the_width(self):
        """同一个正封形状在低清和高清封套上都要认得出，尽管像素位置差着上千。"""
        for width, height in ((800, 539), (2184, 1464)):
            with self.subTest(size=(width, height)):
                fold = round(width - 0.71 * height)
                profile = [0.0] * width
                profile[fold] = 1.0
                self.assertEqual(jav_poster_crop.fold_column(width, height, profile),
                                 fold + 1)

    def test_the_cut_lands_past_the_slope_not_in_the_middle_of_it(self):
        """折痕是一道有宽度的斜坡，梯度的峰在最陡处，书脊最后几列还在它右边。"""
        profile = [0.3] * 800                   # 画面忙的封套，处处都有梯度响应
        for column, value in zip(range(417, 422), (0.35, 0.48, 0.39, 0.44, 0.24)):
            profile[column] = value
        self.assertEqual(jav_poster_crop.fold_column(800, 539, profile), 421)

    def test_the_walk_past_the_slope_is_bounded(self):
        """梯度一路不落回基线时不能一直走下去，最多走源图宽的 1%。"""
        profile = [0.0] * 800
        profile[418] = 1.0
        for column in range(419, 500):
            profile[column] = 0.5               # 斜坡右边一直不落回基线
        self.assertEqual(jav_poster_crop.fold_column(800, 539, profile), 426)

    @staticmethod
    def banded_fold(closing_edge: int) -> list[float]:
        """ABF-328 的折痕照着量出来的梯度：书脊边、七列灰色阴影、另一道边才到正封。"""
        profile = [0.3] * 2184
        profile[1139], profile[1140] = 0.73, 0.56
        for column in range(1141, 1147):
            profile[column] = 0.2
        profile[closing_edge - 1], profile[closing_edge] = 0.56, 0.59
        profile[closing_edge + 1] = 0.06
        return profile

    def test_a_shadow_band_beside_the_fold_stays_outside_the_front(self):
        """两道边之间梯度短暂落回基线；停在那儿，卡片左缘就留下一道灰线。"""
        self.assertEqual(jav_poster_crop.fold_column(2184, 1477, self.banded_fold(1148)), 1149)

    def test_an_edge_well_inside_the_front_is_content_not_the_band(self):
        """离折痕超过一道边的宽度，那是正封里的标题字或人物边缘，不是阴影带的另一侧。"""
        self.assertEqual(jav_poster_crop.fold_column(2184, 1477, self.banded_fold(1160)), 1141)

    def test_a_busy_front_beside_a_weak_fold_is_not_mistaken_for_the_band(self):
        """折痕本身偏弱时，正封里一片花哨的画面处处都和它相当，那些边都不够峭壁。"""
        profile = [0.1] * 2184
        profile[100] = 1.0                      # 窗外画面里最强的那道边
        profile[1139] = 0.4
        for column in range(1141, 1150):
            profile[column] = 0.3 if column % 2 else 0.32
        self.assertEqual(jav_poster_crop.fold_column(2184, 1477, profile), 1140)

    def test_a_thick_spine_puts_both_of_its_edges_in_the_window(self):
        """书脊厚到两条边都落进窗里时按形状挑：左边那条更强，右边那条才是折痕。"""
        profile = [0.0] * 3762
        profile[1939] = 0.57                    # 书脊左缘，挨着封底的留白
        profile[1978] = 0.46                    # 书脊右缘，正封从这里开始
        self.assertEqual(jav_poster_crop.fold_column(3762, 2535, profile), 1979)

    def test_an_edge_too_weak_to_be_a_rival_never_gets_to_vote(self):
        """形状更像正封不足以让一条弱边胜出：强度差太多的根本不进候选。"""
        profile = [0.0] * 3762
        profile[1939] = 0.57
        profile[1978] = 0.20                    # 不到窗内最强边的七成
        self.assertEqual(jav_poster_crop.fold_column(3762, 2535, profile), 1940)

    def test_a_profile_that_does_not_match_the_image_is_ignored(self):
        self.assertIsNone(jav_poster_crop.fold_column(800, 540, [1.0] * 400))


class SeamBeyondTests(unittest.TestCase):
    """厚书脊里的文字边压过了折痕时，切点右边那条满高缝才是折痕。

    ABW-147 的形状：3762×2535，书脊 1922～1978 列，窗内最强的边是书脊文字的 1939，
    书脊右缘 1978 的梯度不到它的七成、进不了候选，可它从上贯到下，文字边不满高。
    """

    WIDTH, HEIGHT = 3762, 2535
    TEXT_EDGE, SPINE_EDGE = 1939, 1978

    def profile(self, seam_at_text: float = 0.36, spine_edge: int = SPINE_EDGE,
                spine_strength: float = 0.66, spine_seam: float = 0.91):
        gradient = [0.0] * self.WIDTH
        seams = [0.0] * self.WIDTH
        gradient[self.TEXT_EDGE], seams[self.TEXT_EDGE] = 0.99, seam_at_text
        gradient[spine_edge], seams[spine_edge] = spine_strength, spine_seam
        return jav_poster_crop.ColumnProfile(gradient, seams)

    def test_a_full_height_seam_right_of_a_cut_that_is_not_one_becomes_the_fold(self):
        self.assertEqual(jav_poster_crop.fold_column(self.WIDTH, self.HEIGHT, self.profile()),
                         self.SPINE_EDGE + 1)

    def test_a_cut_that_is_itself_a_full_height_seam_stays_where_it_is(self):
        """正封里紧挨折痕的边框、色带同样满高；折痕已经满高就没有理由再往右走。"""
        self.assertEqual(jav_poster_crop.fold_column(self.WIDTH, self.HEIGHT,
                                                     self.profile(seam_at_text=0.9)),
                         self.TEXT_EDGE + 1)

    def test_a_seam_outside_the_window_is_content_not_the_fold(self):
        """窗外的满高缝切出来的正封形状不对，那是正封里的一条竖线。"""
        self.assertEqual(jav_poster_crop.fold_column(self.WIDTH, self.HEIGHT,
                                                     self.profile(spine_edge=2100)),
                         self.TEXT_EDGE + 1)

    def test_a_seam_left_of_the_cut_is_never_looked_at(self):
        """书脊左缘也是满高缝，往左看会把整条书脊带回框里。"""
        self.assertEqual(jav_poster_crop.fold_column(self.WIDTH, self.HEIGHT,
                                                     self.profile(spine_edge=1900, spine_strength=0.5)),
                         self.TEXT_EDGE + 1)

    def test_a_faint_seam_that_is_no_cliff_does_not_pull_the_cut(self):
        """淡淡的印刷分隔线能满高，但过不了峭壁那一关。"""
        self.assertEqual(jav_poster_crop.fold_column(self.WIDTH, self.HEIGHT,
                                                     self.profile(spine_strength=0.2)),
                         self.TEXT_EDGE + 1)

    def test_a_bare_gradient_without_seams_behaves_as_before(self):
        self.assertEqual(jav_poster_crop.fold_column(self.WIDTH, self.HEIGHT, list(self.profile())),
                         self.TEXT_EDGE + 1)

    @unittest.skipUnless(opencv_available(), "需要 OpenCV")
    def test_a_lettered_spine_is_cut_at_its_far_edge_from_the_image_itself(self):
        """从画面量：书脊底色上一列竖排文字，文字边和书脊右缘都在窗里，正封从右缘起。"""
        width, height, spine, edge = 800, 538, 400, 430
        image = Image.new("RGB", (width, height), (40, 40, 40))    # 封底和书脊一样暗，左缘弱
        image.paste((20, 20, 20), (spine, 0, edge, height))
        image.paste((120, 120, 120), (edge, 0, width, height))
        for y in range(0, height, 6):                # 书脊里的白色文字：窗内最强、却不满高的边
            image.paste((255, 255, 255), (spine + 12, y, spine + 15, min(y + 4, height)))
        box = jav_poster_crop.front_panel_box(width, height, gradient_of(image))
        self.assertEqual(box["method"], FOLD)
        self.assertGreaterEqual(box["x0"], edge)
        self.assertLessEqual(box["x0"], edge + 3)


class SharpFoldTests(unittest.TestCase):
    """书脊里竖排片名是一片宽的高地，折痕是一条窄线：按突出度折痕才是窗里最显眼的那条。

    DDK-023 的形状：800×539，折痕窗 390～433，书脊片名的高地 391～408 最高 0.50，
    折痕 419 列只有 0.34（不到高地七成），满高覆盖 0.68（不到 `_seam_beyond` 的 0.75）。
    """

    WIDTH, HEIGHT, FOLD_AT = 800, 539, 419

    def profile(self, fold_seam: float = 0.68):
        gradient = [0.1] * self.WIDTH
        seams = [0.3] * self.WIDTH
        gradient[3] = 1.0                                   # 封底左缘，全图最强
        for column in range(391, 409):                      # 片名那片高地
            gradient[column] = 0.5 - abs(column - 401) * 0.01
        gradient[self.FOLD_AT], gradient[self.FOLD_AT + 1] = 0.34, 0.28
        seams[self.FOLD_AT] = fold_seam
        return jav_poster_crop.ColumnProfile(gradient, seams)

    def test_a_narrow_fold_beside_a_lettered_plateau_is_the_fold(self):
        self.assertEqual(jav_poster_crop.fold_column(self.WIDTH, self.HEIGHT, self.profile()),
                         self.FOLD_AT + 2)

    def test_a_narrow_edge_that_is_not_full_height_is_front_lettering(self):
        """正封里紧挨折痕的大字也是窄尖峰，但只占几行，不进候选。"""
        self.assertLess(jav_poster_crop.fold_column(self.WIDTH, self.HEIGHT,
                                                    self.profile(fold_seam=0.3)),
                        self.FOLD_AT)


class SharpBeyondTests(unittest.TestCase):
    """书脊和正封同底色时折痕只在书脊内容所在的行里存在：切点右边那根孤立尖峰才是折痕。

    IPZ-602 的形状：800×536，折痕窗 393～436，书脊内一块带边框的面板与竖排文字形成
    390～409 的高地（最高 0.51，满高覆盖 0.5 上下），按形状选中高地右端、切在 410；
    折痕 420 列梯度 0.24、满高覆盖 0.20，左边面板内部 0.06～0.11、右边正封留白 0.02～0.04，
    周围 16 列中位数 0.065，折痕是它的 3.7 倍。合成剖面左侧取 0.07、右侧 0.02（3.4 倍）。
    """

    WIDTH, HEIGHT, FOLD_AT = 800, 536, 420

    def profile(self, left: float = 0.07, right: float = 0.02, panel_seam: float = 0.5,
                seams: bool = True):
        gradient = [0.05] * self.WIDTH
        coverage = [0.1] * self.WIDTH
        gradient[3] = 1.0                                   # 封底左缘，全图最强
        for column in range(390, 410):                      # 面板边框与文字那片高地
            gradient[column] = 0.4
            coverage[column] = panel_seam
        gradient[396], gradient[404] = 0.51, 0.45
        for column in range(410, self.FOLD_AT):             # 面板内部到折痕
            gradient[column] = left
        gradient[self.FOLD_AT], gradient[self.FOLD_AT + 1] = 0.24, 0.14
        coverage[self.FOLD_AT] = 0.2
        for column in range(self.FOLD_AT + 2, 440):         # 正封左缘
            gradient[column] = right
        return jav_poster_crop.ColumnProfile(gradient, coverage) if seams else gradient

    def test_an_isolated_edge_past_a_bordered_panel_is_the_fold(self):
        self.assertEqual(jav_poster_crop.fold_column(self.WIDTH, self.HEIGHT, self.profile()),
                         self.FOLD_AT + 2)

    def test_an_edge_that_rises_into_front_content_is_the_start_of_lettering(self):
        """左边留白、右边忙：那是正封内容的起笔，切点已经在折痕上（PPT-018 的金色片名）。

        右侧取 0.12 而不是更高：周围中位数是左右两侧合起来算的，右侧再高，尖峰就先在
        孤立度上被拦下，方向判据反而没被测到。
        """
        self.assertEqual(jav_poster_crop.fold_column(self.WIDTH, self.HEIGHT,
                                                     self.profile(left=0.02, right=0.12)),
                         410)

    def test_an_edge_among_other_strokes_is_not_isolated(self):
        """周围中位数压不下去的尖峰是大字里的一笔，哪怕左边比右边忙（MIMK-009 的 423 列）。"""
        self.assertEqual(jav_poster_crop.fold_column(self.WIDTH, self.HEIGHT,
                                                     self.profile(left=0.12, right=0.09)),
                         410)

    def test_a_cut_that_is_already_a_full_height_seam_stays(self):
        """高地本身满高就是折痕，右边再孤立的尖峰也是正封内容。"""
        self.assertEqual(jav_poster_crop.fold_column(self.WIDTH, self.HEIGHT,
                                                     self.profile(panel_seam=0.8)),
                         410)

    def test_a_bare_gradient_without_seams_behaves_as_before(self):
        self.assertEqual(jav_poster_crop.fold_column(self.WIDTH, self.HEIGHT,
                                                     self.profile(seams=False)),
                         410)


class CodeShapeTests(unittest.TestCase):
    """哪些番号的封面是横版封套。判据全部走 `catalog_rules` 现有函数。"""

    def test_studio_and_amateur_codes_are_sleeves(self):
        for code in ("ABW-232", "abw-232", "278GYAN-017", "259LUXU-1475"):
            with self.subTest(code=code):
                self.assertTrue(jav_poster_crop.crops_to_portrait(code))

    def test_finished_landscape_artwork_is_not_cropped(self):
        for code in ("HEYZO-1380", "FC2-PPV-1812235", "FC2-1812235",
                     "040221-001", "092620-001"):
            with self.subTest(code=code):
                self.assertFalse(jav_poster_crop.crops_to_portrait(code))

    def test_non_jav_and_korean_codes_are_not_cropped(self):
        for code in ("", None, "20211103_JENNIFERMENDEZ", "HHD800",
                     "AR-101", "NOAH-101"):
            with self.subTest(code=code):
                self.assertFalse(jav_poster_crop.crops_to_portrait(code))

    def test_a_western_release_is_stopped_by_the_shape_of_its_artwork(self):
        """欧美片的编号和厂牌番号同形，番号这一关拦不住它，宽高比这一关拦得住。

        它们的封面是成品横版剧照，落在 16:9 一档，所以走的是 `none`。
        """
        self.assertTrue(jav_poster_crop.crops_to_portrait("BLACKED-2021"))
        self.assertEqual(
            jav_poster_crop.crop_record("BLACKED-2021", 1920, 1080)["box"]["method"],
            NONE)


class WideSleeveTests(unittest.TestCase):
    """Blu-ray 模板的宽封套：厂牌开门，比例决定走不走，正封形状另有一套先验。"""

    WIDE = (750, 419)

    def test_only_the_listed_labels_open_the_door(self):
        for code in ("CWPBD-126", "cwpbd-119", "SMBD-110"):
            with self.subTest(code=code):
                self.assertTrue(jav_poster_crop.is_wide_sleeve_label(code))
        for code in ("IDBD-446", "PBD-390", "ABW-232", "259LUXU-1475", "", None):
            with self.subTest(code=code):
                self.assertFalse(jav_poster_crop.is_wide_sleeve_label(code))

    def test_a_wide_sleeve_of_a_listed_label_is_cut_at_its_own_prior(self):
        """没有梯度时按宽封套的先验从右缘量回去，不是 DVD 那一档的 0.704。"""
        width, height = self.WIDE
        box = jav_poster_crop.front_panel_box(width, height, None, "CWPBD-126")
        self.assertEqual(box["method"], RATIO)
        self.assertEqual(box["x0"], round(width - jav_poster_crop.WIDE_PANEL_ASPECT * height))
        self.assertEqual((box["x1"], box["y0"], box["y1"]), (width, 0, height))

    def test_the_same_image_without_the_label_is_still_a_still(self):
        """比例一样的图，厂牌不在名单里就照旧当 16:9 剧照：这一档全靠厂牌开门。"""
        width, height = self.WIDE
        for code in ("ABW-232", None):
            with self.subTest(code=code):
                box = jav_poster_crop.front_panel_box(width, height, None, code)
                self.assertEqual(box["method"], NONE)

    def test_a_listed_label_with_a_dvd_shaped_cover_walks_the_dvd_path(self):
        """厂牌只开门，不定切法：同一厂牌哪一期换成 DVD 比例的封套，仍按那一档的形状切；
        比例超出宽封套区间的仍是剧照。"""
        record = jav_poster_crop.crop_record("CWPBD-126", 800, 540, [0.0] * 800)
        self.assertEqual(record["box"]["method"], RATIO)
        self.assertEqual(record["box"]["x0"],
                         round(800 - jav_poster_crop.PANEL_ASPECT * 540))
        self.assertEqual(jav_poster_crop.crop_record("SMBD-110", 1900, 1000)["box"]["method"],
                         NONE)

    @unittest.skipUnless(opencv_available(), "缺 vision 依赖组")
    def test_a_spine_inside_the_narrow_window_is_found(self):
        width, height = self.WIDE
        fold = round(width - 0.855 * height)
        profile = gradient_of(sleeve(width, height, fold))
        box = jav_poster_crop.front_panel_box(width, height, profile, "SMBD-172")
        self.assertEqual(box["method"], FOLD)
        # 那条三列宽的黑线整条留在框外，正封从它右边开始。
        self.assertGreaterEqual(box["x0"], fold + 3, box)
        self.assertLessEqual(box["x0"] - (fold + 3), 2, box)

    @unittest.skipUnless(opencv_available(), "缺 vision 依赖组")
    def test_an_edge_outside_the_narrow_window_is_content_not_the_spine(self):
        """书脊上竖排的片名字边比书脊右缘强，但它切出的形状不在窗里，不按它切。"""
        width, height = self.WIDE
        text_edge = round(width - 0.90 * height)
        profile = gradient_of(sleeve(width, height, text_edge))
        box = jav_poster_crop.front_panel_box(width, height, profile, "CWPBD-126")
        self.assertEqual(box["method"], RATIO)
        self.assertEqual(box["x0"], round(width - jav_poster_crop.WIDE_PANEL_ASPECT * height))

    def test_the_record_routes_the_code_into_the_shape_decision(self):
        record = jav_poster_crop.crop_record("SMBD-110", *self.WIDE, [0.0] * self.WIDE[0])
        self.assertEqual(record["box"]["method"], RATIO)
        self.assertEqual(record["px"], list(self.WIDE))


class RecordTests(unittest.TestCase):
    """边车的内容、读写与失效判定。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.cover = self.root / "ABW-232.jpg"
        sleeve(800, 540, 420).save(self.cover)

    def test_a_shape_that_is_not_cropped_never_touches_the_gradient(self):
        """番号形态先判：不该裁的图连解码都不做。"""
        calls = []

        def source():
            calls.append(1)
            return [0.0] * 800

        record = jav_poster_crop.crop_record("HEYZO-1380", 800, 540, source)
        self.assertEqual(record["box"]["method"], NONE)
        self.assertEqual(calls, [])

    def test_the_record_carries_the_version_the_size_and_the_box(self):
        record = jav_poster_crop.crop_record("ABW-232", 800, 540, [0.0] * 800)
        self.assertEqual(record["version"], jav_poster_crop.ALGORITHM_VERSION)
        self.assertEqual(record["px"], [800, 540])
        self.assertEqual(record["box"]["method"], RATIO)

    def test_the_sidecar_sits_beside_the_cover_and_round_trips(self):
        record = jav_poster_crop.crop_record("ABW-232", 800, 540, [0.0] * 800)
        path = jav_poster_crop.write_sidecar(self.cover, record)
        self.assertEqual(path.name, "ABW-232.poster.json")
        self.assertEqual(jav_poster_crop.read_sidecar(self.cover), record)
        self.assertTrue(jav_poster_crop.is_current(record, 800, 540))

    def test_an_outdated_version_or_a_replaced_cover_forces_a_recount(self):
        record = jav_poster_crop.crop_record("ABW-232", 800, 540, [0.0] * 800)
        self.assertFalse(jav_poster_crop.is_current(record, 2184, 1464),
                         "封面被更大的那张换掉之后，旧框落在新图上是一块错位的区域")
        stale = dict(record, version="poster-crop-v0")
        self.assertFalse(jav_poster_crop.is_current(stale, 800, 540))
        self.assertFalse(jav_poster_crop.is_current(None, 800, 540))

    def test_a_broken_sidecar_reads_as_missing(self):
        jav_poster_crop.sidecar_path(self.cover).write_text("{not json",
                                                            encoding="utf-8")
        self.assertIsNone(jav_poster_crop.read_sidecar(self.cover))


class ProjectionTests(unittest.TestCase):
    """API 字段的投影：只有真的算出框的那一档才给值。"""

    def test_a_real_box_projects_with_the_source_size(self):
        record = jav_poster_crop.crop_record("ABW-232", 800, 540, [0.0] * 800)
        projected = jav_poster_crop.projection(record)
        self.assertEqual(projected["px"], [800, 540])
        self.assertEqual(projected["method"], RATIO)
        self.assertEqual((projected["x1"], projected["y0"], projected["y1"]),
                         (800, 0, 540))

    def test_everything_unusable_projects_to_none(self):
        for record in (None, {}, {"version": "poster-crop-v0"},
                       jav_poster_crop.crop_record("HEYZO-1380", 800, 540),
                       {"version": jav_poster_crop.ALGORITHM_VERSION,
                        "px": [800, 540], "box": {"x0": 0, "x1": 0, "y0": 0,
                                                  "y1": 0, "method": RATIO}},
                       {"version": jav_poster_crop.ALGORITHM_VERSION,
                        "px": [800, 540], "box": {"x0": 0, "x1": 900, "y0": 0,
                                                  "y1": 540, "method": RATIO}}):
            with self.subTest(record=record):
                self.assertIsNone(jav_poster_crop.projection(record))


class ManualBoxTests(unittest.TestCase):
    """人在详情页自己框的那一块，与算出来的框共用同一份边车。"""

    def test_a_hand_drawn_box_records_its_four_edges_and_its_source(self):
        record = jav_poster_crop.manual_record(800, 540, {"x0": 200, "y0": 40,
                                                          "x1": 600, "y1": 500})
        self.assertEqual(record["box"], {"x0": 200, "y0": 40, "x1": 600, "y1": 500,
                                         "method": MANUAL})
        self.assertEqual(record["px"], [800, 540])
        self.assertEqual(record["source"], MANUAL_SOURCE)
        self.assertTrue(jav_poster_crop.is_manual(record))
        self.assertEqual(jav_poster_crop.projection(record)["method"], MANUAL)

    def test_a_box_that_is_not_a_box_is_refused_before_it_reaches_the_disk(self):
        for box in (None, {}, {"x0": 0, "y0": 0, "x1": 0, "y1": 100},
                    {"x0": "左", "y0": 0, "x1": 100, "y1": 100},
                    # 夹回图里之后只剩一条线：整幅右侧之外的框没有内容可取。
                    {"x0": 900, "y0": 0, "x1": 1000, "y1": 100}):
            with self.subTest(box=box):
                self.assertIsNone(jav_poster_crop.manual_record(800, 540, box))

    def test_edges_outside_the_image_are_pulled_back_in(self):
        record = jav_poster_crop.manual_record(800, 540, {"x0": -20, "y0": -5,
                                                          "x1": 1200, "y1": 900})
        self.assertEqual(record["box"]["x0"], 0)
        self.assertEqual((record["box"]["x1"], record["box"]["y1"]), (800, 540))

    def test_a_hand_drawn_box_survives_an_algorithm_bump_but_not_a_new_cover(self):
        """手工框后面没有算法，改判据不构成重算它的理由；换了封面它就作废。"""
        record = jav_poster_crop.manual_record(800, 540, {"x0": 200, "y0": 0,
                                                          "x1": 600, "y1": 540})
        stale = dict(record, version="poster-crop-v0")
        self.assertTrue(jav_poster_crop.is_current(stale, 800, 540))
        self.assertIsNotNone(jav_poster_crop.projection(stale))
        # 算出来的框换个版本号就该重算，两档判据不能混。
        computed = dict(jav_poster_crop.crop_record("ABW-232", 800, 540, [0.0] * 800),
                        version="poster-crop-v0")
        self.assertFalse(jav_poster_crop.is_current(computed, 800, 540))
        self.assertIsNone(jav_poster_crop.projection(computed))
        # 封面被更大的那张换掉：框描述的是另一张图，手工框也一样作废。
        self.assertFalse(jav_poster_crop.is_current(record, 1600, 1080))


class CropBytesTests(unittest.TestCase):
    """按框切出新字节：原图不动，格式只在 PNG 与 JPEG 之间取一个。"""

    @staticmethod
    def payload(image: Image.Image, fmt: str) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, format=fmt)
        return buffer.getvalue()

    def test_a_jpeg_crop_comes_back_as_a_jpeg_of_the_boxed_size(self):
        body = self.payload(Image.new("RGB", (800, 540), (10, 120, 200)), "JPEG")
        cropped = images.crop_to_box(body, (200, 40, 600, 500))
        self.assertEqual(images.measure_image_size(cropped), (400, 460))
        with Image.open(io.BytesIO(cropped)) as opened:
            self.assertEqual(opened.format, "JPEG")

    def test_a_png_stays_a_png_and_transparency_lands_on_white(self):
        source = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        cropped = images.crop_to_box(self.payload(source, "PNG"), (0, 0, 50, 50))
        with Image.open(io.BytesIO(cropped)) as opened:
            self.assertEqual(opened.format, "PNG")
        # 同一张透明图存成 JPEG 那一路要铺白底，否则透明处落成黑块。
        flattened = images.crop_to_box(self.payload(source.convert("RGBA"), "WEBP"),
                                       (0, 0, 50, 50))
        with Image.open(io.BytesIO(flattened)) as opened:
            self.assertEqual(opened.convert("RGB").getpixel((10, 10)), (255, 255, 255))

    def test_a_box_outside_the_image_or_a_junk_payload_yields_nothing(self):
        body = self.payload(Image.new("RGB", (100, 100)), "JPEG")
        self.assertIsNone(images.crop_to_box(body, (0, 0, 200, 50)))
        self.assertIsNone(images.crop_to_box(body, (50, 0, 50, 50)))
        self.assertIsNone(images.crop_to_box(b"not an image", (0, 0, 10, 10)))


class BatchScriptTests(unittest.TestCase):
    """批处理只写边车、只处理该处理的那些图。"""

    def setUp(self):
        self.script = batch_script()
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.cover = self.root / "ABW-232.jpg"
        sleeve(800, 540, 420).save(self.cover)
        self.before = self.cover.read_bytes()

    def run_script(self, *argv):
        return self.script.main([*argv, "--covers", str(self.root)])

    def test_a_dry_run_writes_nothing(self):
        self.assertEqual(self.run_script(), 0)
        self.assertFalse(jav_poster_crop.sidecar_path(self.cover).exists())

    def test_online_upgrade_checks_official_sources_without_local_snapshots(self):
        from peach.jav_cover_fetch import Candidate
        script = batch_script('upgrade_audited_covers')
        candidate = Candidate('pics.dmm.co.jp', 'https://pics.dmm.co.jp/a.jpg', '')
        report = self.root / 'report'
        with patch.object(script, 'best_cover', return_value=(candidate, (2184, 1468), self.before)) as fetch:
            with patch.object(script, 'install_cover') as install:
                result = script.upgrade(self.cover, [], object(), report, live=True,
                                        metadata_root=self.root / 'metadata')
        self.assertEqual(result['status'], 'installed')
        self.assertEqual(fetch.call_args.args[1], 'ABW-232')
        self.assertEqual(fetch.call_args.kwargs['metadata_root'], self.root / 'metadata')
        self.assertEqual(install.call_args.kwargs['evidence']['resolver'], 'official-live-upgrade-v1')
        self.assertEqual((report / 'originals/ABW-232.jpg').read_bytes(), self.before)

    def test_upgrade_resets_connections_only_for_retryable_source_failures(self):
        from peach.jav_cover_fetch import NotFound, Unavailable
        from unittest.mock import Mock
        script = batch_script('upgrade_audited_covers')
        for error, expected in ((NotFound('HTTP 404'), False), (Unavailable('HTTP 503'), True)):
            transport = Mock()
            result = script.failure_result('ABW-232', error, transport)
            self.assertEqual(result['retryable'], expected)
            self.assertEqual(transport.renew.call_count, int(expected))

    def test_javdb_quarantine_preserves_bytes_and_resumes_without_duplicates(self):
        covers = self.root / 'generated/covers'
        covers.mkdir(parents=True)
        target = covers / 'MIDV-377.jpg'
        target.write_bytes(self.before)
        evidence = target.with_suffix('.scraping.json')
        evidence.write_text(json.dumps({'source': 'javdb', 'source_url': 'https://c0.jdbstatic.com/a.jpg'}), encoding='utf-8')
        official = covers / 'SPONE-001.jpg'
        official.write_bytes(self.before)
        report = self.root / 'review/quarantine'
        args = ['--data-root', str(self.root), '--report', str(report)]
        script = batch_script('quarantine_javdb_covers')
        self.assertEqual(script.main(args), 0)
        self.assertTrue(target.exists())
        self.assertEqual(script.main([*args, '--apply']), 0)
        self.assertFalse(target.exists())
        self.assertFalse(evidence.exists())
        self.assertEqual((report / 'originals/MIDV-377.jpg').read_bytes(), self.before)
        self.assertEqual(official.read_bytes(), self.before)
        self.assertEqual(script.main([*args, '--apply']), 0)
        result = json.loads((report / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual((result['matched'], result['quarantined']), (0, 1))

    def test_quarantine_collision_does_not_partially_move_an_item(self):
        covers = self.root / 'generated/covers'
        covers.mkdir(parents=True)
        target = covers / 'MIDV-377.jpg'
        target.write_bytes(self.before)
        evidence = target.with_suffix('.scraping.json')
        evidence.write_text(json.dumps({'source': 'javdb'}), encoding='utf-8')
        report = self.root / 'review/quarantine'
        (report / 'originals').mkdir(parents=True)
        (report / 'originals/MIDV-377.scraping.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            batch_script('quarantine_javdb_covers').main([
                '--data-root', str(self.root), '--report', str(report), '--apply'])
        self.assertEqual(target.read_bytes(), self.before)
        self.assertTrue(evidence.exists())

    def test_audit_keeps_manual_frames_and_reports_unknown_provenance(self):
        manual = jav_poster_crop.manual_record(800, 540, {'x0': 420, 'y0': 0, 'x1': 800, 'y1': 540})
        jav_poster_crop.write_sidecar(self.cover, manual)
        script = batch_script('audit_cover_artwork')
        row, proposed, _, _ = script.inspect(self.cover, self.root / 'metadata')
        self.assertEqual(proposed, manual)
        self.assertEqual(row['method'], MANUAL)
        self.assertTrue('missing_provenance' in row['reasons'])
        self.assertEqual(self.cover.read_bytes(), self.before)

    def test_face_crossing_the_spine_is_preserved_by_offline_reframing(self):
        target = self.root / 'SPONE-001.jpg'
        Image.new('RGB', (800, 600), 'gray').save(target)
        target.with_suffix('.face.json').write_text(json.dumps({
            'px': [800, 600], 'face': {'cx': .555, 'cy': .331, 'w': .257, 'h': .422, 'score': .918},
        }), encoding='utf-8')
        row, proposed, _, layout = batch_script('audit_cover_artwork').inspect(target, self.root / 'metadata')
        self.assertEqual((proposed['box']['method'], layout), (NONE, 'still'))
        self.assertEqual(row['x0'], 0)

    def test_offline_reframing_preserves_a_centered_front_with_padding(self):
        from peach.cover_artwork import FACE_ALGORITHM_VERSION
        target = self.root / 'HHL-018.jpg'
        Image.new('RGB', (840, 563), 'gray').save(target)
        target.with_suffix('.face.json').write_text(json.dumps({
            'version': FACE_ALGORITHM_VERSION, 'px': [840, 563],
            'face': {'cx': .487, 'cy': .231, 'w': .135, 'h': .242, 'score': .93},
        }), encoding='utf-8')
        profile = jav_poster_crop.ColumnProfile([.02] * 224 + [.2] * 394 + [.02] * 222, [0.] * 840)
        profile.seams[224] = profile.seams[618] = .95
        with patch.object(jav_poster_crop, 'file_gradient', return_value=profile):
            row, proposed, _, layout = batch_script('audit_cover_artwork').inspect(target, self.root / 'metadata')
        self.assertEqual((proposed['box']['method'], layout), (CENTER, ''))
        self.assertEqual((row['x0'], proposed['box']['x1']), (225, 618))

    def test_audit_refreshes_legacy_front_only_faces_before_classifying_the_cover(self):
        from peach.face_detect import Face
        from peach.cover_artwork import FACE_ALGORITHM_VERSION
        from unittest.mock import Mock
        target = self.root / 'SPONE-001.jpg'
        Image.new('RGB', (800, 600), 'gray').save(target)
        target.with_suffix('.face.json').write_text(json.dumps({
            'px': [800, 600], 'face': {'cx': .8, 'cy': .3, 'w': .06, 'h': .1, 'score': .9},
        }), encoding='utf-8')
        detector = Mock()
        detector.detect.return_value = [Face(.555, .331, .257, .422, .918), Face(.8, .3, .06, .1, .9)]
        row, proposed, face, layout = batch_script('audit_cover_artwork').inspect(target, self.root / 'metadata', detector=detector)
        self.assertEqual((proposed['box']['method'], layout), (NONE, 'still'))
        self.assertEqual((face['version'], face['face']['cx']), (FACE_ALGORITHM_VERSION, .555))
        self.assertNotIn('stale_face', row['reasons'])

    def test_face_cache_requires_current_algorithm_and_image_dimensions(self):
        from peach.cover_artwork import FACE_ALGORITHM_VERSION
        script = batch_script('detect_cover_faces')
        target = self.cover.with_suffix('.face.json')
        for version, size, expected in (('old', [800, 540], False),
                (FACE_ALGORITHM_VERSION, [1, 1], False), (FACE_ALGORITHM_VERSION, [800, 540], True)):
            target.write_text(json.dumps({'version': version, 'px': size}), encoding='utf-8')
            self.assertEqual(script.current_face(self.cover), expected)

    def test_apply_writes_the_sidecar_and_leaves_the_image_untouched(self):
        self.assertEqual(self.run_script("--apply"), 0)
        record = json.loads(
            jav_poster_crop.sidecar_path(self.cover).read_text(encoding="utf-8"))
        self.assertEqual(record["px"], [800, 540])
        self.assertIn(record["box"]["method"], (FOLD, RATIO))
        self.assertEqual(self.cover.read_bytes(), self.before,
                         "取景是边车元数据，原图一个字节都不动")

    @unittest.skipUnless(opencv_available(), "缺 vision 依赖组")
    def test_a_centred_still_is_counted_in_its_own_bucket(self):
        """16:9 居中拼图是单独一档，批处理照样计数、写边车，不因为认不得这档停下。"""
        still = self.root / "PASN-027.jpg"
        centred(1348, 758, 408, 940).save(still)
        self.assertEqual(self.run_script("--apply"), 0)
        self.assertEqual(jav_poster_crop.read_sidecar(still)["box"]["method"], CENTER)

    def test_a_current_sidecar_is_skipped_and_a_stale_one_is_redone(self):
        self.run_script("--apply")
        self.assertEqual(self.script.pending([self.cover], False), [])
        stale = jav_poster_crop.read_sidecar(self.cover)
        jav_poster_crop.write_sidecar(self.cover, dict(stale, version="poster-crop-v0"))
        self.assertEqual(self.script.pending([self.cover], False),
                         [(self.cover, (800, 540))])

    def test_an_unreadable_cover_is_counted_and_never_given_a_box(self):
        broken = self.root / "BAD-001.jpg"
        broken.write_bytes(b"not an image")
        self.assertEqual(self.run_script("--apply"), 0)
        self.assertFalse(jav_poster_crop.sidecar_path(broken).exists())


class ImageSizeTests(unittest.TestCase):
    def test_the_size_probe_reports_none_for_junk(self):
        root = Path(tempfile.mkdtemp()).resolve()
        broken = root / "x.jpg"
        broken.write_bytes(b"nope")
        self.assertIsNone(batch_script().image_size(broken))
        good = root / "y.jpg"
        buffer = io.BytesIO()
        Image.new("RGB", (40, 30)).save(buffer, "JPEG")
        good.write_bytes(buffer.getvalue())
        self.assertEqual(batch_script().image_size(good), (40, 30))


if __name__ == "__main__":
    unittest.main()
