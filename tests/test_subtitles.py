"""字幕 sidecar：配对判据、登记幂等、WebVTT 出口与播放器挂载。

配对这一段的判据全部只看文件名，所以用例也只给文件名：真实目录里那些名字长什么样，
这里就写什么样。转换那一段相反，喂的是字节而不是字符串——编码判定是它最容易出错的
一环，用 `str` 造样本等于把要测的东西先替掉了。
"""
from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path, PureWindowsPath

from peach import scan, subtitles
from support.ledger import fresh_ledger

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"


class PairingTests(unittest.TestCase):
    """同目录配对：三条判据按先后取第一个成立的，配不上就是孤立字幕。"""

    VIDEOS = ("ABP-758.mp4", "SSIS-001-4K.mp4")

    def test_the_same_stem_pairs_exactly(self):
        self.assertEqual(subtitles.pair("ABP-758.srt", self.VIDEOS),
                         ("ABP-758.mp4", subtitles.EXACT))

    def test_a_language_suffix_still_pairs_and_names_the_language(self):
        self.assertEqual(subtitles.pair("ABP-758.chs.srt", self.VIDEOS),
                         ("ABP-758.mp4", subtitles.SUFFIX))
        self.assertEqual(subtitles.language_of("ABP-758.chs.srt"), "zh-Hans")
        self.assertEqual(subtitles.language_of("ABP-758.cht.ass"), "zh-Hant")
        self.assertEqual(subtitles.language_of("ABP-758.jp.srt"), "ja")

    def test_a_version_suffix_pairs_without_inventing_a_language(self):
        """`-C` 是版次标记，`catalog_rules` 已经这样认定；它不构成语言证据。"""
        self.assertEqual(subtitles.pair("ABP-758-C.ass", self.VIDEOS),
                         ("ABP-758.mp4", subtitles.SUFFIX))
        self.assertEqual(subtitles.language_of("ABP-758-C.ass"), "")

    def test_the_same_release_code_pairs_when_the_names_differ(self):
        self.assertEqual(subtitles.pair("ssis001.srt", self.VIDEOS),
                         ("SSIS-001-4K.mp4", subtitles.CODE))

    def test_nothing_to_pair_with_is_recorded_as_orphan_instead_of_guessed(self):
        self.assertEqual(subtitles.pair("别人的字幕.srt", self.VIDEOS),
                         (None, subtitles.ORPHAN))

    def test_a_title_word_that_looks_like_a_language_is_not_read_as_one(self):
        """语言只看末尾 token：标题里的「日本」不是日语字幕的证据。"""
        self.assertEqual(subtitles.language_of("日本人妻.chs.srt"), "zh-Hans")
        self.assertEqual(subtitles.language_of("日本人妻.srt"), "")

    def test_several_matching_videos_resolve_to_a_reproducible_one(self):
        """同一判据下多个视频命中时取文件名排序最前的，不看哪个先被遍历到。"""
        both = ("ssis001.mp4", "SSIS-001.mp4")
        self.assertEqual(subtitles.pair("SSIS_001.srt", both),
                         ("SSIS-001.mp4", subtitles.CODE))
        self.assertEqual(subtitles.pair("SSIS_001.srt", both[::-1])[0], "SSIS-001.mp4")

    def test_a_graphic_subtitle_is_registered_but_not_playable(self):
        found = subtitles.directory_sidecars(
            PureWindowsPath(r"R:\media\x"),
            {"ABP-758.mp4": (10, "2026-09-01"), "ABP-758.sub": (3, "2026-09-01"),
             "ABP-758.idx": (1, "2026-09-01")},
            ["ABP-758.mp4"])
        self.assertEqual([item.name for item in found], ["ABP-758.sub"])
        self.assertEqual(found[0].format, "sub")
        self.assertFalse(subtitles.is_playable_format(found[0].format))
        self.assertEqual(found[0].path, r"R:\media\x\ABP-758.sub")

    def test_the_menu_label_falls_back_to_what_the_filename_adds(self):
        self.assertEqual(subtitles.track_label("ABP-758.chs.srt", "zh-Hans", "ABP-758.mp4"),
                         "简体中文")
        self.assertEqual(subtitles.track_label("ABP-758.director.srt", "", "ABP-758.mp4"),
                         "director")

    def test_a_subtitle_named_exactly_like_the_video_is_labelled_by_format(self):
        """完全同名时文件名一个字也没多说，菜单里写 `SRT` 比复述正片标题好读。
        """
        self.assertEqual(subtitles.track_label("ABP-758.srt", "", "ABP-758.mp4"), "SRT")
        self.assertEqual(subtitles.track_label("ABP-758.ass", "", "ABP-758.mp4"), "ASS")
        self.assertEqual(subtitles.track_label("别处的字幕.srt", "", "ABP-758.mp4"), "别处的字幕")


class LedgerTests(unittest.TestCase):
    """登记走真实 schema：手写表结构会和 `migrations/` 悄悄漂开。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.db = fresh_ledger(self.root)
        self.connection = sqlite3.connect(self.db)
        self.addCleanup(self.connection.close)
        self.connection.execute(
            "INSERT INTO asset(id,location,path,name,medium,first_seen,last_seen) "
            "VALUES(1,'local',?,'ABP-758.mp4','video','2026-09-01','2026-09-01')",
            (r"R:\media\x\ABP-758.mp4",))
        self.connection.commit()

    def _sidecar(self, name, video=r"R:\media\x\ABP-758.mp4", pairing=subtitles.EXACT):
        return subtitles.Sidecar(
            path=rf"R:\media\x\{name}", name=name, format=subtitles.subtitle_format(name),
            language=subtitles.language_of(name), pairing=pairing, video=video,
            size=120, mtime="2026-09-01")

    def test_a_paired_sidecar_lands_on_the_video_row(self):
        self.assertEqual(
            subtitles.record(self.connection, "local", [self._sidecar("ABP-758.srt")],
                             "2026-09-02 10:00:00"),
            (1, 0))
        rows = subtitles.subtitles_for(self.connection, 1)
        self.assertEqual([row[2] for row in rows], ["ABP-758.srt"])

    def test_a_video_missing_from_the_ledger_degrades_to_orphan(self):
        """指向不存在资产的 asset_id 没人会替我们拦住：运行时连接不开外键。"""
        sidecar = self._sidecar("ABP-758.srt", video=r"R:\media\x\缺席.mp4")
        self.assertEqual(
            subtitles.record(self.connection, "local", [sidecar], "2026-09-02 10:00:00"),
            (1, 1))
        row = self.connection.execute(
            "SELECT asset_id,pairing FROM asset_subtitle").fetchone()
        self.assertEqual(row, (None, subtitles.ORPHAN))

    def test_rescanning_the_same_path_updates_instead_of_duplicating(self):
        subtitles.record(self.connection, "local", [self._sidecar("ABP-758.srt")],
                         "2026-09-02 10:00:00")
        subtitles.record(self.connection, "local", [self._sidecar("ABP-758.srt")],
                         "2026-09-03 10:00:00")
        rows = self.connection.execute(
            "SELECT count(*),min(first_seen),max(last_seen) FROM asset_subtitle").fetchone()
        self.assertEqual(rows, (1, "2026-09-02 10:00:00", "2026-09-03 10:00:00"))

    def test_deleting_the_asset_takes_its_subtitle_rows_with_it(self):
        from peach.web_batch import ASSET_REFERENCE_TABLES
        self.assertIn("asset_subtitle", ASSET_REFERENCE_TABLES)
        subtitles.record(self.connection, "local", [self._sidecar("ABP-758.srt")],
                         "2026-09-02 10:00:00")
        # `PRAGMA foreign_keys` 在事务里是空操作，先提交再开，否则这条断言永远绿。
        self.connection.commit()
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("DELETE FROM asset WHERE id=1")
        self.assertEqual(
            self.connection.execute("SELECT count(*) FROM asset_subtitle").fetchone()[0], 0)


class ScanTests(unittest.TestCase):
    """扫描走账本形态的路径，配对与登记跟着遍历一起完成。"""

    DECLARED = {"local": (r"R:\media",)}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.db = fresh_ledger(self.root)
        self.media = self.root / "media"
        release = self.media / "ABP-758"
        release.mkdir(parents=True)
        for name, payload in (("ABP-758.mp4", b"0" * 16), ("ABP-758.chs.srt", b"1" * 4),
                              ("ABP-758.cht.srt", b"2" * 4), ("别人的字幕.ass", b"3" * 4)):
            (release / name).write_bytes(payload)

    def _scan(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = scan.scan_location(
                self.db, "local", r"R:\media", declared_roots=self.DECLARED,
                mounts={"local": (self.media,)}, windows=False)
        return result, output.getvalue()

    def _rows(self):
        connection = sqlite3.connect(self.db)
        try:
            return connection.execute(
                "SELECT s.path,s.language,s.format,s.pairing,a.name "
                "FROM asset_subtitle s LEFT JOIN asset a ON a.id=s.asset_id "
                "ORDER BY s.path").fetchall()
        finally:
            connection.close()

    def test_sidecars_are_registered_against_the_video_in_the_same_directory(self):
        result, output = self._scan()
        self.assertEqual((result.subtitles, result.orphan_subtitles), (3, 1))
        self.assertIn("字幕 3 条（孤立 1 条）", output)
        self.assertEqual(self._rows(), [
            (r"R:\media\ABP-758\ABP-758.chs.srt", "zh-Hans", "srt",
             subtitles.SUFFIX, "ABP-758.mp4"),
            (r"R:\media\ABP-758\ABP-758.cht.srt", "zh-Hant", "srt",
             subtitles.SUFFIX, "ABP-758.mp4"),
            (r"R:\media\ABP-758\别人的字幕.ass", "", "ass", subtitles.ORPHAN, None),
        ])

    def test_the_subtitle_file_keeps_its_own_asset_row(self):
        """字幕文件自己那一行仍然是 `other`：它说的是「磁盘上有这个文件」。"""
        self._scan()
        connection = sqlite3.connect(self.db)
        try:
            medium = connection.execute(
                "SELECT medium FROM asset WHERE path=?",
                (r"R:\media\ABP-758\ABP-758.chs.srt",)).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(medium, "other")
        self.assertEqual(scan.medium_of("x.srt"), "other")

    def test_rescanning_keeps_one_row_per_path(self):
        self._scan()
        self._scan()
        self.assertEqual(len(self._rows()), 3)


class DecodeTests(unittest.TestCase):
    def test_a_bom_names_the_encoding(self):
        self.assertEqual(subtitles.decode("台词".encode("utf-8-sig")), "台词")
        self.assertEqual(subtitles.decode("台词".encode("utf-16")), "台词")

    def test_common_legacy_encodings_are_tried_in_order(self):
        """顺序就是判据本身：同一串字节常常在几种编码下都「解得出」，只是其中几个是乱码。

        GBK 与 Shift_JIS 的双字节区大面积重叠，日文假名的 Shift_JIS 字节多半也能按 GBK
        读成汉字。这个库里中文字幕远多于日文字幕，所以 GBK 排在前面；判据写死在
        `ENCODINGS` 里，不靠统计猜。半角片假名是 Shift_JIS 单字节区，GBK 认不出来，
        于是它是真的落到最后一档。
        """
        self.assertEqual(subtitles.ENCODINGS, ("utf-8", "gbk", "big5", "shift_jis"))
        self.assertEqual(subtitles.decode("简体台词".encode("gbk")), "简体台词")
        self.assertEqual(subtitles.decode("繁體台詞".encode("big5")), "繁體台詞")
        self.assertEqual(subtitles.decode("ﾃｽﾄ字幕".encode("shift_jis")), "ﾃｽﾄ字幕")

    def test_bytes_no_encoding_explains_are_refused_instead_of_mangled(self):
        # 0x81 在 GBK 与 Shift_JIS 里都是双字节引导位，后面跟 0x20 两边都不成立；
        # Big5 与 UTF-8 连引导位都不认。
        with self.assertRaises(subtitles.SubtitleUndecodable):
            subtitles.decode(b"\x81\x20\x81\x20")


class ConversionTests(unittest.TestCase):
    SRT = (
        "1\n"
        "00:00:01,500 --> 00:00:03,250\n"
        "<i>第一句</i>\n"
        "\n"
        "2\n"
        "0:00:04,000 --> 0:00:05,000\n"
        '<font color="#ffffff">第二句</font>\n'
    )
    ASS = (
        "[Script Info]\nTitle: 示例\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname\nStyle: 默认,黑体\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Actor, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:02.50,0:00:04.00,默认,,0,0,0,,{\\an8}上面一句，带逗号\\N第二行\n"
        "Comment: 0,0:00:09.00,0:00:10.00,默认,,0,0,0,,这一行不是对白\n"
    )

    def test_srt_timestamps_become_dots_and_the_sequence_number_goes_away(self):
        vtt = subtitles.srt_to_vtt(self.SRT)
        self.assertTrue(vtt.startswith("WEBVTT\n\n"))
        self.assertIn("00:00:01.500 --> 00:00:03.250", vtt)
        self.assertIn("00:00:04.000 --> 00:00:05.000", vtt)
        self.assertNotIn("\n1\n", vtt)

    def test_srt_keeps_basic_tags_and_drops_the_ones_webvtt_cannot_draw(self):
        vtt = subtitles.srt_to_vtt(self.SRT)
        self.assertIn("<i>第一句</i>", vtt)
        self.assertIn("第二句", vtt)
        self.assertNotIn("font", vtt)

    def test_ass_reads_the_field_order_and_keeps_commas_inside_the_text(self):
        vtt = subtitles.ass_to_vtt(self.ASS)
        self.assertIn("00:00:02.500 --> 00:00:04.000", vtt)
        self.assertIn("上面一句，带逗号\n第二行", vtt)

    def test_ass_drops_styling_and_lines_that_are_not_dialogue(self):
        vtt = subtitles.ass_to_vtt(self.ASS)
        self.assertNotIn("an8", vtt)
        self.assertNotIn("这一行不是对白", vtt)

    def test_vtt_goes_out_as_it_came_in(self):
        source = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n台词\n"
        self.assertEqual(subtitles.vtt_passthrough(source), source)
        self.assertTrue(subtitles.vtt_passthrough("00:00:01.000 --> 00:00:02.000\n台词\n")
                        .startswith("WEBVTT\n\n"))

    def test_a_graphic_subtitle_refuses_to_become_a_text_track(self):
        with self.assertRaises(subtitles.SubtitleUndecodable):
            subtitles.to_webvtt(b"anything", "sub")


class SubtitleApiTests(unittest.TestCase):
    """API 走真实 app：鉴权、路径闸门和 404/415 都在这一层才成立。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.db = fresh_ledger(self.root)
        self.media = self.root / "media"
        self.media.mkdir()
        self.video = self.media / "ABP-758.mp4"
        self.video.write_bytes(b"0" * 8)
        (self.media / "ABP-758.chs.srt").write_bytes(
            "1\n00:00:01,000 --> 00:00:02,000\n简体台词\n".encode("gbk"))
        (self.media / "ABP-758.jp.srt").write_bytes(b"\x81\x20\x81\x20")
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "ABP-758.en.srt").write_text("WEBVTT\n", encoding="utf-8")
        connection = sqlite3.connect(self.db)
        connection.execute(
            "INSERT INTO asset(id,location,path,name,medium,first_seen,last_seen) "
            "VALUES(1,'local',?,'ABP-758.mp4','video','2026-09-01','2026-09-01')",
            (str(self.video),))
        for name, language, directory in (
            ("ABP-758.chs.srt", "zh-Hans", self.media),
            ("ABP-758.en.srt", "en", self.outside),
            ("ABP-758.jp.srt", "ja", self.media),
        ):
            connection.execute(
                "INSERT INTO asset_subtitle(asset_id,location,path,name,language,format,"
                "size,mtime,pairing,first_seen,last_seen) "
                "VALUES(1,'local',?,?,?,'srt',40,'2026-09-01','suffix',"
                "'2026-09-01','2026-09-01')",
                (str(directory / name), name, language))
        connection.commit()
        connection.close()

    def _client(self):
        from support.web import route_client
        from peach.api import create_app
        from peach.config import PeachSettings
        return route_client(create_app(PeachSettings(
            db_path=self.db, configured=True, token="",
            allowed_media_roots=(self.media,))))

    def test_the_list_names_every_track_and_why_one_cannot_play(self):
        with self._client() as client:
            payload = client.get("/api/assets/1/subtitles").json()
        tracks = payload["subtitles"]
        self.assertEqual([track["name"] for track in tracks],
                         ["ABP-758.chs.srt", "ABP-758.en.srt", "ABP-758.jp.srt"])
        self.assertEqual([track["index"] for track in tracks], [0, 1, 2])
        self.assertEqual(tracks[0]["label"], "简体中文")
        self.assertEqual(tracks[0]["src"], "/api/assets/1/subtitles/0")
        self.assertTrue(tracks[0]["playable"])
        self.assertEqual(tracks[1]["note"], "不在正片所在目录")
        self.assertEqual(tracks[2]["note"], "编码未识别")
        self.assertFalse(tracks[1]["playable"] or tracks[2]["playable"])

    def test_a_track_comes_back_as_webvtt(self):
        with self._client() as client:
            response = client.get("/api/assets/1/subtitles/0")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/vtt"))
        self.assertIn("00:00:01.000 --> 00:00:02.000", response.text)
        self.assertIn("简体台词", response.text)

    def test_a_path_outside_the_video_directory_is_refused(self):
        with self._client() as client:
            self.assertEqual(client.get("/api/assets/1/subtitles/1").status_code, 404)

    def test_an_unreadable_encoding_answers_415(self):
        with self._client() as client:
            response = client.get("/api/assets/1/subtitles/2")
        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()["error"], "编码未识别")

    def test_an_index_past_the_end_and_a_missing_asset_both_404(self):
        with self._client() as client:
            self.assertEqual(client.get("/api/assets/1/subtitles/9").status_code, 404)
            self.assertEqual(client.get("/api/assets/404/subtitles").status_code, 404)

    def test_the_endpoints_require_the_same_token_as_the_rest(self):
        from support.web import route_client
        from peach.api import create_app
        from peach.config import PeachSettings
        app = create_app(PeachSettings(
            db_path=self.db, configured=True, token="secret",
            allowed_media_roots=(self.media,)))
        with route_client(app) as client:
            self.assertEqual(client.get("/api/assets/1/subtitles").status_code, 401)
            self.assertEqual(
                client.get("/api/assets/1/subtitles/0?t=secret").status_code, 200)


if __name__ == "__main__":
    unittest.main()
