"""从单人作品的本地封面与高清帧挑头像：只看单人、不在回收站的作品，脸最清楚的那张赢。"""
from __future__ import annotations

import hashlib
import io
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from peach import portrait_artwork
from peach.avatar_provider import AvatarCandidateCache
from support.ledger import fresh_ledger

PERFORMER = 7792
CO_STAR = 9001
RECORD = {"kind": "performer", "entity_id": PERFORMER, "canonical": "演示女优"}


def picture(width: int, height: int, colour: str) -> bytes:
    """一张有内容的 JPEG；`colour` 决定哈希，几张图在候选缓存里不串。"""
    buffer = io.BytesIO()
    image = Image.new("RGB", (width, height), colour)
    ImageDraw.Draw(image).rectangle((0, 0, width, height // 2), fill="black")
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


class CoverFallbackTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.covers = self.folder / "covers"
        self.posters = self.folder / "posters"
        self.covers.mkdir()
        self.posters.mkdir()
        self.cache = AvatarCandidateCache(self.folder / "candidates")
        self.connection = sqlite3.connect(fresh_ledger(self.folder))
        self.addCleanup(self.connection.close)
        with self.connection:
            for entity_id, name in ((PERFORMER, "演示女优"), (CO_STAR, "共演")):
                self.connection.execute(
                    "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
                    "VALUES(?,'performer',?,?,'t','t')", (entity_id, name, name))

    def add_asset(self, asset_id: int, code: str, *, performers=(PERFORMER,),
                  disposal: str | None = None) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,code,disposal) "
                "VALUES(?,'R:',?,?,'video',?,?)",
                (asset_id, f"R:\\media\\{code}.mp4", f"{code}.mp4", code, disposal))
            for entity_id in performers:
                self.connection.execute(
                    "INSERT INTO asset_entity(asset_id,entity_id,role,source) "
                    "VALUES(?,?,'performer','r18:performer')", (asset_id, entity_id))

    def pick(self, probe=None) -> dict | None:
        return portrait_artwork.cover_fallback(
            self.connection, RECORD, self.cache, self.covers, probe=probe, poster_root=self.posters)

    def test_only_solo_works_outside_the_recycle_bin_are_candidates(self):
        self.add_asset(1, "ABW-001")
        (self.covers / "ABW-001.jpg").write_bytes(picture(800, 538, "blue"))
        self.add_asset(2, "ABW-002", performers=(PERFORMER, CO_STAR))
        (self.covers / "ABW-002.jpg").write_bytes(picture(1600, 1076, "green"))
        self.add_asset(3, "ABW-003", disposal="trash")
        (self.covers / "ABW-003.jpg").write_bytes(picture(1600, 1076, "red"))
        chosen = self.pick()
        self.assertIsNotNone(chosen)
        self.assertEqual((chosen["provider"], chosen["external_id"], chosen["source_kind"]),
                         ("cover-fallback", "ABW-001", "single_performer_cover"))
        self.assertEqual(chosen["evidence"], "单人作品 ABW-001 完整封面")
        self.assertEqual((chosen["width"], chosen["height"]), (800, 538))
        self.assertTrue(Path(chosen["object_path"]).is_file())

    def test_small_covers_and_other_kinds_of_entity_give_nothing(self):
        self.add_asset(1, "ABW-001")
        (self.covers / "ABW-001.jpg").write_bytes(picture(300, 200, "blue"))
        self.assertIsNone(self.pick())
        (self.covers / "ABW-001.jpg").write_bytes(picture(800, 538, "blue"))
        self.assertIsNone(portrait_artwork.cover_fallback(
            self.connection, {**RECORD, "kind": "studio"}, self.cache, self.covers))

    def test_the_clearest_face_beats_the_biggest_picture(self):
        self.add_asset(1, "ABW-001")
        cover = picture(1600, 1076, "blue")
        frame = picture(640, 360, "green")
        (self.covers / "ABW-001.jpg").write_bytes(cover)
        (self.posters / "1_0003.jpg").write_bytes(frame)
        faces = {
            hashlib.sha256(cover).hexdigest(): {"face": {"w": 0.05}, "px": [1600, 1076]},
            hashlib.sha256(frame).hexdigest(): {"face": {"w": 0.5}, "px": [640, 360]},
        }
        chosen = self.pick(probe=lambda path: faces[hashlib.sha256(path.read_bytes()).hexdigest()])
        self.assertIsNotNone(chosen)
        self.assertEqual((chosen["provider"], chosen["source_kind"], chosen["face_width"]),
                         ("poster-fallback", "single_performer_frame", 320))
        self.assertEqual(chosen["evidence"], "单人作品 ABW-001 高清帧 1_0003")


if __name__ == "__main__":
    unittest.main()
