"""建一个跑完全部 migration 的空账本，供测试使用。

手写 `CREATE TABLE asset(...)` 的问题不是啰嗦，是它会和 `migrations/` 悄悄漂开，
而且漂开之后测试不会变红——它会在一个线上不存在的表结构上继续绿。真实存在过的
例子：手写版 `activity_event` 只有 `created_at`，真实 schema 是 `occurred_at`
外加一个 NOT NULL 的 `source`；手写版 `asset` 比真实的少十一列。

模板只建一次，之后按文件复制。24 个 migration 跑一遍约 0.7 秒，每个 `setUp` 都跑
会给套件加上分钟级的开销，而 SQLite 的库就是一个文件，复制它和建空库同价。

真实账本 `peach-data/database/ledger.db` 与这里无关，测试只用临时目录。
"""
from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "migrations"

_template: Path | None = None


def _ledger_template() -> Path:
    global _template
    if _template is None:
        from peach import migrations

        holder = Path(tempfile.mkdtemp(prefix="peach-ledger-template-")).resolve()
        atexit.register(shutil.rmtree, holder, ignore_errors=True)
        candidate = holder / "template.db"
        if not migrations.upgrade(candidate, MIGRATIONS_DIR):
            raise RuntimeError(f"没有可应用的 migration：{MIGRATIONS_DIR}")
        _template = candidate
    return _template


def fresh_ledger(directory: Path | str, name: str = "ledger.db") -> Path:
    """在 `directory` 下放一个空账本并返回它的路径。

    路径先 `.resolve()`：CI runner 的临时目录都是别名（macOS 的 `/var`、Windows 的
    `RUNNER~1` 短名），拿未 resolve 的路径去断言只会在 CI 上红。
    """
    destination = Path(directory).resolve() / name
    shutil.copyfile(_ledger_template(), destination)
    return destination
