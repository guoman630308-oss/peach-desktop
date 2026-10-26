import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import version_bump
from scripts.version_bump import (
    VersionError, bump_part_for, bump_version, plan_bump, read_version, runtime_inputs_changed,
    write_version,
)

#: 版本号的唯一来源，`src/peach/__init__.py` 在测试仓库里的最小复刻。
VERSION_SEED = '"""Peach application package."""\n\n__version__ = "0.7.14"\n'


class BumpPartTests(unittest.TestCase):
    def test_the_bump_part_follows_commit_types_and_new_migrations(self):
        """feat 与破坏性标记推 minor，新迁移文件推 minor，其余都是 patch；major 不自动判。"""
        self.assertEqual(bump_part_for(["fix: 抽屉重画只写滚动层"], []), "patch")
        self.assertEqual(bump_part_for(["docs: 记录运行状态", "chore(release): 版本 0.7.27"], []),
                         "patch")
        self.assertEqual(bump_part_for(["refactor(follow): 来源登记表变成唯一真相"], []), "patch")
        self.assertEqual(bump_part_for(["feat: 配置页并入主站管理菜单"], []), "minor")
        self.assertEqual(bump_part_for(["feat(web): 滚动条改成自绘覆盖式"], []), "minor")
        self.assertEqual(bump_part_for(["fix!: 数据库路径改成 location 键"], []), "minor")
        self.assertEqual(bump_part_for(["refactor(config)!: 删掉盘符键"], []), "minor")
        self.assertEqual(bump_part_for(["fix: 补迁移"], ["migrations/0027_thing.sql"]), "minor")
        self.assertEqual(bump_part_for(["fix: 补迁移"], [r"migrations\0027_thing.sql"]), "minor")
        self.assertEqual(bump_part_for(["fix: 改了迁移的注释"], ["src/peach/tray.py"]), "patch")
        self.assertEqual(
            bump_part_for(["Merge branch 'master' into agent/claude/x", "feature: 新东西"], []),
            "minor")
        self.assertEqual(bump_part_for([], []), "patch")

    def test_runtime_inputs_are_told_apart_from_development_only_paths(self):
        self.assertTrue(runtime_inputs_changed(["src/peach/tray.py"]))
        self.assertTrue(runtime_inputs_changed(["web/app.js"]))
        self.assertTrue(runtime_inputs_changed(["frontend/src/main.ts"]))
        self.assertTrue(runtime_inputs_changed(["migrations/0042_thing.sql"]))
        self.assertTrue(runtime_inputs_changed(["resources/peach.ico"]))
        self.assertTrue(runtime_inputs_changed(["scripts/build_windows.ps1"]))
        self.assertTrue(runtime_inputs_changed(["scripts/build_app_entry.py"]))
        self.assertTrue(runtime_inputs_changed(["pyproject.toml"]))
        self.assertTrue(runtime_inputs_changed(["uv.lock"]))
        self.assertTrue(runtime_inputs_changed([".github/workflows/release.yml"]))
        self.assertTrue(runtime_inputs_changed([r"src\peach\tray.py"]))

        self.assertFalse(runtime_inputs_changed([]))
        self.assertFalse(runtime_inputs_changed(["docs/STATUS.md", "AGENTS.md"]))
        self.assertFalse(runtime_inputs_changed(["tests/test_tray.py"]))
        self.assertFalse(runtime_inputs_changed([".claude/skills/peach-worktree/SKILL.md"]))
        self.assertFalse(runtime_inputs_changed(["scripts/agent_worktree.py"]))

    def test_only_the_version_line_moves(self):
        updated, version = bump_version(VERSION_SEED, "patch")
        self.assertEqual(version, "0.7.15")
        self.assertEqual(updated,
                         '"""Peach application package."""\n\n__version__ = "0.7.15"\n')

        self.assertEqual(bump_version('__version__ = "0.7.14"', "minor")[1], "0.8.0")
        self.assertEqual(bump_version('__version__ = "0.7.14"', "major")[1], "1.0.0")
        with self.assertRaisesRegex(VersionError, "unknown bump part"):
            bump_version('__version__ = "0.7.14"', "epoch")
        with self.assertRaisesRegex(VersionError, "__version__"):
            bump_version("nothing here\n", "patch")


class ReleasePlanTests(unittest.TestCase):
    """区间与档位落在真实 git 仓库上，不是拿字符串假装。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # 先 resolve：CI runner 的临时目录都是别名，未 resolve 的路径只在本机成立。
        self.root = Path(self.tmp.name).resolve()
        subprocess.run(["git", "init", "-b", "master", str(self.root)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Peach Test")
        self.commit("chore: 起点", self.write("src/peach/__init__.py", VERSION_SEED))

    def git(self, *args: str) -> str:
        done = subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True, text=True, encoding="utf-8")
        return done.stdout.strip()

    def write(self, path: str, text: str = "改了\n") -> str:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        return path

    def commit(self, subject: str, *paths: str) -> None:
        for path in paths:
            self.git("add", path)
        self.git("commit", "-m", subject)

    def tag(self, name: str = "v0.7.14") -> None:
        self.git("tag", "-a", name, "-m", f"Peach {name}")

    def test_the_range_starts_at_the_last_version_tag(self):
        self.tag()
        self.commit("fix: 托盘图标补回来", self.write("src/peach/tray.py"))
        self.assertEqual(version_bump.last_tag(self.root), "v0.7.14")
        self.assertEqual(version_bump.release_range(self.root), "v0.7.14..HEAD")
        self.assertEqual(version_bump.subjects_in(self.root, "v0.7.14..HEAD"),
                         ["fix: 托盘图标补回来"])
        self.assertEqual(version_bump.changed_in(self.root, "v0.7.14..HEAD"),
                         ["src/peach/tray.py"])

    def test_without_any_tag_the_range_is_the_whole_history(self):
        self.assertIsNone(version_bump.last_tag(self.root))
        self.assertEqual(version_bump.release_range(self.root), "HEAD")
        self.assertEqual(version_bump.changed_in(self.root, "HEAD"), ["src/peach/__init__.py"])
        self.assertEqual(plan_bump(self.root)["version"], "0.7.15")

    def test_a_feature_since_the_last_tag_plans_the_next_minor(self):
        self.tag()
        self.commit("feat: 配置页加桌面快捷方式", self.write("src/peach/routes_configuration.py"))
        planned = plan_bump(self.root)
        self.assertEqual((planned["range"], planned["bump"], planned["current"],
                          planned["version"], planned["commits"]),
                         ("v0.7.14..HEAD", "minor", "0.7.14", "0.8.0", 1))

    def test_a_fix_since_the_last_tag_plans_the_next_patch(self):
        self.tag()
        self.commit("fix: 忙态提示说明在做什么", self.write("web/app.js"))
        self.assertEqual(plan_bump(self.root)["version"], "0.7.15")

    def test_an_explicit_part_overrides_the_commit_types(self):
        self.tag()
        self.commit("fix: 一处小修", self.write("src/peach/tray.py"))
        self.assertEqual(plan_bump(self.root, "major")["version"], "1.0.0")
        self.assertEqual(plan_bump(self.root, "minor")["version"], "0.8.0")

    def test_release_workflow_changes_plan_a_patch(self):
        self.tag()
        self.commit("fix(desktop): 发布构建使用完整依赖", self.write(".github/workflows/release.yml"))
        self.assertEqual(plan_bump(self.root)["version"], "0.7.15")

    def test_locked_dependency_changes_plan_a_patch(self):
        self.tag()
        self.commit("fix(desktop): 固定构建依赖", self.write("uv.lock"))
        self.assertEqual(plan_bump(self.root)["version"], "0.7.15")

    def test_planning_writes_nothing(self):
        self.tag()
        self.commit("feat: 新面板", self.write("src/peach/panel.py"))
        plan_bump(self.root)
        self.assertEqual(read_version(self.root), "0.7.14")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_a_batch_that_never_reaches_the_runtime_cannot_be_released(self):
        """只改文档、技能与测试时，打出来的包和上一个标签逐字节相同。"""
        self.tag()
        self.commit("docs: 记一笔运行状态", self.write("docs/STATUS.md"))
        self.commit("test: 补一条断言", self.write("tests/test_tray.py"))
        with self.assertRaisesRegex(VersionError, "开发过程"):
            plan_bump(self.root)

    def test_a_tag_with_nothing_after_it_cannot_be_released(self):
        self.tag()
        with self.assertRaisesRegex(VersionError, "没有提交"):
            plan_bump(self.root)

    def test_writing_the_version_leaves_the_rest_of_the_file_alone(self):
        self.assertEqual(write_version(self.root, "minor"), "0.8.0")
        self.assertEqual(read_version(self.root), "0.8.0")
        self.assertEqual((self.root / "src/peach/__init__.py").read_text(encoding="utf-8"),
                         VERSION_SEED.replace("0.7.14", "0.8.0"))


if __name__ == "__main__":
    unittest.main()
