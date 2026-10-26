import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from peach import cover_artwork, jav_poster_crop
from peach.face_detect import Face, FaceModelUnavailable


def jpeg(colour, size=(840, 472)):
    output = io.BytesIO()
    Image.new("RGB", size, colour).save(output, "JPEG")
    return output.getvalue()


class Detector:
    def detect(self, image):
        return [Face(.72, .22, .1, .1, .9)]


class CoverArtworkTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.target = self.root / "259LUXU-1537.jpg"

    def test_javdb_images_are_rejected_before_replacing_any_file(self):
        from peach.jav_cover_fetch import Unavailable
        data = jpeg((80, 90, 100))
        self.target.write_bytes(data)
        from peach.sources.javdb import JAVDB
        evidence_list = [{'source_url': 'https://c0.' + domain + '/covers/x.jpg'}
                         for domain in (*JAVDB.domains, 'tp.spfcas.com')]
        for evidence in [*evidence_list, {'source': 'javdb-public'}]:
            with self.subTest(evidence=evidence), self.assertRaisesRegex(Unavailable, '无 JavDB 水印'):
                cover_artwork.install_cover(self.target, 'FC2-PPV-12345', data, (840, 472), evidence=evidence)
        self.assertEqual(self.target.read_bytes(), data)
        self.assertFalse(self.target.with_suffix('.face.json').exists())

    def test_a_paper_canvas_keeps_a_face_left_of_the_expected_spine(self):
        detector = Detector()
        with patch.object(detector, 'detect', return_value=[Face(.478, .094, .085, .155, .838)]):
            cover_artwork.install_cover(self.target, 'STARS-350', jpeg((80, 90, 100), (840, 594)),
                                        (840, 594), detector=detector)
        face = json.loads(self.target.with_suffix('.face.json').read_text(encoding='utf-8'))
        self.assertEqual(face['layout'], 'still')
        self.assertEqual(face['face']['cx'], .478)
        self.assertIsNone(jav_poster_crop.projection(jav_poster_crop.read_sidecar(self.target)))

    def test_a_paper_canvas_without_faces_preserves_the_complete_picture(self):
        detector = Detector()
        with patch.object(detector, 'detect', return_value=[]):
            cover_artwork.install_cover(self.target, 'SDDE-640', jpeg((80, 90, 100), (840, 594)),
                                        (840, 594), detector=detector)
        face = json.loads(self.target.with_suffix('.face.json').read_text(encoding='utf-8'))
        self.assertEqual(face['layout'], 'still')
        self.assertIsNone(jav_poster_crop.projection(jav_poster_crop.read_sidecar(self.target)))

    def test_centered_front_with_padding_keeps_its_panel(self):
        import numpy
        image = numpy.full((563, 840, 3), 40, numpy.uint8)
        image[:, 225:618] = numpy.random.default_rng(42).integers(100, 255, (563, 393, 3), dtype=numpy.uint8)
        output = io.BytesIO()
        Image.fromarray(image).save(output, 'PNG')
        detector = Detector()
        with patch.object(detector, 'detect', return_value=[Face(.487, .231, .135, .242, .93)]):
            cover_artwork.install_cover(self.target, 'HHL-018', output.getvalue(), (840, 563), detector=detector)
        face = json.loads(self.target.with_suffix('.face.json').read_text(encoding='utf-8'))
        self.assertNotIn('layout', face)
        box = jav_poster_crop.read_sidecar(self.target)['box']
        self.assertEqual(box['method'], jav_poster_crop.CENTER)
        self.assertLessEqual(abs(box['x0'] - 225), 1)
        self.assertLessEqual(abs(box['x1'] - 618), 1)

    def test_large_face_crossing_the_assumed_spine_uses_the_whole_cover(self):
        detector = Detector()
        with patch.object(detector, 'detect', return_value=[
                Face(.555, .331, .257, .422, .918), Face(.81, .3, .06, .1, .95)]):
            cover_artwork.install_cover(self.target, 'SPONE-001', jpeg((80, 90, 100), (800, 600)),
                                        (800, 600), detector=detector)
        face = json.loads(self.target.with_suffix('.face.json').read_text(encoding='utf-8'))
        self.assertEqual(face['layout'], 'still')
        self.assertEqual(face['face']['cx'], .555)
        self.assertIsNone(jav_poster_crop.projection(jav_poster_crop.read_sidecar(self.target)))

    def test_central_portrait_in_a_full_collage_keeps_its_face(self):
        detector = Detector()
        with patch.object(detector, 'detect', return_value=[Face(.508, .2, .09, .155, .917)]):
            cover_artwork.install_cover(self.target, 'MIDV-377', jpeg((80, 90, 100), (800, 600)),
                                        (800, 600), detector=detector)
        face = json.loads(self.target.with_suffix('.face.json').read_text(encoding='utf-8'))
        self.assertEqual(face['layout'], 'still')
        self.assertIsNone(jav_poster_crop.projection(jav_poster_crop.read_sidecar(self.target)))

    def test_dominant_left_portrait_without_a_spine_keeps_the_whole_picture(self):
        detector = Detector()
        with patch.object(detector, 'detect', return_value=[Face(.22, .3, .3, .4, .95), Face(.8, .3, .06, .1, .9)]):
            cover_artwork.install_cover(self.target, 'MIDV-865', jpeg((80, 90, 100), (800, 600)),
                                        (800, 600), detector=detector)
        face = json.loads(self.target.with_suffix('.face.json').read_text(encoding='utf-8'))
        self.assertEqual((face['version'], face['layout'], face['face']['cx']),
                         (cover_artwork.FACE_ALGORITHM_VERSION, 'still', .22))
        self.assertIsNone(jav_poster_crop.projection(jav_poster_crop.read_sidecar(self.target)))

    def test_four_three_collage_without_a_spine_keeps_the_largest_closed_eye_face(self):
        detector = Detector()
        with patch.object(detector, 'detect', return_value=[
                Face(.511, .435, .419, .717, .751), Face(.115, .128, .173, .227, .909)]):
            cover_artwork.install_cover(self.target, 'SONE-694', jpeg((80, 90, 100), (800, 600)),
                                        (800, 600), detector=detector)
        face = json.loads(self.target.with_suffix('.face.json').read_text(encoding='utf-8'))
        self.assertEqual((face['layout'], face['face']['cx']), ('still', .511))
        self.assertIsNone(jav_poster_crop.projection(jav_poster_crop.read_sidecar(self.target)))

    def test_a_large_weak_body_box_does_not_displace_the_portrait_on_a_full_canvas(self):
        detector = Detector()
        with patch.object(detector, 'detect', return_value=[
                Face(.389, .482, .427, .313, .798), Face(.389, .262, .202, .170, .928)]):
            cover_artwork.install_cover(self.target, 'SONE-694', jpeg((80, 90, 100), (800, 600)),
                                        (800, 600), detector=detector)
        face = json.loads(self.target.with_suffix('.face.json').read_text(encoding='utf-8'))
        self.assertEqual(face['face']['cy'], .262)

    def test_full_canvas_layout_is_available_without_a_face_model(self):
        for size in ((800, 600), (840, 594)):
            with self.subTest(size=size), patch('peach.cover_artwork._default_detector',
                                                side_effect=FaceModelUnavailable('未取得')):
                cover_artwork.install_cover(self.target, 'CAWD-186', jpeg((80, 90, 100), size), size)
            face = json.loads(self.target.with_suffix('.face.json').read_text(encoding='utf-8'))
            self.assertEqual(face['layout'], 'still')
            self.assertIsNone(face['face'])
            self.assertNotIn('version', face)
            self.assertIsNone(jav_poster_crop.projection(jav_poster_crop.read_sidecar(self.target)))

    def test_a_clear_spine_keeps_the_front_portrait_despite_a_larger_back_portrait(self):
        detector = Detector()
        with patch.object(detector, 'detect', return_value=[Face(.22, .3, .3, .4, .95), Face(.8, .3, .06, .1, .9)]):
            with patch.object(jav_poster_crop, 'front_panel_box', return_value={
                    'x0': 420, 'y0': 0, 'x1': 800, 'y1': 600, 'method': 'fold'}):
                cover_artwork.install_cover(self.target, 'ABW-232', jpeg((80, 90, 100), (800, 600)),
                                            (800, 600), detector=detector)
        face = json.loads(self.target.with_suffix('.face.json').read_text(encoding='utf-8'))
        self.assertNotIn('layout', face)
        self.assertEqual(face['face']['cx'], .8)

    def test_full_photo_keeps_faces_on_the_left_but_sleeves_use_the_front(self):
        detector = Detector()
        with patch.object(detector, "detect", return_value=[Face(.22, .3, .1, .1, .95)]):
            for code in ("FC2-PPV-3312576", "HEYZO-1380", "092415-001", "ABP-601"):
                with self.subTest(code=code):
                    target = self.root / f"{code}.jpg"
                    cover_artwork.install_cover(target, code, jpeg((90, 80, 70), (800, 500)),
                                                (800, 500), detector=detector)
                    record = json.loads(target.with_suffix(".face.json").read_text(encoding="utf-8"))
                    if code == "ABP-601":
                        self.assertIsNone(record["face"])
                    else:
                        self.assertEqual(record["face"]["cx"], .22)

    def test_install_replaces_image_and_both_focus_sidecars(self):
        data = jpeg((190, 120, 80))
        cover_artwork.install_cover(
            self.target, "259LUXU-1537", data, (840, 472), detector=Detector())
        self.assertEqual(self.target.read_bytes(), data)
        face = json.loads(self.target.with_suffix(".face.json").read_text(encoding="utf-8"))
        poster = jav_poster_crop.read_sidecar(self.target)
        self.assertEqual(face["face"]["cx"], .72)
        # 换头像按脸宽在封面上框方图，`px` 用来判这份记录是不是这张图的。
        self.assertEqual((face["px"], face["face"]["w"]), ([840, 472], .1))
        self.assertTrue(jav_poster_crop.is_current(poster, 840, 472))

    def test_bluray_title_frames_the_front_panel_and_its_face(self):
        detector = Detector()
        title = '作品 ブルーレイディスク'
        with patch.object(detector, 'detect', return_value=[
                Face(.2, .25, .2, .2, .95), Face(.72, .22, .1, .1, .94)]):
            cover_artwork.install_cover(self.target, 'MIDA-239', jpeg((90, 80, 70), (800, 438)),
                (800, 438), detector=detector, evidence={'title': title})
        poster = jav_poster_crop.read_sidecar(self.target)
        self.assertEqual(poster['box']['x0'], round(800 - .85 * 438))
        self.assertEqual(poster['title'], title)
        face = json.loads(self.target.with_suffix('.face.json').read_text(encoding='utf-8'))
        self.assertEqual(face['face']['cx'], .72)

    def test_fc2_title_keeps_the_whole_image_and_left_face(self):
        detector = Detector()
        with patch.object(detector, 'detect', return_value=[Face(.2, .25, .2, .2, .95)]):
            cover_artwork.install_cover(self.target, 'FC2-PPV-12345', jpeg((90, 80, 70), (800, 438)),
                (800, 438), detector=detector, evidence={'title': 'Blu-ray'})
        self.assertIsNone(jav_poster_crop.projection(jav_poster_crop.read_sidecar(self.target)))
        face = json.loads(self.target.with_suffix('.face.json').read_text(encoding='utf-8'))
        self.assertEqual(face['face']['cx'], .2)

    def test_vr_mark_keeps_the_left_main_face_and_whole_artwork(self):
        detector = Detector()
        with patch.object(detector, 'detect', return_value=[
                Face(.22, .25, .2, .2, .95), Face(.75, .2, .1, .1, .94)]):
            cover_artwork.install_cover(self.target, 'MDVR-276', jpeg((90, 80, 70), (800, 600)),
                (800, 600), detector=detector, evidence={'title': '【VR】作品'})
        self.assertIsNone(jav_poster_crop.projection(jav_poster_crop.read_sidecar(self.target)))
        face = json.loads(self.target.with_suffix('.face.json').read_text(encoding='utf-8'))
        self.assertEqual(face['face']['cx'], .22)
        self.assertEqual(face['layout'], 'still')

    def test_digital_collages_keep_the_whole_artwork_and_left_face(self):
        detector = Detector()
        with patch.object(detector, 'detect', return_value=[Face(.22, .8, .2, .2, .95)]):
            for code in ('IENEE-93103', 'IENFE-5301', 'SDHS-039'):
                with self.subTest(code=code):
                    cover_artwork.install_cover(self.target, code, jpeg((90, 80, 70), (800, 540)),
                        (800, 540), detector=detector)
                    self.assertIsNone(jav_poster_crop.projection(jav_poster_crop.read_sidecar(self.target)))
                    face = json.loads(self.target.with_suffix('.face.json').read_text(encoding='utf-8'))
                    self.assertEqual(face['face']['cx'], .22)
                    self.assertEqual(face['face']['cy'], .8)
                    self.assertEqual(face['layout'], 'still')

    def test_missing_face_model_removes_stale_focus_but_keeps_new_cover(self):
        self.target.write_bytes(jpeg((10, 10, 10), (800, 539)))
        self.target.with_suffix(".face.json").write_text('{"ratio":1,"face":{}}')
        self.target.with_suffix(".poster.json").write_text('{"version":"old"}')
        data = jpeg((240, 240, 240))
        with patch.object(cover_artwork, "_default_detector",
                          side_effect=FaceModelUnavailable("missing")):
            cover_artwork.install_cover(self.target, "259LUXU-1537", data, (840, 472))
        self.assertEqual(self.target.read_bytes(), data)
        self.assertFalse(self.target.with_suffix(".face.json").exists())
        self.assertTrue(jav_poster_crop.is_current(
            jav_poster_crop.read_sidecar(self.target), 840, 472))

    def test_preparation_failure_leaves_the_installed_set_untouched(self):
        old = jpeg((10, 10, 10), (800, 539))
        self.target.write_bytes(old)
        face = self.target.with_suffix(".face.json")
        face.write_text('{"old":true}', encoding="utf-8")
        with patch.object(cover_artwork, "_sidecars", side_effect=RuntimeError("failed")), \
                self.assertRaisesRegex(RuntimeError, "failed"):
            cover_artwork.install_cover(
                self.target, "259LUXU-1537", jpeg((20, 20, 20)), (840, 472))
        self.assertEqual(self.target.read_bytes(), old)
        self.assertEqual(face.read_text(encoding="utf-8"), '{"old":true}')

    def test_commit_failure_restores_the_installed_set(self):
        old = jpeg((10, 10, 10), (800, 539))
        self.target.write_bytes(old)
        face = self.target.with_suffix(".face.json")
        poster = self.target.with_suffix(".poster.json")
        face.write_text('{"old":"face"}', encoding="utf-8")
        poster.write_text('{"old":"poster"}', encoding="utf-8")
        replace = os.replace

        def fail_new_face(source, destination):
            source, destination = Path(source), Path(destination)
            if destination == face and source.suffix == ".tmp":
                raise OSError("commit failed")
            replace(source, destination)

        with patch.object(cover_artwork.os, "replace", side_effect=fail_new_face), \
                self.assertRaisesRegex(OSError, "commit failed"):
            cover_artwork.install_cover(
                self.target, "259LUXU-1537", jpeg((20, 20, 20)), (840, 472),
                detector=Detector())
        self.assertEqual(self.target.read_bytes(), old)
        self.assertEqual(face.read_text(encoding="utf-8"), '{"old":"face"}')
        self.assertEqual(poster.read_text(encoding="utf-8"), '{"old":"poster"}')

    def test_concurrent_installs_never_mix_image_and_sidecars(self):
        payloads = [jpeg((220, 20, 20)), jpeg((20, 20, 220))]
        errors = []
        def install(index):
            try:
                evidence = {"raw_sha256": str(index)}
                cover_artwork.install_cover(
                    self.target, "259LUXU-1537", payloads[index], (840, 472),
                    evidence=evidence, detector=Detector())
            except Exception as error:  # pragma: no cover - 失败时由主线程报告
                errors.append(error)
        workers = [threading.Thread(target=install, args=(index,)) for index in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(errors, [])
        winner = payloads.index(self.target.read_bytes())
        evidence = json.loads(self.target.with_suffix(".scraping.json").read_text(encoding="utf-8"))
        self.assertEqual(evidence["raw_sha256"], str(winner))


if __name__ == "__main__":
    unittest.main()
