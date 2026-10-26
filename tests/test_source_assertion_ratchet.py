"""源码文本断言棘轮：「某段字符串在某个仓库文件里」这类断言只许减少，不许增加。

这类断言读出 `web/app.js`、样式表或文档的原文，再 `assertIn` 一段写法。它守住的是
「这几个字符还在」，不是「功能是对的」：换一种等价写法就红，把条件写反了照样绿。
2026-08-01 到 2026-09-23，`tests/test_web_ui.py` 被 670 个提交改过，净增约 1.3 万行，
几乎每次界面修复都在同步改这些断言。改一处样式要同时改它的测试，测试就只是在复述实现。

要守的东西换成能跑的验收：
- 行为（点了提交什么、状态怎么变、算出什么值）写 vitest（`frontend/test/`）或
  `tests/test_web_js.py`，后端写调用真函数或真接口的测试；
- 用户定过的设计决定写 `frontend/e2e/design-*.test.ts`，读 `getComputedStyle`；
- 布局与运行期不变量进 `frontend/e2e/smoke.test.ts`；
- 全仓都该成立的写法约定写成扫描器或 lint（如 `test_complexity_ratchet.py`），
  一条规则一个检查，而不是一处写法一条断言。

计数是纯 AST：一个断言调用点算 1——`assertIn`／`assertNotIn` 的被查对象、
`assertRegex`／`assertNotRegex` 的文本、`assertTrue(x in 文本)` 的右侧，只要来自
`read_text()` 读出的仓库文件就算；模块里自己定义的、内部读了这种文本的 `assert*`
辅助方法（如 `assertPageContains`）每次调用也算 1。仓库文件指路径表达式里出现
`__file__` 的那些，临时目录里造的文件不算。

基线只能降：删掉一条就把基线改小，降到 0 就把那一行删掉。一个文件的计数超过基线
时，别改写成别的断言绕过去，按上面的去向换成行为验收。
"""
import ast
import pathlib
import unittest
from types import MappingProxyType

TESTS = pathlib.Path(__file__).resolve().parent

#: 键是测试文件名，值是这个文件里源码文本断言的调用点数。
BASELINE: dict[str, int] = {
    "test_agency_entity.py": 2,
    "test_buildinfo.py": 3,
    "test_cloudflared_packaging.py": 2,
    "test_dependency_policy.py": 25,
    "test_desktop_settings.py": 5,
    "test_face_detect.py": 3,
    "test_fastapi_api.py": 4,
    "test_follow_assets.py": 1,
    "test_follow_web.py": 1,
    "test_frontend_build.py": 20,
    "test_job_status.py": 1,
    "test_metadata_library.py": 5,
    "test_repo_hygiene.py": 1,
    "test_scripts.py": 13,
    "test_social_avatar_harvest.py": 2,
    "test_studio_icon_variants.py": 2,
    "test_studio_site_harvest.py": 1,
    "test_subprocess_encoding.py": 2,
    "test_tray.py": 3,
    "test_web_js.py": 1,
    "test_web_ui.py": 14,
    "test_windows_update.py": 3,
}

MEMBERSHIP = {"assertIn", "assertNotIn"}
PATTERN = {"assertRegex", "assertNotRegex"}
TRUTH = {"assertTrue", "assertFalse"}
READS = {"read_text", "read_bytes"}


def _outermost_functions(body):
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node
        elif isinstance(node, ast.ClassDef):
            yield from _outermost_functions(node.body)


class _Taint:
    """一个模块里的污点表：哪些名字是仓库路径、哪些是读出来的仓库文本。"""

    def __init__(self, tree: ast.Module):
        self.path_attrs: set[str] = set()
        self.text_attrs: set[str] = set()
        self.helpers: set[str] = set()
        self.assert_helpers: set[str] = set()
        # 只取最外层的函数与方法；嵌套函数随外层一起遍历，单列会把它的断言数两遍。
        self.functions = list(_outermost_functions(tree.body))
        # 模块级只看顶层语句：函数里的局部名字不是模块级的仓库路径。
        top = [node for node in tree.body
               if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        self.envs: dict[ast.AST, tuple[set[str], set[str]]] = {}
        for _ in range(10):
            before = (len(self.path_attrs), len(self.text_attrs), len(self.helpers),
                      len(self.assert_helpers))
            self.module_env = self._env(top, ({"__file__"}, set()))
            for function in self.functions:
                env = self._env(function.body, self.module_env)
                self.envs[function] = env
                self._learn(function, env)
            if before == (len(self.path_attrs), len(self.text_attrs), len(self.helpers),
                          len(self.assert_helpers)):
                break

    def _self_attr(self, node: ast.AST) -> str | None:
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id in {"self", "cls"}):
            return node.attr
        return None

    def is_path(self, node: ast.AST, env) -> bool:
        paths, _ = env
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id in paths:
                return True
            if isinstance(child, ast.Attribute) and child.attr == "__file__":
                return True
            if self._self_attr(child) in self.path_attrs:
                return True
        return False

    def is_text(self, node: ast.AST, env) -> bool:
        _, texts = env
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id in texts:
                return True
            if self._self_attr(child) in self.text_attrs:
                return True
            if isinstance(child, ast.Call):
                func = child.func
                if (isinstance(func, ast.Attribute) and func.attr in READS
                        and self.is_path(func.value, env)):
                    return True
                if isinstance(func, ast.Name) and func.id in self.helpers:
                    return True
                if self._self_attr(func) in self.helpers:
                    return True
        return False

    def _env(self, body, base):
        """在 `body` 里按赋值、循环与推导式传播污点，直到不再变化。"""
        paths, texts = set(base[0]), set(base[1])
        bindings = []
        for statement in body:
            for node in ast.walk(statement):
                if isinstance(node, ast.Assign):
                    bindings.extend((target, node.value) for target in node.targets)
                elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value:
                    bindings.append((node.target, node.value))
                elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                    bindings.append((node.target, node.iter))
                elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                    bindings.append((node.optional_vars, node.context_expr))
        changed = True
        while changed:
            changed = False
            env = (paths, texts)
            for target, value in bindings:
                path, text = self.is_path(value, env), self.is_text(value, env)
                if not (path or text):
                    continue
                for name in ast.walk(target):
                    if isinstance(name, ast.Name):
                        if path and name.id not in paths:
                            paths.add(name.id)
                            changed = True
                        if text and name.id not in texts:
                            texts.add(name.id)
                            changed = True
        return paths, texts

    def _learn(self, function, env):
        """从一个函数里学到：它写进 self／cls 的属性、它是不是返回文本的辅助函数。"""
        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    attr = self._self_attr(target)
                    if attr is None:
                        continue
                    if self.is_text(node.value, env):
                        self.text_attrs.add(attr)
                    elif self.is_path(node.value, env):
                        self.path_attrs.add(attr)
            elif (isinstance(node, ast.Return) and node.value is not None
                  and self.is_text(node.value, env)):
                self.helpers.add(function.name)
        if function.name.startswith("assert") and any(
                isinstance(node, ast.expr) and self.is_text(node, env)
                for node in ast.walk(function)):
            self.assert_helpers.add(function.name)

    def count(self) -> int:
        total = 0
        for function in self.functions:
            if function.name in self.assert_helpers:
                continue
            env = self.envs[function]
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and self._counts(node, env):
                    total += 1
        return total

    def _counts(self, call: ast.Call, env) -> bool:
        name = self._self_attr(call.func)
        if name is None:
            return False
        if name in self.assert_helpers:
            return True
        args = call.args
        if name in MEMBERSHIP and len(args) >= 2:
            return self.is_text(args[1], env)
        if name in PATTERN and args:
            return self.is_text(args[0], env)
        if name in TRUTH and args and isinstance(args[0], ast.Compare):
            compare = args[0]
            return (any(isinstance(op, (ast.In, ast.NotIn)) for op in compare.ops)
                    and any(self.is_text(side, env) for side in compare.comparators))
        return False


def source_assertions(source: str) -> int:
    """一段测试源码里源码文本断言的调用点数。"""
    return _Taint(ast.parse(source)).count()


def survey() -> dict[str, int]:
    found = {}
    for path in sorted(TESTS.glob("test_*.py")):
        count = source_assertions(path.read_text(encoding="utf-8"))
        if count:
            found[path.name] = count
    return found


class SourceAssertionRatchetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.current = MappingProxyType(survey())

    def test_the_counter_tells_repository_text_from_runtime_output(self):
        source = (
            "import pathlib, unittest\n"
            "ROOT = pathlib.Path(__file__).resolve().parents[1]\n"
            "def stylesheet():\n"
            "    return (ROOT / 'web' / 'a.css').read_text()\n"
            "class T(unittest.TestCase):\n"
            "    @classmethod\n"
            "    def setUpClass(cls):\n"
            "        cls.page = (ROOT / 'web' / 'app.js').read_text()\n"
            "    def assertPageContains(self, needle):\n"
            "        self.assertTrue(needle in self.page)\n"
            "    def test_source(self):\n"
            "        app = (ROOT / 'web' / 'app.js').read_text()\n"
            "        body = app[app.index('a'):]\n"
            "        self.assertIn('x', body)\n"                      # 1：切片仍是源码
            "        self.assertNotIn('y', stylesheet())\n"           # 2：辅助函数返回源码
            "        self.assertRegex(self.page, 'z')\n"              # 3：setUpClass 读的属性
            "        self.assertPageContains('w')\n"                  # 4：自带的源码断言辅助
            "        for key in ('a', 'b'):\n"
            "            self.assertIn(key, app)\n"                   # 5：调用点只算一次
            "        joined = ''.join(p.read_text() for p in (ROOT / 'web').glob('*.js'))\n"
            "        self.assertTrue('q' in joined)\n"                # 6：推导式读的也算
            "    def test_behaviour(self, tmp='t'):\n"
            "        out = pathlib.Path(tmp) / 'x.txt'\n"
            "        self.assertIn('x', out.read_text())\n"           # 临时文件不算
            "        self.assertIn('ok', run(ROOT / 'scripts' / 's.py'))\n"  # 跑出来的输出不算
        )
        self.assertEqual(source_assertions(source), 6)

    def test_no_test_file_grew_past_its_baseline(self):
        grown = sorted(f"{name}: {count}（基线 {BASELINE.get(name, 0)}）"
                       for name, count in self.current.items() if count > BASELINE.get(name, 0))
        self.assertEqual(grown, [],
                         "这些测试文件的源码文本断言多了。别改写成别的断言绕过，按本文件开头的"
                         "去向换成行为验收：\n  " + "\n  ".join(grown))

    def test_the_baseline_only_ratchets_down(self):
        current = self.current
        stale = sorted(f"{name}: 现在 {current.get(name, 0)}，基线 {value}"
                       for name, value in BASELINE.items() if current.get(name, 0) < value)
        self.assertEqual(stale, [],
                         "基线落后于测试：删掉断言后把这里改小，降到 0 就删掉那一行，"
                         "不给将来的增长留余量：\n  " + "\n  ".join(stale))


if __name__ == "__main__":
    unittest.main()
