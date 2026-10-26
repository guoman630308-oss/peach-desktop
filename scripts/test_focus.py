"""统一测试入口的定向调试；不签发正式验收记录。"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock


def selection(root: Path, selectors: list[str]) -> tuple[unittest.TestSuite, tuple[str, ...]]:
    suite = unittest.TestSuite()
    browsers = []
    names = []
    for selector in dict.fromkeys(selectors):
        if selector.startswith("frontend/e2e/") and selector.endswith(".test.ts"):
            path = (root / selector).resolve()
            if not path.is_relative_to((root / "frontend/e2e").resolve()) or not path.is_file():
                raise ValueError(f"浏览器用例不存在或超出 e2e 目录：{selector}")
            browsers.append(path.relative_to(root / "frontend").as_posix())
        else:
            name = selector.removeprefix("tests/").removesuffix(".py")
            if not re.fullmatch(r"test_\w+(?:\.\w+)*", name, flags=re.ASCII) \
                    or not (root / "tests" / (name.split(".")[0] + ".py")).is_file():
                raise ValueError(f"用例必须是 tests/test_*.py、Python 用例名或 frontend/e2e/*.test.ts：{selector}")
            names.append(name)
    if "e2e/setup.test.ts" in browsers:
        names.append("test_web_e2e.SetupE2ETests")
    if any(name != "e2e/setup.test.ts" for name in browsers):
        names.append("test_web_e2e.WebE2ESmokeTests")
    sys.path[:0] = [str(root), str(root / "tests")]
    try:
        for name in dict.fromkeys(names):
            suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
    finally:
        del sys.path[:2]
    return suite, tuple(browsers)


def run(runner, args) -> int:
    try:
        suite, browsers = selection(runner.ROOT, args.focus)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    print("定向调试：只运行指定用例，不签发正式记录；提交前仍需 auto。", flush=True)
    flags = {"PEACH_TEST_FOCUS": "1", "PEACH_E2E_FILES": json.dumps(browsers)} if browsers else {}
    with mock.patch.dict(os.environ, flags):
        result = unittest.TextTestRunner(verbosity=2, resultclass=runner.TimedResult).run(suite)
    return 0 if result.wasSuccessful() and result.testsRun > 0 else 1
