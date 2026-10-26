"""`scripts/demo_dataset.py`：SFW 演示库要能被 scan 与 process 离线消费。

README 承诺「文档中的演示素材仅限 SFW」，这里把承诺变成门槛：作品词表不得与
`catalog_rules` 的成人词表相交；生成的目录布局要和 `library_nfo` 的边车规则对得上；
`process_library` 跑完不能向任何外部来源发过请求。

`portrait` 模式的人像来自 `scripts/demo-portraits.json`。这些测试不联网，用本地造的
图片和注入的取图函数走同一条代码路径，验证的是清单的形状、字节校验与「卡片上的名字
就是封面上那位」。清单里每一张画面本身能不能见人由人工判定，测试不代替那一步。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

from peach import catalog_rules
from peach.ffmpeg import FFmpegResolver
from peach.field_owners import review_owner
from peach.library_nfo import local_art, read_nfo, sidecars
from peach.library_processing import process_library
from peach.review_csv import read_rows
from peach.settings_file import PeachConfig
from peach.metadata_auto_apply import _apply_metadata_candidate
from support.conditions import windows_ledger_roots
from support.ledger import fresh_ledger
from support.demo import seed_metadata_cache

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str = "demo_dataset"):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"peach_script_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DemoDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = load_script()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def _generate(self, name: str, count: int = 9, **kwargs) -> tuple[Path, list]:
        output = self.root / name
        items = self.demo.generate(output, count=count, seed=kwargs.pop("seed", 7),
                                   video=kwargs.pop("video", "stub"),
                                   duration=kwargs.pop("duration", 8), **kwargs)
        return output, items

    def test_vocabulary_is_disjoint_from_the_adult_tag_sets(self):
        adult = set().union(
            catalog_rules.ROLE_TAGS, catalog_rules.APPEARANCE_TAGS, catalog_rules.POSITION_TAGS,
            catalog_rules.STORY_TAGS, catalog_rules.RELATIONSHIP_TAGS, catalog_rules.SCENE_TAGS,
            catalog_rules.ATTRIBUTE_TAGS)
        vocabulary = set(self.demo.CONTENT_TAGS) | set(self.demo.SERIES)
        vocabulary |= set(self.demo.SYNTHETIC_PERFORMERS)
        vocabulary |= set(self.demo.CREATORS) | {name for _, name in self.demo.STUDIOS}
        vocabulary |= {title for title, _ in self.demo.TITLES} | set(self.demo.PLOTS)
        self.assertEqual(vocabulary & adult, set())
        for prefix, _ in self.demo.STUDIOS:
            self.assertNotIn(prefix, catalog_rules.KOREAN_MIB_PREFIXES)
            self.assertEqual(catalog_rules.release_code_from_filename(f"{prefix}-001.mp4"), f"{prefix}-001")

    def test_stub_generation_is_deterministic_and_lays_out_the_three_shapes(self):
        first, items = self._generate("one")
        second, _ = self._generate("two")
        manifest_one = json.loads((first / self.demo.MANIFEST_NAME).read_text(encoding="utf-8"))
        manifest_two = json.loads((second / self.demo.MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual(manifest_one["items"], manifest_two["items"])
        kinds = {item.kind for item in items}
        self.assertEqual(kinds, {"coded", "creator", "bare"})
        for item in items:
            self.assertTrue((first / item.path).is_file(), item.path)
            if item.kind == "coded":
                self.assertTrue((first / item.nfo).is_file())
                self.assertTrue((first / item.poster).is_file())
                self.assertEqual(Path(item.poster).name, f"{item.code}-poster.jpg")
            elif item.kind == "creator":
                self.assertTrue((first / item.nfo).is_file())
                self.assertEqual(Path(item.poster).suffix, ".png")
                self.assertEqual(Path(item.path).parent.name, item.creator)
            else:
                self.assertEqual((item.nfo, item.poster), ("", ""))
        galleries = [item for item in items if item.gallery]
        self.assertTrue(galleries)
        for photo in galleries[0].gallery:
            self.assertTrue((first / photo).is_file(), photo)
        self.assertEqual(Path(galleries[0].gallery[0]).parent.name, "P")

    def test_nfo_and_poster_follow_the_sidecar_rules(self):
        output, items = self._generate("nfo")
        coded = next(item for item in items if item.kind == "coded")
        video = output / coded.path
        nfo, posters = sidecars(video)
        self.assertEqual(nfo, output / coded.nfo)
        self.assertEqual(posters[0], output / coded.poster)
        payload, _ = read_nfo(nfo)
        self.assertEqual(payload["id"], coded.code)
        self.assertEqual(payload["title"], coded.title)
        self.assertEqual(payload["maker"], coded.studio)
        self.assertEqual(payload["series"], coded.series)
        self.assertEqual(payload["release_date"], coded.release_date)
        self.assertEqual([actor["japanese_name"] for actor in payload["actresses"]], coded.performers)
        self.assertEqual(payload["local_tags"], coded.tags)
        self.assertEqual(local_art(video, payload), output / coded.poster)
        made = next(item for item in items if item.kind == "creator")
        payload, _ = read_nfo(output / made.nfo)
        self.assertEqual(payload["id"], "")
        self.assertEqual(payload["title"], made.title)

    @windows_ledger_roots
    def test_scan_and_process_consume_the_tree_without_any_network_call(self):
        media, items = self._generate("media")
        db = fresh_ledger(self.root)
        config = PeachConfig(self.root, self.root / "config.toml", present=True,
                             locations={"local": (str(media),)})
        seed_metadata_cache(media, config.directory('sources'))
        factory = Mock()
        result = process_library(config, db, self.root / "generated", self.root / "covers",
                                 provider_factory=factory)
        factory.assert_not_called()
        coded = [item for item in items if item.kind == "coded"]
        self.assertTrue([item for item in items if item.kind == "bare"], "演示树要有裸文件")
        self.assertEqual(result["identified"], len(coded))
        # 裸文件没有番号，和创作者作品一样只是没番号的条目，不是问题项。
        self.assertEqual(result["issue_count"], 0)
        self.assertEqual(result["status"], "complete")
        for item in coded:
            self.assertTrue((self.root / "covers" / f"{item.code}.jpg").is_file(), item.code)
        with closing(sqlite3.connect(db)) as connection:
            connection.row_factory = sqlite3.Row
            videos = connection.execute("SELECT count(*) FROM asset WHERE medium='video'").fetchone()[0]
            images = connection.execute("SELECT count(*) FROM asset WHERE medium='image'").fetchone()[0]
            made_ids = [row["id"] for row in connection.execute(
                "SELECT id FROM asset WHERE medium='video' AND (code IS NULL OR code='') "
                "AND name LIKE '%.mp4' AND path NOT LIKE '%未整理%'")]
        self.assertEqual(videos, len(items))
        # 番号作品旁的 `-poster.jpg` 是附属文件，扫描不登记，封面照样从磁盘读（上面的 covers）。
        listed = [item for item in items if item.poster and not item.poster.endswith("-poster.jpg")]
        self.assertEqual(images, sum(len(item.gallery) for item in items) + len(listed))
        for asset_id in made_ids:
            self.assertTrue((self.root / "generated" / "posters" / f"{asset_id}_4.jpg").is_file())
        rows = read_rows(self.root / "generated" / "library-metadata-field-candidates.csv")
        fields = {(row["code"], row["field"]) for row in rows}
        for item in coded:
            for field in ("title", "original_title", "performers", "studio", "series", "release_date", "tags"):
                self.assertIn((item.code, field), fields, (item.code, field))
        studio_row = next(row for row in rows if row["code"] == coded[0].code and row["field"] == "studio")
        candidate = json.loads(studio_row["candidates_json"])[0]
        self.assertEqual(candidate["source"], "local_nfo")
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.row_factory = sqlite3.Row
            self.assertEqual(_apply_metadata_candidate(
                connection, studio_row, candidate, "2026-09-11", review_owner("local_nfo")), 1)
            self.assertEqual(connection.execute(
                "SELECT canonical_name FROM entity WHERE kind='studio'").fetchone()[0], coded[0].studio)

    def test_ffmpeg_mode_encodes_a_playable_clip(self):
        choice = FFmpegResolver(self.root).ffmpeg()
        probe = FFmpegResolver(self.root).ffprobe()
        if choice is None or probe is None:
            self.skipTest("ffmpeg is unavailable")
        try:
            output, items = self._generate("clip", count=1, video="ffmpeg", duration=2,
                                           ffmpeg=str(choice.path))
        except RuntimeError as error:
            self.skipTest(f"local ffmpeg cannot encode the fixture: {error}")
        result = subprocess.run(
            [str(probe.path), "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(output / items[0].path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertAlmostEqual(float(result.stdout.strip()), 2.0, delta=0.5)

    def test_generation_without_ffmpeg_is_refused_with_a_hint(self):
        with self.assertRaises(RuntimeError) as caught:
            self.demo.generate(self.root / "none", count=1, seed=1, video="ffmpeg", duration=2, ffmpeg=None)
        self.assertIn("--video stub", str(caught.exception))

    def test_next_steps_name_the_settings_lines_and_the_manifest(self):
        text = self.demo.next_steps(self.root / "demo")
        self.assertIn("[media.locations]", text)
        self.assertIn("peach init", text)
        self.assertIn("apply_metadata_tags.py", text)
        self.assertIn(self.demo.MANIFEST_NAME, text)


class PortraitManifestTests(unittest.TestCase):
    """人像清单与取图边界。不联网。"""

    @classmethod
    def setUpClass(cls):
        cls.portraits = load_script("demo_portraits")

    def test_manifest_entries_are_complete_unique_and_large_enough(self):
        entries = self.portraits.load_manifest()
        self.assertGreaterEqual(len(entries), 8)
        names = [entry.name for entry in entries]
        digests = [entry.sha256 for entry in entries]
        # 名字重复会让两条作品挂在同一位名下却是两张脸；哈希重复是同一张图的别名没去干净。
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual(len(set(digests)), len(digests))
        self.assertTrue(any(entry.landscape for entry in entries), "横屏作品需要横版人像")
        for entry in entries:
            self.assertEqual(len(entry.sha256), 64, entry.name)
            self.assertTrue(entry.name.strip() and "\n" not in entry.name, entry.name)
            # 竖版封面高 900，长边不到 640 的原图放大上去就糊了。
            self.assertGreaterEqual(max(entry.width, entry.height), 640, entry.name)
            self.assertTrue(entry.url.startswith("https://"), entry.name)

    def test_fetch_refuses_bytes_that_do_not_match_the_manifest(self):
        entry = self.portraits.load_manifest()[0]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError) as caught:
                self.portraits.fetch(entry, Path(tmp), opener=_opener(b"not the picture"))
            self.assertIn(entry.name, str(caught.exception))
            self.assertEqual(list(Path(tmp).glob("*.jpg")), [], "校验没过就不该留下文件")

    def test_fetch_keeps_bytes_whose_digest_matches(self):
        body = b"pretend this is a jpeg"
        entry = self.portraits.Portrait(
            name="试用", key="試用", category="1-Test", filename="試用.jpg",
            sha256=hashlib.sha256(body).hexdigest(), width=1500, height=2125)
        with tempfile.TemporaryDirectory() as tmp:
            first = self.portraits.fetch(entry, Path(tmp), opener=_opener(body))
            self.assertEqual(first.read_bytes(), body)
            # 命中缓存就不该再取一次；再给一个会抛的 opener，它不该被调用。
            again = self.portraits.fetch(entry, Path(tmp), opener=_refusing_opener)
            self.assertEqual(again, first)


class PortraitDatasetTests(unittest.TestCase):
    """`--art portrait` 的生成路径。人像用本地造的图，取图函数注入，不联网。"""

    @classmethod
    def setUpClass(cls):
        cls.demo = load_script()
        cls.portraits = load_script("demo_portraits")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.made: dict[str, Path] = {}
        self.entries = [
            self._entry("立花ひなの", 1500, 2125),
            self._entry("三条あかり", 1500, 2125),
            self._entry("長峰さやか", 2880, 1800),
            self._entry("御崎るか", 2880, 1800),
        ]

    def _entry(self, name: str, width: int, height: int):
        from PIL import Image

        path = self.root / "pretend" / f"{name}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (width, height), (90, 110, 150)).save(path, quality=70)
        self.made[name] = path
        return self.portraits.Portrait(
            name=name, key=name, category="1-Test", filename=f"{name}.jpg",
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            width=width, height=height)

    def _fetch(self, entry, cache_dir):
        return self.made[entry.name]

    def _generate(self, name: str, **kwargs):
        output = self.root / name
        items = self.demo.generate(
            output, count=kwargs.pop("count", 9), seed=kwargs.pop("seed", 7),
            video=kwargs.pop("video", "stub"), duration=kwargs.pop("duration", 4),
            art="portrait", portraits=self.entries, fetch=self._fetch, **kwargs)
        return output, items

    def test_every_work_names_the_face_that_is_on_its_cover(self):
        output, items = self._generate("faces")
        known = {entry.name for entry in self.entries}
        for item in items:
            self.assertIn(item.portrait, known, item.path)
            if item.kind == "coded":
                # 卡片上的出演者就是封面上那位；对不上细看就穿帮。
                self.assertEqual(item.performers, [item.portrait], item.code)
        manifest = json.loads((output / self.demo.MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual(manifest["art"], "portrait")

    def test_orientation_picks_a_portrait_of_the_same_shape(self):
        _, items = self._generate("shapes", count=16)
        shape = {entry.name: entry.landscape for entry in self.entries}
        for item in items:
            self.assertEqual(shape[item.portrait], item.orientation == "横屏", item.path)

    def test_covers_are_written_at_the_documented_sizes(self):
        from PIL import Image

        output, items = self._generate("covers")
        for item in items:
            if not item.poster:
                continue
            with Image.open(output / item.poster) as cover:
                expected = (self.portraits.COVER_PORTRAIT if item.orientation == "竖屏"
                            else self.portraits.COVER_LANDSCAPE)
                self.assertEqual(cover.size, expected, item.poster)

    def test_an_empty_manifest_is_refused_with_a_hint(self):
        with self.assertRaises(RuntimeError) as caught:
            self.demo.generate(self.root / "none", count=1, seed=1, video="stub",
                               duration=2, art="portrait", portraits=[])
        self.assertIn("--art synthetic", str(caught.exception))


def _opener(body: bytes):
    class _Response:
        def read(self, *args):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def opener(request, timeout=None):
        return _Response()

    return opener


def _refusing_opener(request, timeout=None):
    raise AssertionError("缓存命中后不该再联网")


if __name__ == "__main__":
    unittest.main()
