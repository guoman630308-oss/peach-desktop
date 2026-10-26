"""追更变体判定的隔离测试。

标题取自 2026-08-25 实测的真实来源页：rule34video.com 的
`/models/lazyprocrastinator/`、kemono.cr 的 `fanbox/30917150` 帖子列表，以及 f95zone
`latest_data.php` 里 thread 50685 的记录。证据登记见 `docs/reference-sources.json`。
"""
import unittest
from dataclasses import dataclass

from peach.follow_variants import (
    PROVIDER_PRIORITY, VariantVerdict, classify, group_duplicates, normalize_handle,
)


ALIASES = ("LazyProcrastinator", "LazyProcrast", "Lazyprocastinator")


@dataclass(frozen=True)
class _Item:
    external_id: str
    provider: str
    release_key: str
    variant_kind: str
    published_at: str | None


class ClassifyTests(unittest.TestCase):
    def test_combined_resolution_and_frame_rate_are_version_markers(self):
        for spec, labels in (("4K60fps", "4K + 60fps"), ("1080p60fps", "1080p + 60fps"),
                             ("4K 60fps", "4K + 60fps")):
            for title in (f"Evening Movie ({spec})", f"Evening Movie {spec}"):
                with self.subTest(title=title):
                    verdict = classify(title)
                    self.assertEqual(verdict.release_key, "evening movie")
                    self.assertEqual(verdict.variant_label, labels)
        self.assertEqual(classify("Evening Movie Part 4K60fpsExtra").release_key,
                         "evening movie part 4k60fpsextra")

    def _key(self, title, **kwargs):
        return classify(title, creator_aliases=ALIASES, **kwargs)

    def test_alt_marker_groups_with_its_main_release(self):
        main = self._key("Fiona - Paizuri")
        alt = self._key("Fiona - Paizuri (Nude)")
        self.assertEqual(main.release_key, alt.release_key)
        self.assertEqual(main.variant_kind, "main")
        self.assertIsNone(main.variant_label)
        self.assertEqual(alt.variant_kind, "alt")
        self.assertEqual(alt.variant_label, "nude")

    def test_no_watermark_is_an_alt_not_a_separate_work(self):
        verdict = self._key("Bertha Riding (No Watermark)")
        self.assertEqual(verdict.variant_kind, "alt")
        self.assertEqual(verdict.variant_label, "no watermark")
        self.assertEqual(verdict.release_key, "bertha riding")
        unwatermarked = self._key("Juno Facial - 4K Unwatermarked")
        self.assertEqual(unwatermarked.release_key, "juno facial")
        self.assertEqual(unwatermarked.markers, ("4K", "no watermark"))

    def test_wip_markers_win_over_alt_markers(self):
        verdict = self._key("Mitsuru - School Movie [WIP] (nude)")
        self.assertEqual(verdict.variant_kind, "wip")
        self.assertEqual(verdict.variant_label, "WIP")
        self.assertIn("nude", verdict.markers)
        self.assertEqual(verdict.release_key, "mitsuru school movie")

    def test_bare_wip_token_is_recognized_without_brackets(self):
        self.assertEqual(self._key("Sayuri Cowgirl WIP").variant_kind, "wip")

    def test_trailing_digit_is_a_sequence_not_a_version(self):
        first = self._key("Sayuri - Cowgirl")
        second = self._key("Sayuri - Cowgirl 2")
        self.assertNotEqual(first.release_key, second.release_key)
        self.assertEqual(second.variant_kind, "main")

    def test_explicit_v2_is_an_alt_of_the_same_work(self):
        base = self._key("Riona Heartlily - Cowgirl")
        second = self._key("Riona Heartlily - Cowgirl v2")
        self.assertEqual(base.release_key, second.release_key)
        self.assertEqual(second.variant_label, "v2")

    def test_creator_handle_is_stripped_even_when_misspelled(self):
        tagged = self._key("Femboy Tighnari Footjob [Lazyprocrastinator]")
        misspelled = self._key("Femboy Tighnari Footjob (Lazyprocastinator)")
        bare = self._key("Femboy Tighnari Footjob")
        self.assertEqual(tagged.release_key, bare.release_key)
        self.assertEqual(misspelled.release_key, bare.release_key)

    def test_slash_separated_handle_group_is_stripped(self):
        verdict = classify(
            "Lazy Procrastinator Collection [2026-06-28] [LazyProcrastinator/LazyProcrast]",
            creator_aliases=ALIASES, semantics="release",
        )
        self.assertEqual(verdict.release_key, "lazy procrastinator collection")
        self.assertIsNone(verdict.version)

    def test_dated_collection_batches_are_not_versions(self):
        june = classify("Billyhhyb Collection [2026-06-28] [Billyhhyb]",
                        creator_aliases=ALIASES, semantics="release")
        july = classify("Billyhhyb Collection [2026-07-15] [Billyhhyb]",
                        creator_aliases=ALIASES, semantics="release")
        self.assertEqual(june.release_key, july.release_key)
        self.assertIsNone(june.version)
        self.assertIsNone(july.version)

    def test_work_semantics_keeps_date_brackets_in_the_key(self):
        june = self._key("Pack [2026-06-28]")
        july = self._key("Pack [2026-07-15]")
        self.assertNotEqual(june.release_key, july.release_key)
        self.assertIsNone(june.version)

    def test_release_semantics_group_successive_versions(self):
        june = classify("Lazy Procrastinator Collection", creator_aliases=ALIASES,
                        version="2026-06-28", semantics="release")
        august = classify("Lazy Procrastinator Collection", creator_aliases=ALIASES,
                          version="2026-08-24", semantics="release")
        self.assertEqual(june.release_key, august.release_key)
        self.assertNotEqual(june.version, august.version)

    def test_work_semantics_does_not_mine_versions_from_the_title(self):
        verdict = self._key("Mitsuru v3")
        self.assertIsNone(verdict.version)
        self.assertEqual(verdict.variant_label, "v3")

    def test_tag_list_bracket_is_kept_in_the_release_key(self):
        episode5 = self._key(
            "My Best Friend's Mom Is A Futa - Episode 5 [Anilingus, Light BDSM, Pegging JOI]")
        episode6 = self._key("My Best Friend's Mom Is A Futa - Episode 6 [Pegging JOI]")
        self.assertNotEqual(episode5.release_key, episode6.release_key)
        self.assertIn("anilingus", episode5.release_key)

    def test_brackets_do_not_change_the_key(self):
        with_parens = self._key("Cecilia - Belle Fingering (Zenless Zone Zero)")
        without = self._key("Cecilia - Belle Fingering Zenless Zone Zero")
        self.assertEqual(with_parens.release_key, without.release_key)

    def test_multi_marker_bracket_splits_on_commas(self):
        verdict = self._key("Yorha Commander (nude, 4k)")
        self.assertEqual(verdict.variant_kind, "alt")
        self.assertEqual(verdict.markers, ("nude", "4K"))
        self.assertEqual(verdict.release_key, "yorha commander")

    def test_resolution_and_framerate_labels_keep_their_number(self):
        self.assertEqual(self._key("Sayuri Handy [1080p]").variant_label, "1080p")
        self.assertEqual(self._key("Sayuri Handy [60fps]").variant_label, "60fps")

    def test_short_tokens_are_never_fuzzy_matched_as_handles(self):
        # riona/rinoa 只差一次换位，短 token 上模糊匹配必然误伤。
        self.assertIn("rinoa", self._key("Rinoa Heartilly").release_key)
        self.assertIn("riona", self._key("Riona Heartlily").release_key)

    def test_unknown_semantics_is_rejected(self):
        with self.assertRaises(ValueError):
            classify("x", semantics="whatever")

    def test_normalize_handle_folds_case_and_punctuation(self):
        self.assertEqual(normalize_handle("Lazy_Procrast!"), "lazyprocrast")


    def test_resolution_written_without_the_p_is_still_a_version(self):
        """`2B Camp [4K]` 与 `2B Camp [1080]` 是同一部片的两个版本。

        `[1080]` 此前留在 release_key 里（`2b camp 1080`），两张卡永远合不到一起，
        而 `[4K]` 那张的角标写着「2 个版本」，数的是 `[4K]` + `[WIP]`。
        只认已知分辨率档位：任意三四位数可能是年份或作品序号。
        """
        self.assertEqual(classify("2B Camp [4K]").release_key,
                         classify("2B Camp [1080]").release_key)
        self.assertEqual(classify("2B Camp [1080]").variant_label, "1080p")
        self.assertEqual(classify("Yuffie Photoshoot Part 3 [1080]").release_key,
                         classify("Yuffie Photoshoot Part 3 [4K]").release_key)
        # 不是分辨率的数字照旧留在键里。
        self.assertEqual(classify("Sayuri - Cowgirl 2").release_key, "sayuri cowgirl 2")
        self.assertEqual(classify("Scene [2023]").release_key, "scene 2023")
        self.assertEqual(classify("Part [1200]").release_key, "part 1200")


class GroupDuplicatesTests(unittest.TestCase):
    def test_cross_site_duplicates_fold_onto_one_primary(self):
        video = _Item("4542713", "rule34video", "fiona paizuri", "main", "2026-08-18")
        booru = _Item("998877", "rule34xxx", "fiona paizuri", "main", "2026-08-19")
        nude = _Item("4542721", "rule34video", "fiona paizuri", "alt", "2026-08-18")
        self.assertEqual(group_duplicates([booru, nude, video]), (video, video, video))

    def test_main_wins_over_alt_and_wip_regardless_of_provider(self):
        wip = _Item("1", "kemono", "mitsuru school movie", "wip", "2026-07-01")
        main = _Item("2", "f95zone", "mitsuru school movie", "main", "2026-08-01")
        self.assertEqual(group_duplicates([wip, main]), (main, main))

    def test_provider_priority_breaks_ties_between_equal_variants(self):
        self.assertLess(PROVIDER_PRIORITY["fanbox"], PROVIDER_PRIORITY["kemono"])
        self.assertLess(PROVIDER_PRIORITY["kemono"], PROVIDER_PRIORITY["rule34video"])
        low = _Item("1", "rule34video", "k", "main", "2026-08-01")
        high = _Item("2", "kemono", "k", "main", "2026-08-01")
        self.assertEqual(group_duplicates([low, high])[0], high)

    def test_items_without_a_release_key_are_skipped(self):
        orphan = _Item("1", "kemono", "", "main", None)
        self.assertEqual(group_duplicates([orphan]), (None,))

    def test_result_is_positionally_aligned_with_the_input(self):
        # 条目带 dict 字段就不可哈希，所以返回值必须按位置对齐，不能拿条目当键。
        keyed = _Item("1", "kemono", "k", "main", "2026-08-01")
        loose = _Item("2", "kemono", "", "main", "2026-08-01")
        self.assertEqual(group_duplicates([loose, keyed]), (None, keyed))

    def test_verdict_is_immutable(self):
        verdict = VariantVerdict("k", "main", None, None, ())
        with self.assertRaises(Exception):
            verdict.release_key = "other"


if __name__ == "__main__":
    unittest.main()
