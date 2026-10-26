# -*- coding: utf-8 -*-
"""前端的门槛：固定主包与页面包、清单、契约与 vitest。

ADR-0022 的取舍是「构建产物进 Git」：运行时的 Python 服务、PyInstaller 包和 macOS
上的检出都没有 Node，`web/dist/peach-app.js` 必须是仓库里现成的文件。代价是产物会和
源码脱节，所以这里分两类断言：

- **不需要 Node 的**：产物在不在、页面加载路径、独立模块图、清单是否
  精确钉版本、共享客户端是否唯一。这些在任何机器上都跑。
- **需要 Node 的**：tsc、lint 与 vitest。npm 或 `frontend/node_modules` 不在时本机显式
  跳过——本机可能根本没装 Node，让整个测试域红掉只会让人绕过入口，而不是去装 Node。
  CI 的 Test 工作流由 `web-bundle` 统一执行三个工具；其他任务与本机经正式入口运行。
  CI 实际执行工具的任务缺前置条件时判失败。

「产物是否由当前源码构建出来」这一条不在这里：不装 Node 就无法重建，无从比较。
那道门槛在 CI 的 `web-bundle` job 里，`npm run build` 之后 `git diff --exit-code web/dist`。
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from support.conditions import missing_prerequisite

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DIST = ROOT / "web" / "dist"
BUNDLE = DIST / "peach-app.js"
APP_STYLES = DIST / "peach-app.css"
PAGES_BUNDLE = DIST / "peach-pages.js"
PAGES_STYLES = DIST / "peach-pages.css"


class PageAssets(HTMLParser):
    """页面声明的脚本和样式，按浏览器的加载顺序收集。"""

    def __init__(self, html):
        super().__init__()
        self.scripts = []
        self.styles = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.scripts.append(attrs)
        elif tag == "link" and attrs.get("rel") == "stylesheet":
            self.styles.append(attrs.get("href"))


class MainBundleTests(unittest.TestCase):
    """提交进 Git 的产物必须是浏览器能直接 import 的那一份。"""

    @classmethod
    def setUpClass(cls):
        cls.bundle = BUNDLE.read_text(encoding="utf-8")

    def test_bundle_name_carries_no_content_hash(self):
        """运行时只分发固定名字的主包和独立页面包。"""
        names = sorted(path.name for path in DIST.iterdir() if path.is_file())
        self.assertEqual(names, ["peach-app.css", "peach-app.js", "peach-pages.css", "peach-pages.js"])

    def test_the_main_document_loads_one_module_entry(self):
        assets = PageAssets((ROOT / "web" / "index.html").read_text(encoding="utf-8"))
        self.assertEqual([script["src"] for script in assets.scripts], ["/dist/peach-app.js"])
        self.assertEqual(assets.scripts[0].get("type"), "module")

    def test_the_startup_switches_and_their_payload_are_in_the_shipped_bundle(self):
        """「开机自启」那三颗开关的标签和它们发出去的键，判据落在产物上。

        `web/dist/peach-app.js` 是提交进 Git 的产物。改了 `frontend/src` 不重建，浏览器拿到
        的仍是旧的那一份，而 tsc 和 vitest 都只看源码，谁都不会红——只有扫产物这一条会。
        """
        for label in ("开机后启动 Peach", "只启动托盘，不打开网页", "在桌面创建快捷方式"):
            self.assertIn(label, self.bundle,
                          f"产物里没有「{label}」，先跑 npm --prefix frontend run build")
        payload = self.bundle[self.bundle.index('"/api/configuration/startup"'):][:240]
        for key in ("enabled:", "silent:", "desktop:"):
            self.assertIn(key, payload, f"保存开机自启没带上 {key}")

    def test_react_bundle_records_stable_dependency_paths(self):
        """区域注释只能从 `node_modules/` 起，不得把生成它的工作树路径写进产物。"""
        for bundle in (BUNDLE, PAGES_BUNDLE):
            with self.subTest(bundle.name):
                captured = [line for line in bundle.read_text(encoding="utf-8").splitlines()
                            if line.startswith("//#region ") and "node_modules/" in line
                            and not line.startswith("//#region node_modules/")]
                self.assertEqual(captured, [],
                                 f"{bundle.name} 带了工作树相对路径；在当前工作树安装依赖后重新构建")


class BoardTokenTests(unittest.TestCase):
    """BoardUI 的语义 token 在 `web/board.css` 里另有一份定值，两份各管一片页面。"""

    @staticmethod
    def declarations(css, selector):
        """同一选择器可能分几块写，合并后返回其中的 `--color-*` 声明。"""
        found = {}
        for block in re.finditer(rf"(?ms)^{re.escape(selector)} \{{\n(.*?)^\}}", css):
            found.update(re.findall(r"(--color-[\w-]+):\s*([^;]+);", block.group(1)))
        return found

    #: Peach 自己定值、不跟上游的 token。每一条都要在 `boardui/ORIGIN.md` 的差异表里
    #: 有一行写清原因：默认逐字照抄，例外必须是写下来的决定，不是谁顺手改的一次。
    LOCAL_TOKEN_VALUES = {
        (".dark", "--color-border-checkbox-default"): "var(--color-neutral-600)",
        (".dark", "--color-separator-border"): "var(--color-neutral-700)",
    }

    def test_the_react_subtree_redeclares_shared_tokens_with_upstream_values(self):
        """board.css 在 `:root` 上另定同名 token 且排在后面；React 容器上的值逐字等于 theme.css，
        登记在 `ORIGIN.md` 差异表里的那几条除外。"""
        legacy = "".join(path.read_text(encoding="utf-8") for path in
                         [ROOT / "web" / "board.css", *sorted((ROOT / "web" / "css").glob("*.css"))])
        shared = set(re.findall(r"(--color-[\w-]+):", legacy))
        theme = (FRONTEND / "src" / "react" / "boardui" / "styles" / "theme.css").read_text(encoding="utf-8")
        styles = (FRONTEND / "src" / "react" / "base.css").read_text(encoding="utf-8")
        origin = (FRONTEND / "src" / "react" / "boardui" / "ORIGIN.md").read_text(encoding="utf-8")
        for upstream, local in ((":root", ".peach-react"), (".dark", ".dark .peach-react")):
            expected = {name: value for name, value in self.declarations(theme, upstream).items()
                        if name in shared}
            self.assertTrue(expected, f"theme.css 的 {upstream} 块里没找到同名 token")
            for (scope, name), value in self.LOCAL_TOKEN_VALUES.items():
                if scope == upstream and name in expected:
                    self.assertIn(name, origin, f"{name} 偏离上游，要在 ORIGIN.md 的差异表里写明原因")
                    expected[name] = value
            self.assertEqual(self.declarations(styles, local), expected,
                             f"base.css 的 {local} 要与 theme.css 的 {upstream} 同名 token 逐条一致")

    def test_manual_and_system_dark_palettes_in_board_css_agree(self):
        """手动选深色与跟随系统深色是同一副配色，两块分开写，只改一块时文字色会差一档。"""
        board = (ROOT / "web" / "board.css").read_text(encoding="utf-8")
        manual = re.search(r':root\[data-theme="dark"\]\{(--color-text-primary:[^}]*)\}', board)
        system = re.search(r"@media\(prefers-color-scheme:dark\)\{:root:not\(\[data-theme\]\)\{"
                           r"(--color-text-primary:[^}]*)\}", board)
        self.assertIsNotNone(manual)
        self.assertIsNotNone(system)
        self.assertEqual(sorted(system.group(1).split(";")), sorted(manual.group(1).split(";")))


class ReactBundleTests(unittest.TestCase):
    """React 子树（BoardUI 源码 + Tailwind）与旧样式表同处一页的门槛。

    这几条都是「tsc 和 vitest 看不见、页面上才出事」的约束：产物引用路径、样式表顺序、
    工具类有没有被层叠层压住、Preflight 有没有漏到整页。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = BUNDLE.read_text(encoding="utf-8")
        cls.css = APP_STYLES.read_text(encoding="utf-8")
        cls.pages = PAGES_BUNDLE.read_text(encoding="utf-8")
        cls.pages_css = PAGES_STYLES.read_text(encoding="utf-8")

    def test_shipped_bundles_do_not_require_node_globals(self):
        for name, bundle in (("peach-app.js", self.app), ("peach-pages.js", self.pages)):
            self.assertNotIn("process.env", bundle, f"{name}：库模式没替换 NODE_ENV，浏览器里没有 process")

    def test_shipped_bundles_have_independent_module_graphs(self):
        """主包与页面包各自包含运行依赖，开发标注工具由本机开关单独加载。"""
        for name, bundle, allowed_dynamic in (("peach-app.js", self.app, {"/dev/agentation.js"}),
                                              ("peach-pages.js", self.pages, set())):
            static = re.findall(r'(?m)^\s*(?:import\b[^;]*?|export\s*\*\s*)from\s*["\']([^"\']+)["\']'
                                r'|^\s*import\s*["\']([^"\']+)["\']', bundle)
            dynamic = set(re.findall(r'\bimport\s*\(\s*["\']([^"\']+)["\']\s*\)', bundle))
            self.assertEqual(static, [], name)
            self.assertEqual(dynamic - allowed_dynamic, set(), name)

    def test_utilities_stay_outside_cascade_layers(self):
        """旧样式表不分层。工具类放进层里，`button,input,textarea{color:inherit}` 这类标签规则就会压过它。"""
        for css in (self.css, self.pages_css):
            layers = set(re.findall(r"@layer\s+([\w-]+)", css))
            self.assertNotIn("utilities", layers)
            self.assertNotIn("base", layers)

    def test_preflight_only_reaches_the_react_subtree(self):
        for css in (self.css, self.pages_css):
            self.assertEqual(css.count("@scope"), 1)
            self.assertRegex(css, r"@scope\s*\(\.peach-react\)")
        scoped = (FRONTEND / "src" / "react" / "preflight-scoped.css").read_text(encoding="utf-8")
        upstream = FRONTEND / "node_modules" / "tailwindcss" / "preflight.css"
        if not upstream.is_file():
            self.skipTest("跳过 Preflight 原文比对：frontend/node_modules 还没装")
        opening = "@scope (.peach-react) {\n"
        self.assertIn(opening, scoped)
        self.assertTrue(scoped.endswith("}\n"))
        self.assertEqual(scoped[scoped.index(opening) + len(opening):-2],
                         upstream.read_text(encoding="utf-8"),
                         "preflight-scoped.css 与 tailwindcss 依赖里的原文不一致，按文件开头的说明重新生成")

    def test_react_styles_load_before_the_legacy_stylesheets(self):
        """同名 `--color-*` token 由后面的 board.css 定值，未迁移页面的颜色才不受影响。"""
        assets = PageAssets((ROOT / "web" / "index.html").read_text(encoding="utf-8"))
        order = [assets.styles.index(href) for href in ("/dist/peach-app.css", "/app.css", "/board.css")]
        self.assertEqual(order, sorted(order))

    def test_the_first_frame_script_sets_the_dark_class(self):
        """BoardUI 的深色 token 挂在 `.dark` 上，首帧脚本要在主包加载前按实际深浅加减它。

        之后切换主题由 `applyTheme()` 负责，`frontend/test/appearance/layout.test.ts` 在 DOM 上验。
        """
        index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("classList.toggle('dark',", index)


class BoardUiUpstreamTests(unittest.TestCase):
    """`frontend/src/react/boardui/` 与 `evilcharts/` 是上游注册表源码的逐字副本，只加不改
    （ADR-0031、ADR-0076）。

    每个目录的 `UPSTREAM.sha256` 记下复制时每个文件的 SHA-256。改了副本、多出没登记的文件、
    登记了却删掉，都在这里红。比的是登记的快照而不是线上注册表：上游随时会改，测试不能联网。
    升级上游时重新复制文件、重算对应行，并更新同目录 `ORIGIN.md` 的条目哈希。
    """

    VENDORED = ("boardui", "evilcharts")
    RECORDS = ("ORIGIN.md", "UPSTREAM.sha256")

    def test_the_copied_sources_match_their_recorded_upstream_hashes(self):
        for name in self.VENDORED:
            with self.subTest(name):
                self.assert_matches_upstream(FRONTEND / "src" / "react" / name, name)

    def assert_matches_upstream(self, root: Path, name: str):
        recorded = {}
        for line in (root / "UPSTREAM.sha256").read_text(encoding="utf-8").splitlines():
            digest, path = line.split("  ", 1)
            recorded[path] = digest
        present = {file.relative_to(root).as_posix(): file for file in root.rglob("*")
                   if file.is_file() and file.name not in self.RECORDS}
        self.assertEqual(sorted(present), sorted(recorded),
                         f"{name}/ 的文件要与 UPSTREAM.sha256 一一对应；Peach 自己的组合放在 {name}/ 外面")
        for path, file in present.items():
            self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(), recorded[path],
                             f"{name}/{path} 与复制时的上游内容不同；外观差异在 {name}/ 外面组合")


class FrontendManifestTests(unittest.TestCase):
    """依赖清单和根 `package.json` 是两份，各自的口径都要精确。"""

    def setUp(self):
        self.manifest = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))

    def test_versions_are_exactly_pinned_and_locked(self):
        self.assertTrue(self.manifest["private"])
        declared = {**self.manifest.get("dependencies", {}),
                    **self.manifest.get("devDependencies", {})}
        self.assertTrue(declared)
        for name, version in declared.items():
            self.assertRegex(version, r"^\d+\.\d+\.\d+$",
                             f"{name} 没有钉死版本，两台机器会装出不同的产物")
        lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["name"], self.manifest["name"])
        for name, version in declared.items():
            entry = lock["packages"].get(f"node_modules/{name}")
            self.assertIsNotNone(entry, f"{name} 不在 lockfile 里")
            self.assertEqual(entry["version"], version, name)

    def test_the_root_manifest_stays_limited_to_the_vendored_packages(self):
        """根清单只登记手工 vendor 的那几个包，构建依赖不许混进去。"""
        root = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertNotIn("vite", root.get("devDependencies", {}))
        self.assertNotIn("preact", root.get("dependencies", {}))

    def test_the_frontend_has_no_preact_left(self):
        """前端只有 React 一档（ADR-0031）：留着 Preact 就是两套运行时各打一份进产物。"""
        manifest = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
        for section in ("dependencies", "devDependencies"):
            self.assertNotIn("preact", manifest.get(section, {}))
        config = json.loads(re.sub(r"^\s*//.*$", "", (FRONTEND / "tsconfig.json")
                                   .read_text(encoding="utf-8"), flags=re.M))
        self.assertNotIn("jsxImportSource", config["compilerOptions"])

    def test_dependabot_watches_the_frontend_manifest(self):
        dependabot = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        self.assertIn("directory: /frontend", dependabot)

    def test_ci_owns_the_only_gate_that_can_catch_a_stale_bundle(self):
        """本机验不了「产物是否由当前源码构建」，这道门槛只在 CI。

        它一旦被删掉，源码和 `web/dist/` 就能无声地分叉，而所有本地测试照样全绿——
        那正是这条断言要拦的。`git add --intent-to-add` 不能省：光 `git diff` 只看
        已跟踪的文件，构建新出的产物是未跟踪的，会被直接放过。
        """
        workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        for step in ("actions/setup-node", "npm --prefix frontend ci",
                     "npm --prefix frontend run typecheck", "npm --prefix frontend run lint",
                     "npm --prefix frontend test",
                     "npm --prefix frontend run build",
                     "git add --intent-to-add -- web/dist",
                     "git diff --exit-code --stat -- web/dist"):
            self.assertIn(step, workflow, f"CI 缺了 {step}")

    def test_the_bundle_is_committed_and_the_toolchain_is_not(self):
        tracked = subprocess.run(
            ["git", "-C", str(ROOT), "check-ignore", "web/dist/peach-app.js"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        self.assertEqual(tracked.returncode, 1, "web/dist 被 .gitignore 排除了，产物发不出去")
        for ignored in ("frontend/node_modules/react/package.json",):
            result = subprocess.run(["git", "-C", str(ROOT), "check-ignore", ignored],
                                    capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", check=False)
            self.assertEqual(result.returncode, 0, f"{ignored} 没有被忽略")


class SharedStateContractTests(unittest.TestCase):
    """一份数据有第二个读者时，两个读者读同一个 `queryKey`（ADR-0031）。

    共享数据的家是 `src/query/client.ts` 那一个 QueryClient，壳与 React 岛都从 `@peach/query`
    取它。这条门槛盯的是源码布局：等到跑起来才发现两处各存一份，已经晚了。
    """

    def test_the_site_has_exactly_one_query_client(self):
        sources = sorted(path for path in (FRONTEND / "src").rglob("*.ts*"))
        declared = [path.relative_to(FRONTEND / "src").as_posix() for path in sources
                    if "new QueryClient(" in path.read_text(encoding="utf-8")]
        self.assertEqual(declared, ["query/client.ts"], f"QueryClient 建在 {declared}")

    def test_pages_do_not_keep_a_second_copy_of_shared_data(self):
        """跨页共享的数据不另起一套订阅：`@preact/signals` 已经没有读者。"""
        outside = [path.name for path in sorted((FRONTEND / "src").rglob("*.ts*"))
                   if "@preact/signals" in path.read_text(encoding="utf-8")]
        self.assertEqual(outside, [],
                         f"{outside} 用了 signal：跨页共享请读同一个 queryKey，局部状态用 hooks")


class VitestTests(unittest.TestCase):
    """vitest、tsc 与 lint 走同一个测试入口，但缺 Node 时跳过而不是红。

    三者互不覆盖：vitest 经 Vite 转译时只剥掉类型、不做检查，类型错误照样跑绿；
    裸色值、任意值和在 BoardUI 组件上改样式，类型和行为测试都看不见。
    """

    def _npm(self, tool: str, package: str) -> str:
        npm = shutil.which("npm")
        if npm is None:
            missing_prerequisite(f"跳过 {tool}：本机没有 npm。装 Node 24+ 后 `-Scope web` 会带上它")
        if not (FRONTEND / "node_modules" / package).is_dir():
            missing_prerequisite(f"跳过 {tool}：frontend/node_modules 还没装，先 `npm --prefix frontend ci`")
        return npm

    def test_the_frontend_sources_typecheck(self):
        npm = self._npm("tsc", "typescript")
        completed = subprocess.run(
            [npm, "--prefix", str(FRONTEND), "run", "typecheck", "--silent"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(FRONTEND), check=False)
        self.assertEqual(completed.returncode, 0, f"{completed.stdout}\n{completed.stderr}")

    def test_the_island_suite_has_a_bounded_worker_pool(self):
        config = (FRONTEND / "vitest.config.ts").read_text(encoding="utf-8")
        self.assertIn("maxWorkers: 4", config)

    def test_frontend_sources_and_tests_pass_lint(self):
        """`@shadcn/lint` 挡住裸色值、任意值、内联样式和在 BoardUI 组件上改样式（ADR-0031）。"""
        npm = self._npm("lint", "oxlint")
        completed = subprocess.run(
            [npm, "--prefix", str(FRONTEND), "run", "lint", "--silent", "--", "--format=json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(FRONTEND), check=False)
        output = f"{completed.stdout}\n{completed.stderr}"
        self.assertEqual(completed.returncode, 0, output)
        report = json.loads(completed.stdout)
        self.assertEqual(report["diagnostics"], [], output)
        # 没有问题时 Oxlint 什么都不打印，路径写错查了 0 个文件也照样退出 0。
        self.assertGreater(report["number_of_files"], 0, output)
        copy = subprocess.run([npm, "--prefix", str(FRONTEND), "run", "check:copy"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=FRONTEND)
        self.assertEqual(copy.returncode, 0, copy.stdout + copy.stderr)

    def test_the_island_suite_passes(self):
        npm = self._npm("vitest", "vitest")
        completed = subprocess.run(
            [npm, "--prefix", str(FRONTEND), "test", "--silent"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(FRONTEND), check=False)
        output = f"{completed.stdout}\n{completed.stderr}"
        # vitest 的着色随宿主终端能力漂移，计数断言只认去码后的纯文本；
        # 退出码与用例数两道判据不受着色影响。
        plain = re.sub(r"\x1b\[[0-9;]*m", "", output)
        self.assertEqual(completed.returncode, 0, output)
        self.assertRegex(plain, r"Tests\s+\d+ passed", output)
        self.assertNotRegex(plain, r"Tests\s+0 passed", "vitest 一个用例都没跑")
        counted = re.search(r"Tests\s+(\d+) passed", plain)
        assert counted is not None
        self.assertGreaterEqual(int(counted.group(1)), 10, output)


class BrowserSuiteWorkflowTests(unittest.TestCase):
    """CI 里的浏览器冒烟与设计决定断言只能执行或失败，不能静默跳过。

    缺前置条件在 CI 判失败（`tests/support/conditions.py`）只做了一半：工作流还得真的装齐
    Node、依赖、ffmpeg 与 Chrome，并让结果进入 `verified` 的汇总。任一处被删掉，冒烟就回到
    从未在 CI 执行的状态，或者让 `python` 矩阵的全量行判失败。
    """

    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")

    def job(self, name: str) -> str:
        found = re.search(rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  \S|\Z)", self.workflow)
        self.assertIsNotNone(found, f"CI 没有 {name} job")
        return found.group(1)

    def test_the_browser_job_installs_every_prerequisite_and_runs_the_web_scope(self):
        job = self.job("web-e2e")
        for step in ("runs-on: windows-latest", "actions/setup-node", 'node-version: "24"',
                     "npm --prefix frontend ci", "FedericoCarboni/setup-ffmpeg",
                     'ffmpeg-version: "9.0.1"',
                     "PEACH_E2E_CHROME=", "& .\\scripts\\test.ps1 -Scope web"):
            self.assertIn(step, job, f"web-e2e job 缺了 {step}")

    def test_the_summary_requires_browser_and_frontend_jobs(self):
        needs = re.search(r"(?ms)^  verified:\n.*?^    needs: \[([^\]]*)\]", self.workflow)
        self.assertIsNotNone(needs, "verified job 没有 needs 列表")
        for required in ("web-e2e", "web-bundle"):
            self.assertIn(required, [name.strip() for name in needs.group(1).split(",")])

    def test_matrix_rows_beyond_core_install_what_the_node_suites_need(self):
        job = self.job("python")
        for step in ("actions/setup-node", "npm --prefix frontend ci",
                     "FedericoCarboni/setup-ffmpeg", 'ffmpeg-version: "9.0.1"',
                     "PEACH_E2E_CHROME="):
            self.assertIn(step, job, f"python 矩阵缺了 {step}，全量行上的用例会判失败")


if __name__ == "__main__":
    unittest.main()
