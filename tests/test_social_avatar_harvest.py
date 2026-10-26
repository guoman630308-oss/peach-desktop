"""社媒存在感扩张：og:image 定主、名字闸门、链接分类、头像竞选。"""
import hashlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[1]


def load_module():
    sys.path.insert(0, str(REPO / "src"))
    spec = importlib.util.spec_from_file_location(
        "harvest_social_avatars", REPO / "scripts" / "harvest_social_avatars.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def jpeg(width=400, height=400, color=(200, 30, 30)):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "JPEG")
    return buffer.getvalue()


class CoverFallbackTests(unittest.TestCase):
    def test_single_performer_cover_is_used_and_multi_performer_cover_is_excluded(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / 'JBS-023.jpg').write_bytes(jpeg(1000, 700))
            con = sqlite3.connect(':memory:')
            con.executescript("CREATE TABLE asset(id,code,disposal);"
                              "CREATE TABLE asset_entity(asset_id,entity_id,role);"
                              "INSERT INTO asset VALUES(1,'JBS-023',NULL);"
                              "INSERT INTO asset_entity VALUES(1,5,'performer');")
            record = dict(kind='performer',entity_id=5,canonical='风见步')
            cache = module.AvatarCandidateCache(root/'cache')
            result = module.cover_fallback(con, record, cache, root)
            self.assertEqual(result['source_kind'], 'single_performer_cover')
            self.assertFalse(result['identity_verified'])
            self.assertEqual((result['width'], result['height']), (1000,700))
            con.execute("INSERT INTO asset_entity VALUES(1,6,'performer')")
            self.assertIsNone(module.cover_fallback(con, record, cache, root))
            con.close()

    def test_the_cover_with_the_bigger_face_wins_over_the_bigger_canvas(self):
        """JAV 封面普遍是双联版式，右半幅剧照里那张脸常常只有几十像素。挑封面
        和挑社媒头像用同一把尺：先看脸有多少像素。"""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            big = jpeg(1600, 1076, (10, 20, 30))
            small = jpeg(800, 538, (30, 20, 10))
            (root / 'AAA-001.jpg').write_bytes(big)
            (root / 'BBB-002.jpg').write_bytes(small)
            con = sqlite3.connect(':memory:')
            con.executescript("CREATE TABLE asset(id,code,disposal);"
                              "CREATE TABLE asset_entity(asset_id,entity_id,role);"
                              "INSERT INTO asset VALUES(1,'AAA-001',NULL);"
                              "INSERT INTO asset VALUES(2,'BBB-002',NULL);"
                              "INSERT INTO asset_entity VALUES(1,5,'performer');"
                              "INSERT INTO asset_entity VALUES(2,5,'performer');")
            record = dict(kind='performer', entity_id=5, canonical='风见步')
            cache = module.AvatarCandidateCache(root / 'cache')
            faces = {hashlib.sha256(big).hexdigest(): 40,
                     hashlib.sha256(small).hexdigest(): 220}

            def probe(path):
                payload = Path(path).read_bytes()
                width = 1600 if payload == big else 800
                digest = hashlib.sha256(payload).hexdigest()
                return {"ratio": 1.5, "px": [width, 1],
                        "face": {"cx": 0.5, "cy": 0.4, "w": faces[digest] / width,
                                 "h": 0.2, "score": 0.9}}

            result = module.cover_fallback(con, record, cache, root, probe=probe)
            self.assertEqual(result['external_id'], 'BBB-002')
            con.close()


X_BASE = "https://pbs.twimg.com/profile_images/2033362507679850496/4wvXoOFw"

X_PAGE = f"""
<html><head>
<meta property="og:title" content="釈アリス (@alice_710_) on X"/>
<meta property="og:image" content="{X_BASE}_200x200.jpg"/>
</head><body>
外部リンク：lit.link/shaku_alice
<img src="{X_BASE}_400x400.jpg"/><img src="{X_BASE}_normal.jpg"/>
<a href="https://x.com/intent/follow?screen_name=alice_710_">follow</a>
<a href="https://www.instagram.com/p/Cabc123/">post</a>
<a href="https://youtu.be/abc123/">video</a>
<a href="https://x.com/tos">利用規約</a>
<a href="https://x.com/privacy">隐私</a>
<a href="https://x.com/sitemap.xml">sitemap</a>
<a href="https://x.com/articles/20170514">article</a>
<a href="https://x.com/en/help/troubleshooting/how-twitter-ads-work.html">help</a>
</body></html>
"""

LIT_PAGE = """
<html><head><title>釈アリス lit.link</title></head><body>
<img src="https://prd.storage.lit.link/images/creators/91eec176-2db8-4484-bc85-6892cb9eeaa2/icons/3a347ccf-c2c3-40b8-84e9-8c2b5a5fa0c4.jpe"/>
<a href="https://twitter.com/alice_710_">X</a>
<a href="https://www.instagram.com/shaku._.alice/">Instagram</a>
<a href="https://www.tiktok.com/@shaku_alice">TikTok</a>
<a href="https://video.dmm.co.jp/av/list/?key=釈アリス">DMM</a>
<a href="https://www.mgstage.com/search/cSearch.php?actor[]=釈アリス&amp;type=top">MG</a>
<a href="https://lit.link/zh-tw/shaku_alice">self</a>
</body></html>
"""

LIT_ICON = ("https://prd.storage.lit.link/images/creators/"
            "91eec176-2db8-4484-bc85-6892cb9eeaa2/icons/"
            "3a347ccf-c2c3-40b8-84e9-8c2b5a5fa0c4.jpe")


class Response:
    def __init__(self, status, body, url):
        self.status, self.body, self.url = status, body, url


class FakeHttp:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, request, timeout, max_bytes):
        self.calls.append(request.url)
        handler = self.routes.get(request.url)
        if handler is None:
            return Response(404, b"", request.url)
        status, body = handler
        return Response(status, body, request.url)


class XPageTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_og_image_declares_the_own_avatar_and_the_size_suffix_is_stripped(self):
        """本人头像以 og:image 为准。

        登出页里混着大量 pbs.twimg.com 地址（嵌入模块、引用卡片），出现次数多
        也说明不了归属；X 自己在 og:image 里声明的才是本人。og:image 通常带
        _200x200 档位，先去扩展名再剥档位，才能去要原图。
        """
        parsed = self.module.parse_x_profile(X_PAGE)
        self.assertEqual(parsed["display_name"], "釈アリス (@alice_710_) on X")
        self.assertEqual(parsed["avatars"][0], f"{X_BASE}.jpg")
        self.assertEqual(parsed["avatars"][1], f"{X_BASE}_400x400.jpg")

    def test_without_og_image_an_ambiguous_page_yields_nothing(self):
        """推荐关注里全是别人的头像；基名不止一个就不猜。"""
        body = (f'<img src="{X_BASE}_400x400.jpg"/>'
                '<img src="https://pbs.twimg.com/profile_images/9/other_400x400.jpg"/>')
        parsed = self.module.parse_x_profile(body)
        self.assertEqual(parsed["avatars"], [])

    def test_bio_links_are_exact_host_matches_only(self):
        """`twitter.com` 是 `ads-twitter.com` 的后缀；松一档广告域就成了她的主页。

        同理功能页（/intent、/p/<帖子>、youtu.be 视频页）与 X 页脚（/tos、
        /privacy、/sitemap.xml）都是平台自己的地址，不是这个人的社交链接。
        """
        parsed = self.module.parse_x_profile(X_PAGE)
        urls = parsed["bio_urls"]
        self.assertEqual(
            [u for u in urls if u.startswith("https://x.com")], [],
            "X 域内只允许 handle 形路径，页脚与功能页必须全部拦下")
        self.assertIn("https://lit.link/shaku_alice", urls)
        self.assertNotIn("https://www.instagram.com/p/Cabc123/", urls)
        self.assertNotIn("https://youtu.be/abc123/", urls)

    def test_a_scheme_less_lit_link_in_bio_text_is_still_found(self):
        """X 简介里的链接常以裸域名文本渲染（没有 https://），照找不误。"""
        parsed = self.module.parse_x_profile("简介：lit.link/shaku_alice 是入口")
        self.assertEqual(parsed["bio_urls"], ["https://lit.link/shaku_alice"])


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_platforms_get_handle_labels_and_catalog_pages_stay_catalog(self):
        self.assertEqual(self.module.classify("https://www.instagram.com/shaku._.alice/"),
                         ("social", "Instagram @shaku._.alice"))
        self.assertEqual(self.module.classify("https://www.tiktok.com/@shaku_alice"),
                         ("social", "TikTok @shaku_alice"))
        self.assertEqual(self.module.classify("https://video.dmm.co.jp/av/list/?key=釈アリス"),
                         ("catalog", "DMM 检索"))
        self.assertEqual(self.module.classify("https://www.youtube.com/@shaku_alice"),
                         ("social", "YouTube @shaku_alice"))
        self.assertEqual(self.module.classify("https://lit.link/shaku_alice"),
                         ("social", "链接集（lit.link）"))
        self.assertEqual(self.module.classify("https://notx.com/alice"),
                         ("official", "官方网站"))


class IdentityGateTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_the_alias_hits_before_the_chinese_canonical(self):
        names = ["释爱丽丝", "釈アリス", "Alice Shaku"]
        self.assertEqual(
            self.module.identity_match(names, "釈アリス (@alice_710_) on X"), "釈アリス")

    def test_an_unrelated_page_matches_nothing(self):
        self.assertEqual(self.module.identity_match(["釈アリス"], "someone else"), "")


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    @staticmethod
    def candidate(width, height, sha, evidence="图", face_width=None,
                  source_kind="official_profile", provider="social-web"):
        row = {"provider": provider, "source_kind": source_kind,
               "source_url": "https://example.invalid/a.jpg", "external_id": "x",
               "width": width, "height": height, "mime_type": "image/jpeg",
               "sha256": sha, "object_path": Path("/tmp/x"), "matched": "釈アリス",
               "name_source": "social_identity_gate", "evidence": evidence}
        if face_width is not None:
            row["face_width"] = face_width
        return row

    def test_a_cover_loses_to_any_portrait_however_big_its_face_is(self):
        """作品封面差的是构图，不是清晰度，所以在比脸宽之前就先输一档。

        JAV 封面普遍是双联版式：左半幅整块留给标题和宣传文字，右半幅那张脸还常被
        角标和腰封压住。放进 64–160 px 的圆头像里，圆框中央经常是半行日文标题而不是
        这个人。像素再多也换不来一张能认人的脸。
        """
        cover = self.candidate(800, 538, hashlib.sha256(b"cover").hexdigest(),
                               face_width=240, source_kind="single_performer_cover",
                               provider="cover-fallback")
        portrait = self.candidate(400, 400, hashlib.sha256(b"x").hexdigest(),
                                  face_width=120)
        winner, runners_up = self.module.select_winner([cover, portrait])
        self.assertEqual(winner["width"], 400)
        self.assertEqual([row["width"] for row in runners_up], [800])

    def test_a_cover_still_beats_a_thumbnail(self):
        """封面只压一档：它上面是人像，下面是缩略图——同一张图被裁小的那一份，
        除了没有别的可用之外没有任何选它的理由。"""
        cover = self.candidate(800, 538, hashlib.sha256(b"c").hexdigest(),
                               face_width=40, source_kind="single_performer_cover")
        thumb = self.candidate(1200, 1200, hashlib.sha256(b"t").hexdigest(),
                               face_width=300, source_kind="thumbnail")
        winner, _ = self.module.select_winner([cover, thumb])
        self.assertEqual(winner["width"], 800)

    def test_covers_still_rank_against_each_other_by_face_width(self):
        """同一档之内，判据原样是脸的像素宽。"""
        small = self.candidate(1600, 1076, hashlib.sha256(b"a").hexdigest(),
                               face_width=60, source_kind="single_performer_cover")
        big = self.candidate(800, 538, hashlib.sha256(b"b").hexdigest(),
                             face_width=210, source_kind="single_performer_cover")
        winner, _ = self.module.select_winner([small, big])
        self.assertEqual(winner["width"], 800)

    def test_an_unknown_source_counts_as_a_portrait(self):
        """认不出来源就当人像档：434 张 gfriends 和 14 张 Stash 头像的 provenance
        里根本没有 provider 字段，把它们当封面会让 400×400 的社媒头像一路顶掉。"""
        self.assertEqual(self.module.source_tier({}), self.module.PORTRAIT_TIER)
        self.assertEqual(self.module.source_tier({"provider": "gfriends"}),
                         self.module.PORTRAIT_TIER)
        self.assertEqual(self.module.source_tier({"provider": "cover-fallback"}),
                         self.module.COVER_TIER)

    def test_the_bigger_canvas_loses_to_the_bigger_face(self):
        """`performer-8711` 的真实两张：640×960 的全身照画布更大，脸只有 67 px；
        540×810 的半身照画布小 15%，脸有 160 px。头像圆框里认不认得出人，看的是
        后者。"""
        standing = self.candidate(640, 960, hashlib.sha256(b"stand").hexdigest(),
                                  face_width=67)
        bust = self.candidate(540, 810, hashlib.sha256(b"bust").hexdigest(),
                              face_width=160)
        winner, runners_up = self.module.select_winner([standing, bust])
        self.assertEqual((winner["width"], winner["height"]), (540, 810))
        self.assertEqual([r["width"] for r in runners_up], [640])

    def test_a_face_that_was_not_detected_ranks_below_any_detected_face(self):
        """侧脸、低头、戴口罩都会检不出。检不出的记 0，让位给量得到的那张。"""
        huge = self.candidate(1500, 1500, hashlib.sha256(b"huge").hexdigest(),
                              face_width=0)
        small = self.candidate(400, 400, hashlib.sha256(b"small").hexdigest(),
                               face_width=90)
        winner, _ = self.module.select_winner([huge, small])
        self.assertEqual(winner["width"], 400)

    def test_a_batch_with_no_measurable_face_ranks_by_canvas(self):
        """模型缺席或整批都检不出脸时，脸宽全是 0，判据原样落回画布口径。"""
        rows = [self.candidate(500, 1500, hashlib.sha256(b"t").hexdigest(), face_width=0),
                self.candidate(600, 600, hashlib.sha256(b"s").hexdigest(), face_width=0)]
        winner, _ = self.module.select_winner(rows)
        self.assertEqual((winner["width"], winner["height"]), (600, 600))

    def test_the_same_image_is_measured_once_and_lands_in_the_review_csv(self):
        """脸宽是 CSV 的一列：复核的人据此判断这张脸够不够大。同图只检一次——
        一个人的 X、lit.link、Instagram 三处常常挂着同一张图。"""
        self.assertIn("face_width", self.module.CANDIDATE_FIELDS)
        sha = hashlib.sha256(b"one").hexdigest()
        rows = [self.candidate(400, 400, sha), self.candidate(400, 400, sha),
                self.candidate(400, 400, hashlib.sha256(b"two").hexdigest())]
        calls = []

        def fake(path):
            calls.append(path)
            return {"ratio": 1.0, "px": [400, 400],
                    "face": {"cx": 0.5, "cy": 0.4, "w": 0.22, "h": 0.2, "score": 0.9}}

        self.module.measure_faces(rows, fake)
        self.assertEqual(len(calls), 2)
        self.assertEqual([row["face_width"] for row in rows], [88, 88, 88])
        # 同一次检出既定判据也写 sidecar，页面读的脸框和挑图用的脸框不会来自两次检测。
        self.assertEqual([row["face_record"]["px"] for row in rows],
                         [[400, 400]] * 3)

    def test_same_image_on_two_platforms_collapses_and_merges_evidence(self):
        sha = hashlib.sha256(b"same").hexdigest()
        winner, runners = self.module.select_winner([
            self.candidate(400, 400, sha, "X 头像"),
            self.candidate(400, 400, sha, "lit.link 头像"),
        ])
        self.assertEqual(runners, [])
        self.assertIn("X 头像", winner["evidence"])
        self.assertIn("lit.link 头像", winner["evidence"])

    def test_the_shorter_side_outranks_the_longer_edge(self):
        """竖构图人像与方图头像比长边会偏袒长边；先比短边——500×1500 的长边
        顶到天，可用质量仍由 500 那条边说了算，600×600 赢。"""
        tall = self.candidate(500, 1500, hashlib.sha256(b"tall").hexdigest())
        square = self.candidate(600, 600, hashlib.sha256(b"sq").hexdigest())
        winner, _ = self.module.select_winner([tall, square])
        self.assertEqual((winner["width"], winner["height"]), (600, 600))

    def test_an_avatar_without_a_detected_face_is_not_installed_automatically(self):
        """检不出真人脸的一律退回复核，官方社媒头像也不例外。

        本人挂动漫头像的不在少数，而头像这一格要回答「这是谁」，画出来的脸回答
        不了。YuNet 只认真人脸，检不出就当不是真人；真人的侧脸、低头一起落到
        复核队列，那正是它存在的理由。
        """
        module = self.module
        self.assertTrue(module.shows_a_real_face({"face_width": 126}))
        self.assertFalse(module.shows_a_real_face({"face_width": 0}))
        self.assertFalse(module.shows_a_real_face({}))

    def test_the_auto_bar_needs_original_size_squares_or_portrait_baselines(self):
        module = self.module
        self.assertTrue(module.passes_auto_bar(400, 400))
        self.assertTrue(module.passes_auto_bar(740, 1110))
        self.assertTrue(module.passes_auto_bar(648, 800))
        self.assertFalse(module.passes_auto_bar(320, 320))
        self.assertFalse(module.passes_auto_bar(200, 200))


class InstallGateTests(unittest.TestCase):
    """`--force` 的安全带：只有脸更大才覆盖盘上那张。"""

    def setUp(self):
        self.module = load_module()

    @staticmethod
    def record(width, height, face_w):
        return {"ratio": round(width / height, 3), "px": [width, height],
                "face": {"cx": 0.5, "cy": 0.4, "w": face_w / width, "h": 0.2,
                         "score": 0.9}}

    def install(self, directory, name, payload, record):
        (directory / name).write_bytes(payload)
        if record is not None:
            (directory / name).with_suffix(".face.json").write_text(
                json.dumps(record), encoding="utf-8")

    def winner(self, directory, payload, face_record):
        cached = directory / "cached.jpg"
        cached.write_bytes(payload)
        return {"object_path": cached, "sha256": hashlib.sha256(payload).hexdigest(),
                "mime_type": "image/jpeg", "provider": "social-web",
                "source_url": "https://example.invalid/a.jpg", "external_id": "x",
                "width": 400, "height": 400, "face_record": face_record}

    def test_an_empty_slot_takes_anything_that_won(self):
        """缺头像时这道闸门是空操作。"""
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp).resolve() / "performer-1.img"
            self.assertIsNone(self.module.incumbent_row(missing, lambda p: None))
        self.assertTrue(self.module.outranks_incumbent(
            {"width": 400, "height": 400, "face_width": 10}, None))

    def test_a_portrait_replaces_an_installed_cover_even_with_a_smaller_face(self):
        """盘上那张的来源从 provenance 读回来，所以闸门也认得出它是张封面。

        闸门比的仍是同一把尺 `rank_key`，只是尺的第一格现在是来源档：装着的封面
        脸再大也在下一档，任何人像都能顶掉它。
        """
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            destination = directory / "performer-1.img"
            self.install(directory, "performer-1.img", b"cover",
                         self.record(800, 538, 240))
            Path(f"{destination}.provenance.json").write_text(
                json.dumps({"provider": "cover-fallback"}), encoding="utf-8")
            incumbent = self.module.incumbent_row(destination, lambda p: None)
        self.assertEqual(incumbent["provider"], "cover-fallback")
        portrait = {"width": 400, "height": 400, "face_width": 120,
                    "source_kind": "official_profile"}
        self.assertTrue(self.module.outranks_incumbent(portrait, incumbent))

    def test_an_installed_portrait_without_provenance_is_not_treated_as_a_cover(self):
        """没有 provenance 的按认不出处理，仍是人像档，脸更小的候选顶不掉它。"""
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            destination = directory / "performer-1.img"
            self.install(directory, "performer-1.img", b"portrait",
                         self.record(1000, 1500, 400))
            incumbent = self.module.incumbent_row(destination, lambda p: None)
        self.assertEqual(incumbent["provider"], "")
        self.assertFalse(self.module.outranks_incumbent(
            {"width": 400, "height": 400, "face_width": 120,
             "source_kind": "official_profile"}, incumbent))

    def test_a_smaller_face_never_overwrites_the_installed_one(self):
        """2026-09-06 实测：一趟 --force 换掉 350 张，282 张脸更小。本脚本的候选池
        （X 的 400×400、jae 的 320×500、作品封面）未必比别的管线装的强。"""
        winner = {"width": 400, "height": 400, "face_width": 137}
        incumbent = {"width": 2880, "height": 1800, "face_width": 786}
        self.assertFalse(self.module.outranks_incumbent(winner, incumbent))
        self.assertTrue(self.module.outranks_incumbent(incumbent, winner))

    def test_the_installed_face_comes_from_the_sidecar_instead_of_a_second_detection(self):
        """525 张各检一遍要好几分钟，而 sidecar 本来就是同一次检出的结果。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.install(root, "performer-1.img", jpeg(640, 960),
                         self.record(640, 960, 300))
            calls = []
            row = self.module.incumbent_row(root / "performer-1.img",
                                            lambda path: calls.append(path))
            self.assertEqual(calls, [])
            self.assertEqual((row["width"], row["height"], row["face_width"]),
                             (640, 960, 300))

    def test_an_installed_image_without_a_sidecar_is_detected_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.install(root, "performer-1.img", jpeg(640, 960), None)
            row = self.module.incumbent_row(
                root / "performer-1.img", lambda path: self.record(640, 960, 210))
            self.assertEqual(row["face_width"], 210)

    def test_installing_replaces_the_sidecar_so_the_page_never_frames_the_old_face(self):
        """留着上一张图的脸框，页面会拿它给这一张取景、放大到一个空位置上，而这在
        界面上与「这张图本来就该这么显示」看不出区别。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.install(root, "performer-1.img", jpeg(640, 960),
                         self.record(640, 960, 300))
            self.module.install_avatar(root, "performer", 1, self.winner(
                root, jpeg(400, 400, (9, 9, 9)), self.record(400, 400, 120)))
            written = json.loads(
                (root / "performer-1.face.json").read_text(encoding="utf-8"))
            self.assertEqual(written["px"], [400, 400])

    def test_a_winner_with_no_face_record_drops_the_stale_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.install(root, "performer-1.img", jpeg(640, 960),
                         self.record(640, 960, 300))
            self.module.install_avatar(root, "performer", 1, self.winner(
                root, jpeg(400, 400, (9, 9, 9)), None))
            self.assertFalse((root / "performer-1.face.json").exists())

    def test_cover_candidates_cannot_overwrite_an_installed_portrait(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            original = jpeg(544, 724)
            self.install(root, "performer-1.img", original, self.record(544, 724, 234))
            candidate = self.winner(root, jpeg(2184, 1468), self.record(2184, 1468, 234))
            candidate["provider"] = "cover-fallback"
            with self.assertRaisesRegex(ValueError, "作品封面"):
                self.module.install_avatar(root, "performer", 1, candidate)
            self.assertEqual((root / "performer-1.img").read_bytes(), original)
            self.assertEqual(json.loads((root / "performer-1.face.json").read_text())["px"], [544, 724])

    def test_a_missing_face_model_records_the_reason_instead_of_raising(self):
        """下不到 ONNX 不该把「今天没网」变成「所有人都没有头像」。"""
        from peach.avatar_face import FaceProbe
        probe = FaceProbe()
        with unittest.mock.patch("peach.avatar_face.FaceDetector",
                                 side_effect=RuntimeError("模型未取得")):
            self.assertIsNone(probe(Path("/tmp/nope.jpg")))
        self.assertEqual(probe.unavailable, "模型未取得")
        # 记住之后不再反复重试，也不再抛。
        self.assertIsNone(probe(Path("/tmp/nope.jpg")))



class HarvestTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = Path(tempfile.mkdtemp())
        self.cache_root = self.tmp / "cache"
        self.caches = {
            "social": self.module.AvatarCandidateCache(self.cache_root / "social"),
            "babepedia": self.module.AvatarCandidateCache(self.cache_root / "babepedia"),
            "jae": self.module.AvatarCandidateCache(self.cache_root / "jae"),
        }
        self.limiter = self.module.HostLimiter({"x.com": 0.0, "lit.link": 0.0,
                                                "jae.tokyo": 0.0})

    @staticmethod
    def record(routes):
        return {"entity_id": 8549, "kind": "performer", "canonical": "释爱丽丝",
                "names": ["释爱丽丝", "釈アリス", "しゃくありす"], "routes": routes}

    def test_verified_chain_yields_gated_links_and_a_matching_avatar(self):
        routes = {"x": "alice_710_"}
        avatar_bytes = jpeg(400, 400)
        http = FakeHttp({
            f"https://x.com/{routes['x']}": (200, X_PAGE.encode("utf-8")),
            f"{X_BASE}.jpg": (404, b""),
            f"{X_BASE}_400x400.jpg": (200, avatar_bytes),
            "https://lit.link/shaku_alice": (200, LIT_PAGE.encode("utf-8")),
            LIT_ICON: (200, avatar_bytes),
        })
        result = self.module.harvest_entity(self.record(routes), http, self.limiter,
                                            5.0, self.caches)
        labels = {link["label"] for link in result["links"]}
        self.assertIn("链接集（lit.link）", labels)
        self.assertIn("Instagram @shaku._.alice", labels)
        self.assertIn("TikTok @shaku_alice", labels)
        self.assertIn("DMM 检索", labels)
        self.assertTrue(all(link["gated"] for link in result["links"]),
                        "两跳名字闸门都过了，全部链接都该是自动档")
        self.assertEqual(len(result["candidates"]), 2,
                         "X 与 lit.link 各产一张原始候选；同图去重发生在竞选")
        winner, runners_up = self.module.select_winner(result["candidates"])
        self.assertEqual(runners_up, [], "两张同图，合并后只剩赢家")
        self.assertEqual(winner["sha256"], hashlib.sha256(avatar_bytes).hexdigest())
        self.assertIn("X @alice_710_ 头像", winner["evidence"])
        self.assertIn("lit.link 头像", winner["evidence"])
        self.assertIn("同图", winner["evidence"])

    def test_the_original_image_is_tried_before_the_400_tier(self):
        routes = {"x": "alice_710_"}
        original = jpeg(400, 400, color=(9, 9, 9))
        http = FakeHttp({
            f"https://x.com/{routes['x']}": (200, X_PAGE.encode("utf-8")),
            f"{X_BASE}.jpg": (200, original),
            f"{X_BASE}_400x400.jpg": (200, jpeg(400, 400, color=(1, 1, 1))),
        })
        result = self.module.harvest_entity(self.record(routes), http, self.limiter,
                                            5.0, self.caches)
        winner, _ = self.module.select_winner(result["candidates"])
        self.assertEqual(winner["sha256"], hashlib.sha256(original).hexdigest())

    def test_a_name_mismatch_sends_links_to_review_not_to_the_ledger(self):
        """闸门断掉的链路只进复核；错误归属的链接比没有链接更糟。"""
        routes = {"x": "alice_710_"}
        page = X_PAGE.replace("釈アリス", "別人の人")
        http = FakeHttp({
            f"https://x.com/{routes['x']}": (200, page.encode("utf-8")),
            f"{X_BASE}_400x400.jpg": (200, jpeg(400, 400)),
            "https://lit.link/shaku_alice": (200, page.replace(
                "別人の人 lit.link", "別人の人 lit.link").encode("utf-8")),
            LIT_ICON: (200, jpeg(400, 400, color=(30, 200, 30))),
        })
        result = self.module.harvest_entity(self.record(routes), http, self.limiter,
                                            5.0, self.caches)
        self.assertTrue(result["links"])
        self.assertFalse(any(link["gated"] for link in result["links"]))
        winner, _ = self.module.select_winner(result["candidates"])
        self.assertEqual(winner.get("matched"), "", "名字没过闸，头像不得标已核实")

    def test_the_jae_route_takes_every_year_the_directory_listed_her(self):
        """三届各一张，一起进竞选；身份沿用名录那一步的判定，这里不重新猜人。"""
        big = jpeg(600, 900, color=(10, 90, 10))
        rows = [
            {"portrait_url": "http://www.jae.tokyo/jae2015/images/a.jpg",
             "page": "http://www.jae.tokyo/jae2015/actress.html#joyu038",
             "matched_name": "釈アリス", "verdict": "命中"},
            {"portrait_url": "http://www.jae.tokyo/jae2017/images/b.jpg",
             "page": "http://www.jae.tokyo/jae2017/actress/12.html",
             "matched_name": "釈アリス", "verdict": "命中"},
        ]
        http = FakeHttp({rows[0]["portrait_url"]: (200, jpeg(200, 300)),
                         rows[1]["portrait_url"]: (200, big)})
        result = self.module.harvest_entity(self.record({"jae": rows}), http,
                                            self.limiter, 5.0, self.caches)
        self.assertEqual([c["provider"] for c in result["candidates"]], ["jae", "jae"])
        winner, runners_up = self.module.select_winner(result["candidates"])
        self.assertEqual(winner["sha256"], hashlib.sha256(big).hexdigest())
        self.assertEqual(len(runners_up), 1)
        self.assertTrue(winner["identity_verified"])
        self.assertEqual(winner["matched"], "釈アリス")
        self.assertEqual(winner["external_id"], "jae:12.html")
        self.assertIn("jae2017/actress/12.html", winner["evidence"])

    def test_a_jae_portrait_that_does_not_load_only_leaves_a_note(self):
        rows = [{"portrait_url": "http://www.jae.tokyo/jae2014/images/gone.jpg",
                 "page": "http://www.jae.tokyo/jae2014/performer/", "verdict": "命中"}]
        result = self.module.harvest_entity(self.record({"jae": rows}), FakeHttp({}),
                                            self.limiter, 5.0, self.caches)
        self.assertEqual(result["candidates"], [])
        self.assertTrue(any("jae 名录人像未取得" in note for note in result["notes"]))


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = Path(tempfile.mkdtemp())
        # 测试里绝不能摸到真实 Peach 数据目录：babepedia 与 jae 候选表都指到临时目录。
        self.module.GENERATED_DIR = self.tmp
        self.module.REVIEW_DIR = self.tmp
        self.avatars = self.tmp / "avatars"
        self.avatars.mkdir()

    def ledger(self, rows, links=(), aliases=()):
        db = self.tmp / "ledger.db"
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT, "
                           "canonical_name TEXT, metadata_json TEXT DEFAULT '{}')")
        connection.execute("CREATE TABLE entity_alias(entity_id INTEGER, alias TEXT, "
                           "confidence REAL DEFAULT 1)")
        connection.execute("CREATE TABLE entity_link(entity_id INTEGER, url TEXT, "
                           "hostname TEXT)")
        for entity_id, kind, name in rows:
            connection.execute("INSERT INTO entity VALUES(?,?,?,'{}')",
                               (entity_id, kind, name))
        connection.executemany("INSERT INTO entity_alias VALUES(?,?,1)", aliases)
        connection.executemany("INSERT INTO entity_link VALUES(?,?,?)", links)
        connection.commit()
        connection.close()
        return sqlite3.connect(f"file:{db}?mode=ro", uri=True)

    def test_missing_avatar_with_x_link_is_selected_and_installed_ones_skip(self):
        connection = self.ledger(
            [(1, "performer", "释爱丽丝"), (2, "performer", "有头像")],
            links=[(1, "https://twitter.com/alice_710_", "twitter.com"),
                   (2, "https://twitter.com/other_user", "twitter.com")])
        try:
            (self.avatars / "performer-2.img").write_bytes(jpeg())
            targets = self.module.load_targets(connection, self.avatars, [], False)
            self.assertEqual([t["entity_id"] for t in targets], [1])
            self.assertEqual(targets[0]["routes"]["x"], "alice_710_")
        finally:
            connection.close()

    def test_a_creator_rides_the_babepedia_route_from_the_candidates_csv(self):
        (self.tmp / "babepedia-candidates.csv").write_text(
            "entity_id,creator,verdict,babepedia_name,portrait_url\n"
            "6705,SexySaffron,命中,Saffron Bacchus,"
            "https://www.babepedia.com/pics/Saffron%20Bacchus.jpg\n",
            encoding="utf-8-sig")
        connection = self.ledger([(6705, "creator", "SexySaffron")])
        try:
            targets = self.module.load_targets(connection, self.avatars, [], False)
            self.assertEqual(len(targets), 1)
            self.assertEqual(targets[0]["routes"]["babepedia"]["babepedia_name"],
                             "Saffron Bacchus")
        finally:
            connection.close()

    def test_merged_babepedia_candidates_bind_to_the_current_performer(self):
        (self.tmp / 'babepedia-candidates.csv').write_text(
            'entity_id,creator,verdict,babepedia_name,portrait_url\n'
            '10,Melody Marks,命中,Melody Marks,https://www.babepedia.com/pics/Melody.jpg\n', encoding='utf8')
        connection = self.ledger([(20, 'performer', 'Melody Marks')])
        connection.close()
        with sqlite3.connect(self.tmp / 'ledger.db') as writable:
            writable.executescript('CREATE TABLE entity_redirect(old_id,target_id);'
                                   'INSERT INTO entity_redirect VALUES(10,20);')
        connection = sqlite3.connect(f'file:{self.tmp / "ledger.db"}?mode=ro', uri=True)
        try:
            targets = self.module.load_targets(connection, self.avatars, [], False)
            self.assertEqual(targets[0]['entity_id'], 20)
            self.assertEqual(targets[0]['routes']['babepedia']['babepedia_name'], 'Melody Marks')
        finally:
            connection.close()

    def test_a_performer_rides_the_jae_route_and_keeps_every_year(self):
        (self.tmp / "jae-performer-links-portraits.csv").write_text(
            "entity_id,kind,name,matched_name,portrait_url,source,page,verdict\n"
            "1,performer,释爱丽丝,釈アリス,http://www.jae.tokyo/jae2015/images/a.jpg,"
            "jae,http://www.jae.tokyo/jae2015/actress.html#joyu038,命中\n"
            "1,performer,释爱丽丝,釈アリス,http://www.jae.tokyo/jae2017/images/b.jpg,"
            "jae,http://www.jae.tokyo/jae2017/actress/12.html,命中\n"
            "2,performer,另一位,,http://www.jae.tokyo/jae2017/images/c.jpg,"
            "jae,http://www.jae.tokyo/jae2017/actress/13.html,需人工消歧\n",
            encoding="utf-8-sig")
        connection = self.ledger([(1, "performer", "释爱丽丝"), (2, "performer", "另一位")])
        try:
            targets = self.module.load_targets(connection, self.avatars, [], False)
            self.assertEqual([t["entity_id"] for t in targets], [1],
                             "名字没定下来的行不进路线，头像装错人和链接装错人一样严重")
            self.assertEqual(len(targets[0]["routes"]["jae"]), 2)
        finally:
            connection.close()

    def test_writing_avatars_needs_apply_like_every_other_write_script(self):
        """缺省空跑。

        本仓库所有会写盘的脚本都是「空跑默认、--apply 才写」；头像更该如此——
        装错的是人脸，而人脸正是复核队列存在的理由。此前这里是反过来的
        （`--no-install` 退出式），跑一次就直接往 generated/avatars 里塞图。
        """
        parser = self.module.build_parser()
        options = {action.dest for action in parser._actions}
        self.assertIn("apply", options)
        self.assertNotIn("no_install", options, "写盘不能是退出式开关")
        self.assertFalse(parser.parse_args([]).apply, "缺省必须是空跑")
        self.assertTrue(parser.parse_args(["--apply"]).apply)
        source = (REPO / "scripts" / "harvest_social_avatars.py").read_text(
            encoding="utf-8")
        self.assertIn("elif not args.apply:", source)
        self.assertNotIn("args.no_install", source)

    def test_an_x_function_page_yields_no_handle(self):
        self.assertEqual(self.module.x_handle("https://twitter.com/alice_710_"),
                         "alice_710_")
        self.assertEqual(self.module.x_handle("https://x.com/intent/follow?user_id=1"),
                         "")


if __name__ == "__main__":
    unittest.main()
