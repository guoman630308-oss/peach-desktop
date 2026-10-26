import datetime
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SKILL_TEMPLATE = """---
name: {name}
description: {description}
---

# 标题

最后复核：{reviewed}
证据来源：测试夹具。
"""


class ContextBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = load_script("check_context_budget")

    def test_repository_context_files_pass(self):
        problems, reviewed = self.checker.check_repo(ROOT)
        self.assertEqual(problems, [], "\n".join(problems))
        self.assertTrue(reviewed, "仓库应至少有一个带最后复核日期的技能")

    def test_current_budgets_stay_under_ceiling(self):
        self.assertEqual(self.checker.check_budget_history(), [])
        for name, budget in self.checker.LINE_BUDGET.items():
            self.assertLessEqual(budget, self.checker.MAX_EVER[name], name)
        self.assertLessEqual(
            self.checker.SKILL_MAX_LINES, self.checker.MAX_EVER["SKILL.md"]
        )
        self.assertLessEqual(
            self.checker.DESCRIPTION_MAX_CHARS, self.checker.MAX_EVER["description"]
        )
        for name, budget in self.checker.BYTE_BUDGET.items():
            self.assertLessEqual(budget, self.checker.MAX_EVER_BYTES[name], name)

    def test_raising_a_budget_without_a_reason_is_reported(self):
        original = dict(self.checker.BUDGET_CHANGES)
        try:
            self.checker.BUDGET_CHANGES = {
                "AGENTS.md": [(70, "2026-08-17", "初始值"), (80, "2026-08-18", "初始值")]
            }
            problems = self.checker.check_budget_history()
        finally:
            self.checker.BUDGET_CHANGES = original
        self.assertTrue(any("是上调" in problem for problem in problems), problems)

    def test_budget_above_ceiling_is_reported(self):
        original = dict(self.checker.BUDGET_CHANGES)
        try:
            self.checker.BUDGET_CHANGES = {
                "AGENTS.md": [(70, "2026-08-17", "初始值"), (300, "2026-08-18", "内容实在下沉不了")]
            }
            problems = self.checker.check_budget_history()
        finally:
            self.checker.BUDGET_CHANGES = original
        self.assertTrue(any("越过天花板" in problem for problem in problems), problems)

    def test_over_budget_entry_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            budget = self.checker.LINE_BUDGET["AGENTS.md"]
            (root / "AGENTS.md").write_text("行\n" * (budget + 1), encoding="utf-8")
            problems = self.checker.check_line_budgets(root)
        self.assertTrue(any("超出预算" in problem for problem in problems), problems)

    def test_raising_a_byte_budget_without_a_reason_is_reported(self):
        original = dict(self.checker.BYTE_BUDGET_CHANGES)
        try:
            self.checker.BYTE_BUDGET_CHANGES = {
                "AGENTS.md": [(8 * 1024, "2026-09-03", "初始值"), (12 * 1024, "2026-09-04", "")]
            }
            problems = self.checker.check_budget_history()
        finally:
            self.checker.BYTE_BUDGET_CHANGES = original
        self.assertTrue(any("字节预算" in problem for problem in problems), problems)

    def test_byte_budget_above_ceiling_is_reported(self):
        original = dict(self.checker.BYTE_BUDGET_CHANGES)
        try:
            self.checker.BYTE_BUDGET_CHANGES = {
                "AGENTS.md": [(64 * 1024, "2026-09-03", "初始值")]
            }
            problems = self.checker.check_budget_history()
        finally:
            self.checker.BYTE_BUDGET_CHANGES = original
        self.assertTrue(any("越过天花板" in problem for problem in problems), problems)

    def test_a_file_within_the_line_budget_can_still_be_over_the_byte_budget(self):
        """行数合规不代表体积合规：这正是 STATUS.md 155 KB 那次没被拦住的原因。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            budget = self.checker.BYTE_BUDGET["AGENTS.md"]
            (root / "AGENTS.md").write_text("字" * budget, encoding="utf-8")
            self.assertEqual(
                self.checker.check_line_budgets(root),
                ["CLAUDE.md: 文件缺失"],
            )
            problems = self.checker.check_byte_budgets(root)
        self.assertTrue(
            any("AGENTS.md" in problem and "字节" in problem for problem in problems),
            problems,
        )

    def test_over_long_line_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text(
                "短行\n" + "长" * (self.checker.MAX_LINE_CHARS + 1) + "\n",
                encoding="utf-8",
            )
            problems = self.checker.check_line_lengths(root)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("第 2 行", problems[0])

    def test_skill_frontmatter_and_review_line_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / ".claude" / "skills" / "broken"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# 没有 frontmatter\n", encoding="utf-8")

            named = root / ".claude" / "skills" / "mismatch"
            named.mkdir(parents=True)
            (named / "SKILL.md").write_text(
                SKILL_TEMPLATE.format(
                    name="other", description="描述", reviewed="2026-01-01"
                ),
                encoding="utf-8",
            )

            problems, reviewed = self.checker.check_skills(root)

        self.assertTrue(any("frontmatter" in problem for problem in problems), problems)
        self.assertTrue(any("不一致" in problem for problem in problems), problems)
        self.assertEqual([rel for rel, _ in reviewed], [".claude/skills/mismatch/SKILL.md"])

    def test_long_description_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / ".claude" / "skills" / "verbose"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                SKILL_TEMPLATE.format(
                    name="verbose",
                    description="描" * (self.checker.DESCRIPTION_MAX_CHARS + 1),
                    reviewed="2026-01-01",
                ),
                encoding="utf-8",
            )
            problems, _ = self.checker.check_skills(root)
        self.assertTrue(any("description" in problem for problem in problems), problems)

    def test_index_and_files_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text(
                "| 触发 | `.claude/skills/missing/SKILL.md` |\n", encoding="utf-8"
            )
            skill_dir = root / ".claude" / "skills" / "present"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                SKILL_TEMPLATE.format(
                    name="present", description="描述", reviewed="2026-01-01"
                ),
                encoding="utf-8",
            )
            problems = self.checker.check_skill_index(root)
        self.assertIn("AGENTS.md 索引指向不存在的技能：.claude/skills/missing/SKILL.md", problems)
        self.assertIn("技能未登记进 AGENTS.md 索引：.claude/skills/present/SKILL.md", problems)

    def test_stale_review_uses_fixed_today(self):
        reviewed = [
            ("fresh", datetime.date(2026, 8, 1)),
            ("old", datetime.date(2025, 1, 1)),
        ]
        stale = self.checker.stale_reviews(reviewed, datetime.date(2026, 8, 17))
        self.assertEqual([rel for rel, _ in stale], ["old"])


if __name__ == "__main__":
    unittest.main()
