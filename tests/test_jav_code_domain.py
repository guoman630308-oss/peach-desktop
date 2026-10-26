r"""转载站水印域名不得被当成番号。

起因是一次真实误判：asset 31048 的 path 是

    B:\番号\_未知厂牌\HHD800\hhd800.com@ABW-132.mp4\ABW-132.mp4

真番号 ABW-132 就在文件名里，`code` 却是 `HHD800`——hhd800.com 是转载站域名水印。
它能过关是因为 `normalise_code_key` 的形态规则会替「字母紧贴数字」补出连字符，
`HHD800` 于是变成 `HHD-800`，一路通过 `is_jav_code`、`JAV_ASSET_PREDICATE`、作品页的
`display_code`，还会被 `clean_names` 当成重命名依据写回 ledger。

所以这里同时钉住两侧：名单命中的标识一律不是番号，而真番号里同样没有分隔符的那些
（`IPX219C`、`MEYD911`、`476MLA-179`）必须继续被认出来——形态分不开，只有名单能分。

分隔符本身也是身份的一部分，这里一并钉住：素人系日期式番号里，一本道用 `_`、
加勒比用 `-`，同一天同一序号是两部不同影片，归一化与查询都不许把它们折叠成一个。

同一层还管另外四件事，样本一律取自本机账本的真实文件名：Tokyo-Hot 的 `n1234` 没有
字母段，西片是「厂牌／系列 + 发行日」而不是番号，停用词与手机录像的日期串一律不产出
番号，推广域名剥掉之后才轮到番号主体。
"""
import csv
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from peach.catalog_rules import (
    CODE_BODY_STOPWORDS,
    REPOST_SITE_LABELS,
    code_query_variants,
    compact_label,
    is_code_body_stopword,
    is_jav_asset,
    is_jav_code,
    is_repost_site_label,
    is_uncensored_code,
    jav_fallback_title,
    normalise_code_key,
    release_code_from_filename,
    release_code_from_text,
    release_identity,
    same_release_code,
    scrapes_as_jav,
    tokyo_hot_code,
    western_release_identity,
)
from peach import scripting
from peach.migrations import upgrade
from peach.review_csv import write_rows

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_domain_codes.py"
_spec = importlib.util.spec_from_file_location("audit_domain_codes", SCRIPT)
audit = importlib.util.module_from_spec(_spec)
sys.modules["audit_domain_codes"] = audit
_spec.loader.exec_module(audit)

SCHEMA = """
CREATE TABLE asset(
  id INTEGER PRIMARY KEY, location TEXT NOT NULL, path TEXT NOT NULL, name TEXT,
  medium TEXT, code TEXT, studio TEXT, release_date TEXT, UNIQUE(location,path));
CREATE TABLE entity(
  id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT, normalized_name TEXT,
  UNIQUE(kind,normalized_name));
CREATE TABLE asset_entity(
  asset_id INTEGER, entity_id INTEGER, role TEXT, source TEXT,
  UNIQUE(asset_id,entity_id,role,source));
"""


class RepostLabelTests(unittest.TestCase):
    def test_bare_label_and_full_domain_are_both_recognised(self):
        self.assertTrue(is_repost_site_label("HHD800"))
        self.assertTrue(is_repost_site_label("hhd800.com"))
        self.assertTrue(is_repost_site_label("www.98t.la"))

    def test_zero_padded_form_is_recognised_too(self):
        # `normalise_code_key` 把 `BEI88` 写成 `BEI-088`，而界面显示、SQL 谓词和
        # 重命名脚本比的都是补过零的那一种。只拦一种写法等于没拦。
        self.assertTrue(is_repost_site_label("BEI-088"))
        self.assertEqual(compact_label("BEI-088"), compact_label("bei88"))

    def test_real_codes_of_the_same_shape_are_not_labels(self):
        for code in ("MEYD911", "IPX219C", "476MLA-179", "ABW-132", "PBD390"):
            self.assertFalse(is_repost_site_label(code), code)

    def test_every_listed_label_is_stored_in_compact_form(self):
        # 名单是按压缩形比较的。写成 `HHD-800` 或 `HHD800.com` 都永远命中不了，
        # 而加名单的人不会去读 `is_repost_site_label` 才发现这件事。
        for label in REPOST_SITE_LABELS:
            self.assertEqual(compact_label(label), label, label)


class NormalisationTests(unittest.TestCase):
    def test_watermark_codes_keep_their_digits_without_a_hyphen(self):
        self.assertEqual(normalise_code_key("HHD800"), "HHD800")
        self.assertEqual(normalise_code_key("AAVV333"), "AAVV333")
        self.assertEqual(normalise_code_key("BEI88"), "BEI88")

    def test_real_compact_codes_still_normalise(self):
        self.assertEqual(normalise_code_key("MEYD911"), "MEYD-911")
        self.assertEqual(normalise_code_key("IPVR00296"), "IPVR-296")
        self.assertEqual(normalise_code_key("476MLA179"), "476MLA-179")

    def test_watermark_is_not_a_jav_code(self):
        for code in ("HHD800", "hhd800.com", "AAVV333", "KFA33", "HJD2048", "BEI88"):
            self.assertFalse(is_jav_code(code), code)
            self.assertFalse(is_jav_code(normalise_code_key(code)), code)

    def test_watermark_is_not_a_jav_asset_even_with_release_evidence(self):
        # 搬运包里混着有片商的条目。发行证据能救 `PBD390`，不能把域名变成番号。
        self.assertFalse(is_jav_asset("HHD800", "S1 NO.1 STYLE"))
        self.assertFalse(is_jav_asset("HHD800", None, "2022-04-01"))
        self.assertFalse(is_jav_asset("HHD800", None, None, ("performer",)))
        self.assertTrue(is_jav_asset("PBD390", "MOODYZ"))


class ScrapeGateTests(unittest.TestCase):
    """`scrapes_as_jav` 只拦「创作者作品的文件名被读成番号」，不替代发行证据。"""

    def test_a_creator_filename_that_looks_like_a_code_is_not_asked_about(self):
        # `sumwall95 masturbation_1.mp4` 与 `aerith 2412a.mp4`（角色名加年月）。
        self.assertFalse(scrapes_as_jav("SUMWALL-095", None, "sunwall"))
        self.assertFalse(scrapes_as_jav("AERITH-2412", None, "oscarkim123"))
        self.assertFalse(scrapes_as_jav("DAO-001", None, "Retsu_dao"))

    def test_release_evidence_on_the_same_row_puts_it_back_in(self):
        self.assertTrue(scrapes_as_jav("ABW-358", "Prestige", "oscarkim123"))
        self.assertTrue(scrapes_as_jav("ABW-358", None, "oscarkim123", "2024-12-01"))
        self.assertTrue(scrapes_as_jav("ABW-358", None, "oscarkim123", None, ("performer",)))

    def test_a_release_system_shape_never_needs_the_rest_of_the_evidence(self):
        # 这四种写法本身就是发行体系的证据，创作者的文件名撞不出来。
        for code in ("FC2-PPV-1233719", "FC-437689", "300MIUM-698", "STP-26232",
                     "SIRO-3508", "071213-625"):
            self.assertTrue(scrapes_as_jav(code, None, "合集-洛丽塔 多创作者"), code)

    def test_a_row_without_a_creator_is_asked_about_before_anything_is_known(self):
        """刮削入口不能要求发行证据先落库：那份证据正是这一趟要去取的。"""
        self.assertTrue(scrapes_as_jav("300NTK-625"))
        self.assertTrue(scrapes_as_jav("MIDE-001"))
        self.assertFalse(is_jav_asset("MIDE-001"), '同一条在浏览分类里仍然不算已坐实的发行物')

    def test_a_decided_region_outside_japan_stays_out(self):
        self.assertFalse(scrapes_as_jav("AR-032", "MIB", None, None, (), "kr"))
        self.assertTrue(scrapes_as_jav("MIDE-001", None, None, None, (), "jp"))


class ExtractionTests(unittest.TestCase):
    def test_named_volume_suffix_preserves_the_release_code(self):
        for suffix in ('CD1', 'disc02', 'Part-3', 'DVD4', 'Vol_5'):
            with self.subTest(suffix=suffix):
                self.assertEqual(release_code_from_filename(f'ABW-358-{suffix}.mp4'), 'ABW-358')

    def test_watermark_chain_yields_the_real_code(self):
        self.assertEqual(release_code_from_filename("ABW-132.mp4"), "ABW-132")
        self.assertEqual(release_code_from_filename("IPX219C.mp4"), "IPX-219")
        self.assertEqual(release_code_from_filename("MEYD911.mp4"), "MEYD-911")
        self.assertEqual(release_code_from_filename("476MLA-179.mp4"), "476MLA-179")

    def test_fc2_ids_survive_the_compact_form(self):
        self.assertEqual(release_code_from_text("FC2-PPV-2909046"), "FC2-PPV-2909046")
        self.assertEqual(release_code_from_text("fc2802296"), "FC2-PPV-802296")

    def test_the_fc_dash_short_form_is_an_fc2_id(self):
        """合集包把 FC2 写成 `FC-<商品号>`，认不出就没有番号，整条 FC2 来源链一家都不问。"""
        for name in ("FC-2052614-1.mp4", "FC-2052614_1.mp4", "FC-2052614.mp4",
                     "FC-2052614A.mp4", "FC-2052614-C.mp4"):
            self.assertEqual(release_code_from_filename(name), "FC2-PPV-2052614", name)
        self.assertEqual(release_code_from_filename("FC-437689-C.mp4"), "FC2-PPV-437689")
        self.assertEqual(normalise_code_key("FC-437689"), "FC2-PPV-437689")
        self.assertEqual(release_code_from_text("FC2052614"), "FC2-PPV-052614",
                         "不带分隔符的仍按 FC2 连写读")

    def test_labels_and_domains_extract_to_nothing(self):
        for text in ("HHD800", "hhd800.com", "www.98t.la", "AAVV333", "bei88"):
            self.assertIsNone(release_code_from_text(text), text)

    def test_what_the_volume_strip_leaves_must_be_a_whole_code(self):
        """合集包 `[mtfdz.club]WX17.3` 剥掉域名和 `.3` 只剩 `WX17`，补零凑成的 `WX-017` 是别的片。

        2026-09-25 全库 68221 段文件名与目录名对比，这条判据只改了 7 段，全是误识别：
        合集包名、相机文件 `DSCF27.1.jpg`、随机串 `UWFr85dczsVeysGg.mp4`。
        """
        for name in ("[mtfdz.club]WX17.3", "DSCF27.1.jpg", "dao01(1).mp4", "wen66s.mp4"):
            self.assertIsNone(release_code_from_filename(name), name)
        self.assertEqual(release_code_from_filename("abp762-1.mp4"), "ABP-762")
        self.assertEqual(release_code_from_filename("ABW-358-2.mp4"), "ABW-358")
        self.assertEqual(release_code_from_filename("[98t.tv]SSIS-001-C.mp4"), "SSIS-001")


class DatedCodeSeparatorTests(unittest.TestCase):
    """日期式番号的 `_` 与 `-` 是两个片商，归一化、查询和身份都不得折叠。"""

    def test_each_separator_keeps_its_own_key(self):
        self.assertEqual(normalise_code_key("092415_001"), "092415_001")
        self.assertEqual(normalise_code_key("092415-001"), "092415-001")
        self.assertEqual(normalise_code_key(" 092415_001 "), "092415_001")

    def test_the_two_separators_are_two_different_releases(self):
        self.assertFalse(same_release_code("092415_001", "092415-001"))
        self.assertFalse(same_release_code("092416-001", "092415-001"))
        self.assertTrue(same_release_code("092415_001", "092415_001"))

    def test_queries_never_swap_the_separator(self):
        # 用错分隔符搜到的是别的片商的另一部片，不是同一发行的另一种写法。
        self.assertEqual(code_query_variants("092415_001"), ("092415_001",))
        self.assertEqual(code_query_variants("092415-001"), ("092415-001",))

    def test_a_separatorless_source_id_matches_either_maker(self):
        # 来源只给数字时分隔符无从得知；缺一个字符不是「这是另一部片」的证据。
        self.assertEqual(release_identity("040221001"), "040221001")
        self.assertTrue(same_release_code("040221001", "040221-001"))
        self.assertTrue(same_release_code("040221001", "040221_001"))
        self.assertFalse(same_release_code("040221001", "040222-001"))

    def test_filenames_hand_over_the_separator_they_carry(self):
        self.assertEqual(release_code_from_filename("1pondo-092415_001-FHD.mp4"),
                         "092415_001")
        self.assertEqual(release_code_from_filename("1pon-092415-001-fhd1_(new).mp4"),
                         "092415-001")
        self.assertEqual(release_code_from_text("092415_001"), "092415_001")

    def test_titles_still_strip_a_code_written_the_other_way(self):
        # 身份认分隔符，野生文件名的写法却会漂移：账本 asset 6118 与 6149 是同一部
        # 一本道，一个写 `1pon-092415-001-fhd1`，一个写 `1pondo-092415_001-FHD`。
        self.assertEqual(jav_fallback_title("1pon-092415-001-fhd1_(new).mp4",
                                            "092415_001"), "")

    def test_other_shapes_still_fold_their_underscore(self):
        self.assertEqual(normalise_code_key("ABW_232"), "ABW-232")
        self.assertEqual(normalise_code_key("300MIUM_1239"), "300MIUM-1239")
        self.assertEqual(normalise_code_key("fc2_ppv_802296"), "FC2-PPV-802296")

    def test_one_shared_shape_decides_dated_and_uncensored(self):
        for value in ("092415_001", "092415-001", "040221-0012"):
            self.assertTrue(is_jav_code(value), value)
            self.assertTrue(is_uncensored_code(value), value)
        for value in ("09241-001", "0924150-001", "092415-1", "ABW-232"):
            self.assertFalse(is_uncensored_code(value), value)


class TokyoHotShapeTests(unittest.TestCase):
    """Tokyo-Hot 的编号没有厂牌字母段，规范写法是小写。`k` 与 `red` 两支账本里一条
    没有，按同一发行体系一并认。
    """

    def test_the_canonical_form_is_lowercase_and_zero_padded(self):
        self.assertEqual(tokyo_hot_code("N0646"), "n0646")
        self.assertEqual(tokyo_hot_code("n646"), "n0646")
        self.assertEqual(tokyo_hot_code("K1234"), "k1234")
        self.assertEqual(tokyo_hot_code("RED123"), "red-123")
        self.assertEqual(tokyo_hot_code("red_123"), "red-123")
        self.assertEqual(tokyo_hot_code("ABW-132"), "")

    def test_the_key_the_identity_and_the_shape_gate_agree(self):
        self.assertEqual(normalise_code_key("N0646"), "n0646")
        self.assertEqual(release_identity("N0646"), "n0646")
        self.assertTrue(same_release_code("n646", "N0646"))
        self.assertTrue(is_jav_code("n0646"))
        self.assertTrue(is_uncensored_code("n0646"))
        self.assertEqual(code_query_variants("N0646"), ("n0646",))

    def test_kirari_numbers_carry_an_s_before_the_serial(self):
        """ムゲン 的 KIRARI 在序号前带 `S`；别家的「字母-S数字」没有实证，不放开。"""
        for value in ("MKBD-S89", "MKBD-S118", "MKD-S89", "mkbd-s118"):
            self.assertTrue(is_jav_code(normalise_code_key(value)), value)
        for value in ("ABC-S12", "MKBD-S1", "MKBD-89S"):
            self.assertFalse(is_jav_code(normalise_code_key(value)), value)

    def test_a_bare_number_is_read_only_at_the_head_of_the_name(self):
        self.assertEqual(release_code_from_filename("n1032.mkv"), "n1032")
        self.assertEqual(release_code_from_filename("n0762.mkv"), "n0762")
        self.assertEqual(
            release_code_from_filename("n1025_rena_yamamoto_ss_n_fhd.wmv"), "n1025")
        self.assertEqual(
            release_code_from_filename("n0890_mary_jane_lee_tb_n {ThePornGarage}.mp4"),
            "n0890")

    def test_the_site_name_licenses_a_number_further_in(self):
        self.assertEqual(release_code_from_filename("Tokyo-Hot n0646 HD.wmv"), "n0646")
        self.assertEqual(
            release_code_from_filename("Tokyo Hot (n1042)(Rena Yamamoto)[1080p].wmv"),
            "n1042")
        self.assertIsNone(release_code_from_filename("Tokyo-Hot.mp4"))

    def test_lookalikes_without_the_site_stay_out(self):
        # 编号只有一个字母，形态本身挡不住任何东西：`no0037_01` 是论坛整合包的分卷，
        # `k12` 位数不够，名字中段的 `n1042` 没有站名替它作证。
        self.assertIsNone(release_code_from_filename("no0037_01.wmv"))
        self.assertIsNone(release_code_from_filename("k12.mp4"))
        self.assertIsNone(release_code_from_filename("Rena n1042 clip.mp4"))

    def test_the_number_is_stripped_out_of_the_fallback_title(self):
        self.assertEqual(jav_fallback_title("Tokyo-Hot n0646 HD.wmv", "n0646"), "")
        self.assertEqual(
            jav_fallback_title("n1025_rena_yamamoto_ss_n_fhd.wmv", "n1025"),
            "rena yamamoto ss n")


class DeliveryFilenameTests(unittest.TestCase):
    """发行站自己交付的文件名，番号被站方的写法包着。收益和误判用同一批名字量过：解析不出番号
    却已有 code 的从 803 条降到 799 条，解析出来与 code 不符的仍是 4 条，20721 条
    没有 code 的文件新增解析出 0 条。
    """

    def test_a_dmm_delivery_name_carries_the_code_between_date_and_codec(self):
        self.assertEqual(release_code_from_filename("0112mide612-h264.mp4"), "MIDE-612")

    def test_the_date_prefix_is_four_digits_and_the_tail_is_a_codec(self):
        # 三位是素人系的厂牌段（`259LUXU-1007`），放宽位数就会把它吃掉；尾巴不认
        # 「任意后缀」，否则把演员名写在番号后面的名字也成了同一形状。
        self.assertIsNone(release_code_from_filename("0112mide612-鈴村あいり.mp4"))
        self.assertIsNone(release_code_from_filename("259luxu1137-h264.mp4"))
        self.assertEqual(release_code_from_filename("259LUXU-1137.mp4"), "259LUXU-1137")

    def test_heyzo_writes_its_own_name_before_the_number(self):
        self.assertEqual(release_code_from_filename("heyzo_hd_1031_full.mp4"), "HEYZO-1031")
        self.assertEqual(release_code_from_filename("heyzo_lt_1380_full-1.mp4"), "HEYZO-1380")
        self.assertEqual(release_code_from_filename("HEYZO-1380.mp4"), "HEYZO-1380")

    def test_the_number_alone_is_not_a_heyzo_release(self):
        # 站名是这条规则的全部证据：少了它，创作者目录里按下划线分段、末尾带画质
        # 数字的名字同样满足形状。
        self.assertIsNone(release_code_from_filename("Banbi_20歳の女子大生_1080.mp4"))
        self.assertIsNone(release_code_from_filename("heyzo.mp4"))


class WesternDateShapeTests(unittest.TestCase):
    """西片是「厂牌／系列 + 发行日」，没有番号。

    样本取自本机账本里带这一形态的文件名。身份进 `release_identity` 是因为同一场景在
    不同发布组手里写成两位和四位年份，收敛成一个身份才判得了重；它带句点和四位年份，
    和任何番号都不会撞上。
    """

    def test_the_identity_is_the_series_and_the_release_day(self):
        self.assertEqual(
            western_release_identity(
                "DorcelClub.24.12.02.Christy.White.XXX.1080p.HEVC.x265.PRT.mp4"),
            "DORCELCLUB.2024-12-02")
        self.assertEqual(
            western_release_identity("Vixen.2026.05.07.Scene.XXX.1080p.mp4"),
            "VIXEN.2026-05-07")
        self.assertEqual(
            western_release_identity(
                "E081. Shinaryen.19.11.08.Sexy.Babe.Suck.Big.Cock.Boyfriend "
                "And Rough Sex After Reading A Porn Story -【Shinaryen】.mp4"),
            "SHINARYEN.2019-11-08")

    def test_two_year_writings_of_one_scene_are_one_identity(self):
        self.assertTrue(same_release_code("Vixen.26.05.07", "Vixen.2026.05.07"))
        self.assertFalse(same_release_code("Vixen.26.05.07", "Vixen.26.05.08"))

    def test_no_jav_code_is_mined_out_of_a_western_name(self):
        for name in (
            "DorcelClub.24.12.02.Christy.White.XXX.1080p.HEVC.x265.PRT.mp4",
            "Vixen.2026.05.07.Scene.XXX.1080p.mp4",
            "E115. LIFESELECOTR.20.10.03.Shinaryen.My.Hot.Girlfriend -【Shinaryen】.mp4",
        ):
            self.assertIsNone(release_code_from_filename(name), name)
            self.assertFalse(is_jav_code(western_release_identity(name)), name)

    def test_a_date_inside_the_title_does_not_invent_a_series(self):
        # 日期在标题中段、系列名写在方括号里：从 token 中段搜会把 `Sex` 当成系列。
        self.assertEqual(
            western_release_identity(
                "E078. Redhead.Sucking.Big.Cock.And.Hard.Sex.2019.10.15 -【Shinaryen】.mp4"),
            "")
        self.assertEqual(
            western_release_identity(
                "E097. Gamer Girl Teen Fucked While She Plays（20.03.22）.mp4"), "")

    def test_an_impossible_month_or_day_is_not_a_release_date(self):
        self.assertEqual(western_release_identity("Series.24.13.02.Scene.mp4"), "")
        self.assertEqual(western_release_identity("Series.24.12.32.Scene.mp4"), "")


class CodeBodyStopwordTests(unittest.TestCase):
    """只说明「这是什么文件」的词不是番号主体。

    四个词各自对应本机账本里一批被当成番号的创作者素材与整合包分卷。
    """

    def test_the_camera_and_structure_words_yield_nothing(self):
        self.assertIsNone(release_code_from_filename("IMG_3092 (2).mp4"))
        self.assertIsNone(release_code_from_filename("IMG_7195 (2).mov"))
        self.assertIsNone(release_code_from_filename("video_2025-09-02_20-07-50.mp4"))
        self.assertIsNone(release_code_from_filename("no0037_01.wmv"))
        self.assertIsNone(release_code_from_filename("part 18.mp4"))

    def test_a_phone_capture_is_not_a_dated_release(self):
        # 月日都成立时日期形状拦不住，只有「开头写着这是一段录像」能。
        self.assertIsNone(release_code_from_filename("VID_20241015_071039_730.mp4"))
        self.assertIsNone(release_code_from_filename("VID_20220818_125735_816.mp4"))

    def test_an_impossible_month_or_day_is_not_a_dated_code(self):
        self.assertFalse(is_jav_code("135735_816"))
        self.assertFalse(is_uncensored_code("125745_816"))
        self.assertIsNone(release_code_from_text("125735_816"))

    def test_the_real_dated_releases_still_parse(self):
        self.assertEqual(release_code_from_filename("1pondo-092415_001-FHD.mp4"),
                         "092415_001")
        self.assertEqual(release_code_from_filename("040221-001-carib-1080p.mp4"),
                         "040221-001")
        self.assertEqual(release_code_from_filename("122614_001-1pon-whole1_hd.avi"),
                         "122614_001")

    def test_a_maker_code_that_starts_with_a_stopword_is_kept(self):
        # 判据是整段字母，不是前缀：按前缀一刀切会把 MIB 的 `NOAH-101` 一起拦掉。
        self.assertEqual(release_code_from_filename("[K-MIB]NOAH-101(NOAH)(1).mp4"),
                         "NOAH-101")
        self.assertFalse(is_code_body_stopword("NOAH-101"))
        self.assertTrue(is_code_body_stopword("IMG_3092"))

    def test_a_stopword_already_stored_in_the_ledger_is_not_a_jav_code(self):
        # 提取器不再产出这些值，账本里却存着 58 条，而 `JAV_ASSET_PREDICATE` 通过
        # SQLite 自定义函数直接调 `is_jav_code`。
        for stored in ("VIDEO-2022", "VIDEO-2025", "IMG-3092"):
            self.assertFalse(is_jav_code(stored), stored)
            self.assertFalse(is_jav_asset(stored, "S1 NO.1 STYLE"), stored)
        self.assertTrue(is_jav_code("NOAH-101"))

    def test_a_stored_date_code_with_an_impossible_day_is_not_a_jav_code(self):
        self.assertFalse(is_jav_code("125735-816"))
        self.assertFalse(is_jav_code("131454-967"))
        self.assertTrue(is_jav_code("092415-001"))

    def test_the_table_holds_upper_case_letters_only(self):
        # 比较前先把非字母抹掉再转大写；小写或带数字的条目永远命不中。
        for word in CODE_BODY_STOPWORDS:
            self.assertEqual(word, word.upper(), word)
            self.assertTrue(word.isalpha(), word)


class PromoDomainStrippingTests(unittest.TestCase):
    """推广域名剥在番号前面：头、尾、方括号三种位置共用 `strip_promo_markers`。"""

    def test_a_domain_in_front_of_the_code_is_dropped(self):
        self.assertEqual(release_code_from_filename("www.98T.la@ABW-358-U.mp4"),
                         "ABW-358")
        self.assertEqual(release_code_from_filename("www.xxx.com@ABC-123.mp4"),
                         "ABC-123")
        self.assertEqual(release_code_from_filename("[xxx.cc]ABC-123.mp4"), "ABC-123")

    def test_a_domain_behind_the_code_is_dropped(self):
        self.assertEqual(release_code_from_filename("ABC-123@xxx.net.mp4"), "ABC-123")
        self.assertEqual(release_code_from_filename("ABP-762-fuckbe.com.mp4"), "ABP-762")
        self.assertEqual(release_code_from_filename("259LUXU-1004-fuckbe.com.mp4"),
                         "259LUXU-1004")
        self.assertEqual(
            release_code_from_filename("WAAA-415-UNCENSORED-nyap2p.com.mp4"), "WAAA-415")

    def test_an_fc2_id_behind_a_domain_is_still_an_fc2_id(self):
        self.assertEqual(release_code_from_filename("www.98T.la@FC2-1292985.mp4"),
                         "FC2-PPV-1292985")

    def test_a_bare_watermark_is_still_not_a_code(self):
        for text in ("hhd800.com", "www.98t.la", "HHD800"):
            self.assertIsNone(release_code_from_text(text), text)


class AuditScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.connection = sqlite3.connect(Path(self.tmp.name).resolve() / "ledger.db")
        self.addCleanup(self.connection.close)
        self.connection.executescript(SCHEMA)

    def _asset(self, asset_id, path, code, *, studio=None, kind=None):
        self.connection.execute(
            "INSERT INTO asset(id,location,path,name,medium,code,studio) "
            "VALUES(?,'local',?,?,'video',?,?)",
            (asset_id, path, path.rsplit("\\", 1)[-1], code, studio))
        if kind:
            self.connection.execute(
                "INSERT OR IGNORE INTO entity(id,kind,canonical_name,normalized_name) "
                "VALUES(?,?,?,?)", (asset_id, kind, f"e{asset_id}", f"e{asset_id}"))
            self.connection.execute(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source) "
                "VALUES(?,?,?,'test')", (asset_id, asset_id, kind))
        self.connection.commit()

    def _rows(self):
        return {int(row["asset_id"]): row for row in audit.collect(self.connection)}

    def test_the_reported_asset_gets_the_code_from_its_filename(self):
        self._asset(31048, r"B:\番号\_未知厂牌\HHD800\hhd800.com@ABW-132.mp4\ABW-132.mp4",
                    "HHD800")
        row = self._rows()[31048]
        self.assertEqual(row["verdict"], audit.VERDICT_CODED)
        self.assertEqual(row["proposed_code"], "ABW-132")
        self.assertEqual(row["tier"], "E1")
        self.assertEqual(row["evidence"], "hhd800.com")

    def test_a_watermark_without_any_code_proposes_clearing_instead(self):
        self._asset(1, r"B:\番号\_未知厂牌\BEI88\bei88@sis001@某个标题.mp4\某个标题.mp4",
                    "BEI88")
        row = self._rows()[1]
        self.assertEqual(row["verdict"], audit.VERDICT_BLANK)
        self.assertEqual(row["proposed_code"], "")

    def test_the_code_named_directory_is_not_mined_for_a_proposal(self):
        # `…\WX17\` 那层目录就是被水印顶替出来的，从它身上解析只会拿回同一个水印。
        self._asset(2, r"B:\番号\_未知厂牌\WX17\[mtfdz.club]WX17.3\别删~好回家.rar", "WX17")
        row = self._rows()[2]
        self.assertEqual(row["verdict"], audit.VERDICT_PACK)
        self.assertEqual(row["proposed_code"], "")
        self.assertEqual(row["evidence"], "mtfdz.club")

    def test_a_taste_tag_does_not_shield_a_pack_label(self):
        # WX17 的 269 条里大半挂着口味标签。按「有实体就放过」判会整包漏掉。
        self._asset(3, r"B:\番号\_未知厂牌\WX17\[mtfdz.club]WX17.3\x\a.mp4", "WX17",
                    kind="tag")
        self.assertEqual(self._rows()[3]["verdict"], audit.VERDICT_PACK)

    def test_a_real_code_from_a_repost_site_is_left_alone(self):
        # 文件来自 thzu.cc，但 `TZ-105` 自带分隔符，没人替它造过番号。
        self._asset(4, r"B:\番号\_未知厂牌\TZ-105\thzu.cc@TZ-105.mp4", "TZ-105")
        self.assertNotIn(4, self._rows())

    def test_release_evidence_keeps_a_compact_code_out_of_the_review(self):
        self._asset(5, r"B:\番号\MOODYZ\thz.la@PBD390\PBD390.mp4", "PBD390",
                    studio="MOODYZ")
        self.assertNotIn(5, self._rows())

    def test_an_ad_file_inherits_the_code_its_siblings_agree_on(self):
        # 发行目录里混着广告图和被站点改过名的分卷。逐个文件名判会把它们报成
        # 「无番号，建议清空」，而同目录的另外两条已经指明这是哪一部。
        folder = r"B:\番号\_未知厂牌\HJD2048\hjd2048.com-0112mide612-h264"
        self._asset(10, folder + r"\mide612-5.mp4", "HJD2048")
        self._asset(11, folder + r"\mide612.jpg", "HJD2048")
        self._asset(12, folder + r"\最新成人AV.gif", "HJD2048")
        rows = self._rows()
        self.assertEqual((rows[10]["proposed_code"], rows[10]["proposal_from"]),
                         ("MIDE-612", "文件名"))
        self.assertEqual((rows[12]["proposed_code"], rows[12]["proposal_from"]),
                         ("MIDE-612", "同目录兄弟"))

    def test_two_codes_in_one_directory_leave_the_rest_unproposed(self):
        # 目录里有两部片时，兄弟证据指不出这条属于哪一部，宁可报「无番号」。
        folder = r"B:\番号\_未知厂牌\HHD800\hhd800.com@合集"
        self._asset(13, folder + r"\ABW-132.mp4", "HHD800")
        self._asset(14, folder + r"\SSIS-070.mp4", "HHD800")
        self._asset(15, folder + r"\广告.gif", "HHD800")
        row = self._rows()[15]
        self.assertEqual(row["verdict"], audit.VERDICT_BLANK)
        self.assertEqual((row["proposed_code"], row["proposal_from"]), ("", ""))

    def test_an_unlisted_domain_is_caught_by_path_evidence_alone(self):
        # 名单永远滞后于新站点。路径里带 `<code>.<tld>` 的，不进名单也要报出来，
        # 否则这份排查只能确认已经知道的事。
        self._asset(6, r"B:\番号\_未知厂牌\NEWSITE77\newsite77.com@SSIS-070\SSIS-070.mp4",
                    "NEWSITE77")
        row = self._rows()[6]
        self.assertEqual((row["tier"], row["confidence"]), ("E2", "高"))
        self.assertEqual(row["proposed_code"], "SSIS-070")

    def test_a_sibling_carries_the_evidence_for_a_renamed_file(self):
        # 同一批搬运包里只有部分文件保留了水印；自己那条 path 判不出来的走 E3。
        self._asset(7, r"B:\番号\_未知厂牌\NEWSITE77\newsite77.com@SSIS-070\SSIS-070.mp4",
                    "NEWSITE77")
        self._asset(8, r"B:\番号\_未知厂牌\NEWSITE77\SSIS-071\SSIS-071.mp4", "NEWSITE77")
        row = self._rows()[8]
        self.assertEqual((row["tier"], row["confidence"]), ("E3", "中"))
        self.assertEqual(row["proposed_code"], "SSIS-071")
        self.assertEqual(row["evidence"], "newsite77.com")


class ApplyTests(unittest.TestCase):
    """写库这一侧。

    用真实迁移建库，不用精简 schema：这次改的是 `asset.code`，而 `asset_search.code`
    是靠 `0004`/`0023` 的触发器跟着走的。拿一张没有触发器的表测，等于把「搜索索引里
    还留着旧水印」这个最容易漏的表面从测试里删掉。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.db = self.root / "ledger.db"
        sqlite3.connect(self.db).close()
        upgrade(self.db, MIGRATIONS)
        self.review = self.root / "review.csv"

    def _asset(self, asset_id, path, code):
        connection = sqlite3.connect(self.db)
        connection.execute(
            "INSERT INTO asset(id,location,path,name,medium,code) "
            "VALUES(?,'local',?,?,'video',?)",
            (asset_id, path, path.rsplit("\\", 1)[-1], code))
        connection.commit()
        connection.close()

    def _plan(self, *rows):
        write_rows(self.review, (*audit.FIELDS, "applied"), rows, fill_missing=True)

    def _run(self, *extra):
        return audit.run(audit.build_parser().parse_args(
            ["--db", str(self.db), "--review-csv", str(self.review), *extra]))

    def _codes(self):
        connection = sqlite3.connect(self.db)
        try:
            return {row[0]: (row[1], row[2]) for row in connection.execute(
                "SELECT a.id,a.code,s.code FROM asset a "
                "LEFT JOIN asset_search s ON s.asset_id=a.id")}
        finally:
            connection.close()

    def test_apply_without_a_backup_refuses_to_start(self):
        # `code` 是真相字段，和迁移同级：没有备份就没有回退路径。
        self._asset(1, r"B:\x\HHD800\hhd800.com@ABW-132.mp4", "HHD800")
        self._plan({"asset_id": "1", "current_code": "HHD800",
                    "proposed_code": "ABW-132", "tier": "E1"})
        with self.assertRaises(SystemExit):
            self._run("--apply")
        self.assertEqual(self._codes()[1][0], "HHD800")

    def test_the_reviewed_rows_are_written_and_the_index_follows(self):
        self._asset(1, r"B:\x\HHD800\hhd800.com@ABW-132.mp4", "HHD800")
        self._asset(2, r"B:\x\BEI88\bei88@sis001@合集.mp4", "BEI88")
        self._plan(
            {"asset_id": "1", "current_code": "HHD800",
             "proposed_code": "ABW-132", "tier": "E1"},
            {"asset_id": "2", "current_code": "BEI88",
             "proposed_code": "", "tier": "E1"})
        self.assertEqual(self._run("--apply", "--backup", str(self.root / "b.db")), 0)
        codes = self._codes()
        self.assertEqual(codes[1], ("ABW-132", "ABW-132"))
        self.assertEqual(codes[2], (None, ""))
        self.assertTrue((self.root / "b.db").exists())

    def test_a_pack_row_is_never_written(self):
        # `存疑` 的 269 条论坛整合包要人工看过再定，不能跟着这一批一起改。
        self._asset(3, r"B:\x\WX17\[mtfdz.club]WX17.3\a.rar", "WX17")
        self._plan({"asset_id": "3", "current_code": "WX17",
                    "proposed_code": "", "tier": "存疑"})
        with self.assertRaises(SystemExit):
            self._run("--apply", "--backup", str(self.root / "b.db"))
        self.assertEqual(self._codes()[3][0], "WX17")

    def test_a_row_edited_since_the_review_is_skipped_not_overwritten(self):
        # 出表和写库之间隔着一次人工阅读，期间别的脚本可能已经改过同一行。
        self._asset(4, r"B:\x\HHD800\hhd800.com@ABW-132.mp4", "SSIS-070")
        self._plan({"asset_id": "4", "current_code": "HHD800",
                    "proposed_code": "ABW-132", "tier": "E1"})
        self._run("--apply", "--backup", str(self.root / "b.db"))
        self.assertEqual(self._codes()[4][0], "SSIS-070")

    def test_a_watermark_typed_into_the_csv_is_refused(self):
        # 表是给人改的，但手填回一个域名就绕开了这次修复的全部意义。
        self._asset(5, r"B:\x\HHD800\hhd800.com@ABW-132.mp4", "HHD800")
        self._plan({"asset_id": "5", "current_code": "HHD800",
                    "proposed_code": "hhd800.com", "tier": "E1"})
        self._run("--apply", "--backup", str(self.root / "b.db"))
        self.assertEqual(self._codes()[5][0], "HHD800")

    def test_the_default_run_leaves_the_ledger_alone(self):
        self._asset(6, r"B:\x\HHD800\hhd800.com@ABW-132.mp4", "HHD800")
        self.assertEqual(self._run(), 0)
        self.assertEqual(self._codes()[6][0], "HHD800")

    def test_the_connections_and_the_backup_gate_are_the_shared_ones(self):
        # 只读连接、`--apply` 缺 `--backup` 的拒绝和写前备份都在 `peach.scripting` 里，
        # 这里不再自备一份：同一条判据有两份实现时，修好一份不等于修好这件事。
        self.assertIs(audit.open_readonly, scripting.open_readonly)
        self.assertIs(audit.open_for_write, scripting.open_for_write)


_revert_spec = importlib.util.spec_from_file_location(
    "revert_misread_code", SCRIPT.with_name("revert_misread_code.py"))
revert = importlib.util.module_from_spec(_revert_spec)
_revert_spec.loader.exec_module(revert)


class RevertMisreadCodeTests(unittest.TestCase):
    """论坛合集包 `WX17` 被当成 `WX-017` 刮回另一部片的片名和标签，按键整批撤回。"""

    NOW = "2026-09-25T00:00:00Z"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "ledger.db"
        sqlite3.connect(self.db).close()
        upgrade(self.db, MIGRATIONS)
        connection = sqlite3.connect(self.db)
        connection.executemany(
            "INSERT INTO asset(id,location,path,name,medium,code,catalog_title,field_owners) "
            "VALUES(?,'115',?,?,'video','WX17',?,?)",
            [(1, r"B:\番号\_未知厂牌\WX17\[mtfdz.club]WX17.3\Anna【20V】\Anna (1).mp4",
              "Anna (1).mp4", "新人！「澪」のコト、知ってください。", None),
             (2, r"B:\番号\_未知厂牌\WX17\[mtfdz.club]WX17.3\Anna【20V】\Anna (2).mp4",
              "Anna (2).mp4", "我自己起的名字", '{"catalog_title":"user:manual"}')])
        connection.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
            "VALUES(9,'tag','口交','口交',?,?)", (self.NOW, self.NOW))
        for asset_id in (1, 2):
            connection.execute(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source,metadata_json) "
                "VALUES(?,9,'tag','javinizer:javbus:tag',?)",
                (asset_id, '{"review_item":"WX-017:tags","provider_id":"WXSD-017"}'))
            connection.execute("INSERT INTO asset_tag(asset_id,tag,source) "
                               "VALUES(?,'口交','javinizer:javbus:tag')", (asset_id,))
        for key in ("WX-017:tags", "WX-017:title"):
            connection.execute(
                "INSERT INTO review_decision(category,item_key,status,note,updated_at) "
                "VALUES('metadata_fields',?,'approved','{\"source\":\"javbus\"}',?)",
                (key, self.NOW))
        connection.commit()
        connection.close()

    def _run(self, *extra):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return revert.main(["--db", str(self.db), *extra])

    def _read(self, sql, *params):
        connection = sqlite3.connect(self.db)
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    def test_a_dry_run_writes_nothing(self):
        self.assertEqual(self._run("--code", "WX17"), 0)
        self.assertEqual(self._read("SELECT count(*) FROM asset WHERE code='WX17'"), [(2,)])
        self.assertEqual(self._read("SELECT count(*) FROM asset_tag"), [(2,)])

    def test_everything_scraped_under_the_label_is_taken_back(self):
        self.assertEqual(self._run("--code", "WX17", "--apply",
                                   "--backup", str(self.root / "b.db")), 0)
        self.assertEqual(
            self._read("SELECT id,code,catalog_title,json_extract(field_owners,'$.code') "
                       "FROM asset ORDER BY id"),
            [(1, None, None, "user:manual"), (2, None, "我自己起的名字", "user:manual")])
        self.assertEqual(self._read("SELECT count(*) FROM asset_entity"), [(0,)])
        self.assertEqual(self._read("SELECT count(*) FROM asset_tag"), [(0,)])
        self.assertEqual(self._read("SELECT DISTINCT status FROM review_decision"),
                         [("rejected",)])
        self.assertEqual(self._read("SELECT count(*) FROM asset_search WHERE code<>''"), [(0,)])
        self.assertTrue((self.root / "b.db").exists())

    def test_a_real_code_or_one_with_release_evidence_is_refused(self):
        self.assertEqual(self._run("--code", "ABW-123"), 2)
        connection = sqlite3.connect(self.db)
        connection.execute("UPDATE asset SET studio='ワープ' WHERE id=1")
        connection.commit()
        connection.close()
        self.assertEqual(self._run("--code", "WX17", "--apply",
                                   "--backup", str(self.root / "b.db")), 2)
        self.assertEqual(self._read("SELECT count(*) FROM asset WHERE code='WX17'"), [(2,)])

    def _add_filename_misread(self, asset_id=3, name="dao01(1).mp4", code="DAO-001"):
        connection = sqlite3.connect(self.db)
        connection.execute(
            "INSERT INTO asset(id,location,path,name,medium,code,field_owners) "
            "VALUES(?,'115',?,?,'video',?,'{\"code\":\"scan:filename\"}')",
            (asset_id, rf"B:\创作者\Retsu_dao\{name}", name, code))
        connection.commit()
        connection.close()

    def test_one_refused_target_keeps_the_whole_batch_unwritten(self):
        self._add_filename_misread()
        self.assertEqual(self._run("--code", "WX17", "ABW-123", "--asset-id", "3", "--apply",
                                   "--backup", str(self.root / "b.db")), 2)
        self.assertEqual(self._read("SELECT count(*) FROM asset WHERE code IS NOT NULL"), [(3,)])

    def test_a_filename_misread_is_cleared_by_asset_id(self):
        self._add_filename_misread()
        self.assertEqual(self._run("--code", "WX17", "--asset-id", "3", "--apply",
                                   "--backup", str(self.root / "b.db")), 0)
        self.assertEqual(
            self._read("SELECT code,json_extract(field_owners,'$.code') FROM asset WHERE id=3"),
            [(None, "user:manual")])
        self.assertEqual(self._read("SELECT count(*) FROM asset WHERE code IS NOT NULL"), [(0,)])

    def test_an_asset_whose_file_name_still_yields_the_code_is_refused(self):
        self._add_filename_misread(name="ABW-123.mp4", code="ABW-123")
        self.assertEqual(self._run("--asset-id", "3"), 2)

    def test_a_review_key_shared_with_another_asset_is_refused(self):
        self._add_filename_misread(code="WX-017")
        connection = sqlite3.connect(self.db)
        connection.execute("UPDATE asset SET field_owners=NULL WHERE id=3")
        connection.commit()
        connection.close()
        self.assertEqual(self._run("--code", "WX17"), 2)

    def _add_wrong_source(self, r18dev_id="NTR-007", r18dev_content="1ntr00007"):
        """`348NTR-007` 去前缀查 r18dev 拿回了 DMM 的 `NTR-007`，那是另一个厂牌的另一部片。"""
        connection = sqlite3.connect(self.db)
        connection.execute(
            "INSERT INTO asset(id,location,path,name,medium,code,catalog_title,series,field_owners) "
            "VALUES(5,'115',?,'348NTR-007.mp4','video','348NTR-007','另一部片的片名','素人',"
            "'{\"series\":\"auto:avbase\"}')", (r"B:\番号\ナンパTV\348NTR-007.mp4",))
        connection.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
            "VALUES(?,'performer',?,?,?,?)",
            [(10, "凉川绚音", "凉川绚音", self.NOW, self.NOW),
             (11, "かなえ", "かなえ", self.NOW, self.NOW)])
        wrong = json.dumps({"review_item": "348NTR-007:performers", "provider_id": r18dev_id,
                            "content_id": r18dev_content})
        connection.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,metadata_json) "
            "VALUES(5,?,?,?,?)",
            [(10, "performer", "javinizer:r18dev:performer", wrong),
             (9, "tag", "javinizer:r18dev:tag", wrong),
             (11, "performer", "javinizer:mgstage:performer",
              '{"review_item":"348NTR-007:performers","provider_id":"348NTR-007"}')])
        connection.execute("INSERT INTO asset_tag(asset_id,tag,source) "
                           "VALUES(5,'口交','javinizer:r18dev:tag')")
        connection.executemany(
            "INSERT INTO review_decision(category,item_key,status,note,updated_at) "
            "VALUES('metadata_fields',?,'approved',?,?)",
            [("348NTR-007:title", '{"source":"r18dev"}', self.NOW),
             ("348NTR-007:series", '{"source":"r18dev"}', self.NOW),
             ("348NTR-007:performers", '{"source":"mgstage"}', self.NOW)])
        connection.commit()
        connection.close()

    def test_a_source_that_returned_another_release_is_taken_back_alone(self):
        self._add_wrong_source()
        self.assertEqual(self._run("--source-mismatch", "348NTR-007:r18dev", "--apply",
                                   "--backup", str(self.root / "b.db")), 0)
        self.assertEqual(self._read("SELECT code,catalog_title,series FROM asset WHERE id=5"),
                         [("348NTR-007", None, "素人")])
        self.assertEqual(self._read("SELECT source FROM asset_entity WHERE asset_id=5"),
                         [("javinizer:mgstage:performer",)])
        self.assertEqual(self._read("SELECT count(*) FROM asset_tag WHERE asset_id=5"), [(0,)])
        self.assertEqual(
            self._read("SELECT item_key,status FROM review_decision "
                       "WHERE item_key LIKE '348NTR-007:%' ORDER BY item_key"),
            [("348NTR-007:performers", "approved"), ("348NTR-007:series", "rejected"),
             ("348NTR-007:title", "rejected")])
        self.assertEqual(self._read("SELECT count(*) FROM asset_tag WHERE asset_id IN (1,2)"),
                         [(2,)])
        # 清空的片名不受保护：另一个来源给得出同一部片的片名时照常自动落库。
        self.assertEqual(
            self._read("SELECT json_extract(field_owners,'$.catalog_title') FROM asset WHERE id=5"),
            [("script:revert_misread_code",)])

    def test_fields_an_earlier_run_cleared_as_the_user_are_signed_back_to_the_script(self):
        """以 `user:manual` 清空的字段改签成脚本归属；只认驳回决定点到的、仍空着的那几格。"""
        self._add_wrong_source()
        connection = sqlite3.connect(self.db)
        connection.execute(
            "UPDATE asset SET catalog_title=NULL,field_owners=json_object("
            "'catalog_title','user:manual','series','user:manual') WHERE id=5")
        connection.execute(
            "UPDATE review_decision SET status='rejected',"
            "note='{\"reason\":\"r18dev 去前缀查 348NTR-007 交回的是另一部作品\"}' "
            "WHERE item_key IN ('348NTR-007:title','348NTR-007:series')")
        # 纯文本理由的驳回决定与这条无关，也不能让查询报错。
        connection.execute(
            "INSERT INTO review_decision(category,item_key,status,note,updated_at) "
            "VALUES('metadata_fields','ABW-001:title','rejected','片名不对',?)", (self.NOW,))
        connection.commit()
        connection.close()
        self.assertEqual(self._run("--release-owners"), 0)
        self.assertEqual(
            self._read("SELECT json_extract(field_owners,'$.catalog_title') FROM asset WHERE id=5"),
            [("user:manual",)])
        self.assertEqual(self._run("--release-owners", "--apply",
                                   "--backup", str(self.root / "b.db")), 0)
        # 系列仍有值（`素人`），那是用户写下的，不改签。
        self.assertEqual(
            self._read("SELECT catalog_title,json_extract(field_owners,'$.catalog_title'),"
                       "series,json_extract(field_owners,'$.series') FROM asset WHERE id=5"),
            [(None, "script:revert_misread_code", "素人", "user:manual")])

    def test_a_source_that_still_identifies_the_code_is_refused(self):
        self._add_wrong_source(r18dev_id="348NTR-007", r18dev_content="h_1348ntr00007")
        self.assertEqual(self._run("--source-mismatch", "348NTR-007:r18dev", "--apply",
                                   "--backup", str(self.root / "b.db")), 2)
        self.assertEqual(self._read("SELECT catalog_title FROM asset WHERE id=5"),
                         [("另一部片的片名",)])


_identity_spec = importlib.util.spec_from_file_location(
    "audit_scraped_identity", SCRIPT.with_name("audit_scraped_identity.py"))
identity = importlib.util.module_from_spec(_identity_spec)
_identity_spec.loader.exec_module(identity)


class ScrapedIdentityAuditTests(unittest.TestCase):
    """已落库的刮削归属按来源交回的编号重判身份，分错配、前缀未证实、来源无编号三档。"""

    def test_each_tier_is_told_apart(self):
        self.assertIsNone(identity.classify("ABW-123", {"id": "ABW-123"}))
        self.assertEqual(identity.classify("WX-017", {"id": "WXSD-017"}), identity.MISMATCH)
        self.assertIsNone(identity.classify("476MLA-234", {"id": "MLA-234", "content_id": "mla234"}))
        self.assertEqual(identity.classify("348NTR-007", {"id": "NTR-007", "content_id": "1ntr00007"}),
                         identity.PREFIX)
        self.assertEqual(identity.classify("SMBD-116", {"source_url":
                         "https://www.aventertainments.com/ppv/detail?pro=8853"}), identity.NO_ID)

    def test_the_review_csv_groups_links_and_leaves_no_id_rows_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ledger.db"
            sqlite3.connect(db).close()
            upgrade(db, MIGRATIONS)
            connection = sqlite3.connect(db)
            connection.executemany(
                "INSERT INTO asset(id,location,path,name,medium,code) VALUES(?,'115',?,?,'video',?)",
                [(1, r"B:\a\1.mp4", "1.mp4", "WX17"), (2, r"B:\a\2.mp4", "2.mp4", "WX17"),
                 (3, r"B:\a\3.mp4", "3.mp4", "SMBD-116")])
            connection.execute(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
                "VALUES(9,'tag','口交','口交','t','t')")
            meta = '{"review_item":"WX-017:tags","provider_id":"WXSD-017"}'
            connection.executemany(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source,metadata_json) "
                "VALUES(?,9,'tag',?,?)",
                [(1, "javinizer:javbus:tag", meta), (2, "javinizer:javbus:tag", meta),
                 (3, "javinizer:aventertainment:tag", '{"source_url":"https://x/?pro=8853"}')])
            connection.commit()
            connection.close()
            csv_path = Path(tmp) / "review.csv"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(identity.main(["--db", str(db), "--review-csv", str(csv_path)]), 0)
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual([(row["tier"], row["asset_code"], row["asset_ids"]) for row in rows],
                         [(identity.MISMATCH, "WX17", "1 2")])


if __name__ == "__main__":
    unittest.main()
