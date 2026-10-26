"""映射表改了去向之后重投影已落库的标签：只套这次的映射差。"""
import importlib.util
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from support.ledger import fresh_ledger

from peach.metadata_auto_apply import _apply_metadata_candidate

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reproject_snapshot_tags.py"
_spec = importlib.util.spec_from_file_location("reproject_snapshot_tags", SCRIPT)
reproject = importlib.util.module_from_spec(_spec)
sys.modules["reproject_snapshot_tags"] = reproject
_spec.loader.exec_module(reproject)


def mapping(table: dict[str, str]) -> types.SimpleNamespace:
    """一版只认 `table` 里这些词的映射，代替从 git 取出来的旧模块。"""
    def map_genres(genres, _decisions=None):
        return list(dict.fromkeys(table[word] for word in genres if word in table)), []
    return types.SimpleNamespace(map_genres=map_genres)


#: 改动之前：`巨尻` 并进美臀，`可愛い` 并进高颜值。
BEFORE = mapping({"巨尻": "美臀", "美尻": "美臀", "可愛い": "高颜值", "美少女": "高颜值",
                  "中出し": "中出内射"})


class ReprojectTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = fresh_ledger(self.root)
        self.connection = sqlite3.connect(self.db)
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)

    def land(self, asset_id: int, code: str, genres: list[str], tags: list[str]) -> None:
        """按 `/review` 批准的形状落一组标签，快照里记着来源原词。"""
        snapshot = self.root / f"{code}.json"
        snapshot.write_text(json.dumps({"result": {"id": code, "genres": genres}}), encoding="utf-8")
        self.connection.execute(
            "INSERT INTO asset(id,location,path,name,medium,code) VALUES(?,?,?,?,'video',?)",
            (asset_id, "local", f"R:\\media\\{code}.mp4", code, code))
        _apply_metadata_candidate(
            self.connection, {"field": "tags", "code": code, "item_key": f"{code}:tags"},
            {"source": "javbus", "candidate_key": f"{code}:tags:javbus:x", "confidence": 0.9,
             "provider_id": code, "raw_snapshot": str(snapshot), "value": tags},
            "2026-09-01T00:00:00+00:00", "review:javbus")
        self.connection.commit()

    def tags_of(self, asset_id: int) -> list[str]:
        return sorted(row[0] for row in self.connection.execute(
            "SELECT tag FROM asset_tag WHERE asset_id=?", (asset_id,)))

    def run_script(self, *extra: str, since: bool = True) -> None:
        args = reproject.build_parser().parse_args(
            [*(["--since", "old"] if since else []), "--db", str(self.db),
             "--review-csv", str(self.root / "reproject.csv"), *extra])
        with mock.patch.object(reproject, "taxonomy_at", return_value=BEFORE):
            self.assertEqual(reproject.run(args), 0)

    def fill(self) -> None:
        self.run_script("--apply", "--backup", str(self.root / "before.db"), since=False)

    def sources_of(self, asset_id: int) -> dict[str, str]:
        return dict(self.connection.execute(
            "SELECT tag,source FROM asset_tag WHERE asset_id=?", (asset_id,)).fetchall())

    def test_fill_adds_what_the_snapshot_maps_to_now(self):
        """NHDTB-455：批准时的映射认不得 `Hard Sex`、`Big Asses`、`Anal Sex`，账本只剩潮吹。"""
        self.land(7, "NHDTB-455", ["Hard Sex", "Big Asses", "Squirting", "Anal Sex", "Hi-Def"], ["潮吹"])
        self.fill()
        self.assertEqual(self.tags_of(7), ["巨臀", "潮吹", "肛交", "调教"])
        self.assertTrue(self.sources_of(7)["巨臀"].startswith("auto:metadata-tags@"))
        self.assertEqual(self.sources_of(7)["潮吹"], "javinizer:javbus:tag")

    def test_fill_never_removes_and_never_duplicates_another_source(self):
        self.land(8, "ABC-008", ["巨尻", "中出し"], ["自慰"])
        self.connection.execute(
            "INSERT INTO asset_tag(asset_id,tag,confidence,source) VALUES(8,'中出内射',1.0,'web-user')")
        self.connection.commit()
        self.fill()
        self.assertEqual(self.tags_of(8), ["中出内射", "巨臀", "自慰"])
        self.assertEqual(self.sources_of(8)["中出内射"], "web-user")

    def test_a_hidden_tag_is_not_brought_back(self):
        """页面删标签只记隐藏、行还在，补齐按行看，不会把它补回来再显示。"""
        self.land(9, "ABC-009", ["巨尻", "中出し"], ["中出内射"])
        self.connection.execute(
            "INSERT INTO asset_tag_preference(profile_id,asset_id,normalized_tag,hidden,updated_at) "
            "VALUES('local-default',9,'中出内射',1,'2026-09-02T00:00:00Z')")
        self.connection.commit()
        self.fill()
        self.assertEqual(self.tags_of(9), ["中出内射", "巨臀"])
        self.assertEqual(self.connection.execute(
            "SELECT hidden FROM asset_tag_preference WHERE asset_id=9").fetchone()[0], 1)

    def test_tags_landed_by_union_survive_a_reprojection(self):
        """按差写只动这一组来源下的那几行，这部片已有的并集补标签原样留着。"""
        self.land(10, "ABC-010", ["可愛い", "中出し"], ["高颜值", "中出内射"])
        self.connection.execute(
            "INSERT INTO asset_tag(asset_id,tag,confidence,source) "
            "VALUES(10,'单体作品',0.9,'auto:metadata-tags@20260910T000000')")
        self.connection.commit()
        self.run_script("--apply", "--backup", str(self.root / "before.db"))
        self.assertEqual(self.tags_of(10), ["中出内射", "单体作品", "可爱"])

    def test_an_old_mapping_without_decisions_still_reads(self):
        legacy = types.SimpleNamespace(map_genres=lambda genres: (["美臀"], []))
        self.assertEqual(reproject.mapped_by(legacy, ["巨尻"], {}), ["美臀"])

    def test_a_split_word_moves_to_its_own_tag(self):
        self.land(1, "ABC-001", ["可愛い", "中出し"], ["高颜值", "中出内射"])
        self.run_script("--apply", "--backup", str(self.root / "before.db"))
        self.assertEqual(self.tags_of(1), ["中出内射", "可爱"])

    def test_a_tag_another_word_still_backs_stays(self):
        """`巨尻` 与 `美尻` 都在时「美臀」有 `美尻` 撑着，只加上「巨臀」。"""
        self.land(2, "ABC-002", ["巨尻", "美尻", "美少女", "可愛い"], ["美臀", "高颜值"])
        self.run_script("--apply", "--backup", str(self.root / "before.db"))
        self.assertEqual(self.tags_of(2), ["可爱", "巨臀", "美臀", "高颜值"])

    def test_drift_unrelated_to_the_mapping_change_is_left_alone(self):
        """账本和快照早就对不上的地方不是这次改动造成的，不借机改。"""
        self.land(3, "ABC-003", ["中出し", "可愛い"], ["高颜值", "自慰"])
        self.run_script("--apply", "--backup", str(self.root / "before.db"))
        self.assertEqual(self.tags_of(3), ["可爱", "自慰"])

    def test_a_group_the_change_does_not_touch_is_not_rewritten(self):
        self.land(4, "ABC-004", ["中出し"], ["中出内射"])
        rows = [reproject.judge(group, BEFORE, {})
                for group in reproject.ledger_groups(self.connection)]
        self.assertEqual([row["status"] for row in rows], [reproject.SAME])

    def test_a_missing_snapshot_is_reported_not_guessed(self):
        self.land(5, "ABC-005", ["可愛い"], ["高颜值"])
        (self.root / "ABC-005.json").unlink()
        self.run_script("--apply", "--backup", str(self.root / "before.db"))
        self.assertEqual(self.tags_of(5), ["高颜值"])
        self.assertIn(reproject.NO_SNAPSHOT,
                      (self.root / "reproject.csv").read_text(encoding="utf-8-sig"))

    def test_a_preview_leaves_the_ledger_alone(self):
        self.land(6, "ABC-006", ["可愛い"], ["高颜值"])
        self.run_script()
        self.assertEqual(self.tags_of(6), ["高颜值"])

    def test_the_old_mapping_is_read_from_git(self):
        module = reproject.taxonomy_at("HEAD")
        self.assertTrue(callable(module.map_genres))
        self.assertEqual(module.map_genres(['护士', '4小时以上作品']), (['护士'], []))


if __name__ == "__main__":
    unittest.main()
