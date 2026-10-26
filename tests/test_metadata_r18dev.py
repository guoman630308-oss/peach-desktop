"""r18.dev 套进站点解析器契约：作品 JSON 的解析、combined 页的日文补全、payload 逐键钉住，以及失败分档。"""
import json
import unittest
from unittest.mock import Mock, patch

from peach.http import HttpResponse
from peach.jav_cover_fetch import R18_COMBINED, R18_DETAIL, NotFound, Unavailable
from peach.library_processing import LibraryMetadataProvider, describe_failure, is_missing
from peach.metadata import extract_catalog_evidence, extract_peach_fields, original_wording
from peach.scraping_access import SourcePaused
from peach.sources import R18DEV, FailureReason, Page, R18DevSource, Session, SourceFailure
from peach.sources.r18dev import ACTRESS_IMAGE, actresses, with_japanese

DETAIL_URL = "https://r18.dev/videos/vod/movies/detail/-/dvd_id=ABW-358/json"
COMBINED_URL = "https://r18.dev/videos/vod/movies/detail/-/combined=118abw358/json"
JACKET = "https://pics.dmm.co.jp/digital/video/118abw00358/118abw00358pl.jpg"
DETAIL = {"content_id": "118abw358", "dvd_id": "ABW-358", "title": "Remu Style", "release_date": "2023-05-26",
          "runtime_minutes": 210, "director": "Charlie Nakata", "maker": {"name": "Prestige"},
          "label": {"name": "ABSOLUTELY WONDERFUL"}, "series": {"name": "HOW TO SEX"},
          "images": {"jacket_image": JACKET},
          "actresses": [{"name": "Remu Suzumori"}], "categories": [{"name": "Slender"}, {"name": "Variety"}]}
COMBINED = {"content_id": "118abw358", "title_ja": "涼森れむ流", "series_name_ja": "保健室の先生",
            "label_name_ja": "ABSOLUTELY WONDERFUL", "maker_name_ja": "プレステージ",
            "actresses": [{"id": 1051912, "image_url": "suzumori_remu.jpg", "name_kanji": "涼森れむ",
                           "name_kana": "すずもりれむ", "name_romaji": "Remu Suzumori"}],
            "directors": [{"name_kanji": "チャーリー中田"}],
            "categories": [{"name_en": "Slender", "name_ja": "スレンダー"}, {"name_en": "Variety", "name_ja": "企画"}]}
JAPANESE_ACTRESS = {"japanese_name": "涼森れむ", "name_kana": "すずもりれむ", "name_romaji": "Remu Suzumori",
                    "dmm_id": 1051912, "thumb_url": "https://pics.dmm.co.jp/mono/actjpgs/suzumori_remu.jpg",
                    "profile_source": "r18dev"}
#: `LibraryMetadataProvider.query('ABW-358')` 对这份夹具交出的 dict，逐键钉住。`cover_urls` 是契约统一带的整列，
#: `cover_url` 仍是第一张；`raw` 与 `combined` 原样带着，候选与复核那一路认的就是这份形状。
EXPECTED = {
    "id": "ABW-358", "content_id": "118abw358", "source_url": DETAIL_URL, "title": "涼森れむ流", "maker": "Prestige",
    "series": "保健室の先生", "release_date": "2023-05-26", "director": "Charlie Nakata", "label": "ABSOLUTELY WONDERFUL",
    "runtime": 210, "cover_urls": [JACKET], "cover_url": JACKET, "actresses": [JAPANESE_ACTRESS],
    "genres": ["スレンダー", "企画"], "raw": DETAIL,
    "translations": [{"language": "ja", "title": "涼森れむ流", "series": "保健室の先生",
                      "label": "ABSOLUTELY WONDERFUL", "director": "チャーリー中田"}],
    "combined": COMBINED}


def serve(pages):
    calls = []

    def call(request, timeout, limit):
        calls.append((request.url, request.headers.get("Referer"), limit))
        body = pages.get(request.url)
        if isinstance(body, Exception):
            raise body
        return HttpResponse(200 if body is not None else 404, {}, body or b"", request.url)

    call.calls = calls
    return call


def encoded(data):
    return json.dumps(data, ensure_ascii=False).encode()


class R18DevSourceTests(unittest.TestCase):
    def test_the_config_and_the_two_urls_match_the_cover_layer(self):
        """封面层用同一站的同两页（`jav_cover_fetch.R18_DETAIL` / `R18_COMBINED`），地址要对得上。"""
        site = R18DevSource()
        self.assertIs(site.config, R18DEV)
        self.assertEqual((R18DEV.name, R18DEV.label, R18DEV.provider, R18DEV.stage, R18DEV.interval, R18DEV.cookie,
                          R18DEV.page_limit), ("r18dev", "r18.dev", "r18-json", "official_mirror", 2.0, False, 2 * 1024 * 1024))
        self.assertEqual(site.detail_url("ABW-358"), R18_DETAIL.format(code="ABW-358"))
        self.assertEqual(site.combined_url("118abw358"), R18_COMBINED.format(content_id="118abw358"))
        self.assertEqual(site.detail_url("ABW 358"), DETAIL_URL.replace("ABW-358", "ABW%20358"))

    def test_parse_reads_the_english_page_without_any_transport(self):
        record = R18DevSource().parse(Page(DETAIL_URL, encoded(DETAIL)), "ABW-358")
        self.assertEqual((record.source, record.provenance, record.code, record.source_url),
                         ("r18dev", "r18-json", "ABW-358", DETAIL_URL))
        self.assertEqual((record.title, record.studio, record.label, record.series, record.director,
                          record.release_date, record.runtime, record.cover_urls),
                         ("Remu Style", "Prestige", "ABSOLUTELY WONDERFUL", "HOW TO SEX", "Charlie Nakata",
                          "2023-05-26", 210, (JACKET,)))
        self.assertEqual(record.performers, ({"japanese_name": "Remu Suzumori"},))
        self.assertEqual(record.tags, ("Slender", "Variety"))
        self.assertEqual((record.extra["content_id"], record.extra["raw"]), ("118abw358", DETAIL))

    def test_parse_accepts_the_body_as_str_or_bytes_and_tolerates_missing_fields(self):
        """`_fetch` 交回 bytes；测试桩常给 str，两种都按 JSON 读。站上没给的字段留空，不是 `None`。"""
        sparse = {"content_id": "118abw358", "title": "Remu Style"}
        record = R18DevSource().parse(Page(DETAIL_URL, json.dumps(sparse)), "ABW-358")
        self.assertEqual((record.release_date, record.director, record.cover_urls, record.runtime, record.tags),
                         ("", "", (), None, ()))
        payload = record.payload()
        self.assertEqual((payload["release_date"], payload["director"], payload["cover_url"], payload["cover_urls"],
                          payload["genres"], payload["actresses"]), ("", "", "", [], [], []))

    def test_with_japanese_replaces_names_and_genres_and_keeps_the_brand_name_maker(self):
        record = R18DevSource().parse(Page(DETAIL_URL, encoded(DETAIL)), "ABW-358")
        japanese = with_japanese(record, COMBINED)
        self.assertEqual(japanese.performers, (JAPANESE_ACTRESS,))
        self.assertEqual(japanese.tags, ("スレンダー", "企画"))
        self.assertEqual(japanese.studio, "Prestige", "数据库厂牌实体用品牌名，日文写法会另起一个实体")
        self.assertEqual(japanese.extra["translations"], EXPECTED["translations"])
        self.assertIs(japanese.extra["combined"], COMBINED)
        self.assertEqual((japanese.title, japanese.series), ("涼森れむ流", "保健室の先生"))

    def test_a_combined_page_for_another_work_counts_as_no_japanese_page(self):
        """英文页的标题、系列与罗马字演员不交出去：落了库，系列与演员就成了英文的实体规范名。"""
        record = R18DevSource().parse(Page(DETAIL_URL, encoded(DETAIL)), "ABW-358")
        for combined in ({"content_id": "118xyz001", "title_ja": "别的片"}, ["not", "a", "dict"]):
            with self.subTest(combined=combined):
                bare = with_japanese(record, combined)
                self.assertEqual((bare.title, bare.series, bare.performers), ("", "", ()))
                self.assertEqual((bare.studio, bare.release_date, bare.cover_urls, bare.tags),
                                 (record.studio, record.release_date, record.cover_urls, record.tags))

    def test_an_empty_japanese_side_leaves_title_series_and_cast_empty(self):
        record = R18DevSource().parse(Page(DETAIL_URL, encoded(DETAIL)), "ABW-358")
        bare = with_japanese(record, {"content_id": "118abw358", "actresses": [{"name_kanji": ""}], "categories": []})
        self.assertEqual((bare.title, bare.series, bare.performers, bare.tags), ("", "", (), record.tags))
        self.assertEqual(bare.extra["translations"], [{"language": "ja", "title": "", "series": "", "label": "", "director": ""}])

    def test_actresses_build_the_official_avatar_only_from_a_bare_filename(self):
        rows = [{"id": 7, "image_url": "a.jpg", "name_kanji": "甲"}, {"id": 8, "image_url": "x/y.jpg", "name_romaji": "Otsu"},
                {"image_url": "", "name_kanji": "丙"}]
        found = actresses(rows)
        self.assertEqual([row["thumb_url"] for row in found], [ACTRESS_IMAGE.format(filename="a.jpg"), "", ""])
        self.assertEqual([row["japanese_name"] for row in found], ["甲", "", "丙"])
        self.assertEqual(found[1]["name_romaji"], "Otsu")
        self.assertEqual([row["dmm_id"] for row in found], [7, 8, ""])
        self.assertEqual(actresses(None), [])

    def test_query_asks_the_two_pages_with_the_site_referer_and_limit(self):
        transport = serve({DETAIL_URL: encoded(DETAIL), COMBINED_URL: encoded(COMBINED)})
        record = R18DevSource().query("ABW-358", session=Session(transport))
        self.assertEqual(transport.calls, [(DETAIL_URL, "https://r18.dev/", 2 * 1024 * 1024),
                                           (COMBINED_URL, "https://r18.dev/", 2 * 1024 * 1024)])
        self.assertEqual(record.payload(), EXPECTED)

    def test_the_provider_payload_equals_the_snapshot_shape_field_for_field(self):
        """`LibraryMetadataProvider.query` 走的就是这一站；现有用例按 `peach.jav_cover_fetch._fetch` 打桩，桩仍然生效。"""
        pages = lambda transport, url, **kwargs: json.dumps(COMBINED if "combined=" in url else DETAIL)
        provider = LibraryMetadataProvider.__new__(LibraryMetadataProvider)
        provider.transport = Mock()
        with patch("peach.jav_cover_fetch._fetch", side_effect=pages) as fetch:
            payload = provider.query("ABW-358")
        self.assertEqual(payload, EXPECTED)
        self.assertEqual([call.args[1] for call in fetch.call_args_list], [DETAIL_URL, COMBINED_URL])
        self.assertEqual(fetch.call_args_list[0].kwargs, {"referer": "https://r18.dev/", "limit": 2 * 1024 * 1024, "deadline": None})

    def test_without_the_japanese_page_only_the_language_neutral_fields_stand(self):
        english = dict(EXPECTED, title="", series="", actresses=[], genres=["Slender", "Variety"])
        english.pop("translations")
        english.pop("combined")
        for body in (Unavailable("HTTP 503"), NotFound("HTTP 404"), b"<html>maintenance</html>"):
            with self.subTest(body=body):
                transport = serve({DETAIL_URL: encoded(DETAIL), COMBINED_URL: body})
                self.assertEqual(R18DevSource().query("ABW-358", session=Session(transport)).payload(), english)
        with patch("peach.jav_cover_fetch.NETWORK_RETRY_DELAYS", ()):
            import httpx
            transport = serve({DETAIL_URL: encoded(DETAIL), COMBINED_URL: httpx.ConnectError("reset")})
            self.assertEqual(R18DevSource().query("ABW-358", session=Session(transport)).payload(), english)

    def test_failures_come_through_the_shared_reason_table(self):
        with self.assertRaises(SourceFailure) as caught:
            R18DevSource().query("ABW-358", session=Session(serve({})))
        self.assertEqual((caught.exception.reason, caught.exception.kind, str(caught.exception)),
                         (FailureReason.NOT_FOUND, "not_found", "HTTP 404"))
        with self.assertRaises(SourceFailure) as caught:
            R18DevSource().parse(Page(DETAIL_URL, encoded(dict(DETAIL, content_id="118abw359"))), "ABW-358")
        self.assertEqual((caught.exception.reason, caught.exception.kind, str(caught.exception)),
                         (FailureReason.PARSE_ERROR, "unavailable", "来源返回的番号不匹配"))
        for body in (b"<html>cloudflare</html>", b"[1, 2]"):
            with self.subTest(body=body), self.assertRaises(SourceFailure) as caught:
                R18DevSource().parse(Page(DETAIL_URL, body), "ABW-358")
            self.assertEqual((caught.exception.reason, str(caught.exception)),
                             (FailureReason.PARSE_ERROR, "r18.dev 返回的不是作品 JSON"))
        with self.assertRaises(SourceFailure) as caught:
            R18DevSource().query("ABW-358", session=Session(serve({DETAIL_URL: Unavailable("HTTP 503")})))
        self.assertEqual((caught.exception.reason, caught.exception.status_code), (FailureReason.SERVER_ERROR, 503))
        with self.assertRaises(SourcePaused):
            R18DevSource().query("ABW-358", session=Session(serve({DETAIL_URL: SourcePaused("来源正在冷却")})))
        with self.assertRaises(SourceFailure) as caught:
            R18DevSource().query("ABW-358", session=Session(serve({DETAIL_URL: Unavailable("本趟对外请求配额已用完")})))
        self.assertEqual((caught.exception.reason, str(caught.exception)), (FailureReason.NETWORK, "本趟对外请求配额已用完"))


class OriginalWordingTests(unittest.TestCase):
    """候选只取原文那一侧：r18 快照顶层的英文标题、系列与罗马字演员进不了候选。"""

    ENGLISH = {"source_url": DETAIL_URL, "title": "Remu Style", "maker": "Prestige",
               "series": "HOW TO SEX! The Infirmary Teacher", "release_date": "2023-05-26",
               "actresses": [{"japanese_name": "Remu Suzumori"}]}

    def test_an_empty_japanese_view_does_not_let_the_english_series_through(self):
        payload = dict(self.ENGLISH, translations=[{"language": "ja", "title": "", "series": ""}])
        fields = extract_peach_fields(payload)
        self.assertNotIn("series", fields)
        self.assertNotIn("title", fields)
        self.assertEqual(fields["studio"]["value"], "Prestige", "厂牌用品牌名，照旧取顶层")
        self.assertEqual(fields["release_date"]["value"], "2023-05-26")

    def test_an_english_page_snapshot_without_any_japanese_view_gives_no_names(self):
        """日文那一页没取到时的形态，磁盘缓存里也留着这种旧快照。"""
        fields = extract_peach_fields(self.ENGLISH)
        self.assertEqual({"title", "series", "performers"} & set(fields), set())
        self.assertNotIn("title", extract_catalog_evidence(self.ENGLISH))
        self.assertEqual(fields["studio"]["value"], "Prestige")
        mixed = dict(self.ENGLISH, actresses=[{"japanese_name": "胡桃さくら"}, {"japanese_name": "Remu Suzumori"}])
        self.assertEqual([row["name"] for row in extract_peach_fields(mixed)["performers"]["value"]], ["胡桃さくら"])

    def test_a_top_level_value_written_in_japanese_script_stays(self):
        payload = dict(self.ENGLISH, series="いつでも使えるオナホ後輩",
                       translations=[{"language": "ja", "series": ""}])
        self.assertEqual(original_wording(payload, "series"), "いつでも使えるオナホ後輩")
        self.assertEqual(extract_peach_fields(payload)["series"]["value"], "いつでも使えるオナホ後輩")

    def test_the_japanese_view_wins_and_other_sources_keep_their_top_level_values(self):
        payload = dict(self.ENGLISH, translations=[{"language": "ja", "title": "涼森れむ流", "series": "保健室の先生"}])
        self.assertEqual((original_wording(payload, "title"), original_wording(payload, "series")),
                         ("涼森れむ流", "保健室の先生"))
        javbus = {"source_url": "https://www.javbus.com/ABW-358", "title": "Remu Style", "series": "HOW TO SEX",
                  "actresses": [{"japanese_name": "涼森れむ"}]}
        fields = extract_peach_fields(javbus)
        self.assertEqual((fields["title"]["value"], fields["series"]["value"]), ("Remu Style", "HOW TO SEX"))
        self.assertEqual([row["name"] for row in fields["performers"]["value"]], ["涼森れむ"])


class FailureWordingTests(unittest.TestCase):
    """`is_missing` 与 `describe_failure` 对契约异常给出的分档与措辞，和传输层那两个异常一致。"""

    def test_is_missing_recognises_the_not_found_tier_only(self):
        self.assertTrue(is_missing(NotFound("HTTP 404")))
        self.assertTrue(is_missing(SourceFailure(FailureReason.NOT_FOUND, "AVBase 没有这个番号")))
        self.assertTrue(is_missing(SourceFailure(FailureReason.GONE, "已下架")))
        self.assertFalse(is_missing(SourceFailure(FailureReason.AUTH_REQUIRED, "javdb 要求登录")))
        self.assertFalse(is_missing(Unavailable("HTTP 503")))
        self.assertFalse(is_missing(ValueError("x")))

    def test_describe_failure_words_a_source_failure_like_an_unavailable(self):
        self.assertEqual(describe_failure(SourceFailure(FailureReason.SERVER_ERROR, "HTTP 503", status_code=503)),
                         "来源返回 HTTP 503")
        self.assertEqual(describe_failure(SourceFailure(FailureReason.AUTH_REQUIRED, "javdb 要求登录")), "javdb 要求登录")
        self.assertEqual(describe_failure(SourceFailure(FailureReason.PARSE_ERROR, "来源返回的番号不匹配")),
                         "来源返回的番号不匹配")
        self.assertEqual(describe_failure(Unavailable("HTTP 503")), "来源返回 HTTP 503")
        self.assertEqual(describe_failure(ValueError("x")), "处理出错（ValueError）")


if __name__ == "__main__":
    unittest.main()
