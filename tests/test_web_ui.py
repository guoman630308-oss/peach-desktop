import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


def stylesheet_source() -> str:
    """`web/css/` 的全部分区按层叠顺序拼起来，等于 `/app.css` 交付的那一份。

    样式表按分区拆在 `web/css/` 下、由 `stylesheet_response()` 拼成一份交付，但断言
    守的仍是整份样式表这一个契约：分区边界只是文件边界，选择器和 token 要跨分区看。
    目录用 glob 而不是写死清单，再切出新分区时不必回头改这里。
    """
    web = Path(__file__).resolve().parents[1] / "web"
    return "".join(path.read_text(encoding="utf-8")
                   for path in sorted((web / "css").glob("*.css")))


class StylesheetPartitionTests(unittest.TestCase):
    """样式表分区：清单、层叠顺序，以及每份分区自身必须是完整的 CSS。

    拆分的全部目的是让两处改动落在不同文件上，不改变交付的字节。所以这里守两件事：
    清单和顺序不许悄悄变，切口不许落在规则或注释中间。
    """

    #: 层叠顺序就是这个顺序。加分区要同时改这里——glob 出来的新文件会自动进
    #: `stylesheet_source()`，但插在哪一档决定谁覆盖谁，那是判断而不是发现。
    PARTITIONS = (
        "01-base.css", "02-topbar.css", "03-filterbar.css", "04-manage.css",
        "05-insights.css", "06-index.css", "07-entity.css", "08-photos.css",
        "09-skeleton.css", "11-identity.css", "12-cards.css",
        "15-detail.css", "16-settings.css",
        "17-overlay.css", "18-chips.css", "19-immersive.css",
        "21-online.css", "22-followmanage.css", "23-configuration.css",
        "25-motion.css",
    )

    @classmethod
    def setUpClass(cls):
        cls.web = Path(__file__).resolve().parents[1] / "web"

    def test_partitions_are_the_pinned_set_in_cascade_order(self):
        names = [path.name for path in sorted((self.web / "css").glob("*.css"))]
        self.assertEqual(tuple(names), self.PARTITIONS)
        # 两位数前缀不是装饰：文件名排序就是层叠顺序，`stylesheet_response()` 只做
        # `sorted()`。少了前缀的文件会插到任意位置，样式表照样加载，只是错。
        for name in names:
            self.assertRegex(name, r"^\d{2}-[a-z0-9-]+\.css$")
        self.assertFalse((self.web / "app.css").exists(),
                         "整份 app.css 已经拆成 web/css/ 下的分区，不该再有这个文件")

    def test_each_partition_closes_its_own_braces_and_comments(self):
        """切口只许落在花括号深度 0、注释之外。

        规则或 `@media` 被切成两半时，拼起来仍然完全正确——两份分区各自都不是合法
        CSS，却只有单独看每一份才能发现。注释同理：`/*` 留在上一份、`*/` 落到下一份，
        中间那份的规则会被后来的编辑当成生效内容去改，实际上整段是注释。
        """
        for path in sorted((self.web / "css").glob("*.css")):
            depth, in_comment = 0, False
            body = path.read_text(encoding="utf-8")
            index = 0
            while index < len(body):
                if in_comment:
                    end = body.find("*/", index)
                    if end < 0:
                        break
                    in_comment, index = False, end + 2
                    continue
                if body.startswith("/*", index):
                    in_comment, index = True, index + 2
                    continue
                char = body[index]
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    # 先 `}` 再 `{` 的分区最终深度仍是 0，只有逐个字符看才会露出来。
                    self.assertGreaterEqual(depth, 0, f"{path.name} 多出一个右花括号")
                index += 1
            self.assertEqual(depth, 0, f"{path.name} 有没闭合的花括号，切口落在规则中间")
            self.assertFalse(in_comment, f"{path.name} 有没闭合的注释，切口落在注释中间")


class WebUiPolicyTests(unittest.TestCase):
    """壳层通用样式规范与隐私边界；页面行为由组件和浏览器测试验证。"""

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        web = root / "web"
        markup = [web / "index.html", *sorted(path for path in (root / "frontend" / "src").rglob("*")
                                             if path.suffix in {".js", ".ts", ".tsx"})]
        cls.css = stylesheet_source()
        cls.markup_files = markup
        cls.markup = "\n".join(path.read_text(encoding="utf-8") for path in markup)

    def test_primary_button_fill_is_owned_by_board_styles(self):
        # Board 覆盖层统一负责主按钮面色；基样式中的重复规则会竞争层叠优先级。
        self.assertNotIn(".geist-button.primary{", self.css)

    def assertPageLacks(self, needle: str, message: str = ""):
        for path in self.markup_files:
            with self.subTest(path=path):
                self.assertNotIn(needle, path.read_text(encoding="utf-8"), message)

    def test_every_font_size_comes_from_the_one_type_scale(self):
        """全站只有一套字号刻度，任何写死的像素都要有理由。

        收敛之前样式表里散着 21 种字号（9…48px），相邻两档常常只差半个像素——
        既排不出层级，也没法复核「这里为什么是 12.5」。现在一律走 `--fs-*`。

        唯一的例外是移动端输入框那条 `16px!important`：那是 iOS 的自动放大阈值，
        不是刻度里的一档。让它跟着 `--fs-lg` 走的话，将来把 lg 调成 17 或 15
        都会悄悄破坏那个保护，而症状（在 iPhone 上聚焦输入框页面猛地放大）
        跟字号改动看不出任何关系。
        """
        css = stylesheet_source()
        literals = re.findall(r"font(?:-size)?:(?:\d+ )?([\d.]+)px", css)
        self.assertEqual(literals, ["16"],
                         f"除 iOS 防放大的 16px 外不该有写死字号，实际 {literals}")
        declared = re.findall(r"--fs-([a-z0-9]+):(\d+)px", css)
        self.assertEqual(declared,
                         [("xs", "12"), ("sm", "13"), ("md", "14"), ("lg", "16"),
                          ("xl", "20"), ("2xl", "24"), ("3xl", "32"), ("4xl", "48")])
        # 下限是 12px：更小的灰字在 vercel-report-design 里被点名为要拒绝的反射。
        self.assertNotIn("--fs-", css.split("--fs-xs")[0][-40:],
                         "刻度必须从 --fs-xs 开始，别在前面塞更小的档")

    TUNGSTEN_ALLOWED_SELECTORS = (
        ":focus",              # 焦点环：:focus / :focus-visible / :focus-within
        ".watchprogress", ".vjs-play-progress", ".vjs-progress-holder",  # 进度与数据
        ".ptoggle:checked",  # Toggle 开态：Geist Toggle 实测轨道 rgb(0,112,243)
        ".flink", ".taste-history-guide-content a",  # 真正的链接
        ".vjs-subtitles-control>button[aria-pressed=true]:after", "[data-player-quality-badge]",  # 播放器字幕开态与清晰度
    )

    def test_tungsten_is_reserved_for_focus_links_progress_and_toggle(self):
        """蓝色只给焦点环、链接、进度／数据和 Toggle 开态，选中态与主动作一律反相墨色。

        `vercel-report-design`（vercel.com/design.md）要求「Design in monochrome」，颜色只在
        对状态、动作或数据有显著意义时才用，并配非颜色线索。2026-09-03 实测 Geist：Tabs
        选中是墨色文字加 2px 墨色下划线，Switch 选中是抬起一档的灰面，Checkbox 选中是墨色
        勾，主按钮是 #EDEDED 底 #0A0A0A 字——都没有蓝；只有 Toggle 开态轨道是 rgb(0,112,243)。
        收敛前 Peach 有两套强调色：筛选 pill 选中反相成白，其它 40 多处选中／主按钮／悬停
        却是蓝，同一页上「被选中」和「可以按」长得一样。
        """
        css = stylesheet_source()
        self.assertNotIn("--tungsten-soft", css, "蓝色浅底 token 已退役，不得再引入")
        offenders = []
        selected_with_blue = []
        for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
            selector, body = match.group(1).strip(), match.group(2)
            if "tungsten" not in body or "--tungsten:" in body:
                continue
            leaf = selector.split("{")[-1]  # 去掉 @media 前缀
            if any(token in leaf for token in self.TUNGSTEN_ALLOWED_SELECTORS):
                continue
            offenders.append(leaf)
            if any(state in leaf for state in (
                    'aria-pressed="true"', "aria-current", ".selected", ".current",
                    ".picked", ":checked", ".primary")):
                selected_with_blue.append(leaf)
        self.assertEqual(selected_with_blue, [],
                         f"选中态与主动作不得用蓝，改用 --ink／--ink-2 反相：{selected_with_blue}")
        self.assertEqual(offenders, [],
                         f"这些规则的 --tungsten 不在允许的焦点／链接／进度／Toggle 之列：{offenders}")

    INVERTED_PRESSED_ALLOWED = (
        ".hovertools .laterbtn",   # 卡片悬停浮层「稍后看」
        ".followimagedots button",  # 图集页码点
    )

    HOVER_FILL_ALLOWED = (
        ".ib",              # 顶栏图标按钮，八个里只有一个有按下态
        ".brandpill",       # 顶栏厂牌胶囊，全站一颗
        ".playerstatsbtn",  # 播放器覆盖层，悬停走 ::after 另一层
        ".fb .like",        # 这一排彩色反馈按钮的既有约定就是悬停预览按下后的颜色
        ".popmenu.gselectmenu button",  # 同上；2026-09-04 实测 vercel.com 后台的菜单行，悬停与选中共用同一枚 5% 填充
    )

    STATE_TOKENS = ('[aria-pressed="true"]', '[aria-selected="true"]',
                    '[aria-current="page"]', '[aria-current="true"]', ".selected")

    SELECTED_ON_GROUND = (
        '.chip[aria-pressed="true"]',                     # 产地选择站在对话框的 --ground 上
        '.popmenu.gselectmenu button[aria-selected="true"]',  # 浮层菜单填 --ground
        '.ib[aria-pressed="true"]',                       # 顶栏填 --ground
    )

    def test_font_weights_stay_on_the_three_geist_steps(self):
        """字重只有 400／500／600 三档。

        `vercel-report-design`（vercel.com/design.md）明说不要自造数字字重，Geist 本身
        也只发 regular／medium／semibold。收敛前样式表里有 550、650、700、750、800
        五种自造值，同一级标题在不同页面粗细不一，却没有任何一处能说出「为什么这里是 650」。
        """
        css = stylesheet_source()
        weights = sorted(set(re.findall(r"font-weight:\s*([^;}]+)", css)))
        self.assertEqual(weights, ["400", "500", "600"],
                         f"字重只能是三档之一，实际出现 {weights}")

    def test_every_border_radius_comes_from_the_radius_vocabulary(self):
        """圆角只有五个语义 token，加上 0 与 50%。

        收敛前样式表写着 1、2、3、5、7、9、10、11、14、16、18、24、28、40px 等
        二十来种字面圆角，相邻两档差一像素，谁也说不清 7 和 8 的区别。现在：
        `--badge-radius` 标记、`--control-radius` 控件、`--surface-radius` 不浮起的
        内嵌表面、`--floating-radius` 浮层与卡片、`--pill-radius` 连续的条与胶囊，
        圆形用 50%。嵌在带边框容器里的头尾条用 `calc(token - 1px)` 保持同心。
        """
        css = stylesheet_source()
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        token = (r"(?:0|50%|inherit|var\(--(?:badge|control|surface|floating|pill|tag)-radius\)"
                 r"|calc\(var\(--(?:surface|floating)-radius\) - 1px\))")
        allowed = re.compile(rf"^{token}(?: {token}){{0,3}}(?:!important)?$")
        offenders = sorted(
            value.strip() for value in re.findall(r"border-radius:\s*([^;}]+)", css)
            if not allowed.match(value.strip()))
        self.assertEqual(offenders, [], f"圆角不在词汇表里：{offenders}")
        self.assertIn("--surface-radius:8px", css)

    def test_every_class_selector_in_the_stylesheet_is_actually_used(self):
        """样式表里的每个类选择器都要有人用它。

        没人用的规则不会报错，只会一直被读、被改、被当成「现在的样子」来推理。
        实测一次就清出九组：`.fdetails` 那一整套折叠摘要、`.mediatabs`、
        `.edge .srcrow`、`.javhint`、`.photosets`、`.meta .whosep`、`.reviewhead`、
        `.resourceerror`——对应的 JS 早就删了或改了名，样式留在原地。

        两类例外，都必须是「前缀 + 运行时拼出来的一段」，不接受逐个类名的豁免：
        vendor 在运行时自己加的类（Video.js、Swiper），以及模板里用模板串拼出来的
        类名（`geist-note-${kind}` 这种，源码里不会出现完整的 `geist-note-warning`）。

        查的是页面上交付的全部旧样式：`web/css/` 的分区加 `web/board.css`。只出现在
        `:not(.x)` 里的类不要求有人挂——没人挂时那一项照样匹配，删掉它反而会降低整条
        选择器的特指度。
        """
        board = (Path(__file__).resolve().parents[1] / "web" / "board.css").read_text(encoding="utf-8")
        # 注释里会写类名当例子，`url()` 里的域名（www.w3.org）会被当成 `.org`。
        css = re.sub(r"/\*.*?\*/", "", self.css + board, flags=re.S)
        css = re.sub(r"url\([^)]*\)", "url()", css)
        css = re.sub(r":not\(\.[A-Za-z_][A-Za-z0-9_-]*\)", "", css)
        selectors = set(re.findall(r"\.(-?[A-Za-z_][A-Za-z0-9_-]*)", css))
        vendor = ("vjs-", "swiper-")
        composed = ("geist-note-",)
        # 前缀豁免要能兑现：拼接那一处必须真的在模板里。
        for prefix in composed:
            self.assertIn(prefix, self.markup, f"{prefix} 已经没人拼了，连同规则一起删")
        # 按整词找：`entitylink` 不能靠 `entitylinks` 算有人用。
        unused = sorted(
            name for name in selectors
            if not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", self.markup)
            and not name.startswith(vendor + composed))
        self.assertEqual(unused, [], f"样式表里有没人用的类选择器：{unused}")

    def test_studio_metadata_is_not_compiled_as_inline_javascript(self):
        self.assertPageLacks('onerror="this.parentNode.innerHTML=')
        self.assertPageLacks('onload="if(this.naturalWidth')

    def test_image_fallbacks_are_declarative_data_not_inline_handlers(self):
        """`<img>` 的回退链以 data 属性声明，模板不编译事件处理器。"""
        self.assertPageLacks(' onerror="', "模板不能包含内联 onerror 属性")
        # 捕获阶段的委托监听与「没有 `data-drop` 的 <img> 一概不动」由
        # `frontend/test/card-art/image-fallback.test.ts` 跑真元素验收；页面上另有一批
        # 靠 CSS 或父节点兜底的图（厂牌 `.mk`），把它们删掉反而是错的。壳自己拼的图也走同一套声明。

    def test_no_site_icon_is_fetched_by_the_browser_from_the_site_itself(self):
        """站点图标全部由本机给：浏览器不向对方站点要图，也不问第三方图标代理。

        外链的 favicon 是向对方站点发出的真实请求，打开一位女优的资料页就会把 Peach
        的页面地址报给每一个被链接的站点；第三方代理那一跳则把这一列里的每个站逐个
        报出去。资料页外链走 `/link-mark`，采集页和口味页走 `/site-mark`，是同一套
        挑图、合成与缓存；资料卡那一排由 entity-hero.test.tsx 钉住。
        """
        for gone in ("faviconUrl", "google.com/s2/favicons", "faviconFallbackUrl", "SITE_FAVICONS"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, self.markup, "站点图标不得由浏览器向站外取")

    def test_no_caller_ever_hands_the_link_mark_endpoint_a_url(self):
        # 让前端把地址递给服务端去取，等于开一个任意地址抓取的口子。和 `/follow-stream`
        # 同一条规矩：服务端只取账本里已有的地址。`linkMarkUrl` 自己只吐 id 由
        # test_web_js.test_the_link_mark_endpoint_only_ever_carries_an_id 验收；
        # 这里守的是「没人绕过它另写一个带地址的调用」。
        self.assertPageLacks("/link-mark?url=", "外链图标端点不得接受前端给的地址")
        self.assertPageLacks("/site-mark?url=", "站点圆标端点同样只认键")

# void 元素没有结束标签，压进栈里只会制造假报错。
_VOID = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})


def tag_balance_problems(source: str) -> list:
    """返回 HTML 里开闭标签不配平的地方，配平时返回空列表。

    只做结构配平，不校验属性与语义：那是另一件事，也没有不引入依赖就能做的办法。
    """
    stack, problems = [], []

    class Balance(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag not in _VOID:
                stack.append((tag, self.getpos()[0]))

        def handle_endtag(self, tag):
            if tag in _VOID:
                return
            line = self.getpos()[0]
            if not stack:
                problems.append(f"第 {line} 行 </{tag}> 没有对应的开始标签")
                return
            if stack[-1][0] == tag:
                stack.pop()
                return
            for index in range(len(stack) - 1, -1, -1):
                if stack[index][0] == tag:
                    skipped = "、".join(
                        f"<{name}>（第 {at} 行）" for name, at in stack[index + 1:])
                    problems.append(
                        f"第 {line} 行 </{tag}> 跳过了仍然开着的 {skipped}，"
                        f"实际关掉的是第 {stack[index][1]} 行的 <{tag}>")
                    del stack[index:]
                    break
            else:
                problems.append(
                    f"第 {line} 行 </{tag}> 无处可配，"
                    f"当前开着的是第 {stack[-1][1]} 行的 <{stack[-1][0]}>")

    parser = Balance(convert_charrefs=True)
    parser.feed(source)
    parser.close()
    for name, line in stack:
        problems.append(f"第 {line} 行的 <{name}> 直到文件结束都没有关闭")
    return problems


class IndexHtmlTagBalanceTests(unittest.TestCase):
    """index.html 的开闭标签必须配平。

    2026-09-03：设置面板的「安全」分组后面多出一个 `</section>`。浏览器按 HTML5
    容错规则把它当成关闭最外层 `section.settingspanel`，随后那三行 `</div></div>
    </section>` 全部无处可配、被静默丢弃。这一处没有造成可见故障——多余标签后面
    只有结束标签、没有内容，已经插入的节点不会被回溯搬走，所以 DOM 与本意一致。
    换个位置就不是这样了：多余标签后面只要还有内容，那些内容就会落到错误的父节点
    下，而页面照样渲染、控制台照样安静。所以这里守的是配平本身，不是某一处症状。
    """

    def test_index_html_tags_are_balanced(self):
        page = Path(__file__).resolve().parents[1] / "web" / "index.html"
        problems = tag_balance_problems(page.read_text(encoding="utf-8"))
        if problems:
            self.fail("index.html 标签不配平：\n" + "\n".join(problems))

    def test_the_balance_checker_actually_catches_a_stray_end_tag(self):
        """门槛自己也要有人守：探测器写坏了会安静地永远通过。

        这里喂的就是 index.html 当时的形状——多余的 `</section>` 跨过两层还开着的
        div，后面跟着三个再也配不上的结束标签。
        """
        self.assertEqual([], tag_balance_problems("<section><div><div></div></div></section>"))
        problems = tag_balance_problems(
            "<section>\n<div>\n<div>\n</section>\n</div>\n</div>\n</section>")
        self.assertEqual(4, len(problems), problems)
        self.assertIn("第 4 行 </section> 跳过了仍然开着的", problems[0])
        self.assertIn("<div>（第 2 行）", problems[0])


class CoverSleeveThresholdTests(unittest.TestCase):
    def test_the_page_and_the_module_share_the_sleeve_thresholds(self):
        """页面按这对阈值决定取景，模块按它分档竖海报；两边的数必须是同一对。"""
        root = Path(__file__).resolve().parents[1]
        source = (root / "src" / "peach" / "jav_poster_crop.py").read_text(encoding="utf-8")
        low = float(re.search(r"^SLEEVE_RATIO_MIN = ([\d.]+)", source, re.M).group(1))
        high = float(re.search(r"^SLEEVE_RATIO_MAX = ([\d.]+)", source, re.M).group(1))
        page = (root / "frontend" / "src" / "card-art" / "framing.ts").read_text(encoding="utf-8")
        anchor = re.search(r"r\s*>=\s*([\d.]+)\s*\?\s*'still'\s*:\s*r\s*>\s*([\d.]+)\s*\?\s*'sleeve'", page)
        self.assertIsNotNone(anchor, "coverAnchor 的封套判定不见了")
        self.assertEqual((low, high), (float(anchor.group(2)), float(anchor.group(1))))
        self.assertLess(low, high)

    def test_the_face_script_takes_the_thresholds_from_the_module(self):
        """脚本按封套丢掉左半边的脸，用的必须是模块里那一份，不是自己抄的一份。"""
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "detect_cover_faces.py").read_text(encoding="utf-8")
        source = (root / "src" / "peach" / "cover_artwork.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"^SLEEVE_RATIO_M(?:IN|AX) = ", script + source, re.M),
                          "阈值抄成第二份就会和页面漂开")


class BoardStyleIsolationTests(unittest.TestCase):
    def test_the_board_layer_is_the_only_interface_and_ships_unconditionally(self):
        """board.css 随页面一起加载，页面上没有第二套界面可选。

        它是盖在 `web/css/` 上的覆盖层，两份一起才画得出一个界面。留一个开关把它摘掉，
        剩下的是一屏对不上的类名——`.board-*` 那些节点仍在 DOM 里，谁也不给它们样式。
        判据落在三处：入口 HTML 无条件引它、页面源码没有 `peach.legacy-ui` 与
        `original-design` 的消费者、应用与设置面板都不读第二套界面的开关。设置里那枚
        「增加对比度」由 `frontend/test/react/settings-panel.test.tsx` 在渲染结果上验。
        """
        root = Path(__file__).resolve().parents[1]
        html = (root / "web/index.html").read_text(encoding="utf-8")
        self.assertIn('<link rel="stylesheet" href="/board.css">', html)
        paths = [*sorted((root / "web").rglob("*")), *sorted((root / "frontend" / "src").rglob("*"))]
        for path in paths:
            if path.suffix not in {".js", ".css", ".html", ".ts", ".tsx"} or "dist" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for token in ("peach.legacy-ui", "original-design", "legacyUISetting"):
                self.assertNotIn(token, text, f"{path.name} 仍在读第二套界面的开关")

    def test_icon_centering_does_not_override_toolbar_visibility(self):
        root = Path(__file__).resolve().parents[1]
        css = (root / "web/board.css").read_text(encoding="utf-8")
        rules = re.findall(r'([^{}]+)\{([^{}]*)\}', css)
        centering = [selector for selector, body in rules
                     if 'display:inline-grid' in body and 'place-items:center' in body]
        self.assertTrue(any('.srctools button' in selector for selector in centering))
        self.assertFalse(any('.ib,' in selector or '.sidebaraddmenu button' in selector
                             for selector in centering))


class MotionRecipeTests(unittest.TestCase):
    """原地换态的那一组动效：时长与缓动只读 token，形态只动合成属性。

    这里只检查减少动态效果的 token 与允许动画的属性；动画交互由浏览器验证。
    """

    #: 这一组新增的动效类，每一条都必须只从 token 取时长。
    MOTION_TOKENS = ("--motion-swap", "--motion-reveal", "--motion-stagger")

    def test_reduced_motion_zeroes_every_motion_token(self):
        """新加的三档时长跟原有的一样，由那一条全局规则一次关掉，不逐处补。

        `--motion-stagger` 尤其不能漏：时长归零而错峰延迟还在的话，`both` 会把后面
        几位数字按住不显示，读数看上去缺了几位。
        """
        # 三档 token 和原有的那几档住在同一处（board.css 的 `:root`），关掉它们的也是
        # 同一条规则；`stylesheet_source()` 只拼 `web/css/`，board.css 要单独读。
        board = (Path(__file__).resolve().parents[1]
                 / "web/board.css").read_text(encoding="utf-8")
        # board.css 里不止一条 reduced motion 规则，token 那一条认 `--board-motion:0s`。
        start = board.index("--board-motion:0s")
        reduced = board[start:board.index("}", start)]
        for token in self.MOTION_TOKENS:
            with self.subTest(token=token):
                self.assertIn(f"{token}:0", reduced, f"{token} 要在 reduced motion 下归零")

    def test_motion_recipes_only_animate_compositor_properties(self):
        """这几条都长在读数、按钮和整页占位上，一次重排就是一整棵子树。"""
        root = Path(__file__).resolve().parents[1]
        motion = "\n".join((root / path).read_text(encoding="utf-8")
                           for path in ("web/css/25-motion.css", "frontend/src/ui-kit/motion.css"))
        allowed = {"opacity", "filter", "transform", "translate", "stroke-dashoffset"}
        frames = re.findall(r"@keyframes\s+[\w-]+\{(.*?)\}\s*\}", motion, re.S)
        self.assertTrue(frames, "这两份样式里应当有关键帧")
        for body in frames:
            for prop in re.findall(r"([a-z-]+)\s*:", body):
                with self.subTest(prop=prop):
                    self.assertIn(prop, allowed, f"关键帧里不许动 {prop}")
        for declaration in re.findall(r"transition:([^;}]+)", motion):
            for prop in re.findall(r"\b([a-z-]+)\s+var\(", declaration):
                with self.subTest(prop=prop):
                    self.assertIn(prop, allowed, f"过渡里不许动 {prop}")

if __name__ == "__main__":
    unittest.main()
