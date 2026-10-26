"""文案只描述最终状态的门槛。判据与放行方式见 `scripts/check_copy_final_state.py`。

写成测试而不是文档里的一条提醒，是因为提醒已经证明拦不住：用户四次要求删掉界面上
「没必要的说明文字和中间态说明」，四次都有遗漏。遗漏的形态是固定的——被要求去掉
某个东西之后，交付物里到处留着「（无那个东西）」：标题、注释、说明都在讲被否掉的
中间状态，而不是只描述最终的成品。
"""
from __future__ import annotations

import sys
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_copy_final_state as checker  # noqa: E402


class CopyFinalStateTests(unittest.TestCase):
    def test_project_markdown_passes_seiso_stable_rules(self):
        executable = shutil.which("seiso", path=str(Path(sys.executable).parent))
        self.assertIsNotNone(executable, "缺少文档检查器，请按 docs/TESTING.md 安装开发依赖。")
        result = subprocess.run(
            [executable, "check", "--output-format", "concise"], cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_copy_narrates_a_rejected_or_superseded_state(self):
        findings = [item for item in checker.scan_repo(ROOT) if item.severity == "error"]
        self.assertEqual(
            findings, [],
            "文案只写最终状态。逐行修掉下列位置，或确认它是真实事故记录后在行尾加"
            f" {checker.DISABLE_MARKER}：\n"
            + "\n".join(str(item) for item in findings))

    def test_the_disable_marker_works_line_by_line(self):
        source = "# 以前这里是另一种做法\n# 以前这里是另一种做法  # copy-lint-disable-line\n"
        found = checker.scan_python("sample.py", source)
        self.assertEqual([item.line for item in found], [1])

    def test_python_identifiers_are_code_and_literals_are_prose(self):
        # 变量名是被执行的值：`legacy_id` 扫进来，`source='legacy:asset'` 这类取值
        # 就会被判成违规。字符串字面量相反，命令行帮助和页面文案都从那里出。
        self.assertEqual(
            checker.scan_python("sample.py", "legacy_id = 1\nprior_state = 2\n"), [])
        found = checker.scan_python("sample.py", "HELP = '以前的写法'\n")
        self.assertEqual([item.line for item in found], [1])

    def test_f_strings_include_static_prose_and_keep_expressions_as_code(self):
        for source in ('message = f"以前这里是旧实现 {value}"',
                       'message = f"{value} 以前这里是旧实现"',
                       'message = f"{f\'以前这里是旧实现 {value}\'}"'):
            with self.subTest(source=source):
                self.assertTrue(any(item.severity == "error" for item in checker.scan_python("sample.py", source)))
        self.assertEqual(checker.scan_python("sample.py", 'message = f"{previously_used_to}"'), [])

    def test_conditional_instructions_are_advisory(self):
        found = checker.scan_lines("sample.md", "如果以前配置过代理，请核对代理地址。", skip_code_fences=True)
        self.assertTrue(found)
        self.assertTrue(all(item.severity == "warning" for item in found))

    def test_html_reads_visible_text_without_inspecting_attributes_as_code(self):
        found = checker.scan_html("sample.html", '<p data-id="previously">正常</p><input title="以前这里是旧实现">')
        self.assertEqual([(item.line, item.severity) for item in found], [(1, "error")])

    def test_markdown_code_fences_carry_commands_not_prose(self):
        source = "正文\n```\ngit log --grep 以前\n```\n以前\n"
        found = checker.scan_lines("sample.md", source, skip_code_fences=True)
        self.assertEqual([item.line for item in found], [5])

    def test_every_rule_reports_file_line_and_term(self):
        found = checker.scan_lines("sample.md", "# 曾经有另一种做法\n",
                                   skip_code_fences=True)
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].path, found[0].line, found[0].term),
                         ("sample.md", 1, "曾经"))
        self.assertIn("sample.md:1", str(found[0]))

    def test_the_scan_covers_every_declared_surface(self):
        """扫描面写死成 glob 就会漏掉新目录，所以直接核对它收到了哪些文件。"""
        picked = {path.relative_to(ROOT).as_posix() for path in checker.targets(ROOT)}
        for required in ("AGENTS.md", "README.md", "docs/WRITING.md",
                         "web/index.html",
                         "src/peach/web_entity.py",
                         "tests/test_web_ui.py",
                         ".claude/skills/peach-worktree/SKILL.md"):
            self.assertIn(required, picked)
        self.assertFalse(any(path.startswith("frontend/src/") for path in picked))
        # ADR 正文的内容就是决策与被否掉的备选方案，本门槛不适用。
        self.assertFalse(any(path.startswith("docs/adr/") for path in picked))


if __name__ == "__main__":
    unittest.main()
