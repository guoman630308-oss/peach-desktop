"""仓库与顶层目录的整洁门槛。

检查顶层布局、工作树登记、私有产物和公开文档中的个人信息。
"""
import ast
import pathlib
import re
import socket
import subprocess
import tempfile
import tomllib
import unittest

import peach
from peach import appid
from scripts import test_runner

REPO = pathlib.Path(peach.__file__).resolve().parents[2]
DOCS = REPO / "docs"

#: ADR-0017 定义的四个运行时目录，加一个归置处。顶层只允许这五项。
TOP_LEVEL_ALLOWED = {"peach-app", "peach-data", "peach-sync", "peach-worktrees", "attic"}

#: 文件管理器自己写的缓存文件，按 casefold 比对（Windows 上大小写会变）。门槛拦的是人
#: 放上去的散落文档和产物；这几个不是人放的，macOS Finder 只要打开过那个文件夹就会写
#: `.DS_Store`，Windows 资源管理器写 `desktop.ini` 与 `Thumbs.db`，删掉照样再长出来。
#: 不豁免它们，这条门槛在任何有图形界面的开发机上都是红的，红得和布局无关。
TOP_LEVEL_GENERATED_FILES = frozenset({".ds_store", "desktop.ini", "thumbs.db"})

PRIVATE_CONTEXT_DOCUMENTS = frozenset({
    "HANDOFF.md", "STATUS.md", "PRODUCT_BACKLOG.md", "VERIFICATION_EFFICIENCY.md",
    "UI_REUSE_REVIEW.md", "SCRAPING_AUDIT.md",
})
PRIVATE_CONTEXT_PATHS = frozenset({
    "docs/reference-snapshots/tag-vocabulary-audit.md", "docs/scraping-audit.csv",
    "resources/seed/entities.json",
})
PRIVATE_ARTIFACT_ROOTS = frozenset({
    "peach-data", "peach-sync", "peach-worktrees", "secrets", "generated", "review", "attic", "build",
})
PRIVATE_RECORD_PATH = re.compile(r"(?:peach-data/(?:review|archive)/|attic/(?:evidence|reviews)/)(?![<*])[^`\s)]+")
COLLECTION_INVENTORY = re.compile(
    r"(?:本机|本库|真实馆藏|真实库|库里|馆里)[^。\n]{0,100}\d[\d,]*\s*"
    r"(?:条资产|行|部|作品|张头像|张封面|个文件|条来源|个题材|枚圆标|位缺头像)"
)


def _private_artifact(name: str) -> bool:
    path = pathlib.PurePosixPath(name)
    if path.parts[0].casefold() in PRIVATE_ARTIFACT_ROOTS:
        return True
    if name.startswith((".claude/worktrees/", ".codex/environments/")):
        return True
    if name in PRIVATE_CONTEXT_PATHS:
        return True
    if path.parent == pathlib.PurePosixPath("docs") and path.name in PRIVATE_CONTEXT_DOCUMENTS:
        return True
    if path.name == ".env" or path.name.startswith(".env.") and path.name != ".env.example":
        return True
    return bool(re.search(r"\.(?:db|sqlite\d*)(?:-(?:wal|shm))?$", path.name, re.I))


class PrivateArtifactTests(unittest.TestCase):
    """公开代码树拒收私有上下文、账本与运行产物。"""

    def test_private_artifact_paths_are_rejected_and_public_sources_are_allowed(self):
        for name in ("docs/HANDOFF.md", "docs/STATUS.md", "docs/PRODUCT_BACKLOG.md",
                     "peach-data/review/catalog.csv", "review/current-inventory.csv",
                     "attic/evidence/session.json", "secrets/access.json", ".env.local",
                     "database/ledger.db-wal", "database/history.sqlite3"):
            with self.subTest(path=name):
                self.assertTrue(_private_artifact(name))
        for name in ("docs/REUSE.md", "docs/OPERATIONS.md", "docs/TESTING.md",
                     "tests/test_job_status.py", "web/dist/peach-app.js", ".env.example"):
            with self.subTest(path=name):
                self.assertFalse(_private_artifact(name))

    def test_tracked_files_exclude_private_artifacts(self):
        done = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True)
        names = done.stdout.decode("utf-8").split("\0")
        offenders = [name for name in names if name and (REPO / name).is_file() and _private_artifact(name)]
        self.assertEqual(offenders, [], "私有记录与运行产物必须保存在代码仓库外")

    def test_git_ignores_private_context_and_runtime_directories(self):
        names = [f"docs/{name}" for name in sorted(PRIVATE_CONTEXT_DOCUMENTS)]
        names += sorted(PRIVATE_CONTEXT_PATHS)
        names += ["peach-data/review/catalog.csv", "secrets/access.json", "review/catalog.csv",
                  "attic/evidence/session.json"]
        public_sources = ["frontend/src/react/review/review-page.tsx"]
        done = subprocess.run(["git", "check-ignore", "--no-index", "-z", "--stdin"], cwd=REPO,
                              input=("\0".join([*names, *public_sources]) + "\0").encode("utf-8"),
                              capture_output=True, check=False)
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8"))
        self.assertEqual(set(done.stdout.decode("utf-8").strip("\0").split("\0")), set(names))

    def test_private_inventory_and_record_references_are_distinguished_from_examples(self):
        self.assertIsNotNone(COLLECTION_INVENTORY.search("本机盘点有 1234 部作品"))
        self.assertIsNotNone(PRIVATE_RECORD_PATH.search("peach-data/review/current-catalog.csv"))
        self.assertIsNone(COLLECTION_INVENTORY.search("示例库包含 12 部合成视频"))
        self.assertIsNone(PRIVATE_RECORD_PATH.search("<Peach 数据目录>/review/<复核产物>"))


def _loose_files(top: pathlib.Path) -> list[str]:
    """顶层的散落文件名，去掉文件管理器自己写的缓存。"""
    return sorted(child.name for child in top.iterdir() if child.is_file()
                  and child.name.casefold() not in TOP_LEVEL_GENERATED_FILES)


class TopLevelLayoutTests(unittest.TestCase):
    """顶层只放四个运行时目录加 attic/，且不放散落文件。

    在别的机器或 CI 上这个布局不成立（只 clone 了 peach-app），那里跳过——门槛要能在
    真实布局上拦住人，不能因为环境不同就把整套测试拖红。
    """

    def setUp(self):
        # 往上找真正的顶层，而不是 `REPO.parent`：测试几乎总是在隔离工作树里跑，那时
        # 上一级是 `peach-worktrees/`，判据不成立就会永远跳过——一个永远跳过的门槛
        # 等于没有，和它要防的失效是同一种。
        self.top = None
        for candidate in [REPO, *REPO.parents]:
            if all((candidate / name).is_dir()
                   for name in ("peach-app", "peach-data", "peach-worktrees")):
                self.top = candidate
                break
        if self.top is None:
            self.skipTest("不是双盘运行时布局（只 clone 了 peach-app），跳过")

    def test_only_the_runtime_directories_and_attic_live_at_the_top(self):
        extra = sorted(p.name for p in self.top.iterdir()
                       if p.is_dir() and p.name not in TOP_LEVEL_ALLOWED)
        self.assertEqual(
            extra, [],
            "顶层多了目录：`peach-` 前缀专属那四个运行时目录，别的东西进 attic/ 的"
            " builds／evidence／instances／tools／reviews，目录名写成 YYYYMMDD-主题",
        )

    def test_no_loose_files_at_the_top(self):
        self.assertEqual(_loose_files(self.top), [],
                         "顶层不放散落文件：文档进 attic/reviews/，产物进 attic/evidence/")


class LooseFileGuardTests(unittest.TestCase):
    """散落文件的判据本身。不依赖这台机器的布局，所以在哪儿都跑。"""

    def test_the_guard_exempts_only_the_file_manager_caches(self):
        """门槛自身也要能被证伪：缓存放行，人放上去的文件照旧算违规。"""
        with tempfile.TemporaryDirectory() as raw:
            sample = pathlib.Path(raw).resolve()
            for name in (".DS_Store", "desktop.ini", "Thumbs.db",
                         "REVIEW.md", "probe.json", "ds_store.md"):
                (sample / name).write_bytes(b"x")
            (sample / "peach-app").mkdir()
            self.assertEqual(_loose_files(sample),
                             ["REVIEW.md", "ds_store.md", "probe.json"])


class UnregisteredWorktreeTests(unittest.TestCase):
    """两个工作树落点里都不许留下未登记的目录。

    工作树被回收后目录可能留在原地，成了一份看不出区别的旧副本——在里面跑 git
    全部作用于主检出的 master。`peach-worktrees/` 是 `create` 的落点，也是智能体被告知
    要去干活的地方，所以它和 `.claude/worktrees/` 同一条判据。别的会话此刻可能正合法地
    占着一个工作树，所以判据是「有没有登记」而不是「有没有目录」，登记过的放行，
    本测试也从不删东西。
    """

    def _git(self, *args: str) -> str:
        done = subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", check=False)
        if done.returncode != 0:
            self.skipTest(f"git 不可用或不是仓库：{done.stderr.strip()}")
        return done.stdout

    def test_no_unregistered_directory_lingers_in_either_worktree_root(self):
        # `--git-common-dir` 在工作树里指向主检出的 `.git`，在主检出里是相对路径，
        # 所以统一按 REPO 解析再取上一级。只 clone 了 peach-app 的机器上两个落点都可能
        # 不存在，不存在的那一个跳过。
        common = pathlib.Path(self._git("rev-parse", "--git-common-dir").strip())
        main = (REPO / common).resolve().parent
        registered = {
            pathlib.Path(line[len("worktree "):]).resolve()
            for line in self._git("worktree", "list", "--porcelain").splitlines()
            if line.startswith("worktree ")
        }
        for root in (main.parent / "peach-worktrees", main / ".claude" / "worktrees"):
            if not root.is_dir():
                continue
            residue = sorted(child.name for child in root.iterdir()
                             if child.is_dir() and child.resolve() not in registered)
            self.assertEqual(
                residue, [],
                f"{root} 下有未登记的工作树残留：`prune --apply` 扫空目录，"
                "里面还有文件的按它报的路径确认没人在用后手动删",
            )


class BundledImageTests(unittest.TestCase):
    """图像二进制只许出现在 `resources/` 下。

    ADR-0024 的验收清单里一直有这一条，没有实现。ADR-0026 划出「法人实体标识」这条窄
    例外之后它更要有：例外一旦开口，下一张图进来时挡住它的只能是门槛，不是那份 ADR
    的措辞。`resources/` 之内由 `test_brand_marks` 逐条查判据，这里只管边界。
    """

    #: 判扩展名而不判字节：仓库里没有无扩展名的图，而读全部文件去嗅探要慢两个数量级。
    IMAGE_SUFFIXES = frozenset({
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".avif", ".ico", ".icns", ".tiff",
    })

    def test_images_live_only_under_resources(self):
        done = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", check=False)
        if done.returncode != 0:
            self.skipTest(f"git 不可用或不是仓库：{done.stderr.strip()}")
        stray = sorted(
            name for name in done.stdout.split("\0")
            if name and pathlib.PurePosixPath(name).suffix.lower() in self.IMAGE_SUFFIXES
            and not name.startswith("resources/")
        )
        self.assertEqual(
            stray, [],
            "图像二进制只进 resources/：应用自身品牌资产直接放 resources/，"
            "厂牌与站点标识走 scripts/sync_brand_marks.py 收进 resources/marks/（ADR-0026），"
            "其余图片是本机派生产物，留在 peach-data/generated/",
        )


class DocumentationMediaTests(unittest.TestCase):
    """`docs/assets/` 只放少量演示媒体，且每个文件都要小到能进 Git。

    README 的介绍视频住在这里，所以这一档不是「不许有字节」而是「有上限」：没有上限的话，
    下一段素材进来时挡住它的只剩人的自觉，而仓库一旦收下几十 MB 的二进制，clone 的人
    每次都得付这笔钱，删掉也只是从工作区消失，历史里照样躺着。
    """

    ASSETS = DOCS / "assets"
    #: 只认这几种容器：都是浏览器与 GitHub 直接播得动的格式，不需要额外转码说明。
    MEDIA_SUFFIXES = frozenset({".mp4", ".webm"})
    MAX_BYTES = 8 * 1024 * 1024

    def test_demo_media_stays_within_its_allowed_types_and_size(self):
        if not self.ASSETS.is_dir():
            self.skipTest("docs/assets 不存在")
        for path in sorted(self.ASSETS.rglob("*")):
            if not path.is_file():
                continue
            with self.subTest(asset=path.name):
                self.assertIn(
                    path.suffix.lower(), self.MEDIA_SUFFIXES,
                    "docs/assets 只收 mp4 与 webm 演示媒体；截图与图标走 resources/",
                )
                self.assertLessEqual(
                    path.stat().st_size, self.MAX_BYTES,
                    f"{path.name} 超过 {self.MAX_BYTES // (1024 * 1024)} MB："
                    "演示媒体要先压到这个上限以内，压不下去就别放进仓库",
                )




#: 家目录路径：`C:\Users\<名字>\…`、`/Users/<名字>/…`、`/home/<名字>/…`。名字段要以字母开头，
#: `<user>`、`<用户目录>` 这类占位由此放行；前面不许紧贴单词字符，`pixiv.net/users/…` 这种
#: URL 路径不算家目录。CI 靶机的 `runner`／`runneradmin`（含 8.3 短名 `RUNNER~1`）是文档里
#: 会正当提到的名字，放行。
_HOME_DIRECTORY = (r"(?<!\w)[\\/](?:Users|home)[\\/]"
                   r"(?!(?:runner|runneradmin)\b)[A-Za-z][\w.-]*")

#: mDNS 主机名 `<名字>.local`。只放行三个有文档意义的占位：默认名 `peach` 与讲双机布局用的
#: `peach-writer`／`peach-reader`。`self.local`／`this.local` 是属性名与后缀同形，不算主机名；
#: `<mounts.local>` 是占位；`settings.local.json`、`_tcp.local.` 后面还有段，都不是主机名。
_MDNS_HOST = (r"(?<![\w<-])(?!(?:self|this|peach|peach-writer|peach-reader)\.local\b)"
              r"[a-z0-9-]+\.local\b(?![.\w])")

#: RFC 1918 私网地址；`10.` 段收尾要词边界，Windows 版本号 `10.0.26200.1234` 才不会被算进去。
#: 举例用 RFC 5737 的 192.0.2.0/24、198.51.100.0/24、203.0.113.0/24：它们不属于任何人，
#: 读者也不会照抄进自己的配置。
_PRIVATE_IPV4 = (r"\b192\.168\.\d{1,3}\.\d{1,3}"
                 r"|\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"
                 r"|\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}")

#: 只属于某一台机器的字面量。它们要么住在设置文件里，要么由用户在命令行给，
#: 不能编译进 `src/peach/`——否则第一个陌生用户跑起来就连着别人家的坐标（ADR-0023）。
#: 挂载点与项目位置只在源码里拦：说明文档要讲清双机布局，就得点出具体落点。
PERSONAL_LITERAL = re.compile("(?i)" + "|".join((
    r"Desktop[\\/]peach",                       # 某一台机器的项目位置
    _HOME_DIRECTORY,
    _MDNS_HOST,
    r"Volumes[\\/](?:peach-sync|RESOURCES)",    # macOS 上的具体挂载点
    r"[\\/]IMSL[\\/]",                          # CloudDrive 挂载目录
    _PRIVATE_IPV4,
)))

#: 全树一律不许出现的机器坐标：私网地址的具体一台、任何人的家目录、任何一台机器的 mDNS 名。
#: 仓库公开后它们既是别人家的坐标又是个人信息（ADR-0023 第四阶段）。判据写成形状而不是
#: 点名：门槛自己也进 Git，点名等于把要拦的坐标印在公开代码里。当前维护者的账号名与
#: 主机名由 `MachineCoordinateTests` 运行时取本机的值再扫一遍，对任何维护者都成立；
#: 那一遍先剔掉仓库自己的公开归属，判据见 `_this_machine_guard`。
#:
#: 判据是「只对某一台机器成立」，不是「像个名字」：仓库的 GitHub 归属与 LICENSE 的
#: 版权人本来就要公开署名，它们不在拦截范围内。
MACHINE_COORDINATE = re.compile("(?i)" + "|".join((_PRIVATE_IPV4, _HOME_DIRECTORY, _MDNS_HOST)))

#: 从 GitHub 的仓库 URL 里取归属名，`https://github.com/<owner>/<repo>` 与
#: `git@github.com:<owner>/<repo>` 两种写法都收。
_GITHUB_OWNER = re.compile(r"github\.com[:/]+([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/")

#: 门槛自身要写出它拦的形状，所以只有它自己豁免。
COORDINATE_EXEMPT_FILES = frozenset({"tests/test_repo_hygiene.py"})

#: 第三方代码与构建产物的措辞不由本仓库决定，压缩后的它们也没有可读的行。
#: 上游 README 快照逐字节锁在 `docs/reference-sources.json` 的 sha256 上，示例地址属于上游。
COORDINATE_EXEMPT_PREFIXES = ("web/vendor/", "web/dist/", "docs/reference-snapshots/upstream/")


def _live_strings(path: pathlib.Path) -> list[tuple[int, str]]:
    """源码里真正会被当值用的字符串常量，去掉文档串。

    文档串和注释留给解释：说明「哪一台机器是 writer」时提到具体名字是有信息量的，
    把它编译成默认值才是问题。判据因此是 AST 而不是 grep。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef))
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


def _repository_identity(repo: pathlib.Path = REPO) -> frozenset[str]:
    """仓库自己的公开归属名，casefold 过。

    三个来源都取，取不到的静默跳过：`git remote` 的 URL、`pyproject.toml` 的项目 URL、
    `src/peach/appid.py` 的反向域名前缀。它们指的是同一个归属，多取一处是为了任一处
    缺失时判据仍然成立，而不是把名字写进这里——门槛自己也进 Git，点名等于把维护者的
    账号名印进公开代码，和形状判据不点名坐标是同一个理由。
    """
    names = set(appid.BUNDLE_PREFIX.split("."))
    urls: list[str] = []
    with (repo / "pyproject.toml").open("rb") as handle:
        urls += tomllib.load(handle)["project"].get("urls", {}).values()
    done = subprocess.run(["git", "remote", "-v"], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", check=False)
    if done.returncode == 0:
        urls += done.stdout.split()
    for url in urls:
        found = _GITHUB_OWNER.search(url)
        if found:
            names.add(found.group(1))
    return frozenset(name.casefold() for name in names if name)


def _this_machine_guard(names, identity) -> re.Pattern[str] | None:
    """只在网址、挂载目录和账号/主机配置中匹配本机名字。

    仓库公开归属不属于个人坐标；用户目录与 mDNS 等坐标仍由 MACHINE_COORDINATE
    检查。无剩余名字时返回 None，避免空正则匹配全部内容。
    """
    remaining = sorted(name for name in names if name and name.casefold() not in identity)
    if not remaining:
        return None
    names_pattern = r"(?:" + "|".join(map(re.escape, remaining)) + r")"
    context = (r"(?:https?://|smb://|\\\\|/Volumes/|/mnt/|/media/)"
               r"|\b(?:username|user_name|hostname|smb_host|account)['\"]?\s*[:=]\s*['\"]?")
    return re.compile("(?:" + context + ")" + names_pattern + r"(?![\w.-])", re.IGNORECASE)


class PersonalLiteralTests(unittest.TestCase):
    """`src/peach/` 里不许留只对某一台机器成立的默认值。"""

    def test_common_machine_names_require_coordinate_context(self):
        guard = _this_machine_guard({"test", "desktop"}, frozenset())
        for text in ("test the desktop app", "https://example.invalid/test", "desktop-client", "accounting test"):
            self.assertIsNone(guard.search(text), text)
        for text in ("https://desktop/media", "smb://test/share", r"\\desktop\share",
                     "/Volumes/test/media", "hostname = 'desktop'", "username: test",
                     '{"hostname": "desktop"}', '{"username": "test"}'):
            self.assertIsNotNone(guard.search(text), text)

    def test_no_module_hardcodes_a_personal_coordinate(self):
        source = pathlib.Path(peach.__file__).parent
        offenders = []
        for path in sorted(source.rglob("*.py")):
            offenders += [
                f"{path.relative_to(source).as_posix()}:{line}：{text[:60]}"
                for line, text in _live_strings(path)
                if PERSONAL_LITERAL.search(text)
            ]
        self.assertEqual(offenders, [],
                         "把它搬进 <Peach 数据目录>/config.toml（peach init --from-existing 会生成），"
                         "或者做成命令行参数")

    def test_the_guard_catches_the_shape_it_describes(self):
        """门槛自身也要能被证伪，否则它可能只是一段永远为真的代码。"""
        for sample in (r"C:\Users\alice\Desktop\peach\peach-data", "~/Desktop/peach",
                       "/Users/alice/peach", "/home/alice/peach", "alice-mbp.local",
                       "/Volumes/peach-sync", "https://10.234.56.78",
                       "/Volumes/RESOURCES/media", "/Users/x/Desktop/IMSL/115"):
            self.assertTrue(PERSONAL_LITERAL.search(sample), sample)
        for allowed in ("127.0.0.1", "0.0.0.0", "peach.local", "224.0.0.251",
                        "192.0.2.1", r"R:\media", "peach-data", "peach-sync",
                        "Chrome/131.0.0.0", r"C:\Users\<user>\peach-data",
                        "https://www.pixiv.net/users/93812377", "self.local"):
            self.assertIsNone(PERSONAL_LITERAL.search(allowed), allowed)


class ReleaseFilesTests(unittest.TestCase):
    """公开发布必须齐备的治理文件（ADR-0023 第 4 阶段）。

    仓库一旦公开，许可证、贡献说明、安全说明和 issue/PR 模板就是陌生人判断「能不能用、
    怎么报问题」的唯一依据。缺一份的后果不是报错而是沉默：使用者无从判断授权范围，
    漏洞只能开成公开 issue。它们又都是「一次写好、之后没人再看」的文件，正是重构和
    路径调整时最容易被顺手删掉的那一类，所以在这里钉住存在性。
    """

    #: 相对仓库根的路径。清单只增不减，删除任何一项都要先改 ADR-0023。
    REQUIRED = (
        "LICENSE",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CHANGELOG.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/ISSUE_TEMPLATE/config.yml",
    )

    def test_every_release_file_exists_and_has_content(self):
        missing = [name for name in self.REQUIRED if not (REPO / name).is_file()]
        self.assertEqual(missing, [], "公开发布缺少治理文件")
        empty = [name for name in self.REQUIRED
                 if not (REPO / name).read_text(encoding="utf-8").strip()]
        self.assertEqual(empty, [], "治理文件存在但是空的，等于没有")

    def test_the_licence_is_the_agpl_v3(self):
        text = (REPO / "LICENSE").read_text(encoding="utf-8")
        for marker in ("GNU AFFERO GENERAL PUBLIC LICENSE", "Version 3"):
            self.assertIn(marker, text,
                          "许可证是 AGPL-3.0-or-later（ADR-0023 第 4 阶段），换许可证要先改 ADR")

    def test_pyproject_licence_expression_matches_the_licence_file(self):
        """pyproject 的 SPDX 表达式和 LICENSE 全文是同一份许可证的两种写法。"""
        with (REPO / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]
        self.assertEqual(
            project.get("license"), "AGPL-3.0-or-later",
            "pyproject.toml 的 SPDX 表达式必须与 LICENSE 全文和 ADR-0023 第 4 阶段一致；"
            "换许可证要 LICENSE、pyproject.toml、ADR-0023 三处同改",
        )


class MachineCoordinateTests(unittest.TestCase):
    """Git 跟踪的每个文本文件都不许写出某一台机器的坐标。

    只管源码的门槛拦不住这件事：真实 IP、用户名和账号名过去散在测试夹具、安装脚本、
    ADR、状态文档与技能里，它们都不是「默认值」，公开仓库后却一样把用户的网络布局
    和账号名交出去。所以判据是 `git ls-files` 的全部文本，不是某个目录。
    """

    def _tracked_text_files(self, exempt_files=COORDINATE_EXEMPT_FILES):
        done = subprocess.run(["git", "ls-files", "-z"], cwd=REPO,
                              capture_output=True, check=False)
        if done.returncode != 0:
            self.skipTest(f"git 不可用或不是仓库：{done.stderr.decode('utf-8', 'replace')}")
        for name in done.stdout.decode("utf-8").split("\0"):
            if not name or name in exempt_files:
                continue
            if name.startswith(COORDINATE_EXEMPT_PREFIXES):
                continue
            path = REPO / name
            if not path.is_file():
                continue
            raw = path.read_bytes()
            if b"\0" in raw:
                continue
            try:
                yield name, raw.decode("utf-8")
            except UnicodeDecodeError:
                continue

    def test_no_tracked_file_names_a_machine_coordinate(self):
        offenders = []
        for name, text in self._tracked_text_files():
            for number, line in enumerate(text.splitlines(), 1):
                found = MACHINE_COORDINATE.search(line)
                if found:
                    offenders.append(f"{name}:{number}：{found.group(0)}")
        self.assertEqual(
            offenders, [],
            "换成 RFC 5737 的文档地址（192.0.2.0/24、198.51.100.0/24、203.0.113.0/24）"
            "或中性的主机名、账号名、`<用户目录>` 这类占位",
        )

    def test_public_documents_exclude_collection_inventories_and_private_record_names(self):
        offenders = []
        for name, text in self._tracked_text_files():
            if not name.endswith(".md"):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if COLLECTION_INVENTORY.search(line) or PRIVATE_RECORD_PATH.search(line.replace("\\", "/")):
                    offenders.append(f"{name}:{number}")
        self.assertEqual(offenders, [], "馆藏盘点与私有记录名应保存在部署 Peach 数据目录，检查输出只列文件和行号")

    def test_no_tracked_file_names_this_machine(self):
        """本机的账号名与主机名对任何维护者都成立，正好不用在正则里点名。

        扫的是「剔掉仓库自身归属之后剩下的本机名字」，判据与剔除的理由见
        `_this_machine_guard`。本文件自己也扫：形状门槛豁免它是因为它要写出形状，
        可它没有理由写出这台机器。
        """
        hostname = socket.gethostname()
        names = {pathlib.Path.home().name, hostname, hostname.split(".", 1)[0]}
        this_machine = _this_machine_guard(names, _repository_identity())
        if this_machine is None:
            self.skipTest("本机的账号名与主机名都是仓库自己的公开归属，只剩形状门槛可扫")
        offenders = []
        for name, text in self._tracked_text_files(exempt_files=frozenset()):
            for number, line in enumerate(text.splitlines(), 1):
                found = this_machine.search(line)
                if found:
                    offenders.append(f"{name}:{number}：{found.group(0)}")
        self.assertEqual(offenders, [], "换成中性的账号名、主机名或 `<用户目录>` 这类占位")

    def test_the_whole_tree_guard_catches_the_shape_it_describes(self):
        """门槛自身也要能被证伪，否则它可能只是一段永远为真的代码。"""
        for sample in ("https://10.234.56.78", "PEACH_SHARED_SMB_HOST=192.168.1.9",
                       "review_writer_origin = 'https://10.0.0.5'", "172.31.112.1",
                       r"C:\Users\alice\Desktop\peach", "/Users/alice/Desktop/peach",
                       "/home/alice/peach", "smb://alice@alice-mbp.local/peach-sync",
                       "peach-two.local", "https://Alice-PC.local/"):
            self.assertTrue(MACHINE_COORDINATE.search(sample), sample)
        for allowed in ("127.0.0.1", "0.0.0.0", "224.0.0.251", "198.18.0.1",
                        "192.0.2.2", "198.51.100.162", "203.0.113.1",
                        "peach.local", "peach-writer.local", "peach-reader.local",
                        "https://<writer>.local", "<mounts.local>/x",
                        ".claude/settings.local.json", "_https._tcp.local.",
                        "copy_database(self.shared, self.local)", "peach-sync",
                        r"C:\Users\<user>\Desktop\peach", "~/Desktop/<用户目录>/peach",
                        r"C:\Users\RUNNER~1\AppData\Local\Temp", "/home/runner/work",
                        "https://www.pixiv.net/users/93812377",
                        "Chrome/131.0.0.0", "10.0.26200.1234", r"R:\media",
                        "https://github.com/peach-mitao/peach",
                        "Copyright (C) 2026 longmeidao"):
            self.assertIsNone(MACHINE_COORDINATE.search(allowed), allowed)

    def test_the_repository_identity_covers_its_own_github_owner(self):
        """归属名要真的从仓库元数据里解析出来，取不到就等于没剔除。"""
        with (REPO / "pyproject.toml").open("rb") as handle:
            urls = tomllib.load(handle)["project"]["urls"]
        owners = {found.group(1).casefold() for found in
                  (_GITHUB_OWNER.search(url) for url in urls.values()) if found}
        self.assertTrue(owners, "pyproject 的项目 URL 里应当写出仓库的 GitHub 归属")
        self.assertLessEqual(owners, _repository_identity(),
                             "仓库自己的 GitHub 归属没被认出来")

    def test_the_identity_exemption_does_not_hollow_out_the_this_machine_guard(self):
        """剔除只放行「光提到归属名」，同一个名字落进坐标形状照旧拦下。

        用合成的名字断言，这样判据本身不写出任何真实归属。
        """
        owner, other = "example-owner", "peach-two"
        guard = _this_machine_guard({owner, other}, frozenset({owner}))
        for allowed in (f"https://github.com/{owner}/peach",
                        f"io.github.{owner}.peach.tray",
                        f"Copyright (C) 2026 {owner}",
                        f"{owner}-peach", f"{owner}.Peach"):
            self.assertIsNone(guard.search(allowed), allowed)
        for blocked in (f"smb://{other}/peach-sync", f"/Volumes/{other}/media"):
            self.assertIsNotNone(guard.search(blocked), blocked)
        for blocked in (f"/Users/{owner}/Desktop/peach", f"/home/{owner}/peach",
                        rf"C:\Users\{owner}\peach-data",
                        f"smb://{owner}@{owner}-mbp.local/peach-sync"):
            self.assertIsNotNone(MACHINE_COORDINATE.search(blocked), blocked)
        self.assertIsNone(_this_machine_guard({owner}, frozenset({owner})),
                          "名字全被剔掉时不许返回会匹配任何位置的空正则")


class ArchitectureDriftTests(unittest.TestCase):
    """架构文档点名的模块必须真的存在。

    它逼不出「架构变了要重写描述」，但能逼出「模块改名或删掉之后文档还在指它」——
    那种失真最难发现，因为文档读起来一切正常。
    """

    def test_every_module_the_document_names_still_exists(self):
        text = (DOCS / "ARCHITECTURE.md").read_text(encoding="utf-8")
        source = pathlib.Path(peach.__file__).parent
        missing = sorted({
            name for name in re.findall(r"`src/peach/(\w+)\.py`", text)
            if not (source / f"{name}.py").is_file()
        })
        self.assertEqual(missing, [],
                         "架构文档还在指已经不存在的模块，改名或删除时请一起改")


#: `` `docs/HANDOFF.md`「数据安全」「身份、来源与标识采集」 `` 这种写法：一个路径后面跟一个或多个节名。
SECTION_REFERENCE = re.compile(r"`([\w./-]+\.md)`((?:「[^」]+」)+)")


def _headings(path: pathlib.Path) -> list[str]:
    return [line.lstrip("#").strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("#")]


class SectionReferenceTests(unittest.TestCase):
    """入口文件、技能与文档指向的「某文件某一节」必须真的有那一节。

    2026-09-13 盘点时四个技能引用的 `docs/HANDOFF.md` 节名已经改掉了，技能读起来一切
    正常，照着去找的人才发现找不到。节名跟着文件走，改标题的人看不见谁在引用它。
    """

    def test_every_referenced_section_exists(self):
        sources = [REPO / "AGENTS.md", REPO / "CLAUDE.md", REPO / "README.md",
                   *sorted((REPO / ".claude" / "skills").glob("*/SKILL.md")),
                   *sorted(DOCS.glob("*.md"))]
        broken = []
        for source in sources:
            for target, names in SECTION_REFERENCE.findall(source.read_text(encoding="utf-8")):
                # 只写文件名的引用在 `docs/` 下找唯一同名文件（技能里引用参考快照就是这么写的）。
                candidates = [REPO / target, source.parent / target,
                              *(sorted(DOCS.rglob(target)) if "/" not in target else [])]
                found = next((path for path in candidates if path.is_file()), None)
                if found is None:
                    broken.append(f"{source.relative_to(REPO).as_posix()} -> {target}（文件不存在）")
                    continue
                headings = _headings(found)
                for name in re.findall(r"「([^」]+)」", names):
                    if not any(heading == name or heading.startswith(name) for heading in headings):
                        broken.append(f"{source.relative_to(REPO).as_posix()} -> {target}「{name}」")
        self.assertEqual(broken, [],
                         "这些引用指向的节已经不存在，改标题时请一起改引用：\n  " + "\n  ".join(broken))


#: ADR 文件名 `NNNN-主题.md` 与首行标题 `# ADR-NNNN：…`。
_ADR_FILE = re.compile(r"^(\d{4})-[^/\\]+\.md$")
_ADR_TITLE = re.compile(r"^# ADR-(\d{4})(?!\d)")


def _adr_numbering_problems(adr_dir: pathlib.Path) -> list[str]:
    """`docs/adr/` 里编号不唯一、文件名与标题编号不符的文件，一条一行。"""
    problems: list[str] = []
    by_number: dict[str, list[str]] = {}
    for path in sorted(adr_dir.glob("*.md")):
        named = _ADR_FILE.match(path.name)
        if named is None:
            problems.append(f"{path.name}：文件名不是 NNNN-主题.md")
            continue
        number = named.group(1)
        by_number.setdefault(number, []).append(path.name)
        first = next(iter(path.read_text(encoding="utf-8").splitlines()), "")
        titled = _ADR_TITLE.match(first)
        if titled is None or titled.group(1) != number:
            problems.append(f"{path.name}：首行标题应以 `# ADR-{number}` 开头，实际是 {first[:40]!r}")
    for number, names in sorted(by_number.items()):
        if len(names) > 1:
            problems.append(f"ADR-{number} 重号：{'、'.join(names)}")
    return problems


class AdrNumberingTests(unittest.TestCase):
    """ADR 编号全树唯一，文件名编号与首行标题编号一致。

    编号由新建 ADR 的人自己取「当前最大号加一」，两个并行工作树各取一次就会撞号，
    而没有任何门槛看编号，ready 与 integrate 两边都放行：2026-09-23 两份 ADR-0044
    就是这样先后合进 master 的。编号是别处引用 ADR 的唯一钥匙，撞号后「见 ADR-0044」
    指向哪一份说不清。后合入的一方 ready 前必须纳入 master 重跑选测，本门槛就在那一步拦住。
    """

    ADR_DIR = DOCS / "adr"

    def test_every_adr_number_is_unique_and_matches_its_title(self):
        problems = _adr_numbering_problems(self.ADR_DIR)
        self.assertEqual(
            problems, [],
            "ADR 编号有冲突：后合入的那份改用当前最大号加一，文件名与首行标题一起改，"
            "再全树搜旧编号的引用：\n  " + "\n  ".join(problems),
        )

    def test_the_guard_catches_the_shape_it_describes(self):
        """门槛自身也要能被证伪：重号、标题错号、文件名不合形状都要报出来。"""
        with tempfile.TemporaryDirectory() as raw:
            sample = pathlib.Path(raw)
            for name, title in (("0044-scraping.md", "# ADR-0044：甲"),
                                ("0044-booru.md", "# ADR-0044：乙"),
                                ("0045-packs.md", "# ADR-0046：丙"),
                                ("0046-bursts.md", "# ADR-00460：丁"),
                                ("0047-fine.md", "# ADR-0047：戊"),
                                ("notes.md", "# 笔记")):
                (sample / name).write_text(title + "\n正文\n", encoding="utf-8")
            problems = _adr_numbering_problems(sample)
        self.assertEqual(problems, [
            "0045-packs.md：首行标题应以 `# ADR-0045` 开头，实际是 '# ADR-0046：丙'",
            "0046-bursts.md：首行标题应以 `# ADR-0046` 开头，实际是 '# ADR-00460：丁'",
            "notes.md：文件名不是 NNNN-主题.md",
            "ADR-0044 重号：0044-booru.md、0044-scraping.md",
        ])

    def test_adding_an_adr_selects_this_guard(self):
        """新建 ADR 的分支按改动选测时必须跑到本门槛，否则撞号照样能过 ready。"""
        scopes, _ = test_runner.scopes_for_changes(["docs/adr/0099-example.md"])
        self.assertTrue(
            any("test_repo_hygiene.py" in test_runner.SCOPES.get(scope, ()) for scope in scopes),
            f"改 docs/adr/ 选到的域 {scopes} 都没有登记 test_repo_hygiene.py",
        )


if __name__ == "__main__":
    unittest.main()
