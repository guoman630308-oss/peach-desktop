"""模块分层门槛。

Peach 是模块化单体，分层靠约定而不是包结构，所以约定必须有人守。这里守一条：
**web 层之外的模块不许 import web 层。**

这条规则不是凭空立的。`catalog_rules.py` 原名 `web_logic.py`，而 `repository.py`
（数据层）要用它的 `is_jav_code`，`taste_history.py` 要用 `LENGTH_TAGS`——数据层
import 一个叫 web 的模块，读代码的人只会得出「分层反了」的结论。实际反的是名字，
但名字骗人和结构错了一样难查。改名之后加这条断言，让下一次真反了的时候直接报错。
"""
import ast
import importlib
import pathlib
import tempfile
import typing
import unittest

import peach
from peach.web_contract import WebContract


SOURCE_ROOT = pathlib.Path(peach.__file__).parent

#: web 层自己。层内互相依赖是正常的。
#:
#: 按文件名推导而不是手写清单。写死一份清单的话，拆分出 `web_review`、
#: `web_resource_sync`、`web_settings` 之后没人回来更新，那三个模块就一直不设防。
#: 守规则的东西自己先漂了，是这条门槛最容易出的故障。
WEB_MODULES = {path.stem for path in SOURCE_ROOT.glob("web_*.py")}

#: FastAPI 适配层：`api` 是组装点，`routes_*` 是它挂上去的路由表。把 web 层接到
#: HTTP 上是这一层的职责，方向是对的。
#:
#: 和 `WEB_MODULES` 同样按文件名推导。只写着 `api` 的话，`create_app` 拆成
#: `routes_auth`/`routes_pages`/`routes_media`/`routes_api` 之后再拆一个模块就会被判成
#: 「非 web 模块 import 了 web 层」——门槛报的是假警，改的人只会来把它加进清单。
ROUTE_MODULES = {path.stem for path in SOURCE_ROOT.glob("routes_*.py")}
COMPOSITION_ROOTS = {"api"} | ROUTE_MODULES


def _local_imports(path: pathlib.Path) -> set[str]:
    """取出一个模块 import 的本包模块名（`from .x import y` 与 `from . import x`）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            if node.module:
                names.add(node.module.split(".")[0])
            else:
                names.update(alias.name for alias in node.names)
    return names


#: 项目根：`src/peach` 的上两级。脚本和源码要一起扫。
PROJECT_ROOT = SOURCE_ROOT.parent.parent


def _python_sources() -> list[pathlib.Path]:
    return sorted(SOURCE_ROOT.glob("*.py")) + sorted((PROJECT_ROOT / "scripts").glob("*.py"))


class SharedRuleTests(unittest.TestCase):
    """领域规则只许有一份实现。

    番号归一化最容易长出第二份：`catalog_rules.normalise_code_key`、
    `scripts/fetch_jav_covers.normalise_code`、`scripts/scrape_codes.normalise`
    三处逐字相同过，靠一句「与 scrape_codes 同口径」的注释维持一致——注释不是门槛。

    番号既是身份判定也是封面缓存键：三份一旦漂移，同一部片会解析出两个键，
    封面缓存静默失配，而两边的数都「对」，只是口径不同。
    """

    #: 归一化实现的指纹。`FC2-PPV-` 的拼装和番号形态的正则，凑齐就是又抄了一份。
    FINGERPRINTS = ('f"FC2-PPV-', r'([A-Z]+)-?(\d+)$')

    def test_release_code_normalisation_exists_once(self):
        offenders = []
        for path in _python_sources():
            if path.name == "catalog_rules.py":
                continue
            text = path.read_text(encoding="utf-8")
            if all(mark in text for mark in self.FINGERPRINTS):
                offenders.append(path.name)
        self.assertEqual(
            offenders, [],
            "番号归一化又被抄了一份，请改 import catalog_rules.normalise_code_key",
        )

    def test_the_review_csv_encoding_is_declared_once(self):
        """复核 CSV 的编码只许在一处写死。

        AGENTS.md 把 CSV 定为「复核产物」：可机读、可重放。它的编码因此不是风格问题——
        `utf-8-sig` 的 BOM 决定 Excel 打开时中文是不是乱码，`newline=""` 决定 Windows
        下行间会不会多出空行。两条都属于「写的时候一切正常、几天后有人真去开那份表才
        发现坏了」，靠人眼复查拦不住。

        这两条此前在 46 个读写点各写一遍，而且已经开始藏：有两处把参数顺序写成
        `"w", newline="", encoding="utf-8-sig"`，按规范顺序 grep 根本扫不到。所以这里
        断言的是字面量本身只出现一次，而不是某个固定写法。
        """
        offenders = []
        for path in _python_sources():
            if path.name == "review_csv.py":
                continue
            if '"utf-8-sig"' in path.read_text(encoding="utf-8"):
                offenders.append(path.name)
        self.assertEqual(
            offenders, [],
            "编码不要再写一遍：用 peach.review_csv 的 read_rows/write_rows，"
            "确实只能自己开文件的（流式写）就用它的 ENCODING 常量",
        )

    def test_batch_scripts_share_one_cli_tail(self):
        """批处理脚本的 main() 收尾只许有一份。

        它此前抄了四份。共用的不只是形状，还有「pid 锁必须包住整个 run」和「策略
        异常交出自己的退出码而不是抛栈」这两条约定；散成四份，下一个脚本照抄时漏掉
        锁或吞掉退出码都不会有人发现。
        """
        offenders = [path.name for path in sorted((PROJECT_ROOT / "scripts").glob("*.py"))
                     if "with PidFileLock(args.lock):" in path.read_text(encoding="utf-8")]
        self.assertEqual(offenders, [],
                         "又有脚本自己写了 main() 收尾，请改调 peach.jobs.job_main")

    def test_pure_rules_are_imported_from_the_policy_module_not_through_web(self):
        """纯规则要直接从 `catalog_rules` 取，不要借道 web 层的再导出。

        `web_contract` 把 `is_jav_code` 再导出了一遍，于是脚本写成
        `from peach.web_contract import is_jav_code`——为一条纯规则拉起整个 web 层，
        读代码的人还会以为这条规则属于 web。
        """
        offenders = []
        for path in sorted((PROJECT_ROOT / "scripts").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for rule in ("is_jav_code", "normalise_code_key", "LENGTH_TAGS"):
                if "from peach.web_contract import" in text and rule in text.split(
                        "from peach.web_contract import", 1)[1].split("\n", 1)[0]:
                    offenders.append(f"{path.name} → web_contract.{rule}")
        self.assertEqual(offenders, [],
                         "纯规则请直接从 peach.catalog_rules 取")


class ContractConformanceTests(unittest.TestCase):
    """域模块声明的窄契约，真的 `WebContract` 必须全部满足。

    `Protocol` 在运行期什么都不检查，域模块又只照着自己那份 Protocol 写调用，
    于是「契约上写了、实现里没有」不会有任何东西报错，一路要等到用户点那个按钮。

    实测事故：`web_links.LinkContract` 声明了 `write_connection`，`WebContract` 只有
    `write_transaction`，`/api/links/prune` 在线上直接 500；而 `tests/test_web_links.py`
    的 FakeContract 恰好把两个名字都给了，测试因此全绿。这条门槛就是补那次的窗口。
    """

    @staticmethod
    def _declared(protocol) -> set[str]:
        """一份 Protocol 声明的能力：带注解的属性 + 协议体里定义的方法。"""
        names = set(typing.get_type_hints(protocol))
        names.update(name for name, value in vars(protocol).items()
                     if callable(value) and not name.startswith("_"))
        return names

    @staticmethod
    def _web_protocols() -> list[type]:
        """web 层各模块自己定义的 Protocol，按文件名发现而不是手写清单。"""
        found: list[type] = []
        for path in sorted(SOURCE_ROOT.glob("web_*.py")):
            module = importlib.import_module(f"peach.{path.stem}")
            for obj in vars(module).values():
                if (isinstance(obj, type) and getattr(obj, "_is_protocol", False)
                        and obj.__module__ == module.__name__ and obj not in found):
                    found.append(obj)
        return found

    def test_every_web_protocol_is_satisfied_by_the_real_contract(self):
        protocols = self._web_protocols()
        self.assertGreaterEqual(len(protocols), 5, "没发现域契约，门槛已空转")
        with tempfile.TemporaryDirectory() as tmp:
            # 构造只是赋值路径和建锁，不碰磁盘也不连库；临时目录仅用于坐实这一点。
            contract = WebContract(pathlib.Path(tmp).resolve() / "ledger.db")
            missing = [f"{protocol.__name__}.{name}"
                       for protocol in protocols
                       for name in sorted(self._declared(protocol))
                       if not hasattr(contract, name)]
        self.assertEqual(
            missing, [],
            "域契约声明了 WebContract 没有的能力：要么补上实现，要么改契约声明",
        )


class LayeringTests(unittest.TestCase):
    def test_the_web_layer_is_actually_discovered(self):
        """推导出来的 web 层不能是空的。

        改成按文件名推导之后，清单不会再漂；但万一命名约定变了，glob 会安静地返回
        空集，上面那条断言就永远成立——门槛还在，只是不再拦任何东西。
        """
        self.assertGreaterEqual(len(WEB_MODULES), 4, "没发现 web 层，门槛已空转")
        self.assertIn("web_contract", WEB_MODULES)

    def test_the_route_layer_is_actually_discovered(self):
        """`routes_*` 也不能是空的，理由同上：它现在参与放行判定。

        `COMPOSITION_ROOTS` 一旦被 glob 推成只有 `api`，上面那条 web 层门槛会立刻
        对四个路由模块报假警；反过来，如果有人改名让 glob 全落空，放行集合缩小，
        门槛会变严而不是变松——但报出来的偏移点是错的，所以这里直接钉住。
        """
        self.assertGreaterEqual(len(ROUTE_MODULES), 4, "没发现路由层，放行集合已漂")
        self.assertIn("routes_api", ROUTE_MODULES)

    def test_nothing_below_the_route_layer_imports_it(self):
        """路由层是最上面一层：只有 `api` 和它自己的同层模块可以 import `routes_*`。

        方向必须单向。`routes_media` 里放着「取哪个文件、报哪个缓存头」这类 HTTP
        决策，一旦被 `media`、`previews` 之类的下层 import，FastAPI 就渗进了不需要
        知道 HTTP 的地方，测试也从此必须先建一个 app 才能跑。
        """
        offenders = []
        for path in sorted(SOURCE_ROOT.glob("*.py")):
            module = path.stem
            if module in COMPOSITION_ROOTS:
                continue
            for imported in sorted(_local_imports(path)):
                if imported in ROUTE_MODULES:
                    offenders.append(f"{module} → {imported}")
        self.assertEqual(
            offenders, [],
            "只有 api 与 routes_* 自己可以依赖路由层：需要共享的东西下沉到领域模块",
        )

    def test_only_web_modules_and_the_composition_root_import_the_web_layer(self):
        offenders = []
        for path in sorted(SOURCE_ROOT.glob("*.py")):
            module = path.stem
            if module in WEB_MODULES or module in COMPOSITION_ROOTS:
                continue
            for imported in sorted(_local_imports(path)):
                if imported in WEB_MODULES:
                    offenders.append(f"{module} → {imported}")
        self.assertEqual(
            offenders, [],
            "非 web 模块不得依赖 web 层：把共享的纯规则下沉到 catalog_rules 一类的策略模块",
        )

    def test_route_modules_only_depend_on_the_auth_routes(self):
        """路由层内部也是单向的：`routes_auth` 在最下面，别的 `routes_*` 只许 import 它。

        登录、首启与错误三页共用的薄壳 `page_shell` 和配置页要的运行信息住在 web 层（`web_entry`），
        `same_origin` 住在 `routes_auth`。共享件放进任何一个 `routes_*`，别的路由模块就得反向去拿，
        成一个靠函数内 import 撑住的环——import 图上看不出来，读代码的人也说不清谁在谁上面。
        函数内的 import 一样算：`_local_imports` 扫整棵语法树，不只看文件顶部。
        """
        offenders = []
        for path in sorted(SOURCE_ROOT.glob("routes_*.py")):
            module = path.stem
            allowed = set() if module == "routes_auth" else {"routes_auth"}
            for imported in sorted(_local_imports(path) & ROUTE_MODULES - allowed):
                offenders.append(f"{module} → {imported}")
        self.assertEqual(
            offenders, [],
            "routes_* 之间只许依赖 routes_auth：两个路由都要的东西下沉到 web_* 模块",
        )

    def test_catalog_rules_depends_on_nothing_inside_peach(self):
        """最底下那层必须是纯的：只要它开始 import 别的 Peach 模块，就不再是策略了。"""
        self.assertEqual(
            _local_imports(SOURCE_ROOT / "catalog_rules.py"), set(),
            "catalog_rules 是最底层纯规则，不能依赖任何 Peach 模块",
        )

    def test_the_old_web_logic_name_is_gone(self):
        self.assertFalse(
            (SOURCE_ROOT / "web_logic.py").exists(),
            "web_logic.py 已改名为 catalog_rules.py；两个同时存在意味着改名没做干净",
        )
        # 只看 import，不看全文：`catalog_rules` 的 docstring 要讲清楚它改过名，
        # 那句话里出现旧名字是应该的。
        for path in sorted(SOURCE_ROOT.glob("*.py")):
            self.assertNotIn(
                "web_logic", _local_imports(path),
                f"{path.name} 仍在 import 旧模块名 web_logic",
            )


if __name__ == "__main__":
    unittest.main()
