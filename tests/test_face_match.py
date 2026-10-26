"""人脸比对（SFace）：模型的取得、余弦、不可用时的退路。真模型不进测试，不出网。"""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from peach import face_detect, face_match
from peach.face_detect import Face, FaceModelUnavailable


class EnsureModelTests(unittest.TestCase):
    """与 YuNet 同一条取法：固定地址、校验 sha256，取不到就抛。"""

    def test_a_matching_local_model_is_reused_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp).resolve() / "sface.onnx"
            payload = b"sface-bytes"
            target.write_bytes(payload)
            with patch.object(face_match, "MODEL_SHA256",
                              hashlib.sha256(payload).hexdigest()), \
                 patch.object(face_detect.urllib.request, "urlopen") as opener:
                self.assertEqual(face_match.ensure_model(target), target)
            opener.assert_not_called()

    def test_a_missing_model_without_network_is_reported_not_guessed(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp).resolve() / "sface.onnx"
            with self.assertRaises(FaceModelUnavailable) as caught:
                face_match.ensure_model(target, allow_download=False)
            self.assertIn("人脸比对模型", str(caught.exception))

    def test_the_pinned_model_is_the_lfs_object(self):
        self.assertEqual(len(face_match.MODEL_SHA256), 64)
        self.assertIn("media.githubusercontent.com", face_match.MODEL_URL)
        self.assertEqual(face_match.COSINE_THRESHOLD, 0.363)


class CosineTests(unittest.TestCase):
    def test_same_direction_is_one_and_orthogonal_is_zero(self):
        self.assertAlmostEqual(face_match.cosine((1, 2, 3), (2, 4, 6)), 1.0)
        self.assertAlmostEqual(face_match.cosine((1, 0), (0, 1)), 0.0)
        self.assertEqual(face_match.cosine((0, 0), (1, 1)), 0.0)


class MatcherTests(unittest.TestCase):
    def test_an_unavailable_model_is_reported_once_and_compares_nothing(self):
        built = []

        def broken():
            built.append(1)
            raise FaceModelUnavailable("缺少人脸比对模型：sface.onnx")

        with patch.object(face_match, "FaceEmbedder", side_effect=broken):
            matcher = face_match.FaceMatcher()
            self.assertEqual(built, [], "用不上就不去取模型")
            self.assertIsNone(matcher.embedding(b"anything"))
            self.assertIsNone(matcher.embedding(b"again"))
        self.assertEqual(built, [1])
        self.assertIn("缺少人脸比对模型", matcher.unavailable)

    def test_a_face_without_landmarks_gives_no_feature(self):
        """摆正要五个关键点；框里没带就不比，不拿没摆正的脸去算分。"""
        import numpy

        class Detector:
            def detect(self, _image):
                return [Face(cx=0.5, cy=0.5, width=0.3, height=0.3, score=0.9)]

        embedder = face_match.FaceEmbedder.__new__(face_match.FaceEmbedder)
        embedder._detector = Detector()
        embedder._recognizer = None
        self.assertIsNone(embedder.embed(numpy.zeros((400, 400, 3), numpy.uint8)))


class SmallFaceCropTests(unittest.TestCase):
    """全身照上的小脸先裁出来放大再摆正；大脸和裁出来检不到的都照整图走。"""

    POINTS = (0.4, 0.4, 0.6, 0.4, 0.5, 0.5, 0.42, 0.6, 0.58, 0.6)

    def embedder(self, faces_on_crop):
        import cv2
        import numpy

        seen = []

        class Detector:
            def __init__(self, first):
                self.first = first

            def detect(self, image):
                seen.append(image.shape[:2])
                return self.first if len(seen) == 1 else faces_on_crop

        class Recognizer:
            def alignCrop(self, image, _box):
                seen.append(("aligned", image.shape[:2]))
                return image

            def feature(self, _aligned):
                return numpy.ones((1, 4), numpy.float32)

        embedder = face_match.FaceEmbedder.__new__(face_match.FaceEmbedder)
        embedder._cv2 = cv2
        embedder._recognizer = Recognizer()
        return embedder, Detector, seen

    def run_on(self, face_width, faces_on_crop):
        import numpy

        embedder, Detector, seen = self.embedder(faces_on_crop)
        embedder._detector = Detector([Face(cx=0.5, cy=0.2, width=face_width, height=face_width,
                                            score=0.9, landmarks=self.POINTS)])
        feature = embedder.embed(numpy.zeros((1000, 800, 3), numpy.uint8))
        return feature, seen

    def test_a_small_face_is_aligned_on_the_enlarged_crop(self):
        side = face_match.FACE_CROP_SIDE
        feature, seen = self.run_on(0.1, [Face(cx=0.5, cy=0.5, width=0.5, height=0.5,
                                                score=0.9, landmarks=self.POINTS)])
        self.assertEqual(feature, (1.0, 1.0, 1.0, 1.0))
        self.assertEqual(seen, [(1000, 800), (side, side), ("aligned", (side, side))])

    def test_a_large_face_is_aligned_on_the_whole_picture(self):
        _feature, seen = self.run_on(0.2, [])
        self.assertEqual(seen, [(1000, 800), ("aligned", (1000, 800))])

    def test_a_crop_without_a_usable_face_falls_back_to_the_whole_picture(self):
        _feature, seen = self.run_on(0.1, [Face(cx=0.5, cy=0.5, width=0.5, height=0.5, score=0.9)])
        side = face_match.FACE_CROP_SIDE
        self.assertEqual(seen, [(1000, 800), (side, side), ("aligned", (1000, 800))])


if __name__ == "__main__":
    unittest.main()
