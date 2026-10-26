import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "match_babepedia_creators.py"
_spec = importlib.util.spec_from_file_location("match_babepedia_creators", SCRIPT)
match = importlib.util.module_from_spec(_spec)
sys.modules["match_babepedia_creators"] = match
_spec.loader.exec_module(match)


class _Response:
    def __init__(self, status, body):
        self.status = status
        self.body = body.encode("utf-8")


def _transport(pages):
    """pages: 查询串 -> (status, html)。未列出的查询按搜索结果页返回。"""
    def call(request, timeout, limit):
        import urllib.parse
        query = urllib.parse.unquote(request.url.rsplit("/", 1)[-1])
        status, html = pages.get(
            query, (200, "<title>Babepedia - Search results for 'x'</title>"))
        return _Response(status, html)
    return call


def profile(name, nude=True):
    word = "nude " if nude else ""
    return (200, f"<title>{name} - Free {word}pics, galleries & more at Babepedia</title>")


CHALLENGE = (429, "<title>Just a moment...</title>")


def spellings(name):
    return [variant for variant, _ in match.name_variants(name)]


class VariantTests(unittest.TestCase):
    def test_camel_case_is_split_into_words(self):
        self.assertIn("Sexy Saffron", spellings("SexySaffron"))

    def test_separators_become_spaces(self):
        self.assertIn("ruth lee", spellings("ruth_lee"))

    def test_original_spelling_is_tried_first(self):
        self.assertEqual(match.name_variants("MattieDoll")[0], ("MattieDoll", False))

    def test_trailing_digits_are_dropped_as_a_last_resort(self):
        self.assertIn(("banbi", True), match.name_variants("banbi_555"))

    def test_spacing_only_rewrites_are_lossless(self):
        lossy = dict(match.name_variants("SexySaffron"))
        self.assertFalse(lossy["SexySaffron"])
        self.assertFalse(lossy["Sexy Saffron"])

    def test_stub_shorter_than_the_floor_is_never_queried(self):
        # `G3104` 削成 `G` 后不指向任何人，只会撞上无关档案并招来限流。
        self.assertNotIn("G", spellings("G3104"))
        self.assertNotIn("N", spellings("N1032"))

    def test_variants_are_unique(self):
        variants = spellings("Shinaryen")
        self.assertEqual(len(variants), len(set(variants)))


class TitleClassificationTests(unittest.TestCase):
    def test_both_title_suffixes_count_as_a_profile(self):
        # `Free pics` 与 `Free nude pics` 都是真实档案；写死一种会漏掉一整类。
        for nude in (True, False):
            transport = _transport({"X": profile("Ruth Lee", nude=nude)})
            self.assertEqual(match.fetch_title(transport, "X"), "Ruth Lee")

    def test_search_results_page_means_no_profile_for_this_spelling(self):
        self.assertIsNone(match.fetch_title(_transport({}), "qqzzxxwweerr9988"))

    def test_rate_limit_is_never_reported_as_absence(self):
        # 这是 r18 那次 203 位假阴性的同一形状：限流必须抛错，不能返回 None。
        with self.assertRaises(match.RateLimited):
            match.fetch_title(_transport({"X": CHALLENGE}), "X")

    def test_unknown_page_shape_is_not_treated_as_absence(self):
        with self.assertRaises(match.RateLimited):
            match.fetch_title(_transport({"X": (200, "<title>whatever</title>")}), "X")


class ResolveTests(unittest.TestCase):
    def test_second_variant_resolves_when_the_first_spelling_misses(self):
        transport = _transport({"Sexy Saffron": profile("Saffron Bacchus")})
        verdict, variant, found, overlap = match.resolve(transport, "SexySaffron", 0)
        self.assertEqual(variant, "Sexy Saffron")
        self.assertEqual(found, "Saffron Bacchus")
        # 查询词 saffron 与档案名重合，算确认命中。
        self.assertEqual(verdict, match.VERDICT_HIT)
        self.assertGreater(overlap, 0)

    def test_alias_hop_with_no_shared_token_needs_a_human(self):
        # 别名跳转可能落到完全不同的名字上，这种不能自动当成已确认。
        transport = _transport({"Shinaryen": profile("Brianna Marchant")})
        verdict, _, found, overlap = match.resolve(transport, "Shinaryen", 0)
        self.assertEqual((verdict, found, overlap),
                         (match.VERDICT_REVIEW, "Brianna Marchant", 0.0))

    def test_all_variants_missing_is_a_confirmed_absence(self):
        verdict, _, found, _ = match.resolve(_transport({}), "luckydog22", 0)
        self.assertEqual((verdict, found), (match.VERDICT_NONE, ""))

    def test_persistent_rate_limit_reports_unavailable_not_absence(self):
        transport = _transport({"SexySaffron": CHALLENGE, "Sexy Saffron": CHALLENGE})
        verdict, _, _, _ = match.resolve(transport, "SexySaffron", 0, retries=2)
        self.assertEqual(verdict, match.VERDICT_BLOCKED)

    def test_lossy_variant_can_never_produce_a_confirmed_hit(self):
        # `fantia-3760310` 是站点作品号。削掉数字剩下的 `fantia` 会撞上艺名里
        # 含 Fantia 的人，词元重合度还够高——这正是它被误判成命中的真实过程。
        transport = _transport({"fantia": profile("Rio Hcup Fantia")})
        verdict, variant, found, overlap = match.resolve(
            transport, "fantia-3760310", 0)
        self.assertEqual((verdict, variant, found), (match.VERDICT_REVIEW,
                                                     "fantia", "Rio Hcup Fantia"))
        self.assertGreater(overlap, 0, "重合度仍如实记录，只是不再据此判定确认")

    def test_partial_name_resolves_to_the_full_stage_name(self):
        transport = _transport({"Shinaryen": profile("Tania Shinaryen")})
        verdict, _, found, _ = match.resolve(transport, "Shinaryen", 0)
        self.assertEqual((verdict, found), (match.VERDICT_HIT, "Tania Shinaryen"))


class TokenTests(unittest.TestCase):
    def test_short_fragments_do_not_create_false_overlap(self):
        self.assertNotIn("a", match.tokens("pandor_a"))

    def test_overlap_is_case_insensitive(self):
        self.assertTrue(match.tokens("ruth_lee") & match.tokens("Ruth Lee"))


if __name__ == "__main__":
    unittest.main()
