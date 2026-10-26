"""创作者审计保留独立身份、保护人工判断，并可整批回滚。"""
import sqlite3
import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace

from peach.classification import creator_collection_base, is_structural_creator
from peach.entities import (apply_directory_rejections, creator_directory_key, derived_directory_rejections,
                            merge_entity, normalize_entity_name, upsert_asset_entity)
from peach.field_owners import write_owned_fields
from peach.entity_classification import write_claim
from peach.metadata_creator_attributions import apply_plan, collect, restore
from tests.support.ledger import fresh_ledger
from scripts import audit_creator_attributions as audit


class CreatorAttributionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = fresh_ledger(Path(self.directory.name).resolve())
        self.connection = sqlite3.connect(self.db)
        self.addCleanup(self.connection.close)

    def asset(self, name, *, path=None, studio=None, code=None, source='legacy:asset', metadata=None):
        cursor = self.connection.execute(
            "INSERT INTO asset(location,path,name,medium,creator,studio,code) VALUES('local',?,?,'video',?,?,?)",
            (path or f'B:\\创作者\\{name}\\clip.mp4', (path or 'clip.mp4').rsplit('\\', 1)[-1], name, studio, code))
        asset_id = cursor.lastrowid
        # 历史错误直接造在夹具里，摄取入口本身须拒绝发行平台。
        held = self.connection.execute("SELECT id FROM entity WHERE kind='creator' AND canonical_name=?", (name,)).fetchone()
        if held:
            entity_id = held[0]
        else:
            entity_id = self.connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at) VALUES('creator',?,?,'t','t')",
                (name, name.casefold())).lastrowid
        self.connection.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source,metadata_json) VALUES(?,?,'creator',?,?)",
                                (asset_id, entity_id, source, metadata or '{}'))
        return asset_id, entity_id

    def plan(self):
        return [row for row in collect(self.connection) if row['action'] in {'remove','replace'}]

    def test_rejected_account_attribution_blocks_directory_reingest_and_allows_manual_review(self):
        asset_id, entity_id = self.asset('Parent Account')
        self.connection.execute("DELETE FROM asset_entity WHERE asset_id=?", (asset_id,))
        self.connection.execute("INSERT INTO review_decision(category,item_key,status,updated_at) "
            "VALUES('creator-attribution',?,'rejected','t')", (f'{asset_id}:parent account',))
        self.assertIsNone(upsert_asset_entity(self.connection, kind='creator',name='Parent Account',
            asset_id=asset_id,role='creator',source='scan:directory'))
        self.assertEqual(self.connection.execute('SELECT count(*) FROM asset_entity WHERE asset_id=?',(asset_id,)).fetchone()[0],0)
        self.assertEqual(upsert_asset_entity(self.connection,kind='creator',name='Parent Account',
            asset_id=asset_id,role='creator',source='user:manual'),entity_id)

    def test_collection_quality_suffix_uses_the_rejected_canonical_account_decision(self):
        asset_id, _ = self.asset('Parent Account')
        self.connection.execute("INSERT INTO review_decision(category,item_key,status,updated_at) "
            "VALUES('creator-attribution',?,'rejected','t')", (f'{asset_id}:parent account',))
        self.assertIsNone(upsert_asset_entity(self.connection, kind='creator',name='Parent Account4K',
            asset_id=asset_id,role='creator',source='scan:directory'))

    def test_directory_rejection_covers_new_files_and_subdirectories(self):
        self.connection.execute("INSERT INTO review_decision(category,item_key,status,updated_at) "
            "VALUES('creator-attribution',?,'rejected','t')",
            (creator_directory_key('Parent Account', r'B:\创作者\Parent Account\Other Channel'),))
        blocked = [r'B:\创作者\Parent Account\Other Channel\new.mp4',
                   r'B:\创作者\Parent Account\other channel\图片\new.jpg']
        for path in blocked:
            asset_id, _ = self.asset('Parent Account', path=path)
            with self.subTest(path=path):
                self.assertIsNone(upsert_asset_entity(self.connection, kind='creator', name='Parent Account',
                    asset_id=asset_id, role='creator', source='scan:directory'))
                self.assertIsNotNone(upsert_asset_entity(self.connection, kind='creator', name='Parent Account',
                    asset_id=asset_id, role='creator', source='user:manual'))
        own_id, entity_id = self.asset('Parent Account', path=r'B:\创作者\Parent Account\own.mp4')
        self.assertEqual(upsert_asset_entity(self.connection, kind='creator', name='Parent Account',
            asset_id=own_id, role='creator', source='scan:directory'), entity_id)

    def test_fully_rejected_subtrees_yield_topmost_directory_rejections(self):
        root = r'B:\创作者\Parent Account'
        rejected = [rf'{root}\Other Channel\a.mp4', rf'{root}\Other Channel\图片\b.jpg',
                    rf'{root}\Mixed\c.mp4', r'B:\卖家\Reseller\Parent Account\e.mp4']
        for path in rejected:
            asset_id, _ = self.asset('Parent Account', path=path)
            self.connection.execute("INSERT INTO review_decision(category,item_key,status,note,updated_at) "
                "VALUES('creator-attribution',?,'rejected','另一账号','t')", (f'{asset_id}:parent account',))
        self.asset('Parent Account', path=rf'{root}\Mixed\own.mp4')
        proposals = derived_directory_rejections(self.connection)
        self.assertEqual([(row['directory'], row['subtree_assets']) for row in proposals],
                         [(rf'{root}\Other Channel', 2), (r'B:\卖家\Reseller\Parent Account', 1)])
        self.assertEqual(apply_directory_rejections(self.connection, proposals, reviewer='script:test'), 2)
        self.assertEqual(derived_directory_rejections(self.connection), [])
        new_id, _ = self.asset('Elsewhere', path=rf'{root}\Other Channel\新\d.mp4')
        self.assertIsNone(upsert_asset_entity(self.connection, kind='creator', name='Parent Account',
            asset_id=new_id, role='creator', source='scan:directory'))

    def test_collection_suffix_respects_rejection_without_a_canonical_account(self):
        asset_id, _ = self.asset('Parent Account4K')
        self.connection.execute("INSERT INTO review_decision(category,item_key,status,updated_at) "
            "VALUES('creator-attribution',?,'rejected','t')", (f'{asset_id}:parent account',))
        self.assertIsNone(upsert_asset_entity(self.connection, kind='creator', name='Parent Account4K',
            asset_id=asset_id, role='creator', source='scan:directory'))

    def test_merged_person_alias_ingests_into_the_preserved_artist(self):
        asset_id, creator_id = self.asset('Former Name')
        artist_id = upsert_asset_entity(self.connection,kind='performer',name='Current Name',
            asset_id=asset_id,role='performer',source='release:cast',metadata={'official':'preserved'})
        merge_entity(self.connection,target_id=artist_id,source_id=creator_id,source_name='Former Name',
                     alias_source='user:identity-merge:creator')
        another_id, _ = self.asset('Independent Account')
        found = upsert_asset_entity(self.connection,kind='creator',name='Former Name',
            asset_id=another_id,role='creator',source='scan:directory',metadata={'other':'data'})
        self.assertEqual(found,artist_id)
        self.assertIsNone(self.connection.execute("SELECT id FROM entity WHERE kind='creator' AND canonical_name='Former Name'").fetchone())
        self.assertEqual(self.connection.execute('SELECT role FROM asset_entity WHERE asset_id=? AND entity_id=?',
            (another_id,artist_id)).fetchone(),('performer',))
        self.assertEqual(json.loads(self.connection.execute('SELECT metadata_json FROM entity WHERE id=?',
            (artist_id,)).fetchone()[0]),{'official':'preserved'})

    def test_unmerged_artist_alias_keeps_an_independent_account(self):
        asset_id, _ = self.asset('Independent Account')
        artist_id = upsert_asset_entity(self.connection,kind='performer',name='Current Name',
            asset_id=asset_id,role='performer',source='release:cast')
        self.connection.execute("INSERT INTO entity_alias VALUES(?, 'Shared Alias','shared alias','source:directory',1)",(artist_id,))
        found = upsert_asset_entity(self.connection,kind='creator',name='Shared Alias',
            asset_id=asset_id,role='creator',source='scan:directory')
        self.assertNotEqual(found,artist_id)

    def test_content_month_quality_and_repost_directories_are_not_accounts(self):
        names = ['zj','11月','AI修复','黑丝','美腿足交','7sht.me','98T.la202202092146']
        for name in names:
            asset_id, _ = self.asset(name)
            self.assertIsNone(upsert_asset_entity(self.connection,kind='creator',name=name,
                asset_id=asset_id,role='creator',source='legacy:asset'))
        self.asset('Santa'); self.asset('banbi_555'); self.asset('alice.example.com')
        self.assertCountEqual([row['current_creator'] for row in self.plan()],names)
        self.asset('zj',path=r'B:\手动归属\zj\clip.mp4',source='user:manual')
        self.assertCountEqual([row['current_creator'] for row in self.plan()],names[1:])
        self.assertEqual({row['relation_source']:row['action'] for row in collect(self.connection)
                          if row['current_creator']=='zj'}, {'legacy:asset':'review', 'user:manual':'keep'})

    def test_directory_names_built_from_genre_vocabulary_are_not_accounts(self):
        for name in ('黑丝美腿', 'jk足交合集', '极品美腿天花板', '合集-巨乳 多创作者', '美腿 1080p',
                     '2023年5月', '4K', '맨발_모음', '검스A맨발B_풋잡', '&网红套图（走光）'):
            with self.subTest(name=name):
                self.assertTrue(is_structural_creator(name))
        for name in ('Alice', '海生', 'jk小美', 'fc2美臀OLさくら', 'Lena Anderson', 'analove', '黑丝小仙女'):
            with self.subTest(name=name):
                self.assertFalse(is_structural_creator(name))

    def test_collection_annotations_reduce_to_the_account_name(self):
        for name in ('someone 合集', 'someone合辑', 'someone全集', 'someone最新12v', 'someone  30v 40g',
                     'someone V12 80GB', 'someone(2)', 'someone 去重版 4K', 'someone 120GB', 'someone 1080p'):
            with self.subTest(name=name):
                self.assertEqual(creator_collection_base(name), 'someone')
        for name in ('someone 2509', 'someone', 'v12', '合集'):
            with self.subTest(name=name):
                self.assertEqual(creator_collection_base(name), name)

    def test_month_and_quality_collections_reuse_accounts_and_restore_all_relations(self):
        _, target = self.asset('muchi_tina',source='user:manual')
        ids = [self.asset(name)[0] for name in ['muchi_tina2025.4','muchi_tina10月','muchi_tina2025年一月']]
        self.connection.commit()
        frozen = self.plan()
        self.assertEqual([row['proposed_creator'] for row in frozen],['muchi_tina']*3)
        with self.connection:
            receipt = apply_plan(self.connection,frozen)
        for asset_id in ids:
            self.assertEqual(self.connection.execute('SELECT creator FROM asset WHERE id=?',(asset_id,)).fetchone()[0],'muchi_tina')
            self.assertEqual(self.connection.execute("SELECT entity_id FROM asset_entity WHERE asset_id=? AND role='creator'",(asset_id,)).fetchone()[0],target)
        with self.connection:
            restore(self.connection,receipt)
        self.assertEqual(len(self.plan()),3)
        self.assertEqual(self.connection.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_versioned_release_directories_and_publisher_numbers_are_excluded(self):
        samples = {
            'WAAA-415_6K-C':r'B:\云下载\WAAA-415_6K-C\@Milan@ty999.me_WAAA-415_6K-C.mp4',
            'wavr00178pl':r'B:\云下载\wavr00178pl\wavr00178.part1.mp4',
            'fellatiojapan-090-':r'B:\创作者\fellatiojapan-090-\fellatiojapan-090-AyumuNakahori-1080p\090-AyumuNakahori-1080p.mp4',
            'fellatiojapan-324':r'B:\云下载\fellatiojapan-324\2048社区 - big2048.com@fellatiojapan-324.mp4',
        }
        for name,path in samples.items():
            asset_id,_ = self.asset(name,path=path)
            self.assertIsNone(upsert_asset_entity(self.connection,kind='creator',name=name,
                              asset_id=asset_id,role='creator',source='scan:directory'))
        self.asset('banbi_555',path=r'B:\创作者\banbi_555\scene title.mp4')
        self.asset('ABW-987',path=r'B:\创作者\ABW-987\another title.mp4')
        self.assertCountEqual([row['current_creator'] for row in self.plan()],samples)

    def test_release_directory_does_not_assign_its_code_to_unrelated_files(self):
        examples = [('WAAA-415_6K-C', 'HMN-597.mp4'),
                    ('WAAA-415_6K-C', 'daily life.mp4'),
                    ('gachincoppv-1009-HD', 'gachincoppv-1009-HD1.wmv'),
                    ('gachincoppv-1009-HD', 'gachincoppv-1009-HD3.wmv')]
        ids = []
        for name, filename in examples:
            asset_id, _ = self.asset(name, path=f'B:\\云下载\\{name}\\{filename}')
            ids.append(asset_id)
            self.assertIsNone(upsert_asset_entity(self.connection,kind='creator',name=name,
                              asset_id=asset_id,role='creator',source='scan:directory'))
        self.asset('banbi_555', path=r'B:\创作者\banbi_555\holiday.mp4')
        frozen = self.plan()
        self.assertCountEqual([row['asset_id'] for row in frozen], ids)
        with self.connection:
            receipt = apply_plan(self.connection, frozen)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM asset WHERE creator IS NULL AND code IS NULL').fetchone()[0], 4)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM asset').fetchone()[0], 5)
        with self.connection:
            restore(self.connection, receipt)
        self.assertCountEqual([row['asset_id'] for row in self.plan()], ids)

    def test_tokyo_publisher_requires_registered_studio_and_matching_release_file(self):
        asset_id,_ = self.asset('Tokyo',studio='东京热',code='n1042',path=r'B:\云下载\TokyoHot-n1042.mp4')
        studio_id = upsert_asset_entity(self.connection,kind='studio',name='东京热',
                                       asset_id=asset_id,role='studio',source='release:studio')
        self.connection.execute("INSERT INTO entity_alias(entity_id,alias,normalized_alias,source) VALUES(?,'Tokyo Hot','tokyo hot','release')",(studio_id,))
        self.asset('Tokyo',studio='东京热',code='n1042',path=r'B:\创作者\Tokyo\unrelated.mp4')
        self.assertEqual([row['asset_id'] for row in self.plan()],[asset_id])

    def test_source_confirmed_publisher_lands_as_studio_and_is_reversible(self):
        asset_id, entity_id = self.asset('Published Brand')
        write_claim(self.connection,entity_id=entity_id,facet='account_role',value='studio',
                    source='source:official',source_url='https://publisher.test/',evidence='发行目录将出演者与厂牌分列',
                    status='observed',confidence=1)
        self.connection.commit()
        self.assertIsNone(upsert_asset_entity(self.connection,kind='creator',name='Published Brand',
                                             asset_id=asset_id,role='creator',source='scan:directory'))
        self.assertIsNone(upsert_asset_entity(self.connection,kind='creator',name='Published Brand2025.05',
                                             asset_id=asset_id,role='creator',source='scan:directory'))
        with self.connection:
            receipt = apply_plan(self.connection,self.plan())
        self.assertEqual(self.connection.execute('SELECT creator,studio FROM asset WHERE id=?',(asset_id,)).fetchone(),(None,'Published Brand'))
        self.assertEqual(len(receipt['created_entities']),1)
        with self.connection:
            restore(self.connection,receipt)
        self.assertEqual(self.connection.execute('SELECT creator,studio FROM asset WHERE id=?',(asset_id,)).fetchone(),('Published Brand',None))
        self.assertEqual(self.connection.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_confirmed_studio_and_aliases_are_excluded_from_automatic_creator_ingest(self):
        asset_id, entity_id = self.asset('Published Brand')
        write_claim(self.connection,entity_id=entity_id,facet='account_role',value='studio',
                    source='source:official',source_url='https://publisher.test/',evidence='发行厂牌官网',
                    status='observed',confidence=1)
        self.connection.execute("INSERT INTO entity_alias(entity_id,alias,normalized_alias,source) VALUES(?,'Brand.test','brand.test','release')",(entity_id,))
        receipt = apply_plan(self.connection,self.plan())
        studio_id = receipt['created_entities'][0]['id']
        merge_entity(self.connection,target_id=studio_id,source_id=entity_id,
                     source_name='Published Brand',alias_source='user:identity-merge:creator')
        for name in ('Published Brand','Published Brand2025.05','Brand.test','Brand.test2025.05'):
            with self.subTest(name=name):
                self.assertIsNone(upsert_asset_entity(self.connection,kind='creator',name=name,
                                  asset_id=asset_id,role='creator',source='scan:directory'))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM entity WHERE kind='creator'").fetchone()[0],0)
        self.assertEqual(self.connection.execute('SELECT entity_id,role FROM asset_entity WHERE asset_id=?',(asset_id,)).fetchall(),[(studio_id,'studio')])
        self.assertEqual(self.connection.execute('PRAGMA foreign_key_check').fetchall(),[])
        manual_id = upsert_asset_entity(self.connection,kind='creator',name='Published Brand',
                                       asset_id=asset_id,role='creator',source='user:manual')
        self.assertIsNotNone(manual_id)

    def test_unconfirmed_studio_claim_does_not_block_creator_ingest(self):
        asset_id, _ = self.asset('Alice')
        studio_id = upsert_asset_entity(self.connection,kind='studio',name='Brand',
                                       asset_id=asset_id,role='studio',source='source:catalog')
        write_claim(self.connection,entity_id=studio_id,facet='account_role',value='studio',
                    source='source:search',evidence='待核验的同名检索',status='candidate',confidence=0.5)
        self.assertIsNotNone(upsert_asset_entity(self.connection,kind='creator',name='Brand',
                              asset_id=asset_id,role='creator',source='scan:directory'))

    def test_created_studio_with_subsequent_alias_refuses_atomic_restore(self):
        asset_id, entity_id = self.asset('Published Brand')
        write_claim(self.connection,entity_id=entity_id,facet='account_role',value='studio',
                    source='source:official',source_url='https://publisher.test/',evidence='发行厂牌官网',
                    status='observed',confidence=1)
        self.connection.commit()
        with self.connection:
            receipt = apply_plan(self.connection,self.plan())
        studio_id = receipt['created_entities'][0]['id']
        self.connection.execute("INSERT INTO entity_alias(entity_id,alias,normalized_alias,source) VALUES(?,'Brand','brand','user:manual')",(studio_id,))
        self.connection.commit()
        with self.assertRaises(ValueError):
            with self.connection:
                restore(self.connection,receipt)
        self.assertEqual(self.connection.execute('SELECT creator,studio FROM asset WHERE id=?',(asset_id,)).fetchone(),(None,'Published Brand'))
        self.assertEqual(self.connection.execute('SELECT count(*) FROM entity_alias WHERE entity_id=?',(studio_id,)).fetchone()[0],1)

    def test_collection_ingest_reuses_existing_account_and_preserves_profile(self):
        _, target = self.asset('muchi_tina',source='user:manual')
        self.connection.execute("UPDATE entity SET metadata_json='{\"profile\":\"kept\"}' WHERE id=?",(target,))
        asset_id,_ = self.asset('other')
        returned = upsert_asset_entity(self.connection,kind='creator',name='muchi_tina2025.05',
                                      asset_id=asset_id,role='creator',source='scan:directory')
        self.assertEqual(returned,target)
        self.assertEqual(self.connection.execute('SELECT metadata_json FROM entity WHERE id=?',(target,)).fetchone()[0],'{"profile":"kept"}')

    def test_platform_is_not_ingested_as_creator(self):
        asset_id, _ = self.asset('RealSeller')
        entity_id = upsert_asset_entity(self.connection, kind='creator', name='Myfans',
                                        asset_id=asset_id, role='creator', source='user:manual')
        self.assertIsNone(entity_id)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM entity WHERE canonical_name='Myfans'").fetchone()[0], 0)

    def test_audit_covers_all_sources_and_preserves_accounts_and_manual_identity(self):
        self.asset('Myfans')
        self.asset('Alice', path='https://example.test/users/alice')
        self.asset('ConfirmedSeller', source='user:watermark')
        self.asset('banbi_555', path=r'B:\创作者\banbi_555\a title.mp4')
        self.asset('FC2-PPV', source='user:manual')
        rows = collect(self.connection)
        self.assertEqual(len(rows), 5)
        self.assertEqual([row['current_creator'] for row in rows if row['action']=='remove'], ['Myfans'])
        self.assertEqual({row['verdict'] for row in rows}, {'platform','online_account','source_identity','identifier_candidate'})

    def test_studio_aliases_and_dated_scene_titles_have_file_evidence(self):
        asset_id, _ = self.asset('Das', studio='ダスッ！')
        studio_id = upsert_asset_entity(self.connection, kind='studio', name='ダスッ！',
                                        asset_id=asset_id, role='studio', source='release:studio')
        self.connection.execute("INSERT INTO entity_alias(entity_id,alias,normalized_alias,source) VALUES(?,'Das','das','release')", (studio_id,))
        self.asset('Freya Mayer Ready to Serve You', path=r'B:\西方\DarkRoomVR.21.04.02.Freya.Mayer.Ready.to.Serve.You.XXX\movie.mp4', studio='DarkRoomVR')
        self.asset('Jane Doe', path=r'B:\西方\Studio.21.04.02.Jane.Doe.XXX\movie.mp4')
        self.asset('DPMI-058_000', path=r'B:\云下载\DPMI-058_000\dpmi00058hhb_000.mp4')
        self.asset('ABW-123', path=r'B:\云下载\ABW-123\ABW-123.mp4', code='ABW-123')
        self.assertEqual({row['current_creator']: row['verdict'] for row in self.plan()}, {
            'Das':'studio', 'Freya Mayer Ready to Serve You':'release_title',
            'DPMI-058_000':'release_identifier', 'ABW-123':'release_identifier'})

    def test_repair_and_restore_preserve_identity_metadata_and_personal_records(self):
        asset_id, entity_id = self.asset('Myfans', metadata='{"evidence":"folder"}')
        self.connection.execute('UPDATE asset SET play_count=9,rating=4 WHERE id=?', (asset_id,))
        self.connection.commit()
        plan = self.plan()
        with self.connection:
            manifest = apply_plan(self.connection, plan)
        self.assertEqual(len(manifest['relations']), 1)
        self.assertEqual(self.connection.execute('SELECT creator,play_count,rating FROM asset WHERE id=?', (asset_id,)).fetchone(), (None,9,4))
        self.assertEqual(self.connection.execute('SELECT canonical_name FROM entity WHERE id=?', (entity_id,)).fetchone(), ('Myfans',))
        with self.connection:
            restore(self.connection, manifest)
        self.assertEqual(self.connection.execute('SELECT creator,play_count,rating FROM asset WHERE id=?', (asset_id,)).fetchone(), ('Myfans',9,4))
        self.assertEqual(self.connection.execute('SELECT metadata_json FROM asset_entity WHERE asset_id=?', (asset_id,)).fetchone(), ('{"evidence":"folder"}',))
        self.assertEqual(self.connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_stale_plan_and_later_edits_reject_whole_batch(self):
        asset_id, _ = self.asset('Myfans')
        other_id, _ = self.asset('FC2-PPV')
        self.connection.commit()
        plan = self.plan()
        write_owned_fields(self.connection, [asset_id], {'creator':'Seller'}, 'user:manual')
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, '计划'):
            with self.connection:
                apply_plan(self.connection, plan)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM asset_entity').fetchone()[0], 2)
        with self.connection:
            manifest = apply_plan(self.connection, self.plan())
        write_owned_fields(self.connection, [other_id], {'creator':'VerifiedSeller'}, 'user:manual')
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, '后续改动'):
            with self.connection:
                restore(self.connection, manifest)
        self.assertEqual(self.connection.execute('SELECT creator FROM asset WHERE id=?', (other_id,)).fetchone()[0], 'VerifiedSeller')

    def test_protected_and_independent_identity_are_reviewed(self):
        asset_id, entity_id = self.asset('Myfans')
        write_owned_fields(self.connection, [asset_id], {'creator':'Myfans'}, 'review:identity')
        self.asset('FC2-PPV')
        self.connection.execute("INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id) VALUES(?, 'site','creator','seller-12')", (entity_id,))
        self.assertEqual([row['current_creator'] for row in self.plan()], ['FC2-PPV'])

    def test_publisher_moves_to_existing_studio_and_restores_as_a_batch(self):
        studio_asset, _ = self.asset('Alice')
        studio_id = upsert_asset_entity(self.connection, kind='studio', name='Vixen',
                                        asset_id=studio_asset, role='studio', source='release:studio', metadata={'official':'preserved'})
        studio_before = self.connection.execute('SELECT metadata_json,updated_at FROM entity WHERE id=?', (studio_id,)).fetchone()
        asset_id, creator_id = self.asset('Vixen', path=r'B:\云下载\A Scene [Vixen.com]\scene.mp4')
        self.connection.commit()
        with self.connection:
            manifest = apply_plan(self.connection, self.plan())
        self.assertEqual(self.connection.execute('SELECT creator,studio FROM asset WHERE id=?', (asset_id,)).fetchone(), (None,'Vixen'))
        self.assertEqual(self.connection.execute('SELECT entity_id,role FROM asset_entity WHERE asset_id=?', (asset_id,)).fetchall(), [(studio_id,'studio')])
        self.assertEqual(self.connection.execute('SELECT metadata_json,updated_at FROM entity WHERE id=?', (studio_id,)).fetchone(), studio_before)
        with self.connection:
            restore(self.connection, manifest)
        self.assertEqual(self.connection.execute('SELECT creator,studio FROM asset WHERE id=?', (asset_id,)).fetchone(), ('Vixen',None))
        self.assertEqual(self.connection.execute('SELECT entity_id,role FROM asset_entity WHERE asset_id=?', (asset_id,)).fetchall(), [(creator_id,'creator')])

    def test_site_post_identity_requires_matching_file_and_preserves_accounts(self):
        self.asset('[7sht.me]legsjapan-955', path=r'B:\云下载\[7sht.me]legsjapan-955\legsjapan-955.mp4')
        self.asset('fellatiojapan-312', path=r'B:\云下载\fellatiojapan-312\fellatiojapan-312.mp4')
        self.asset('legsjapan-999', path=r'B:\创作者\legsjapan-999\a title.mp4')
        self.assertEqual({row['current_creator'] for row in self.plan()}, {'[7sht.me]legsjapan-955','fellatiojapan-312'})

    def test_restore_refuses_a_creator_relation_added_without_flat_field_changes(self):
        asset_id, _ = self.asset('Myfans')
        self.connection.commit()
        with self.connection:
            manifest = apply_plan(self.connection, self.plan())
        upsert_asset_entity(self.connection, kind='creator', name='NewSeller', asset_id=asset_id,
                            role='creator', source='user:manual')
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, '关系已发生后续改动'):
            with self.connection:
                restore(self.connection, manifest)

    def performers(self, *names):
        for name in names:
            self.connection.execute("INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at) "
                                    "VALUES('performer',?,?,'t','t')", (name, normalize_entity_name(name)))

    def shaped(self, account, filenames, source='legacy:asset'):
        for filename in filenames:
            self.asset(account, path=f'B:\\创作者\\{account}\\{filename}', source=source)
        return {row['verdict'] for row in collect(self.connection) if row['current_creator'] == account}

    def test_series_naming_with_rotating_cast_is_a_review_candidate_not_a_repair(self):
        self.performers('八ッ橋さい子', '本多由奈', '北川ゆず')
        self.assertEqual(self.shaped('COSH こすっち', ['こすっち001 八ッ橋さい子.mp4', 'こすっち002 八ッ橋さい子.mp4',
                                                     'こすっち003 本多由奈.mp4', 'こすっち005 北川ゆず.mp4']), {'label_shape'})
        uralesbian = ['Akari Asagiri & Eri Hirasawa - 120 - Sensual Kiss.mp4',
                      'Shino Aoi & Mai Miori - 108 - Double Masturbation.mp4', 'ChieKobayashi-ShinoAoi-039-1080p.mp4']
        self.assertEqual(self.shaped('UraLesbian', uralesbian, source='user:manual-organize'), {'label_shape'})
        self.assertEqual(self.plan(), [])

    def test_series_naming_does_not_hold_back_a_verified_studio_repair(self):
        _, entity_id = self.asset('Label Works', path='B:\\创作者\\Label Works\\Ann Lee & Mia Rose - 01 - Kiss.mp4')
        write_claim(self.connection, entity_id=entity_id, facet='account_role', value='studio', source='source:publisher',
                    source_url='https://label.test/', evidence='官网', status='observed', confidence=1)
        verdicts = self.shaped('Label Works', ['Ann Lee & Leo King - 02 - Pool.mp4', 'Sam Fox & Mia Rose - 03 - Day.mp4'])
        self.assertEqual(verdicts, {'verified_studio'})

    def test_couple_accounts_titles_and_stray_files_keep_their_account_verdicts(self):
        self.performers('Mia Rose', 'Leo King', 'Ann Lee')
        couple = ['Mia Rose & Leo King - 01 - Beach.mp4', 'Mia Rose & Ann Lee - 02 - Pool.mp4',
                  'Mia Rose & Leo King - 03 - Night.mp4', 'Mia Rose & Sam Fox - 04 - Day.mp4']
        titles = ['Account 012 Beach Day.mp4', 'Account 013 Hot Tub.mp4', 'Account 014 Night Out.mp4']
        stray = ['MariHirose-RiaKurumi-488-1080p.mp4', 'clip1.mp4', 'clip2.mp4', 'clip3.mp4']
        for account, names in [('Couple', couple), ('Account', titles), ('RiaKurumi', stray)]:
            self.assertNotIn('label_shape', self.shaped(account, names))

    def test_cli_preserves_existing_foreign_key_violations_and_keeps_a_restore_manifest(self):
        _, entity_id = self.asset('Myfans')
        self.connection.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source) VALUES(99999,?,'tag','legacy')", (entity_id,))
        self.connection.commit()
        baseline = self.connection.execute('PRAGMA foreign_key_check').fetchall()
        manifest = self.db.parent / 'manifest.json'
        args = SimpleNamespace(db=self.db, apply=True, backup=self.db.parent/'before.db', restore=None, manifest=manifest)
        audit._write_ledger(args, {'rows':self.plan()})
        payload = json.loads(manifest.read_text(encoding='utf-8'))
        self.assertEqual(payload['status'], 'committed')
        self.assertEqual(self.connection.execute('PRAGMA foreign_key_check').fetchall(), baseline)
        args.backup = self.db.parent / 'repaired.db'
        args.restore = manifest
        args.manifest = None
        audit._write_ledger(args, payload)
        self.assertEqual(self.connection.execute('SELECT creator FROM asset').fetchone()[0], 'Myfans')
        self.assertEqual(self.connection.execute('PRAGMA foreign_key_check').fetchall(), baseline)


if __name__ == '__main__':
    unittest.main()
