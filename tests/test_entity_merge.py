"""实体合并：同一位女优的旧艺名与现用艺名各成一个实体时的归并。

合并不可逆，所以关系迁移、别名保留和唯一约束冲突的处理都要有回归。
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach.entities import (
    canonicalize_entity_name,
    collapse_repeated_entity_name,
    merge_entity,
    normalize_entity_name,
    resolve_entity,
    upsert_asset_entity,
)
from peach.migrations import upgrade

ROOT = Path(__file__).resolve().parents[1]


class EntityMergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "ledger.db"
        upgrade(self.db, ROOT / "migrations")
        self.con = sqlite3.connect(self.db)
        for asset_id in (1, 2, 3):
            self.con.execute(
                "INSERT INTO asset(id,location,path,name,medium) VALUES(?,?,?,?,'video')",
                (asset_id, "local", f"/x/{asset_id}.mp4", f"{asset_id}.mp4"))
        for entity_id, name in ((10, "橋本ありな"), (11, "新ありな")):
            self.con.execute(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES(?,'performer',?,?,'t','t')", (entity_id, name, name.lower()))
        self.con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(?,?,'performer','r18',1.0)",
            [(1, 10), (2, 10), (2, 11), (3, 11)])
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_merge_moves_relations_and_keeps_the_old_name_searchable(self):
        moved = merge_entity(self.con, target_id=10, source_id=11,
                             source_name="新ありな", alias_source="r18:performer")
        self.con.commit()

        assets = {r[0] for r in self.con.execute(
            "SELECT asset_id FROM asset_entity WHERE entity_id=10")}
        self.assertEqual(assets, {1, 2, 3}, "三条关系都应归到保留实体")
        self.assertEqual(moved["assets"], 1, "asset 2 两边都有，去重后只迁移 asset 3")
        self.assertIsNone(self.con.execute(
            "SELECT 1 FROM entity WHERE id=11").fetchone(), "被并入的实体应删除")
        aliases = {r[0] for r in self.con.execute(
            "SELECT alias FROM entity_alias WHERE entity_id=10")}
        self.assertIn("新ありな", aliases, "旧名必须留作别名，否则按它搜索会落空")
        self.assertEqual(moved["aliases"], 0, "source 无别名时计数应为 0")

    def test_merge_moves_source_aliases_and_counts_them(self):
        self.con.execute(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
            " VALUES(11,'ありな','ありな','r18',1.0)")
        self.con.commit()
        moved = merge_entity(self.con, target_id=10, source_id=11,
                             source_name="新ありな", alias_source="r18:performer")
        self.con.commit()
        self.assertEqual(moved["aliases"], 1, "source 的别名应被迁移并计数")

    def test_person_merge_keeps_identity_names_and_drops_collection_spellings(self):
        for name in ("新ありな4K", "新ありな 合集", "黑丝美腿", "ABP-123"):
            self.con.execute(
                "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
                " VALUES(11,?,?,'r18',1.0)", (name, normalize_entity_name(name)))
        self.con.execute(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
            " VALUES(11,'ありな','ありな','r18',1.0)")
        merge_entity(self.con, target_id=10, source_id=11,
                     source_name="新ありな 1080p", alias_source="user:review")
        aliases = {r[0] for r in self.con.execute("SELECT alias FROM entity_alias WHERE entity_id=10")}
        self.assertEqual(aliases, {"ありな"})
        self.assertEqual(self.con.execute("SELECT target_id FROM entity_redirect WHERE old_id=11").fetchone(), (10,))

    def test_tag_merge_keeps_the_retired_name_as_alias(self):
        for entity_id, name in ((20, "合集"), (21, "混合集")):
            self.con.execute(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES(?,'tag',?,?,'t','t')", (entity_id, name, name))
        merge_entity(self.con, target_id=20, source_id=21, source_name="混合集", alias_source="retired")
        self.assertEqual(self.con.execute("SELECT alias FROM entity_alias WHERE entity_id=20").fetchall(),
                         [("混合集",)])

    def test_two_pages_on_the_same_site_both_follow_the_merge(self):
        """同一个站上的两个页面是两条引用，合并后都归目标实体（0032）。

        这两条各自对应站上一个真实页面，挂的作品不同。旧的
        `UNIQUE(entity_id,provider,external_kind)` 会丢掉其中一条，等于合并一次就少一个
        能点进去的页面。
        """
        self.con.executemany(
            "INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id)"
            " VALUES(?,'javdb','performer',?)", [(10, "d45k9"), (11, "ZX5z7")])
        self.con.commit()
        moved = merge_entity(self.con, target_id=10, source_id=11,
                             source_name="新ありな", alias_source="r18:performer")
        self.con.commit()
        self.assertEqual(moved["refs"], 1)
        self.assertEqual(moved["dropped_refs"], 0)
        kept = self.con.execute(
            "SELECT external_id FROM entity_external_ref WHERE entity_id=10"
            " ORDER BY external_id").fetchall()
        self.assertEqual(kept, [("ZX5z7",), ("d45k9",)])

    def test_merge_leaves_no_dangling_rows(self):
        merge_entity(self.con, target_id=10, source_id=11,
                     source_name="新ありな", alias_source="r18:performer")
        self.con.commit()
        for table in ("asset_entity", "entity_alias", "entity_external_ref",
                      "entity_link", "entity_search_term"):
            left = self.con.execute(
                f"SELECT count(*) FROM {table} WHERE entity_id=11").fetchone()[0]
            self.assertEqual(left, 0, f"{table} 不应残留被删实体的行")
        self.assertEqual(
            self.con.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_a_second_id_from_the_same_source_is_dropped_not_raised(self):
        """合并过的人在来源那边还是两个页面，第二个 id 只能丢掉。

        实体 8004「桥本有菜」名下挂着「橋本ありな」「新ありな」等 8 个别名，r18dev 给
        前者 1032668、给后者 1078619，账本存的是后者。按别名认出同一个人之后再插第二个
        id，撞的是 `UNIQUE(entity_id,provider,external_kind)`——插入语句的 `ON CONFLICT`
        只认主键那一组，于是一条落库把整批自动落库连同别的字段一起带走。
        """
        self.con.execute(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence) "
            "VALUES(10,'橋本ありな別名','橋本ありな別名','mapping',1.0)")
        self.con.execute(
            "INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id)"
            " VALUES(10,'r18dev','performer','1078619')")
        self.con.commit()
        entity_id = upsert_asset_entity(
            self.con, kind="performer", name="橋本ありな別名", asset_id=3,
            role="performer", source="javinizer:r18dev:performer",
            external_provider="r18dev", external_id="1032668")
        self.con.commit()
        self.assertEqual(entity_id, 10)
        self.assertEqual(
            self.con.execute(
                "SELECT external_id FROM entity_external_ref "
                "WHERE entity_id=10 AND provider='r18dev'").fetchall(),
            [("1078619",)], "先到的那条留着")
        self.assertEqual(
            self.con.execute(
                "SELECT count(*) FROM asset_entity WHERE asset_id=3 AND entity_id=10 "
                "AND source='javinizer:r18dev:performer'").fetchone()[0], 1,
            "引用写不进去不影响这条出演关系本身")

    def test_person_upsert_collapses_repeated_name_and_reuses_unique_alias(self):
        self.con.execute(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence) "
            "VALUES(10,'Hashimoto Arina','hashimoto arina','mapping',1.0)"
        )
        entity_id = upsert_asset_entity(
            self.con, kind="performer", name="Hashimoto Arina Hashimoto Arina",
            asset_id=3, role="performer", source="javbus:performer",
        )
        self.assertEqual(entity_id, 10)
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM entity WHERE kind='performer'"
        ).fetchone()[0], 2, "旧名别名不得被重新建成第三个实体")
        self.assertEqual(collapse_repeated_entity_name("A B A B"), "A B")
        self.assertEqual(canonicalize_entity_name("performer", "画像を拡大する"), "")

    def test_studio_ingest_reuses_official_names_and_registered_aliases(self):
        target = upsert_asset_entity(self.con, kind='studio', name='Attackers',
                                    asset_id=1, role='studio', source='r18:studio')
        self.con.execute("INSERT INTO entity_alias VALUES(?,? ,?,'mapping',1)",
                         (target, 'アタッカーズ', 'アタッカーズ'))
        for name in ('ATTACKERS（アタッカーズ）', 'アタッカーズ'):
            self.assertEqual(upsert_asset_entity(self.con, kind='studio', name=name,
                asset_id=3, role='studio', source='makers:studio'), target)
            self.assertEqual(resolve_entity(self.con, 'studio', name)[0], target)
        self.assertEqual(self.con.execute("SELECT count(*) FROM entity WHERE kind='studio'").fetchone()[0], 1)

    def test_studio_ingest_preserves_ambiguous_aliases(self):
        for name in ('Alpha', 'Beta'):
            entity_id = upsert_asset_entity(self.con, kind='studio', name=name,
                asset_id=1, role='studio', source='mapping')
            self.con.execute("INSERT INTO entity_alias VALUES(?,? ,?,'mapping',1)",
                             (entity_id, 'Shared', 'shared'))
        found = upsert_asset_entity(self.con, kind='studio', name='Shared',
            asset_id=3, role='studio', source='makers:studio')
        self.assertEqual(self.con.execute('SELECT canonical_name FROM entity WHERE id=?',
                                         (found,)).fetchone()[0], 'Shared')

    def test_metadata_landing_uses_one_studio_name_for_fields_and_relations(self):
        from peach.metadata_auto_apply import _apply_metadata_candidate
        self.con.row_factory = sqlite3.Row
        target = upsert_asset_entity(self.con, kind='studio', name='Attackers',
                                    asset_id=1, role='studio', source='r18:studio')
        group = {'asset_id': '3', 'asset_path': '/x/3.mp4', 'code': '',
                 'field': 'studio', 'item_key': 'asset:3:studio'}
        candidate = {'source': 'makers', 'value': 'ATTACKERS（アタッカーズ）',
                     'confidence': 1, 'candidate_key': 'studio-identity'}
        _apply_metadata_candidate(self.con, group, candidate, '2026-10-12', 'review:makers')
        self.assertEqual(self.con.execute('SELECT studio FROM asset WHERE id=3').fetchone()[0], 'Attackers')
        self.assertEqual(self.con.execute("SELECT entity_id FROM asset_entity WHERE asset_id=3 AND role='studio'").fetchone()[0], target)


class ZeroWidthNameTests(unittest.TestCase):
    """规范名里的零宽字符。

    上游译名夹带过两个实例：performer 的 `‌斋藤亚美里` 和 creator 的
    `比特ビット‌`。界面上和普通名字一模一样，问题全在比对上。
    """

    def test_a_zero_width_char_does_not_survive_into_the_canonical_name(self):
        self.assertEqual(canonicalize_entity_name("performer", "‌斋藤亚美里"),
                         "斋藤亚美里")
        self.assertEqual(canonicalize_entity_name("creator", "比特ビット‌"),
                         "比特ビット")

    def test_the_normalised_name_matches_across_the_invisible_difference(self):
        """`strip()` 不认零宽字符是空白，`normalized_name` 于是带着它。

        后果不是显示出错，是 `upsert_asset_entity` 按 `normalized_name` 找不到已有
        实体，同一个人存成两条；按名字搜也一个都搜不到。
        """
        self.assertEqual(normalize_entity_name("比特ビット‌"),
                         normalize_entity_name("比特ビット"))

    def test_the_joiner_inside_an_emoji_is_left_alone(self):
        """U+200D 在 emoji 序列里是连字符，剥掉会把一个字形拆成三个。

        创作者名字里带 emoji 是常事，所以它不在剥除名单里。
        """
        self.assertEqual(canonicalize_entity_name("creator", "👨‍👩‍👧"),
                         "👨‍👩‍👧")


if __name__ == "__main__":
    unittest.main()
