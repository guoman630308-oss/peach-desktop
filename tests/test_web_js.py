"""跑真的 JS：`frontend/src/` 里的纯模块按行为验收，不按源码文本验收。

页面源断言（`test_web_ui.py`）能守住「这段代码还在」，守不住「它算得对」。
以 JAV 标题为例，
`self.assertPageContains("return it?.is_jav?name.replace(JAV_MEDIA_SUFFIX,''):name")`
这样的断言把三元表达式的写法一起写死，改成 `if` 就红，而真把条件写反了它照样绿。

所以纯模块（不碰 DOM、不读 state、输入输出都是值）改用 Node 直接执行：这里给输入
和期望，Node 那边 `import` 真模块、调真函数、把结果按 JSON 交回来。契约从此是
「同样的输入算出同样的结果」，实现怎么写都行。

只覆盖纯模块。碰 DOM 的那些没有 headless 浏览器就跑不了，继续留在页面源断言里，
不要为了「都能跑」给它们塞一层假 document——那验收的就成了那层假的。
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 用例以能力名定位源码，Node 直接运行纯模块。
SOURCES = {
    "core.js": ROOT / "frontend" / "src" / "core" / "index.ts",
    "tags.js": ROOT / "frontend" / "src" / "core" / "tags.ts",
    "jav-title.js": ROOT / "frontend" / "src" / "core" / "jav-title.ts",
    "filter-scroll.js": ROOT / "frontend" / "src" / "application" / "filter-scroll.js",
    "search-morph.js": ROOT / "frontend" / "src" / "application" / "search-morph.js",
}
NODE = shutil.which("node")

# Node 那边的入口。模块按绝对 file:// URL 引入，所以驱动脚本放临时目录也能找到它们；
# 用例从 argv 进来，期望值全留在 Python 这边，避免同一张表在两种语言里各写一份。
# TS 源码靠 Node 自带的类型剥离直接跑；源码里不带扩展名的相对 import 按 Vite 的口径补 `.ts` 或 `/index.ts`。
DRIVER = """
import { existsSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { extname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith('.') && context.parentURL?.endsWith('.ts') && !extname(specifier)) {
      const base = new URL(specifier, context.parentURL).href;
      for (const candidate of [base + '.ts', base + '/index.ts']) {
        if (existsSync(fileURLToPath(candidate))) return next(candidate, context);
      }
    }
    return next(specifier, context);
  },
});

const [, , payload] = process.argv;
const cases = JSON.parse(payload);
const cache = new Map();
const results = [];
for (const [module, fn, args] of cases) {
  try {
    if (!cache.has(module)) {
      cache.set(module, await import(pathToFileURL(module).href));
    }
    const target = cache.get(module)[fn];
    if (typeof target !== 'function' && typeof target !== 'object') {
      results.push({ ok: false, error: `${module} 没有导出 ${fn}` });
      continue;
    }
    results.push({ ok: true, value: typeof target === 'function' ? target(...args) : target });
  } catch (error) {
    results.push({ ok: false, error: String(error && error.stack || error) });
  }
}
process.stdout.write(JSON.stringify(results));
"""


@unittest.skipUnless(NODE, "没装 Node，纯模块的行为验收跳过")
class WebJsBehaviourTests(unittest.TestCase):
    def test_mobile_filter_scroll_has_direction_hysteresis_and_visible_resets(self):
        down = dict(y=120, origin=100, direction=1, free=True)
        jitter = dict(y=117, origin=120, direction=-1, free=True)
        cases = [
            [None, 100, True],
            [dict(y=100, origin=100, direction=0, free=False), 108, True],
            [down, 117, True],
            [jitter, 112, True],
            [down, 120, True],
            [down, -4, True],
            [down, 130, False],
            [down, 130, True, True],
        ]
        results = self.run_js([("filter-scroll.js", "filterScrollState", args) for args in cases])
        self.assertEqual([result["free"] for result in results],
                         [False, True, True, False, True, False, False, False])
        self.assertEqual(results[2]["origin"], 120)
        self.assertEqual(results[5]["y"], 0)

    def test_search_glass_morph_has_geometry_and_stays_in_the_viewport(self):
        css = (ROOT / "web/board.css").read_text(encoding="utf-8")
        easing = css.split("--spring-pane:", 1)[1].split(";", 1)[0]
        for viewport in (320, 390, 760):
            expanded = dict(left=48, top=14, width=viewport-56, height=36)
            compact = dict(left=viewport-124, top=14, width=36, height=36)
            interrupted = dict(left=80, top=12, width=180, height=40)
            for start, end in ((compact, expanded), (expanded, compact), (interrupted, compact)):
                with self.subTest(viewport=viewport, start=start):
                    frames = self.run_js([("search-morph.js", "searchMorphFrames",
                                          [start, end, expanded, 7, 16, easing, viewport])])[0]
                    self.assertGreater(len(frames), 2)
                    self.assertAlmostEqual(float(frames[0]["width"][:-2]), start["width"])
                    self.assertAlmostEqual(float(frames[-1]["width"][:-2]), end["width"])
                    self.assertGreater(max(float(frame["height"][:-2]) for frame in frames), 36)
                    for frame in frames:
                        x, y = (float(value[:-2]) for value in frame["translate"].split())
                        left = expanded["left"] + x
                        width = float(frame["width"][:-2])
                        height = float(frame["height"][:-2])
                        self.assertGreaterEqual(left, 8)
                        self.assertLessEqual(left + width, viewport - 8 + .001)
                        self.assertAlmostEqual(expanded["top"] + y + height/2, 32)
                        self.assertNotIn("opacity", frame)

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._driver = Path(cls._tmp.name) / "driver.mjs"
        cls._driver.write_text(DRIVER, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_js(self, cases):
        """按顺序执行 `[模块, 导出名, 参数列表]`，返回结果列表。"""
        located = [[SOURCES[module].as_posix(), fn, args] for module, fn, args in cases]
        done = subprocess.run(
            [NODE, str(self._driver), json.dumps(located)],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        if done.returncode != 0:
            self.fail(f"Node 跑不起来（exit {done.returncode}）：{done.stderr.strip()[:2000]}")
        results = json.loads(done.stdout)
        for case, result in zip(cases, results):
            if not result["ok"]:
                self.fail(f"{case[0]}.{case[1]}{tuple(case[2])} 抛异常：{result['error'][:800]}")
        return [result["value"] for result in results]

    def assertJsResults(self, table):
        """`table` 是 `(模块, 导出名, 参数, 期望)`，一次全跑完再逐条比。

        一次起一个 Node 进程要几百毫秒，逐条起会把这个文件拖成十几秒。
        """
        cases = [[module, fn, list(args)] for module, fn, args, _ in table]
        for (module, fn, args, want), got in zip(table, self.run_js(cases)):
            self.assertEqual(got, want, f"{module}.{fn}{tuple(args)}")

    # ── JAV 标题 ────────────────────────────────────────────────────────────

    def test_media_suffix_is_stripped_only_for_jav(self):
        # 番号作品的文件名后缀是噪声；创作者作品的文件名里点号可能有意义，
        # 一律剥会把 `clip.2024.1080p` 削短。
        self.assertJsResults([
            ("jav-title.js", "javFileDisplayName",
             [{"is_jav": True, "name": "ABC-123.mp4"}], "ABC-123"),
            ("jav-title.js", "javFileDisplayName",
             [{"is_jav": True, "name": "ABC-123.RMVB"}], "ABC-123"),
            ("jav-title.js", "javFileDisplayName",
             [{"is_jav": False, "name": "clip.2024.1080p.mp4"}],
             "clip.2024.1080p.mp4"),
            ("jav-title.js", "javFileDisplayName", [None], ""),
        ])

    def test_official_title_prefers_the_japanese_one(self):
        # catalog_title 有时是英文机翻，original_title 才是原始日文；两条都没日文
        # 就按顺序取第一条，都没有就空。
        self.assertJsResults([
            ("jav-title.js", "javPreferredTitle",
             [{"catalog_title": "Some English", "original_title": "日本語タイトル"}],
             "日本語タイトル"),
            ("jav-title.js", "javPreferredTitle",
             [{"catalog_title": "Some English", "original_title": ""}],
             "Some English"),
            ("jav-title.js", "javPreferredTitle", [{}], ""),
        ])

    def test_code_prefix_is_taken_off_the_title_but_only_when_it_is_a_prefix(self):
        self.assertJsResults([
            # 番号 + 分隔符开头的才算前缀，剥掉之后再清掉残留的分隔符。
            ("jav-title.js", "javTitleParts",
             [{"is_jav": True, "code": "ABC-123", "name": "ABC-123 一个标题"}],
             {"code": "ABC-123", "title": "一个标题", "badges": []}),
            ("jav-title.js", "javTitleParts",
             [{"is_jav": True, "code": "ABC-123", "name": "ABC-123.一个标题.mp4"}],
             {"code": "ABC-123", "title": "一个标题", "badges": []}),
            # 番号只是恰好出现在开头、后面直接接字母，那不是前缀，别切。
            ("jav-title.js", "javTitleParts",
             [{"is_jav": True, "code": "ABC", "name": "ABCDEF 别切我"}],
             {"code": "ABC", "title": "ABCDEF 别切我", "badges": []}),
            # display_code 是对外显示的写法，前缀判定和输出都用它。
            ("jav-title.js", "javTitleParts",
             [{"is_jav": True, "code": "ABC123", "display_code": "ABC-123",
               "name": "ABC-123 标题"}],
             {"code": "ABC-123", "title": "标题", "badges": []}),
            # 不是 JAV，或者没有番号，就只有一个标题，连 badges 字段都不给。
            ("jav-title.js", "javTitleParts",
             [{"is_jav": False, "name": "普通作品"}],
             {"code": "", "title": "普通作品"}),
            ("jav-title.js", "javTitleParts",
             [{"is_jav": True, "code": "", "name": "没番号"}],
             {"code": "", "title": "没番号"}),
        ])

    def test_an_explicitly_empty_display_title_is_an_answer_not_a_miss(self):
        # API 返回空 display_title 是「清洗后确认无标题」，不是「没查到」。
        # 回退到脏文件名等于把清洗结果丢掉——这条踩过。
        self.assertJsResults([
            ("jav-title.js", "javTitleParts",
             [{"is_jav": True, "code": "ABC-123", "display_title": "",
               "name": "ABC-123 hhd800.com@脏文件名"}],
             {"code": "ABC-123", "title": "", "badges": []}),
            ("jav-title.js", "javTitleParts",
             [{"is_jav": True, "code": "ABC-123", "display_title": "干净标题",
               "name": "ABC-123 脏文件名"}],
             {"code": "ABC-123", "title": "干净标题", "badges": []}),
            # 键不存在才回退到文件名推出来的标题。
            ("jav-title.js", "javTitleParts",
             [{"is_jav": True, "code": "ABC-123", "name": "ABC-123 文件名标题"}],
             {"code": "ABC-123", "title": "文件名标题", "badges": []}),
            # 官方标题优先于任何 display_title。
            ("jav-title.js", "javTitleParts",
             [{"is_jav": True, "code": "ABC-123", "display_title": "干净标题",
               "original_title": "日本語", "name": "ABC-123"}],
             {"code": "ABC-123", "title": "日本語", "badges": []}),
        ])

    def test_only_the_three_edition_badges_survive(self):
        # 版本徽章是白名单：其它 edition_badges（画质、分卷之类）不进标题行。
        self.assertJsResults([
            ("jav-title.js", "javTitleParts",
             [{"is_jav": True, "code": "ABC-123", "name": "ABC-123 标题",
               "edition_badges": ["中字", "4K", "无码破解"]}],
             {"code": "ABC-123", "title": "标题", "badges": ["中字", "无码破解"]}),
            ("jav-title.js", "javDisplayName",
             [{"is_jav": True, "code": "ABC-123", "name": "ABC-123 标题",
               "edition_badges": ["中字"]}],
             "ABC-123 中字 标题"),
            # 没标题就只剩番号，不留下尾随空格。
            ("jav-title.js", "javDisplayName",
             [{"is_jav": True, "code": "ABC-123", "display_title": "",
               "name": "ABC-123"}],
             "ABC-123"),
        ])

    def test_title_html_escapes_everything_it_interpolates(self):
        # 标题来自刮削结果，是不可信输入。番号、徽章、标题三处都得转义。
        self.assertJsResults([
            ("jav-title.js", "javTitleHtml",
             [{"is_jav": False, "name": "<script>x</script>"}],
             "&lt;script&gt;x&lt;/script&gt;"),
            ("jav-title.js", "javTitleHtml",
             [{"is_jav": True, "code": "ABC-123", "display_title": "<b>x</b>",
               "name": "ABC-123"}],
             '<span class="ui-javidentity"><strong class="ui-javcode">ABC-123</strong>'
             '</span> <span class="ui-javtitle">&lt;b&gt;x&lt;/b&gt;</span>'),
            ("jav-title.js", "javTitleHtml",
             [{"is_jav": True, "code": "ABC-123", "display_title": "",
               "name": "ABC-123", "edition_badges": ["无码"]}],
             '<span class="ui-javidentity"><strong class="ui-javcode">ABC-123</strong>'
             '<small class="javedition uncensored">无码</small></span>'),
        ])

    # ── 标签显示名 ──────────────────────────────────────────────────────────

    def test_tag_labels_only_rename_and_never_invent(self):
        # 映射只做「显示名」，不做同义词归并：账本里的标签名是真相。查不到就原样
        # 返回，绝不能顺手补一个机翻后缀（`深喉` 曾被显示成 `深喉咙`）。
        self.assertJsResults([
            # `足交` 是通行写法，不改成 `脚交`。
            ("tags.js", "tagLabel", ["足交"], "足交"),
            ("tags.js", "tagLabel", ["美腿"], "美腿"),
            ("tags.js", "tagLabel", ["1080P"], "1080p"),
            ("tags.js", "tagLabel", ["OL制服"], "OL制服"),
            ("tags.js", "tagLabel", ["JK制服"], "JK制服"),
            ("tags.js", "tagLabel", ["秘书OL"], "秘书OL"),
            ("tags.js", "tagLabel", ["深喉"], "深喉"),
            ("tags.js", "tagLabel", ["无码"], "无码"),
            ("tags.js", "tagLabel", ["没有别名的标签"], "没有别名的标签"),
            ("tags.js", "tagLabel", [""], ""),
        ])

    # ── core 里的格式化 ─────────────────────────────────────────────────────

    # ── 链接与路径 ──────────────────────────────────────────────────────────

    def test_the_link_mark_endpoint_only_ever_carries_an_id(self):
        # 让前端把地址递给服务端去取，等于开一个任意地址抓取的口子。和
        # `/follow-stream` 同一条规矩：服务端只认账本里已有的链接 id。
        results = self.run_js([
            ["core.js", "linkMarkUrl", [{"link_id": 7}]],
            # `?? ''` 不是 `|| ''`：id 为 0 是合法的行号，不能被当成空。
            ["core.js", "linkMarkUrl", [{"link_id": 0}]],
            ["core.js", "linkMarkUrl", [{}]],
            # 就算调用方把整条链接连 url 一起递进来，出去的也只有 id。
            ["core.js", "linkMarkUrl", [{"link_id": 3, "url": "https://x.com/a"}]],
        ])
        self.assertEqual(results, ["/link-mark?id=7", "/link-mark?id=0",
                                   "/link-mark?id=", "/link-mark?id=3"])
        for url in results:
            self.assertNotIn("url=", url, "外链图标端点不得接受前端给的地址")

    def test_brand_marks_cover_the_host_and_its_subdomains_only(self):
        # 名单按图标库的覆盖面定，不按链接条数：Phosphor 有字形的常见社媒一家一枚配齐，
        # 库里没有的（livedoor、ameblo、lit.link 这类）才走 `/link-mark` 按站点图标合成。
        self.assertJsResults([
            ("core.js", "brandIcon", ["https://x.com/remu"], "brand-x"),
            ("core.js", "brandIcon", ["https://www.twitter.com/remu"], "brand-x"),
            ("core.js", "brandIcon", ["https://mobile.x.com/remu"], "brand-x"),
            ("core.js", "brandIcon", ["https://www.instagram.com/arina_hashimoto1215"], "brand-instagram"),
            ("core.js", "brandIcon", ["https://www.threads.com/@remu"], "brand-threads"),
            ("core.js", "brandIcon", ["https://www.threads.net/@remu"], "brand-threads"),
            ("core.js", "brandIcon", ["https://www.tiktok.com/@remu"], "brand-tiktok"),
            ("core.js", "brandIcon", ["https://www.youtube.com/@remu"], "brand-youtube"),
            ("core.js", "brandIcon", ["https://youtu.be/abc"], "brand-youtube"),
            ("core.js", "brandIcon", ["https://www.facebook.com/remu"], "brand-facebook"),
            ("core.js", "brandIcon", ["https://linktr.ee/remu"], "brand-linktree"),
            # 只认后缀边界：`notx.com` 不是 `x.com` 的子域。
            ("core.js", "brandIcon", ["https://notx.com/remu"], ""),
            # 图标库里没有这几家，它们继续走 `/link-mark`。
            ("core.js", "brandIcon", ["https://blog.livedoor.jp/remu"], ""),
            ("core.js", "brandIcon", ["https://lit.link/remu"], ""),
            ("core.js", "brandIcon", ["https://example.com/a"], ""),
            # 坏地址返回空串而不是抛异常——资料页的链接来自刮削，什么都可能有。
            ("core.js", "brandIcon", ["不是网址"], ""),
            ("core.js", "brandIcon", [None], ""),
        ])

    def test_a_site_reads_as_the_short_name_people_actually_say(self):
        # 事务所和片商平时就被叫作 NAX、T-POWERS、DMM；子域照样命中主域那一条。
        self.assertJsResults([
            ("core.js", "siteName", ["https://official.nax-pro.com/model/14717"], "NAX"),
            ("core.js", "siteName", ["https://www.T-Powers.co.jp/"], "T-POWERS"),
            ("core.js", "siteName", ["https://video.dmm.co.jp/av/list/?id=1"], "DMM"),
            # 表里没有这一家时交回空串，调用点退回账本里的 label。
            ("core.js", "siteName", ["https://example.com/a"], ""),
            ("core.js", "siteName", ["不是网址"], ""),
        ])

    def test_a_company_page_calls_its_own_site_the_official_site(self):
        """厂牌页与事务所页上指回自家站的链接写「官方网站」，指向别家的保留那家的名字；人物页照旧写名字。"""
        light = {"url": "https://lightpro.jp/", "label": "LIGHT"}
        nax = {"url": "https://official.nax-pro.com/", "label": "New Actor eXperience"}
        self.assertJsResults([
            ("core.js", "officialLinkText",
             [{"url": "https://www.km-produce.com/", "label": "K M Produce"}, "studio",
              ["K M Produce", "KMP"]], "官方网站"),
            ("core.js", "officialLinkText",
             [{"url": "https://cmore.jp/", "label": "C-more Entertainment"}, "studio",
              ["C-more Entertainment"]], "官方网站"),
            # 别名也算自家：合并进来的旧名下装的链接。
            ("core.js", "officialLinkText",
             [{"url": "https://example.jp/", "label": "Studio CLAP"}, "studio",
              ["C-more Entertainment", "Studio CLAP"]], "官方网站"),
            # 短名是缩写时认 label；label 只是主机名等于没起名。
            ("core.js", "officialLinkText", [nax, "studio", ["New Actor eXperience"]], "官方网站"),
            ("core.js", "officialLinkText",
             [{"url": "http://www.ran-maru.com", "label": "www.ran-maru.com"}, "studio", ["乱丸"]], "官方网站"),
            ("core.js", "officialLinkText",
             [{"url": "https://kirakira-av.com/", "label": "www.kirakira-av.com"}, "studio",
              ["kira*kira"]], "官方网站"),
            ("core.js", "officialLinkText",
             [{"url": "https://bangbros.com/websites/BangBus", "label": "BangBros"}, "studio",
              ["BangBus"]], "BangBros"),
            ("core.js", "officialLinkText",
             [{"url": "https://www.prestige-av.com/goods/", "label": "Prestige"}, "studio",
              ["Jackson"]], "Prestige"),
            ("core.js", "officialLinkText", [light, "agency", ["LIGHT", "RIGHT"]], "官方网站"),
            ("core.js", "officialLinkText", [nax, "agency", ["New Actor eXperience"]], "官方网站"),
            ("core.js", "officialLinkText",
             [{"url": "https://old-crew.blog.jp/", "label": "官方博客"}, "agency",
              ["Office Old Crew"]], "官方博客"),
            # 事务所页上链到别家（拆分出来的 EST 链着 LIGHT 的旧站）保留那家的名字。
            ("core.js", "officialLinkText", [light, "agency", ["EST", "エスト"]], "LIGHT"),
            ("core.js", "officialLinkText", [light, "performer", ["松本一香"]], "LIGHT"),
            # 存档快照照写 label，厂牌页上也不改写成「官方网站」。
            ("core.js", "officialLinkText",
             [{"url": "https://web.archive.org/web/20220512171503/https://ones-double.com/people/",
               "label": "官网存档（2022-05）"}, "studio", ["ONE'S DOUBLE"]],
             "官网存档（2022-05）"),
        ])

    def test_a_site_mark_carries_a_key_and_never_a_url(self):
        """站点圆标的地址里只出现服务端认得的键，任何一段都不是对方站点的地址。

        拼出对方站点的地址就等于让浏览器自己去连它：那既拿不到站点备好的大图标，
        又把「正在看哪些站」这件事按站分发出去。参数值该转义的照转，`a b.com`
        这种进来也只能是查询串里的一个值，越不出 `/site-mark` 这个路径。
        """
        self.assertJsResults([
            ("core.js", "siteMarkUrl", [{"source": "mgstage"}], "/site-mark?source=mgstage"),
            ("core.js", "siteMarkUrl", [{"domain": "kemono.cr"}], "/site-mark?domain=kemono.cr"),
            ("core.js", "siteMarkUrl", [{"domain": "a b.com"}], "/site-mark?domain=a+b.com"),
        ])

    def test_entity_paths_are_semantic_and_survive_slashes_in_names(self):
        # 女优名字里有斜杠，不编码会把路径切成多段。
        self.assertJsResults([
            ("core.js", "entityPath", ["performer", "波多野結衣"],
             "/performers/%E6%B3%A2%E5%A4%9A%E9%87%8E%E7%B5%90%E8%A1%A3"),
            ("core.js", "entityPath", ["performer", "A/B"], "/performers/A%2FB"),
            ("core.js", "entityPath", ["studio", "S1"], "/studios/S1"),
            ("core.js", "entityPath", ["series", "X"], "/series/X"),
            # 没登记的种类原样当路径段用，不静默变成 undefined。
            ("core.js", "entityPath", ["新种类", "X"], "/新种类/X"),
        ])

    def test_catalog_includes_home_state_and_wall_routes(self):
        # 「这条路径算不算馆藏列表」决定了状态标题、筛选栏和高亮；管理页不算。
        self.assertJsResults([
            ("core.js", "isCatalogPath", ["/"], True),
            ("core.js", "isCatalogPath", ["/wall"], True),
            ("core.js", "isCatalogPath", ["/unseen"], True),
            ("core.js", "isCatalogPath", ["/junk-files"], True),
            ("core.js", "isCatalogPath", ["/stats"], False),
            ("core.js", "isCatalogPath", ["/trash"], False),
            # 原型链上的属性名不许算命中——这就是它用 hasOwnProperty 的原因。
            ("core.js", "isCatalogPath", ["constructor"], False),
            ("core.js", "isCatalogPath", ["toString"], False),
        ])

    def test_folded_names_compare_across_width_case_and_padding(self):
        # 别名比对用它：全角、大小写、首尾空白都不该让同一个名字变成两个。
        self.assertJsResults([
            ("core.js", "foldName", ["  Remu  "], "remu"),
            ("core.js", "foldName", ["ＲＥＭＵ"], "remu"),
            ("core.js", "foldName", [None], ""),
        ])

    def test_the_minus_one_duration_sentinel_never_becomes_a_duration(self):
        """账本里的 `-1` 是探测硬失败的哨兵，不是时长。

        用户实测 /item/86287（ABF-234-UN.mp4，duration=-1）：播放器顶上标着「直播」，
        总时长显示 `0:NaN`。链条是——`Number(it.duration)||0` 对 -1 求值仍是 -1，通过了
        真值判断，于是强行 `player.duration(-1)`；而 Video.js 的 setter 写着
        `parseFloat(e)<0 ? Infinity : e`，随后 `=== Infinity` 就 `addClass("vjs-live")`。
        一部本地影片因此被当成直播流。`fmtDur(-1)` 同样算过 `0:-1`，用户在卡片上看到过。

        所以判据是「有限且大于零」，不是「非空」。这条直接拿 -1 去问函数，不比对
        源码文本——把三元换成 if 不该让它红，把条件写反才该红。
        """
        self.assertJsResults([
            ("core.js", "realDuration", [-1], 0),
            ("core.js", "realDuration", [0], 0),
            ("core.js", "realDuration", [None], 0),
            ("core.js", "realDuration", ["不是数字"], 0),
            ("core.js", "realDuration", [12.5], 12.5),
            ("core.js", "fmtDur", [-1], "—"),
        ])

    def test_formatters_round_the_way_the_ui_reads_them(self):
        self.assertJsResults([
            ("core.js", "esc", ["<a href='x'>&"], "&lt;a href=&#39;x&#39;&gt;&amp;"),
            ("core.js", "esc", [None], ""),
            # 时长缺失显示破折号，不显示 0:00——那会被读成「零秒的视频」。
            ("core.js", "fmtDur", [0], "—"),
            ("core.js", "fmtDur", [None], "—"),
            ("core.js", "fmtDur", [-5], "—"),
            ("core.js", "fmtDur", [59], "0:59"),
            ("core.js", "fmtDur", [61], "1:01"),
            ("core.js", "fmtDur", [3661], "1:01:01"),
            # 48 小时起按天读：「1284:00:00」没人一眼读得出是五十多天。
            ("core.js", "fmtDur", [48 * 3600 - 1], "47:59:59"),
            ("core.js", "fmtDur", [48 * 3600], "2 天"),
            ("core.js", "fmtDur", [4622400], "53 天 12 小时"),
            # fmtClock 是播放器时间轴，0 秒是真的 0 秒。
            ("core.js", "fmtClock", [0], "0:00"),
            ("core.js", "fmtClock", [-5], "0:00"),
            ("core.js", "fmtSize", [1024 * 1024], "1 MB"),
            ("core.js", "fmtSize", [3 * 1024 * 1024 * 1024], "3.0 GB"),
            ("core.js", "fmtSize", [2 * 1024 ** 4], "2.00 TB"),
            ("core.js", "fmtSize", [10 * 1024 ** 5], "10.00 PB"),
            # 不到 1 MB 写 KB，1 字节也至少是 1 KB，不读成「0 MB」。
            ("core.js", "fmtSize", [1], "1 KB"),
            ("core.js", "fmtSize", [512 * 1024], "512 KB"),
            # 0 是真的 0（可回收空间、已下载量）；缺值与坏值是未知。
            ("core.js", "fmtSize", [0], "0 B"),
            ("core.js", "fmtSize", [None], "大小未知"),
            ("core.js", "fmtSize", ["不是数字"], "大小未知"),
            ("core.js", "fmtSize", [-1], "大小未知"),
            # 首字母按字素取：emoji、肤色与 ZWJ 组合整枚取，不劈成半个代理对。
            ("core.js", "firstGrapheme", ["🦊 Fox"], "🦊"),
            ("core.js", "firstGrapheme", ["👩🏽‍💻 Priya"], "👩🏽‍💻"),
            ("core.js", "firstGrapheme", ["  佐藤"], "佐"),
            ("core.js", "firstGrapheme", [""], "?"),
            ("core.js", "firstGrapheme", [None, ""], ""),
            ("core.js", "leadingGraphemes", ["🇯🇵Studio", 2], "🇯🇵S"),
            ("core.js", "clipGraphemes", ["🦊" * 20, 5], "🦊🦊🦊🦊…"),
            ("core.js", "clipGraphemes", ["短名", 5], "短名"),
        ])


if __name__ == "__main__":
    unittest.main()
