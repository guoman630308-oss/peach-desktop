"""实体链接安装：已复核的结果必须能进库，不合法的必须留下拒绝理由。"""
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]

#: 停放页的最小形态，照 `crusegroup.net` 2026-09-26 实测的结构手写：200、标题
#: `Redirecting...`、正文一段把访客送去停放平台路由的脚本，响应头里带着平台主机名。
PARKED_PAGE = (b'<!doctype html><html><head><title>Redirecting...</title></head><body>'
               b'<script>fetch("https://router.parklogic.com/model/221")</script></body></html>')
PARKED_HEADERS = {"permissions-policy": 'ch-ua=(self "https://*.parklogic.com")'}


def load_module(name: str = "install_entity_links"):
    sys.path.insert(0, str(REPO / "src"))
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCHEMA = """
CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT);
CREATE TABLE entity_alias(entity_id INTEGER, alias TEXT);
CREATE TABLE entity_link(
  id INTEGER PRIMARY KEY,
  entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  link_kind TEXT NOT NULL CHECK(link_kind IN ('official','social','catalog','source_reference')),
  label TEXT NOT NULL, url TEXT NOT NULL, hostname TEXT,
  is_sensitive INTEGER NOT NULL DEFAULT 0 CHECK(is_sensitive IN (0,1)),
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(entity_id,url));
"""


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "ledger.db"
        self.connection = sqlite3.connect(self.db)
        self.connection.executescript(SCHEMA)
        self.connection.executemany(
            "INSERT INTO entity VALUES(?,?,?)",
            [(1, "studio", "MOODYZ"), (2, "studio", "Prestige"), (3, "performer", "立花美涼")])
        self.connection.execute("INSERT INTO entity_alias VALUES(?,?)", (2, "プレステージ"))
        self.connection.commit()

    def tearDown(self):
        self.connection.close()

    def row(self, **kw):
        base = {"entity_id": "", "kind": "studio", "name": "MOODYZ", "link_kind": "official",
                "label": "官方网站", "url": "https://moodyz.com/", "evidence": "标题自述厂牌名"}
        base.update(kw)
        return base

    def test_a_reviewed_link_reaches_the_ledger_with_its_provenance(self):
        """装进去的不只是 URL，还有它凭什么被采信。

        没有 provenance 的话，半年后没人能判断这条链接是人工看图确认的还是脚本猜的，
        于是整表都只能重查一遍——那等于这次复核白做。
        """
        planned = self.module.plan(self.connection, [self.row()])
        self.assertEqual([p["action"] for p in planned], ["insert"])
        self.assertEqual(self.module.install(self.connection, planned, "review.csv"), 1)

        stored = self.connection.execute(
            "SELECT entity_id,link_kind,label,url,hostname,metadata_json FROM entity_link"
        ).fetchone()
        self.assertEqual(stored[:5], (1, "official", "官方网站", "https://moodyz.com/", "moodyz.com"))
        metadata = json.loads(stored[5])
        self.assertEqual(metadata["source"], "review.csv")
        self.assertEqual(metadata["evidence"], "标题自述厂牌名")
        self.assertIn("installed_at", metadata)

    def test_running_twice_does_not_duplicate(self):
        """复核表会被反复重跑；第二遍必须是零写入而不是第二行。"""
        planned = self.module.plan(self.connection, [self.row()])
        self.module.install(self.connection, planned, "review.csv")
        again = self.module.plan(self.connection, [self.row()])
        self.assertEqual([p["action"] for p in again], ["skip"])
        self.assertIn("已存在", again[0]["reason"])
        self.assertEqual(self.module.install(self.connection, again, "review.csv"), 0)
        self.assertEqual(
            self.connection.execute("SELECT count(*) FROM entity_link").fetchone()[0], 1)

    def test_automatic_link_keeps_its_revert_batch(self):
        planned = self.module.plan(self.connection, [self.row()])
        self.module.install(self.connection, planned, 'auto:company-profile', batch='batch1')
        metadata = json.loads(self.connection.execute('SELECT metadata_json FROM entity_link').fetchone()[0])
        self.assertEqual((metadata['source'],metadata['batch']), ('auto:company-profile','batch1'))

    def test_a_label_edited_in_the_review_table_reaches_the_ledger_on_rerun(self):
        """label 是资料页上的链接文字，复核表是它的出处：表里改了字，重跑就对齐，不另建一行。"""
        self.module.install(self.connection, self.module.plan(self.connection, [self.row()]),
                            "review.csv")
        again = self.module.plan(self.connection, [self.row(label="官网存档（2022-05）")])
        self.assertEqual([p["action"] for p in again], ["relabel"])
        self.assertEqual(self.module.install(self.connection, again, "review.csv"), 0)
        self.assertEqual(self.module.relabel(self.connection, again), 1)
        self.assertEqual(self.connection.execute("SELECT label FROM entity_link").fetchall(),
                         [("官网存档（2022-05）",)])

    def test_a_url_without_scheme_does_not_become_a_second_row(self):
        """复核表是人和脚本混写的，`moodyz.com` 和 `https://moodyz.com/` 都会出现。

        不归一的话同一个站会绕过 UNIQUE(entity_id,url) 建出两行，资料页上就并排出现
        两条一模一样的链接。
        """
        self.module.install(self.connection, self.module.plan(self.connection, [self.row()]),
                            "review.csv")
        planned = self.module.plan(self.connection, [self.row(url="moodyz.com/")])
        self.assertEqual(planned[0]["action"], "skip")

    def test_the_old_host_of_a_renamed_site_does_not_become_a_second_row(self):
        """目录型来源抄的是 2023 年改名前的写法，写入端要把它收成现主机。

        `UNIQUE(entity_id,url)` 和「已存在，跳过」都只认字面，
        `twitter.com/<handle>` 与 `x.com/<handle>` 在它们眼里是两条链接。minnano-av
        一批 710 条里就有 170 条是这个形态，全写进去等于把 `normalize_link_hosts.py`
        清掉的那 295 条重新长回来，而资料页会并排显示两枚一样的 X 图标。
        """
        self.module.install(
            self.connection,
            self.module.plan(self.connection, [self.row(url="https://x.com/kouzaisaki")]),
            "review.csv")
        planned = self.module.plan(
            self.connection, [self.row(url="https://twitter.com/kouzaisaki")])
        self.assertEqual(planned[0]["action"], "skip")
        self.assertIn("已存在", planned[0]["reason"])
        self.assertEqual(
            self.connection.execute("SELECT count(*) FROM entity_link").fetchone()[0], 1)

    def test_a_renamed_host_is_stored_in_its_current_form(self):
        """收成现主机是写进库里，不只是拿来比对——否则库里仍是旧写法。"""
        self.module.install(
            self.connection,
            self.module.plan(self.connection, [self.row(url="https://twitter.com/kouzaisaki")]),
            "review.csv")
        stored = self.connection.execute("SELECT url FROM entity_link").fetchone()[0]
        self.assertEqual(stored, "https://x.com/kouzaisaki")

    def test_an_entity_can_be_matched_by_alias(self):
        planned = self.module.plan(self.connection, [self.row(name="プレステージ")])
        self.assertEqual(planned[0]["entity_id"], 2)
        self.assertEqual(planned[0]["reason"], "按别名")

    def test_an_unknown_entity_is_reported_not_silently_dropped(self):
        """静默丢行会让「装了 N 条」和「表里多了 N 条」对不上，而没人知道差在哪。"""
        planned = self.module.plan(self.connection, [self.row(name="不存在的厂牌")])
        self.assertEqual(planned[0]["action"], "skip")
        self.assertIn("找不到", planned[0]["reason"])

    def test_an_invalid_link_kind_is_refused_before_sqlite_raises(self):
        """表上有 CHECK 约束，但让它抛出来会中断整批。提前拦下才能继续装其余的。"""
        planned = self.module.plan(self.connection, [self.row(link_kind="blog")])
        self.assertEqual(planned[0]["action"], "skip")
        self.assertIn("link_kind", planned[0]["reason"])

    def test_a_non_http_url_is_refused(self):
        for bad in ("javascript:alert(1)", "ftp://example.com/"):
            planned = self.module.plan(self.connection, [self.row(url=bad)])
            self.assertEqual(planned[0]["action"], "skip", bad)
            self.assertIn("URL", planned[0]["reason"])

    def test_a_link_without_a_label_is_refused(self):
        """资料页拿 label 当链接文字；空 label 会渲染成一个点不到的空链接。"""
        planned = self.module.plan(self.connection, [self.row(label="  ")])
        self.assertEqual(planned[0]["action"], "skip")
        self.assertIn("label", planned[0]["reason"])

    def test_apply_without_backup_refuses_and_writes_nothing(self):
        """真实账本写入必须带备份；缺备份要在读输入之前就停。"""
        code = self.module.main(["--db", str(self.db), "--input", str(self.tmp / "x.csv"),
                                 "--apply"])
        self.assertEqual(code, 2)
        self.assertEqual(
            self.connection.execute("SELECT count(*) FROM entity_link").fetchone()[0], 0)

    def test_dry_run_is_the_default_and_touches_nothing(self):
        source = self.tmp / "review.csv"
        source.write_text(
            "entity_id,kind,name,link_kind,label,url,evidence\n"
            ",studio,MOODYZ,official,官方网站,https://moodyz.com/,标题自述\n",
            encoding="utf-8-sig")
        # `--no-check` 不只是提速：测试不许发网络请求，否则一次断网就变成红灯。
        self.assertEqual(self.module.main(
            ["--db", str(self.db), "--input", str(source), "--no-check"]), 0)
        self.assertEqual(
            self.connection.execute("SELECT count(*) FROM entity_link").fetchone()[0], 0)

    def test_a_link_that_does_not_open_never_reaches_the_ledger(self):
        """这条守的是一次真实事故。

        首批 703 条链接一条都没验就装进了账本，事后逐条测发现 289 条 official 里有
        107 条打不开（84 个 404、18 个 502），37% 是死的。上游给什么就存什么，等于把
        minnano-av 几年前的快照当成现在的事实——T-POWERS 改过站，`/official/talent/X`
        早已 404，而资料页上它看起来和好链接一模一样。

        门槛放在安装器而不是各个采集器里：这里是所有来源进入账本的唯一入口。
        """
        planned = self.module.plan(self.connection, [
            self.row(url="https://dead.example/gone"),
            self.row(name="Prestige", url="https://alive.example/"),
        ])
        self.assertEqual([p["action"] for p in planned], ["insert", "insert"])
        self.module.check_links(
            planned, probe=lambda url: (url == "https://alive.example/", "HTTP 404"))
        self.assertEqual([p["action"] for p in planned], ["skip", "insert"])
        self.assertIn("打不开", planned[0]["reason"])
        self.assertIn("404", planned[0]["reason"])
        self.assertEqual(self.module.install(self.connection, planned, "review.csv"), 1)

    def test_dead_links_already_in_the_ledger_are_found_for_pruning(self):
        """

        链接还会随时间烂掉——事务所改版、艺人解约、博客注销——所以这条路要留着复用，
        不是一次性的清理脚本。
        """
        for url in ("https://alive.example/", "https://dead.example/gone"):
            planned = self.module.plan(self.connection, [self.row(url=url)])
            self.module.install(self.connection, planned, "seed.csv")
        self.assertEqual(
            self.connection.execute("SELECT count(*) FROM entity_link").fetchone()[0], 2)

        dead = self.module.dead_links(
            self.connection, probe=lambda url: (url == "https://alive.example/", "HTTP 404"))
        self.assertEqual([d["url"] for d in dead], ["https://dead.example/gone"])
        self.assertEqual(dead[0]["entity"], "MOODYZ")
        self.assertEqual(dead[0]["note"], "HTTP 404")

    def test_only_a_gone_status_counts_as_proof_that_a_link_is_dead(self):
        """取不到不等于没了，这两件事只有 404／410 能划清。按「非 200 就删」会把这些好链接一起删掉。

        这和取证失败时写 `未取得` 而不是写结论是同一条规矩。
        """
        for note in ("HTTP 404", "HTTP 410"):
            self.assertTrue(self.module.is_gone(note), note)
        for note in ("HTTP 403", "HTTP 400", "HTTP 500", "HTTP 502",
                     "取不到：ConnectError", "取不到：ReadTimeout", "可打开"):
            self.assertFalse(self.module.is_gone(note), note)

    def test_the_reachability_gate_does_not_re_probe_rows_already_skipped(self):
        """已经因为别的原因跳过的行不必再请求一次——那是白费一次往返。"""
        planned = self.module.plan(self.connection, [self.row(name="不存在的厂牌")])
        self.assertEqual(planned[0]["action"], "skip")
        probed = []

        def probe(url):
            probed.append(url)
            return True, ""

        self.module.check_links(planned, probe=probe)
        self.assertEqual(probed, [])


class ResolvesTests(unittest.TestCase):
    """可达性探测：抖动要重试，状态码是一次成局。"""

    class Client:
        """`httpx.Client` 的替身，按剧本逐次抛错、返回状态码或返回整个响应。"""

        def __init__(self, script):
            self.script = script

        def __call__(self, **kwargs):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, headers=None):
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            status, body, response_headers = step if isinstance(step, tuple) else (step, b"", {})
            return httpx.Response(status, content=body, headers=response_headers,
                                  request=httpx.Request("GET", url))

    def setUp(self):
        # `time` 和 `httpx` 是进程共享的真模块，替身必须还回去：这一组用例先跑完，
        # 后面每个测试的 sleep 就都成了 list.append。
        self.module = load_module()
        self.slept = []
        for holder, name in ((self.module.time, "sleep"), (self.module.httpx, "Client")):
            self.addCleanup(setattr, holder, name, getattr(holder, name))
        self.module.time.sleep = self.slept.append

    def use(self, *script):
        self.module.httpx.Client = self.Client(list(script))

    def test_a_blip_on_the_first_try_is_not_a_conclusion(self):
        """这条出口的 TLS 大约三次断一次，一次失败就判死会把好站写成死链。"""
        self.use(ConnectionError("SSL: UNEXPECTED_EOF_WHILE_READING"), 200)
        self.assertEqual(self.module.resolves("https://eltra.jp/"), (True, "可打开"))
        self.assertEqual(len(self.slept), 1)

    def test_a_status_code_is_answered_at_once(self):
        """404 重试三次还是 404，多敲两次只是浪费对方的带宽。"""
        self.use(404, 200)
        self.assertEqual(self.module.resolves("https://x.jp/gone"), (False, "HTTP 404"))
        self.assertEqual(self.slept, [])

    def test_every_try_failing_keeps_the_reason(self):
        """全程取不到时说明必须留着：那不是「页面没了」，下一步是换个时间复查。"""
        self.use(*[ConnectionError("boom")] * 3)
        ok, note = self.module.resolves("https://down.jp/")
        self.assertFalse(ok)
        self.assertIn("ConnectionError", note)
        self.assertFalse(self.module.is_gone(note), "取不到不等于确证没了")

    def test_a_parked_domain_is_gone_even_though_it_answers_200(self):
        """事务所注销、域名被停放平台接走之后，页面照样 200，内容已不是这家公司。"""
        self.use((200, PARKED_PAGE, PARKED_HEADERS))
        ok, note = self.module.resolves("https://www.crusegroup.net/model/221")
        self.assertFalse(ok)
        self.assertIn("parklogic.com", note)
        self.assertTrue(self.module.is_gone(note))
        self.assertEqual(self.slept, [], "停放页是站点的回答，不是抖动，不必重试")

    def test_a_parked_domain_behind_an_invalid_certificate_is_gone(self):
        """停放平台没有原主的证书，HTTPS 当场失败；同一地址走 http 才看得见停放页。"""
        certificate = ConnectionError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        self.use(certificate, (200, PARKED_PAGE, PARKED_HEADERS))
        ok, note = self.module.resolves("https://crusegroup.net/model/316")
        self.assertFalse(ok)
        self.assertIn("证书", note)
        self.assertTrue(self.module.is_gone(note))

    def test_an_invalid_certificate_alone_is_not_proof(self):
        """证书链不全的真站也报同一个错；http 那一页不是停放页就只算取不到。"""
        certificate = ConnectionError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        self.use(*[certificate, (200, b"<title>T-POWERS</title>", {})] * 3)
        ok, note = self.module.resolves("https://www.t-powers.co.jp/")
        self.assertFalse(ok)
        self.assertFalse(self.module.is_gone(note))

    def test_an_ordinary_page_that_links_elsewhere_still_opens(self):
        self.use((200, b"<title>ARM</title><a href='https://x.com/arm_pro'>X</a>", {}))
        self.assertEqual(self.module.resolves("https://arm-p.com/models/"), (True, "可打开"))

    def test_pruning_can_be_limited_to_one_site(self):
        """清一家注销事务所的链接时，库里别的死链不跟着一起删。"""
        connection = sqlite3.connect(":memory:")
        connection.executescript(SCHEMA)
        connection.execute("INSERT INTO entity VALUES(1,'performer','山岸逢花')")
        connection.executemany(
            "INSERT INTO entity_link(entity_id,link_kind,label,url,created_at,updated_at) "
            "VALUES(1,'official',?,?,'2026-01-01','2026-01-01')",
            [("Cruse Group", "https://www.crusegroup.net/model/221"),
             ("Cruse Group", "https://crusegroup.net/"),
             ("别的事务所", "https://other.example/gone"),
             ("存档", "https://web.archive.org/web/2015/http://z-earth2.crusegroup.net/")])
        probed = []
        dead = self.module.dead_links(
            connection, host="crusegroup.net",
            probe=lambda url: probed.append(url) or (False, "HTTP 404"))
        self.assertEqual(probed, ["https://www.crusegroup.net/model/221",
                                  "https://crusegroup.net/"])
        self.assertEqual(len(dead), 2)

    def test_pruning_marks_a_retired_performers_link_and_deletes_the_rest(self):
        """已隐退女优的事务所页没了，留成失效标记；还在活动的人照旧删，已标记的不再去敲。"""
        tmp = Path(tempfile.mkdtemp())
        db = tmp / "ledger.db"
        with sqlite3.connect(db) as connection:
            connection.executescript(SCHEMA + "CREATE TABLE performer_profile("
                                     "entity_id INTEGER PRIMARY KEY, active_until INTEGER);")
            connection.executemany("INSERT INTO entity VALUES(?,'performer',?)",
                                   [(1, "东条苍"), (2, "神宫寺")])
            connection.execute("INSERT INTO performer_profile VALUES(1, 2021)")
            connection.executemany(
                "INSERT INTO entity_link(id,entity_id,link_kind,label,url,created_at,updated_at) "
                "VALUES(?,?,'official','Cruse Group',?,'2026-01-01','2026-01-01')",
                [(1, 1, "https://crusegroup.net/model/316"),
                 (2, 2, "https://www.crusegroup.net/model/295")])
        probed = []
        self.module.resolves = lambda url: probed.append(url) or (False, "HTTP 404")
        code = self.module.main(["--db", str(db), "--prune-dead", "--host", "crusegroup.net",
                                 "--apply", "--backup", str(tmp / "backup.db")])
        self.assertEqual(code, 0)
        with sqlite3.connect(db) as connection:
            rows = connection.execute("SELECT id, metadata_json FROM entity_link").fetchall()
        self.assertEqual([row[0] for row in rows], [1])
        self.assertEqual(json.loads(rows[0][1])["gone"]["note"], "HTTP 404")

        probed.clear()
        self.assertEqual(self.module.main(["--db", str(db), "--prune-dead"]), 0)
        self.assertEqual(probed, [], "已标记失效的链接不再重验")

        with sqlite3.connect(db) as connection:
            connection.executemany(
                "INSERT INTO entity_link(entity_id,link_kind,label,url,created_at,updated_at) "
                "VALUES(?,?,'x',?,'2026-01-01','2026-01-01')",
                [(1, "official", "https://hanaya-project.co.jp/model/aoi/"),
                 (1, "social", "https://x.com/aoi"), (2, "official", "https://arm-p.com/models/")])
        self.assertEqual(self.module.main(["--db", str(db), "--prune-dead", "--retired"]), 0)
        self.assertEqual(probed, ["https://hanaya-project.co.jp/model/aoi/"],
                         "--retired 只查已隐退女优的官网链接")


class NormalizeHostsTests(unittest.TestCase):
    """twitter.com → x.com 的主机归一：改写要保真，撞上已有的新写法要删而不是炸。

    2026-09-02 实测账本：295 条 `twitter.com` + 1 条 `www.twitter.com`，67 条 `x.com`，
    其中 6 个实体两种写法都有。这些用例就是那张计划表的缩影。
    """

    def setUp(self):
        self.module = load_module("normalize_link_hosts")
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "ledger.db"
        self.connection = sqlite3.connect(self.db)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.executemany(
            "INSERT INTO entity VALUES(?,?,?)",
            [(1, "performer", "立花美涼"), (2, "performer", "河北彩花")])
        self.connection.commit()

    def tearDown(self):
        self.connection.close()

    def link(self, entity_id, url, label="X @h", metadata="{}"):
        host = url.split("/")[2]
        self.connection.execute(
            "INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,metadata_json,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (entity_id, "social", label, url, host, metadata, "2026-01-01", "2026-01-01"))
        self.connection.commit()
        return self.connection.execute("SELECT last_insert_rowid()").fetchone()[0]

    def count(self):
        return self.connection.execute("SELECT count(*) FROM entity_link").fetchone()[0]

    def test_only_listed_hosts_are_touched_and_the_path_is_kept(self):
        """只换主机名，handle 大小写、查询和片段都是用户复核过的，原样留着。"""
        self.assertEqual(self.module.target_url("http://twitter.com/Abc_D?s=1#x"),
                         "https://x.com/Abc_D?s=1#x")
        self.assertEqual(self.module.target_url("https://mobile.twitter.com/abc"), "https://x.com/abc")
        for untouched in ("https://x.com/abc", "https://instagram.com/abc", "https://twitter.co.jp/a"):
            self.assertEqual(self.module.target_url(untouched), "", untouched)

    def test_a_rewrite_keeps_the_row_and_only_changes_url_and_hostname(self):
        """改写不是删了再插：id、label、metadata（provenance）全都要留在原行上。"""
        link_id = self.link(1, "https://twitter.com/tachibana", metadata='{"source":"a.csv"}')
        planned = self.module.plan(self.connection)
        self.assertEqual([(p["id"], p["action"]) for p in planned], [(link_id, "rewrite")])
        self.assertEqual(self.module.apply_plan(self.connection, planned), (1, 0))
        row = self.connection.execute(
            "SELECT id,label,url,hostname,metadata_json,created_at,updated_at FROM entity_link").fetchone()
        self.assertEqual(tuple(row)[:5], (link_id, "X @h", "https://x.com/tachibana", "x.com",
                                          '{"source":"a.csv"}'))
        self.assertEqual(row["created_at"], "2026-01-01")
        self.assertNotEqual(row["updated_at"], "2026-01-01")

    def test_an_entity_that_already_has_the_new_form_drops_the_old_row(self):
        """`UNIQUE(entity_id,url)` 不允许并存；留下的那行承载同一个 handle，计划表要点名它。"""
        keeper = self.link(1, "https://x.com/tachibana")
        old = self.link(1, "https://twitter.com/tachibana")
        planned = self.module.plan(self.connection)
        self.assertEqual([(p["id"], p["action"]) for p in planned], [(old, "drop")])
        self.assertIn(f"#{keeper}", planned[0]["reason"])
        self.assertEqual(self.module.apply_plan(self.connection, planned), (0, 1))
        self.assertEqual([r[0] for r in self.connection.execute("SELECT id FROM entity_link")], [keeper])

    def test_two_old_forms_of_one_handle_resolve_within_the_same_run(self):
        """同一实体 `twitter.com/a` 和 `mobile.twitter.com/a` 都映到一处：先改写的占位，后来的删。

        不记录本轮已占用的地址，第二条会规划成 rewrite，写入时撞 UNIQUE 让整批回滚。
        """
        first = self.link(1, "https://twitter.com/a")
        second = self.link(1, "https://mobile.twitter.com/a")
        planned = self.module.plan(self.connection)
        self.assertEqual([(p["id"], p["action"]) for p in planned],
                         [(first, "rewrite"), (second, "drop")])
        self.assertEqual(self.module.apply_plan(self.connection, planned), (1, 1))
        self.assertEqual(self.count(), 1)

    def test_the_same_handle_on_two_entities_is_not_a_collision(self):
        self.link(1, "https://twitter.com/shared")
        self.link(2, "https://x.com/shared")
        planned = self.module.plan(self.connection)
        self.assertEqual([p["action"] for p in planned], ["rewrite"])

    def test_a_second_run_finds_nothing_to_do(self):
        self.link(1, "https://twitter.com/a")
        self.link(2, "https://x.com/b")
        self.module.apply_plan(self.connection, self.module.plan(self.connection))
        self.connection.commit()
        self.assertEqual(self.module.plan(self.connection), [])

    def test_apply_without_backup_refuses_and_writes_nothing(self):
        self.link(1, "https://twitter.com/a")
        self.assertEqual(self.module.main(["--database", str(self.db), "--apply"]), 2)
        self.assertEqual(self.connection.execute(
            "SELECT url FROM entity_link").fetchone()[0], "https://twitter.com/a")

    def test_dry_run_is_the_default_and_touches_nothing(self):
        self.link(1, "https://twitter.com/a")
        self.assertEqual(self.module.main(["--database", str(self.db)]), 0)
        self.assertEqual(self.connection.execute(
            "SELECT url FROM entity_link").fetchone()[0], "https://twitter.com/a")

    def test_apply_with_backup_writes_and_the_backup_holds_the_old_state(self):
        self.link(1, "https://twitter.com/a")
        self.link(1, "https://x.com/b")
        self.link(1, "https://twitter.com/b")
        backup = self.tmp / "bak" / "ledger.db"
        self.assertEqual(self.module.main(
            ["--database", str(self.db), "--apply", "--backup", str(backup)]), 0)
        self.assertEqual(sorted(r[0] for r in self.connection.execute("SELECT url FROM entity_link")),
                         ["https://x.com/a", "https://x.com/b"])
        saved = sqlite3.connect(backup)
        self.assertEqual(saved.execute("SELECT count(*) FROM entity_link").fetchone()[0], 3)
        saved.close()


if __name__ == "__main__":
    unittest.main()
