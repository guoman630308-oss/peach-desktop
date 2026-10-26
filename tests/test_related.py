import unittest

from peach import related
from peach.related import rank_related


def _closeness(left, right, weights):
    return related._closeness_features(related._feature(left), related._feature(right), weights)


def _reasons(source, candidate):
    return related._reasons_features(related._sets(source), related._sets(candidate))


def _naive_rank(source, candidates, limit, *, seed=None, diversity=0.22):
    """优化前的朴素 MMR：每选一条，就把整池和**全部**已选重算一遍 closeness。

    它留在测试里只有一个用途：当基准。增量维护「候选对已选集的最大
    closeness」是等价变换，一旦哪天有人把它改成不等价的写法（比如改成只和
    最后一条比、或者改成平均值），推荐顺序会静静地变，而不会报错。
    """
    pool = [dict(candidate) for candidate in candidates
            if int(candidate["id"]) != int(source["id"])]
    weights = related._idf([source, *pool])
    for candidate in pool:
        candidate["_relevance"] = _closeness(source, candidate, weights)
        candidate["_reasons"] = _reasons(source, candidate)
    pool = [candidate for candidate in pool
            if candidate["_relevance"] > 0 and candidate["_reasons"]]
    selected = []
    stable_seed = seed or str(source["id"])
    while pool and len(selected) < max(0, int(limit)):
        def key(candidate):
            redundancy = max(
                (_closeness(candidate, prior, weights) for prior in selected),
                default=0.0,
            )
            mmr = float(candidate["_relevance"]) - diversity * redundancy
            return (-mmr, related._stable_key(stable_seed, int(candidate["id"])))

        choice = min(pool, key=key)
        choice["ranking_score"] = round(-key(choice)[0], 4)
        pool.remove(choice)
        reasons = choice.pop("_reasons")
        relevance = choice.pop("_relevance")
        choice["why"] = " · ".join(reasons[:2])
        choice["related_score"] = round(relevance, 4)
        selected.append(choice)
    return selected


def _corpus(size=120):
    """往每个维度都撑出重叠的候选池。模数互质，于是同创作者、同厂牌、
    标签重叠和时长接近会交错出现，MMR 的冗余惩罚才真的会改变名次。"""
    rows = []
    for asset_id in range(2, 2 + size):
        rows.append({
            "id": asset_id,
            "entities": {
                "tag": {10 + asset_id % 7, 20 + asset_id % 5, 30 + asset_id % 11},
                "creator": {40 + asset_id % 3} if asset_id % 4 else set(),
                "performer": {50 + asset_id % 6},
                "studio": {60 + asset_id % 2},
                "series": {70} if asset_id % 9 == 0 else set(),
            },
            "year": 2010 + asset_id % 12,
            "duration": 600 + 37 * (asset_id % 20),
        })
    return rows


def _corpus_source():
    return {
        "id": 1,
        "entities": {"tag": {10, 20, 30}, "creator": {40}, "performer": {50},
                     "studio": {60}, "series": {70}},
        "year": 2016,
        "duration": 1200,
    }


class RelatedRankerTests(unittest.TestCase):
    def test_actual_mmr_score_contains_redundancy_penalty(self):
        source = {"id": 1, "entities": {"tag": {10}}}
        candidates = [{"id": aid, "entities": {"tag": {10}}} for aid in (2, 3)]
        first, second = rank_related(source, candidates, 2)
        self.assertEqual(first["ranking_score"], first["related_score"])
        self.assertAlmostEqual(second["ranking_score"], second["related_score"] - 0.22 * 0.55)

    def test_field_sort_covers_the_pool_and_keeps_missing_values_last(self):
        source = {"id": 1, "entities": {"tag": {10}}}
        candidates = [{"id": aid, "entities": {"tag": {10}}, "source_score": score}
                      for aid, score in ((2, 1.0), (3, None), (4, 4.5))]
        for descending, expected in ((True, [4, 2, 3]), (False, [2, 4, 3])):
            rows = rank_related(source, candidates, 3, order_by="source_score", descending=descending)
            self.assertEqual([row["id"] for row in rows], expected)

    def test_rare_shared_tag_beats_common_shared_tag(self):
        source = {"id": 1, "entities": {"tag": {10, 20}}}
        common = {"id": 2, "entities": {"tag": {10}}}
        rare = {"id": 3, "entities": {"tag": {20}}}
        background = [
            {"id": asset_id, "entities": {"tag": {10}}}
            for asset_id in range(4, 14)
        ]
        ranked = rank_related(source, [common, rare, *background], 2, seed="fixed")
        self.assertEqual(ranked[0]["id"], 3)
        self.assertEqual(ranked[0]["why"], "标签接近")

    def test_result_is_stable_and_mmr_adds_variety(self):
        source = {"id": 1, "entities": {"creator": {7}, "tag": {10, 11}}}
        candidates = [
            {"id": 2, "entities": {"creator": {7}, "tag": {10, 11}}},
            {"id": 3, "entities": {"creator": {7}, "tag": {10, 11}}},
            {"id": 4, "entities": {"creator": {7}, "tag": {10}}},
        ]
        first = rank_related(source, candidates, 3, seed="fixed", diversity=0.4)
        second = rank_related(source, candidates, 3, seed="fixed", diversity=0.4)
        self.assertEqual([row["id"] for row in first], [row["id"] for row in second])
        self.assertEqual(first[0]["why"], "同创作者 · 标签接近")

    def test_unrelated_items_are_excluded(self):
        source = {"id": 1, "entities": {"tag": {10}}}
        ranked = rank_related(source, [{"id": 2, "entities": {"tag": {99}}}], 10)
        self.assertEqual(ranked, [])


    def test_incremental_mmr_picks_exactly_what_the_naive_loop_picks(self):
        """这次优化只能改代价，不能改结果。

        逐条对整个返回字典，不只对 id：`why` 和 `related_score` 同样是契约，
        它们直接显示在“接着看”和 Mix 队列里。diversity=0 把 MMR 退化成纯排序，
        0.6 则让冗余惩罚大到足以改变名次；两端都要对得上才算等价。
        """
        source, corpus = _corpus_source(), _corpus()
        for limit in (0, 1, 3, 8, 25, 200):
            for diversity in (0.0, 0.22, 0.6):
                with self.subTest(limit=limit, diversity=diversity):
                    expected = _naive_rank(source, corpus, limit,
                                           seed="fixed", diversity=diversity)
                    actual = rank_related(source, corpus, limit,
                                          seed="fixed", diversity=diversity)
                    self.assertEqual(actual, expected)

    def test_mmr_cost_is_linear_in_limit(self):
        """守住优化的实际目的：代价对 limit 是线性，不是二次。

        旧写法在真实候选池（上限 4000）上 limit=28 要 6.2 秒、limit=60 要 28 秒，
        而悬浮 Mix 卡片和详情页“接着看”都卡在这一步。上界取 池×(limit+2)：
        增量实现宽松成立，任何「每轮重算全部已选」的写法当场超出。
        """
        source, corpus = _corpus_source(), _corpus()
        calls = 0
        original = related._closeness_features

        def counting(left, right, weights):
            nonlocal calls
            calls += 1
            return original(left, right, weights)

        related._closeness_features = counting
        try:
            picked = rank_related(source, corpus, 16, seed="fixed")
        finally:
            related._closeness_features = original
        self.assertEqual(len(picked), 16)
        self.assertLessEqual(calls, len(corpus) * 18,
                             f"closeness 调用 {calls} 次，已超出线性上界")


if __name__ == "__main__":
    unittest.main()
