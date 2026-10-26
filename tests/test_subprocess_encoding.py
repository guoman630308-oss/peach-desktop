"""禁止 subprocess 文本模式不写 encoding。

Windows 上 Python 的文本 I/O 不写 `encoding` 时按本机 ANSI 解码（简中系统是
`cp936`/GBK）。子进程输出只要含中文，读取线程就抛 `UnicodeDecodeError`，而
`subprocess.run` **本身不抛异常、返回码正常**，`stdout` 静默变成 `None`：

    >>> subprocess.run(["git","log","-1","--format=%s","282f8d9"],
    ...                capture_output=True, text=True)
    UnicodeDecodeError: 'gbk' codec can't decode byte 0xae in position 2
    AttributeError: 'NoneType' object has no attribute 'strip'

这个坑修过一次（`282f8d9`，改的正是 `tests/test_job_status.py`），但只覆盖了
同文件里的一个调用，其余 5 处照旧——包括 `src/peach/versioning.py` 这段生产
代码，它读 git 输出，而本仓库的提交信息就是中文的。

显式写父进程的 `encoding="utf-8"` 仍不保证正确：如果子进程是 Peach 自己的
Python CLI，Windows 管道默认输出 GBK，父进程强制按 UTF-8 解码仍会产生乱码。
这种由 Peach 控制的子进程还必须用 `PYTHONIOENCODING=utf-8` 固定输出端。

按项目约定
「必须每次成立的规则要由脚本、测试或 hook 强制」，这条测试就是那个机制。
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED = ("src", "scripts", "tests")
TEXT_FLAGS = ("text", "universal_newlines")


def _subprocess_calls(tree: ast.AST):
    """产出 (行号, 关键字字典)，只看 subprocess 的进程启动调用。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        if "subprocess" not in name:
            continue
        if not name.endswith((".run", ".Popen", ".check_output", ".call",
                              ".check_call")):
            continue
        yield node.lineno, {kw.arg: ast.unparse(kw.value)
                            for kw in node.keywords if kw.arg}


def offenders() -> list[str]:
    found = []
    for folder in SCANNED:
        for path in sorted((ROOT / folder).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for line, keywords in _subprocess_calls(tree):
                text_mode = any(keywords.get(flag) == "True" for flag in TEXT_FLAGS)
                if text_mode and not keywords.get("encoding"):
                    found.append(f"{path.relative_to(ROOT).as_posix()}:{line}")
    return found


class SubprocessEncodingTests(unittest.TestCase):
    def test_no_text_mode_subprocess_without_an_explicit_encoding(self):
        found = offenders()
        self.assertEqual(found, [], "这些调用会在中文输出上静默丢空 stdout：\n  "
                                    + "\n  ".join(found))

    def test_the_guard_actually_detects_the_pattern(self):
        # 守卫自身必须可证伪，否则它可能只是恒真。
        tree = ast.parse("import subprocess\n"
                         "subprocess.run(['git'], capture_output=True, text=True)\n")
        calls = list(_subprocess_calls(tree))
        self.assertEqual(len(calls), 1)
        self.assertNotIn("encoding", calls[0][1])

    def test_the_wrapper_blind_spot_is_covered_by_hand(self):
        """守卫只认 `subprocess.*` 字面调用，包装器调用它看不见。

        `VersionManager._git` 走的是注入的 `self._execute`，AST 里没有
        `subprocess` 字样，所以这条守卫抓不到它——而它恰恰是暴露最大的一处：
        读 git 输出，本仓库的提交信息是中文的。盲点用一条显式断言补上。
        """
        source = (ROOT / "src" / "peach" / "versioning.py").read_text(encoding="utf-8")
        self.assertIn('encoding="utf-8"', source)
        self.assertIn('errors="replace"', source)

    def test_the_guard_accepts_a_locked_encoding(self):
        tree = ast.parse("import subprocess\n"
                         "subprocess.run(['git'], text=True, encoding='utf-8')\n")
        _, keywords = next(iter(_subprocess_calls(tree)))
        self.assertEqual(keywords.get("encoding"), "'utf-8'")


if __name__ == "__main__":
    unittest.main()
