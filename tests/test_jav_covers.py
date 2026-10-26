import csv
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "src" / "peach" / "jav_cover_fetch.py"
_spec = importlib.util.spec_from_file_location("fetch_jav_covers", SCRIPT)
covers = importlib.util.module_from_spec(_spec)
sys.modules["fetch_jav_covers"] = covers
_spec.loader.exec_module(covers)


def jpeg(width, height):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (128, 90, 70)).save(buffer, format="JPEG")
    return buffer.getvalue()


class _Response:
    def __init__(self, status, body):
        self.status = status
        self.body = body


class FetchRetryTests(unittest.TestCase):
    def test_a_cooled_image_host_does_not_block_the_next_candidate_probe(self):
        blocked, available = 'https://one.example/high.jpg', 'https://two.example/cover.jpg'
        fallback = transport_for({available: (200, jpeg(1000, 674))})
        def transport(request, timeout, limit):
            if request.url == blocked:
                raise covers.SourcePaused('来源正在冷却')
            return fallback(request, timeout, limit)
        winner, size, _ = covers.best_cover(transport, 'FC2-PPV-123456', 0,
            prior_candidates=tuple(map(covers.candidate_for, (blocked, available))))
        self.assertEqual((winner.url, size), (available, (1000, 674)))

    def test_a_cooled_full_download_uses_the_next_measured_image(self):
        blocked, available = 'https://one.example/high.jpg', 'https://two.example/cover.jpg'
        def transport(request, timeout, limit):
            if 'Range' in request.headers:
                return _Response(200, jpeg(1200, 800) if request.url == blocked else jpeg(1000, 674))
            if request.url == blocked:
                raise covers.SourcePaused('来源正在冷却')
            return _Response(200, jpeg(1000, 674))
        winner, size, _ = covers.best_cover(transport, 'FC2-PPV-123456', 0,
            prior_candidates=tuple(map(covers.candidate_for, (blocked, available))))
        self.assertEqual((winner.url, size), (available, (1000, 674)))

    def test_all_cooled_images_remain_retryable_instead_of_becoming_missing(self):
        candidate = covers.candidate_for('https://one.example/cover.jpg')
        for phase in ('probe', 'download'):
            with self.subTest(phase=phase):
                def transport(request, timeout, limit, phase=phase):
                    if phase == 'download' and 'Range' in request.headers:
                        return _Response(200, jpeg(1000, 674))
                    raise covers.SourcePaused('来源正在冷却')
                with self.assertRaises(covers.SourcePaused):
                    covers.best_cover(transport, 'FC2-PPV-123456', 0, prior_candidates=(candidate,))

    def test_catalog_cooldown_preserves_a_known_usable_image(self):
        url = 'https://one.example/cover.jpg'
        with patch.object(covers, 'prestige_group_images', side_effect=covers.SourcePaused('来源正在冷却')):
            winner, _, _ = covers.best_cover(transport_for({url: (200, jpeg(1000, 674))}), 'ABW-232', 0,
                prior_candidates=(covers.candidate_for(url),))
        self.assertEqual(winner.url, url)

    def test_larger_official_cover_beats_a_smaller_product_image(self):
        large = covers.candidate_for('https://official.example/full.jpg')
        small = covers.candidate_for('https://image.mgstage.com/pb_e_product.jpg')
        winner, size, _ = covers.best_cover(transport_for({large.url: (200, jpeg(2184, 1468)),
            small.url: (200, jpeg(840, 563))}), 'FC2-PPV-123456', 0, prior_candidates=(small, large))
        self.assertEqual((winner, size), (large, (2184, 1468)))

    def test_mgs_releases_use_their_own_catalog_without_waiting_for_an_unrelated_mirror(self):
        url = 'https://image.mgstage.com/images/nanpatv/200gana/1995/pb_e_200gana-1995.jpg'
        with patch.object(covers, 'mgstage_images', return_value=[covers.candidate_for(url)]):
            with patch.object(covers, 'r18_evidence', side_effect=AssertionError('专属目录已取得')):
                with patch.object(covers, 'dmm_evidence', side_effect=AssertionError('MGS 不在 DMM 目录')):
                    _, size, _ = covers.best_cover(transport_for({url: (200, jpeg(840, 472))}), '200GANA-1995', 0)
        self.assertEqual(size, (840, 472))

    def test_prestige_code_routing_uses_the_official_group_without_a_snapshot(self):
        url = 'https://www.prestige-av.com/api/media/package.jpg'
        with patch.object(covers, 'prestige_group_images', return_value=[covers.candidate_for(url)]) as group:
            with patch.object(covers, 'r18_evidence', side_effect=AssertionError('片商目录已取得')):
                _, size, _ = covers.best_cover(transport_for({url: (200, jpeg(1600, 1000))}), 'ABW-192', 0)
        self.assertEqual(size, (1600, 1000))
        group.assert_called_once()
    def test_empty_snapshots_use_the_identified_dmm_catalog_and_largest_image(self):
        COVER = 'https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/ssis00057/ssis00057pl.jpg'
        ENDPOINT = 'https://api.video.dmm.co.jp/graphql'
        item = {'id': 'ssis00057', 'title': '作品', 'makerContentId': 'SSIS-057',
                'packageImage': {'largeUrl': COVER}}
        modern = COVER.replace('awsimgsrc.dmm.co.jp/pics_dig/', 'awsimgsrc.dmm.com/dig/')
        transport = transport_for({
            ENDPOINT: (200, json.dumps({'data': {'ppvContent': item}}).encode()),
            COVER: (200, jpeg(800, 540)), modern: (200, jpeg(2184, 1468)),
        })
        with patch.object(covers, 'r18_evidence') as mirror:
            winner, size, _ = covers.best_cover(transport, 'SSIS-057', 0)
        self.assertEqual(winner.url, modern)
        self.assertEqual(size, (2184, 1468))
        mirror.assert_not_called()

    def test_maker_small_image_and_a_missing_mirror_do_not_suppress_dmm_lookup(self):
        factory = 'https://cdn.up-timely.com/image/32/cover.jpg'
        original = 'https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/cawd00447/cawd00447pl.jpg'
        modern = original.replace('awsimgsrc.dmm.co.jp/pics_dig/', 'awsimgsrc.dmm.com/dig/')
        item = {'id': 'cawd00447', 'title': '作品', 'makerContentId': 'CAWD-447',
                'packageImage': {'largeUrl': original}}
        guessed = covers.candidate_for('https://awsimgsrc.dmm.com/dig/digital/video/1cawd00447/1cawd00447pl.jpg')
        evidence = covers.MetadataEvidence((covers.candidate_for(factory), guessed), sources=frozenset({'makers', 'r18dev'}))
        winner, size, _ = covers.best_cover(transport_for({factory: (200, jpeg(800, 538)),
            'https://api.video.dmm.co.jp/graphql': (200, json.dumps({'data': {'ppvContent': item}}).encode()),
            modern: (200, jpeg(2184, 1468))}), 'CAWD-447', 0, known=evidence)
        self.assertEqual((winner.url, size), (modern, (2184, 1468)))

    def test_catalog_connection_failure_is_not_a_definitive_missing_cover(self):
        diagnostics = {}
        with patch.object(covers, 'dmm_evidence', return_value=covers.MetadataEvidence()):
            with self.assertRaises(covers.Unavailable) as raised:
                covers.best_cover(lambda *args: _Response(503, b''), 'MIDA-085', 0, diagnostics=diagnostics)
        self.assertNotIsInstance(raised.exception, covers.NotFound)
        self.assertGreater(diagnostics.get('source_http_503', 0), 0)

    def test_official_catalog_probe_preserves_source_cooldown_writes(self):
        from unittest.mock import Mock
        transport = Mock(return_value=_Response(429, b''))
        transport.hold = Mock()
        with self.assertRaises(covers.Unavailable):
            covers.best_cover(transport, 'MIDA-085', 0)
        self.assertTrue(any(call.args[0] == 'dmm' for call in transport.hold.call_args_list))

    def test_cover_rejections_report_measured_sizes_and_decode_failures(self):
        candidate = covers.Candidate("test", "https://example.test/cover.jpg")
        for size, expected in [((600, 400), "too_small"), ((1600, 1000), "invalid_image")]:
            diagnostics = {}
            with patch.object(covers, "cached_metadata", return_value=covers.MetadataEvidence()), \
                    patch.object(covers, "probe_size", return_value=size), \
                    patch.object(covers, "_fetch", return_value=b"invalid"):
                with self.assertRaises(covers.Unavailable):
                    covers.best_cover(lambda *args: None, "FC2-PPV-123456", 0,
                                      prior_candidates=(candidate,), diagnostics=diagnostics)
            self.assertEqual(diagnostics, {expected: 1})

    def test_official_placeholders_only_say_the_work_was_taken_down(self):
        """DMM 下架的片各版本都 302 到「准备中」，原因要说成下架，不是没取到图。"""
        candidate = covers.Candidate("test", "https://example.test/cover.jpg")
        diagnostics = {}
        with patch.object(covers, "cached_metadata", return_value=covers.MetadataEvidence()), \
                patch.object(covers, "probe_size", side_effect=covers.NotFound(covers.PLACEHOLDER_REASON)):
            with self.assertRaisesRegex(covers.Unavailable, f"^{covers.OFFICIAL_PLACEHOLDER_ONLY}$"):
                covers.best_cover(lambda *args: None, "FC2-PPV-123456", 0,
                                  prior_candidates=(candidate,), diagnostics=diagnostics)
        self.assertEqual(diagnostics, {"placeholder": 1})

    def test_source_saying_no_is_told_apart_from_source_trouble(self):
        """404 与「所有渠道都没有候选」是来源明确说没有，采集任务据此记住一阵子；
        5xx 与候选探测失败下次再问可能就好了，仍是普通的 Unavailable。"""
        with self.assertRaises(covers.NotFound):
            covers._fetch(lambda *args: _Response(404, b""), "https://example.test/x", referer="r", limit=10)
        with self.assertRaises(covers.Unavailable) as raised:
            covers._fetch(lambda *args: _Response(503, b""), "https://example.test/x", referer="r", limit=10)
        self.assertNotIsInstance(raised.exception, covers.NotFound)
        with patch.object(covers, "cached_metadata", return_value=covers.MetadataEvidence()):
            with self.assertRaises(covers.NotFound):
                covers.best_cover(lambda *args: None, "FC2-PPV-123456", 0)
        candidate = covers.Candidate("test", "https://example.test/cover.jpg")
        with patch.object(covers, "cached_metadata", return_value=covers.MetadataEvidence()), \
                patch.object(covers, "probe_size", side_effect=covers.Unavailable("HTTP 503")):
            with self.assertRaises(covers.Unavailable) as raised:
                covers.best_cover(lambda *args: None, "FC2-PPV-123456", 0, prior_candidates=(candidate,))
        self.assertNotIsInstance(raised.exception, covers.NotFound)

    def test_candidates_that_all_answered_no_are_a_definitive_miss(self):
        """FC2 卖家删了商品图，官方页上的两张存储地址都回 404：来源答过了，是确定的没有。
        混着一张 5xx 时那一张可能还在，仍是这趟没问成。"""
        candidates = (covers.Candidate("a", "https://storage1.contents.fc2.com/a.jpg"),
                      covers.Candidate("b", "https://storage2.contents.fc2.com/b.jpg"))

        def best(side_effect):
            with patch.object(covers, "cached_metadata", return_value=covers.MetadataEvidence()), \
                    patch.object(covers, "probe_size", side_effect=side_effect):
                covers.best_cover(lambda *args: None, "FC2-PPV-123456", 0, prior_candidates=candidates)

        with self.assertRaisesRegex(covers.NotFound, f"^{covers.NO_USABLE_OFFICIAL[0]}$"):
            best([covers.NotFound("HTTP 404"), covers.NotFound("HTTP 404")])
        with self.assertRaisesRegex(covers.NotFound, f"^{covers.NO_USABLE_OFFICIAL[1]}$"):
            best([covers.NotFound("HTTP 404"), (200, 200)])
        with self.assertRaises(covers.Unavailable) as raised:
            best([covers.NotFound("HTTP 404"), covers.Unavailable("HTTP 503")])
        self.assertNotIsInstance(raised.exception, covers.NotFound)

    def test_every_candidate_cut_off_at_the_connection_is_a_route_problem(self):
        """中国移动宽带直连 DMM 图片主机时，每一张候选都断在握手上：这说的是线路，
        不是官方没有图；只要有一家回过话，就仍按来源的回答判。"""
        candidates = (covers.Candidate("a", "https://pics.dmm.co.jp/a.jpg"),
                      covers.Candidate("b", "https://awsimgsrc.dmm.co.jp/b.jpg"))
        cut = covers.httpx.ConnectError("EOF occurred in violation of protocol")

        def best(side_effect):
            with patch.object(covers, "cached_metadata", return_value=covers.MetadataEvidence()), \
                    patch.object(covers, "probe_size", side_effect=side_effect):
                covers.best_cover(lambda *args: None, "FC2-PPV-123456", 0, prior_candidates=candidates)

        with self.assertRaisesRegex(covers.CoverConnectError, f"^{covers.HOSTS_UNREACHABLE}$"):
            best([cut, cut])
        with self.assertRaises(covers.CoverConnectError):
            best([cut, covers.DeadlineExceeded("预算用尽")])
        with self.assertRaises(covers.Unavailable) as raised:
            best([cut, covers.Unavailable("HTTP 503")])
        self.assertNotIsInstance(raised.exception, covers.CoverConnectError)
        with self.assertRaises(covers.DeadlineExceeded):
            best([covers.Unavailable("HTTP 503"), covers.DeadlineExceeded("预算用尽")])

    def test_full_download_dimensions_must_match_probe_and_exceed_existing_image(self):
        candidate = covers.Candidate("test", "https://example.test/large.jpg")
        with patch.object(covers, "cached_metadata", return_value=covers.MetadataEvidence()), \
                patch.object(covers, "probe_size", return_value=(1600, 1000)), \
                patch.object(covers, "_fetch", return_value=jpeg(800, 500)):
            with self.assertRaises(covers.Unavailable):
                covers.best_cover(lambda *args: None, "FC2-PPV-123456", 0,
                                  prior_candidates=(candidate,), minimum_pixels=1000 * 700)

    def test_an_animated_official_candidate_is_skipped_for_a_still_one(self):
        """JavArchive 在 FC2 链末档，图床存的常是 GIF 预览动画，只读头部量不出它在动。"""
        frames = [Image.new("RGB", (800, 450), color) for color in ("red", "blue")]
        buffer = io.BytesIO()
        frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:])
        moving = covers.Candidate("img.javstore.net", "https://img.javstore.net/images/FC2PPV-123456.gif")
        still = covers.Candidate("img.javstore.net", "https://img.javstore.net/images/123456pl.jpg")
        pages = {moving.url: (200, buffer.getvalue()), still.url: (200, jpeg(800, 450))}
        with patch.object(covers, "cached_metadata", return_value=covers.MetadataEvidence()), \
                patch.object(covers, "probe_size", return_value=(800, 450)):
            chosen, _size, _data = covers.best_cover(transport_for(pages), "FC2-PPV-123456", 0,
                                                     prior_candidates=(moving, still))
            self.assertEqual(chosen.url, still.url)
            with self.assertRaisesRegex(covers.NotFound, "动图"):
                covers.best_cover(transport_for(pages), "FC2-PPV-123456", 0, prior_candidates=(moving,))

    def test_transient_transport_errors_use_the_project_backoff_window(self):
        attempts = 0

        def transport(request, timeout, limit):
            nonlocal attempts
            attempts += 1
            if attempts < 5:
                raise covers.httpx.ConnectError("temporary TLS EOF")
            return _Response(200, b"ok")

        with patch.object(covers.time, "sleep") as sleep:
            body = covers._fetch(
                transport, "https://example.test/cover.jpg",
                referer="https://example.test/", limit=100,
            )

        self.assertEqual(body, b"ok")
        self.assertEqual(attempts, 5)
        self.assertEqual(
            sleep.call_args_list,
            [call(2), call(4), call(6), call(8)],
        )

    def test_exhausted_transport_error_discards_the_poisoned_pool(self):
        class Transport:
            closed = False

            def close(self):
                self.closed = True

        original = Transport()
        replacement = Transport()
        with patch.object(covers, "HttpxTransport", return_value=replacement):
            actual = covers._renew_transport_after_error(
                original, covers.httpx.PoolTimeout("pool exhausted"),
            )

        self.assertTrue(original.closed)
        self.assertIs(actual, replacement)

    def test_confirmed_absence_keeps_the_healthy_pool(self):
        transport = object()
        actual = covers._renew_transport_after_error(
            transport, covers.Unavailable("no candidates"),
        )
        self.assertIs(actual, transport)


class HostLimitedTransportTests(unittest.TestCase):
    def test_each_host_has_an_independent_request_clock(self):
        now = [0.0]
        waits = []
        requested = []

        def clock():
            return now[0]

        def sleep(seconds):
            waits.append(seconds)
            now[0] += seconds

        def inner(request, timeout, limit):
            requested.append(request.url)
            return _Response(200, b"ok")

        transport = covers.HostLimitedTransport(
            inner, 1.5, clock=clock, sleeper=sleep,
        )
        transport(covers.HttpRequest("GET", "https://a.example/1", {}), 30, 10)
        now[0] = 0.2
        transport(covers.HttpRequest("GET", "https://b.example/1", {}), 30, 10)
        transport(covers.HttpRequest("GET", "https://a.example/2", {}), 30, 10)

        self.assertEqual(requested, [
            "https://a.example/1", "https://b.example/1", "https://a.example/2",
        ])
        self.assertEqual(waits, [1.3])


def transport_for(pages):
    """pages: URL -> (status, bytes)。未列出的 URL 返回 404。"""
    def call(request, timeout, limit):
        status, body = pages.get(request.url, (404, b""))
        return _Response(status, body)
    return call


class CodeShapeTests(unittest.TestCase):
    def test_amateur_prefix_is_stripped_as_a_second_try(self):
        # 实测：`278GYAN-017` 查不到，去掉三位厂牌前缀后的 `GYAN-017` 能查到。
        self.assertEqual(covers.code_variants("278GYAN-017"), ["278GYAN-017", "GYAN-017"])

    def test_studio_codes_have_no_second_variant(self):
        self.assertEqual(covers.code_variants("ABW-232"), ["ABW-232"])

    def test_codes_are_normalised_to_three_digits(self):
        # 归一化本体收进了 catalog_rules，脚本 import 它；这里验的是脚本用的
        # 确实是那一份，而不是又抄了一个同名函数。
        self.assertEqual(covers.normalise_code_key("abw232"), "ABW-232")
        self.assertEqual(covers.normalise_code_key("ABW-0232"), "ABW-232")

    def test_amateur_prefix_survives_normalisation(self):
        self.assertEqual(covers.normalise_code_key("278gyan17"), "278GYAN-017")


class ContentIdTests(unittest.TestCase):
    def test_digits_are_padded_to_five_places(self):
        # `waaa415` 返回 404，`waaa00415` 才拿得到 2184x1468。
        self.assertIn("waaa00415", covers.cid_variants("waaa415"))

    def test_original_form_is_tried_first(self):
        self.assertEqual(covers.cid_variants("bazx00302")[0], "bazx00302")

    def test_maker_prefixed_ids_still_yield_a_bare_form(self):
        self.assertIn("abw00232", covers.cid_variants("118abw232"))

    def test_empty_content_id_yields_nothing(self):
        self.assertEqual(covers.cid_variants(""), [])

    def test_dmm_urls_expand_to_modern_and_legacy_cdn_routes(self):
        original = "https://pics.dmm.co.jp/mono/movie/adult/118abw232/118abw232pl.jpg"
        urls = {candidate.url for candidate in covers.dmm_cdn_images(original)}
        self.assertIn(
            "https://awsimgsrc.dmm.com/dig/mono/movie/118abw232/118abw232pl.jpg",
            urls,
        )
        self.assertIn(
            "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/118abw232/118abw232pl.jpg",
            urls,
        )

    def test_an_address_from_elsewhere_counts_only_on_an_official_image_path(self):
        dmm = "https://pics.dmm.co.jp/digital/video/dazd00308/dazd00308pl.jpg"
        self.assertEqual({candidate.url for candidate in covers.official_image_candidates(dmm)},
                         {candidate.url for candidate in covers.dmm_cdn_images(dmm)})
        mgs = "https://image.mgstage.com/images/prestige/sp/pasn/031/pake-03_sp-pasn-031.jpg"
        self.assertEqual([candidate.url for candidate in covers.official_image_candidates(mgs)],
                         [mgs])
        # 社区站图床给的地址核对不了是哪一部，不当官方候选。
        self.assertEqual(covers.official_image_candidates(
            "https://c0.jdbstatic.com/covers/xe/XeVp0e.jpg"), [])
        self.assertEqual(covers.official_image_candidates(""), [])

    def test_a_listed_address_is_installed_when_no_official_channel_knows_the_code(self):
        """r18 与本机快照都没有这部时，来源给的 DMM 封套就是唯一的候选，照样量尺寸、下载。"""
        url = "https://pics.dmm.co.jp/digital/video/dazd00308/dazd00308pl.jpg"
        body = jpeg(800, 538)

        def transport(request, timeout, limit):
            return _Response(200, body) if request.url == url else _Response(404, b"")

        candidate, size, data = covers.best_cover(
            transport, "DAZD-308", 0,
            prior_candidates=tuple(covers.official_image_candidates(url)))
        self.assertEqual((candidate.url, size, data), (url, (800, 538), body))


class OfficialSourceTests(unittest.TestCase):
    def test_fc2_archive_cover_is_upgraded_to_the_measured_hires_rendition(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fc2-candidate-log.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=[
                    "code", "result", "is_collection", "cover_url",
                ])
                writer.writeheader()
                writer.writerow({
                    "code": "FC2 3701252", "result": "取得", "is_collection": "",
                    "cover_url": "https://contents-thumbnail2.fc2.com/w276/x/cover.jpg",
                })
            candidate = covers.fc2_cover_candidates(path)["FC2-PPV-3701252"]
        self.assertEqual(candidate.url,
                         "https://contents-thumbnail2.fc2.com/w1200/x/cover.jpg")
        self.assertEqual(candidate.referer, "https://fc2cmadb.com/")

    def test_fc2_hires_candidate_skips_unrelated_jav_discovery(self):
        url = "https://contents-thumbnail2.fc2.com/w1200/x/cover.jpg"
        candidate = covers.Candidate("contents-thumbnail2.fc2.com", url,
                                     "https://fc2cmadb.com/")
        with patch.object(covers, "r18_evidence") as r18:
            winner, size, _body = covers.best_cover(
                transport_for({url: (200, jpeg(1200, 1361))}), "FC2-PPV-3701252", 0,
                prior_candidates=(candidate,),
            )
        r18.assert_not_called()
        self.assertEqual(winner, candidate)
        self.assertEqual(size, (1200, 1361))

    def test_cached_javinizer_cover_and_content_id_are_reused_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "ABW-232"
            folder.mkdir()
            (folder / "r18dev.json").write_text(json.dumps({
                "source": "r18dev",
                "result": {
                    "source": "r18dev",
                    "content_id": "118abw232",
                    "maker": "Prestige",
                    "cover_url": (
                        "https://pics.dmm.co.jp/mono/movie/adult/118abw232/"
                        "118abw232pl.jpg"
                    ),
                },
            }), encoding="utf-8")

            evidence = covers.cached_metadata(root, "ABW-232")

        self.assertIn("r18dev", evidence.sources)
        self.assertTrue(covers._is_prestige(evidence))
        self.assertIn(
            "https://awsimgsrc.dmm.com/dig/mono/movie/118abw232/118abw232pl.jpg",
            {candidate.url for candidate in evidence.candidates},
        )

    def test_community_snapshots_lend_their_maker_but_not_their_cover(self):
        """JavBus 搜不到原番号时返回别的作品，快照里的图不能跳过图源印证直接当官方候选。"""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "HEYZO-1380"
            folder.mkdir()
            (folder / "javbus.json").write_text(json.dumps({"source": "javbus", "result": {
                "source": "javbus", "maker": "Prestige", "content_id": "heyzo-1380",
                "cover_url": "https://www.javbus.com/imgs/cover/zps_b.jpg"}}), encoding="utf-8")
            evidence = covers.cached_metadata(Path(tmp), "HEYZO-1380")
        self.assertEqual((evidence.candidates, evidence.sources), ((), frozenset({"javbus"})))
        self.assertTrue(covers._is_prestige(evidence))

    def test_mgstage_uses_only_the_enlarge_image(self):
        page = (b'<img src="https://image.mgstage.com/related_thumb.jpg">'
                b'<a id="EnlargeImage" '
                b'href="https://image.mgstage.com/images/prestige/abw/232/'
                b'pb_e_abw-232.jpg">open</a>')
        detail = "https://www.mgstage.com/product/product_detail/ABW-232/"
        candidates = covers.mgstage_images(
            transport_for({detail: (200, page)}), "ABW-232",
        )
        self.assertEqual(
            [candidate.url for candidate in candidates],
            ["https://image.mgstage.com/images/prestige/abw/232/pb_e_abw-232.jpg"],
        )
        self.assertEqual(candidates[0].kind, covers.PRODUCT_IMAGE)

    def test_mgstage_product_image_beats_an_equal_size_preview(self):
        preview = covers.candidate_for(
            "https://image.mgstage.com/images/luxutv/sp/259luxu/1509/popsample2_sp-259luxu-1509.jpg")
        product = covers.candidate_for(
            "https://image.mgstage.com/images/luxutv/259luxu/1509/pb_e_259luxu-1509.jpg")
        with patch.object(covers, "cached_metadata", return_value=covers.MetadataEvidence()), \
                patch.object(covers, "probe_size", return_value=(840, 472)), \
                patch.object(covers, "_fetch", return_value=jpeg(840, 472)):
            winner, size, _ = covers.best_cover(
                lambda *args: None, "259LUXU-1509", 0,
                prior_candidates=(preview, product),
            )
        self.assertEqual((winner.url, winner.kind, size),
                         (product.url, covers.PRODUCT_IMAGE, (840, 472)))

    def test_mgstage_preview_remains_a_fallback(self):
        preview = covers.candidate_for(
            "https://image.mgstage.com/images/luxutv/sp/259luxu/1509/popsample2_sp-259luxu-1509.jpg")
        with patch.object(covers, "cached_metadata", return_value=covers.MetadataEvidence()), \
                patch.object(covers, "probe_size", return_value=(840, 472)), \
                patch.object(covers, "_fetch", return_value=jpeg(840, 472)):
            winner, _, _ = covers.best_cover(
                lambda *args: None, "259LUXU-1509", 0, prior_candidates=(preview,))
        self.assertEqual(winner, preview)

    def test_prestige_api_selects_exact_code_and_package_image(self):
        query = covers.urllib.parse.urlencode({
            "isEnabledQuery": "true", "searchText": "ABW-232",
            "isEnableAggregation": "false", "release": "false",
            "reservation": "false", "soldOut": "false", "from": 0,
            "aggregationTermsSize": 0, "size": 20,
        })
        search = f"{covers.PRESTIGE_SEARCH}?{query}"
        product = covers.PRESTIGE_PRODUCT.format(uuid="exact-uuid")
        image = "https://www.prestige-av.com/api/media/a/b/package.jpg"
        candidates = covers.prestige_images(transport_for({
            search: (200, json.dumps({"hits": {"hits": [
                {"_source": {"deliveryItemId": "GOOEABW-232",
                              "productUuid": "goods-uuid"}},
                {"_source": {"deliveryItemId": "ABW-232",
                              "productUuid": "exact-uuid"}},
            ]}}).encode()),
            product: (200, b'{"packageImage":{"path":"a/b/package.jpg"}}'),
        }), "ABW-232")
        self.assertEqual([candidate.url for candidate in candidates], [image])

    def test_prestige_and_mgstage_both_participate_in_quality_comparison(self):
        official = [covers.candidate_for(
            "https://www.prestige-av.com/api/media/a/package.jpg"
        )]
        with patch.object(covers, "prestige_images", return_value=official), \
                patch.object(covers, "mgstage_images", return_value=[]) as mgstage:
            actual = covers.prestige_group_images(object(), "ABW-232")
        self.assertEqual(actual, official)
        mgstage.assert_called_once_with(unittest.mock.ANY, "ABW-232", deadline=None)

    def test_prestige_miss_uses_mgstage_as_the_fallback(self):
        fallback = [covers.candidate_for(
            "https://image.mgstage.com/images/prestige/abw/232/pb_e_abw-232.jpg"
        )]
        with patch.object(covers, "prestige_images", return_value=[]), \
                patch.object(covers, "mgstage_images", return_value=fallback) as mgstage:
            actual = covers.prestige_group_images(object(), "ABW-232")
        self.assertEqual(actual, fallback)
        mgstage.assert_called_once_with(unittest.mock.ANY, "ABW-232", deadline=None)

    def test_prestige_cooldown_queries_mgstage_with_the_same_deadline(self):
        fallback = [covers.candidate_for('https://image.mgstage.com/pb_e_abw-232.jpg')]
        with patch.object(covers, 'prestige_images', side_effect=covers.SourcePaused('地区限制')), \
                patch.object(covers, 'mgstage_images', return_value=fallback) as mgstage:
            actual = covers.prestige_group_images(object(), 'ABW-232', deadline=123)
        self.assertEqual(actual, fallback)
        mgstage.assert_called_once_with(unittest.mock.ANY, 'ABW-232', deadline=123)

    def test_mgstage_cooldown_keeps_the_prestige_candidate(self):
        official = [covers.candidate_for('https://www.prestige-av.com/api/media/package.jpg')]
        with patch.object(covers, 'prestige_images', return_value=official), \
                patch.object(covers, 'mgstage_images', side_effect=covers.SourcePaused('来源正在冷却')):
            self.assertEqual(covers.prestige_group_images(object(), 'ABW-232'), official)

    def test_both_group_sites_cooled_preserves_the_cooldown(self):
        with patch.object(covers, 'prestige_images', side_effect=covers.SourcePaused('地区限制')), \
                patch.object(covers, 'mgstage_images', side_effect=covers.SourcePaused('来源正在冷却')) as mgstage:
            with self.assertRaises(covers.SourcePaused):
                covers.prestige_group_images(object(), 'ABW-232')
        mgstage.assert_called_once()

    def test_group_deadline_stops_dispatching_sources(self):
        with patch.object(covers, 'prestige_images', side_effect=covers.DeadlineExceeded('动作预算已用尽')), \
                patch.object(covers, 'mgstage_images') as mgstage:
            with self.assertRaises(covers.DeadlineExceeded):
                covers.prestige_group_images(object(), 'ABW-232')
        mgstage.assert_not_called()

    def test_standard_flow_never_requests_blocked_avbase(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "DASS-468"
            folder.mkdir()
            url = "https://awsimgsrc.dmm.com/dig/digital/video/dass00468/dass00468pl.jpg"
            (folder / "r18dev.json").write_text(json.dumps({
                "source": "r18dev",
                "result": {"source": "r18dev", "cover_url": url},
            }), encoding="utf-8")

            def transport(request, timeout, limit):
                self.assertNotIn("avbase", request.url)
                return _Response(200, jpeg(2184, 1468)) if request.url == url else _Response(404, b"")

            winner, size, _ = covers.best_cover(
                transport, "DASS-468", 0, metadata_root=root,
            )

        self.assertEqual((winner.url, size), (url, (2184, 1468)))


class CrossProductCoverTests(unittest.TestCase):
    """真实事故：259LUXU-1475 的封面是 SNG-021 的（`118sng021`）。

    错图来自已删除的 avbase 抓取——那个页面上 258/259 系番号的主图是 DVD 合集
    封套。抓取路径删掉之后错图仍在，因为「复用上一轮成功记录」只看 result 是不是
    「取得」。
    """

    def test_dmm_content_id_must_carry_the_code_letters(self):
        self.assertTrue(covers.is_cross_product_cover(
            "259LUXU-1475",
            "https://pics.dmm.co.jp/mono/movie/adult/118sng021/118sng021pl.jpg"))
        self.assertTrue(covers.is_cross_product_cover(
            "428SUKE-080",
            "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/h_1711mgt00164/h_1711mgt00164pl.jpg"))

    def test_matching_content_ids_and_non_dmm_hosts_pass(self):
        self.assertFalse(covers.is_cross_product_cover(
            "ABW-232", "https://pics.dmm.co.jp/mono/movie/adult/118abw232/118abw232pl.jpg"))
        # `MGT-164` -> `h_1711mgt00164`：厂牌数字与补零都不影响字母段。
        self.assertFalse(covers.is_cross_product_cover(
            "MGT-164",
            "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/h_1711mgt00164/h_1711mgt00164pl.jpg"))
        # MGS、Prestige、FC2 的路径里没有 content_id，判据不适用，不能顺手拦掉。
        self.assertFalse(covers.is_cross_product_cover(
            "259LUXU-1475",
            "https://image.mgstage.com/images/luxutv/259luxu/1475/pb_e_259luxu-1475.jpg"))
        self.assertFalse(covers.is_cross_product_cover(
            "FC2-PPV-3071875",
            "https://contents-thumbnail2.fc2.com/w1200/storage/x.jpg"))

    def test_dmm_content_id_must_carry_the_code_number(self):
        # 字母段对上、数字段不对：MIB 的 `YUJ-101` 取回 `yuj00011`，`435MFC-135` 取回
        # `mfcc00027`。954 条成功记录里，这样的正好 9 条，全是错图。
        for code, cid in (("YUJ-101", "yuj00011"), ("CHU-201", "chu00021"),
                          ("435MFC-135", "h_1711mfcc00027")):
            with self.subTest(code=code):
                self.assertTrue(covers.is_cross_product_cover(
                    code, f"https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/{cid}/{cid}pl.jpg"))
        # 补零、厂牌数字前缀、去掉的素人前缀和 DVD 版尾缀都不改变数字本身。
        for code, cid in (("278GYAN-017", "gyan00017"), ("428SUKE-073", "h_1711suke00073"),
                          ("476MLA-234", "mla234"), ("HA-102", "49ha102r")):
            with self.subTest(code=code):
                self.assertFalse(covers.is_cross_product_cover(
                    code, f"https://pics.dmm.co.jp/mono/movie/{cid}/{cid}pl.jpg"))

    def test_an_fc2_image_named_after_another_work_is_cross_product(self):
        # JavArchive 图床的文件名就是作品号；十位的 `1654160016` 是上传时间戳，说明不了归属。
        for url in ("https://img.javstore.net/images/2024/01/15/2184960PL.gif",
                    "http://img2.javstore.net/images/2026/04/27/4536228pl.jpg"):
            with self.subTest(url=url):
                self.assertTrue(covers.is_cross_product_cover("FC2-PPV-1512205", url))
        for code, url in (
                ("FC2-PPV-2653914", "https://img.javstore.net/images/2022/02/14/FC2PPV-2653914-1-1.gif"),
                ("FC2-PPV-1083921", "https://img.javstore.net/images/2023/12/24/1083921pl.jpg"),
                ("FC2-PPV-2935152", "https://img.javstore.net/images/2022/07/31/pl1654160016.07k.gif"),
                # 十六进制上传串里夹着的五位数不是作品号：这张是 3264420 真正的竖版商品图。
                ("FC2-PPV-3264420", "https://img.javstore.net/images/2025/10/07/5dd29b14380eaPS.jpg")):
            with self.subTest(url=url):
                self.assertFalse(covers.is_cross_product_cover(code, url))

    def test_a_carried_wrong_cover_loses_to_the_right_one(self):
        wrong = "https://pics.dmm.co.jp/mono/movie/adult/118sng021/118sng021pl.jpg"
        right = "https://image.mgstage.com/images/luxutv/259luxu/1475/pb_e_259luxu-1475.jpg"
        transport = transport_for({wrong: (200, jpeg(1600, 1080)),
                                   right: (200, jpeg(800, 539))})

        winner, size, _ = covers.best_cover(
            transport, "259LUXU-1475", 0,
            prior_candidates=tuple(map(covers.candidate_for, (wrong, right))),
        )

        # 错图更大也不能赢：择优是在同一部片的候选之间比，不是在图之间比。
        self.assertEqual((winner.url, size), (right, (800, 539)))

    def test_restore_does_not_bring_a_cross_product_cover_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "cover-fetch-log.csv"
            wrong = "https://pics.dmm.co.jp/mono/movie/adult/118sng021/118sng021pl.jpg"
            with log.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=covers.FIELDS)
                writer.writeheader()
                writer.writerow({"code": "259LUXU-1475", "result": "取得", "source": "",
                                 "width": "800", "height": "539", "kb": "155",
                                 "url": wrong, "note": ""})
            output = Path(tmp) / "covers"

            result = covers.restore_logged_successes(
                transport_for({wrong: (200, jpeg(800, 539))}), log, output)

            self.assertEqual(result["logged"], 0)
            self.assertFalse((output / "259LUXU-1475.jpg").exists())

    def test_amateur_codes_reach_mgs_without_cached_maker_evidence(self):
        """素人系番号一直没问过 MGS——旧写法要先有元数据证明厂牌是 Prestige。

        而这批番号在 r18.dev 是 404，元数据永远不会出现，于是唯一有图的来源
        永远轮不到。判据改成番号形状后，MGS 才第一次被问到。
        """
        # 2026-09-01 实测的 MGS 商品页标记，原样保留 id 与 class。
        page = ('<a id="EnlargeImage" class="link_magnify" rel="lightbox" '
                'href="https://image.mgstage.com/images/luxutv/259luxu/1475/'
                'pb_e_259luxu-1475.jpg">')
        detail = "https://www.mgstage.com/product/product_detail/259LUXU-1475/"
        cover = "https://image.mgstage.com/images/luxutv/259luxu/1475/pb_e_259luxu-1475.jpg"
        transport = transport_for({detail: (200, page.encode("utf-8")),
                                   cover: (200, jpeg(800, 539))})

        winner, size, _ = covers.best_cover(transport, "259LUXU-1475", 0)

        self.assertEqual((winner.source, winner.url, size),
                         ("image.mgstage.com", cover, (800, 539)))


class KoreanMibCoverTests(unittest.TestCase):
    """韩国 MIB 的编号不在 JAV 目录站上。

    真实事故：`B:\\MVP\\MIB\\` 下 19 张封面全错。`YUJ-101` 这类是数字段拼错，番号核验
    拦得住；`HA-101`、`MY-102` 这类撞上番号完全相同的日本作品，核验拦不住，只能不问。
    """

    def test_best_cover_refuses_without_asking_any_source(self):
        def transport(request, timeout, limit):
            self.fail(f"不该发出请求：{request.url}")

        with self.assertRaises(covers.Unavailable) as raised:
            covers.best_cover(transport, "HA-101", 0, prior_candidates=(covers.candidate_for(
                "https://pics.dmm.co.jp/digital/video/3ha00101/3ha00101pl.jpg"),))
        self.assertEqual(str(raised.exception), covers.MIB_NOT_JAV)

    def test_restore_does_not_bring_a_mib_cover_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "cover-fetch-log.csv"
            url = "https://image.mgstage.com/images/mywife/sp/292my/102/pake-03_sp-292my-102.jpg"
            with log.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=covers.FIELDS)
                writer.writeheader()
                writer.writerow({"code": "MY-102", "result": "取得", "source": "",
                                 "width": "900", "height": "506", "kb": "120",
                                 "url": url, "note": ""})
            output = Path(tmp) / "covers"

            result = covers.restore_logged_successes(
                transport_for({url: (200, jpeg(900, 506))}), log, output)

            self.assertEqual(result["logged"], 0)
            self.assertFalse((output / "MY-102.jpg").exists())

    def test_mib_codes_never_enter_the_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "ledger.db"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE asset(code TEXT, medium TEXT, location TEXT)")
            connection.executemany(
                "INSERT INTO asset(code,medium,location) VALUES(?, 'video', '115')",
                [("ABW-232",), ("HA-101",), ("YUJ-101",)],
            )
            connection.commit(); connection.close()
            for only_shaped in (True, False):
                with self.subTest(only_shaped=only_shaped):
                    self.assertEqual(
                        covers.pending(database, root / "covers", only_shaped), ["ABW-232"])


class BestCoverTests(unittest.TestCase):
    def test_cold_cache_uses_live_maker_evidence_to_find_hd_sources(self):
        large = "https://www.prestige-av.com/api/media/fixture.jpg"
        pages = {covers.R18_DETAIL.format(code="ABW-232"): (200, json.dumps({
            "content_id": "118abw232", "maker": {"name": "Prestige"},
        }).encode()), large: (200, jpeg(1024, 690))}
        with patch.object(covers, "prestige_group_images", return_value=[covers.candidate_for(large)]) as group:
            candidate, size, data = covers.best_cover(transport_for(pages), "ABW-232", 0)
        group.assert_called_once()
        self.assertEqual(size, (1024, 690))
        self.assertEqual(candidate.url, large)
        self.assertEqual(data, pages[large][1])

    def test_known_same_size_url_is_not_probed_during_upgrade(self):
        known = "https://pics.dmm.co.jp/mono/movie/adult/118abw232/118abw232pl.jpg"
        larger = "https://awsimgsrc.dmm.com/dig/mono/movie/118abw232/118abw232pl.jpg"

        def transport(request, timeout, limit):
            if request.url == known:
                raise AssertionError("成功日志已证明同尺寸，不应重复请求")
            if request.url == larger:
                return _Response(200, jpeg(1200, 808))
            return _Response(404, b"")

        winner, size, _ = covers.best_cover(
            transport, "ABW-232", 0,
            prior_candidates=tuple(map(covers.candidate_for, (known, larger))),
            known_sizes={known: (800, 539)},
            minimum_pixels=800 * 539,
        )

        self.assertEqual((winner.url, size), (larger, (1200, 808)))

    def test_known_larger_url_is_still_probed(self):
        known = "https://pic.duga.jp/a/jacket.jpg"
        transport = transport_for({known: (200, jpeg(1000, 674))})

        winner, size, _ = covers.best_cover(
            transport, "ABW-232", 0,
            prior_candidates=(covers.candidate_for(known),),
            known_sizes={known: (1000, 674)},
            minimum_pixels=800 * 539,
        )

        self.assertEqual((winner.url, size), (known, (1000, 674)))

    def test_largest_candidate_wins_regardless_of_host(self):
        # ABW-232 没有数字版，duga 的 1000x674 才是最优——固定优先级链会选错。
        dmm = "https://pics.dmm.co.jp/mono/movie/adult/118abw232/118abw232pl.jpg"
        duga = "https://pic.duga.jp/unsecure/prestige/6270/noauth/jacket.jpg"
        mgs = "https://image.mgstage.com/images/p/pake.jpg"
        transport = transport_for({
            dmm: (200, jpeg(800, 539)),
            duga: (200, jpeg(1000, 674)),
            mgs: (200, jpeg(840, 563)),
        })
        winner, size, _ = covers.best_cover(
            transport, "ABW-232", 0,
            prior_candidates=tuple(map(covers.candidate_for, (dmm, duga, mgs))),
        )
        self.assertEqual((winner.source, size), ("pic.duga.jp", (1000, 674)))

    def test_constructed_hires_url_beats_the_aggregator(self):
        transport = transport_for({
            "https://r18.dev/videos/vod/movies/detail/-/dvd_id=GYAN-017/json":
                (200, b'{"content_id":"gyan00017"}'),
            "https://awsimgsrc.dmm.com/dig/digital/video/gyan00017/gyan00017pl.jpg":
                (200, jpeg(2184, 1464)),
            "https://pic.duga.jp/a/jacket.jpg": (200, jpeg(1000, 674)),
        })
        winner, size, _ = covers.best_cover(
            transport, "GYAN-017", 0,
            prior_candidates=(covers.candidate_for("https://pic.duga.jp/a/jacket.jpg"),),
        )
        self.assertEqual((winner.source, size), ("awsimgsrc.dmm.com", (2184, 1464)))

    def test_official_jacket_url_is_used_when_content_id_is_not_a_digital_path(self):
        official = "https://pics.dmm.co.jp/mono/movie/adult/118abw232/118abw232pl.jpg"
        transport = transport_for({
            "https://r18.dev/videos/vod/movies/detail/-/dvd_id=ABW-232/json":
                (200, ('{"content_id":"118abw232","images":{"jacket_image":'
                       '{"large":" ","large2":"' + official + '"}}}').encode()),
            official: (200, jpeg(800, 539)),
        })

        winner, size, _ = covers.best_cover(transport, "ABW-232", 0)

        self.assertEqual((winner.url, size), (official, (800, 539)))

    def test_thumbnails_are_never_considered(self):
        thumb = "https://pic.duga.jp/a/jacket_thumb.jpg"
        cover = "https://pic.duga.jp/a/jacket.jpg"
        transport = transport_for({
            cover: (200, jpeg(1000, 674)),
            thumb: (200, jpeg(3000, 2000)),
        })
        winner, size, _ = covers.best_cover(
            transport, "ABW-232", 0,
            prior_candidates=tuple(map(covers.candidate_for, (thumb, cover))),
        )
        self.assertEqual(size, (1000, 674), "缩略图即使更大也不能入选")

    def test_undersized_images_are_rejected(self):
        url = "https://pic.duga.jp/a/jacket.jpg"
        transport = transport_for({
            url: (200, jpeg(147, 200)),
        })
        with self.assertRaises(covers.Unavailable):
            covers.best_cover(
                transport, "ABW-232", 0,
                prior_candidates=(covers.candidate_for(url),),
            )

    def test_full_download_failure_falls_back_to_the_next_measured_cover(self):
        high = "https://one.example/high.jpg"
        low = "https://two.example/low.jpg"

        def transport(request, timeout, limit):
            if "Range" in request.headers:
                return _Response(200, jpeg(1200, 800) if request.url == high
                                 else jpeg(1000, 674))
            if request.url == high:
                return _Response(503, b"")
            return _Response(200, jpeg(1000, 674))

        winner, size, _data = covers.best_cover(
            transport, "ABW-232", 0,
            prior_candidates=tuple(map(covers.candidate_for, (high, low))),
        )

        self.assertEqual((winner.url, size), (low, (1000, 674)))

    def test_no_candidate_anywhere_is_reported_not_guessed(self):
        with self.assertRaises(covers.Unavailable):
            covers.best_cover(transport_for({}), "NOPE-999", 0)

    def test_related_work_images_never_enter_candidates(self):
        main = "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/vrkm00711/vrkm00711pl.jpg"
        related = "https://pics.dmm.co.jp/digital/video/vrkm01340/vrkm01340jp-1.jpg"
        base_transport = transport_for({
            main: (200, jpeg(2184, 1365)),
        })
        def transport(request, timeout, limit):
            if related in request.url:
                raise AssertionError("关联作品图片不得进入量尺寸请求")
            return base_transport(request, timeout, limit)
        winner, size, _ = covers.best_cover(
            transport, "VRKM-711", 0,
            prior_candidates=(covers.candidate_for(main),),
        )
        self.assertEqual((winner.url, size), (main, (2184, 1365)))


class SettledMissTests(unittest.TestCase):
    """续跑不该把上轮已经探完的落空再探一遍——那是最贵的一类。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "cover-fetch-log.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def _log(self, rows):
        with self.log.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=covers.FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in covers.FIELDS})

    def test_a_confirmed_absence_is_not_retried(self):
        self._log([{"code": "HEYZO-1380", "result": "未取得",
                    "note": "所有渠道都没有候选"},
                   {"code": "ABW-220", "result": "未取得",
                    "note": "候选都不是可用封套"}])
        self.assertEqual(covers.settled_misses(self.log), {"HEYZO-1380", "ABW-220"})

    def test_a_transport_failure_is_always_retried(self):
        """一次超时不等于确认没有，不能靠它把番号永久踢出队列。"""
        self._log([{"code": "SSNI-001", "result": "未取得",
                    "note": "ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING]"},
                   {"code": "SSNI-002", "result": "未取得",
                    "note": "ReadTimeout: timed out"},
                   {"code": "SSNI-003", "result": "未取得",
                    "note": f"CoverConnectError: {covers.HOSTS_UNREACHABLE}"}])
        self.assertEqual(covers.settled_misses(self.log), set())

    def test_a_successful_row_is_not_treated_as_a_miss(self):
        self._log([{"code": "GYAN-017", "result": "取得", "width": "2184"}])
        self.assertEqual(covers.settled_misses(self.log), set())

    def test_missing_log_means_nothing_is_skipped(self):
        self.assertEqual(covers.settled_misses(self.log / "nope.csv"), set())

    def test_scoped_batch_keeps_unselected_source_rows(self):
        self._log([{"code": "HEYZO-1380", "result": "未取得"},
                   {"code": "KUZU-25010", "result": "未取得"}])
        selected = {"KUZU-25010"}
        rows = [row for row in covers.logged_rows(self.log)
                if row["code"] not in selected]
        self.assertEqual([row["code"] for row in rows], ["HEYZO-1380"])


class RestoreLoggedSuccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "cover-fetch-log.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def _log(self, rows):
        with self.log.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=covers.FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in covers.FIELDS})

    def test_missing_successes_are_restored_from_the_recorded_url(self):
        url = "https://awsimgsrc.dmm.co.jp/cover.jpg"
        data = jpeg(800, 539)
        self._log([{"code": "ABP-993", "result": "取得", "width": "800",
                    "height": "539", "url": url}])
        output = Path(self.tmp.name) / "covers"

        result = covers.restore_logged_successes(
            transport_for({url: (200, data)}), self.log, output)

        self.assertEqual(result, {"logged": 1, "restored": 1, "skipped": 0,
                                  "failed": []})
        self.assertEqual((output / "ABP-993.jpg").read_bytes(), data)
        self.assertFalse((output / "ABP-993.restore.tmp").exists())

    def test_changed_upstream_image_is_refused_without_overwriting(self):
        url = "https://awsimgsrc.dmm.co.jp/cover.jpg"
        self._log([{"code": "ABP-993", "result": "取得", "width": "800",
                    "height": "539", "url": url}])
        output = Path(self.tmp.name) / "covers"

        result = covers.restore_logged_successes(
            transport_for({url: (200, jpeg(900, 600))}), self.log, output)

        self.assertEqual(result["restored"], 0)
        self.assertEqual([item["code"] for item in result["failed"]], ["ABP-993"])
        self.assertFalse((output / "ABP-993.jpg").exists())


class PendingCoverTests(unittest.TestCase):
    def test_fc2_only_queue_excludes_every_other_code_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "ledger.db"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE asset(code TEXT, medium TEXT, location TEXT)")
            connection.executemany(
                "INSERT INTO asset(code,medium,location) VALUES(?, 'video', '115')",
                [("ABW-232",), ("FC2-PPV-3701252",), ("RAIKUN325",)],
            )
            connection.commit(); connection.close()
            self.assertEqual(
                covers.pending(database, root / "covers", False, fc2_only=True),
                ["FC2-PPV-3701252"],
            )

    def test_queue_can_be_scoped_to_pikpak(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "ledger.db"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE asset(code TEXT, medium TEXT, location TEXT)"
            )
            connection.executemany(
                "INSERT INTO asset(code,medium,location) VALUES(?,?,?)",
                [
                    ("ABW-232", "video", "pikpak"),
                    ("SSNI-001", "video", "115"),
                    ("RAIKUN325", "video", "pikpak"),
                ],
            )
            connection.commit()
            connection.close()

            self.assertEqual(
                covers.pending(database, root / "covers", True, "pikpak"),
                ["ABW-232"],
            )

    def test_upgrade_queue_contains_only_existing_covers_within_width_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "ledger.db"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE asset(code TEXT, medium TEXT, location TEXT)"
            )
            connection.executemany(
                "INSERT INTO asset(code,medium,location) VALUES(?,?,?)",
                [("ABW-232", "video", "115"), ("DASS-468", "video", "115"),
                 ("PPT-018", "video", "115")],
            )
            connection.commit()
            connection.close()
            output = root / "covers"
            output.mkdir()
            (output / "ABW-232.jpg").write_bytes(jpeg(800, 539))
            (output / "DASS-468.jpg").write_bytes(jpeg(2184, 1468))

            self.assertEqual(
                covers.pending(database, output, True, existing=True, max_width=800),
                ["ABW-232"],
            )

    def test_audit_uses_the_same_jav_shape_and_size_buckets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "ledger.db"
            output = root / "covers"
            log = root / "cover-fetch-log.csv"
            output.mkdir()
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE asset(code TEXT, medium TEXT, location TEXT)"
            )
            connection.executemany(
                "INSERT INTO asset(code,medium,location) VALUES(?,?,?)",
                [("ABW-232", "video", "115"), ("DASS-468", "video", "115"),
                 ("FC2-PPV-1234567", "video", "115"),
                 ("RAIKUN325", "video", "115")],
            )
            connection.commit()
            connection.close()
            (output / "ABW-232.jpg").write_bytes(jpeg(800, 539))
            with log.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=covers.FIELDS)
                writer.writeheader()
                writer.writerow({"code": "ABW-232", "result": "取得"})

            report = covers.audit_state(database, output, log)

        self.assertEqual(report["jav_codes"], 2)
        self.assertEqual(report["decoded_covers"], 1)
        self.assertEqual(report["missing"], 1)
        self.assertEqual(report["width_buckets"]["le_800"], 1)


class UpgradeExistingTests(unittest.TestCase):
    def test_only_a_larger_candidate_replaces_the_cover_and_success_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "ledger.db"
            output = root / "covers"
            log = root / "cover-fetch-log.csv"
            output.mkdir()
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE asset(code TEXT, medium TEXT, location TEXT)"
            )
            connection.executemany(
                "INSERT INTO asset(code,medium,location) VALUES(?,?,?)",
                [("AAA-001", "video", "115"), ("BBB-002", "video", "115")],
            )
            connection.commit()
            connection.close()
            (output / "AAA-001.jpg").write_bytes(jpeg(800, 539))
            (output / "BBB-002.jpg").write_bytes(jpeg(800, 539))
            with log.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=covers.FIELDS)
                writer.writeheader()
                writer.writerow({"code": "AAA-001", "result": "取得",
                                 "source": "old-a", "width": 800, "height": 539})
                writer.writerow({"code": "BBB-002", "result": "取得",
                                 "source": "old-b", "width": 800, "height": 539})

            def fetched(_transport, code, _delay, **_kwargs):
                if code == "AAA-001":
                    return (covers.Candidate("new-a", "https://new/a.jpg"),
                            (1000, 674), jpeg(1000, 674))
                return (covers.Candidate("new-b", "https://new/b.jpg"),
                        (700, 470), jpeg(700, 470))

            args = covers.build_parser().parse_args([
                "--db", str(database), "--out", str(output), "--log", str(log),
                "--upgrade-existing", "--delay", "0", "--min-free", "0",
            ])
            transport = unittest.mock.Mock()
            with patch.object(covers, "HttpxTransport", return_value=transport), \
                    patch.object(covers, "best_cover", side_effect=fetched), \
                    patch.object(covers, "system_volume", return_value=root), \
                    patch.object(covers.DiskGuard, "check", return_value=100.0):
                self.assertEqual(covers.run(args), 0)

            self.assertEqual(Image.open(output / "AAA-001.jpg").size, (1000, 674))
            self.assertEqual(Image.open(output / "BBB-002.jpg").size, (800, 539))
            rows = {row["code"]: row for row in covers.logged_rows(log)}
            self.assertEqual(rows["AAA-001"]["source"], "new-a")
            self.assertEqual(rows["BBB-002"]["source"], "old-b")


if __name__ == "__main__":
    unittest.main()
