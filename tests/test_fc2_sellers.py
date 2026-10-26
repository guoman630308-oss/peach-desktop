"""卖家证据、账号冲突、馆藏匹配及按批撤回的行为验证。"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach import fc2_sellers
from peach.entities import upsert_asset_entity
from peach.entity_classification import write_claim
from peach.web_entity import q_entity, q_index
from peach.web_state import WebContract
from scripts import revert_auto_landing
from tests.support.ledger import fresh_ledger


class Fc2SellerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.db = fresh_ledger(self.root)
        self.connection = sqlite3.connect(self.db)
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        self.batch = fc2_sellers.SOURCE + '@test'

    def asset(self, code):
        row = self.connection.execute("INSERT INTO asset(location,path,name,medium,code,play_count,rating,creator) "
            "VALUES('local',?,?,'video',?,8,4,'User Account')", (f'B:\\FC2\\{code}.mp4',f'{code}.mp4',code))
        self.connection.commit()
        return row.lastrowid

    def snapshot(self, code='FC2-PPV-1234567', name='Seller', account='seller', provider='fc2ppvdb', **changes):
        number = code.rsplit('-',1)[1]
        page = f'https://fc2ppv-db.com/ja/videos/{number}' if provider == 'fc2ppvdb' else f'https://fc2cmadb.com/articles/{number}'
        row = {'id':code, 'label':name, 'seller_url':f'https://adult.contents.fc2.com/users/{account}/', 'source_url':page, **changes}
        (self.cache / f'{code}-{provider}.json').write_text(json.dumps(row),encoding='utf-8')
        return row

    def install(self):
        with self.connection:
            return fc2_sellers.install(self.connection,fc2_sellers.collect(self.connection,self.cache),batch=self.batch)

    def undo(self):
        with self.connection:
            aliases = revert_auto_landing.planned_aliases(self.connection,fc2_sellers.SOURCE,self.batch)
            refs = revert_auto_landing.planned_refs(self.connection,fc2_sellers.SOURCE,self.batch)
            links = revert_auto_landing.planned_links(self.connection,fc2_sellers.SOURCE,self.batch)
            sellers = fc2_sellers.planned_revert(self.connection,fc2_sellers.SOURCE,self.batch)
            for row in aliases:
                self.connection.execute('DELETE FROM entity_alias WHERE entity_id=? AND source=?',(row['entity_id'],row['source']))
            for row in refs:
                self.connection.execute('DELETE FROM entity_external_ref WHERE provider=? AND external_kind=? AND external_id=?',(row['provider'],row['kind'],row['id']))
            for row in links:
                self.connection.execute('DELETE FROM entity_link WHERE id=?',(row['id'],))
            fc2_sellers.revert(self.connection,sellers)

    def test_seller_account_and_split_files_share_work_without_changing_cast_or_history(self):
        asset_id = self.asset('FC2-PPV-1234567-1')
        self.asset('FC2-PPV-1234567-2')
        performer = upsert_asset_entity(self.connection,kind='performer',name='Cast',asset_id=asset_id,role='performer',source='user:manual')
        self.connection.commit()
        before = [tuple(row) for row in self.connection.execute('SELECT * FROM asset ORDER BY id')]
        cast = tuple(self.connection.execute('SELECT * FROM asset_entity WHERE entity_id=?',(performer,)).fetchone())
        self.snapshot()
        result = self.install()
        self.assertEqual(len(result['entity_ids']),1)
        contract = WebContract(self.db)
        page = q_index(contract,'creators')
        self.assertEqual(page['items'][0]['identity_labels'],['卖家'])
        self.assertEqual(page['items'][0]['n'],1)
        self.assertEqual(len(q_entity(contract,{'kind':'creator','name':'Seller'})['links']),1)
        self.assertEqual([tuple(row) for row in self.connection.execute('SELECT * FROM asset ORDER BY id')],before)
        self.assertEqual(tuple(self.connection.execute('SELECT * FROM asset_entity WHERE entity_id=?',(performer,)).fetchone()),cast)
        self.assertEqual(self.connection.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(self.install()['changes'],0)
        self.undo()
        self.assertEqual(self.connection.execute("SELECT count(*) FROM entity WHERE kind='creator'").fetchone()[0],0)
        self.assertEqual([tuple(row) for row in self.connection.execute('SELECT * FROM asset ORDER BY id')],before)

    def test_conflicting_source_accounts_are_skipped_and_nonlibrary_records_are_ignored(self):
        self.asset('FC2-PPV-1234567')
        self.snapshot()
        self.snapshot(provider='fc2cmadb',account='other')
        self.snapshot(code='FC2-PPV-7654321')
        plan = fc2_sellers.collect(self.connection,self.cache)
        self.assertEqual(plan['accounts'],[])
        self.assertEqual(plan['skipped'][0]['reason'],'作品来源给出不同卖家账号')

    def test_reuse_by_account_preserves_user_name_metadata_reference_and_seller_claim(self):
        one = self.asset('FC2-PPV-1234567')
        two = self.asset('FC2-PPV-7654321')
        entity_id = upsert_asset_entity(self.connection,kind='creator',name='User Name',asset_id=one,role='creator',
            source='user:manual',external_provider='fc2',external_id='seller',metadata={'user':'kept'})
        write_claim(self.connection,entity_id=entity_id,facet='account_role',value='seller',source='user:manual',status='approved',evidence='用户确认')
        self.connection.commit()
        entity = tuple(self.connection.execute('SELECT * FROM entity WHERE id=?',(entity_id,)).fetchone())
        ref = tuple(self.connection.execute('SELECT * FROM entity_external_ref WHERE entity_id=?',(entity_id,)).fetchone())
        self.snapshot(code='FC2-PPV-7654321',name='Public Name')
        self.install()
        self.assertEqual(tuple(self.connection.execute('SELECT * FROM entity WHERE id=?',(entity_id,)).fetchone()),entity)
        self.assertEqual(tuple(self.connection.execute('SELECT * FROM entity_external_ref WHERE entity_id=?',(entity_id,)).fetchone()),ref)
        self.assertEqual(self.connection.execute('SELECT alias FROM entity_alias WHERE entity_id=?',(entity_id,)).fetchone()[0],'Public Name')
        self.undo()
        self.assertEqual(self.connection.execute('SELECT count(*) FROM asset_entity WHERE entity_id=? AND asset_id=?',(entity_id,two)).fetchone()[0],0)
        self.assertEqual(tuple(self.connection.execute('SELECT * FROM entity WHERE id=?',(entity_id,)).fetchone()),entity)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM entity_classification WHERE entity_id=?',(entity_id,)).fetchone()[0],1)

    def test_same_name_different_account_and_invalid_public_evidence_do_not_land(self):
        one = self.asset('FC2-PPV-1234567')
        upsert_asset_entity(self.connection,kind='creator',name='Seller',asset_id=one,role='creator',source='user:manual',external_provider='fc2',external_id='other')
        self.connection.commit()
        row = self.snapshot()
        self.assertEqual(fc2_sellers.collect(self.connection,self.cache)['accounts'],[])
        for changes in ({'source_url':'https://fc2ppv-db.com/ja/videos/7654321'},
                        {'seller_url':'https://adult.contents.fc2.com.evil.test/users/seller/'},
                        {'seller_url':'https://adult.contents.fc2.com/users/seller/?token=private'},
                        {'label':'FC2-PPV'}):
            self.assertIsNone(fc2_sellers.seller_record({**row,**changes},'fc2ppvdb'))

    def test_new_accounts_with_shared_names_are_conflicts(self):
        self.asset('FC2-PPV-1234567')
        self.asset('FC2-PPV-7654321')
        self.snapshot()
        self.snapshot(code='FC2-PPV-7654321',account='other')
        plan = fc2_sellers.collect(self.connection,self.cache)
        self.assertEqual(plan['accounts'],[])
        self.assertEqual(len(plan['skipped']),2)
        self.assertTrue(all(row['reason']=='不同 FC2 账号使用相同卖家名称' for row in plan['skipped']))

    def test_revert_preserves_later_user_account_name(self):
        self.asset('FC2-PPV-1234567')
        self.snapshot()
        entity_id = self.install()['entity_ids'][0]
        self.connection.execute("UPDATE entity SET canonical_name='User Seller',normalized_name='userseller' WHERE id=?",(entity_id,))
        self.connection.commit()
        self.undo()
        self.assertEqual(self.connection.execute('SELECT canonical_name FROM entity WHERE id=?',(entity_id,)).fetchone()[0],'User Seller')

    def test_rejected_seller_name_is_skipped_while_other_accounts_still_land(self):
        rejected = self.asset('FC2-PPV-1234567')
        self.asset('FC2-PPV-7654321')
        self.connection.execute("INSERT INTO review_decision(category,item_key,status,note,updated_at) "
                                "VALUES('creator-attribution',?,'rejected','','2026-10-08T00:00:00+00:00')",
                                (f'{rejected}:seller',))
        self.connection.commit()
        self.snapshot()
        self.snapshot(code='FC2-PPV-7654321',name='Other Seller',account='other')
        result = self.install()
        self.assertEqual(len(result['entity_ids']),1)
        self.assertEqual([(row['code'],row['reason']) for row in result['skipped']],[('FC2-PPV-1234567','摄取判定不收这个卖家名')])
        names = [row[0] for row in self.connection.execute("SELECT canonical_name FROM entity WHERE kind='creator'")]
        self.assertEqual(names,['Other Seller'])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM entity_external_ref WHERE external_id='seller'").fetchone()[0],0)

    def test_held_account_gets_no_alias_or_claim_from_a_work_that_ingest_rejects(self):
        one = self.asset('FC2-PPV-1234567')
        two = self.asset('FC2-PPV-7654321')
        entity_id = upsert_asset_entity(self.connection,kind='creator',name='User Name',asset_id=one,role='creator',
            source='user:manual',external_provider='fc2',external_id='seller')
        self.connection.execute("INSERT INTO review_decision(category,item_key,status,note,updated_at) "
                                "VALUES('creator-attribution',?,'rejected','','2026-10-08T00:00:00+00:00')",
                                (f'{two}:{fc2_sellers.normalize_entity_name("User Name")}',))
        self.connection.commit()
        self.snapshot(code='FC2-PPV-7654321',name='Public Name')
        result = self.install()
        self.assertEqual(result['entity_ids'],[])
        self.assertEqual([row['asset_id'] for row in result['skipped']],[two])
        for table in ('entity_alias','entity_classification','entity_link'):
            self.assertEqual(self.connection.execute(f'SELECT count(*) FROM {table} WHERE entity_id=?',(entity_id,)).fetchone()[0],0)

    def test_collection_names_are_planned_against_the_account_that_ingest_picks(self):
        one = self.asset('FC2-PPV-1234567')
        two = self.asset('FC2-PPV-7654321')
        held = upsert_asset_entity(self.connection,kind='creator',name='Studio',asset_id=one,role='creator',source='user:manual')
        self.connection.commit()
        self.snapshot(code='FC2-PPV-7654321',name='Studio 4K')
        plan = fc2_sellers.collect(self.connection,self.cache)
        self.assertEqual(plan['accounts'][0]['entity_id'],held)
        self.install()
        self.assertEqual(self.connection.execute("SELECT entity_id FROM asset_entity WHERE asset_id=? AND role='creator'",(two,)).fetchone()[0],held)
        self.connection.execute("UPDATE entity_external_ref SET external_id='other' WHERE entity_id=?",(held,))
        self.connection.commit()
        (self.cache / 'FC2-PPV-7654321-fc2ppvdb.json').unlink()
        self.snapshot(code='FC2-PPV-1234567',name='Studio 4K',account='third')
        plan = fc2_sellers.collect(self.connection,self.cache)
        self.assertEqual([row['reason'] for row in plan['skipped']],['同名对应不同 FC2 账号'])

    def test_works_in_the_recycle_bin_get_no_seller(self):
        asset_id = self.asset('FC2-PPV-1234567')
        self.connection.execute("UPDATE asset SET disposal='trash' WHERE id=?",(asset_id,))
        self.connection.commit()
        self.snapshot()
        self.assertEqual(fc2_sellers.collect(self.connection,self.cache)['accounts'],[])

    def test_stale_work_plan_rolls_back_and_revert_keeps_later_user_metadata(self):
        asset_id = self.asset('FC2-PPV-1234567')
        self.snapshot()
        plan = fc2_sellers.collect(self.connection,self.cache)
        self.connection.execute("UPDATE asset SET code='ABW-001' WHERE id=?",(asset_id,))
        self.connection.commit()
        with self.assertRaisesRegex(ValueError,'已过期'):
            with self.connection:
                fc2_sellers.install(self.connection,plan,batch=self.batch)
        self.connection.execute("UPDATE asset SET code='FC2-PPV-1234567' WHERE id=?",(asset_id,))
        self.connection.commit()
        entity_id = self.install()['entity_ids'][0]
        self.connection.execute("UPDATE entity SET metadata_json=json_set(metadata_json,'$.user','kept') WHERE id=?",(entity_id,))
        self.connection.commit()
        self.undo()
        self.assertEqual(json.loads(self.connection.execute('SELECT metadata_json FROM entity WHERE id=?',(entity_id,)).fetchone()[0])['user'],'kept')
