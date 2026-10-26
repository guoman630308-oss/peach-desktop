"""挂载来源注册表、配置保护与 SQL/逐项求值的一致性。"""
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from peach import media_configuration, media_sources, settings_file


class MediaSourceCostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.config = replace(settings_file.PeachConfig(self.root, self.root / "config.toml"),
            locations={"guangya": ("G:/Movies", "G:/Movies/Direct"),
                       "onedrive": ("O:/Public", "O:/Private"), "mounted": ("M:/",)},
            media_costs={"g:/movies": "metered", "G:/Movies/Direct": "free", "O:/Public": "free"})

    def test_registry_has_mount_sources_and_proxy_defaults(self):
        self.assertTrue({"onedrive", "guangya", "mounted"}.issubset(media_sources.SCANNABLE_LOCATIONS))
        self.assertEqual(media_sources.SOURCE_COSTS["guangya"], "free")
        self.assertEqual(media_sources.SOURCE_COSTS["115"], "free")
        self.assertEqual(media_sources.SOURCE_COSTS["pikpak"], "metered")
        self.assertEqual(media_sources.SOURCE_COSTS["onedrive"], "metered")
        roots, _, errors = media_configuration.validate([
            {"location": "onedrive", "path": "O:/", "cost": "free"},
            {"location": "mounted", "path": "M:/", "cost": "metered"}], windows=True)
        self.assertEqual(errors, [])
        self.assertEqual(set(roots), {"onedrive", "mounted"})

    def test_minimal_configuration_objects_use_source_defaults(self):
        for config in (SimpleNamespace(locations={"115": ("B:/",)}), SimpleNamespace()):
            self.assertEqual(media_sources.cost_for("115", "B:\\film.mp4", config), "free")
            expression, params = media_sources.cost_sql(config=config)
            with sqlite3.connect(":memory:") as connection:
                value = connection.execute(
                    f"SELECT {expression} FROM (SELECT '115' AS location,'B:\\film.mp4' AS path)", params).fetchone()[0]
            self.assertEqual(value, "free")

    def test_longest_root_directory_boundary_and_unknown_paths(self):
        cases = [
            ("guangya", "G:\\Movies\\film.mp4", "metered"),
            ("guangya", "g:\\movies\\Direct\\film.mp4", "free"),
            ("guangya", "G:\\MoviesOther\\film.mp4", "free"),
            ("onedrive", "O:\\Public\\film.mp4", "free"),
            ("onedrive", "O:\\Private\\film.mp4", "metered"),
            ("onedrive", "O:\\Outside\\film.mp4", "metered"),
            ("mounted", "M:\\film.mp4", "metered"),
            ("unknown", "X:\\film.mp4", "metered"),
            ("local", "R:\\film.mp4", "free"),
            ("115", "B:\\film.mp4", "free"),
        ]
        expression, params = media_sources.cost_sql("a.location", "a.path", config=self.config)
        with sqlite3.connect(":memory:") as connection:
            for location, path, expected in cases:
                with self.subTest(location=location, path=path):
                    actual = media_sources.cost_for(location, path, self.config)
                    result = connection.execute(
                        f"SELECT {expression} FROM (SELECT ? AS location, ? AS path) a",
                        (*params, location, path)).fetchone()[0]
                    self.assertEqual((actual, result), (expected, expected))

    def test_null_and_empty_paths_aggregate_configured_root_protection(self):
        config = replace(self.config, media_costs={"G:/Movies": "free", "G:/Movies/Direct": "free",
            "O:/Public": "free", "O:/Private": "metered", "M:/": "free"})
        expression, params = media_sources.cost_sql(config=config)
        with sqlite3.connect(":memory:") as connection:
            for location, expected in (("guangya", "free"), ("onedrive", "metered"), ("mounted", "free")):
                for path in (None, ""):
                    result = connection.execute(
                        f"SELECT {expression} FROM (SELECT ? AS location, ? AS path)",
                        (*params, location, path)).fetchone()[0]
                    self.assertEqual(result, expected)
                    self.assertEqual(media_sources.cost_for(location, config=config), expected)

    def test_sql_expression_can_filter_assets_with_bound_parameters(self):
        expression, params = media_sources.cost_sql(config=self.config)
        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE TABLE asset(location TEXT,path TEXT)")
            connection.executemany("INSERT INTO asset VALUES(?,?)", [
                ("onedrive", "O:\\Public\\film.mp4"), ("onedrive", "O:\\Private\\film.mp4")])
            rows = connection.execute(f"SELECT path FROM asset WHERE ({expression})=?",
                                      (*params, "free")).fetchall()
        self.assertEqual(rows, [("O:\\Public\\film.mp4",)])

    def test_config_roundtrip_preserves_root_overrides_and_effective_form_cost(self):
        settings_file.write(self.config)
        loaded = settings_file.load_config(environ={"PEACH_DATA_ROOT": str(self.root)})
        self.assertEqual(loaded.media_costs, self.config.media_costs)
        rows = media_configuration.rows(loaded, windows=True)
        self.assertEqual([row["cost"] for row in rows], ["metered", "free", "free", "metered", "metered"])
        costs = media_configuration.costs([{"location": "guangya", "path": "G:/Movies", "cost": "free"}],
                                         loaded, windows=True)
        self.assertEqual(costs["G:\\Movies"], "free")

    def test_invalid_cost_values_are_rejected_without_filesystem_work(self):
        for value in (True, "proxy", [], None):
            self.assertTrue(media_configuration.validate([
                {"location": "onedrive", "path": "O:/", "cost": value}], windows=True)[2])
        settings_file.write(replace(self.config, media_costs={"G:/": "unknown"}))
        with self.assertRaisesRegex(settings_file.SettingsFileError, "free.*metered"):
            settings_file.load_config(environ={"PEACH_DATA_ROOT": str(self.root)})
        with self.assertRaisesRegex(ValueError, "列名"):
            media_sources.cost_sql("a.location; DROP TABLE asset", config=self.config)
