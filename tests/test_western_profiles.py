"""西方档案身份、单位转换、别名及链接边界。"""
import unittest
import json
import sqlite3
import tempfile
from pathlib import Path
from contextlib import closing

from peach.http import HttpResponse
from peach.western_profiles import profile, land
from peach.western_artwork import babepedia_portraits
from peach.performer_header import header, profiled
from tests.support.ledger import fresh_ledger


class WesternProfileTests(unittest.TestCase):
    def page(self, extra='', name='Christy White'):
        return f'<h1 id="babename">{name}</h1><h2 id="aka"><small>Also known as:</small>Christine White - Christina Andreadou<img id="aliasinfobtn"></h2><div id="personal-info-block">{extra}</div>'.encode()

    def cell(self, label, value):
        return f'<div class="info-item"><span class="label">{label}:</span><span class="value">{value}</span></div>'

    def test_units_dates_aliases_and_profile_links(self):
        body = self.page(''.join(self.cell(label,value) for label,value in (
            ('Born','Monday 17th of September 2001'), ('Height', '5\'6" (or 167 cm)'),
            ('Measurements','34–26–34 in (B–W–H)'), ('Bra/cup size','32B - JP: 70C'),
            ('Years active','2021-present'), ('Birthplace','Thessaloniki, Greece (#20)'))))
        body += b'<div id="socialicons"><a class="proficon x" href="https://x.com/Christy_whitee">X</a><a class="proficon www" href="https://linktr.ee/christywhite_">Web</a><a class="proficon x" href="https://localhost/">bad</a></div>'
        got = profile(lambda *a: HttpResponse(200,{},body),'Christy White')
        facts = got['profile']
        self.assertEqual(facts['birth_date'],'2001-09-17')
        self.assertEqual([facts[k] for k in ('height_cm','bust_cm','waist_cm','hip_cm')],[167,86,66,86])
        self.assertEqual(facts['cup'],'C')
        self.assertEqual(facts['debut_year'],2021)
        self.assertNotIn('active_until',facts)
        self.assertEqual(facts['birthplace'],'Thessaloniki, Greece')
        self.assertEqual(got['aliases'],['Christine White','Christina Andreadou'])
        self.assertEqual(len(got['links']),2)
        self.assertIn('Born',facts['raw'])

    def test_invalid_dates_and_unspecified_units_are_not_invented(self):
        body = self.page(self.cell('Born','31st of February 2000') + self.cell('Measurements','34-26-34')
                         + self.cell('Bra/cup size','32B') + self.cell('Years active','2015-2025'))
        facts = profile(lambda *a:HttpResponse(200,{},body),'Christy White')['profile']
        for key in ('birth_date','bust_cm','cup'):
            self.assertNotIn(key,facts)
        self.assertEqual(facts['active_until'],2025)

    def test_split_active_years_use_the_first_start_and_the_last_end(self):
        for raw, until in [('2014 - 2017, 2019 - 2021', 2021), ('2014 - 2017, 2019 - present', None)]:
            with self.subTest(raw=raw):
                body = self.page(self.cell('Years active', raw))
                facts = profile(lambda *a, body=body: HttpResponse(200, {}, body), 'Christy White')['profile']
                self.assertEqual(facts['debut_year'], 2014)
                self.assertEqual(facts.get('active_until'), until)

    def test_profile_links_that_disagree_with_held_links_are_reported_not_written(self):
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(fresh_ledger(Path(directory).resolve()))) as connection:
            entity_id = connection.execute("INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at) VALUES('performer','Christy White','christy white','t','t')").lastrowid
            connection.executemany("INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,created_at,updated_at) VALUES(?,?,?,?,?,'t','t')",
                                   [(entity_id, 'social', 'X', 'https://x.com/christy_a', 'x.com'),
                                    (entity_id, 'official', '官网', 'https://christy.example/', 'christy.example')])
            body = self.page() + (b'<div id="socialicons"><a class="proficon x" href="https://twitter.com/christy_b">X</a>'
                                  b'<a class="proficon www" href="https://linktr.ee/christy">Web</a>'
                                  b'<a class="proficon instagram" href="https://www.instagram.com/christy">IG</a></div>')
            record = profile(lambda *a: HttpResponse(200, {}, body), 'Christy White')
            with connection:
                result = land(connection, entity_id, 'Christy White', record, batch='auto:babepedia-profile@links')
            self.assertEqual([item['reason'] for item in result['link_conflicts']], ['X 已登记另一个账号', '已有官网'])
            self.assertEqual(len(result['added_links']), 1)
            self.assertEqual(connection.execute('SELECT count(*) FROM entity_link WHERE entity_id=?', (entity_id,)).fetchone()[0], 3)

    def test_one_failed_profile_keeps_the_others_in_the_plan(self):
        from types import SimpleNamespace
        from scripts import harvest_western_profiles as harvest

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = fresh_ledger(root)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.executemany("INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) VALUES(?,'performer',?,?,'t','t')",
                                       [(1, 'Christy White', 'christy white'), (2, 'Someone Else', 'someone else')])
            waits = []
            limiter = SimpleNamespace(wait=waits.append)
            http = lambda *a: HttpResponse(200, {}, self.page())
            http.close = lambda: None
            args = SimpleNamespace(db=path, entity=[2, 1, 3], profile_url=None, plan=root / 'plan.json', out=root / 'out.json')
            self.assertEqual(harvest.collect(args, http, limiter)['issues'], 2)
            records = json.loads((root / 'plan.json').read_text(encoding='utf8'))
            self.assertEqual([(record['entity_id'], 'issue' in record) for record in records], [(1, False), (2, True), (3, True)])
            self.assertEqual(len(waits), 2)

    def test_babepedia_alias_identity_is_required_for_all_profile_fields(self):
        with self.assertRaisesRegex(ValueError,'身份不一致'):
            profile(lambda *a:HttpResponse(200,{},self.page()),'Someone Else')

    def test_creator_profile_and_aliases_share_the_person_header(self):
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(fresh_ledger(Path(directory).resolve()))) as connection:
            entity_id = connection.execute("INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at) VALUES('creator','RiaKurumi','riakurumi','t','t')").lastrowid
            body = b'<h1 id="babename">Ria Kurumi</h1><h2 id="aka"><small>Also known as:</small>Kurumi Momota - Momo Ichinose - \xe7\x99\xbe\xe7\x94\xb0\xe3\x81\x8f\xe3\x82\x8b\xe3\x81\xbf</h2>'
            body += ('<div id="personal-info-block">' + self.cell('Height','149 cm') + '</div>').encode()
            record = profile(lambda *a:HttpResponse(200,{},body),'RiaKurumi')
            with connection:
                result = land(connection,entity_id,'RiaKurumi',record,batch='auto:babepedia-profile@creator')
            self.assertTrue(result['profile_written'])
            self.assertIn(entity_id,profiled(connection))
            facts = header(connection,entity_id,'RiaKurumi')
            self.assertEqual(facts['profile']['height'],149)
            self.assertIn('Kurumi Momota',[r[0] for r in connection.execute('SELECT alias FROM entity_alias WHERE entity_id=?',(entity_id,))])
            self.assertGreater(facts['name_groups']['total'],1)

    def test_single_latin_names_land_only_as_the_page_primary_name(self):
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(fresh_ledger(Path(directory).resolve()))) as connection:
            first = connection.execute("INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at) VALUES('performer','Lena Anderson','lena anderson','t','t')").lastrowid
            body = b'<h1 id="babename">Blaire Ivory</h1><h2 id="aka"><small>Also known as:</small>Lena Anderson - Lena - Mara - Mara Watson - Colett</h2>'
            record = profile(lambda *a: HttpResponse(200, {}, body), 'Lena Anderson')
            with connection:
                land(connection, first, 'Lena Anderson', record, batch='auto:babepedia-profile@single')
            self.assertEqual(sorted(r[0] for r in connection.execute('SELECT alias FROM entity_alias WHERE entity_id=?', (first,))),
                             ['Blaire Ivory', 'Mara Watson'])
            second = connection.execute("INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at) VALUES('performer','Kitten Doe','kitten doe','t','t')").lastrowid
            body = b'<h1 id="babename">Kittenx</h1><h2 id="aka"><small>Also known as:</small>Kitten Doe - Kitty</h2>'
            record = profile(lambda *a: HttpResponse(200, {}, body), 'Kitten Doe')
            with connection:
                land(connection, second, 'Kitten Doe', record, batch='auto:babepedia-profile@primary')
            self.assertEqual([r[0] for r in connection.execute('SELECT alias FROM entity_alias WHERE entity_id=?', (second,))], ['Kittenx'])

    def test_repair_drops_stored_single_latin_aliases_except_the_page_primary_name(self):
        from scripts import repair_western_single_aliases as repair

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = fresh_ledger(root)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) VALUES(1,'performer','Lena Anderson','lena anderson','t','t')")
                connection.executemany("INSERT INTO entity_alias(entity_id,alias,normalized_alias,source) VALUES(1,?,?,?)",
                                       [('Lena', 'lena', 'auto:babepedia-profile@1'), ('Mara Watson', 'mara watson', 'auto:babepedia-profile@1'),
                                        ('Kittenx', 'kittenx', 'auto:babepedia-profile@1'), ('Anna', 'anna', 'user:alias')])
            (root / 'plan.json').write_text(json.dumps([{'entity_id': 1, 'matched_name': 'Kittenx'}]), encoding='utf-8')
            common = ['--db', str(path), '--plan', str(root / 'plan.json'), '--output', str(root / 'receipt.json')]
            self.assertEqual(repair.main(common), 0)
            self.assertEqual(repair.main([*common, '--apply', '--backup', str(root / 'backup.db')]), 0)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(sorted(r[0] for r in connection.execute('SELECT alias FROM entity_alias')),
                                 ['Anna', 'Kittenx', 'Mara Watson'])

    def test_named_person_gallery_adds_candidates_and_excludes_other_people(self):
        body = self.page() + b'<div id="profbox2"><a class="img" href="/pics/Christy.jpg"></a></div><div class="useruploads2"><a class="img" href="/user-uploads/Christy.jpg"><img alt="Christy White"></a><a class="img" href="/user-uploads/Other.jpg"><img alt="Someone Else"></a></div><a class="img" href="/pics/related.jpg"><img alt="Christy White"></a>'
        found = babepedia_portraits(lambda *a:HttpResponse(200,{},body),'Christy White')
        self.assertEqual(len(found),2)
        self.assertEqual([p['automatic_install'] for p in found],[True,False])

    def test_profile_landing_preserves_existing_profile_and_links_and_alias_owners(self):
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(fresh_ledger(Path(directory).resolve()))) as connection:
            entity_id = connection.execute("INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at) VALUES('performer','Christy White','christy white','t','t')").lastrowid
            connection.execute("INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at) VALUES('performer','Christine White','christine white','t','t')")
            record = profile(lambda *a: HttpResponse(200,{},self.page(self.cell('Height','167 cm'))
                + b'<div id="socialicons"><a class="proficon x" href="https://x.com/christy">X</a></div>'),'Christy White')
            with connection:
                first = land(connection,entity_id,'Christy White',record,batch='auto:babepedia-profile@test')
            self.assertTrue(first['profile_written'])
            self.assertEqual(len(first['added_links']),1)
            metadata = json.loads(connection.execute('SELECT metadata_json FROM entity_link WHERE id=?',
                (first['added_links'][0],)).fetchone()[0])
            self.assertEqual((metadata['source'],metadata['batch']),
                             ('auto:babepedia-profile','auto:babepedia-profile@test'))
            self.assertEqual(connection.execute('SELECT count(*) FROM entity_alias WHERE entity_id=? AND normalized_alias=?',(entity_id,'christine white')).fetchone()[0],0)
            self.assertEqual(connection.execute('SELECT alias FROM entity_alias WHERE entity_id=?',(entity_id,)).fetchall(),[('Christina Andreadou',)])
            record['profile']['height_cm'] = 170
            with connection:
                second = land(connection,entity_id,'Christy White',record,batch='auto:babepedia-profile@test2')
            self.assertTrue(second['preserved_profile'])
            self.assertEqual(second['added_links'],[])
            self.assertEqual(connection.execute('SELECT height_cm FROM performer_profile WHERE entity_id=?',(entity_id,)).fetchone()[0],167)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(),[])
