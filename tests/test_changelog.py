import contextlib
import io
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import changelog
from scripts.changelog import Commit, entry_for, group_entries, has_section, promote, render_body
from scripts.version_bump import VersionError

REPO = "owner/repo"

#: 一份最小的 `CHANGELOG.md`：头部、润色过的未发布节、一个发布节、底部链接块。
DOCUMENT = """# 变更日志

说明段。

## [未发布]

### 新增

- 配置页新增桌面快捷方式开关。

### 修复

- 忙态提示会说明正在进行的动作。

## [0.27.1] - 2026-09-06

第四个预发布。

[未发布]: https://github.com/owner/repo/compare/v0.27.1...HEAD
[0.27.1]: https://github.com/owner/repo/releases/tag/v0.27.1
"""


class EntryTests(unittest.TestCase):
    def test_only_user_facing_types_become_entries(self):
        self.assertEqual(entry_for(Commit("feat: 配置页并入管理菜单", "")),
                         ("新增", "配置页并入管理菜单"))
        self.assertEqual(entry_for(Commit("fix(web): 忙态提示说明在做什么", "")),
                         ("修复", "web：忙态提示说明在做什么"))
        self.assertEqual(entry_for(Commit("perf(media): 首段缩到 0.67 秒", "")),
                         ("变更", "**播放**：首段缩到 0.67 秒"))
        for subject in ("docs: 记一笔", "test: 补断言", "chore(release): 版本 0.29.1",
                        "refactor(follow): 登记表收口", "style: 空行", "随手写的主题"):
            with self.subTest(subject=subject):
                self.assertIsNone(entry_for(Commit(subject, "")))

    def test_known_scopes_become_area_labels_and_unknown_ones_wait_for_a_human(self):
        self.assertEqual(entry_for(Commit("feat(review): 候选支持多选", "")),
                         ("新增", "**复核**：候选支持多选"))
        self.assertEqual(entry_for(Commit("fix(avatars): 只装更大的那张", "")),
                         ("修复", "**采集**：只装更大的那张"))
        self.assertEqual(entry_for(Commit("fix(web): 忙态提示说明在做什么", "")),
                         ("修复", "web：忙态提示说明在做什么"))
        self.assertEqual(entry_for(Commit("feat: 没有 scope 就没有标签", "")),
                         ("新增", "没有 scope 就没有标签"))

    def test_every_mapped_label_is_in_the_closed_vocabulary(self):
        self.assertLessEqual(set(changelog.AREA_BY_SCOPE.values()),
                             set(changelog.AREAS))

    def test_development_only_scopes_stay_out_even_under_fix(self):
        self.assertIsNone(entry_for(Commit("fix(tests): 断言跟上新名字", "")))
        self.assertIsNone(entry_for(Commit("fix(docs): 链接失效", "")))

    def test_breaking_changes_are_told_by_the_bang_or_the_footer(self):
        self.assertEqual(entry_for(Commit("fix!: 数据库路径改成 location 键", "")),
                         ("破坏性变化", "数据库路径改成 location 键"))
        self.assertEqual(
            entry_for(Commit("feat(config): 换掉盘符键", "BREAKING CHANGE: 设置文件要重写")),
            ("破坏性变化", "**配置**：设置文件要重写"))

    def test_security_work_gets_its_own_group(self):
        self.assertEqual(entry_for(Commit("fix(security): 凭据不再进日志", "")),
                         ("安全", "凭据不再进日志"))

    def test_groups_keep_the_documented_order_and_drop_duplicates(self):
        grouped = group_entries([
            Commit("fix: 同一句", ""), Commit("feat: 新东西", ""),
            Commit("fix: 同一句", ""), Commit("fix!: 破坏性的", ""),
        ])
        self.assertEqual(list(grouped), ["破坏性变化", "新增", "修复"])
        self.assertEqual(grouped["修复"], ["同一句"])

    def test_the_body_is_markdown_with_one_blank_line_between_groups(self):
        body = render_body(group_entries([Commit("feat: 甲", ""), Commit("fix: 乙", "")]))
        self.assertEqual(body, "### 新增\n\n- 甲\n\n### 修复\n\n- 乙")


class PromoteTests(unittest.TestCase):
    def promoted(self, document: str = DOCUMENT, **overrides) -> str:
        options = {"date": "2026-09-07", "repo": REPO, "previous": "v0.27.1"}
        return promote(document, overrides.pop("version", "0.28.0"),
                       **{**options, **overrides})

    def test_the_unreleased_wording_moves_into_the_new_section(self):
        updated = self.promoted()
        self.assertIn("## [0.28.0] - 2026-09-07", updated)
        self.assertIn("- 配置页新增桌面快捷方式开关。", updated.split("## [0.28.0]")[1])
        self.assertTrue(has_section(updated, "0.28.0"))

    def test_an_empty_unreleased_section_is_left_behind_at_the_top(self):
        updated = self.promoted()
        between = updated.split(f"## [{changelog.UNRELEASED}]")[1].split("## [0.28.0]")[0]
        self.assertEqual(between.strip(), "")

    def test_the_sections_stay_in_descending_order(self):
        updated = self.promoted()
        self.assertLess(updated.index("## [0.28.0]"), updated.index("## [0.27.1]"))

    def test_both_compare_links_are_written(self):
        updated = self.promoted()
        self.assertIn("[未发布]: https://github.com/owner/repo/compare/v0.28.0...HEAD", updated)
        self.assertIn("[0.28.0]: https://github.com/owner/repo/compare/v0.27.1...v0.28.0",
                      updated)
        self.assertIn("[0.27.1]: https://github.com/owner/repo/releases/tag/v0.27.1", updated)

    def test_the_first_release_links_to_its_own_tag(self):
        document = DOCUMENT.replace(
            "## [0.27.1] - 2026-09-06\n\n第四个预发布。\n\n", "").replace(
            "[0.27.1]: https://github.com/owner/repo/releases/tag/v0.27.1\n", "").replace(
            "compare/v0.27.1...HEAD", "compare/v0.1.0...HEAD")
        updated = self.promoted(document, version="0.1.0", previous=None)
        self.assertIn("[0.1.0]: https://github.com/owner/repo/releases/tag/v0.1.0", updated)

    def test_a_draft_fills_in_when_nobody_wrote_the_unreleased_section(self):
        bare = DOCUMENT.replace(
            "### 新增\n\n- 配置页新增桌面快捷方式开关。\n\n"
            "### 修复\n\n- 忙态提示会说明正在进行的动作。\n\n", "")
        updated = promote(bare, "0.28.0", date="2026-09-07", repo=REPO, previous="v0.27.1",
                          fallback="### 新增\n\n- 起草出来的那条")
        self.assertIn("- 起草出来的那条", updated)

    def test_a_release_with_nothing_to_say_is_refused(self):
        bare = DOCUMENT.replace(
            "### 新增\n\n- 配置页新增桌面快捷方式开关。\n\n"
            "### 修复\n\n- 忙态提示会说明正在进行的动作。\n\n", "")
        with self.assertRaisesRegex(VersionError, "没有可记录的变化"):
            promote(bare, "0.28.0", date="2026-09-07", repo=REPO, previous="v0.27.1")

    def test_the_same_version_cannot_be_promoted_twice(self):
        with self.assertRaisesRegex(VersionError, "已经有 0.27.1"):
            self.promoted(version="0.27.1")

    def test_a_document_without_the_required_anchors_is_refused(self):
        with self.assertRaisesRegex(VersionError, "未发布"):
            self.promoted("# 变更日志\n\n## [0.27.1] - 2026-09-06\n")
        without_link = DOCUMENT.replace(
            "[未发布]: https://github.com/owner/repo/compare/v0.27.1...HEAD\n", "")
        with self.assertRaisesRegex(VersionError, "链接"):
            self.promoted(without_link)


class SectionTests(unittest.TestCase):
    """取出一节的正文：发布前要人确认的就是这段文字。"""

    def test_a_section_stops_at_the_next_heading(self):
        self.assertEqual(changelog.section_of(DOCUMENT, changelog.UNRELEASED),
                         "### 新增\n\n- 配置页新增桌面快捷方式开关。\n"
                         "\n### 修复\n\n- 忙态提示会说明正在进行的动作。")

    def test_the_last_section_stops_before_the_link_block(self):
        self.assertEqual(changelog.section_of(DOCUMENT, "0.27.1"), "第四个预发布。")

    def test_a_version_without_a_section_yields_nothing(self):
        self.assertEqual(changelog.section_of(DOCUMENT, "0.28.0"), "")

    def test_a_release_refuses_raw_commit_scopes_before_it_is_shipped(self):
        raw = "## [0.8.0] - 2026-09-19\n\n### 修复\n\n- web：修好一处界面\n"
        with self.assertRaisesRegex(VersionError, "未归类.*web"):
            changelog.require_labeled_entries(raw, "0.8.0")

        labeled = raw.replace("web：", "**界面**：")
        changelog.require_labeled_entries(labeled, "0.8.0")


class NotesTests(unittest.TestCase):
    """Release 页正文：那一节原文加安装提示，缺那一节就拒绝。"""

    def test_the_notes_are_the_section_followed_by_the_install_note(self):
        self.assertEqual(changelog.notes(DOCUMENT, "0.27.1"),
                         f"第四个预发布。\n\n{changelog.INSTALL_NOTE}\n")

    def test_a_version_without_a_section_cannot_be_released(self):
        with self.assertRaisesRegex(VersionError, "0.28.0"):
            changelog.notes(DOCUMENT, "0.28.0")

    def test_the_command_line_names_what_it_failed_to_produce(self):
        """`--notes` 失败说的是 Release 正文，不借用定版那条「变更日志未更新」。"""
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / changelog.CHANGELOG).write_text(DOCUMENT, encoding="utf-8")
            out = io.StringIO()
            with mock.patch.object(changelog, "ROOT", Path(folder)), \
                    contextlib.redirect_stdout(out):
                code = changelog.main(["--notes", "0.28.0"])
        self.assertEqual(code, 1)
        self.assertTrue(out.getvalue().startswith("Release 正文未生成："), out.getvalue())
        self.assertNotIn("变更日志未更新", out.getvalue())

    def test_the_release_workflow_reads_the_notes_it_does_not_inline_them(self):
        """固定文案只活在 `INSTALL_NOTE` 一处；工作流在标签提交上生成文件再交给 gh。"""
        workflow = (Path(changelog.ROOT) / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/changelog.py --notes", workflow)
        self.assertIn("--to release/RELEASE_NOTES.md", workflow)
        self.assertIn("--notes-file RELEASE_NOTES.md", workflow)
        self.assertNotIn("--notes 'Windows", workflow)


class DueTests(unittest.TestCase):
    """该不该发下一版。判据只看使用者那一侧，不看集成了多少次。"""

    def _due(self, subjects, *, days):
        commits = [Commit(subject, "") for subject in subjects]
        with mock.patch.object(changelog, "read_commits", return_value=commits), \
                mock.patch.object(changelog, "_waiting_days", return_value=days), \
                mock.patch.object(changelog.version_bump, "release_range",
                                  return_value="v0.1.0..HEAD"):
            return changelog.due(Path("."))

    def test_a_hundred_refactors_are_zero_changes_to_a_user(self):
        """这条判据要跟「集成了多少次」分开，不然又变成按工作量发版。"""
        found = self._due([f"refactor(web): 第 {n} 次收口" for n in range(100)], days=30)
        self.assertEqual((found["entries"], found["due"], found["why"]), (0, False, []))

    def test_the_weekly_slot_needs_something_to_put_in_it(self):
        subjects = ["fix(web): 修一处"]
        self.assertFalse(self._due(subjects, days=changelog.DUE_DAYS - 1)["due"])
        ripe = self._due(subjects, days=changelog.DUE_DAYS)
        self.assertTrue(ripe["due"])
        self.assertIn(f"距上一版 {changelog.DUE_DAYS} 天", ripe["why"][0])

    def test_enough_entries_do_not_wait_for_the_slot(self):
        subjects = [f"feat(web): 第 {n} 件事" for n in range(changelog.DUE_ENTRIES)]
        found = self._due(subjects, days=0)
        self.assertTrue(found["due"])
        self.assertIn("不必等满周期", found["why"][0])

    def test_breaking_changes_and_security_fixes_never_wait(self):
        for subject, group in (("feat(config)!: 换掉盘符键", changelog.BREAKING),
                               ("fix(security): 凭据不再进日志", changelog.SECURITY_GROUP)):
            with self.subTest(group=group):
                found = self._due([subject], days=0)
                self.assertTrue(found["due"])
                self.assertIn(group, found["why"][-1])
                self.assertIn("不等周期", found["why"][-1])

    def test_the_counts_it_reports_are_what_calibrates_the_threshold(self):
        """阈值是没有样本时的起点，所以每次都要把实际值报出来。"""
        found = self._due(["feat: 甲", "fix: 乙", "fix: 丙"], days=2)
        self.assertEqual((found["entries"], found["days"]), (3, 2))
        self.assertEqual(found["groups"], {"新增": 1, "修复": 2})


class ShippedChangelogTests(unittest.TestCase):
    """仓库里那份 `CHANGELOG.md` 自己要符合脚本认的形状。"""

    def setUp(self):
        self.document = (Path(changelog.ROOT) / changelog.CHANGELOG).read_text(encoding="utf-8")

    def test_the_anchors_the_release_entry_needs_are_all_present(self):
        self.assertRegex(self.document, r"(?m)^## \[未发布\]$")
        self.assertRegex(self.document, r"(?m)^\[未发布\]: https://github\.com/")
        self.assertIn("keepachangelog.com", self.document)
        self.assertIn("semver.org", self.document)

    def test_every_released_section_carries_an_iso_date(self):
        headings = [line for line in self.document.splitlines()
                    if line.startswith("## [") and not line.startswith("## [未发布]")]
        self.assertTrue(headings, "至少要留一节已发布版本，compare 链条才接得上")
        for heading in headings:
            with self.subTest(heading=heading):
                self.assertRegex(heading, r"^## \[\d+\.\d+\.\d+\] - \d{4}-\d{2}-\d{2}$")

    def _released_versions(self):
        return re.findall(r"(?m)^## \[(\d+\.\d+\.\d+)\]", self.document)

    def test_every_released_section_has_its_compare_link(self):
        for version in self._released_versions():
            with self.subTest(version=version):
                self.assertRegex(self.document,
                                 rf"(?m)^\[{re.escape(version)}\]: https://github\.com/")

    def test_every_entry_carries_a_label_from_the_closed_vocabulary(self):
        allowed = "|".join(changelog.AREAS)
        for line in self.document.splitlines():
            if not line.startswith("- "):
                continue
            with self.subTest(line=line):
                self.assertRegex(line, rf"^- \*\*(?:{allowed})\*\*：")

    def test_released_sections_run_newest_first(self):
        keys = [tuple(int(part) for part in version.split("."))
                for version in self._released_versions()]
        self.assertEqual(keys, sorted(keys, reverse=True))


if __name__ == "__main__":
    unittest.main()
