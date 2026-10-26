import json
import sqlite3
import shutil
import tempfile
import unittest
from pathlib import Path

from peach.migrations import plan, upgrade


MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class MigrationTests(unittest.TestCase):
    def test_disposal_reason_migration_preserves_assets_tags_and_unknown_reasons(self):
        prior = self.root.resolve() / 'prior'
        prior.mkdir()
        for path in MIGRATIONS.glob('*.sql'):
            if path.name < '0046_':
                shutil.copyfile(path, prior / path.name)
        upgrade(self.db, prior)
        connection = sqlite3.connect(self.db)
        try:
            connection.execute("INSERT INTO asset(id,location,path,medium,disposal) VALUES(1,'local','sample.mp4','video','trash')")
            connection.execute("INSERT INTO asset_tag(asset_id,tag,source) VALUES(1,'示例','user')")
            connection.commit()
            self.assertEqual([migration.version for migration in upgrade(self.db, MIGRATIONS)], ['0046', '0047'])
            self.assertEqual(connection.execute("SELECT disposal,disposal_reason FROM asset WHERE id=1").fetchone(), ('trash', None))
            self.assertEqual(connection.execute("SELECT tag,source FROM asset_tag WHERE asset_id=1").fetchall(), [('示例', 'user')])
            self.assertEqual(connection.execute("SELECT count(*) FROM asset").fetchone()[0], 1)
        finally:
            connection.close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "ledger.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_database_upgrades_and_is_idempotent(self):
        sqlite3.connect(self.db).close()
        backup = self.root / "before.db"
        done = upgrade(self.db, MIGRATIONS, backup)
        self.assertEqual([m.version for m in done], [f'{number:04d}' for number in range(48)])
        self.assertTrue(backup.exists())
        con = sqlite3.connect(self.db)
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_schema WHERE type='table'"
        )}
        versions = [row[0] for row in con.execute(
            "SELECT version FROM schema_migration ORDER BY version"
        )]
        con.close()
        self.assertTrue({"asset", "profile", "media_binding", "activity_event",
                         "provider_profile", "entity", "entity_alias",
                         "entity_external_ref", "asset_entity", "entity_link",
                         "entity_search_term", "watch_queue", "asset_preference", "asset_quality_goal",
                         "playlist", "playlist_item",
                         "asset_tag_preference", "asset_search", "follow_playback",
                         "genre_decision", "asset_subtitle", "task_run", "performer_profile", "code_sample_image",
                         "entity_classification", "entity_identity_link", "seed_pack_history", "schema_migration"} <= tables)
        self.assertEqual(versions, [f'{number:04d}' for number in range(48)])
        self.assertEqual(upgrade(self.db, MIGRATIONS), [])
        self.assertEqual(plan(self.db, MIGRATIONS)[1], [])

    def test_old_schema_columns_are_reconciled(self):
        con = sqlite3.connect(self.db)
        con.executescript("""
          CREATE TABLE asset(
            id INTEGER PRIMARY KEY,
            location TEXT NOT NULL, path TEXT NOT NULL, name TEXT, medium TEXT,
            size INTEGER, mtime TEXT, hash_kind TEXT, hash TEXT,
            creator TEXT, series TEXT, code TEXT,
            duration REAL, width INTEGER, height INTEGER, vcodec TEXT, fps REAL,
            has_audio INTEGER, ctx_length TEXT, ctx_orient TEXT, ctx_quality TEXT,
            ctx_pace TEXT, ctx_people TEXT, play_count INTEGER DEFAULT 0,
            last_played TEXT, rating INTEGER, o_count INTEGER, watch_ratio REAL,
            source_id INTEGER, stash_scene_id INTEGER, snapshot_path TEXT,
            first_seen TEXT, last_seen TEXT, UNIQUE(location,path));
          CREATE TABLE asset_tag(asset_id INTEGER,tag TEXT,confidence REAL,source TEXT,
                                 UNIQUE(asset_id,tag));
        """)
        con.close()
        upgrade(self.db, MIGRATIONS)
        con = sqlite3.connect(self.db)
        columns = {row[1] for row in con.execute("PRAGMA table_info(asset)")}
        con.close()
        self.assertTrue({"studio", "feedback", "disposal", "leave_ratio", "play_seconds",
                         "feedback_at", "seek_count", "max_reached", "release_date",
                         "catalog_title", "original_title"} <= columns)

    def test_checksum_change_is_rejected(self):
        sqlite3.connect(self.db).close()
        upgrade(self.db, MIGRATIONS)
        con = sqlite3.connect(self.db)
        con.execute("UPDATE schema_migration SET checksum='changed' WHERE version='0001'")
        con.commit()
        con.close()
        with self.assertRaises(RuntimeError):
            plan(self.db, MIGRATIONS)

    def test_missing_migration_directory_is_rejected_instead_of_reported_empty(self):
        """migration 目录不存在时拒绝，不要把空账本当成迁移到位的账本。

        `pip install .`（非 editable）只装 `src/` 下的包，仓库根的 `migrations/` 不进
        wheel。目录缺失若被读成「0 个迁移」，`peach init` 会打印成功并留下 0 字节账本，
        `peach migrate status` 还跟着报 0 pending。
        """
        absent = self.root / "no-such-migrations"
        with self.assertRaises(FileNotFoundError) as caught:
            plan(self.db, absent)
        self.assertIn(str(absent), str(caught.exception))
        with self.assertRaises(FileNotFoundError):
            upgrade(self.db, absent)
        self.assertFalse(self.db.exists())

    def test_empty_migration_directory_reports_nothing_to_apply(self):
        empty = self.root / "empty-migrations"
        empty.mkdir()
        self.assertEqual(upgrade(self.db, empty), [])

    def test_entity_migration_backfills_flattened_relations(self):
        base_migrations = self.root / "base-migrations"
        base_migrations.mkdir()
        for name in ("0000_legacy_schema.sql", "0001_core_boundaries.sql"):
            shutil.copyfile(MIGRATIONS / name, base_migrations / name)
        sqlite3.connect(self.db).close()
        upgrade(self.db, base_migrations)
        con = sqlite3.connect(self.db)
        con.executescript("""
          INSERT INTO asset(id,location,path,name,medium,creator,studio,series)
          VALUES(1,'local','one.mp4','one.mp4','video','Owner A','Studio A','Series A');
          INSERT INTO asset_tag(asset_id,tag,confidence,source)
          VALUES(1,'演员:Performer A',1.0,'stash:performer'),
                (1,'Tag A',0.9,'stash:tag');
        """)
        con.close()
        upgrade(self.db, MIGRATIONS)
        con = sqlite3.connect(self.db)
        entities = set(con.execute("SELECT kind,canonical_name FROM entity"))
        relations = set(con.execute(
            "SELECT e.kind,ae.role,ae.source FROM asset_entity ae "
            "JOIN entity e ON e.id=ae.entity_id WHERE ae.asset_id=1"
        ))
        con.close()
        self.assertTrue({('performer','Performer A'),('tag','Tag A'),
                         ('studio','Studio A'),('creator','Owner A'),
                         ('series','Series A')} <= entities)
        self.assertTrue({('performer','performer','stash:performer'),
                         ('tag','tag','stash:tag'),('studio','studio','legacy:asset'),
                         ('creator','creator','legacy:asset'),
                         ('series','series','legacy:asset')} <= relations)

    def test_late_legacy_tags_are_backfilled_by_0005(self):
        base_migrations = self.root / "base-migrations"
        base_migrations.mkdir()
        for name in (
            "0000_legacy_schema.sql", "0001_core_boundaries.sql",
            "0002_canonical_entities.sql", "0003_entity_pages_and_watch_queue.sql",
            "0004_asset_search_fts.sql",
        ):
            shutil.copyfile(MIGRATIONS / name, base_migrations / name)
        sqlite3.connect(self.db).close()
        upgrade(self.db, base_migrations)
        con = sqlite3.connect(self.db)
        con.executescript("""
          INSERT INTO asset(id,location,path,name,medium)
          VALUES(1,'local','late.mp4','late.mp4','video');
          INSERT INTO asset_tag(asset_id,tag,confidence,source)
          VALUES(1,'足交',0.6,'vision_creator'),
                (1,'演员:Late Performer',1.0,'stash:performer');
        """)
        con.close()
        upgrade(self.db, MIGRATIONS)
        con = sqlite3.connect(self.db)
        relations = set(con.execute(
            "SELECT e.kind,e.canonical_name,ae.role,ae.source,ae.confidence "
            "FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
            "WHERE ae.asset_id=1"
        ))
        search_entities = con.execute(
            "SELECT entities FROM asset_search WHERE asset_id=1"
        ).fetchone()[0]
        con.close()
        self.assertIn(('tag', '足交', 'tag', 'vision_creator', 0.6), relations)
        self.assertIn(('performer', 'Late Performer', 'performer',
                       'stash:performer', 1.0), relations)
        self.assertIn('足交', search_entities)
        self.assertIn('Late Performer', search_entities)

    def test_structural_creator_cleanup_keeps_real_creator(self):
        base_migrations = self.root / "base-migrations"
        base_migrations.mkdir()
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if not path.name.startswith(("0007_", "0009_")):
                shutil.copyfile(path, base_migrations / path.name)
        sqlite3.connect(self.db).close()
        upgrade(self.db, base_migrations)
        connection = sqlite3.connect(self.db)
        connection.executescript("""
          INSERT INTO asset(id,location,path,name,medium,creator)
          VALUES(1,'115','B:/xxr/门槛/one.mp4','one.mp4','video','门槛');
          INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)
          VALUES(101,'creator','门槛','门槛','now','now'),
                (102,'creator','Actual Creator','actual creator','now','now');
          INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)
          VALUES(1,101,'creator','legacy:asset',0.8),
                (1,102,'creator','reviewed',1.0);
        """)
        connection.close()
        upgrade(self.db, MIGRATIONS)
        connection = sqlite3.connect(self.db)
        self.assertEqual(connection.execute(
            "SELECT creator FROM asset WHERE id=1"
        ).fetchone()[0], "Actual Creator")
        self.assertEqual(connection.execute(
            "SELECT e.canonical_name FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
            "WHERE ae.asset_id=1 AND e.kind='creator'"
        ).fetchall(), [("Actual Creator",)])
        connection.close()

    def test_asce_cleanup_removes_only_false_creator_board_assertions(self):
        base_migrations = self.root / "base-migrations"
        base_migrations.mkdir()
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if not path.name.startswith("0009_"):
                shutil.copyfile(path, base_migrations / path.name)
        sqlite3.connect(self.db).close()
        upgrade(self.db, base_migrations)
        connection = sqlite3.connect(self.db)
        connection.executescript("""
          INSERT INTO asset(id,location,path,name,medium,creator)
          VALUES(1,'115','B:/kkg/asce/one.mp4','one.mp4','video','asce');
          INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)
          VALUES(101,'creator','asce','asce','now','now'),
                (102,'tag','制服','制服','now','now'),
                (103,'tag','原创','原创','now','now');
          INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)
          VALUES(1,101,'creator','legacy:asset',0.8),
                (1,102,'tag','vision_creator',0.6),
                (1,103,'tag','name',0.9);
          INSERT INTO asset_tag(asset_id,tag,confidence,source)
          VALUES(1,'制服',0.6,'vision_creator'),(1,'原创',0.9,'name');
        """)
        connection.close()
        upgrade(self.db, MIGRATIONS)
        connection = sqlite3.connect(self.db)
        self.assertIsNone(connection.execute(
            "SELECT creator FROM asset WHERE id=1"
        ).fetchone()[0])
        self.assertEqual(connection.execute(
            "SELECT tag,source FROM asset_tag WHERE asset_id=1"
        ).fetchall(), [("原创", "name")])
        self.assertEqual(connection.execute(
            "SELECT e.canonical_name,ae.source FROM asset_entity ae "
            "JOIN entity e ON e.id=ae.entity_id WHERE ae.asset_id=1"
        ).fetchall(), [("原创", "name")])
        connection.close()

    def test_folder_attribution_corrections_are_narrow_and_evidence_backed(self):
        base_migrations = self.root / "base-migrations"
        base_migrations.mkdir()
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if not path.name.startswith("0010_"):
                shutil.copyfile(path, base_migrations / path.name)
        sqlite3.connect(self.db).close()
        upgrade(self.db, base_migrations)
        connection = sqlite3.connect(self.db)
        connection.executescript("""
          INSERT INTO asset(id,location,path,name,medium,creator)
          VALUES(1,'115','B:\\云下载\\足交仙人\\feet of Suzyq (1).mp4','one.mp4','video','足交仙人'),
                (2,'115','B:\\MVP\\捅主任\\TokyoDolls\\32.mp4','32.mp4','video','捅主任'),
                (3,'115','B:\\创作者\\捅主任\\real.mp4','real.mp4','video','捅主任');
          INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)
          VALUES(101,'creator','足交仙人','足交仙人','now','now'),
                (102,'creator','捅主任','捅主任','now','now');
          INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)
          VALUES(1,101,'creator','legacy:asset',0.8),
                (2,102,'creator','legacy:asset',0.8),
                (3,102,'creator','legacy:asset',0.8);
        """)
        connection.close()
        upgrade(self.db, MIGRATIONS)
        connection = sqlite3.connect(self.db)
        self.assertEqual(connection.execute(
            "SELECT id,creator FROM asset ORDER BY id"
        ).fetchall(), [(1, "suzuq"), (2, None), (3, "捅主任")])
        relations = connection.execute(
            "SELECT ae.asset_id,e.canonical_name,ae.source,ae.confidence "
            "FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
            "WHERE e.kind='creator' ORDER BY ae.asset_id"
        ).fetchall()
        self.assertEqual(relations, [(1, "suzuq", "user:watermark", 1.0),
                                     (3, "捅主任", "legacy:asset", 0.8)])
        self.assertEqual(connection.execute(
            "SELECT alias,source,confidence FROM entity_alias "
            "WHERE entity_id=(SELECT id FROM entity WHERE normalized_name='suzuq')"
        ).fetchall(), [("Suzyq", "filename", 0.95)])
        connection.close()

    def test_fts_tracks_asset_entity_alias_and_search_term_changes(self):
        sqlite3.connect(self.db).close()
        upgrade(self.db, MIGRATIONS)
        connection = sqlite3.connect(self.db)
        connection.executescript("""
          INSERT INTO asset(id,location,path,name,medium,code)
          VALUES(1,'local','one.mp4','Prestige sample.mp4','video','ABW-001');
          INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)
          VALUES(1001,'performer','高桥千凛','高桥千凛','now','now');
          INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)
          VALUES(1,1001,'performer','test',1.0);
          INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)
          VALUES(1001,'Takachi Chisato','takachi chisato','test',1.0);
          INSERT INTO entity_search_term(entity_id,term,purpose,source,created_at)
          VALUES(1001,'private release keyword','source_lookup','test','now');
        """)
        for query in ('"Prestige"', '"高桥千"', '"Takachi"', '"release keyword"'):
            self.assertEqual(connection.execute(
                "SELECT asset_id FROM asset_search WHERE asset_search MATCH ?", (query,)
            ).fetchall(), [(1,)])
        connection.execute("UPDATE asset SET name='Renamed title.mp4' WHERE id=1")
        self.assertEqual(connection.execute(
            "SELECT asset_id FROM asset_search WHERE asset_search MATCH ?", ('"Renamed"',)
        ).fetchall(), [(1,)])
        self.assertEqual(connection.execute(
            "SELECT asset_id FROM asset_search WHERE asset_search MATCH ?", ('"Prestige"',)
        ).fetchall(), [])
        connection.execute(
            "UPDATE asset SET catalog_title='Official catalog title',"
            "original_title='Original catalog title' WHERE id=1"
        )
        for query in ('"Official catalog"', '"Original catalog"'):
            self.assertEqual(connection.execute(
                "SELECT asset_id FROM asset_search WHERE asset_search MATCH ?", (query,)
            ).fetchall(), [(1,)])
        connection.close()

    def test_rule34_source_case_duplicates_merge_without_losing_item_state(self):
        base_migrations = self.root / "base-migrations"
        base_migrations.mkdir()
        for path in sorted(MIGRATIONS.glob("*.sql")):
            # 历史基线停在 0037，follow_playback 建表与重建随 0020/0021 统一应用。
            # 0038 起的版本号触发器依赖这些表，留在后续的完整迁移链中。
            if path.name < "0038_" and not path.name.startswith(("0020_", "0021_", "0022_", "0024_")):
                shutil.copyfile(path, base_migrations / path.name)
        sqlite3.connect(self.db).close()
        upgrade(self.db, base_migrations)
        connection = sqlite3.connect(self.db)
        connection.executescript("""
          INSERT INTO asset(id,location,path,name,medium)
          VALUES(1,'online','https://example.test/1','saved','video');
          INSERT INTO follow_source(
            id,provider,ref,label,url,semantics,metadata_json,created_at,updated_at
          ) VALUES
            (10,'rule34xxx','lazyprocrastinator','lazyprocrastinator',
             'https://rule34.xxx/?tags=lazyprocrastinator','work','{}','1','1'),
            (11,'rule34xxx','LazyProcrastinator','LazyProcrastinator',
             'https://rule34.xxx/?tags=LazyProcrastinator','work','{}','2','2');
          INSERT INTO follow_item(
            source_id,external_id,title,published_precision,release_key,variant_kind,
            status,asset_id,metadata_json,first_seen_at,last_seen_at
          ) VALUES
            (10,'same','same','exact','same','main','ignored',NULL,'{}','1','2'),
            (10,'lower-only','lower','exact','lower','main','new',NULL,'{}','1','2'),
            (11,'same','same','exact','same','main','saved',1,'{}','2','3'),
            (11,'upper-only','upper','exact','upper','main','seen',NULL,'{}','2','3');
        """)
        connection.close()

        upgrade(self.db, MIGRATIONS)

        connection = sqlite3.connect(self.db)
        sources = connection.execute(
            "SELECT id,ref,url FROM follow_source WHERE provider='rule34xxx'"
        ).fetchall()
        items = connection.execute(
            "SELECT external_id,status,asset_id FROM follow_item ORDER BY external_id"
        ).fetchall()
        connection.close()
        self.assertEqual(sources, [(10, "lazyprocrastinator",
            "https://rule34.xxx/?tags=lazyprocrastinator")])
        self.assertEqual(items, [
            ("lower-only", "new", None),
            ("same", "saved", 1),
            ("upper-only", "seen", None),
        ])


FK_SEED_SQL = r"""
INSERT INTO profile(id,user_id,name,is_default,settings_json,created_at,updated_at)
VALUES('p2','local','Second',0,'{}','1','1');

INSERT INTO asset(id,location,path,name,medium,disposal) VALUES
  (1,'local','R:\Media\alpha.mp4','Alpha title.mp4','video',NULL),
  (2,'local','R:\Media\beta.mp4','Beta title.mp4','video','trash');

INSERT INTO asset_tag(asset_id,tag,confidence,source) VALUES
  (1,'巨乳',1.0,'name'),(1,'制服',0.8,'vision'),(2,'巨乳',1.0,'r18');

INSERT INTO media_binding(
  asset_id,backend,external_id,priority,metadata_json,last_synced_at)
VALUES(1,'stash','101',100,'{}','1');

INSERT INTO activity_event(id,asset_id,profile_id,kind,occurred_at,value_json,source)
VALUES(1,1,'local-default','play','1','{}','web'),
      (2,2,'p2','play','2','{}','web');

INSERT INTO asset_tag_preference(profile_id,asset_id,normalized_tag,hidden,updated_at)
VALUES('local-default',1,'制服',1,'1'),('p2',2,'巨乳',1,'1');

INSERT INTO watch_queue(profile_id,asset_id,added_at,source)
VALUES('local-default',1,'1','web');

INSERT INTO asset_preference(profile_id,asset_id,liked,updated_at)
VALUES('p2',1,1,'1');

INSERT INTO asset_quality_goal(profile_id,asset_id,wanted,updated_at)
VALUES('p2',1,1,'1');

INSERT INTO playlist(id,profile_id,name,source_kind,source_seed_asset_id,
                     current_asset_id,created_at,updated_at)
VALUES(1,'local-default','Mix','mix',1,1,'1','1');

INSERT INTO follow_source(
  id,provider,ref,label,url,semantics,metadata_json,created_at,updated_at)
VALUES(10,'rule34xxx','someone','someone',
       'https://rule34.xxx/?tags=someone','work','{}','1','1');

INSERT INTO follow_item(
  id,source_id,external_id,title,published_precision,release_key,variant_kind,
  status,asset_id,metadata_json,first_seen_at,last_seen_at)
VALUES(100,10,'x','X','exact','x','main','new',NULL,'{}','1','1');

INSERT INTO follow_playback(
  follow_item_id,profile_id,play_count,play_seconds,max_reached,last_played)
VALUES(100,'local-default',2,10,0.5,1.0),(100,'p2',1,5,0.2,2.0);
"""

# 0024 声明的删除行为。profile.user_id 是唯一保留 NO ACTION 的外键：即时外键下
# NO ACTION 已等于 RESTRICT，而重建 profile 会让 DROP TABLE 的隐式 DELETE 真的
# 级联删掉 profile 私有状态。判据写在 0024 的头部注释里。
EXPECTED_DELETE_RULES = {
    ("asset_tag", "asset_id"): ("asset", "CASCADE"),
    ("media_binding", "asset_id"): ("asset", "CASCADE"),
    ("activity_event", "asset_id"): ("asset", "CASCADE"),
    ("activity_event", "profile_id"): ("profile", "SET NULL"),
    ("asset_tag_preference", "profile_id"): ("profile", "CASCADE"),
    ("follow_playback", "profile_id"): ("profile", "CASCADE"),
    ("asset_subtitle", "asset_id"): ("asset", "CASCADE"),
}
NO_ACTION_ALLOWED = {("profile", "user_id")}


class AssetFieldOwnerColumnTests(unittest.TestCase):
    """0029：真相字段的归属列与乐观并发计数。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.db = self.root / "ledger.db"
        sqlite3.connect(self.db).close()
        upgrade(self.db, MIGRATIONS)

    def test_asset_gains_field_owners_and_a_revision_counter(self):
        connection = sqlite3.connect(self.db)
        self.addCleanup(connection.close)
        columns = {row[1]: (row[2], row[3], row[4])
                   for row in connection.execute("PRAGMA table_info(asset)")}
        self.assertEqual(columns["field_owners"][0], "TEXT")
        declaration, not_null, default = columns["mutation_revision"]
        self.assertEqual(declaration, "INTEGER")
        self.assertEqual((not_null, default), (1, "0"))

    def test_existing_rows_start_unowned_at_revision_zero(self):
        """归属不回填：谁写的答不出来时，留空比猜一个写入者诚实。"""
        connection = sqlite3.connect(self.db)
        self.addCleanup(connection.close)
        connection.execute(
            "INSERT INTO asset(id,location,path,name,medium,studio) "
            "VALUES(1,'local','R:\\\\a.mp4','a.mp4','video','某厂牌')")
        row = connection.execute(
            "SELECT field_owners,mutation_revision FROM asset WHERE id=1").fetchone()
        self.assertEqual(row, (None, 0))

    def test_sqlite_has_the_json_functions_the_owner_map_needs(self):
        """`json_extract` / `json_patch` 是归属写入唯一的实现手段，先确认它们在。"""
        connection = sqlite3.connect(self.db)
        self.addCleanup(connection.close)
        merged = connection.execute(
            "SELECT json_patch('{\"a\":\"scan:filename\"}','{\"b\":\"user:manual\"}')"
        ).fetchone()[0]
        self.assertEqual(json.loads(merged),
                         {"a": "scan:filename", "b": "user:manual"})
        self.assertEqual(connection.execute(
            "SELECT json_extract('{\"studio\":\"user:manual\"}','$.\"studio\"')"
        ).fetchone()[0], "user:manual")


class ForeignKeyDeleteRuleTests(unittest.TestCase):
    """0024：补齐缺失的 ON DELETE，重建时不丢行、不丢索引与触发器。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # 用 addCleanup 而不是 tearDown：清理按后进先出跑，连接一定先于临时目录关闭。
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.db = self.root / "ledger.db"

    def _migrations_below(self, upper: str):
        """只含版本号小于 `upper` 的迁移目录。

        按上界取，不按「排除 0024」取补集：补集会把后来新增的每一个版本都算进这份
        「到 0023 为止」的目录，而 0025 与 0024 无关，照样装得上。这一组用例盯的是
        0024 那次重建，多装一个版本就会把它新增的表和索引算成 0024 的账。
        """
        directory = self.root / f"below-{upper}"
        directory.mkdir()
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name < upper:
                shutil.copyfile(path, directory / path.name)
        return directory

    def _upgrade_through_0023(self):
        sqlite3.connect(self.db).close()
        upgrade(self.db, self._migrations_below("0024"))

    def _seed(self):
        connection = sqlite3.connect(self.db)
        connection.executescript(FK_SEED_SQL)
        connection.commit()
        connection.close()

    def _open(self, foreign_keys=False):
        connection = sqlite3.connect(self.db)
        connection.isolation_level = None
        # 断言失败也要先关连接，否则 Windows 上 tearDown 删不掉临时库。
        self.addCleanup(connection.close)
        if foreign_keys:
            connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _counts(connection):
        # asset_search 的影子表另行核对；schema_migration 本次必然 +1。
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'asset_search%' "
            "AND name<>'schema_migration' ORDER BY name"
        )]
        return {name: connection.execute(
            f'SELECT count(*) FROM "{name}"').fetchone()[0] for name in names}

    @staticmethod
    def _objects(connection, kind):
        return {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type=? AND sql IS NOT NULL", (kind,)
        )}

    @staticmethod
    def _search_rows(connection):
        return sorted(row[0] for row in connection.execute(
            "SELECT asset_id FROM asset_search"))

    @staticmethod
    def _delete_rules(connection):
        rules = {}
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        for table in tables:
            for row in connection.execute(f'PRAGMA foreign_key_list("{table}")'):
                rules[(table, row[3])] = (row[2], row[6])
        return rules

    def test_upgrade_keeps_rows_indexes_triggers_and_search(self):
        self._upgrade_through_0023()
        self._seed()
        connection = self._open()
        before_counts = self._counts(connection)
        before_indexes = self._objects(connection, "index")
        before_triggers = self._objects(connection, "trigger")
        before_views = self._objects(connection, "view")
        before_search = self._search_rows(connection)
        connection.close()
        self.assertEqual(before_counts["asset_tag"], 3)
        self.assertEqual(before_counts["follow_playback"], 2)

        self.assertEqual(
            [m.version for m in upgrade(self.db, self._migrations_below("0025"))], ["0024"])

        connection = self._open()
        self.assertEqual(self._counts(connection), before_counts)
        self.assertEqual(self._objects(connection, "trigger"), before_triggers)
        self.assertEqual(self._objects(connection, "view"), before_views)
        after_indexes = self._objects(connection, "index")
        self.assertEqual(before_indexes - after_indexes, set())
        self.assertEqual(after_indexes - before_indexes,
                         {"idx_asset_disposal_id", "idx_asset_tag_source_asset"})
        self.assertEqual(self._search_rows(connection), before_search)
        self.assertEqual(connection.execute(
            "SELECT asset_id FROM asset_search WHERE asset_search MATCH ?", ('"Alpha"',)
        ).fetchall(), [(1,)])
        self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        connection.close()

    def test_every_foreign_key_declares_a_delete_rule(self):
        self._upgrade_through_0023()
        upgrade(self.db, MIGRATIONS)
        connection = self._open()
        rules = self._delete_rules(connection)
        connection.close()
        for key, expected in EXPECTED_DELETE_RULES.items():
            self.assertEqual(rules.get(key), expected, key)
        undeclared = {key for key, value in rules.items() if value[1] == "NO ACTION"}
        self.assertEqual(undeclared, NO_ACTION_ALLOWED)

    def test_deleting_asset_cascades_to_its_dependent_rows(self):
        self._upgrade_through_0023()
        self._seed()
        upgrade(self.db, MIGRATIONS)
        connection = self._open(foreign_keys=True)
        connection.execute("DELETE FROM asset WHERE id=1")
        self.assertEqual(connection.execute(
            "SELECT asset_id,tag FROM asset_tag ORDER BY asset_id,tag").fetchall(),
            [(2, "巨乳")])
        for table in ("media_binding", "watch_queue", "asset_preference",
                      "asset_quality_goal"):
            self.assertEqual(connection.execute(
                f"SELECT count(*) FROM {table} WHERE asset_id=1").fetchone()[0], 0, table)
        self.assertEqual(connection.execute(
            "SELECT id FROM activity_event ORDER BY id").fetchall(), [(2,)])
        self.assertEqual(connection.execute(
            "SELECT profile_id,asset_id FROM asset_tag_preference").fetchall(),
            [("p2", 2)])
        self.assertEqual(connection.execute(
            "SELECT source_seed_asset_id,current_asset_id FROM playlist").fetchall(),
            [(None, None)])
        self.assertEqual(self._search_rows(connection), [2])
        self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        connection.close()

    def test_deleting_profile_keeps_activity_history_without_owner(self):
        self._upgrade_through_0023()
        self._seed()
        upgrade(self.db, MIGRATIONS)
        connection = self._open(foreign_keys=True)
        connection.execute("DELETE FROM profile WHERE id='p2'")
        self.assertEqual(connection.execute(
            "SELECT follow_item_id,profile_id FROM follow_playback").fetchall(),
            [(100, "local-default")])
        self.assertEqual(connection.execute(
            "SELECT profile_id,asset_id FROM asset_tag_preference").fetchall(),
            [("local-default", 1)])
        # 行为历史保留，只丢归属。
        self.assertEqual(connection.execute(
            "SELECT id,asset_id,profile_id FROM activity_event ORDER BY id").fetchall(),
            [(1, 1, "local-default"), (2, 2, None)])
        self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        connection.close()

    def test_deleting_app_user_with_profiles_is_still_refused(self):
        self._upgrade_through_0023()
        self._seed()
        upgrade(self.db, MIGRATIONS)
        connection = self._open(foreign_keys=True)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM app_user WHERE id='local'")
        self.assertEqual(connection.execute(
            "SELECT count(*) FROM app_user WHERE id='local'").fetchone()[0], 1)
        connection.close()

    def test_orphan_rows_abort_the_migration_instead_of_being_dropped(self):
        self._upgrade_through_0023()
        self._seed()
        connection = self._open()
        connection.execute(
            "INSERT INTO asset_tag(asset_id,tag,confidence,source) "
            "VALUES(999,'orphan',1.0,'name')")
        connection.close()

        with self.assertRaises(sqlite3.IntegrityError):
            upgrade(self.db, MIGRATIONS)

        connection = self._open()
        self.assertEqual(connection.execute(
            "SELECT max(version) FROM schema_migration").fetchone()[0], "0023")
        self.assertEqual(connection.execute(
            "SELECT count(*) FROM asset_tag WHERE asset_id=999").fetchone()[0], 1)
        self.assertEqual(connection.execute(
            "SELECT count(*) FROM asset_tag").fetchone()[0], 4)
        self.assertEqual(self._delete_rules(connection).get(("asset_tag", "asset_id")),
                         None)
        connection.close()

    def test_new_indexes_are_chosen_by_their_justifying_queries(self):
        self._upgrade_through_0023()
        self._seed()
        upgrade(self.db, MIGRATIONS)
        connection = self._open()
        trash = " ".join(row[3] for row in connection.execute(
            "EXPLAIN QUERY PLAN SELECT count(*) FROM asset a WHERE a.disposal='trash'"))
        self.assertIn("idx_asset_disposal_id", trash)
        sources = " ".join(row[3] for row in connection.execute(
            "EXPLAIN QUERY PLAN SELECT source,count(*),count(DISTINCT asset_id) "
            "FROM asset_tag GROUP BY source ORDER BY count(*) DESC"))
        self.assertIn("idx_asset_tag_source_asset", sources)
        connection.close()


class AssetSubtitleTableTests(unittest.TestCase):
    """0027：字幕 sidecar 表的形状由 schema 自己守住。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.db = self.root / "ledger.db"
        sqlite3.connect(self.db).close()
        upgrade(self.db, MIGRATIONS)
        self.connection = sqlite3.connect(self.db)
        self.connection.isolation_level = None
        self.addCleanup(self.connection.close)
        self.connection.execute(
            "INSERT INTO asset(id,location,path,name,medium,first_seen,last_seen) "
            r"VALUES(1,'local','R:\media\x\a.mp4','a.mp4','video','2026-09-01','2026-09-01')")

    def _insert(self, **overrides):
        row = {"asset_id": 1, "location": "local", "path": r"R:\media\x\a.srt",
               "name": "a.srt", "language": "zh-Hans", "format": "srt", "size": 40,
               "mtime": "2026-09-01", "pairing": "exact",
               "first_seen": "2026-09-01", "last_seen": "2026-09-01"}
        row.update(overrides)
        columns = ",".join(row)
        self.connection.execute(
            f"INSERT INTO asset_subtitle({columns}) "
            f"VALUES({','.join('?' * len(row))})", tuple(row.values()))

    def test_the_same_path_can_only_be_registered_once_per_location(self):
        self._insert()
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert(name="别名.srt")

    def test_an_unpaired_row_must_declare_itself_orphan(self):
        self._insert(asset_id=None, pairing="orphan")
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert(asset_id=None, pairing="exact", path=r"R:\media\x\b.srt")

    def test_an_unknown_pairing_name_is_refused(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert(pairing="差不多")

    def test_deleting_the_asset_takes_its_subtitle_rows(self):
        self._insert()
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("DELETE FROM asset WHERE id=1")
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM asset_subtitle").fetchone()[0], 0)
        self.assertEqual(
            self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_the_index_carries_the_listing_query(self):
        plan = " ".join(row[3] for row in self.connection.execute(
            "EXPLAIN QUERY PLAN SELECT id,name FROM asset_subtitle "
            "WHERE asset_id=1 ORDER BY name COLLATE NOCASE, id"))
        self.assertIn("idx_asset_subtitle_asset", plan)


class TaskRunTableTests(unittest.TestCase):
    """0028：任务中心那张表的约束由数据库守，不靠服务层自觉。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name).resolve() / "ledger.db"
        sqlite3.connect(self.db).close()
        upgrade(self.db, MIGRATIONS)
        self.connection = sqlite3.connect(self.db)
        self.addCleanup(self.connection.close)

    def _insert(self, **fields):
        fields.setdefault("task_key", "demo")
        fields.setdefault("trigger", "manual")
        fields.setdefault("status", "running")
        columns = ",".join(fields)
        marks = ",".join("?" * len(fields))
        cursor = self.connection.execute(
            f"INSERT INTO task_run({columns}) VALUES({marks})", list(fields.values()))
        self.connection.commit()
        return cursor.lastrowid

    def test_one_active_run_per_mutex_key(self):
        self._insert(mutex_key="follow-check", started_at="2026-09-11T00:00:00.000Z")
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert(mutex_key="follow-check", started_at="2026-09-11T00:01:00.000Z")

    def test_finished_runs_release_the_mutex_key_without_losing_it(self):
        """终态行不占索引位，但 `mutex_key` 留着——「上次是谁挡的」还查得到。"""
        for _ in range(3):
            self._insert(mutex_key="follow-check", status="succeeded",
                         finished_at="2026-09-11T00:00:00.000Z")
        self._insert(mutex_key="follow-check", started_at="2026-09-11T00:02:00.000Z")
        kept = self.connection.execute(
            "SELECT count(*) FROM task_run WHERE mutex_key='follow-check'").fetchone()
        self.assertEqual(kept[0], 4)

    def test_runs_without_a_mutex_key_never_block_each_other(self):
        for _ in range(3):
            self._insert(started_at="2026-09-11T00:00:00.000Z")
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM task_run WHERE status='running'").fetchone()[0], 3)

    def test_terminal_status_and_finished_at_agree(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert(status="succeeded")
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert(status="running", finished_at="2026-09-11T00:00:00.000Z")

    def test_unknown_status_and_trigger_are_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert(status="done", finished_at="2026-09-11T00:00:00.000Z")
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert(trigger="webhook")

    def test_recent_runs_of_one_task_use_their_index(self):
        plan_rows = " ".join(row[3] for row in self.connection.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM task_run WHERE task_key='demo' "
            "ORDER BY id DESC LIMIT 20"))
        self.assertIn("idx_task_run_key_recent", plan_rows)


class ExternalRefUniquenessTests(unittest.TestCase):
    """0032：一位实体在一个站点可以有多个页面，一个页面仍只属于一位实体。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name).resolve() / "ledger.db"
        sqlite3.connect(self.db).close()
        upgrade(self.db, MIGRATIONS)
        self.connection = sqlite3.connect(self.db)
        self.addCleanup(self.connection.close)
        for entity_id, name in ((10, "釈アリス"), (11, "天川そら")):
            self.connection.execute(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES(?,'performer',?,?,'t','t')", (entity_id, name, name))
        self.connection.commit()

    def _ref(self, entity_id: int, external_id: str):
        self.connection.execute(
            "INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id)"
            " VALUES(?,'javdb','performer',?)", (entity_id, external_id))
        self.connection.commit()

    def test_one_performer_can_hold_two_pages_on_the_same_site(self):
        self._ref(10, "d45k9")
        self._ref(10, "ZX5z7")
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM entity_external_ref WHERE entity_id=10").fetchone()[0], 2)

    def test_the_same_page_cannot_be_claimed_twice(self):
        self._ref(10, "d45k9")
        with self.assertRaises(sqlite3.IntegrityError):
            self._ref(11, "d45k9")
        with self.assertRaises(sqlite3.IntegrityError):
            self._ref(10, "d45k9")

    def test_deleting_the_performer_takes_every_page_with_it(self):
        self._ref(10, "d45k9")
        self._ref(10, "ZX5z7")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("DELETE FROM entity WHERE id=10")
        self.connection.commit()
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM entity_external_ref").fetchone()[0], 0)


class OrphanSourceRowsTests(unittest.TestCase):
    """0039：删来源时没跟着走的子表行被清掉，还有主人的原样留下。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.db = self.root / "ledger.db"
        below = self.root / "below-0039"
        below.mkdir()
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name < "0039":
                shutil.copyfile(path, below / path.name)
        sqlite3.connect(self.db).close()
        upgrade(self.db, below)
        connection = sqlite3.connect(self.db)
        # 和服务的连接一样不开外键，才造得出生产上那种孤儿行。
        connection.executescript("""
          INSERT INTO follow_source(id,provider,ref,label,url,created_at,updated_at)
            VALUES(1,'rule34xxx','kept','kept','https://x.test/kept','t','t');
          INSERT INTO follow_item(id,source_id,external_id,title,release_key,first_seen_at,last_seen_at)
            VALUES(10,1,'a','kept','a','t','t'), (11,106,'b','orphan','b','t','t');
          INSERT INTO follow_playback(follow_item_id,play_count) VALUES(10,1),(11,1),(12,1);
          INSERT INTO feed_source(id,kind,url,created_at) VALUES(1,'javdb_actor','https://j.test/1','t');
          INSERT INTO feed_item(id,source_id,item_key,first_seen) VALUES(20,1,'k','t'),(21,9,'k','t');
          INSERT INTO feed_discovery(id,code,source_id,discovered_at)
            VALUES(30,'ABC-001',1,'t'),(31,'ABC-002',9,'t');
        """)
        connection.commit()
        connection.close()

    def test_rows_of_removed_sources_are_cleared(self):
        upgrade(self.db, MIGRATIONS)
        connection = sqlite3.connect(self.db)
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual([row[0] for row in connection.execute("SELECT id FROM follow_item")], [10])
        self.assertEqual([row[0] for row in connection.execute(
            "SELECT follow_item_id FROM follow_playback")], [10])
        self.assertEqual([row[0] for row in connection.execute("SELECT id FROM feed_item")], [20])
        # 发现过的作品是「这个番号还没入库」，不随订阅消失，只断开来源。
        self.assertEqual(connection.execute(
            "SELECT id,source_id FROM feed_discovery ORDER BY id").fetchall(), [(30, 1), (31, None)])


if __name__ == "__main__":
    unittest.main()
