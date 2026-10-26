"""浏览分类、来源保护、身份衔接与分页的行为回归。"""
import sqlite3
import tempfile
import unittest
from unittest import mock

from peach import entity_classification as classification, entity_identity_research as research
from peach.entities import merge_entity, upsert_asset_entity
from peach.web_entity import q_index, q_entity, q_suggest
from peach.web_catalog import q_items
from peach.web_state import WebContract
from peach.web_catalog import attach_avatar_availability
from peach.performer_alias_followup import Missing, Unavailable
from peach.sources.base import Page
from tests.support.ledger import fresh_ledger

SOUGOU = research.WIKI_ROOTS['sougouwiki'] + 'd/'


def _work_table(*cast):
    rows = ''.join(f'<tr><td>COSH-00{index}</td><td>こすっち00{index}</td>'
                   f'<td><a href="{SOUGOU}%A4%A2{index}">{name}</a></td></tr>' for index, name in enumerate(cast, 1))
    return ('<meta charset="utf-8"><div id="page-body"><div class="user-area"><table>'
            '<tr><th>NO</th><th>TITLE</th><th>ACTRESS</th></tr>' + rows + '</table></div></div>').encode()


class _Pages:
    """站上只有 `listed` 里的页；`failing` 里的地址模拟网络失败。"""

    def __init__(self, listed=None, failing=()):
        self.listed, self.failing, self.asked = listed or {}, set(failing), []

    def get(self, url):
        self.asked.append(url)
        if url in self.failing:
            raise Unavailable('Wiki 网络请求未取得')
        if url not in self.listed:
            raise Missing('站上没有这一页')
        return Page(url, self.listed[url])


class EntityClassificationTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db=fresh_ledger(self.directory.name)
        self.connection=sqlite3.connect(self.db)
        self.addCleanup(self.connection.close)
        self.connection.row_factory=sqlite3.Row

    def entity(self,name,kind='creator'):
        self.connection.execute("INSERT INTO asset(location,path,name,medium,creator,studio) VALUES('local',?,?, 'video',?,'Publisher')",
            (f'B:\\西方\\Publisher\\Publisher.24.12.02.{name.replace(" ",".")}.mp4',f'{name}.mp4',name))
        asset_id=self.connection.execute('SELECT last_insert_rowid()').fetchone()[0]
        entity_id=upsert_asset_entity(self.connection,kind=kind,name=name,asset_id=asset_id,role=kind,source='legacy:asset')
        self.connection.commit()
        return asset_id,entity_id

    def test_account_work_portraits_without_person_identity_are_marked_as_stand_ins(self):
        asset_id,entity_id=self.entity('Publisher Account')
        self.connection.execute("UPDATE asset SET snapshot_path='sheet.jpg' WHERE id=?",(asset_id,))
        self.connection.commit()
        contract=WebContract(self.db)
        with mock.patch.object(WebContract,'has_avatar',return_value=True):
            row={'entity_id':entity_id,'rep':asset_id,'has_image':True}
            attach_avatar_availability(contract,[row])
            self.assertTrue(row['has_avatar'])
            self.assertTrue(row['avatar_stand_in'])
            self.assertTrue(row['has_image'])
            self.claim(entity_id,'identity','person')
            self.connection.commit()
            attach_avatar_availability(contract,[row])
            self.assertTrue(row['has_avatar'])
            self.assertNotIn('avatar_stand_in',row)

    def claim(self,entity_id,facet,value,**kwargs):
        classification.write_claim(self.connection,entity_id=entity_id,facet=facet,value=value,
            source='source:publisher',source_url='https://publisher.test/profile',evidence='发行方资料',status='observed',confidence=1,**kwargs)

    def finding(self,name,entity_id):
        return {'entity_id':entity_id,'name':name,'cast_studios':['Publisher'],
            'cast_source_url':'https://publisher.test/profile', 'claims':[
                {'facet':'identity','value':'person','source_url':'https://publisher.test/profile','evidence':'发行方署名','status':'observed','confidence':1},
                {'facet':'occupation','value':'adult_performer','source_url':'https://publisher.test/profile','evidence':'发行出演名单','status':'observed','confidence':1}]}

    def test_multiple_markets_and_occupations_are_independent_of_entity_role(self):
        _,entity_id=self.entity('Known Person',kind='performer')
        for facet,value in [('identity','person'),('occupation','adult_performer'),('occupation','model'),('market','japanese_av'),('market','western_adult')]:
            self.claim(entity_id,facet,value)
        self.connection.commit()
        data=q_entity(WebContract(self.db),{'kind':'performer','name':'Known Person'})
        self.assertEqual(data['identity_labels'],['女优','西方'])
        self.assertEqual(len(classification.classifications(self.connection,[entity_id])[entity_id]),5)
        self.assertNotIn('classifications',data)

    def test_candidates_and_search_hits_do_not_qualify_for_trusted_filters(self):
        _,known=self.entity('Known Person'); _,unknown=self.entity('Unknown Person')
        self.claim(known,'account_role','seller')
        classification.write_claim(self.connection,entity_id=unknown,facet='account_role',value='seller',source='script:lookup',evidence='同名搜索命中')
        self.connection.commit()
        data=q_index(WebContract(self.db),'performers',limit=1,category='blogger')
        self.assertEqual(data['items'],[])
        self.assertFalse(data['has_more'])
        self.assertEqual(data['categories'],{})
        unclassified=q_index(WebContract(self.db),'performers')
        self.assertEqual([row['entity_id'] for row in unclassified['items']],[unknown])
        self.assertEqual(unclassified['items'][0]['identity_labels'],[])
        self.assertEqual(q_index(WebContract(self.db),'creators',category='seller')['items'][0]['entity_id'],known)
        self.assertEqual(q_index(WebContract(self.db),'creators',category="person' OR 1=1")['items'],[])

    def test_personal_accounts_publishers_and_animation_have_distinct_categories(self):
        _,person=self.entity('Personal Account')
        self.claim(person,'identity','person')
        _,publisher=self.entity('Publishing Account')
        self.claim(publisher,'identity','account')
        self.claim(publisher,'account_role','publisher')
        _,animation=self.entity('Animation Account')
        self.claim(animation,'identity','account')
        self.claim(animation,'account_role','publisher')
        self.claim(animation,'occupation','animator')
        _,collection=self.entity('Resource Collection')
        self.claim(collection,'identity','collection')
        self.claim(collection,'occupation','content_creator')
        self.connection.commit()
        contract=WebContract(self.db)
        self.assertCountEqual([r['entity_id'] for r in q_index(contract,'performers',category='blogger')['items']],
                              [person,publisher])
        self.assertEqual(q_entity(contract,{'kind':'creator','name':'Resource Collection'})['identity_labels'],[])
        self.assertNotIn(collection,[r['entity_id'] for r in q_index(contract,'performers')['items']])
        self.assertEqual(q_index(contract,'performers',category='animation')['items'][0]['entity_id'],animation)

    def test_existing_jav_directory_identity_is_shared_by_profiles_and_paginated_filters(self):
        expected = []
        for provider in sorted(classification.JAV_DIRECTORIES):
            _, entity_id = self.entity(provider, kind='performer')
            self.connection.execute('INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id) '
                                    'VALUES(?,?,?,?)', (entity_id,provider,'performer',str(entity_id)))
            expected.append(entity_id)
        self.connection.commit()
        before = self.connection.total_changes
        for provider in sorted(classification.JAV_DIRECTORIES):
            profile = q_entity(WebContract(self.db),{'kind':'performer','name':provider})
            self.assertEqual(profile['identity_labels'],['女优'])
            self.assertEqual(classification.classifications(self.connection,[profile['id']])[profile['id']],[])
        found = []
        for offset in range(len(expected)):
            page = q_index(WebContract(self.db),'performers',category='japanese_av',limit=1,offset=offset)
            found.extend(row['entity_id'] for row in page['items'])
            self.assertEqual(page['categories'],{'japanese_av':len(expected)})
        self.assertCountEqual(found,expected)
        self.assertEqual(self.connection.total_changes,before)

    def test_collection_refs_general_directories_and_candidates_do_not_assert_jav_identity(self):
        for provider, external_kind in [('minnano-av','production'),('babepedia','performer'),
                                        ('stash','performer'),('r18','performer_name'),('kmib','performer')]:
            _, entity_id = self.entity(provider, kind='performer')
            self.connection.execute('INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id) '
                                    'VALUES(?,?,?,?)', (entity_id,provider,external_kind,str(entity_id)))
            classification.write_claim(self.connection,entity_id=entity_id,facet='market',value='japanese_av',
                                       source='script:search',evidence='未核验同名搜索')
        self.connection.commit()
        data = q_index(WebContract(self.db),'performers')
        self.assertEqual(len(data['items']),5)
        self.assertTrue(all(row['identity_labels']==[] for row in data['items']))
        self.assertEqual(q_index(WebContract(self.db),'performers',category='japanese_av')['items'],[])

    def test_category_counts_respect_search_and_visible_works_without_being_limited_to_one_page(self):
        _, seller = self.entity('Seller Match')
        self.claim(seller,'account_role','seller')
        self.entity('Creator Match'); self.entity('Outside')
        asset_id, hidden = self.entity('Vanished Match')
        self.connection.execute("UPDATE asset SET disposal='vanished' WHERE id=?",(asset_id,))
        self.connection.commit()
        data = q_index(WebContract(self.db),'creators',q='Match',limit=1,category='seller')
        self.assertEqual(data['categories'],{'seller':1})
        self.assertEqual([row['entity_id'] for row in data['items']],[seller])

    def test_fc2_release_does_not_determine_performer_occupation(self):
        asset_id, entity_id = self.entity('Known Person',kind='performer')
        self.claim(entity_id,'market','japanese_av')
        self.connection.execute("UPDATE asset SET code='FC2-PPV-1234567',region='west' WHERE id=?",(asset_id,))
        self.connection.commit()
        for category in ('japanese_av','western'):
            page = q_index(WebContract(self.db),'performers',category=category,limit=1)
            self.assertEqual(page['categories'],{'japanese_av':1,'western':1})
            self.assertEqual(page['items'][0]['identity_labels'],['女优','西方'])
            self.assertEqual(page['items'][0]['identity_categories'],['japanese_av','western'])
        self.assertEqual(q_index(WebContract(self.db),'performers',category='amateur')['items'],[])
        self.connection.execute("UPDATE asset SET disposal='vanished' WHERE id=?",(asset_id,))
        self.connection.commit()
        self.assertEqual(classification.summaries(self.connection,[entity_id])[entity_id]['identity_labels'],['女优'])

    def test_amateur_scene_tag_does_not_classify_a_jav_performer_as_amateur(self):
        asset_id, entity_id = self.entity('JAV Performer',kind='performer')
        self.claim(entity_id,'market','japanese_av')
        self.connection.execute("UPDATE asset SET code='SIRO-5333' WHERE id=?",(asset_id,))
        upsert_asset_entity(self.connection,kind='tag',name='素人',asset_id=asset_id,
                            role='tag',source='test:scene-tag')
        self.connection.commit()
        contract=WebContract(self.db)
        profile=q_entity(contract,{'kind':'performer','name':'JAV Performer'})
        self.assertEqual(profile['identity_labels'],['女优'])
        self.assertEqual(q_index(contract,'performers',category='amateur')['items'],[])
        self.assertEqual(q_index(contract,'performers')['categories'],{'japanese_av':1})

    def work(self,name,code=None,*,tag=None,disposal=None):
        """给已有出演者再挂一部作品。"""
        asset_id=self.connection.execute("INSERT INTO asset(location,path,name,medium,code,disposal) VALUES('local',?,?,'video',?,?)",
            (f'B:\\作品\\{name}\\{code or "clip"}-{self.connection.execute("SELECT count(*) FROM asset").fetchone()[0]}.mp4',
             'clip.mp4',code,disposal)).lastrowid
        upsert_asset_entity(self.connection,kind='performer',name=name,asset_id=asset_id,role='performer',source='legacy:asset')
        if tag:
            upsert_asset_entity(self.connection,kind='tag',name=tag,asset_id=asset_id,role='tag',source='test:scene-tag')
        return asset_id

    def test_amateur_identity_follows_trusted_claims_or_all_amateur_releases(self):
        _, confirmed = self.entity('Nonprofessional Cast',kind='performer')
        self.claim(confirmed,'occupation','amateur_performer')
        fc2_id, fc2_only = self.entity('FC2 Cast',kind='performer')
        self.connection.execute("UPDATE asset SET code='FC2-PPV-1234567' WHERE id=?",(fc2_id,))
        self.work('FC2 Cast',tag='素人')
        self.work('FC2 Cast',disposal='vanished')
        mixed_id, _ = self.entity('Mixed Cast',kind='performer')
        self.connection.execute("UPDATE asset SET code='FC2-PPV-7654321' WHERE id=?",(mixed_id,))
        self.work('Mixed Cast','ABP-001')
        _, guessed = self.entity('Guessed Cast',kind='performer')
        classification.write_claim(self.connection,entity_id=guessed,facet='occupation',
            value='amateur_performer',source='script:search',evidence='未核验推断')
        jav_id, jav = self.entity('Known JAV Cast',kind='performer')
        self.connection.execute("UPDATE asset SET code='FC2-PPV-1111111' WHERE id=?",(jav_id,))
        self.claim(jav,'market','japanese_av')
        self.connection.commit()
        contract=WebContract(self.db)
        page=q_index(contract,'performers',category='amateur')
        self.assertEqual(sorted(item['entity_id'] for item in page['items']),sorted([confirmed,fc2_only]))
        self.assertEqual(page['categories']['amateur'],2)
        self.assertEqual(q_entity(contract,{'kind':'performer','name':'Mixed Cast'})['identity_labels'],[])
        self.assertEqual(q_entity(contract,{'kind':'performer','name':'Guessed Cast'})['identity_labels'],[])
        self.assertEqual(q_entity(contract,{'kind':'performer','name':'Known JAV Cast'})['identity_labels'],['女优'])
        self.claim(confirmed,'occupation','adult_performer')
        self.claim(fc2_only,'market','western_adult')
        self.connection.commit()
        self.assertEqual(q_index(contract,'performers',category='amateur')['items'],[])

    def test_amateur_tags_from_the_vision_model_do_not_classify_a_performer(self):
        asset_id, styled = self.entity('Styled Cast',kind='performer')
        self.connection.execute("INSERT INTO asset_tag(asset_id,tag,confidence,source) VALUES(?,'素人',0.6,'vision_creator')",(asset_id,))
        for source in ('vision_creator','vision_creator_review'):
            upsert_asset_entity(self.connection,kind='tag',name='素人',asset_id=asset_id,role='tag',source=source)
        self.connection.commit()
        self.assertEqual(classification.summaries(self.connection,[styled])[styled]['identity_categories'],[])
        upsert_asset_entity(self.connection,kind='tag',name='素人',asset_id=asset_id,role='tag',source='name')
        self.connection.commit()
        self.assertEqual(classification.summaries(self.connection,[styled])[styled]['identity_categories'],['amateur'])

    def test_animation_author_appears_in_artist_directory_with_its_own_category(self):
        _, animator = self.entity('Animator')
        self.claim(animator,'occupation','animator')
        _, artist = self.entity('Artist')
        self.claim(artist,'occupation','artist')
        self.connection.commit()
        page = q_index(WebContract(self.db),'performers',category='animation')
        self.assertEqual([row['entity_id'] for row in page['items']],[animator])
        self.assertEqual(page['categories'],{'animation':1})
        self.assertEqual(q_index(WebContract(self.db),'creators')['items'],[])

    def test_real_accounts_share_artist_directory_and_keep_avatars_and_profile_routes(self):
        _, actor = self.entity('Actor', kind='performer')
        _, blogger = self.entity('Blogger')
        _, seller = self.entity('Seller')
        _, animator = self.entity('Animator')
        self.claim(seller,'account_role','seller')
        self.claim(animator,'occupation','animator')
        self.connection.commit()
        artists = q_index(WebContract(self.db),'performers')
        self.assertCountEqual([row['entity_id'] for row in artists['items']],[actor,blogger,animator])
        self.assertEqual({row['entity_id']:row['entity_kind'] for row in artists['items']},{actor:'performer',blogger:'creator',animator:'creator'})
        separate = q_index(WebContract(self.db),'creators')
        self.assertCountEqual([row['entity_id'] for row in separate['items']],[seller])

    def test_person_account_can_belong_to_blogger_and_seller_directories(self):
        _, person = self.entity('Person Account')
        self.claim(person,'identity','person')
        self.claim(person,'account_role','seller')
        self.connection.commit()
        contract = WebContract(self.db)
        self.assertEqual(q_index(contract,'performers')['items'][0]['entity_id'],person)
        self.assertEqual(q_index(contract,'creators')['items'][0]['entity_id'],person)
        self.assertEqual(q_entity(contract,{'kind':'creator','name':'Person Account'})['identity_labels'],['网黄博主','卖家'])

    def test_western_cast_occupation_does_not_assert_a_japanese_av_career(self):
        _, entity_id = self.entity('Western Performer',kind='performer')
        self.claim(entity_id,'occupation','adult_performer')
        self.claim(entity_id,'market','western_adult')
        self.connection.commit()
        profile = q_entity(WebContract(self.db),{'kind':'performer','name':'Western Performer'})
        self.assertEqual(profile['identity_labels'],['西方'])
        self.assertEqual(q_index(WebContract(self.db),'performers',category='japanese_av')['items'],[])

    def test_script_cannot_approve_or_supply_unvalidated_values(self):
        _,entity_id=self.entity('Known Person')
        for patch in [{'status':'approved'}, {'status':'observed','source_url':'http://example.test'}]:
            with self.assertRaises(ValueError):
                classification.write_claim(self.connection,entity_id=entity_id,facet='identity',value='person',source='script:lookup',evidence='推断',**patch)
        with self.assertRaises(ValueError):
            self.claim(entity_id,'occupation','unknown_profession')
        with self.assertRaisesRegex(ValueError,'代码判据'):
            classification.write_claim(self.connection,entity_id=entity_id,facet='identity',value='person',
                source='script:identity-research',source_url='https://publisher.test/profile',
                evidence='研究清单判断',status='observed',confidence=1)

    def test_only_code_sources_make_observed_claims_trusted(self):
        _,research=self.entity('Research Person',kind='performer')
        _,parsed=self.entity('Parsed Person',kind='performer')
        self.connection.execute("INSERT INTO entity_classification VALUES(?,'market','japanese_av',"
            "'script:identity-research','https://publisher.test/a','研究清单判断','observed',1,'t')",(research,))
        self.claim(parsed,'market','japanese_av')
        self.connection.commit()
        page=q_index(WebContract(self.db),'performers',category='japanese_av')
        self.assertEqual([row['entity_id'] for row in page['items']],[parsed])
        self.assertEqual(page['categories'],{'japanese_av':1})

    def test_research_observations_downgrade_to_candidates_and_code_facts_stay(self):
        _,research=self.entity('Research Person',kind='performer')
        _,parsed=self.entity('Parsed Person',kind='performer')
        self.connection.execute("INSERT INTO entity_classification VALUES(?,'market','japanese_av',"
            "'script:identity-research','https://publisher.test/a','研究清单判断','observed',0.8,'t')",(research,))
        self.connection.execute("INSERT INTO entity_identity_link VALUES(?,?,'same_person',"
            "'script:identity-research','https://publisher.test/a','研究清单判断','observed','t')",(research,parsed))
        self.claim(parsed,'market','japanese_av')
        plan=classification.untrusted_observed(self.connection)
        self.assertEqual([(row['entity_id'],row['source']) for row in plan['entity_classification']],
                         [(research,'script:identity-research')])
        self.assertEqual(len(plan['entity_identity_link']),1)
        self.assertEqual(classification.downgrade_untrusted_observed(self.connection),
                         {'entity_classification':1,'entity_identity_link':1})
        rows=self.connection.execute('SELECT entity_id,status,confidence,evidence FROM entity_classification ORDER BY entity_id').fetchall()
        self.assertEqual([tuple(row) for row in rows],
                         [(research,'candidate',0.8,'研究清单判断'),(parsed,'observed',1.0,'发行方资料')])
        self.assertEqual(classification.untrusted_observed(self.connection),
                         {'entity_classification':[],'entity_identity_link':[]})

    def test_name_wiki_work_list_through_the_research_list_lands_as_candidate_release(self):
        _,entity_id=self.entity('COSH こすっち')
        classification.write_claim(self.connection,entity_id=entity_id,facet='identity',value='unknown',
                                   source=research.SOURCE,evidence='公开身份来源未取得')
        keys=research.lookup_keys('COSH こすっち',['こすっち'])
        self.assertEqual(keys,['COSH こすっち','こすっち'])
        self.assertEqual(research.lookup_keys('secret_japan'),['secret_japan'])
        listed={SOUGOU+'%A4%B3%A4%B9%A4%C3%A4%C1':_work_table('八ッ橋さい子','本多由奈','北川ゆず')}
        finding=research.wiki_finding(entity_id,'COSH こすっち',keys,{'sougouwiki':_Pages(listed),'av_neme':_Pages()})
        with self.connection:
            research.apply(self.connection,research.plan(self.connection,[finding]))
        claims={(row['value'],row['status']) for row in self.connection.execute(
            "SELECT value,status FROM entity_classification WHERE entity_id=? AND facet='identity'",(entity_id,))}
        self.assertEqual([claim['status'] for claim in finding['claims']],['observed','rejected'])
        self.assertEqual(claims,{('release','candidate'),('unknown','rejected')})

    def test_name_wiki_lookup_records_pages_asked_and_leaves_failed_fetches_unfetched(self):
        missing=research.wiki_finding(7,'lucky',['lucky'],{'sougouwiki':_Pages(),'av_neme':_Pages()})
        self.assertEqual([(claim['value'],claim['status']) for claim in missing['claims']],[('unknown','candidate')])
        self.assertIn('sougouwiki「lucky」（页不存在）',missing['claims'][0]['evidence'])
        self.assertIn('av_neme「lucky」（页不存在）',missing['claims'][0]['evidence'])
        broken=_Pages(failing=[SOUGOU+'lucky'])
        self.assertIsNone(research.wiki_finding(7,'lucky',['lucky'],{'sougouwiki':broken,'av_neme':_Pages()}))
        few=_Pages({SOUGOU+'lucky':_work_table('八ッ橋さい子','本多由奈')})
        finding=research.wiki_finding(7,'lucky',['lucky'],{'sougouwiki':few,'av_neme':_Pages()})
        self.assertIn('sougouwiki「lucky」（不是作品一览）',finding['claims'][0]['evidence'])

    def test_cast_role_repair_and_restore_preserve_all_business_fields(self):
        asset_id,entity_id=self.entity('Known Person')
        self.connection.execute('UPDATE asset SET play_count=7,rating=4 WHERE id=?',(asset_id,));self.connection.commit()
        before=dict(self.connection.execute('SELECT * FROM asset WHERE id=?',(asset_id,)).fetchone())
        frozen=research.plan(self.connection,[self.finding('Known Person',entity_id)])
        self.assertEqual(len(frozen[0]['cast']),1)
        with self.connection:
            receipt=research.apply(self.connection,frozen)
        self.assertEqual(self.connection.execute('SELECT role FROM asset_entity WHERE asset_id=?',(asset_id,)).fetchone()[0],'performer')
        self.assertEqual(self.connection.execute('SELECT group_concat(DISTINCT status) FROM entity_classification').fetchone()[0],'candidate')
        self.assertEqual(self.connection.execute('SELECT group_concat(status) FROM entity_identity_link').fetchone()[0],'candidate')
        self.assertEqual(classification.related_identities(self.connection,entity_id),[])
        with self.connection:
            research.restore(self.connection,receipt)
        after=dict(self.connection.execute('SELECT * FROM asset WHERE id=?',(asset_id,)).fetchone())
        before.pop('mutation_revision');after.pop('mutation_revision')
        self.assertEqual(before,after)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM entity').fetchone()[0],1)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM entity_identity_link').fetchone()[0],0)
        self.assertEqual(self.connection.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_stale_plan_and_history_changes_block_repair_or_restore(self):
        asset_id,entity_id=self.entity('Known Person')
        frozen=research.plan(self.connection,[self.finding('Known Person',entity_id)])
        self.connection.execute('UPDATE asset SET creator=? WHERE id=?',('User choice',asset_id));self.connection.commit()
        with self.assertRaisesRegex(ValueError,'已过期'):
            with self.connection: research.apply(self.connection,frozen)
        frozen=research.plan(self.connection,[self.finding('Known Person',entity_id)])
        with self.connection: receipt=research.apply(self.connection,frozen)
        self.connection.execute('UPDATE asset SET play_count=9 WHERE id=?',(asset_id,));self.connection.commit()
        with self.assertRaisesRegex(ValueError,'后续改动'):
            with self.connection: research.restore(self.connection,receipt)

    def test_manual_creator_relation_is_not_reclassified_as_cast(self):
        asset_id,entity_id=self.entity('Known Person')
        self.connection.execute("UPDATE asset_entity SET source='user:manual' WHERE asset_id=?",(asset_id,));self.connection.commit()
        self.assertEqual(research.plan(self.connection,[self.finding('Known Person',entity_id)])[0]['cast'],[])

    def test_entity_merge_transfers_classification_with_foreign_keys_disabled(self):
        _,target=self.entity('Known Person');_,source=self.entity('Old Name')
        self.claim(source,'occupation','model');self.connection.commit()
        with self.connection:
            merge_entity(self.connection,target_id=target,source_id=source,source_name='Old Name',alias_source='user:review')
        self.assertEqual(classification.classifications(self.connection,[target])[target][0]['label'],'模特')
        self.assertEqual(self.connection.execute('PRAGMA foreign_key_check').fetchall(),[])

    def part(self, number, code='FC2-PPV-3312576', disposal=None):
        name=f'{code}-{number}.mp4'
        cursor=self.connection.execute(
            "INSERT INTO asset(location,path,name,medium,code,size,duration,disposal) "
            "VALUES('local',?,?,'video',?,100,600,?)", (f'B:\\番号\\{code}\\{name}',name,code,disposal))
        for kind, label in [('performer','Cast Person'),('performer','Costar'),('studio','FC2 Studio'),('tag','Test Tag')]:
            upsert_asset_entity(self.connection,kind=kind,name=label,asset_id=cursor.lastrowid,role=kind,source='test:cast')
        self.connection.commit()
        return cursor.lastrowid

    def test_merge_deduplicates_identical_source_facts_and_keeps_latest_check_time(self):
        _, target = self.entity('Target'); _, source = self.entity('Source')
        for entity_id, checked in ((source, '2026-10-07T01:00:00+00:00'),
                                   (target, '2026-10-06T01:00:00+00:00')):
            classification.write_claim(self.connection, entity_id=entity_id, facet='identity',
                value='person', source='source:official', evidence='同一份署名',
                source_url='https://publisher.test/person', status='observed', confidence=1, checked_at=checked)
        self.connection.commit()
        with self.connection:
            merge_entity(self.connection, target_id=target, source_id=source,
                         source_name='Source', alias_source='user:review')
        rows = classification.classifications(self.connection, [target])[target]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['checked_at'], '2026-10-07T01:00:00+00:00')

    def test_merge_rejects_different_facts_from_the_same_source(self):
        _, target = self.entity('Target'); _, source = self.entity('Source')
        for entity_id, evidence in ((source, '记录甲'), (target, '记录乙')):
            classification.write_claim(self.connection, entity_id=entity_id, facet='identity',
                value='person', source='source:official', evidence=evidence, status='observed', confidence=1,
                source_url='https://publisher.test/person')
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, '来源冲突'), self.connection:
            merge_entity(self.connection, target_id=target, source_id=source,
                         source_name='Source', alias_source='user:review')
        self.assertEqual(self.connection.execute('SELECT count(*) FROM entity WHERE id IN (?,?)',
                                                (target, source)).fetchone()[0], 2)

    def test_fc2_collection_counts_once_in_profiles_rosters_tags_and_search(self):
        for number in range(1,20): self.part(number)
        contract=WebContract(self.db)
        profile=q_entity(contract,{'kind':'performer','name':'Cast Person'})
        self.assertEqual(profile['asset_count'],1)
        self.assertEqual(profile['related_performers'][0]['n'],1)
        self.assertEqual(profile['tags'][0]['n'],1)
        for kind in ('performers','studios','tags'):
            self.assertEqual(q_index(contract,kind)['items'][0]['n'],1)
        suggest=q_suggest(contract,'Cast Person',kinds=['performer'])
        self.assertEqual(suggest['groups'][0]['items'][0]['n'],1)
        page=q_items(contract,{'performer':'Cast Person','limit':'5'})
        self.assertEqual(page['work_total'],1)
        self.assertEqual(page['total'],19)
        self.assertEqual(len(page['items']),5)
        self.assertEqual(page['items'][0]['part_group']['count'],19)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM asset').fetchone()[0],19)

    def test_fc2_work_counts_keep_visibility_and_invalidate_with_ledger_changes(self):
        self.part(1);self.part(3,disposal='trash');self.part(4,disposal='vanished')
        contract=WebContract(self.db)
        self.assertEqual(q_entity(contract,{'kind':'performer','name':'Cast Person'})['asset_count'],1)
        self.part(1,code='FC2-PPV-7777777')
        self.assertEqual(q_entity(contract,{'kind':'performer','name':'Cast Person'})['asset_count'],2)
        self.assertEqual(q_items(contract,{'performer':'Cast Person'})['work_total'],2)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM asset_entity').fetchone()[0],16)

    def test_ambiguous_fc2_encodes_are_counted_as_individual_files(self):
        self.part(1);self.part(2)
        self.part('1-1080p')
        profile=q_entity(WebContract(self.db),{'kind':'performer','name':'Cast Person'})
        self.assertEqual(profile['asset_count'],3)
        self.assertEqual(q_items(WebContract(self.db),{'performer':'Cast Person'})['work_total'],3)

    def test_cross_role_merge_routes_only_explicitly_marked_aliases(self):
        _, target=self.entity('Known Person',kind='performer')
        _, source=self.entity('Old Account Name')
        with self.connection:
            merge_entity(self.connection,target_id=target,source_id=source,
                         source_name='Old Account Name',alias_source='user:identity-merge:creator')
        self.assertEqual(q_entity(WebContract(self.db),{'kind':'creator','name':'Old Account Name'}),
                         {'redirect':{'id':target,'kind':'performer','name':'Known Person'}})
        self.assertEqual(q_entity(WebContract(self.db),{'kind':'studio','name':'Known Person'}),{'error':'not found'})
        self.assertEqual(self.connection.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_matching_names_without_a_merge_are_distinct_identities(self):
        self.entity('Known Person',kind='performer')
        self.assertEqual(q_entity(WebContract(self.db),{'kind':'creator','name':'Known Person'}),{'error':'not found'})
