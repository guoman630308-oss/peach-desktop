"""补头像这一批：认得准的装上，认不准的摆成对照表。

找错人是这件事唯一会出的大错。图库按名字存图，而 `ななみ` 这种单名下面躺着几十个人，
所以「只找出一张」是唯一敢自动装的判据，其余一张都不装。
"""
from __future__ import annotations

import importlib.util
import io
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from peach import avatar_picker, gfriends
from peach.http import HttpResponse

from support.ledger import fresh_ledger

REPO = Path(__file__).resolve().parents[1]

FILETREE = {
    "Content": {
        # 只有一个人的名字：图库里这一张就是她。
        "7-S1": {"吉良いろは.jpg": "吉良いろは.jpg?t=1"},
        # 同一个单名下面躺着好几个人，一张都不能自动挑。
        "0-Hand-Storage": {"ななみ.jpg": "ななみ.jpg?t=2"},
        "8-GRAPHIS": {"ななみ.jpg": "ななみ.jpg?t=3"},
        "y-Minnano": {"ななみ.jpg": "AI-Fix-ななみ.jpg?t=4"},
    }
}


def picture(width: int = 600, height: int = 900, colour: str = "red") -> bytes:
    """一张有起伏的图。整张一个颜色的会被当成来源的占位底色挡掉。"""
    buffer = io.BytesIO()
    image = Image.new("RGB", (width, height), colour)
    ImageDraw.Draw(image).rectangle((0, 0, width, height // 2), fill="black")
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


COLOURS = ("red", "green", "blue", "purple")


def transport_of(size: tuple[int, int]):
    """按地址给图：同一个名字下的几张是几个人，字节不能一样。

    存候选按内容哈希命名，全吐同一张的话三个人只会落下一个文件，而「摆在一起让人挑」
    正是这条用例要看的。
    """
    seen: dict[str, str] = {}

    def send(request, timeout=None, max_bytes=None):
        colour = seen.setdefault(request.url, COLOURS[len(seen) % len(COLOURS)])
        return HttpResponse(200, {}, picture(*size, colour=colour), request.url)
    return send


def load_script(name: str):
    """按文件路径加载脚本，先登记 `sys.modules` 再执行。"""
    sys.path.insert(0, str(REPO / "src"))
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PortraitGapTests(unittest.TestCase):
    def setUp(self):
        self.module = load_script("fill_portrait_gaps")
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.database = fresh_ledger(self.root)
        self.avatars = self.root / "avatars"
        self.providers = self.root / "provider-cache" / "performer-avatars"
        index_dir = self.providers / avatar_picker.GFRIENDS_CACHE
        index_dir.mkdir(parents=True, exist_ok=True)
        (index_dir / gfriends.INDEX_NAME).write_text(json.dumps(FILETREE),
                                                     encoding="utf-8")
        connection = sqlite3.connect(self.database)
        with connection:
            for entity_id, name in ((8713, "吉良いろは"), (8896, "ななみ"),
                                    (8000, "深田えいみ")):
                connection.execute(
                    "INSERT INTO entity(id,kind,canonical_name,normalized_name,"
                    "created_at,updated_at) VALUES(?,'performer',?,?,'t','t')",
                    (entity_id, name, name))
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,catalog_title,code,"
                "release_date) VALUES(41,'R:','R:\\Media\\a.mp4','a.mp4','video',"
                "'片子','ABC-001','2026-01-02')")
            connection.execute(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source)"
                " VALUES(41,8713,'performer','r18:performer')")
        connection.close()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def args(self, *extra):
        return self.module.build_parser().parse_args(
            ["--db", str(self.database), "--avatar-root", str(self.avatars),
             "--providers-root", str(self.providers),
             "--out", str(self.root / "gaps.csv"),
             "--sheet", str(self.root / "sheet"),
             "--base-url", "http://localhost:9999", *extra])

    def run_script(self, *extra, size: tuple[int, int] = (600, 900)):
        import peach.http as peach_http

        original = peach_http.HttpxTransport
        peach_http.HttpxTransport = lambda *a, **k: transport_of(size)
        try:
            self.module.run(self.args(*extra))
        finally:
            peach_http.HttpxTransport = original
        from peach.review_csv import read_rows
        return {row["name"]: row for row in read_rows(self.root / "gaps.csv")}

    def installed(self, entity_id: int) -> Path:
        return self.avatars / f"performer-{entity_id}.img"

    def test_a_name_the_gallery_knows_once_is_installed(self):
        rows = self.run_script("--apply")
        self.assertEqual(rows["吉良いろは"]["action"], "装上")
        self.assertTrue(self.installed(8713).exists())

    def test_a_name_several_people_share_installs_nothing(self):
        """`ななみ` 在图库里命中三张，那是三个人。自动挑等于随机安一张别人的脸。"""
        rows = self.run_script("--apply")
        self.assertEqual(rows["ななみ"]["action"], "多张（认不准）")
        self.assertFalse(self.installed(8896).exists())

    def test_a_name_the_gallery_does_not_have_is_recorded_as_such(self):
        rows = self.run_script("--apply")
        self.assertEqual(rows["深田えいみ"]["action"], "无候选")

    def test_without_apply_the_avatar_root_stays_empty(self):
        rows = self.run_script()
        self.assertEqual(rows["吉良いろは"]["action"], "装上")
        self.assertIn("未装", rows["吉良いろは"]["detail"])
        self.assertFalse(self.installed(8713).exists())

    def test_a_picture_below_the_size_bar_is_left_alone(self):
        """图库最后几档里有 200 px 的缩略图。装上去比留着首字母还糟。"""
        rows = self.run_script("--apply", size=(120, 180))
        self.assertEqual(rows["吉良いろは"]["action"], "图太小")
        self.assertFalse(self.installed(8713).exists())

    def test_the_contrast_sheet_shows_every_candidate_and_links_to_her_works(self):
        self.run_script("--apply")
        page = (self.root / "sheet" / "index.html").read_text(encoding="utf-8")
        self.assertIn("ななみ", page)
        self.assertEqual(page.count('<figure>'), 3)
        self.assertEqual(len(list((self.root / "sheet" / "candidates").iterdir())), 3)
        self.assertNotIn("吉良いろは", page)

    def test_the_sheet_points_at_this_library_not_at_the_gallery(self):
        """对着片子认人是唯一靠得住的判据，链接得能点开。"""
        connection = sqlite3.connect(self.database)
        with connection:
            connection.execute(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source)"
                " VALUES(41,8896,'performer','r18:performer')")
        connection.close()
        self.run_script("--apply")
        page = (self.root / "sheet" / "index.html").read_text(encoding="utf-8")
        self.assertIn("http://localhost:9999/assets/41", page)
        self.assertIn("ABC-001", page)

    def test_someone_who_already_has_a_portrait_is_not_a_target(self):
        self.avatars.mkdir(parents=True, exist_ok=True)
        self.installed(8713).write_bytes(picture())
        connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        try:
            names = {record["name"] for record in self.module.targets(connection,
                                                                      self.avatars)}
        finally:
            connection.close()
        self.assertNotIn("吉良いろは", names)

    def test_the_entry_hands_job_main_everything_it_takes(self):
        """`job_main` 要用 `--lock` 把 pid 锁包住整个 run。缺了它是启动就崩。"""
        args = self.module.build_parser().parse_args(["--sheet", "s"])
        self.assertTrue(args.lock)

    def test_the_people_with_the_most_works_come_first(self):
        """对照表要人一个个认，排在前面的该是库里真出现过几次的那些。"""
        connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        try:
            names = [record["name"] for record in self.module.targets(connection,
                                                                      self.avatars)]
        finally:
            connection.close()
        self.assertEqual(names[0], "吉良いろは")


if __name__ == "__main__":
    unittest.main()
