"""`scripts/` 的两条硬门槛，按 AST 判而不是按源码字符串判。

写在测试里而不是文档里，是因为这两件事都反复回归过：路径写死过一轮又一轮，
写库脚本也出现过「跑起来就直接改库」的形态。文档里的提醒不会拦住任何一次提交。

判据落在 AST 上而不是 `grep`：注释和文档串里出现 `R:\\peach-data` 是在说明历史，
不是在读它；反过来，`"UPDATE asset SET ..."` 拼在多行字符串里 grep 也照样能漏。
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SRC = ROOT / "src" / "peach"

#: Peach 数据目录的写死形态。搬到内置盘之后 `R:\\peach-data` 已经不存在，而写死的路径不会报
#: 「配置过时」——它只会安静地建一个空库，或者往一个没人看的目录里写日志。用户主目录
#: 下的具体位置同样算写死：它对任何别的用户都不成立。
DATA_ROOT_LITERAL = re.compile(
    r"R:[\\/]peach-data|[\\/](?:Users|home)[\\/][^\\/]+[\\/]Desktop", re.I)

#: 只有这两个模块可以谈论平台与 Peach 数据目录的具体位置，其余一律从它们取。
PATH_AUTHORITIES = {"config.py", "platform.py"}

#: 门槛（b）的例外，逐个写清理由。
DATA_ROOT_ALLOWLIST: set[str] = set()

DML = re.compile(r"\b(INSERT|UPDATE|DELETE|REPLACE)\b", re.I)
DML_CLAUSE = re.compile(r"\b(INTO|FROM|SET)\b", re.I)

#: 门槛（c）的例外，逐个写清理由。
#:
#: 判据是「这个脚本是不是把既有账本行按复核结论改掉」。是的话必须有 `--apply`：
#: 复核产物和真实写入之间必须有一道人为闸门。下面这些不是那种形态。
APPLY_ALLOWLIST = {
    # 常驻批处理，由 `peach.jobs.job_main` + PidFileLock 驱动，没有「先出复核表再决定
    # 写不写」这一步：它们写的是自己刚生成的探测结果与九宫格产物。停不停由任务控制，
    # 不由一个命令行开关控制。
    "probe.py",
    "sheets.py",
    # 摄取入口。它新增行而不是按复核结论改既有行，整个用途就是写：把 115 服务端
    # 算好的 SHA1 灌进来。目前还没有备份闸门，是后续该补的，不是本门槛要管的形态。
    "sync_sha1_115.py",
}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """模块／类／函数的文档串节点。它们是叙述，不是被执行的值。"""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            found.add(id(body[0].value))
    return found


def _live_strings(path: Path) -> list[tuple[int, str]]:
    """去掉文档串之后，源码里真正会被当值用的字符串常量。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstring_nodes(tree)
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in skip]


def _apply_flags(path: Path) -> set[str]:
    """脚本声明过的 `--apply*` 开关，含由 `add_ledger_write_args` 代为声明的。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    flags: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (node.func.attr if isinstance(node.func, ast.Attribute)
                else node.func.id if isinstance(node.func, ast.Name) else "")
        if name == "add_ledger_write_args":
            flags.add("--apply")
        if name == "add_argument":
            flags.update(arg.value for arg in node.args
                         if isinstance(arg, ast.Constant) and isinstance(arg.value, str))
    return {flag for flag in flags if flag.startswith("--apply")}


class ScriptPathPolicyTests(unittest.TestCase):
    """门槛（b）：Peach 数据目录只在 `peach.config` / `peach.platform` 里出现。"""

    def test_no_script_writes_a_data_root_path_by_hand(self):
        offenders = []
        for path in sorted(SCRIPTS.glob("*.py")):
            if path.name in DATA_ROOT_ALLOWLIST:
                continue
            offenders += [f"{path.name}:{line}" for line, text in _live_strings(path)
                          if DATA_ROOT_LITERAL.search(text)]
        self.assertEqual(offenders, [], "改成从 peach.config 取（DATABASE_PATH、"
                                        "GENERATED_DIR、LOG_DIR、STATE_DIR 等）")

    def test_only_config_and_platform_know_where_the_data_root_is(self):
        offenders = []
        for path in sorted(SRC.glob("*.py")):
            if path.name in PATH_AUTHORITIES:
                continue
            offenders += [f"{path.name}:{line}" for line, text in _live_strings(path)
                          if DATA_ROOT_LITERAL.search(text)]
        self.assertEqual(offenders, [])

    def test_the_guard_catches_the_shape_it_describes(self):
        """门槛自身也要能被证伪，否则它可能只是一段永远为真的代码。"""
        self.assertTrue(DATA_ROOT_LITERAL.search(r"R:\peach-data\database\ledger.db"))
        self.assertTrue(DATA_ROOT_LITERAL.search("C:/Users/<user>/Desktop/peach"))
        self.assertTrue(DATA_ROOT_LITERAL.search("/Users/<user>/Desktop/peach"))
        self.assertIsNone(DATA_ROOT_LITERAL.search("peach-data/generated"))


class ScriptWriteGatePolicyTests(unittest.TestCase):
    """门槛（c）：会改账本的脚本必须自己声明 `--apply`。"""

    def test_every_script_with_dml_declares_an_apply_switch(self):
        offenders = []
        for path in sorted(SCRIPTS.glob("*.py")):
            if path.name in APPLY_ALLOWLIST:
                continue
            has_dml = any(DML.search(text) and DML_CLAUSE.search(text)
                          for _line, text in _live_strings(path))
            if has_dml and not _apply_flags(path):
                offenders.append(path.name)
        self.assertEqual(offenders, [], "真实写入默认必须是 dry-run："
                                        "用 peach.scripting.add_ledger_write_args")

    def test_the_allowlist_has_no_stale_entries(self):
        """例外表本身会过期。脚本删了或者已经补上 `--apply`，条目就该跟着走。"""
        for name in sorted(APPLY_ALLOWLIST | DATA_ROOT_ALLOWLIST):
            with self.subTest(script=name):
                self.assertTrue((SCRIPTS / name).is_file(), f"{name} 已不存在")
        for name in sorted(APPLY_ALLOWLIST):
            with self.subTest(script=name):
                self.assertFalse(_apply_flags(SCRIPTS / name),
                                 f"{name} 已经有 --apply，从例外表里去掉")

    def test_the_guard_catches_the_shape_it_describes(self):
        self.assertTrue(DML.search("INSERT OR IGNORE INTO asset_tag(asset_id,tag)"))
        self.assertTrue(DML.search("UPDATE asset SET path=? WHERE id=?"))
        self.assertIsNone(DML.search("SELECT count(*) FROM asset"))


class ReleaseEntryImportTests(unittest.TestCase):
    """门槛（d）：发布入口导入时只靠标准库。

    Release 工作流核对标签提交那一步只有 `setup-python` 装的裸解释器，依赖要到后面的
    构建步骤才装——次序是故意的：不给一个没过 CI 的提交花十分钟构建。入口只要多引一条
    第三方依赖，那一步就 `ModuleNotFoundError`，而此时标签已经推上去了：版本有标签、却没有
    任何可下载的制品，而发布流程的全部意义就是「每个 X.Y.Z 对应一份制品」。
    """

    def test_the_release_entry_imports_with_a_bare_interpreter(self):
        base = Path(sys.base_prefix)
        interpreter = next((path for path in (base / "python.exe",
                                              base / "bin" / "python3",
                                              base / "bin" / "python") if path.is_file()), None)
        if interpreter is None:
            self.skipTest(f"找不到不带 site-packages 的基底解释器：{base}")
        # `-S` 不载入 site：基底解释器自己的 site-packages 里也可能装着 filelock，
        # 载入了就会让重依赖混过这道门槛。`-E` 不认 PYTHONPATH，理由相同。
        done = subprocess.run(
            [str(interpreter), "-S", "-E", "-X", "utf8", "-c",
             "import scripts.release_tag as entry; print(entry.MASTER_WRITER)"],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=False)
        self.assertEqual(done.returncode, 0,
                         "发布入口得能裸解释器导入：" + (done.stderr or done.stdout).strip())


if __name__ == "__main__":
    unittest.main()
