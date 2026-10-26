"""媒体库分组、路径边界与配置往返。"""
import sqlite3
import tomllib
import unittest
from dataclasses import replace
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from peach import media_libraries, settings_file


class MediaLibraryTests(unittest.TestCase):
    def config(self):
        return SimpleNamespace(locations={"local": ("R:\\Media", "S:\\Other")},
                               library_names={"R:\\Media": "电影", "S:\\Other": "电影"})

    def test_named_roots_share_one_library(self):
        rows = media_libraries.libraries(self.config())
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["roots"]), 2)

    def test_library_icons_follow_source_or_explicit_selection(self):
        config = self.config()
        config.locations = {"pikpak": ("A:\\",)}
        self.assertEqual(media_libraries.libraries(config)[0]["icon"], "pikpak")
        config.library_icons = {"A:\\": "heart"}
        self.assertEqual(media_libraries.libraries(config)[0]["icon"], "heart")
        config.library_icons = {"A:\\": "<script>"}
        self.assertEqual(media_libraries.libraries(config)[0]["icon"], "pikpak")

    def test_filter_observes_source_and_directory_boundary(self):
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE asset (location TEXT, path TEXT)")
            db.executemany("INSERT INTO asset VALUES (?,?)", [
                ("local", "r:\\media\\one.mp4"), ("local", "R:\\MediaExtra\\two.mp4"),
                ("online", "R:\\Media\\three.mp4"), ("local", "S:\\Other\\four.mp4")])
            clause, args = media_libraries.predicate(self.config(), "电影")
            self.assertEqual(db.execute("SELECT count(*) FROM asset a WHERE " + clause, args).fetchone()[0], 2)
            clause, args = media_libraries.predicate(self.config(), "不存在")
            self.assertEqual(db.execute("SELECT count(*) FROM asset a WHERE " + clause, args).fetchone()[0], 0)

    def test_library_names_round_trip_toml(self):
        root, name = "R:\\Media", '电影 "精选"'
        line = f"{settings_file._render_value(root)} = {settings_file._render_value(name)}"
        self.assertEqual(tomllib.loads(line), {root: name})
        with TemporaryDirectory() as directory:
            config = settings_file.load_config(environ={"PEACH_DATA_ROOT": directory})
            config = replace(config, library_names={root: name}, library_icons={root: "star"})
            config.path.parent.mkdir(parents=True, exist_ok=True)
            config.path.write_text(settings_file.render(config), encoding="utf-8")
            loaded = settings_file.load_config(environ={"PEACH_DATA_ROOT": directory})
            self.assertEqual(loaded.library_names, {root: name})
            self.assertEqual(loaded.library_icons, {root: "star"})
