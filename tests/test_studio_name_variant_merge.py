"""厂牌重名合并：配错一对就是把两家厂牌的作品搅在一起，而且不可逆。

这里守的是「什么才算证据」。两条判据都不许退化成转写或形状猜测。
"""
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load_module():
    sys.path.insert(0, str(REPO / "src"))
    spec = importlib.util.spec_from_file_location(
        "merge_studio_name_variants", REPO / "scripts" / "merge_studio_name_variants.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VariantKeyTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_two_different_japanese_names_never_share_a_key(self):
        """真实陷阱：只留 ASCII 的折法把这两家都折成 `tv`，凭空造出一对。

        `シロウトTV` 是素人企划，`ラグジュTV`（16 部）是另一家。合并它们不可逆。
        """
        self.assertNotEqual(self.module.variant_key("シロウトTV"),
                            self.module.variant_key("ラグジュTV"))
        self.assertNotEqual(self.module.variant_key("プレステージ"),
                            self.module.variant_key("ムーディーズ"))

    def test_quote_star_and_fullwidth_spellings_fold_together(self):
        for left, right in (("AVS collector's", "AVS collector’s"),
                            ("OFFICE K'S", "OFFICE K’S"),
                            ("D*Collection", "D☆Collection"),
                            ("kira*kira", "kira☆kira"),
                            ("V&R PRODUCE", "V＆R PRODUCE")):
            with self.subTest(left=left):
                self.assertEqual(self.module.variant_key(left),
                                 self.module.variant_key(right))

    def test_unrelated_latin_studios_keep_their_own_keys(self):
        self.assertNotEqual(self.module.variant_key("Fitch"),
                            self.module.variant_key("Fetish Box"))

    def test_official_brand_spellings_share_a_key(self):
        for plain, official in [('Attackers', 'ATTACKERS（アタッカーズ）'),
                                ('PREMIUM', 'PREMIUM（プレミアム）'), ('kawaii', 'kawaii*')]:
            self.assertEqual(self.module.variant_key(plain), self.module.variant_key(official))
        self.assertNotEqual(self.module.variant_key('FALENO'), self.module.variant_key('FALENO(2)'))


class SpellingVariantTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_the_ascii_spelling_survives(self):
        """用户 2026-09-04 定的口径：统一为英文、罗马音。"""
        names = {1: "AVS collector's", 2: "AVS collector’s"}
        rows = self.module.spelling_variants(names, {1: 6, 2: 6})
        self.assertEqual([(row["keep_id"], row["drop_id"]) for row in rows], [(1, 2)])

    def test_the_ascii_side_wins_even_with_fewer_assets(self):
        """作品数不参与这一类判断：写法才是这里唯一的取舍点。"""
        names = {1: "D*Collection", 2: "D☆Collection"}
        rows = self.module.spelling_variants(names, {1: 1, 2: 99})
        self.assertEqual(rows[0]["keep_name"], "D*Collection")

    def test_two_ascii_spellings_fall_back_to_the_asset_count(self):
        names = {1: "Hey Hey", 2: "HEYHEY"}
        rows = self.module.spelling_variants(names, {1: 2, 2: 40})
        self.assertEqual(rows[0]["keep_id"], 2)

    def test_three_entities_on_one_key_are_left_to_a_human(self):
        """两条能配成一对，三条要先决定谁并进谁——那不是脚本该替人做的。"""
        names = {1: "kira*kira", 2: "kira☆kira", 3: "kira★kira"}
        self.assertEqual(len({self.module.variant_key(n) for n in names.values()}), 1)
        self.assertEqual(self.module.spelling_variants(names, {}), [])

    def test_a_studio_without_a_twin_is_not_a_pair(self):
        self.assertEqual(self.module.spelling_variants({1: "Prestige"}, {1: 425}), [])

    def test_asset_counts_use_works_instead_of_source_relations(self):
        with sqlite3.connect(':memory:') as connection:
            connection.executescript(
                "CREATE TABLE entity(id INTEGER,kind TEXT);"
                "CREATE TABLE asset(id INTEGER,code TEXT);"
                "CREATE TABLE asset_entity(entity_id INTEGER,asset_id INTEGER);"
                "INSERT INTO entity VALUES(1,'studio');"
                "INSERT INTO asset VALUES(10,'ATID-479');"
                "INSERT INTO asset_entity VALUES(1,10),(1,10);")
            counts, prefixes = self.module.asset_facts(connection)
            self.assertEqual(dict(counts), {1: 1})
            self.assertEqual(dict(prefixes), {1: {'ATID'}})


class ScriptVariantTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.names = {1: "MOODYZ", 2: "ムーディーズ", 3: "Prestige", 4: "プレステージ"}
        self.counts = {1: 122, 2: 7, 3: 425, 4: 9}

    def rows(self, prefixes):
        return self.module.script_variants(self.names, self.counts, prefixes, set())

    def test_a_shared_code_prefix_is_the_evidence(self):
        """前缀属于厂牌。`ムーディーズ` 的 MIDV 也出现在 `MOODYZ` 名下就是同一家。"""
        rows = self.rows({1: {"MIDE", "MIDV"}, 2: {"MIDV"}})
        self.assertEqual([(row["keep_name"], row["drop_name"]) for row in rows],
                         [("MOODYZ", "ムーディーズ")])
        self.assertIn("MIDV", str(rows[0]["evidence"]))

    def test_an_english_brand_beats_its_katakana_spelling_regardless_of_asset_count(self):
        rows = self.module.script_variants(
            self.names, {1: 1, 2: 999}, {1: {"MIDV"}, 2: {"MIDV"}}, set())
        self.assertEqual(rows[0]["keep_name"], "MOODYZ")

    def test_a_japanese_name_beats_its_romaji_transliteration(self):
        """罗马音不算英文：`Celeb no Tomo` 是番号站的转写，厂牌自己写 `セレブの友`。"""
        names = {1: "Celeb no Tomo", 2: "セレブの友"}
        rows = self.module.script_variants(
            names, {1: 40, 2: 3}, {1: {"CESD"}, 2: {"CESD"}}, set())
        self.assertEqual([(row["keep_name"], row["drop_name"]) for row in rows],
                         [("セレブの友", "Celeb no Tomo")])

    def test_no_shared_prefix_means_no_pair(self):
        """名字读起来像不算证据。这条捷径唯一的身份保证就是前缀。"""
        self.assertEqual(self.rows({1: {"MIDE"}, 2: {"ABP"}}), [])

    def test_av_open_entries_keep_their_studio_identities(self):
        self.assertEqual(self.module.script_variants(
            {1: 'kawaii', 2: 'ダスッ！'}, {1: 48, 2: 23},
            {1: {'AVOP'}, 2: {'AVOP'}}, set()), [])

    def test_a_prefix_shared_with_two_latin_studios_is_left_to_a_human(self):
        rows = self.rows({1: {"ABP"}, 2: {"ABP"}, 3: {"ABP"}})
        self.assertEqual(rows, [])

    def test_a_japanese_studio_with_no_codes_at_all_is_left_alone(self):
        """`ヒビノ` 这类只有 1 部、番号取不出前缀的，没有可核验的证据。"""
        self.assertEqual(self.rows({1: {"MIDV"}, 2: set()}), [])

    def test_entities_already_paired_by_spelling_are_skipped(self):
        taken = {1, 2}
        self.assertEqual(
            self.module.script_variants(self.names, self.counts,
                                        {1: {"MIDV"}, 2: {"MIDV"}}, taken), [])


class BracketedVariantTests(unittest.TestCase):
    """日英并写的一串。证据只能是账本自己登记过的写法。"""

    def setUp(self):
        self.module = load_module()
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(
            "CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT,"
            " normalized_name TEXT);"
            "CREATE TABLE entity_alias(entity_id INTEGER, alias TEXT,"
            " normalized_alias TEXT, source TEXT, confidence REAL);")
        self.connection.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name) VALUES(?,'studio',?,?)",
            [(1, "Prestige", "prestige"),
             (2, "プレステージプレミアム(PRESTIGE PREMIUM)",
              "プレステージプレミアム(prestige premium)"),
             (3, "Faleno", "faleno")])
        self.connection.executemany(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
            " VALUES(1,?,?,'peach:canonicalization',1.0)",
            [("プレステージプレミアム", "プレステージプレミアム"),
             ("Prestige Premium", "prestige premium")])
        self.connection.commit()
        self.counts = {1: 437, 2: 8, 3: 30}

    def tearDown(self):
        self.connection.close()

    def names(self):
        return {int(row[0]): str(row[1]) for row in self.connection.execute(
            "SELECT id,canonical_name FROM entity WHERE kind='studio'")}

    def test_a_registered_alias_on_one_side_is_the_evidence(self):
        """素人系番号以数字开头，class B 一个前缀都取不到，接不住这一类。"""
        rows = self.module.bracketed_variants(
            self.connection, self.names(), self.counts, set())
        self.assertEqual([(row["keep_name"], row["drop_name"]) for row in rows],
                         [("Prestige", "プレステージプレミアム(PRESTIGE PREMIUM)")])

    def test_an_unregistered_spelling_is_never_evidence(self):
        """括号本身说明不了什么。两边都没登记过就没有可核验的身份。"""
        self.connection.execute("DELETE FROM entity_alias")
        self.connection.commit()
        self.assertEqual(self.module.bracketed_variants(
            self.connection, self.names(), self.counts, set()), [])

    def test_two_sides_pointing_at_two_studios_are_left_to_a_human(self):
        """一串指到两家去说明它本身有歧义，合并不可逆，交人工。"""
        self.connection.execute(
            "UPDATE entity SET canonical_name='Prestige(Faleno)',"
            "normalized_name='prestige(faleno)' WHERE id=2")
        self.connection.commit()
        self.assertEqual(self.module.bracketed_variants(
            self.connection, self.names(), self.counts, set()), [])

    def test_an_alias_registered_on_two_studios_keeps_the_pair_unresolved(self):
        self.connection.execute(
            "INSERT INTO entity_alias VALUES(3,'Prestige Premium','prestige premium','mapping',1)")
        self.assertEqual(self.module.bracketed_variants(
            self.connection, self.names(), self.counts, set()), [])

    def test_a_name_already_paired_by_another_class_is_skipped(self):
        self.assertEqual(self.module.bracketed_variants(
            self.connection, self.names(), self.counts, {2}), [])

    def test_a_serial_suffix_is_not_a_second_spelling(self):
        """`AVS collector's (2)` 这种带序号的名字不是并写。"""
        self.connection.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name)"
            " VALUES(4,'studio','Faleno (2)','faleno (2)')")
        self.connection.commit()
        rows = self.module.bracketed_variants(
            self.connection, self.names(), {**self.counts, 4: 1}, set())
        self.assertNotIn("Faleno (2)", [row["drop_name"] for row in rows])


class RelinkTests(unittest.TestCase):
    """合并只改账本，标识却按 canonical_name 落盘，不改挂就成了没人认领的文件。"""

    def setUp(self):
        self.module = load_module()
        from peach.previews import logo_key
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.row = {"keep_name": "Prestige", "drop_name": "プレステージ"}
        self.drop = logo_key(str(self.row["drop_name"]))
        self.keep = logo_key(str(self.row["keep_name"]))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, body=b"x"):
        (self.root / name).write_bytes(body)

    def test_the_discarded_name_hands_its_plates_to_the_survivor(self):
        self.write(f"{self.drop}.logo.img", b"jae")
        moved = self.module.relink_logos([self.row], self.root)
        self.assertEqual(moved["moved"],
                         [f"{self.drop}.logo.img -> {self.keep}.logo.img"])
        self.assertEqual((self.root / f"{self.keep}.logo.img").read_bytes(), b"jae")
        self.assertFalse((self.root / f"{self.drop}.logo.img").exists())

    def test_a_plate_the_survivor_already_has_is_never_overwritten(self):
        """和安装同一条口径：已有的一个字节都不动，多出来的那张留在原地等人看。"""
        self.write(f"{self.drop}.icon.img", b"old")
        self.write(f"{self.keep}.icon.img", b"current")
        moved = self.module.relink_logos([self.row], self.root)
        self.assertEqual(moved["moved"], [])
        self.assertEqual(moved["left"], [f"{self.drop}.icon.img"])
        self.assertEqual((self.root / f"{self.keep}.icon.img").read_bytes(), b"current")

    def test_the_sidecars_travel_with_the_plate(self):
        """`.ct` 记着这张图是什么类型，落在旧名下 `/logo` 就答不出 Content-Type。"""
        self.write(f"{self.drop}.logo.img", b"jae")
        self.write(f"{self.drop}.logo.img.ct", b"image/png")
        self.write(f"{self.drop}.logo.img.provenance.json", b"{}")
        self.module.relink_logos([self.row], self.root)
        self.assertEqual((self.root / f"{self.keep}.logo.img.ct").read_bytes(),
                         b"image/png")
        self.assertTrue((self.root / f"{self.keep}.logo.img.provenance.json").is_file())

    def test_a_pair_that_lands_on_one_file_name_is_left_alone(self):
        """两个名字折成同一个落盘名时，搬运就是把文件搬到它自己头上。"""
        self.write("Same.logo.img", b"one")
        moved = self.module.relink_logos(
            [{"keep_name": "Same", "drop_name": "Same"}], self.root)
        self.assertEqual(moved, {"moved": [], "left": []})
        self.assertEqual((self.root / "Same.logo.img").read_bytes(), b"one")

    def test_a_pair_with_nothing_installed_is_a_no_op(self):
        self.assertEqual(self.module.relink_logos([self.row], self.root),
                         {"moved": [], "left": []})


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name).resolve() / "ledger.db"
        self.connection = sqlite3.connect(self.path)
        self.connection.executescript(
            "CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT,"
            " normalized_name TEXT, updated_at TEXT);"
            "CREATE TABLE profile(id INTEGER PRIMARY KEY, settings_json TEXT DEFAULT '{}', updated_at TEXT);"
            "CREATE TABLE entity_alias(entity_id INTEGER, alias TEXT,"
            " normalized_alias TEXT, source TEXT, confidence REAL,"
            " UNIQUE(entity_id, normalized_alias));"
            "CREATE TABLE entity_external_ref(entity_id INTEGER, provider TEXT,"
            " external_kind TEXT, external_id TEXT,"
            " UNIQUE(entity_id, provider, external_kind));"
            "CREATE TABLE entity_link(entity_id INTEGER, link_kind TEXT, label TEXT,"
            " url TEXT, hostname TEXT, is_sensitive INTEGER, metadata_json TEXT,"
            " created_at TEXT, updated_at TEXT, UNIQUE(entity_id, url));"
            "CREATE TABLE entity_search_term(entity_id INTEGER, term TEXT,"
            " purpose TEXT, source TEXT, created_at TEXT, UNIQUE(entity_id, term));"
            "CREATE TABLE entity_membership(member_id INTEGER PRIMARY KEY,"
            " agency_id INTEGER, source TEXT, confidence REAL, checked_at TEXT);"
            "CREATE TABLE label_maker(label_id INTEGER PRIMARY KEY,"
            " maker_id INTEGER, source TEXT, confidence REAL, checked_at TEXT);"
            "CREATE TABLE performer_profile(entity_id INTEGER PRIMARY KEY, source TEXT);"
            "CREATE TABLE follow_source(id INTEGER PRIMARY KEY, entity_id INTEGER);"
            "CREATE TABLE feed_source(id INTEGER PRIMARY KEY, entity_id INTEGER);"
            "CREATE TABLE feed_discovery_entity(discovery_id INTEGER, entity_id INTEGER,"
            " PRIMARY KEY(discovery_id, entity_id));"
            "CREATE TABLE entity_redirect(old_id INTEGER PRIMARY KEY,"
            " target_id INTEGER NOT NULL, source TEXT NOT NULL, merged_at TEXT NOT NULL);"
            "CREATE TABLE asset(id INTEGER PRIMARY KEY, code TEXT, studio TEXT,"
            " field_owners TEXT DEFAULT '{}', mutation_revision INTEGER DEFAULT 0);"
            "CREATE TABLE asset_entity(asset_id INTEGER, entity_id INTEGER, role TEXT,"
            " source TEXT, confidence REAL, metadata_json TEXT, first_seen_at TEXT,"
            " last_seen_at TEXT, UNIQUE(asset_id, entity_id, role));")
        self.connection.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name) VALUES(?,?,?,?)",
            [(1, "studio", "MOODYZ", "moodyz"), (2, "studio", "ムーディーズ", "ムーディーズ")])
        self.connection.executemany(
            "INSERT INTO asset(id,code,studio) VALUES(?,?,?)",
            [(10, "MIDE-100", "MOODYZ"), (11, "MIDV-200", "ムーディーズ")])
        self.connection.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role) VALUES(?,?,'studio')",
            [(10, 1), (11, 2)])
        self.connection.commit()
        self.row = {"keep_id": 1, "keep_name": "MOODYZ", "keep_assets": 1,
                    "drop_id": 2, "drop_name": "ムーディーズ", "drop_assets": 1,
                    "klass": "日文名／罗马字名", "evidence": "共用番号前缀 MIDV"}

    def tearDown(self):
        self.connection.close()
        self.tmp.cleanup()

    def test_the_flat_projection_follows_the_canonical_relation(self):
        """只改实体不改投影，下一次刮削会照着 `asset.studio` 把旧实体再建一遍。"""
        with self.connection:
            moved = self.module.apply_rows(self.connection, [self.row])
        self.assertEqual(moved["flat_rewritten"], 1)
        self.assertEqual(
            sorted(row[0] for row in self.connection.execute("SELECT studio FROM asset")),
            ["MOODYZ", "MOODYZ"])

    def test_the_discarded_name_survives_as_an_alias(self):
        """旧名一律留作别名，否则按旧名搜索会落空。"""
        with self.connection:
            self.module.apply_rows(self.connection, [self.row])
        aliases = [row[0] for row in self.connection.execute(
            "SELECT alias FROM entity_alias WHERE entity_id=1")]
        self.assertIn("ムーディーズ", aliases)

    def test_every_asset_ends_up_on_the_surviving_entity(self):
        with self.connection:
            self.module.apply_rows(self.connection, [self.row])
        self.assertEqual(
            [row[0] for row in self.connection.execute(
                "SELECT count(*) FROM asset_entity WHERE entity_id=1")], [2])
        self.assertEqual(
            [row[0] for row in self.connection.execute(
                "SELECT count(*) FROM entity WHERE id=2")], [0])


if __name__ == "__main__":
    unittest.main()
