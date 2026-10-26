"""厂牌官网采集：候选是假设，判定才是证据。"""
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load_module():
    """判据模块的一份独立副本：各条测试往它身上打的补丁（`probe`、`time`）互不串门。"""
    sys.path.insert(0, str(REPO / "src"))
    spec = importlib.util.spec_from_file_location(
        "peach._studio_sites_under_test", REPO / "src" / "peach" / "studio_sites.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_script(module, args) -> int:
    """跑命令行那一趟，判据换成打过补丁的那份副本。"""
    spec = importlib.util.spec_from_file_location(
        "harvest_studio_sites", REPO / "scripts" / "harvest_studio_sites.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    script.sites = module
    return script.run(args)


def page(title: str, filler: str = "本編は18歳以上の方のみご覧いただけます。") -> bytes:
    """够长、含标题的正常页面；长度必须越过空壳下限，否则测的就不是标题判定。"""
    body = f"<html><head><title>{title}</title></head><body>{filler * 400}</body></html>"
    return body.encode("utf-8")


class SlugTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_a_multiword_name_yields_both_joined_and_hyphenated_hosts(self):
        """`Wanz Factory` 的真实域名是 wanz-factory.com，而 `Idea Pocket` 是 ideapocket.com。

        两种写法都常见，只推一种就会漏掉一半；连写在前是因为它更常见。
        """
        self.assertEqual(self.module.slugs("Wanz Factory"), ["wanzfactory", "wanz-factory"])
        self.assertEqual(self.module.slugs("MOODYZ"), ["moodyz"])

    def test_a_name_without_latin_letters_yields_no_candidates(self):
        """`无码厂标` 这类名字推不出域名。

        推不出来要老实返回空，让它落到「没有可推导的候选域名」，
        而不是拼出一个必然打不开的地址再去请求一遍。
        """
        self.assertEqual(self.module.slugs("无码厂标"), [])
        self.assertEqual(self.module.candidate_urls("无码厂标"), [])

    def test_candidate_urls_are_unique_and_prefer_dot_com(self):
        urls = self.module.candidate_urls("MOODYZ")
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(urls[0], "https://moodyz.com/")
        self.assertTrue(any(u.endswith("moodyz.jp/") for u in urls))

    def test_the_www_variant_is_not_a_separate_candidate(self):
        """多出来的那一半候选几乎只在失败路径上产生开销。

        transport 开着 follow_redirects，只用 `www.` 的站会从裸域跳过来；而裸域连不上时
        `www.` 基本也连不上。首轮 57 个厂牌 × 8 个候选跑了三个多小时还没完——httpx 把
        单个标量同时当作 connect 和 read 超时，死域名每个吃掉两份。这条测试守的是那次代价。
        """
        urls = self.module.candidate_urls("Wanz Factory")
        self.assertFalse([url for url in urls if url.startswith("https://www.")])
        self.assertEqual(len(urls), 8)   # 2 个 slug 写法 × 4 种域名形态


class NormaliseTests(unittest.TestCase):
    """比对前留下哪些字符，决定了整整一类厂牌能不能被判。"""

    def setUp(self):
        self.module = load_module()

    def test_a_name_written_only_in_japanese_still_has_something_to_compare(self):
        """账本 130 个有作品的厂牌里有 19 个一个拉丁字母都没有。

        只留 ASCII 的话它们的 token 全是空串，`site_verdict` 第一步就判「没有可比对的
        字符」；这些厂牌的域名又推不出来，`--seeds` 是唯一入口，于是人工查到的地址取回
        什么都没用。实测 `https://www.1pondo.tv/` 返回 200、标题正是
        `一本道 | 美を追求する高画質アダルト動画サイト`，照样被写成「没有官网」。
        """
        for name in ("一本道", "カリビアンコム", "スーパーモデルメディア", "俺の素人"):
            self.assertTrue(self.module.normalise(name), name)

    def test_the_prolonged_sound_mark_is_part_of_the_name_but_the_dot_is_not(self):
        """`ー` 在名字里，`・` 是分隔符——两个都住在片假名区，取舍不能按区块一刀切。"""
        self.assertEqual(self.module.normalise("スーパーモデルメディア"), "スーパーモデルメディア")
        self.assertEqual(self.module.normalise("ソフト・オン・デマンド"), "ソフトオンデマンド")

    def test_the_separators_that_never_line_up_are_still_stripped(self):
        """全角括号、`【】` 和日文注音照旧剥掉，否则 `Idea Pocket` 那一类又对不上了。"""
        self.assertEqual(
            self.module.normalise("【IDEAPOCKET (アイデアポケット）】公式サイト"),
            "ideapocketアイデアポケット公式サイト")

    def test_different_studios_are_not_folded_onto_one_token(self):
        """剥字符不能把两家压成同一个串。"""
        self.assertNotEqual(self.module.normalise("MOODYZ"), self.module.normalise("MADONNA"))
        self.assertNotEqual(self.module.normalise("一本道"), self.module.normalise("東京熱"))


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_a_japanese_only_name_is_judged_by_the_same_two_signals(self):
        """`一本道` 实测：标题自述厂牌名 + 页面是成人站，和拉丁名厂牌走的是同一条路。"""
        title = "一本道 | 美を追求する高画質アダルト動画サイト"
        verdict, note = self.module.site_verdict(
            "一本道", 200, page(title), title, "https://www.1pondo.tv/")
        self.assertEqual(verdict, "ok")
        self.assertIn("成人站", note)

    def test_an_uncensored_site_that_says_video_the_other_way_is_still_adult(self):
        """无码站普遍写「アダルト動画」「無修正」，不写「アダルトビデオ」。

        少了这两个词，`www.1pondo.tv` 这种厂牌名自述得清清楚楚的真官网只能判 weak，
        原因仅仅是站上用的是「動画」而不是「ビデオ」。
        """
        for filler in ("高画質アダルト動画サイト", "無修正オリジナル作品"):
            self.assertTrue(self.module.ADULT.search(filler), filler)

    def test_the_adult_check_still_turns_away_the_same_name_companies(self):
        """放宽成人词不能把同名站一起放进来——那正是这条判据存在的理由。"""
        for filler in ("Home | Hunter Engineering Company®",
                       "Bazooka Bass Tubes 车载音响产品目录",
                       "Madonna – Icon Community"):
            self.assertIsNone(self.module.ADULT.search(filler), filler)

    def test_a_title_that_names_the_studio_is_accepted(self):
        """实测标题：分隔符、全角括号和日文注音都对不齐，剥到字母数字才可比。"""
        title = "年齢チェック | 人気知名度NO.1！アダルトビデオ最強のAVメーカー【MOODYZ(ムーディーズ)】公式サイト"
        verdict, _ = self.module.site_verdict("MOODYZ", 200, page(title), title)
        self.assertEqual(verdict, "ok")

        title = "年齢チェック | ハイクオリティのAVメーカー【IDEAPOCKET (アイデアポケット）】公式サイト"
        verdict, _ = self.module.site_verdict("Idea Pocket", 200, page(title), title)
        self.assertEqual(verdict, "ok")

    def test_an_unrelated_company_with_the_same_name_is_not_confirmed(self):
        """「标题里有厂牌名」证明不了这是**这个**厂牌的站。

        实测四例全都 200、都不是停放页、标题里都有厂牌名：`madonna.com` 是歌手麦当娜
        （真站 madonna-av.com）、`hunter.com` 是 Hunter Engineering、`bazooka.com`
        是车载音响。缺的那一半是「这一页是不是 AV 厂牌站」——我们要找的就是 AV 官网，
        成人语境本来就是判据的一部分，不是补丁。这类只能判 weak 交给人看，不能算已确认：
        一条错的官网会被下游当成社媒 handle 的来源。
        """
        for studio, title in [("MADONNA", "Madonna – Icon Community"),
                              ("Hunter", "Home | Hunter Engineering Company®"),
                              ("BAZOOKA", "Bazooka Bass Tubes")]:
            verdict, note = self.module.site_verdict(
                studio, 200, page(title, "公司简介与产品目录。"), title,
                f"https://{studio.lower()}.com/")
            self.assertEqual(verdict, "weak", studio)
            self.assertIn("成人站", note)

    def test_a_derived_domain_on_an_adult_site_counts_even_when_the_title_uses_kana(self):
        """日站普遍把品牌名写成假名，拿拉丁名比标题对整整一类真站都会失败。

        实测：`honnaka.jp` 的标题是「年齢チェック | 全作品、本物中出しのAVメーカー
        【本中】公式サイト」，`muku.tv` 是「【無垢】」，`tameikegoro.jp` 是「【TAMEIKE】」。
        这是系统性漏判，不是个别情况。此时两个独立信号同样成立：域名由厂牌名按固定规则
        推出（不是人喂的），且这一页确实是成人站。
        """
        title = "年齢チェック | 全作品、本物中出しのAVメーカー【本中】公式サイト"
        verdict, note = self.module.site_verdict(
            "Hon Naka", 200, page(title), title, "https://honnaka.jp/",
            derived_hosts=frozenset({"honnaka.jp"}))
        self.assertEqual(verdict, "ok")
        self.assertIn("假名", note)

    def test_a_derived_domain_that_is_not_an_adult_site_is_still_refused(self):
        """`bazooka.com`、`madonna.com` 的域名同样推得出来。

        把它们和上面那条区分开的正是成人语境这一条；少了它，这条规则会把每个
        「同名公司」都当成官网确认掉——比不做这条判定更糟。
        """
        title = "Bazooka Bass Tubes"
        verdict, _ = self.module.site_verdict(
            "BAZOOKA", 200, page(title, "car audio subwoofers. "), title,
            "https://bazooka.com/", derived_hosts=frozenset({"bazooka.com"}))
        self.assertEqual(verdict, "weak")

    def test_a_page_that_says_it_is_unavailable_is_refused(self):
        """2026-09-03 实测的假阳性：`bangbus.com` 与 `monstersofcock.com` 都返回 200、
        82 KB、正文成人词齐全，标题只有 `Site Unavailable`。

        域名确实由厂牌名推出、页面确实是成人站，两个信号都成立，所以旧规则把它们判成 ok，
        写进账本就是两条错的官网。一页自己说自己打不开，就不能当官网证据。
        """
        title = "Site Unavailable"
        verdict, note = self.module.site_verdict(
            "BangBus", 200, page(title, "bang bus porn videos. "), title,
            "https://www.bangbus.com/", derived_hosts=frozenset({"bangbus.com"}))
        self.assertEqual(verdict, "未取得")
        self.assertIn("不可用", note)

    def test_a_maintenance_notice_is_refused_too(self):
        """同一类：日站的维护页也会 200，标题写着メンテナンス。"""
        title = "メンテナンス中 | AVメーカー"
        verdict, _ = self.module.site_verdict(
            "Muku", 200, page(title), title, "https://muku.tv/",
            derived_hosts=frozenset({"muku.tv"}))
        self.assertEqual(verdict, "未取得")

    def test_the_unavailable_check_looks_at_the_title_only(self):
        """反向：这条只判标题，不判正文。

        真站正文里出现 `not found`、`maintenance` 一点也不稀奇——脚本里的报错字符串、
        埋在页面里的客服说明都会命中。停放页那条已经吃过一次「按正文判会误杀」的教训，
        这条不重走。
        """
        title = "年齢チェック | 凌辱、背徳のAVメーカー【ATTACKERS（アタッカーズ）】公式サイト"
        body = page(title, "if(!el){console.error('not found')} 年齢認証 メンテナンス予定はこちら。")
        verdict, _ = self.module.site_verdict(
            "Attackers", 200, body, title, "https://attackers.net/",
            derived_hosts=frozenset())
        self.assertEqual(verdict, "ok")

    def test_a_seeded_url_does_not_get_the_derived_domain_signal(self):
        """人喂进来的地址不能借「域名由厂牌名推出」这条的力。

        那条信号的价值在于域名是独立推出来的；种子是人给的，混在一起就等于自证。
        """
        title = "年齢チェック | マニアック フェチのAVメーカー【ダスッ！】公式サイト"
        seeded, _ = self.module.site_verdict(
            "Das", 200, page(title), title, "https://dasdas.jp/")
        found, _ = self.module.site_verdict(
            "Das", 200, page(title), title, "https://dasdas.jp/",
            derived_hosts=frozenset({"dasdas.jp"}))
        self.assertNotEqual(seeded, "ok", "种子不该借域名推导这条信号确认")
        self.assertEqual(found, "ok", "同一页面若域名是推出来的才算两个独立信号")

    def test_a_derived_domain_that_redirects_elsewhere_loses_the_signal(self):
        """跳走之后，「域名由厂牌名推出」这条证据就不再对我们落到的那一页成立。

        实测：`Natural High` 的一个推导域名重定向到了 `linkedin.com/in/fareedkhan`。
        按请求地址判 derived 的话，一个 LinkedIn 个人主页会被确认成厂牌官网。
        判据必须落在最终主机上。
        """
        title = "Fareed Khan - Ericsson | LinkedIn"
        verdict, _ = self.module.site_verdict(
            "Natural High", 200, page(title, "adult contacts and 18+ groups. "), title,
            "https://www.linkedin.com/in/fareedkhan",
            derived_hosts=frozenset({"naturalhigh.com", "naturalhigh.jp"}))
        self.assertNotEqual(verdict, "ok")

    def test_a_western_studio_page_still_counts_as_adult(self):
        """判据不能只认日文，否则西方厂牌会被一律打成待复核。"""
        title = "BLACKED - The Best Adult Video Site"
        verdict, _ = self.module.site_verdict(
            "Blacked", 200, page(title, "adult video content, 18+ only. "), title,
            "https://blacked.com/")
        self.assertEqual(verdict, "ok")

    def test_a_parked_page_is_rejected_even_though_its_title_echoes_the_name(self):
        """这是猜域名最危险的失败模式。

        抢注页同样回 200，标题里同样有厂牌名（因为那就是域名本身）。只看标题的话
        `moodyz.com` 的停放页会被当成官网采信，再被下游当成社媒 handle 的来源，
        最后把抢注者的账号安到厂牌头上。
        """
        title = "moodyz.com - This domain is for sale"
        verdict, note = self.module.site_verdict("MOODYZ", 200, page(title), title)
        self.assertEqual(verdict, "未取得")
        # 拦在标题还是正文都算数，守的是「被拒绝且理由指向域名出售」。
        self.assertRegex(note, "出售|停放")

    def test_a_generic_domain_whose_title_is_just_its_address_is_rejected(self):
        """停放页规则拦不住这一类：它是个正常的站，只是不属于这个厂牌。

        实测 `https://www.prestige.com/` 返回 200、不是停放页、标题正好是
        `prestige.com`，因此通过了「标题含厂牌名」被判成 Prestige 官网——而真站是
        `prestige-av.com`，`prestige.com` 是另一家公司。判据是：标题若不比域名多说
        任何东西，就等于没有自述身份。
        """
        verdict, note = self.module.site_verdict(
            "Prestige", 200, page("prestige.com"), "prestige.com",
            "https://www.prestige.com/")
        self.assertEqual(verdict, "未取得")
        self.assertIn("域名回显", note)

    def test_a_real_site_is_not_rejected_by_the_domain_echo_rule(self):
        """真站的标题远不止域名，这条规则不能误伤它们。"""
        title = "年齢チェック | 人気知名度NO.1！アダルトビデオ最強のAVメーカー【MOODYZ(ムーディーズ)】公式サイト"
        verdict, _ = self.module.site_verdict(
            "MOODYZ", 200, page(title), title, "https://moodyz.com/")
        self.assertEqual(verdict, "ok")

    def test_a_title_that_is_only_the_brand_name_is_not_a_domain_echo(self):
        """实测误伤：`https://www.naturalhigh.co.jp/` 是 Natural High 的真官网。

        它返回 200、成人站、标题正是 `NATURAL HIGH（ナチュラルハイ）`。把标题
        normalise 成 naturalhigh 再去比主机 naturalhighcojp 的话，它是主机的一部分、
        于是判成「域名回显」——可域名本来就是按厂牌名推出来的，这两者必然互相包含，
        整类真站都会被打掉。所以域名回显要求标题原样印着 TLD。
        """
        title = "NATURAL HIGH（ナチュラルハイ）"
        verdict, _ = self.module.site_verdict(
            "Natural High", 200, page(title, "AVメーカー、18歳未満禁止。"), title,
            "https://www.naturalhigh.co.jp/")
        self.assertEqual(verdict, "ok")

    def test_a_title_that_prints_the_domain_plus_a_real_tagline_is_kept(self):
        """标题里出现域名不等于回显，多说了话就还是自述身份。"""
        title = "attackers.net｜凌辱、背徳のAVメーカー【ATTACKERS】公式サイト"
        verdict, _ = self.module.site_verdict(
            "Attackers", 200, page(title), title, "https://attackers.net/")
        self.assertEqual(verdict, "ok")

    def test_a_page_that_only_hands_off_to_a_parking_platform_is_rejected(self):
        """停放页不一定自述在出售，只把访客交给停放平台的路由脚本。"""
        title = "Cruse Group"
        body = page(title) + b'<script>fetch("https://router.parklogic.com/")</script>'
        verdict, note = self.module.site_verdict(
            "Cruse Group", 200, body, title, "https://crusegroup.net/")
        self.assertEqual(verdict, "未取得")
        self.assertIn("parklogic.com", note)

    def test_a_parked_title_is_caught_even_when_the_body_keyword_is_far_down(self):
        """实测漏判：`kawaii.com - domain for sale`。

        关键词确实在正文里，但在第 81683 字节——任何固定的正文扫描窗口都会漏掉它。
        而它就写在标题里，我此前从来没搜过标题。真站的标题不会说自己在出售，
        所以标题可以用最宽的判据。
        """
        title = "kawaii.com - domain for sale"
        body = page("无关内容") + b"x" * 90000 + "domain for sale".encode()
        verdict, note = self.module.site_verdict("kawaii", 200, body, title,
                                                 "https://www.kawaii.com/")
        self.assertEqual(verdict, "未取得")
        self.assertIn("出售", note)

    def test_a_parked_title_with_words_between_is_still_caught(self):
        """实测漏判：`Attackers - The Domain Name Attackers.com is Now For Sale.`

        逐字匹配「domain for sale」对不上——中间插了域名和 is Now。这正是为什么
        标题要单独用更松的判据，而不是把正文那套原样套过来。
        """
        title = "Attackers - The Domain Name Attackers.com is Now For Sale."
        verdict, note = self.module.site_verdict("Attackers", 200, page(title), title,
                                                 "https://attackers.com/")
        self.assertEqual(verdict, "未取得")
        self.assertIn("出售", note)

    def test_a_short_page_is_rejected(self):
        """空壳页也是 200。真站首页实测都在 10 KB 以上。"""
        verdict, note = self.module.site_verdict("MOODYZ", 200, b"<title>MOODYZ</title>", "MOODYZ")
        self.assertEqual(verdict, "未取得")
        self.assertIn("字节", note)

    def test_a_page_that_never_names_the_studio_is_not_guessed_into_ok(self):
        title = "無料動画まとめサイト"
        verdict, _ = self.module.site_verdict("MOODYZ", 200, page(title), title)
        self.assertEqual(verdict, "未取得")

    def test_a_body_only_mention_is_weak_not_ok(self):
        """正文提到不等于这是官网——转载站也会提到厂牌名。

        它值得人看一眼，所以照样出行，但判定必须和标题自述区分开，
        不能混进 ok 里被当成已确认。
        """
        body = page("よくある動画サイト", "MOODYZ の作品を紹介します。")
        verdict, note = self.module.site_verdict("MOODYZ", 200, body, "よくある動画サイト")
        self.assertEqual(verdict, "weak")
        self.assertIn("人工", note)

    def test_a_non_200_is_rejected_before_reading_the_body(self):
        verdict, note = self.module.site_verdict("MOODYZ", 404, page("MOODYZ"), "MOODYZ")
        self.assertEqual(verdict, "未取得")
        self.assertIn("404", note)

    def test_page_title_survives_nested_tags_and_whitespace(self):
        body = b"<html><head><title>\n  <span>MOODYZ</span>  \xe5\x85\xac\xe5\xbc\x8f\n</title></head>"
        self.assertEqual(self.module.page_title(body), "MOODYZ 公式")


    def test_a_shift_jis_page_is_decoded_instead_of_mangled(self):
        """老厂牌站仍有不少是 Shift_JIS。

        硬按 UTF-8 解不会报错，只会把标题变成一串 U+FFFD，然后「标题里没有厂牌名」
        这条判定会把真官网误杀掉——失败是静默的，这正是它值得一个测试的原因。
        """
        title = "MOODYZ 公式サイト"
        body = f"<html><head><title>{title}</title></head></html>".encode("cp932")
        self.assertEqual(self.module.page_title(body), title)


class AliasVerdictTests(unittest.TestCase):
    """账本里的其他写法和规范名一起参与「页面自述厂牌名」那一道。"""

    def setUp(self):
        self.module = load_module()
        # 实测 `https://www.tokyo-hot.com/` 的标题。
        self.title = "年齢確認 | Tokyo-Hot 東京熱 無修正オリジナル徹底凌辱動画"

    def test_the_canonical_name_alone_cannot_match_a_differently_written_site(self):
        """账本挑了简体「东京热」当规范名，站上写的是日文新字体「東京熱」。

        逐字比不上，而这不是页面的问题：页面不会知道账本挑了哪个写法。
        """
        verdict, note = self.module.site_verdict(
            "东京热", 200, page(self.title), self.title, "https://www.tokyo-hot.com/")
        self.assertEqual(verdict, "未取得")
        self.assertIn("没有厂牌名", note)

    def test_a_registered_alias_lets_the_page_declare_itself(self):
        """别名是账本里这个实体自己的名字，不是从页面上猜出来的，所以不放松判据。"""
        verdict, note = self.module.site_verdict(
            "东京热", 200, page(self.title), self.title, "https://www.tokyo-hot.com/",
            aliases=("Tokyo Hot", "Tokyo-Hot", "東京熱"))
        self.assertEqual(verdict, "ok")
        self.assertIn("成人站", note)

    def test_the_note_says_which_spelling_matched(self):
        """复核的人看到标题里写着别的写法，得能立刻明白为什么判成了 ok。"""
        _, note = self.module.site_verdict(
            "东京热", 200, page(self.title), self.title, "https://www.tokyo-hot.com/",
            aliases=("東京熱",))
        self.assertIn("東京熱", note)

    def test_a_canonical_match_does_not_claim_an_alias(self):
        """规范名自己对上的时候判词不加那半句，不然每一行都在解释一件没发生的事。"""
        title = "年齢チェック | AVメーカー【MOODYZ】公式サイト"
        _, note = self.module.site_verdict(
            "MOODYZ", 200, page(title), title, "https://moodyz.com/", aliases=("ムーディーズ",))
        self.assertEqual(note, "标题自述厂牌名，且页面是成人站")

    def test_an_alias_does_not_rescue_a_page_that_is_not_adult(self):
        """别名只替页面找回名字，成人语境那一条照旧要自己站住。"""
        title = "Tokyo Hot Chicken | 東京熱 麻辣炸鸡"
        verdict, _ = self.module.site_verdict(
            "东京热", 200, page(title, "本店の営業時間のご案内。"), title,
            "https://tokyohot-chicken.example/", aliases=("東京熱",))
        self.assertEqual(verdict, "weak")

    def test_a_name_and_aliases_that_are_all_unusable_say_so(self):
        """一个可比对字符都凑不出来时要写清是「没得比」，不是「页面没提」。"""
        title = "なにかのサイト"
        verdict, note = self.module.site_verdict(
            "・", 200, page(title), title, "https://example.com/", aliases=("＋＋",))
        self.assertEqual(verdict, "未取得")
        self.assertIn("没有可比对", note)


class ConfirmedSiteTests(unittest.TestCase):
    """用户确认的官网：补一条页面上没有的信息，不是放宽判据。"""

    def setUp(self):
        self.module = load_module()
        self.title = "SOFT ON DEMAND（ソフト・オン・デマンド）"

    def test_sod_create_points_at_the_parent_company_site(self):
        """裁决本身要能被读出来：地址和理由都写在表里，不散在代码逻辑里。"""
        url, reason = self.module.CONFIRMED_SITES["SOD Create"]
        self.assertEqual(url, "https://www.sod.co.jp/")
        self.assertIn("Soft On Demand", reason)

    def test_a_confirmed_site_is_ok_although_the_title_never_says_the_studio_name(self):
        """这一页 200、是成人站、标题是母公司名——`SOD Create` 这个串它不会有。

        通用判据到此为止只能判「标题与正文都没有厂牌名」，而缺的那条信息
        （这个厂牌属于哪家公司）本来就不在页面上。
        """
        without, note = self.module.site_verdict(
            "SOD Create", 200, page(self.title), self.title, "https://www.sod.co.jp/")
        self.assertEqual(without, "未取得")
        self.assertIn("没有厂牌名", note)
        verdict, note = self.module.site_verdict(
            "SOD Create", 200, page(self.title), self.title, "https://www.sod.co.jp/",
            confirmed=self.module.CONFIRMED_SITES["SOD Create"][1])
        self.assertEqual(verdict, "ok")
        self.assertIn("用户", note)
        self.assertIn("SOFT ON DEMAND", note, "实测标题要留在证据里，才复核得了")

    def test_the_whitelist_does_not_reopen_the_other_gates(self):
        """确认的是「这个地址属于这家公司」，不是「这个地址返回什么都算」。

        域名过期被抢注、站点在维护，页面照样 200——白名单要是把这几道一起绕过去，
        一条停放页就会以 ok 进账本，而它比没有链接更糟。
        """
        reason = self.module.CONFIRMED_SITES["SOD Create"][1]
        for title, hint in [("sod.co.jp is for sale | HugeDomains", "出售"),
                            ("メンテナンス中", "不可用")]:
            verdict, note = self.module.site_verdict(
                "SOD Create", 200, page(title), title, "https://www.sod.co.jp/",
                confirmed=reason)
            self.assertEqual(verdict, "未取得", title)
            self.assertIn(hint, note)
        verdict, _ = self.module.site_verdict(
            "SOD Create", 503, page(self.title), self.title, "https://www.sod.co.jp/",
            confirmed=reason)
        self.assertEqual(verdict, "未取得")

    def test_a_studio_under_its_japanese_name_verifies_on_its_own_title(self):
        """番号站转写的罗马音在厂牌自己站上不出现；账本记日文原名，页面自述就对得上。

        实测 `https://mko-labo.net/top` 返回 200、55 KB、标题
        `トップ | 調教、全身奉仕、醜態...M女専門のAVメーカー【えむっ娘ラボ】公式`。
        罗马音 `M Girls' Lab` 判未取得，那是账本名字的问题，不该靠白名单补。
        """
        url = "https://mko-labo.net/top"
        title = "トップ | 調教、全身奉仕、醜態...M女専門のAVメーカー【えむっ娘ラボ】公式"
        self.assertNotIn("えむっ娘ラボ", self.module.CONFIRMED_SITES)
        romanized, _ = self.module.site_verdict(
            "M Girls' Lab", 200, page(title), title, url)
        self.assertEqual(romanized, "未取得")
        verdict, _ = self.module.site_verdict("えむっ娘ラボ", 200, page(title), title, url)
        self.assertEqual(verdict, "ok")


class PlatformEntityTests(unittest.TestCase):
    """发行平台不是厂牌，「厂牌官网」这条路对它们本来就不成立。"""

    def setUp(self):
        self.module = load_module()

    def test_the_known_platforms_are_recognised_whatever_the_spelling(self):
        for name in ["FC2-PPV", "fc2 ppv", "FC2", "myfans", "MyFans"]:
            self.assertTrue(self.module.is_platform(name), name)

    def test_real_studios_are_not_swept_up(self):
        for name in ["MOODYZ", "Attackers", "SOD Create", "Fitch"]:
            self.assertFalse(self.module.is_platform(name), name)


class PlatformRowTests(unittest.TestCase):
    """平台要出现在复核件上并说明为什么不适用，不能静默少扫一个。

    少扫一个和「扫过但没找到」在复核件上长得一模一样，人只会读到「FC2-PPV 没有官网」。
    """

    def setUp(self):
        self.module = load_module()
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "ledger.db"
        writer = sqlite3.connect(self.db)
        self.addCleanup(writer.close)
        writer.execute(
            "CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT)")
        writer.execute("CREATE TABLE asset(id INTEGER PRIMARY KEY, medium TEXT)")
        writer.execute("CREATE TABLE asset_entity(asset_id INTEGER, entity_id INTEGER)")
        writer.execute("CREATE TABLE entity_alias(entity_id INTEGER, alias TEXT)")
        writer.execute("INSERT INTO entity VALUES(1,'studio','FC2-PPV')")
        writer.commit()

    def args(self):
        import argparse
        return argparse.Namespace(
            db=self.db, output=self.tmp / "out.csv", seeds=None,
            min_assets=3, only=["FC2-PPV"], interval=0.0, timeout=1.0, limit=0)

    def test_a_platform_gets_its_own_verdict_without_a_single_request(self):
        def refuse(*_args, **_kwargs):
            raise AssertionError("不该为发行平台发出任何请求")
        self.module.probe = refuse
        self.assertEqual(run_script(self.module, self.args()), 0)
        from peach.review_csv import read_rows
        rows = read_rows(self.tmp / "out.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verdict"], self.module.PLATFORM_VERDICT)
        self.assertIn("发行平台", rows[0]["note"])
        self.assertEqual(rows[0]["candidate_url"], "",
                         "一个地址都没试过，就不该在行上留候选")


class LoadStudioTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "ledger.db"
        writer = sqlite3.connect(self.db)
        self.addCleanup(writer.close)
        writer.execute(
            "CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT)")
        writer.execute("CREATE TABLE asset(id INTEGER PRIMARY KEY, medium TEXT)")
        writer.execute("CREATE TABLE asset_entity(asset_id INTEGER, entity_id INTEGER)")
        writer.execute("CREATE TABLE entity_alias(entity_id INTEGER, alias TEXT)")
        writer.executemany("INSERT INTO entity_alias VALUES(?,?)",
                           [(1, "Big Pictures"), (1, "ビッグ"), (2, "")])
        writer.executemany("INSERT INTO entity VALUES(?,?,?)",
                           [(1, "studio", "Big"), (2, "studio", "Small"),
                            (3, "performer", "人"), (4, "studio", "Empty")])
        for asset_id, entity_id in [(10, 1), (11, 1), (12, 1), (13, 2), (14, 3)]:
            writer.execute("INSERT OR IGNORE INTO asset VALUES(?,?)", (asset_id, "video"))
            writer.execute("INSERT INTO asset_entity VALUES(?,?)", (asset_id, entity_id))
        writer.commit()

    def reader(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        self.addCleanup(connection.close)
        return connection

    def test_studios_are_ordered_by_asset_count_and_filtered_by_minimum(self):
        connection = self.reader()
        self.assertEqual([r["studio"] for r in self.module.load_studios(connection, 1)],
                         ["Big", "Small"])
        self.assertEqual([r["studio"] for r in self.module.load_studios(connection, 3)],
                         ["Big"])

    def test_a_named_studio_is_loaded_even_though_it_is_under_the_minimum(self):
        """指名就是「不管几部都要这一个」。

        BangBus、BangBros18 各只有 1 部视频，OPPAI、MonstersOfCock 各 2 部，全部低于默认的 3。
        作品数门槛照旧作用于默认路径，这里只是不再让它挡住指名的目标。
        """
        rows = self.module.load_named_studios(self.reader(), ["Small"])
        self.assertEqual(rows, [{"entity_id": 2, "studio": "Small", "assets": 1}])

    def test_a_named_studio_with_no_video_at_all_still_comes_back(self):
        """一部视频都没有的厂牌也要能补官网——它的作品可能全是图片，或者刚建实体。

        默认路径用 JOIN 数作品，这种厂牌根本不会出现；指名路径必须把它取出来并记 0，
        而不是当成「没这个厂牌」。
        """
        rows = self.module.load_named_studios(self.reader(), ["Empty"])
        self.assertEqual(rows, [{"entity_id": 4, "studio": "Empty", "assets": 0}])

    def test_named_studios_keep_the_order_they_were_given(self):
        """复核件的行序要可预期：给的顺序就是写出来的顺序。"""
        rows = self.module.load_named_studios(self.reader(), ["Small", "Big"])
        self.assertEqual([r["studio"] for r in rows], ["Small", "Big"])

    def test_aliases_come_back_per_entity_and_only_for_the_ids_asked_for(self):
        """别名一次查完：默认扫描有一百多家，每家一次往返换不来任何东西。"""
        self.assertEqual(self.module.load_aliases(self.reader(), [1, 2, 4]),
                         {1: ("Big Pictures", "ビッグ")})

    def test_asking_for_nothing_does_not_hit_the_database(self):
        """`--limit 0` 之后可能一家都不剩，空 IN 列表是个语法错误。"""
        self.assertEqual(self.module.load_aliases(self.reader(), []), {})

    def test_an_empty_alias_row_is_not_a_spelling(self):
        """空串进了表，`site_verdict` 会拿到一个 normalise 后为空的写法：白占一次比对。"""
        self.assertEqual(self.module.load_aliases(self.reader(), [2]), {})

    def test_a_name_the_ledger_does_not_have_fails_loudly(self):
        """拼错的名字必须报错。

        指名用法下静悄悄少扫一个，在复核件上和「扫过但没找到」长得一模一样；
        一个 typo 会被当成「这家没有官网」的结论。同名的 performer 也不算——
        只认 `kind='studio'`。
        """
        with self.assertRaises(SystemExit) as caught:
            self.module.load_named_studios(self.reader(), ["Big", "Attackers", "人"])
        self.assertIn("Attackers", str(caught.exception))
        self.assertIn("人", str(caught.exception))


class ProbeRetryTests(unittest.TestCase):
    """传输层抖动不是结论。

    实测 `www.naturalhigh.co.jp` 第一次 `ReadTimeout`，同一地址再取就是 200、标题
    `NATURAL HIGH（ナチュラルハイ）`。没有重试时那一次抖动直接写成「这家没有官网」，
    而账本里少一条 official 链接，下游的图标采集就整整跳过这个厂牌。
    """

    def setUp(self):
        self.module = load_module()
        self.calls: list[str] = []
        self.slept: list[float] = []
        self.module.crawler_client = lambda: None
        self.module.time = type("clock", (), {
            "sleep": lambda _self, seconds: self.slept.append(seconds),
            "monotonic": lambda _self: 0.0,
        })()

    def transport(self, outcomes):
        """按 `outcomes` 依次作答：异常就抛，元组就当成一次成功的响应。"""
        module = self.module
        results = list(outcomes)

        class Fake:
            def __init__(self, client, *, owns_client=False):
                pass

            def __call__(self, request, timeout, limit):
                answer = results.pop(0)
                if isinstance(answer, Exception):
                    raise answer
                status, body, url = answer
                return type("response", (), {"status": status, "body": body, "url": url})()

            def close(self):
                pass

        module.HttpxTransport = Fake
        return module

    def test_a_transport_blip_is_retried_and_then_succeeds(self):
        module = self.transport([TimeoutError("read timed out"),
                                 (200, b"body", "https://www.naturalhigh.co.jp/")])
        status, body, final = module.probe("http://www.naturalhigh.co.jp/", 8.0, backoff=0.0)
        self.assertEqual((status, body), (200, b"body"))
        self.assertEqual(final, "https://www.naturalhigh.co.jp/")
        self.assertEqual(self.slept, [0.0])

    def test_the_last_failure_is_raised_after_the_retries_run_out(self):
        """一直连不上就得原样抛出来，让上层把真实异常名写进复核件。"""
        module = self.transport([TimeoutError("1"), TimeoutError("2"), TimeoutError("3")])
        with self.assertRaises(TimeoutError):
            module.probe("https://dead.example/", 8.0, backoff=0.0)
        self.assertEqual(self.slept, [0.0, 0.0])

    def test_an_http_status_is_not_retried(self):
        """404 是站点的回答，不是抖动。重试它只会把整批时间翻三倍。"""
        module = self.transport([(404, b"", "https://x.example/")])
        self.assertEqual(module.probe("https://x.example/", 8.0)[0], 404)
        self.assertEqual(self.slept, [])


class RunTrailTests(unittest.TestCase):
    """失败行要看得见是在哪几个地址上失败的。

    后一个候选覆盖前一个的话：`SOD Create` 试了六个地址，复核件上只剩最后那个的
    「取不到：ConnectError」，而真正值得看的是被盖掉的 `www.sod.co.jp`——200、成人站、
    标题 `SOFT ON DEMAND（ソフト・オン・デマンド）`。判未取得可以，看不见理由不行。

    现在这条用 `Prestige` 复现同一个形状：`SOD Create` 已进 `CONFIRMED_SITES`，第一个
    候选就是确认地址且直接采信，走不到「一串候选全失败」这条路了。
    """

    def setUp(self):
        self.module = load_module()
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "ledger.db"
        writer = sqlite3.connect(self.db)
        self.addCleanup(writer.close)
        writer.execute(
            "CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT)")
        writer.execute("CREATE TABLE asset(id INTEGER PRIMARY KEY, medium TEXT)")
        writer.execute("CREATE TABLE asset_entity(asset_id INTEGER, entity_id INTEGER)")
        writer.execute("CREATE TABLE entity_alias(entity_id INTEGER, alias TEXT)")
        writer.execute("INSERT INTO entity VALUES(1,'studio','Prestige')")
        writer.commit()
        self.module.time = type("clock", (), {
            "sleep": lambda _self, seconds: None,
            "monotonic": lambda _self: 0.0,
        })()

    def args(self, seeds: Path | None = None):
        import argparse
        return argparse.Namespace(
            db=self.db, output=self.tmp / "out.csv", seeds=seeds,
            min_assets=3, only=["Prestige"], interval=0.0, timeout=1.0, limit=0)

    def row(self) -> dict:
        from peach.review_csv import read_rows
        rows = read_rows(self.tmp / "out.csv")
        self.assertEqual(len(rows), 1)
        return rows[0]

    def probe_map(self, answers: dict):
        def probe(url, timeout, **kwargs):
            answer = answers[url]
            if isinstance(answer, Exception):
                raise answer
            return answer
        self.module.probe = probe

    def test_every_candidate_leaves_its_reason_in_the_note(self):
        seeds = self.tmp / "seeds.csv"
        seeds.write_text("studio,site\nPrestige,https://prestige.co.jp/\n"
                         "Prestige,https://www.prestige-av.jp/\n", encoding="utf-8")
        title = "プレステージ"
        self.probe_map({
            "https://prestige.co.jp/": ConnectionError("handshake"),
            "https://www.prestige-av.jp/": (200, page(title),
                                            "https://www.prestige-av.jp/"),
            "https://prestige.com/": (200, page("Prestige.com is for sale"),
                                      "https://www.hugedomains.com/x"),
            "https://prestige.jp/": ConnectionError("nx"),
            "https://prestige-av.com/": ConnectionError("nx"),
            "https://prestige.tv/": ConnectionError("nx"),
        })
        self.assertEqual(run_script(self.module, self.args(seeds)), 0)
        row = self.row()
        self.assertEqual(row["verdict"], "未取得")
        self.assertIn("https://prestige.co.jp/ → 取不到：ConnectionError", row["note"])
        self.assertIn("プレステージ", row["note"])
        # 行上的 url／标题／sha256 仍归最后一个真取回字节的候选，方便按 sha256 复现；
        # 「都试过哪些」由 note 承担。
        self.assertIn("hugedomains", row["final_url"])

    def test_a_confirmed_row_keeps_the_single_reason_not_the_trail(self):
        """采信了就不再堆一串理由：ok 那一行只该说这一个地址为什么成立。"""
        seeds = self.tmp / "seeds.csv"
        seeds.write_text("studio,site\nPrestige,https://dead.example/\n"
                         "Prestige,https://www.prestige-av.jp/\n", encoding="utf-8")
        title = "年齢チェック | AVメーカー【PRESTIGE（プレステージ）】公式サイト"
        self.probe_map({
            "https://dead.example/": ConnectionError("nx"),
            "https://www.prestige-av.jp/": (200, page(title),
                                            "https://www.prestige-av.jp/"),
        })
        self.assertEqual(run_script(self.module, self.args(seeds)), 0)
        row = self.row()
        self.assertEqual(row["verdict"], "ok")
        self.assertEqual(row["final_url"], "https://www.prestige-av.jp/")
        self.assertNotIn("→", row["note"])


class ConfirmedRunTests(unittest.TestCase):
    """确认过的地址要真的走到 `run()` 里，并且排在所有推导候选前面。

    `SOD Create` 推得出九个地址，八个是死的；确认地址排最前就只发一个请求。
    """

    def setUp(self):
        self.module = load_module()
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "ledger.db"
        writer = sqlite3.connect(self.db)
        self.addCleanup(writer.close)
        writer.execute(
            "CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT)")
        writer.execute("CREATE TABLE asset(id INTEGER PRIMARY KEY, medium TEXT)")
        writer.execute("CREATE TABLE asset_entity(asset_id INTEGER, entity_id INTEGER)")
        writer.execute("CREATE TABLE entity_alias(entity_id INTEGER, alias TEXT)")
        writer.execute("INSERT INTO entity VALUES(1,'studio','SOD Create')")
        writer.commit()
        self.module.time = type("clock", (), {
            "sleep": lambda _self, seconds: None,
            "monotonic": lambda _self: 0.0,
        })()

    def test_the_progress_line_cannot_take_the_batch_down(self):
        """入口块要把 stdout 换成 UTF-8：进度行里有日文标题，而本机控制台是 GBK。

        证据在 CSV 里（UTF-8），进度行糊掉无所谓；一个 print 把整批掉到一半才是代价。
        """
        source = (REPO / "scripts" / "harvest_studio_sites.py").read_text(encoding="utf-8")
        entry = source.split('if __name__ == "__main__":')[-1]
        self.assertIn('sys.stdout.reconfigure(encoding="utf-8", errors="replace")', entry)

    def test_the_confirmed_address_is_the_only_one_requested(self):
        import argparse
        asked: list[str] = []
        # 真实标题里那个 `・` 在 GBK 控制台上编不出来；`run()` 要打印标题，
        # 直调时没有入口块那句 `reconfigure`。这条测的是先试哪个地址，不拿它重现编码。
        title = "SOFT ON DEMAND（ソフトオンデマンド）"

        def probe(url, timeout, **kwargs):
            asked.append(url)
            return 200, page(title), url
        self.module.probe = probe
        args = argparse.Namespace(
            db=self.db, output=self.tmp / "out.csv", seeds=None,
            min_assets=3, only=["SOD Create"], interval=0.0, timeout=1.0, limit=0)
        self.assertEqual(run_script(self.module, args), 0)
        self.assertEqual(asked, ["https://www.sod.co.jp/"])
        from peach.review_csv import read_rows
        row = read_rows(self.tmp / "out.csv")[0]
        self.assertEqual(row["verdict"], "ok")
        self.assertEqual(row["final_url"], "https://www.sod.co.jp/")
        self.assertIn("用户", row["note"])


class AliasRunTests(unittest.TestCase):
    """别名要真的从账本走到判定里，不是只在 `site_verdict` 的参数上存在。"""

    def setUp(self):
        self.module = load_module()
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "ledger.db"
        writer = sqlite3.connect(self.db)
        self.addCleanup(writer.close)
        writer.execute(
            "CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT)")
        writer.execute("CREATE TABLE asset(id INTEGER PRIMARY KEY, medium TEXT)")
        writer.execute("CREATE TABLE asset_entity(asset_id INTEGER, entity_id INTEGER)")
        writer.execute("CREATE TABLE entity_alias(entity_id INTEGER, alias TEXT)")
        writer.execute("INSERT INTO entity VALUES(1,'studio','东京热')")
        writer.executemany("INSERT INTO entity_alias VALUES(?,?)",
                           [(1, "东京热"), (1, "東京熱")])
        writer.commit()
        self.module.time = type("clock", (), {
            "sleep": lambda _self, seconds: None,
            "monotonic": lambda _self: 0.0,
        })()

    def test_a_seeded_site_is_confirmed_through_the_ledger_alias(self):
        """实测：账本规范名是简体「东京热」，`www.tokyo-hot.com` 的标题写的是「東京熱」。

        这家的域名推不出来（名字里没有拉丁字母），`--seeds` 是唯一入口；别名接不上的话，
        人工查到的地址取回 200 也只能写成未取得。规范名自己也登记在别名表里，判词不该
        因此写成「对上的是别名『东京热』」。
        """
        import argparse
        seeds = self.tmp / "seeds.csv"
        seeds.write_text("studio,site\n东京热,https://www.tokyo-hot.com/\n", encoding="utf-8")
        title = "年齢確認 | Tokyo-Hot 東京熱 無修正オリジナル徹底凌辱動画"
        self.module.probe = lambda url, timeout, **kwargs: (200, page(title), url)
        args = argparse.Namespace(
            db=self.db, output=self.tmp / "out.csv", seeds=seeds,
            min_assets=3, only=["东京热"], interval=0.0, timeout=1.0, limit=0)
        self.assertEqual(run_script(self.module, args), 0)
        from peach.review_csv import read_rows
        row = read_rows(self.tmp / "out.csv")[0]
        self.assertEqual(row["verdict"], "ok")
        self.assertEqual(row["final_url"], "https://www.tokyo-hot.com/")
        self.assertIn("東京熱", row["note"])
        self.assertNotIn("「东京热」", row["note"])


if __name__ == "__main__":
    unittest.main()
