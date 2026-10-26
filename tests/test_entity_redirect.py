"""实体合并的墓碑（migration 0040）：旧 id 解析到并进去的那一条。

账本之外按实体 id 记下的东西（头像文件名、复核 CSV、页面上的 `/entity-image?id=`）在合并后
还拿着旧 id。墓碑让它们跳到现在的实体；目标没了就和目标一样查不到，不悬空。
"""
from __future__ import annotations

import importlib.util
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from PIL import Image, ImageDraw

from peach.entities import merge_entity, resolve_entity_id
from peach.web_review import _use_canonical_entity_names

from support.ledger import fresh_ledger

HAS_DEPS = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))


def add_person(connection, entity_id: int, name: str) -> None:
    connection.execute(
        "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
        " VALUES(?,'performer',?,?,'t','t')", (entity_id, name, name.casefold()))


def merge(connection, source_id: int, target_id: int, name: str) -> dict:
    moved = merge_entity(connection, target_id=target_id, source_id=source_id,
                         source_name=name, alias_source="test:merge")
    connection.commit()
    return moved


def picture(colour: str) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", (40, 60), colour)
    ImageDraw.Draw(image).rectangle((0, 0, 40, 30), fill="black")
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


class EntityRedirectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = fresh_ledger(self.tmp.name)
        # 和服务、脚本的连接一样不开外键：ON DELETE 声明不会替我们收拾墓碑。
        self.con = sqlite3.connect(self.db)
        self.addCleanup(self.con.close)
        for entity_id, name in ((10, "橋本ありな"), (11, "新ありな"), (12, "桥本有菜")):
            add_person(self.con, entity_id, name)
        self.con.commit()

    def redirects(self) -> list[tuple]:
        return self.con.execute(
            "SELECT old_id,target_id FROM entity_redirect ORDER BY old_id").fetchall()

    def test_the_merged_id_resolves_to_the_target(self):
        merge(self.con, 11, 10, "新ありな")
        self.assertEqual(resolve_entity_id(self.con, 11), 10)
        self.assertEqual(resolve_entity_id(self.con, 10), 10)
        self.assertIsNone(resolve_entity_id(self.con, 999))
        self.assertEqual(self.con.execute(
            "SELECT source FROM entity_redirect WHERE old_id=11").fetchone(), ("test:merge",))

    def test_a_chain_of_merges_points_every_old_id_at_the_last_target(self):
        merge(self.con, 11, 10, "新ありな")
        moved = merge(self.con, 10, 12, "橋本ありな")
        self.assertEqual(self.redirects(), [(10, 12), (11, 12)])
        self.assertEqual(moved["redirects"], 1)
        self.assertEqual(resolve_entity_id(self.con, 11), 12)
        self.assertEqual(self.con.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_deleting_the_target_removes_its_redirects(self):
        """目标被删（标签改名、清理脚本都直接删 entity）时旧 id 和目标一样查不到。

        悬空的墓碑会让下一次合并后的 `foreign_key_check` 不为 0，ADR-0064 的后继按它回滚。
        """
        merge(self.con, 11, 10, "新ありな")
        self.con.execute("DELETE FROM entity WHERE id=10")
        self.con.commit()
        self.assertEqual(self.redirects(), [])
        self.assertIsNone(resolve_entity_id(self.con, 11))
        self.assertEqual(self.con.execute("PRAGMA foreign_key_check(entity_redirect)").fetchall(), [])

    def test_a_dangling_redirect_resolves_to_nothing(self):
        """绕开触发器留下的悬空行（比如重建 entity 表时丢了触发器）也不解析到死实体。"""
        merge(self.con, 11, 10, "新ありな")
        self.con.execute("DROP TRIGGER entity_redirect_target_delete")
        self.con.execute("DELETE FROM entity WHERE id=10")
        self.assertIsNone(resolve_entity_id(self.con, 11))

    def test_a_new_entity_that_reuses_the_id_wins_over_the_redirect(self):
        """`entity.id` 没有 AUTOINCREMENT，删掉的最大 id 会再发给下一条新实体。"""
        merge(self.con, 12, 10, "桥本有菜")
        add_person(self.con, 12, "別の人")
        self.con.commit()
        self.assertEqual(resolve_entity_id(self.con, 12), 12)
        self.assertEqual(self.redirects(), [])

    def test_favorites_follow_merges_when_sqlite_reuses_the_highest_id(self):
        from peach.web_contract import WebContract, dispatch_api_get, dispatch_api_post
        contract = WebContract(self.db)
        for entity_id in (12, 10):
            dispatch_api_post(contract, "/api/library-shortcuts", {
                "action": "favorite", "entity_id": entity_id, "on": True})
        saved = dispatch_api_post(contract, "/api/library-shortcuts", {
            "action": "save_filter", "name": "常用", "href": "/?loc="})['filters']
        self.con.execute("UPDATE profile SET settings_json=json_set(settings_json,'$.searchHistoryLimit',25)"
                         " WHERE id='local-default'")
        self.con.execute("INSERT INTO profile(id,user_id,name,is_default,settings_json,created_at,updated_at)"
                         " SELECT 'second',user_id,'另一个',0,settings_json,created_at,updated_at"
                         " FROM profile WHERE id='local-default'")
        self.con.execute("INSERT INTO profile(id,user_id,name,is_default,settings_json,created_at,updated_at)"
                         " SELECT 'broken',user_id,'损坏',0,'{',created_at,updated_at"
                         " FROM profile WHERE id='local-default'")
        self.con.commit()
        merge(self.con, 12, 10, "桥本有菜")
        inserted = self.con.execute("INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                                    " VALUES('performer','別の人','別の人','t','t')").lastrowid
        self.con.commit()
        self.assertEqual(inserted, 12)
        self.assertEqual(self.redirects(), [])
        result = dispatch_api_get(contract, "/api/library-shortcuts", {})
        self.assertEqual([row['entity_id'] for row in result['favorites']], [10])
        self.assertEqual(result['filters'], saved)
        other = json.loads(self.con.execute("SELECT settings_json FROM profile WHERE id='second'").fetchone()[0])
        self.assertEqual([row['entity_id'] for row in other['libraryShortcuts']['favorites']], [10])
        self.assertEqual(other['libraryShortcuts']['filters'], saved)
        self.assertEqual(other['searchHistoryLimit'], 25)
        self.assertEqual(self.con.execute("SELECT settings_json FROM profile WHERE id='broken'").fetchone()[0], '{')
        self.assertEqual(dispatch_api_post(contract, "/api/library-shortcuts", {
            "action": "favorite", "entity_id": 10, "on": False})['favorites'], [])

    def test_favorite_transfer_rolls_back_with_entity_merge(self):
        original = json.dumps({'sidebarOrder': ['tags'], 'libraryShortcuts': {
            'favorites': [{'entity_id': 12}], 'filters': []}})
        self.con.execute("UPDATE profile SET settings_json=? WHERE id='local-default'", (original,))
        self.con.commit()
        merge_entity(self.con, target_id=10, source_id=12, source_name="桥本有菜", alias_source="test:merge")
        self.con.rollback()
        self.assertEqual(self.con.execute("SELECT settings_json FROM profile WHERE id='local-default'").fetchone()[0], original)
        self.assertIsNotNone(self.con.execute("SELECT id FROM entity WHERE id=12").fetchone())

    def test_merging_into_a_missing_or_the_same_entity_is_refused(self):
        with self.assertRaises(ValueError):
            merge_entity(self.con, target_id=999, source_id=11,
                         source_name="新ありな", alias_source="test:merge")
        with self.assertRaises(ValueError):
            merge_entity(self.con, target_id=11, source_id=11,
                         source_name="新ありな", alias_source="test:merge")
        self.assertIsNotNone(self.con.execute("SELECT 1 FROM entity WHERE id=11").fetchone())

    def test_follows_and_feed_subscriptions_follow_the_merge(self):
        """关注与订阅绑在人身上，合并后跟着走，不悬空在已删的实体上。"""
        self.con.executescript("""
          INSERT INTO follow_source(id,entity_id,provider,ref,label,url,created_at,updated_at)
            VALUES(1,11,'x','arina','arina','https://x.test/arina','t','t');
          INSERT INTO feed_source(id,kind,url,entity_id,created_at)
            VALUES(1,'javdb_actor','https://j.test/1',11,'t');
          INSERT INTO feed_discovery(id,code,discovered_at)
            VALUES(1,'ABC-001','t'),(2,'ABC-002','t');
          INSERT INTO feed_discovery_entity(discovery_id,entity_id) VALUES(1,10),(1,11),(2,11);
        """)
        moved = merge(self.con, 11, 10, "新ありな")
        self.assertEqual((moved["follows"], moved["feeds"], moved["discoveries"]), (1, 1, 1))
        self.assertEqual(self.con.execute("SELECT entity_id FROM follow_source").fetchall(), [(10,)])
        self.assertEqual(self.con.execute("SELECT entity_id FROM feed_source").fetchall(), [(10,)])
        self.assertEqual(self.con.execute(
            "SELECT discovery_id,entity_id FROM feed_discovery_entity ORDER BY 1").fetchall(),
            [(1, 10), (2, 10)])
        self.assertEqual(self.con.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_a_review_row_carrying_the_old_id_shows_the_target(self):
        """候选 CSV 的 `entity_id` 停在出候选那一刻；实体并入别人之后，这一行认现在那一条。"""
        merge(self.con, 11, 10, "新ありな")
        self.con.row_factory = sqlite3.Row
        rows = [{"entity_id": "11", "current_name": "Arina Arata"},
                {"entity_id": "12", "current_name": "桥本有菜"},
                {"entity_id": "999", "current_name": "nobody"}]
        _use_canonical_entity_names(self.con, rows)
        self.assertEqual([(row["entity_id"], row["current_name"]) for row in rows],
                         [("10", "橋本ありな"), ("12", "桥本有菜"), ("999", "nobody")])
        self.assertEqual(rows[0]["source_name"], "Arina Arata")


@unittest.skipUnless(HAS_DEPS, "FastAPI/httpx 尚未安装")
class EntityRedirectRouteTests(unittest.TestCase):
    """页面与接口拿着旧 id 或旧名来，落到并进去的那一条。"""

    def setUp(self):
        from fastapi.testclient import TestClient
        from peach.api import create_app
        from peach.config import PeachSettings

        self.folder = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.avatars = self.folder / "avatars"
        self.avatars.mkdir()
        database = fresh_ledger(self.folder)
        connection = sqlite3.connect(database)
        add_person(connection, 10, "橋本ありな")
        add_person(connection, 11, "新ありな")
        connection.execute(
            "INSERT INTO asset(id,location,path,name,medium) VALUES(1,'local',?,'1.mp4','video')",
            (r"R:\media\1.mp4",))
        connection.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(1,11,'performer','test',1.0)")
        connection.commit()
        merge(connection, 11, 10, "新ありな")
        connection.close()
        self.face = mock.patch("peach.avatar_provider.FaceProbe").start()
        self.face.return_value.return_value = None
        self.addCleanup(mock.patch.stopall)
        self.app = create_app(PeachSettings(
            db_path=database, configured=True, token="secret", avatar_root=self.avatars,
            candidate_root=self.folder / "generated", cover_root=self.folder / "covers"))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def test_the_old_name_opens_the_target_page(self):
        response = self.client.get("/api/entity", params={
            "kind": "performer", "name": "新ありな", "t": "secret"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], 10)
        self.assertEqual(response.json()["canonical_name"], "橋本ありな")

    def test_entity_and_photo_queries_accept_current_and_merged_ids(self):
        for path in ('/api/entity', '/api/photos'):
            named = self.client.get(path, params={
                'kind': 'performer', 'name': '橋本ありな', 't': 'secret'})
            self.assertEqual(named.status_code, 200, named.text)
            for entity_id in (10, 11):
                response = self.client.get(path, params={
                    'kind': 'performer', 'id': entity_id, 'name': 'ignored', 't': 'secret'})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), named.json())

    def test_entity_id_queries_refuse_invalid_missing_or_other_kind_ids(self):
        for path in ('/api/entity', '/api/photos'):
            for entity_id in ('', '0', '-1', '1.0', 'ten', '１０', str(2**63), '9' * 50):
                response = self.client.get(path, params={
                    'kind': 'performer', 'id': entity_id, 't': 'secret'})
                self.assertEqual(response.json(), {'error': 'invalid entity'}, (path, entity_id))
            for kind, entity_id in (('studio', '10'), ('performer', '999')):
                response = self.client.get(path, params={'kind': kind, 'id': entity_id, 't': 'secret'})
                self.assertEqual(response.json(), {'error': 'not found'}, (path, kind, entity_id))

    def test_verified_cross_kind_merge_ids_use_the_final_identity(self):
        with sqlite3.connect(self.app.state.settings.db_path) as connection:
            connection.execute(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES(20,'creator','Old Account','old account','t','t')")
            merge_entity(connection, target_id=10, source_id=20, source_name='Old Account',
                         alias_source='user:identity-merge:creator')
            add_person(connection, 12, 'Final Person')
            merge_entity(connection, target_id=12, source_id=10, source_name='橋本ありな', alias_source='test:merge')
        expected = {'redirect': {'id': 12, 'kind': 'performer', 'name': 'Final Person'}}
        for identity in ({'id': 20}, {'name': 'Old Account'}):
            response = self.client.get('/api/entity', params={'kind': 'creator', 't': 'secret', **identity})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), expected)
        query = '?tag=a%2Fb&tag=second&media=photos&t=secret'
        for method in ('GET', 'HEAD'):
            response = self.client.request(method, '/creators/20' + query, follow_redirects=False)
            self.assertEqual(response.status_code, 307)
            self.assertEqual(response.headers['location'], '/performers/12' + query)
            self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(self.client.get('/api/photos', params={
            'kind': 'creator', 'id': 20, 't': 'secret'}).json(), {'error': 'not found'})

    def test_cross_kind_id_redirect_requires_source_kind_evidence(self):
        with sqlite3.connect(self.app.state.settings.db_path) as connection:
            connection.executemany(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES(?,'creator',?,?,'t','t')", [(20, 'Explicit', 'explicit'), (21, 'Generic', 'generic')])
            merge_entity(connection, target_id=10, source_id=20, source_name='Explicit',
                         alias_source='user:identity-merge:creator')
            merge_entity(connection, target_id=10, source_id=21, source_name='Generic', alias_source='test:merge')
        for kind, entity_id in (('creator', 10), ('studio', 20), ('creator', 21)):
            response = self.client.get('/api/entity', params={'kind': kind, 'id': entity_id, 't': 'secret'})
            self.assertEqual(response.json(), {'error': 'not found'}, (kind, entity_id))
            prefix = 'studios' if kind == 'studio' else 'creators'
            for method in ('GET', 'HEAD'):
                page = self.client.request(method, f'/{prefix}/{entity_id}?t=secret', follow_redirects=False)
                self.assertEqual(page.status_code, 404, (kind, entity_id, method))

    def test_a_reused_cross_kind_merge_id_keeps_its_current_identity(self):
        with sqlite3.connect(self.app.state.settings.db_path) as connection:
            connection.execute(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES(20,'creator','Old Account','old account','t','t')")
            merge_entity(connection, target_id=10, source_id=20, source_name='Old Account',
                         alias_source='user:identity-merge:creator')
            add_person(connection, 20, 'New Person')
        self.assertEqual(self.client.get('/api/entity', params={
            'kind': 'creator', 'id': 20, 't': 'secret'}).json(), {'error': 'not found'})
        self.assertEqual(self.client.get('/creators/20?t=secret', follow_redirects=False).status_code, 404)
        current = self.client.get('/api/entity', params={'kind': 'performer', 'id': 20, 't': 'secret'})
        self.assertEqual(current.json()['id'], 20)
        self.assertEqual(current.json()['canonical_name'], 'New Person')

    def test_tag_profiles_and_photos_use_the_same_identity_by_name_and_id(self):
        with sqlite3.connect(self.app.state.settings.db_path) as connection:
            connection.execute("INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
                               " VALUES(20,'tag','Tag A','tag a','t','t')")
            connection.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) VALUES(1,20,'tag','test',1)")
            connection.execute("INSERT INTO asset(id,location,path,name,medium) VALUES(2,'local',?,'2.jpg','image')",
                               (r'R:\media\2.jpg',))
            connection.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) VALUES(2,20,'tag','test',1)")
        for path in ('/api/entity', '/api/photos'):
            named = self.client.get(path, params={'kind': 'tag', 'name': 'Tag A', 't': 'secret'})
            by_id = self.client.get(path, params={'kind': 'tag', 'id': '20', 't': 'secret'})
            self.assertEqual(named.status_code, 200, named.text)
            self.assertEqual(by_id.json(), named.json())
            if path == '/api/entity':
                self.assertEqual(by_id.json()['asset_count'], 1)
            else:
                self.assertEqual(by_id.json()['total'], 1)
                self.assertEqual(by_id.json()['items'][0]['id'], 2)

    def test_tag_hidden_assets_are_excluded_from_profiles_photos_and_samples(self):
        with sqlite3.connect(self.app.state.settings.db_path) as connection:
            connection.executemany(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES(?,'tag',?,?,'t','t')",
                [(20, 'Tag A', 'tag a'), (21, 'Hidden only', 'hidden only'), (22, 'Visible only', 'visible only')])
            add_person(connection, 12, 'Hidden cast')
            connection.execute("UPDATE asset SET code='ABC-001',size=10,snapshot_path='visible.jpg' WHERE id=1")
            connection.executemany(
                "INSERT INTO asset(id,location,path,name,medium,size,code,snapshot_path,play_count)"
                " VALUES(?,'local',?,?,?,?,?,?,?)",
                [(2, r'R:\media\visible\2.jpg', '2.jpg', 'image', 10, None, None, 0),
                 (3, r'R:\media\3.mp4', '3.mp4', 'video', 1000, 'ABC-002', 'hidden.jpg', 100),
                 (4, r'R:\media\hidden\4.jpg', '4.jpg', 'image', 10, None, None, 0),
                 (5, r'R:\media\5.mp4', '5.mp4', 'video', 500, 'ABC-003', 'hidden-cast.jpg', 0)])
            connection.executemany(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) VALUES(?,?,?,'test',1)",
                [(asset_id, 20, 'tag') for asset_id in (1, 2, 3, 4, 5)]
                + [(1, 22, 'tag'), (3, 21, 'tag'), (3, 10, 'performer'), (5, 12, 'performer')])
            connection.executemany(
                "INSERT INTO asset_tag_preference(profile_id,asset_id,normalized_tag,hidden,updated_at)"
                " VALUES('local-default',?,'tag a',1,0)", [(3,), (4,), (5,)])
            connection.executemany(
                "INSERT INTO code_sample_image(code,position,url,site,source,fetched_at)"
                " VALUES(?,1,'https://example.test/sample.jpg','dmm','test','t')",
                [('ABC-001',), ('ABC-002',), ('ABC-003',)])

        def read(path, kind='tag', entity_id=20):
            response = self.client.get(path, params={'kind': kind, 'id': entity_id, 't': 'secret'})
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        profile = read('/api/entity')
        self.assertEqual(profile['asset_count'], 1)
        self.assertEqual(profile['representative_asset_id'], 1)
        self.assertEqual({tag['id']: tag['n'] for tag in profile['tags']}, {20: 1, 22: 1})
        self.assertEqual([(person['id'], person['n'], person['rep']) for person in profile['related_performers']],
                         [(10, 1, 1)])
        photos = read('/api/photos')
        self.assertEqual(photos['total'], 1)
        self.assertEqual([item['id'] for item in photos['items']], [2])
        self.assertEqual({item['id']: item['n'] for item in photos['sets']}, {'code:ABC-001': 1, 2: 1})
        self.assertEqual(photos['sample_total'], 1)
        self.assertEqual(read('/api/entity', 'performer', 10)['asset_count'], 2)
        from peach.web_catalog import _entity_asset_filter, q_facets
        from peach.web_state import WebContract

        contract = WebContract(self.app.state.settings.db_path)
        with contract.read_connection() as connection:
            clause, params = _entity_asset_filter(connection, 'tag', 'Tag A')
            scoped_assets = connection.execute(f'SELECT a.id FROM asset a WHERE {clause} ORDER BY a.id', params).fetchall()
        self.assertEqual([row[0] for row in scoped_assets], [1, 2])
        facets = q_facets(contract, filters={'tag': 'Tag A'})
        self.assertEqual(facets['stats']['total'], profile['asset_count'])
        self.assertEqual({item['k']: item['n'] for item in facets['tags']}, {'Tag A': 1, 'Visible only': 1})
        scoped_facets = q_facets(contract, scope_kind='tag', scope_name='Tag A')
        self.assertEqual(scoped_facets['stats']['total'], profile['asset_count'])
        self.assertEqual(scoped_facets['tags'], facets['tags'])

        with sqlite3.connect(self.app.state.settings.db_path) as connection:
            connection.execute("UPDATE asset_tag_preference SET hidden=0 WHERE normalized_tag='tag a'")
        self.assertEqual(read('/api/entity')['asset_count'], 3)
        restored = read('/api/photos')
        self.assertEqual(restored['total'], 2)
        self.assertEqual(restored['sample_total'], 3)
        self.assertEqual(q_facets(contract, filters={'tag': 'Tag A'})['stats']['total'], 3)

    def test_an_old_image_address_redirects_to_the_target(self):
        body = picture("navy")
        (self.avatars / "performer-10.img").write_bytes(body)
        response = self.client.get(
            "/entity-image", params={"kind": "performer", "id": 11, "thumb": 1, "t": "secret"},
            follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        location = urlsplit(response.headers["location"])
        self.assertEqual((location.scheme, location.netloc, location.path), ("", "", "/entity-image"))
        self.assertEqual(parse_qs(location.query),
                         {"kind": ["performer"], "id": ["10"], "thumb": ["1"], "t": ["secret"]})
        followed = self.client.get(
            "/entity-image", params={"kind": "performer", "id": 11, "t": "secret"})
        self.assertEqual(followed.status_code, 200)
        self.assertEqual(followed.content, body)

    def test_an_unknown_id_still_404s(self):
        response = self.client.get(
            "/entity-image", params={"kind": "performer", "id": 999, "t": "secret"},
            follow_redirects=False)
        self.assertEqual(response.status_code, 404)

    def test_a_picture_picked_under_the_old_id_lands_on_the_target(self):
        body = picture("olive")
        response = self.client.post(
            "/api/avatar-pick?id=11&kind=performer&name=me.jpg&t=secret",
            content=body, headers={"content-type": "image/jpeg"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], 10)
        self.assertEqual((self.avatars / "performer-10.img").read_bytes(), body)
        self.assertFalse((self.avatars / "performer-11.img").exists())


if __name__ == "__main__":
    unittest.main()
