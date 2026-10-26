"""一本道官网作品 JSON 的取页与解析。"""
import json
import unittest

from peach.http import HttpResponse
from peach.sources import FailureReason, Page, Session, SourceFailure
from peach.sources.onepondo import ONEPONDO, STUDIO, OnePondoSource, movie_id, names_this_studio

COVER = "https://www.1pondo.tv/moviepages/112312_478/images/str.jpg"
DETAIL = "https://www.1pondo.tv/dyn/phpauto/movie_details/movie_id/112312_478.json"


def details(movie="112312_478", title="裸演奏 〜第5回演奏会・ホルン〜", release="2012-11-23",
            duration=3874, series="裸演奏", actresses=("飯岡かなこ",), english=("Kanako Iioka",),
            tags=("AV女優", "スレンダー", "中出し", "720p"), thumbs=True):
    raw = {"MovieID": movie, "Title": title, "Release": release, "Duration": duration,
           "Desc": "楽器とエロスのハーモニー。", "DescEn": None,
           "Series": series, "SeriesEn": "Hadaka Enso",
           "Actor": actresses[0] if actresses else "",
           "ActressesJa": list(actresses), "ActressesEn": list(english),
           "UCNAME": list(tags), "SiteID": 2470, "Status": True,
           "MovieThumb": f"https://www.1pondo.tv/moviepages/{movie}/images/thum_b.jpg"}
    if thumbs:
        for field in ("ThumbLow", "ThumbMed", "ThumbHigh", "ThumbUltra"):
            raw[field] = f"https://www.1pondo.tv/moviepages/{movie}/images/str.jpg"
    return json.dumps(raw, ensure_ascii=False)


def parse_details(body, code):
    return OnePondoSource().parse(Page(DETAIL, body.encode()), code).payload()


def serve(pages):
    calls = []

    def call(request, timeout, limit):
        calls.append((request.url, request.headers.get("Referer"), limit))
        body = pages.get(request.url)
        return HttpResponse(200 if body is not None else 404, {}, (body or "").encode(), request.url)

    call.calls = calls
    return call


class OnePondoCodeTests(unittest.TestCase):
    def test_the_shop_writes_the_code_with_an_underscore(self):
        # 分隔符是片商标识，账本两种写法都有：文件名带进来的那个可能是连字号。
        self.assertEqual(movie_id("112312_478"), "112312_478")
        self.assertEqual(movie_id("092415-001"), "092415_001")
        self.assertEqual(OnePondoSource().detail_url("092415-001"),
                         "https://www.1pondo.tv/dyn/phpauto/movie_details/movie_id/092415_001.json")

    def test_codes_that_are_not_dated_ask_nowhere(self):
        # `125735_816` 是手机录像 `VID_20220818_125735_816.mp4` 剪出来的，12 月 57 日不存在。
        for code in ("ABW-358", "FC2-PPV-4364209", "n1234", "125735-816", "", None):
            self.assertEqual(movie_id(code), "", repr(code))
            self.assertEqual(OnePondoSource().detail_url(code), "", repr(code))
        transport = serve({})
        with self.assertRaises(SourceFailure) as caught:
            OnePondoSource().query("ABW-358", session=Session(transport))
        self.assertEqual((caught.exception.reason, str(caught.exception)),
                         (FailureReason.NOT_FOUND, "这个番号不是一本道的写法"))
        self.assertEqual(transport.calls, [])

    def test_only_local_evidence_names_this_studio(self):
        # 一本道与カリビアンコム 的番号同形，拿另一家的番号去问，答回来的是同一天发行的
        # 另一部片。所以问不问它看本机手上的路径、文件名与账本厂牌。
        self.assertTrue(names_this_studio("B:/云下载/[JAV] [Uncensored] 1pon 112312_478/x.mp4", "", ""))
        self.assertTrue(names_this_studio("B:/MVP/1pondo-092415_159-001-FHD/x.mp4", "x.mp4", None))
        self.assertTrue(names_this_studio("", "", "一本道"))
        self.assertFalse(names_this_studio("B:/云下载/Carib-040221-001-FHD/x.mp4", "x.mp4", "カリビアンコム"))
        self.assertFalse(names_this_studio())


class OnePondoDetailTests(unittest.TestCase):
    def test_the_work_json_gives_every_field_the_review_card_shows(self):
        found = parse_details(details(), "112312_478")
        self.assertEqual(found["id"], "112312_478")
        self.assertEqual(found["content_id"], "112312_478")
        self.assertEqual(found["source_url"], "https://www.1pondo.tv/movies/112312_478/")
        self.assertEqual(found["title"], "裸演奏 〜第5回演奏会・ホルン〜")
        self.assertEqual(found["release_date"], "2012-11-23")
        self.assertEqual(found["maker"], STUDIO)
        self.assertEqual(found["series"], "裸演奏")
        self.assertEqual(found["genres"], ["AV女優", "スレンダー", "中出し", "720p"])
        self.assertEqual(found["cover_url"], COVER)
        self.assertEqual(found["cover_urls"], [COVER])

    def test_the_whole_payload_matches_the_snapshot_shape_key_by_key(self):
        self.assertEqual(parse_details(details(), "112312_478"), {
            "id": "112312_478", "content_id": "112312_478", "source_url": "https://www.1pondo.tv/movies/112312_478/",
            "title": "裸演奏 〜第5回演奏会・ホルン〜", "description": "楽器とエロスのハーモニー。",
            "actresses": [{"japanese_name": "飯岡かなこ", "name_romaji": "Kanako Iioka"}],
            "maker": "一本道", "label": "", "series": "裸演奏", "director": "", "release_date": "2012-11-23",
            "runtime": 64.57, "genres": ["AV女優", "スレンダー", "中出し", "720p"],
            "cover_urls": [COVER], "cover_url": COVER})

    def test_the_runtime_arrives_in_seconds_and_lands_in_minutes(self):
        self.assertEqual(parse_details(details(), "112312_478")["runtime"], 64.57)
        self.assertIsNone(parse_details(details(duration=0), "112312_478")["runtime"])
        self.assertIsNone(parse_details(details(duration=None), "112312_478")["runtime"])

    def test_the_japanese_name_is_the_identity_and_the_romaji_follows_as_an_alias(self):
        found = parse_details(details(actresses=("白咲碧", "陽菜"), english=("Aoi Shirosaki", "Hina")),
                              "112312_478")
        self.assertEqual(found["actresses"],
                         [{"japanese_name": "白咲碧", "name_romaji": "Aoi Shirosaki"},
                          {"japanese_name": "陽菜", "name_romaji": "Hina"}])

    def test_a_work_with_only_the_single_actor_field_still_names_her(self):
        found = parse_details(details(actresses=("飯岡かなこ",), english=()), "112312_478")
        self.assertEqual(found["actresses"], [{"japanese_name": "飯岡かなこ", "name_romaji": ""}])

    def test_another_works_json_is_not_this_ones_data(self):
        # 番号同形的另一家片子问到这里时，官网回的作品号对不上，那份资料一个字都不能用。
        for body, code, wording in ((details(movie="092415_001"), "112312_478", "一本道回的作品号对不上这个番号"),
                                    ("null", "112312_478", "一本道回的作品号对不上这个番号"),
                                    (details(), "ABW-358", "这个番号不是一本道的写法")):
            with self.subTest(code=code, body=body[:30]), self.assertRaises(SourceFailure) as caught:
                parse_details(body, code)
            self.assertEqual((caught.exception.reason, str(caught.exception)), (FailureReason.NOT_FOUND, wording))

    def test_a_body_that_is_not_json_is_a_changed_site_not_a_missing_work(self):
        with self.assertRaises(SourceFailure) as caught:
            parse_details("<html>维护中</html>", "112312_478")
        self.assertEqual((caught.exception.reason, str(caught.exception)),
                         (FailureReason.PARSE_ERROR, "一本道返回的不是作品 JSON"))

    def test_the_cover_falls_back_to_the_list_thumb_when_the_still_is_missing(self):
        found = parse_details(details(thumbs=False), "112312_478")
        self.assertEqual(found["cover_url"],
                         "https://www.1pondo.tv/moviepages/112312_478/images/thum_b.jpg")
        self.assertEqual(ONEPONDO.name, "1pondo")


class OnePondoQueryTests(unittest.TestCase):
    def test_the_json_is_asked_on_the_shop_host_with_its_own_page_limit(self):
        transport = serve({DETAIL: details()})
        found = OnePondoSource().query("112312-478", session=Session(transport))
        self.assertEqual(found.title, "裸演奏 〜第5回演奏会・ホルン〜")
        self.assertEqual(transport.calls, [(DETAIL, "https://www.1pondo.tv/", 1024 * 1024)])

    def test_a_withdrawn_work_answers_404_and_reads_as_absent(self):
        with self.assertRaises(SourceFailure) as caught:
            OnePondoSource().query("112312_478", session=Session(serve({})))
        self.assertEqual((caught.exception.kind, str(caught.exception)), ("not_found", "一本道站上没有这部片"))


if __name__ == "__main__":
    unittest.main()
