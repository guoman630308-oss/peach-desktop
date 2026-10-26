"""按设计只在某个平台上成立的用例条件，以及外部前置条件缺失时的处理。"""

from __future__ import annotations

import os
from typing import NoReturn
import unittest


def missing_prerequisite(reason: str) -> NoReturn:
    """缺 Node、依赖、ffmpeg 或浏览器这类外部前置条件时调用。

    本机没装时显式跳过，免得整个测试域红掉让人绕过入口。CI 的 runner 由工作流负责
    装齐，缺了就是工作流写错：跳过会让用例在 CI 里永远不执行，所以那里判失败。
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        raise AssertionError(f"CI 缺少前置条件，工作流应当装好：{reason}")
    raise unittest.SkipTest(reason)


#: 直接把临时目录填入 `[media.locations]` 的用例只适用于 Windows，因为该表保存
#: Windows 格式的数据库路径前缀。POSIX 临时路径无法通过目录访问权限检查。
#: 跨平台用例应将 Windows 格式的前缀填入 `[media.locations]`，将临时媒体目录填入
#: `[media.mounts]`，按产品的路径转换规则验证。
windows_ledger_roots = unittest.skipUnless(os.name == "nt", "数据库中的目录前缀是 Windows 盘符")
