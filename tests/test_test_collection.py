"""禁止把测试写在 `if __name__ == "__main__"` 之后的缩进块里。

`unittest.main()` 那两行一旦被插在类中间，后面所有缩进 4 格的 `def test_` 都会
被解析成 `if` 块的语句而不是类方法——**不报错、不告警，测试直接消失**：

    class ReviewQueueTests(unittest.TestCase):
        def test_a(self): ...

    if __name__ == "__main__":
        unittest.main()

        def test_b(self): ...      # 永远不会被收集

2026-08-19 发现 `test_rm_web.py` 与 `test_web_ui.py` 各有一段这样的死区，
合计 16 条测试从写下起就没跑过，其中包括复核页三类候选的全部数据层断言。
「测试通过」于是并不代表那条路径被覆盖，比没有测试更危险。

按项目约定，要求每次成立的规则得由机制强制，不能只写进文档。
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent


def _is_main_guard(node: ast.stmt) -> bool:
    """认出 `if __name__ == "__main__":`。"""
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    left = node.test.left
    return isinstance(left, ast.Name) and left.id == "__name__"


def dead_definitions(path: Path) -> list[str]:
    """返回被埋在入口块里的类与函数名。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    buried = []
    for node in tree.body:
        if not _is_main_guard(node):
            continue
        for inner in node.body:
            if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                buried.append(f"{path.name}:{inner.lineno} {inner.name}")
    return buried


class TestCollectionTests(unittest.TestCase):
    def test_no_test_is_buried_after_the_entry_block(self):
        buried = []
        for path in sorted(TESTS_DIR.glob("test_*.py")):
            buried.extend(dead_definitions(path))
        self.assertEqual(buried, [],
                         "入口块必须放在文件最后；它后面缩进的定义永远不会被收集")

    def test_the_guard_actually_catches_the_shape_it_describes(self):
        broken = TESTS_DIR / "_collection_guard_sample.py"
        broken.write_text(
            'import unittest\n'
            'if __name__ == "__main__":\n'
            '    unittest.main()\n'
            '\n'
            '    def test_lost(self):\n'
            '        pass\n',
            encoding="utf-8")
        try:
            found = dead_definitions(broken)
        finally:
            broken.unlink()
        self.assertEqual(len(found), 1)
        self.assertIn("test_lost", found[0])


if __name__ == "__main__":
    unittest.main()
