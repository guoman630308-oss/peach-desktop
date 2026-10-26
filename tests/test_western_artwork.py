"""西方官方图片的身份绑定、唯一作品匹配、头像比脸与按批撤回的无番号封面。"""
import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest import mock
from unittest.mock import Mock
from pathlib import Path

from PIL import Image, ImageDraw

from peach import avatar_cover_face, avatar_picker, western_artwork as artwork
from peach.http import HttpResponse
from peach.web_catalog import attach_artwork_fields
from peach.web_state import WebContract
from scripts import harvest_western_artwork, revert_auto_landing
from tests.support.ledger import fresh_ledger


def node(name='Lena Anderson', released='2019-08-14T17:30:00Z', slug='cam-to-me'):
    return {'title': 'Cam To Me', 'slug': slug, 'releaseDate': released,
            'models': [{'name': name}, {'name': 'Kai Taylor'}],
            'images': {'poster': [{'src': 'https://cdn.tushy.com/small.jpg', 'width': 640},
                                  {'src': 'https://cdn.tushy.com/full.jpg', 'width': 1920}]}}


class WesternSourceTests(unittest.TestCase):
    def test_gallery_urls_encode_spaces_and_keep_the_public_provider_port(self):
        page = b'<h1 id="babename">Melody Marks</h1><div id="profbox2"><a class="img" href="/pics/Melody Marks2.jpg"></a><a class="img" href="https://www.babepedia.com:123/pics/Other.jpg"></a><a class="img" href="https://localhost/pics/Other.jpg"></a></div>'
        found = artwork.babepedia_portraits(lambda *a: HttpResponse(200,{},page), 'Melody Marks')
        self.assertEqual([r['upstream_url'] for r in found],
                         ['https://www.babepedia.com/pics/Melody%20Marks2.jpg'])

    def http(self, nodes, detail=None):
        self.calls = []
        def call(request, timeout, max_bytes):
            self.calls.append(request)
            body = json.loads(request.body)
            data = ({'searchVideos': {'edges': [{'node': n} for n in nodes]}}
                    if 'query' in body['variables'] else {'findOneVideo': detail or nodes[0]})
            return HttpResponse(200, {}, json.dumps({'data': data}).encode())
        return call

    def test_exact_name_and_date_select_the_official_full_image(self):
        result = artwork.official_scene(self.http([node('Lena Paul'), node()], detail=node()),
                                        'tushy', 'Lena Anderson', '2019-08-14')
        self.assertEqual(result['image_url'], 'https://cdn.tushy.com/full.jpg')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(json.loads(self.calls[0].body)['variables']['site'], 'TUSHY')

    def test_wrong_date_or_shared_first_name_do_not_bind_a_scene(self):
        for wrong in (node('Lena Paul'), node(released='2019-08-15')):
            self.assertIsNone(artwork.official_scene(self.http([wrong]), 'tushy', 'Lena Anderson', '2019-08-14'))
            self.assertEqual(len(self.calls), 1)

    def test_two_exact_scenes_require_review(self):
        self.assertIsNone(artwork.official_scene(self.http([node(), node(slug='other')]),
                                                'tushy', 'Lena Anderson', '2019-08-14'))

    def test_details_must_retain_the_matching_identity(self):
        self.assertIsNone(artwork.official_scene(self.http([node()], node('Someone Else')),
                                                'tushy', 'Lena Anderson', '2019-08-14'))

    def test_an_image_from_another_host_is_not_an_official_cover(self):
        detail = node()
        detail['images']['poster'] = [{'src': 'https://evil-tushy.com/pic.jpg', 'width': 3000}]
        self.assertIsNone(artwork.official_scene(self.http([node()], detail),
                                                'tushy', 'Lena Anderson', '2019-08-14'))

    def test_graphql_errors_are_unavailable(self):
        def error(*args): return HttpResponse(200, {}, b'{"errors":[{"message":"blocked"}]}')
        with self.assertRaisesRegex(ValueError, '未取得'):
            artwork.official_scene(error, 'tushy', 'Lena Anderson', '2019-08-14')

    def test_main_portraits_exclude_related_people_and_user_uploads(self):
        page = b'<h1 id="babename">Melody Marks</h1><div id="profbox2"><a class="img" href="/pics/Melody.jpg"></a><a class="img" href="https://other.com/a.jpg"></a></div><div class="useruploads2"><a class="img" href="/pics/unverified.jpg"></a></div>'
        calls = []
        def http(request, *args):
            calls.append(request.url)
            return HttpResponse(200, {}, page)
        portraits = artwork.babepedia_portraits(http, 'Melody Marks')
        self.assertEqual([p['upstream_url'] for p in portraits], ['https://www.babepedia.com/pics/Melody.jpg'])
        self.assertEqual(calls, ['https://www.babepedia.com/babe/Melody_Marks'])

    def test_a_different_profile_or_challenge_page_is_unavailable(self):
        for page in (b'<h1 id="babename">Melody Other</h1>', b'<title>Just a moment</title>'):
            with self.assertRaisesRegex(ValueError, '未取得'):
                artwork.babepedia_portraits(lambda *a, body=page: HttpResponse(200, {}, body), 'Melody Marks')

    def test_explicit_profile_alias_binds_a_different_stage_name(self):
        page = b'<h1 id="babename">Blaire Ivory</h1><h2 id="aka"><small>Also known as:</small> Lena Anderson - Lena - Blair<img id="aliasinfobtn" alt="More info on her aliases"></h2><div id="profbox2"><a class="img" href="/pics/Blaire.jpg"></a></div>'
        calls = []
        def http(request, *args):
            calls.append(request.url)
            return HttpResponse(200, {}, page)
        result = artwork.babepedia_portraits(http, 'Lena Anderson',
                                            profile_url='https://www.babepedia.com/babe/Blaire_Ivory')
        self.assertEqual(result[0]['matched_name'], 'Blaire Ivory')
        self.assertEqual(calls, ['https://www.babepedia.com/babe/Blaire_Ivory'])

    def test_a_profile_url_from_another_site_is_rejected_before_request(self):
        calls = []
        with self.assertRaisesRegex(ValueError, '档案地址'):
            artwork.babepedia_portraits(lambda *a: calls.append(a), 'Lena Anderson',
                                        profile_url='https://other.com/babe/Blaire_Ivory')
        self.assertEqual(calls, [])

    def test_filename_date_requires_the_publisher_and_valid_calendar_date(self):
        self.assertEqual(artwork.filename_date('tushy.19.08.14.lena.anderson.4k.mp4', 'tushy'), '2019-08-14')
        self.assertEqual(artwork.filename_date('vixen.19.08.14.lena.mp4', 'tushy'), '')
        self.assertEqual(artwork.filename_date('tushy.19.02.31.lena.mp4', 'tushy'), '')


def portrait(colour) -> tuple:
    image = Image.new('RGB', (600, 800), colour)
    buffer = io.BytesIO(); image.save(buffer, 'JPEG')
    body = buffer.getvalue()
    origin = {'upstream_url': f'https://www.babepedia.com/pics/{colour}.jpg', 'automatic_install': True}
    return origin, body, avatar_picker.accept_image(body)


class Matcher:
    """按图中心的颜色给特征：红色是她，蓝色是别人，封面截图记作她。"""
    unavailable = ''

    def embedding(self, body):
        if body == b'cover':
            return [1.0, 0.0]
        pixel = Image.open(io.BytesIO(body)).convert('RGB').getpixel((300, 400))
        return [1.0, 0.0] if pixel[0] > pixel[2] else [0.0, 1.0]


class WesternPortraitGateTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(avatar_cover_face, 'cut', return_value=(b'cover', {}))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.covers = [SimpleNamespace(code='', asset_id=1), SimpleNamespace(code='', asset_id=2)]

    def test_the_portrait_that_matches_her_single_work_covers_is_chosen(self):
        winner, evidence, reason = artwork.face_matched_portrait([portrait('blue'), portrait('red')],
                                                                 self.covers, Matcher())
        self.assertEqual(winner.origin['upstream_url'], 'https://www.babepedia.com/pics/red.jpg')
        self.assertEqual([cover['asset_id'] for cover in evidence['covers']], [1, 2])
        self.assertEqual(reason, '')

    def test_without_covers_or_a_matching_face_nothing_is_installed(self):
        self.assertEqual(artwork.face_matched_portrait([portrait('red')], [], Matcher())[0], None)
        winner, evidence, reason = artwork.face_matched_portrait([portrait('blue')], self.covers, Matcher())
        self.assertIsNone(winner)
        self.assertEqual(evidence, {})
        self.assertEqual(reason, '图库 1 张认不准')

    def test_an_unavailable_model_installs_nothing(self):
        matcher = Matcher()
        matcher.unavailable = '模型未下载'
        winner, _evidence, reason = artwork.face_matched_portrait([portrait('red')], self.covers, matcher)
        self.assertIsNone(winner)
        self.assertIn('模型未下载', reason)


class WesternArtworkBatchTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()

    def test_profile_addresses_come_only_from_confirmed_candidate_rows(self):
        db = fresh_ledger(self.root)
        with closing(sqlite3.connect(db)) as connection:
            kept, unsure = (connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at) "
                "VALUES('creator',?,?,'','')", (name, name.lower())).lastrowid for name in ('Kept', 'Unsure'))
            connection.commit()
            table = self.root / 'babepedia-candidates.csv'
            table.write_text('entity_id,verdict,profile_url\n'
                             f'{kept},命中,https://www.babepedia.com/babe/Kept\n'
                             f'{unsure},需人工确认,https://www.babepedia.com/babe/Someone\n', encoding='utf-8')
            self.assertEqual(harvest_western_artwork.profile_urls(connection, table, [kept, unsure]),
                             {kept: 'https://www.babepedia.com/babe/Kept'})

    def test_revert_removes_one_batch_of_covers_with_their_sidecars(self):
        covers = self.root / 'covers'
        covers.mkdir()
        for asset_id, record in ((1, {'source': artwork.SOURCE, 'batch': artwork.SOURCE + '@one'}),
                                 (2, {'source': artwork.SOURCE, 'batch': artwork.SOURCE + '@two'}),
                                 (3, {'provider': 'western-official'})):
            (covers / f'ASSET-ID-{asset_id}.jpg').write_bytes(b'jpeg')
            (covers / f'ASSET-ID-{asset_id}.face.json').write_text('{}', encoding='utf8')
            (covers / f'ASSET-ID-{asset_id}.scraping.json').write_text(json.dumps(record), encoding='utf8')
        self.assertEqual([path.name for path in artwork.planned_covers(covers, artwork.SOURCE, '')],
                         ['ASSET-ID-1.jpg', 'ASSET-ID-2.jpg'])
        db = fresh_ledger(self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            revert_auto_landing.main(['--db', str(db), '--apply', '--backup', str(self.root / 'backup.db'),
                                      '--source', artwork.SOURCE, '--batch', artwork.SOURCE + '@one',
                                      '--cover-root', str(covers), '--logo-root', str(self.root / 'logos')])
        self.assertEqual(sorted(path.name for path in covers.iterdir()),
                         ['ASSET-ID-2.face.json', 'ASSET-ID-2.jpg', 'ASSET-ID-2.scraping.json',
                          'ASSET-ID-3.face.json', 'ASSET-ID-3.jpg', 'ASSET-ID-3.scraping.json'])


class WesternLocalArtworkTests(unittest.TestCase):
    def test_cover_projection_and_picker_use_an_asset_key_without_changing_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            image = Image.new('RGB', (640, 360), 'red')
            ImageDraw.Draw(image).rectangle((20, 20, 200, 200), fill='black')
            buffer = io.BytesIO(); image.save(buffer, 'JPEG')
            (root / 'ASSET-ID-1.jpg').write_bytes(buffer.getvalue())
            con = sqlite3.connect(':memory:')
            con.executescript("CREATE TABLE asset(id,code,catalog_title,name,snapshot_path,medium,size);"
                             "CREATE TABLE asset_entity(asset_id,entity_id,role);"
                             "CREATE TABLE entity(id,kind);"
                             "INSERT INTO entity VALUES(5,'performer');"
                             "INSERT INTO asset VALUES(1,NULL,NULL,'Tushy',NULL,'video',1);"
                             "INSERT INTO asset_entity VALUES(1,5,'performer');")
            choices = avatar_picker.asset_artwork(con, root, 5)
            self.assertEqual(choices[0].ref, 'asset:1:cover')
            self.assertEqual(choices[0].cast, 1)
            source = avatar_picker.ArtworkSource(root, lambda *a: None)
            body, origin = avatar_picker.resolve('asset:1:cover', con, root, 5, None, artwork=source)
            self.assertEqual(body, buffer.getvalue())
            self.assertEqual(origin['asset_code'], '')
            contract = WebContract(root / 'ledger.db', cover_root=root)
            item = {'id': 1, 'code': None}
            attach_artwork_fields(contract, item)
            self.assertEqual(item['cover_key'], 'ASSET-ID-1')
            self.assertIsNone(item['code'])
            self.assertTrue(item['has_cover'])
            self.assertEqual(avatar_picker.asset_artwork(con, root, 6), [])
            (root / 'ASSET-ID-1.scraping.json').write_text(json.dumps({
                'provider': 'western-official', 'performers': ['Lena Anderson', 'Kai Taylor']}), encoding='utf8')
            choice = avatar_picker.asset_artwork(con, root, 5)[0]
            self.assertEqual(choice.cast, 2)
            self.assertIsNone(choice.focus)
            con.execute('ALTER TABLE asset ADD COLUMN disposal')
            probe = Mock()
            self.assertEqual(avatar_cover_face.faces(con, root, 5, probe), [])
            probe.on_bytes.assert_not_called()
            con.close()
