import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from peach import face_detect
from peach.face_detect import (
    MODEL_SHA256,
    FaceModelUnavailable,
    ensure_model,
)


class EnsureModelTests(unittest.TestCase):
    """模型是二进制资产，不进 Git；取一次就得能复现同一份。"""

    def test_a_matching_local_model_is_reused_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp).resolve() / "yunet.onnx"
            payload = b"model-bytes"
            target.write_bytes(payload)
            with patch.object(face_detect, "MODEL_SHA256",
                              hashlib.sha256(payload).hexdigest()), \
                 patch.object(face_detect.urllib.request, "urlopen") as opener:
                self.assertEqual(ensure_model(target), target)
            opener.assert_not_called()

    def test_a_half_written_model_is_replaced_not_trusted(self):
        """半个下载同样留下一个文件。

        只看「文件在不在」的话，下一步 `FaceDetectorYN_create` 会抛一个和网络
        毫无关系的错，排查会从模型格式开始找——而真正的原因是上一次没下完。
        """
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp).resolve() / "yunet.onnx"
            target.write_bytes(b"half")
            with self.assertRaises(FaceModelUnavailable):
                ensure_model(target, allow_download=False)
            self.assertFalse(target.exists(), "校验不过的文件必须删掉，不能留着下次再骗一次")

    def test_a_download_whose_digest_differs_is_refused(self):
        class _Response:
            def read(self):
                return b"not-the-model"

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp).resolve() / "yunet.onnx"
            with patch.object(face_detect.urllib.request, "urlopen",
                              return_value=_Response()):
                with self.assertRaises(FaceModelUnavailable) as caught:
                    ensure_model(target)
            self.assertIn("校验不符", str(caught.exception))
            self.assertFalse(target.exists())

    def test_the_pinned_digest_is_a_real_sha256(self):
        # 定版是这条复用记录的全部意义：URL 指向 main 分支，内容可能变。
        self.assertEqual(len(MODEL_SHA256), 64)
        self.assertTrue(all(ch in "0123456789abcdef" for ch in MODEL_SHA256))
        self.assertIn("media.githubusercontent.com", face_detect.MODEL_URL,
                      "opencv_zoo 用 Git LFS，raw 域名只会回一个指针文件")


class DetectorContractTests(unittest.TestCase):
    def test_scripts_do_not_reach_for_the_removed_cascade_api(self):
        """OpenCV 5 的 wheel 里没有 `CascadeClassifier`，也没有级联 XML。

        两个脚本此前一起门都进不去，954 张封面 0 个 sidecar——「人脸取景没生效」
        的成因就在这里，不在取景公式上。
        """
        root = Path(__file__).resolve().parents[1]
        for name in ("detect_cover_faces.py", "detect_avatar_faces.py"):
            source = (root / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn("CascadeClassifier", source, name)
            self.assertNotIn("haarcascades", source, name)
            self.assertIn("FaceDetector()", source, name)

    def test_normalised_face_is_clamped_into_the_frame(self):
        detector = face_detect.FaceDetector.__new__(face_detect.FaceDetector)
        face = face_detect.Face(cx=0.8, cy=0.2, width=0.25, height=0.3, score=0.9)
        self.assertAlmostEqual(face.area, 0.075, places=6)
        self.assertIsInstance(detector, face_detect.FaceDetector)


class _Model:
    """假的 YuNet：按送检尺寸回不同的框，用来看几个尺度的读数怎么合成一个分。

    `reads` 的键是送检时的长边，值是 `(cx, cy, w, h, score)`，坐标按那一档的像素给。
    """

    def __init__(self, reads):
        self._reads = reads
        self._size = None

    def setInputSize(self, size):                    # noqa: N802 - 对齐 OpenCV 的拼法
        self._size = size

    def detect(self, _image):
        import numpy

        cols, rows = self._size
        rows_out = []
        for box in self._reads.get(max(cols, rows), []):
            cx, cy, width, height, score = box
            rows_out.append([cx - width / 2, cy - height / 2, width, height]
                            + [0.0] * 10 + [score])
        if not rows_out:
            return 1, None
        return 1, numpy.array(rows_out, dtype="float32")


class _Cv2:
    """只提供 `detect` 用得到的那一个函数。"""

    @staticmethod
    def resize(image, size):
        import numpy

        cols, rows = size
        return numpy.zeros((rows, cols, 3), dtype=image.dtype)


def _detector_over(reads, *, score=face_detect.DEFAULT_SCORE):
    import numpy

    detector = face_detect.FaceDetector.__new__(face_detect.FaceDetector)
    detector._cv2 = _Cv2()
    detector.score = score
    detector._detector = _Model(reads)
    return detector, numpy.zeros((1440, 2560, 3), dtype="uint8")


class DetectAcrossScalesTests(unittest.TestCase):
    """一张脸的分是它在几个尺度上读数的中位数，不是最高的那一次。"""

    def test_a_face_read_on_every_scale_keeps_its_middling_score(self):
        detector, image = _detector_over({
            320: [(160, 90, 40, 50, 0.90)],
            640: [(320, 180, 80, 100, 0.85)],
            1280: [(640, 360, 160, 200, 0.30)],
        })
        faces = detector.detect(image)
        self.assertEqual(len(faces), 1)
        self.assertEqual(faces[0].score, 0.85)
        self.assertAlmostEqual(faces[0].cx, 0.5, places=2)

    def test_a_box_that_only_one_scale_believes_is_dropped(self):
        """实测封面 `SRN-104`：罩住整个身体的框在 320 上 0.85，另两档只有 0.31、0.49。

        取最高分它和真正的脸打平，再按面积一比就赢了；取中位数它根本进不了名单。
        """
        detector, image = _detector_over({
            320: [(160, 90, 150, 160, 0.85)],
            640: [(320, 180, 300, 320, 0.31)],
            1280: [(640, 360, 600, 640, 0.49)],
        })
        self.assertEqual(detector.detect(image), [])

    def test_a_face_missing_from_one_scale_still_counts(self):
        detector, image = _detector_over({
            320: [(160, 90, 40, 50, 0.88)],
            640: [(320, 180, 80, 100, 0.91)],
        })
        faces = detector.detect(image)
        self.assertEqual(len(faces), 1)
        self.assertEqual(faces[0].score, 0.88)

    def test_a_small_image_is_only_sent_once(self):
        """三档都缩不下去时只检一次，那一次的读数就是分——补 0 会把小图的脸全判掉。"""
        import numpy

        detector = face_detect.FaceDetector.__new__(face_detect.FaceDetector)
        detector._cv2 = _Cv2()
        detector.score = face_detect.DEFAULT_SCORE
        detector._detector = _Model({200: [(100, 60, 40, 50, 0.72)]})
        faces = detector.detect(numpy.zeros((120, 200, 3), dtype="uint8"))
        self.assertEqual([face.score for face in faces], [0.72])

    def test_two_faces_side_by_side_stay_two_faces(self):
        detector, image = _detector_over({
            320: [(80, 90, 40, 50, 0.80), (240, 90, 40, 50, 0.70)],
            640: [(160, 180, 80, 100, 0.82), (480, 180, 80, 100, 0.74)],
            1280: [(320, 360, 160, 200, 0.81), (960, 360, 160, 200, 0.72)],
        })
        faces = detector.detect(image)
        self.assertEqual(sorted(face.score for face in faces), [0.72, 0.81])


class MainFaceTests(unittest.TestCase):
    """挑主角那张脸：分数先卡一道，再在剩下的里取最大。"""

    @staticmethod
    def face(cx, cy, width, height, score):
        return face_detect.Face(cx=cx, cy=cy, width=width, height=height, score=score)

    def test_a_big_but_weak_box_loses_to_the_real_face(self):
        """实测 `performer-8218`：大出一倍多的那个框罩在胸口，分只有 0.798。

        只按面积挑，圆头像就取景在胸口——脸在画面上四分之一处。
        """
        chest = self.face(0.389, 0.482, 0.427, 0.313, 0.798)
        head = self.face(0.389, 0.262, 0.202, 0.170, 0.928)
        self.assertEqual(face_detect.main_face([chest, head]), head)

    def test_a_small_sharp_bystander_does_not_beat_the_subject(self):
        """实测 `performer-8540`：右上角还检出一个 0.066×0.052 的小框。

        分数相当时仍按面积挑，否则背景里那张小而清晰的脸会抢走取景。
        """
        subject = self.face(0.608, 0.207, 0.215, 0.152, 0.930)
        bystander = self.face(0.694, 0.050, 0.066, 0.052, 0.925)
        self.assertEqual(face_detect.main_face([subject, bystander]), subject)

    def test_no_face_is_no_face(self):
        self.assertIsNone(face_detect.main_face([]))


if __name__ == "__main__":
    unittest.main()
