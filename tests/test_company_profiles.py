"""公司事实的来源、日期精度、填空与撤回。"""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from peach import company_profiles
from peach.entities import upsert_asset_entity
from peach import web_contract
from support.ledger import fresh_ledger


class CompanyProfileTests(unittest.TestCase):
    def test_dates_keep_precision_and_reject_copyright_or_invalid_dates(self):
        for raw, expected in [('2002年5月', '2002-05'), ('1999年3月1日', '1999-03-01'), ('2016', '2016'), ('平成16年7月21日', '2004-07-21')]:
            self.assertEqual(company_profiles.date_text(raw), expected)
        for raw in ['© 2002', '20周年', '2025年2月30日', '2002年（ブランド発売）']:
            self.assertIsNone(company_profiles.date_text(raw))

    def test_official_tables_separate_company_and_brand_and_detect_conflicts(self):
        html = '<table><tr><th>設立</th><td>2002年5月</td></tr><tr><th>ブランド設立</th><td>2003年</td></tr><tr><th>主要取引先</th><td>WILL</td></tr></table><dl><dt>所在地</dt><dd>東京都中野区</dd></dl><a href="/company/">会社概要 Company</a>'
        parsed = company_profiles.extract(html, 'https://brand.example/')
        self.assertEqual(parsed['facts']['founded']['value'], '2002-05')
        self.assertEqual(parsed['facts']['launched']['value'], '2003')
        self.assertNotIn('parent', parsed['facts'])
        self.assertNotIn('country', parsed['facts'])
        self.assertEqual(parsed['company_pages'], ['https://brand.example/company/'])
        conflict = company_profiles.extract(html + '<dl><dt>設立</dt><dd>2001</dd></dl>', 'https://brand.example/')
        self.assertNotIn('founded', conflict['facts'])
        self.assertEqual(conflict['conflicts'][0]['field'], 'founded')

    def test_fill_protects_existing_facts_and_revert_preserves_metadata(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        connection = sqlite3.connect(fresh_ledger(Path(temporary.name).resolve()))
        self.addCleanup(connection.close)
        connection.execute("INSERT INTO entity(id,kind,canonical_name,normalized_name,metadata_json,created_at,updated_at) VALUES(1,'studio','Brand','brand','{\"provider\":\"catalog\"}','2026-10-07','2026-10-07')")
        fact = {'value': '2002', 'source_url': 'https://brand.example/company', 'evidence': '設立：2002年'}
        self.assertEqual(company_profiles.fill(connection, 1, {'founded': fact}, source='auto:company-profile', batch='batch1')['written'], ['founded'])
        conflict = company_profiles.fill(connection, 1, {'founded': {**fact, 'value': '2001'}}, source='auto:company-profile', batch='batch2')
        self.assertEqual(conflict, {'written': [], 'conflicts': ['founded']})
        self.assertEqual(company_profiles.planned_revert(connection, 'auto:company-profile', 'batch2'), [])
        rows = company_profiles.planned_revert(connection, 'auto:company-profile', 'batch1')
        self.assertEqual(company_profiles.revert(connection, rows), 1)
        self.assertEqual(json.loads(connection.execute('SELECT metadata_json FROM entity').fetchone()[0]), {'provider': 'catalog'})

    def test_group_company_tables_are_scoped_by_the_entity_names(self):
        html = '<table><tr><th>社名</th><td>ソフト・オン・デマンド株式会社</td></tr><tr><th>設立</th><td>1995年11月</td></tr></table><table><tr><th>社名</th><td>SODクリエイト株式会社</td></tr><tr><th>設立</th><td>1999年3月</td></tr></table>'
        result = company_profiles.extract_for_entity(html, 'https://group.example/about_us/info/', ['SOD Create', 'SODクリエイト'])
        self.assertEqual(result['facts']['founded']['value'], '1999-03')
        self.assertEqual(result['facts']['legal_name']['value'], 'SODクリエイト株式会社')
        unnamed = company_profiles.extract_for_entity(html, 'https://group.example/about_us/info/', ['SOD Create'])
        self.assertEqual(unnamed['facts'], {})

    def test_terms_operator_comes_from_the_page_sentence_and_listings_give_no_group(self):
        html = '<h1>Vixen Media Group</h1><p>OUR BRANDS Each studio is a world. Explore Vixen, Blacked, Blacked Raw, Tushy, and Deeper, all available on Vixen Plus</p>'
        self.assertEqual(company_profiles.page_facts(html, 'https://www.vixengroup.com/', ['Tushy'])['facts'], {})
        terms = '<p>These terms of service are entered into between you and General Media Systems, LLC (the Company).</p>'
        facts = company_profiles.page_facts(terms, 'https://www.anybrand.example/terms', ['Any'])['facts']
        self.assertEqual(facts['operator']['value'], 'General Media Systems, LLC')
        self.assertEqual(facts['operator']['evidence'], 'These terms of service are entered into between you and General Media Systems, LLC (')
        self.assertEqual(company_profiles.page_facts(terms, 'https://www.anybrand.example/news', ['Any'])['facts'], {})

    def test_group_statements_without_labels_are_not_observed(self):
        cases = [
            ('<p>SODグループ</p><p>SODクリエイト株式会社</p>', 'https://corporate.sod.co.jp/business/softondemand/', ['SOD Create']),
            ('<h2>メーカー一覧</h2><p>FALENO GROUP</p><p>素人CLOVER PROFILE</p>', 'https://falenogroup.com/makers/', ['素人CLOVER']),
            ('<dl><dt>2006</dt><dd>組織拡大の為、ティーパワーズ株式会社を設立</dd></dl>', 'https://www.t-powers.co.jp/company/', ['T-POWERS']),
        ]
        for html, url, names in cases:
            with self.subTest(url=url):
                self.assertEqual(company_profiles.page_facts(html, url, names)['facts'], {})

    def test_replay_reads_saved_pages_and_drops_cross_page_conflicts(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / 'a.html').write_text('<table><tr><th>設立</th><td>2002年5月</td></tr><tr><th>所在地</th><td>東京都</td></tr></table>', encoding='utf-8')
        (root / 'b.html').write_text('<dl><dt>設立</dt><dd>2001年</dd></dl>', encoding='utf-8')
        (root / 'c.html').write_text('<dl><dt>商号</dt><dd>株式会社ブランド</dd></dl>', encoding='utf-8')
        pages = [{'url': 'https://brand.example/', 'final_url': 'https://www.brand.example/company', 'http_status': 200, 'cache': str(root / 'a.html')},
                 {'url': 'https://brand.example/about', 'http_status': 404},
                 {'url': 'https://brand.example/x', 'final_url': 'https://brand.example/x', 'http_status': 200, 'cache': str(root / 'b.html')},
                 {'url': 'https://registry.example/brand', 'final_url': 'https://registry.example/brand', 'http_status': 200, 'cache': str(root / 'c.html')},
                 {'url': 'https://brand.example/y', 'final_url': 'https://brand.example/y', 'cache': str(root / 'c.html')}]
        result = company_profiles.replay(pages, ['Brand'], {'brand.example'})
        self.assertEqual(list(result['facts']), ['location'])
        self.assertEqual(result['facts']['location']['source_url'], 'https://www.brand.example/company')

    def test_observed_fact_replaces_a_candidate_and_revert_lists_candidates(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        connection = sqlite3.connect(fresh_ledger(Path(temporary.name).resolve()))
        self.addCleanup(connection.close)
        held = {'company_profile': {
            'group': {'value': 'Group', 'source_url': 'https://brand.example/', 'status': 'candidate', 'source': 'auto:company-profile', 'batch': 'old'},
            'location': {'value': '東京都', 'source_url': 'https://brand.example/', 'status': 'candidate', 'source': 'auto:company-profile', 'batch': 'old'}}}
        connection.execute("INSERT INTO entity(id,kind,canonical_name,normalized_name,metadata_json,created_at,updated_at) VALUES(1,'studio','Brand','brand',?,'2026-10-07','2026-10-07')",
                           (json.dumps(held),))
        self.assertEqual(company_profiles.planned_revert(connection, 'auto:company-profile', 'old'),
                         [{'entity_id': 1, 'entity': 'Brand', 'fields': ['group', 'location']}])
        fact = {'value': '大阪府', 'source_url': 'https://brand.example/company', 'evidence': '所在地：大阪府', 'status': 'observed'}
        self.assertEqual(company_profiles.fill(connection, 1, {'location': fact}, source='auto:company-profile', batch='new')['written'], ['location'])
        profile = json.loads(connection.execute('SELECT metadata_json FROM entity').fetchone()[0])['company_profile']
        self.assertEqual(profile['location']['value'], '大阪府')
        self.assertEqual(list(company_profiles.public_profile({'company_profile': profile})), ['location'])

    def test_landing_writes_only_replayed_facts_and_ignores_listed_aliases_and_links(self):
        from scripts import harvest_company_profiles as harvest

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        path = fresh_ledger(root)
        with sqlite3.connect(path) as connection:
            connection.execute("INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) VALUES(1,'studio','Brand','brand','2026-10-07','2026-10-07')")
            connection.execute("INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,created_at,updated_at) VALUES(1,'official','Brand','https://brand.example/','brand.example','2026-10-07','2026-10-07')")
        connection.close()
        (root / 'page.html').write_text('<table><tr><th>設立</th><td>2002年5月</td></tr></table>', encoding='utf-8')
        listed = {'value': 'Brand Group', 'source_url': 'https://brand.example/', 'evidence': 'Brand Group', 'status': 'observed'}
        plan = {'entities': [{'entity_id': 1, 'name': 'Brand', 'status': '已核查',
                              'facts': {'founded': {**listed, 'value': '2002-05'}, 'group': listed},
                              'pages': [{'url': 'https://brand.example/company', 'final_url': 'https://brand.example/company', 'http_status': 200, 'cache': str(root / 'page.html')}],
                              'aliases': [{'value': 'ブランド', 'source_url': 'https://brand.example/', 'status': 'observed'}],
                              'links': [{'entity_id': 1, 'link_kind': 'catalog', 'label': 'Brand', 'url': 'https://catalog.example/brand', 'status': 'observed'}]}]}
        (root / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
        args = SimpleNamespace(input=root / 'plan.json', db=path, apply=True, backup=root / 'backup.db',
                               batch='auto:company-profile@test', output=root / 'receipt.json')
        self.assertEqual(harvest.land(args), 0)
        with closing(sqlite3.connect(path)) as connection:
            profile = json.loads(connection.execute('SELECT metadata_json FROM entity WHERE id=1').fetchone()[0])['company_profile']
            self.assertEqual(list(profile), ['founded'])
            self.assertEqual(profile['founded']['evidence'], '設立：2002年5月')
            self.assertEqual(connection.execute('SELECT count(*) FROM entity_alias').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT url FROM entity_link').fetchall(), [('https://brand.example/',)])
        change = json.loads((root / 'receipt.json').read_text(encoding='utf-8'))['changes'][0]
        self.assertEqual(change['not_replayed'], ['group'])
        self.assertEqual(change['ignored'], {'aliases': 1, 'links': 1})

    def test_repair_demotes_unreplayable_fields_and_drops_batch_aliases_and_links(self):
        from scripts import repair_company_profile_landing as repair

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        path = fresh_ledger(root)
        batch = 'auto:company-profile@old'
        stamp = {'source': 'auto:company-profile', 'batch': batch, 'status': 'observed', 'source_url': 'https://brand.example/company'}
        profile = {'founded': {**stamp, 'value': '2002-05', 'evidence': '設立：2002年5月'},
                   'group': {**stamp, 'value': 'Brand Group', 'evidence': 'Brand Group：Brand'},
                   'location': {'source': 'auto:company-profile', 'batch': 'other', 'status': 'observed', 'value': '東京都',
                                'source_url': 'https://brand.example/company'}}
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("INSERT INTO entity(id,kind,canonical_name,normalized_name,metadata_json,created_at,updated_at) VALUES(1,'studio','Brand','brand',?,'2026-10-07','2026-10-07')",
                               (json.dumps({'company_profile': profile}, ensure_ascii=False),))
            connection.execute("INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,created_at,updated_at) VALUES(1,'official','Brand','https://brand.example/','brand.example','2026-10-07','2026-10-07')")
            connection.execute("INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,metadata_json,created_at,updated_at) VALUES(1,'catalog','Brand','https://catalog.example/brand','catalog.example',?,'2026-10-07','2026-10-07')",
                               (json.dumps({'source': 'auto:company-profile', 'batch': batch}),))
            connection.execute("INSERT INTO entity_alias(entity_id,alias,normalized_alias,source) VALUES(1,'ブランド','ブランド',?)", (batch,))
        (root / 'page.html').write_text('<table><tr><th>設立</th><td>2002年5月</td></tr></table>', encoding='utf-8')
        plan = {'entities': [{'entity_id': 1, 'pages': [{'url': 'https://brand.example/company', 'final_url': 'https://brand.example/company',
                                                         'http_status': 200, 'cache': str(root / 'page.html')}]}]}
        (root / 'plan.json').write_text(json.dumps(plan), encoding='utf-8')
        common = ['--db', str(path), '--batch', batch, '--plan', str(root / 'plan.json'), '--output', str(root / 'receipt.json')]
        self.assertEqual(repair.main(common), 0)
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM entity_alias').fetchone()[0], 1)
        self.assertEqual(repair.main([*common, '--apply', '--backup', str(root / 'backup.db')]), 0)
        with closing(sqlite3.connect(path)) as connection:
            held = json.loads(connection.execute('SELECT metadata_json FROM entity').fetchone()[0])['company_profile']
            self.assertEqual({key: fact['status'] for key, fact in held.items()},
                             {'founded': 'observed', 'group': 'candidate', 'location': 'observed'})
            self.assertEqual(connection.execute('SELECT count(*) FROM entity_alias').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT url FROM entity_link').fetchall(), [('https://brand.example/',)])
        self.assertEqual(json.loads((root / 'receipt.json').read_text(encoding='utf-8'))['summary'],
                         {'keep': 1, 'evidence': 0, 'candidate': 1, 'aliases': 1, 'links': 1})

    def test_resume_retries_rate_limited_entities(self):
        from scripts import harvest_company_profiles as harvest

        self.assertTrue(harvest.settled({'status': '已核查', 'pages': [{'http_status': 200}]}))
        self.assertTrue(harvest.settled({'status': '未取得', 'reason': '没有可访问的已确认官网', 'pages': []}))
        self.assertFalse(harvest.settled({'status': '已核查', 'pages': [{'http_status': 200}, {'http_status': 429}]}))
        self.assertFalse(harvest.settled({'status': '未取得', 'pages': [{'http_status': 403}]}))
        self.assertFalse(harvest.settled({'status': '未取得', 'pages': [{'reason': 'ConnectTimeout'}]}))

    def test_public_profile_requires_public_source_and_known_nonempty_values(self):
        facts = {key: {'value': value, 'source_url': url} for key, value, url in [('country','日本','https://brand.example/company'), ('operator','secret','https://localhost/admin'), ('founded','','https://brand.example/'), ('telephone','123','https://brand.example/')]}
        self.assertEqual(list(company_profiles.public_profile({'company_profile': facts})), ['country'])

    def test_company_profile_survives_catalog_ingest(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        path = fresh_ledger(root)
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        connection.execute("INSERT INTO asset(id,location,path,name,medium) VALUES(1,'local','C:/test/a.mp4','a.mp4','video')")
        entity_id = upsert_asset_entity(connection, kind='studio', name='Brand', asset_id=1, role='studio', source='scan:filename')
        fact = {'value': '2002', 'source_url': 'https://brand.example/company'}
        company_profiles.fill(connection, entity_id, {'founded': fact}, source='auto:company-profile', batch='batch1')
        upsert_asset_entity(connection, kind='studio', name='Brand', asset_id=1, role='studio', source='scan:filename', metadata={'provider': 'fresh'})
        metadata = json.loads(connection.execute('SELECT metadata_json FROM entity WHERE id=?', (entity_id,)).fetchone()[0])
        self.assertEqual(metadata['provider'], 'fresh')
        self.assertEqual(metadata['company_profile']['founded']['value'], '2002')
        connection.commit()
        contract = web_contract.WebContract(path, avatar_root=root / 'avatars', logo_root=root / 'logos', cover_root=root / 'covers')
        response = web_contract.q_entity(contract, {'kind': 'studio', 'name': 'Brand'})
        self.assertEqual(response['company_profile']['founded']['value'], '2002')


if __name__ == '__main__':
    unittest.main()
