"""头像去水印：位置先验、裁切让位、笔画 mask 与脚本的只读默认。"""
from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from peach import avatar_watermark as wm  # noqa: E402


def canvas(width: int, height: int, value: int = 120) -> np.ndarray:
    return np.full((height, width, 3), value, np.uint8)


def stamp(image: np.ndarray, box: tuple[int, int, int, int],
          value: int = 250) -> None:
    """在框里画几道亮笔画，模拟一处实心水印。"""
    x, y, w, h = box
    for offset in range(0, w, max(2, w // 8)):
        cv2.line(image, (x + offset, y + 2), (x + offset, y + h - 2),
                 (value, value, value), 2)


class EdgePriorTests(unittest.TestCase):
    def test_a_box_hugging_the_bottom_counts_as_a_watermark_position(self):
        self.assertTrue(wm.on_edge(wm.Mark(10, 960, 300, 40), 683, 1024))

    def test_a_box_in_the_middle_is_part_of_the_picture(self):
        self.assertFalse(wm.on_edge(wm.Mark(200, 500, 300, 40), 683, 1024))

    def test_each_of_the_four_edges_qualifies(self):
        width, height = 800, 600
        for mark in (wm.Mark(300, 0, 200, 40), wm.Mark(300, 560, 200, 40),
                     wm.Mark(0, 300, 100, 40), wm.Mark(700, 300, 100, 40)):
            with self.subTest(box=mark.box):
                self.assertTrue(wm.on_edge(mark, width, height))


class CropTests(unittest.TestCase):
    def test_a_bottom_watermark_is_cut_away(self):
        crop = wm.crop_box([wm.Mark(10, 960, 300, 40)], 683, 1024)
        self.assertEqual(crop, (0, 0, 683, 960))

    def test_watermarks_on_two_edges_are_both_cut(self):
        marks = [wm.Mark(13, 8, 168, 81), wm.Mark(242, 958, 417, 62)]
        self.assertEqual(wm.crop_box(marks, 683, 1024), (0, 89, 683, 869))

    def test_nothing_to_cut_without_marks(self):
        self.assertIsNone(wm.crop_box([], 683, 1024))

    def test_a_watermark_off_the_edges_cannot_be_cut(self):
        self.assertIsNone(wm.crop_box([wm.Mark(200, 500, 300, 40)], 683, 1024))

    def test_cutting_stops_before_it_eats_most_of_the_picture(self):
        # 贴着顶部边缘带下沿的一处水印，裁掉它要去掉 15% 以上的高度加上余量。
        tall = wm.crop_box([wm.Mark(0, 0, 683, 400)], 683, 1024)
        self.assertIsNone(tall)

    def test_the_cut_never_crosses_the_face(self):
        marks = [wm.Mark(13, 8, 168, 81)]
        face = (270, 60, 151, 199)
        self.assertIsNone(wm.crop_box(marks, 683, 1024, face))

    def test_a_cut_clear_of_the_face_still_happens(self):
        marks = [wm.Mark(13, 8, 168, 81)]
        face = (270, 152, 151, 199)
        self.assertEqual(wm.crop_box(marks, 683, 1024, face), (0, 89, 683, 935))


class RemainderTests(unittest.TestCase):
    def test_a_cut_away_watermark_leaves_nothing_behind(self):
        marks = [wm.Mark(10, 960, 300, 40)]
        self.assertEqual(wm.remaining(marks, (0, 0, 683, 960)), ())

    def test_coordinates_move_with_the_crop(self):
        marks = [wm.Mark(100, 500, 80, 30)]
        left = wm.remaining(marks, (0, 89, 683, 869))
        self.assertEqual([mark.box for mark in left], [(100, 411, 80, 30)])

    def test_without_a_crop_everything_remains(self):
        marks = [wm.Mark(200, 500, 300, 40)]
        self.assertEqual(wm.remaining(marks, None), tuple(marks))


class MaskTests(unittest.TestCase):
    def test_the_mask_covers_the_strokes_and_not_the_whole_box(self):
        image = canvas(400, 300)
        box = (40, 120, 200, 40)
        stamp(image, box)
        mask = wm.stroke_mask(image, [wm.Mark(*box)])
        x, y, w, h = box
        inside = mask[y:y + h, x:x + w]
        self.assertGreater(inside.mean(), 0)
        self.assertLess(float((inside > 0).mean()), 0.9)

    def test_nothing_outside_the_boxes_is_marked(self):
        image = canvas(400, 300)
        box = (40, 120, 200, 40)
        stamp(image, box)
        mask = wm.stroke_mask(image, [wm.Mark(*box)])
        mask[box[1] - wm.MASK_DILATE:box[1] + box[3] + wm.MASK_DILATE,
             box[0] - wm.MASK_DILATE:box[0] + box[2] + wm.MASK_DILATE] = 0
        self.assertEqual(int(mask.sum()), 0)

    def test_dark_strokes_on_a_light_patch_are_found_too(self):
        image = canvas(400, 300, 230)
        box = (40, 120, 200, 40)
        stamp(image, box, value=20)
        mask = wm.stroke_mask(image, [wm.Mark(*box)])
        self.assertGreater(mask[120:160, 40:240].mean(), 0)


class ScrubTests(unittest.TestCase):
    def test_an_image_without_marks_comes_back_untouched(self):
        image = canvas(400, 600)
        result, report = wm.scrub(image, [])
        self.assertIs(result, image)
        self.assertEqual(report["marks"], 0)
        self.assertIsNone(report["crop"])

    def test_an_edge_watermark_is_cut_and_nothing_is_repainted(self):
        image = canvas(400, 600)
        box = (40, 560, 300, 30)
        stamp(image, box)
        result, report = wm.scrub(image, [wm.Mark(*box)])
        self.assertEqual(report["crop"], [0, 0, 400, 560])
        self.assertEqual(report["inpainted"], 0)
        self.assertEqual(result.shape[:2], (560, 400))

    def test_a_watermark_the_crop_cannot_reach_is_repainted(self):
        image = canvas(400, 600)
        box = (40, 280, 300, 30)
        stamp(image, box)
        result, report = wm.scrub(image, [wm.Mark(*box)])
        self.assertIsNone(report["crop"])
        self.assertEqual(report["inpainted"], 1)
        self.assertEqual(result.shape, image.shape)
        # 修过之后那一块应该重新贴近底色，不再是一片亮笔画。
        patch = cv2.cvtColor(result[280:310, 40:340], cv2.COLOR_BGR2GRAY)
        self.assertLess(abs(float(patch.mean()) - 120), 12)

    def test_the_report_carries_the_size_after_cropping(self):
        image = canvas(400, 600)
        stamp(image, (40, 560, 300, 30))
        _, report = wm.scrub(image, [wm.Mark(40, 560, 300, 30)])
        self.assertEqual(report["px"], [400, 560])


class ModelTests(unittest.TestCase):
    def test_a_missing_model_is_reported_when_downloads_are_off(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(wm.WatermarkModelUnavailable):
                wm.ensure_model(Path(root) / "nope.onnx", allow_download=False)

    def test_a_file_that_fails_the_checksum_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "db.onnx"
            target.write_bytes(b"not a model")
            with self.assertRaises(wm.WatermarkModelUnavailable):
                wm.ensure_model(target, allow_download=False)


class FakeDetector:
    """脚本测试用的检出器。模型和网络都不参与，框由测试给定。"""

    def __init__(self, boxes: dict[str, list[wm.Mark]]) -> None:
        self.boxes = boxes
        self.name = ""

    def detect(self, image):
        return tuple(self.boxes.get(self.name, ()))


class FakeFaces:
    def detect(self, image):
        return []


class ScriptTests(unittest.TestCase):
    def setUp(self):
        import scrub_avatar_watermarks as script

        self.script = script

    def _avatar(self, root: Path, name: str, box: tuple[int, int, int, int]):
        image = canvas(400, 600)
        stamp(image, box)
        path = root / name
        path.write_bytes(cv2.imencode(".jpg", image)[1].tobytes())
        path.with_name(f"{name}.ct").write_text("image/jpeg", encoding="utf-8")
        path.with_name(f"{name}.provenance.json").write_text(
            json.dumps({"source_url": "https://example.test/a.jpg"}),
            encoding="utf-8")
        return path

    def _run(self, root: Path, boxes: dict, extra: list[str]) -> dict:
        source = root / "avatars"
        source.mkdir()
        for name, box in boxes.items():
            self._avatar(source, name, box)
        detector = FakeDetector({name: [wm.Mark(*box, 0.98)]
                                 for name, box in boxes.items()})

        def build(*args, **kwargs):
            return detector

        original = self.script.main

        def run():
            return original([
                "--source", str(source), "--archive", str(root / "old"),
                "--review", str(root / "review"),
                "--candidates", str(root / "cand.csv"), *extra])

        with mock.patch.object(wm, "MarkDetector", build), \
                mock.patch.object(self.script.face_detect, "FaceDetector",
                                  FakeFaces), \
                mock.patch.object(self.script.avatar_watermark, "MarkDetector",
                                  build):
            # 检出器按文件名给框，所以每张图检之前先告诉它现在检的是哪一张。
            real_detect = detector.detect
            names = list(boxes)

            def detect(image):
                detector.name = names[detect.calls % len(names)]
                detect.calls += 1
                return real_detect(image)

            detect.calls = 0
            detector.detect = detect
            self.assertEqual(run(), 0)
        return {"csv": root / "cand.csv", "source": source,
                "archive": root / "old", "review": root / "review"}

    def test_the_default_run_writes_candidates_but_not_the_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            boxes = {"creator-1.img": (40, 560, 300, 30)}
            out = self._run(root, boxes, [])
            before = (out["source"] / "creator-1.img").read_bytes()
            rows = list(csv.DictReader(
                out["csv"].read_text(encoding="utf-8").splitlines()))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["plan"], "crop")
            self.assertFalse(out["archive"].exists())
            self.assertTrue((out["review"] / "creator-1.png").is_file())
            self.assertEqual((out["source"] / "creator-1.img").read_bytes(),
                             before)

    def test_applying_archives_the_original_and_records_what_was_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            boxes = {"creator-1.img": (40, 560, 300, 30)}
            out = self._run(root, boxes, ["--apply"])
            self.assertTrue((out["archive"] / "creator-1.img").is_file())
            record = json.loads(
                (out["source"] / "creator-1.img.provenance.json")
                .read_text(encoding="utf-8"))
            self.assertEqual(record[self.script.STAMP]["crop"], [0, 0, 400, 560])
            self.assertEqual(record["height"], 560)
            self.assertEqual(
                (out["source"] / "creator-1.img.ct")
                .read_text(encoding="utf-8"), "image/jpeg")
            kept = cv2.imread(str(out["source"] / "creator-1.img"))
            self.assertEqual(kept.shape[:2], (560, 400))

    def test_an_image_already_scrubbed_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            boxes = {"creator-1.img": (40, 560, 300, 30)}
            source = root / "avatars"
            source.mkdir()
            path = self._avatar(source, "creator-1.img", (40, 560, 300, 30))
            path.with_name(f"{path.name}.provenance.json").write_text(
                json.dumps({self.script.STAMP: {"marks": 1}}), encoding="utf-8")
            detector = FakeDetector({"creator-1.img": [wm.Mark(40, 560, 300, 30, 0.98)]})
            with mock.patch.object(self.script.avatar_watermark, "MarkDetector",
                                   lambda *a, **k: detector), \
                    mock.patch.object(self.script.face_detect, "FaceDetector",
                                      FakeFaces):
                self.assertEqual(self.script.main([
                    "--source", str(source), "--archive", str(root / "old"),
                    "--review", str(root / "review"),
                    "--candidates", str(root / "cand.csv"), "--apply"]), 0)
            self.assertFalse((root / "old").exists())
            del boxes

    def test_hand_written_boxes_join_the_detected_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marks = root / "marks.csv"
            marks.write_text(
                "file,x,y,w,h\ncreator-1.img,40,280,300,30\n", encoding="utf-8")
            boxes = {"creator-1.img": (40, 560, 300, 30)}
            out = self._run(root, boxes, ["--marks", str(marks)])
            rows = list(csv.DictReader(
                out["csv"].read_text(encoding="utf-8").splitlines()))
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["origin"] for row in rows},
                             {"detected", "manual"})
            self.assertEqual({row["plan"] for row in rows}, {"crop+inpaint"})

    def test_a_marks_file_with_broken_rows_is_read_past(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "marks.csv"
            path.write_text("file,x,y,w,h\n"
                            ",1,2,3,4\n"
                            "a.img,x,2,3,4\n"
                            "b.img,1,2,0,4\n"
                            "c.img,1,2,3,4\n", encoding="utf-8")
            found = self.script.read_marks(path)
            self.assertEqual(list(found), ["c.img"])
            self.assertEqual(found["c.img"][0].box, (1, 2, 3, 4))


if __name__ == "__main__":
    unittest.main()
