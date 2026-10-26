"""让连接器的默认退避一睡就红。

连接器对 GET 失败按 0、1、2、4、8 秒退避，那是抓取任务的节奏。输入框联想、圆标补全
这些敲一下字就问一次的路径必须传自己的 `sleeper`，站点一挂立刻回空，不能让人干等
15 秒。这里把 `_BaseConnector.__init__` 里 `sleeper` 的默认值换成一炸就红的桩：
没传 `sleeper` 又真的进了退避的连接器，会在这一步露出来，而不是把测试拖成 15 秒。

改的是 `__kwdefaults__` 而不是 `time.sleep`：默认值在 `def` 时就绑死了 `time.sleep`
那个函数对象，事后替换模块属性够不着它。
"""
from __future__ import annotations

from unittest import mock

from peach import follow_sources


def no_real_backoff():
    def boom(seconds: float) -> None:
        raise AssertionError(f"交互路径不该等退避，却要睡 {seconds} 秒")

    return mock.patch.dict(follow_sources._BaseConnector.__init__.__kwdefaults__,
                           {"sleeper": boom})
