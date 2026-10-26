"""收尾时那句「该发版了」：什么时候该出声，什么时候一个字都不能说。

提醒最容易坏在「说得太多」上：每轮回话都触发一次的钩子，只要有一次判错就变成每分钟
一条的噪音，人会连同真正该看的那一条一起划过去。所以这里的断言大半是在证明它闭嘴。
"""
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import release_due
from support.gitrepo import seed_repository

#: 版本号的唯一来源在测试仓库里的最小复刻。
VERSION_SEED = '"""Peach application package."""\n\n__version__ = "0.7.14"\n'


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, encoding="utf-8", check=True)
    return done.stdout.strip()


def commit(root: Path, subject: str) -> None:
    note = root / "note.txt"
    note.write_text(subject, encoding="utf-8")
    git(root, "add", "note.txt")
    git(root, "commit", "-m", subject)


class ReleaseDueCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # 先 resolve：CI runner 的临时目录都是别名，git 报回来的是真实路径，拿未
        # resolve 的路径去比对会本机全绿、CI 全红。
        self.root = Path(self.tmp.name).resolve()
        self.latch = self.root / "state" / "release-due.json"
        self.repo = seed_repository(self.root / "repo",
                                    {"src/peach/__init__.py": VERSION_SEED}, "chore: 起底")

    def pile_up_user_facing_work(self, count: int = 10) -> None:
        """攒够「不必等满周期」那道判据要的条目数。"""
        for index in range(count):
            commit(self.repo, f"feat(review): 复核加第 {index} 项")

    def say(self) -> str:
        return release_due.verdict(self.repo, self.latch)["say"]


class SpeakingTests(ReleaseDueCase):
    def test_a_release_worth_of_user_facing_changes_gets_said(self):
        self.pile_up_user_facing_work()
        said = self.say()
        self.assertIn("该发下一版了", said)
        self.assertIn("release_tag.py", said)

    def test_the_same_master_is_only_asked_about_once(self):
        """闩住 sha，不是闩住会话。

        每轮回话结束都会触发一次，按会话闩的话，同一个 master 在下一个会话里又会被
        问一遍；按 sha 闩，master 前进了才再问，那时确实是又攒了新东西。
        """
        self.pile_up_user_facing_work()
        first = release_due.verdict(self.repo, self.latch)
        release_due.remember(self.latch, first["sha"])
        self.assertEqual(self.say(), "")
        commit(self.repo, "feat(follow): 关注页加筛选")
        self.assertIn("该发下一版了", self.say())

    def test_a_latch_that_cannot_be_read_counts_as_never_asked(self):
        """闩坏了要退回「再问一次」，不能退回「永远闭嘴」。

        反过来的话，一次写坏的文件会让这条提醒从此消失，而且没有任何迹象。
        """
        self.pile_up_user_facing_work()
        self.latch.parent.mkdir(parents=True, exist_ok=True)
        self.latch.write_text("{ 半个 json", encoding="utf-8")
        self.assertIn("该发下一版了", self.say())


class SilenceTests(ReleaseDueCase):
    def test_work_no_user_can_see_says_nothing(self):
        for subject in ("chore: 收拾脚本", "test: 补断言", "refactor(web): 收口",
                        "docs: 记一笔"):
            commit(self.repo, subject)
        self.assertEqual(self.say(), "")

    def test_a_worker_worktree_is_never_asked_to_release(self):
        """工作树里的工作者不发版，提醒他等于提醒错人。"""
        self.pile_up_user_facing_work()
        worker = self.root / "worker"
        git(self.repo, "worktree", "add", "-b", "agent/test/x", str(worker))
        self.assertEqual(release_due.verdict(worker.resolve(), self.latch)["say"], "")
        self.assertFalse(release_due.is_main_checkout(worker.resolve()))
        self.assertTrue(release_due.is_main_checkout(self.repo))

    def test_standing_on_another_branch_says_nothing(self):
        self.pile_up_user_facing_work()
        git(self.repo, "checkout", "-q", "-b", "agent/test/y")
        self.assertEqual(self.say(), "")

    def test_a_dirty_checkout_says_nothing(self):
        """此刻发不了版，问了也动不了手；等这轮落地，下一次收尾时再问。"""
        self.pile_up_user_facing_work()
        (self.repo / "note.txt").write_text("还没提交", encoding="utf-8")
        self.assertEqual(self.say(), "")

    def test_somewhere_that_is_not_a_repository_says_nothing(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.assertEqual(release_due.verdict(outside, self.latch)["say"], "")


class HookTests(ReleaseDueCase):
    def run_hook(self, cwd: Path) -> tuple[int, str]:
        received = json.dumps({"hook_event_name": "Stop", "cwd": str(cwd)})
        with mock.patch("sys.stdin", io.StringIO(received)), \
                mock.patch("sys.stdout", io.StringIO()) as out:
            code = release_due.main(["--hook-event", "--latch", str(self.latch)])
        return code, out.getvalue()

    def test_the_answer_is_the_shape_both_agents_read(self):
        """Claude 与 Codex 的 Stop 钩子读的是同一个字段，所以一个脚本供两边。"""
        self.pile_up_user_facing_work()
        code, out = self.run_hook(self.repo)
        self.assertEqual(code, 0)
        self.assertIn("该发下一版了", json.loads(out)["systemMessage"])

    def test_nothing_to_say_prints_nothing_at_all(self):
        """不该发时连「不用发」也不说：钩子每轮都跑，多一个字就是每轮多一行。"""
        commit(self.repo, "chore: 收拾脚本")
        code, out = self.run_hook(self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_the_hook_reads_the_session_directory_from_the_event(self):
        """钩子里的仓库位置只能听事件的 `cwd`。

        Claude 的 `${CLAUDE_PROJECT_DIR}` 在进入工作树后仍指着会话启动时的那个项目
        根，照它判的话，工作树里的工作者会话会被当成主检出，收到一条他动不了手的提醒。
        """
        self.pile_up_user_facing_work()
        worker = self.root / "worker"
        git(self.repo, "worktree", "add", "-b", "agent/test/z", str(worker))
        self.assertEqual(self.run_hook(worker.resolve()), (0, ""))

    def test_only_the_hook_moves_the_latch(self):
        """人手查是只读的，不该顺手把钩子的嘴堵上。"""
        self.pile_up_user_facing_work()
        with mock.patch("sys.stdout", io.StringIO()):
            release_due.main(["--repo", str(self.repo), "--latch", str(self.latch)])
        self.assertFalse(self.latch.exists())
        self.run_hook(self.repo)
        self.assertTrue(self.latch.exists())


class WiringTests(unittest.TestCase):
    """判断写好了却没人调用，等于没写。"""

    ROOT = Path(__file__).resolve().parents[1]

    def claude_stop_argv(self) -> list[list[str]]:
        settings = json.loads((self.ROOT / ".claude" / "settings.json")
                              .read_text(encoding="utf-8"))
        return [[entry["command"], *entry.get("args", [])]
                for group in settings["hooks"]["Stop"] for entry in group["hooks"]]

    def claude_stop_commands(self) -> list[str]:
        return [" ".join(argv) for argv in self.claude_stop_argv()]

    def codex_stop_commands(self) -> list[str]:
        hooks = json.loads((self.ROOT / ".codex" / "hooks.json")
                           .read_text(encoding="utf-8"))
        return [entry["command"]
                for group in hooks["hooks"]["Stop"] for entry in group["hooks"]]

    def called_by(self, commands: list[str]) -> str:
        found = [line for line in commands
                 if "scripts/release_due.py" in line and "--hook-event" in line]
        self.assertEqual(len(found), 1, commands)
        return found[0]

    def called_by_argv(self, every: list[list[str]]) -> list[str]:
        found = [argv for argv in every
                 if any("scripts/release_due.py" in part for part in argv)]
        self.assertEqual(len(found), 1, every)
        return found[0]

    def test_the_claude_stop_hook_runs_the_check(self):
        self.called_by(self.claude_stop_commands())

    def test_the_codex_stop_hook_runs_the_same_check(self):
        self.called_by(self.codex_stop_commands())

    def test_both_entries_launch_through_uv_so_one_line_serves_both_platforms(self):
        """解释器由 `uv` 解析，命令里不出现 venv 的平台子目录。

        钩子配置不支持按操作系统分支（`if` 只对工具事件生效，也不认平台），所以写死
        `.venv/Scripts` 的那一台以外，解释器根本不存在，钩子每轮报一次错。挂两条让
        错的那条自然失败也不行：报错本身就是每轮一行的噪音，而这条提醒的全部价值在于
        不该说的时候一个字都不说。

        `--no-project` 是这里的重点，不是随手加的开关。这个脚本整条导入链都是标准库，
        不需要项目的 venv；而带上项目的话，`uv` 在一个有 `pyproject.toml` 却还没建
        venv 的检出里会顺手建一个空的，`scripts/test.ps1` 随后优先选中它，那棵树的
        测试从此连 `filelock` 都导不进来。钩子每轮都跑，这种副作用会落在每一个新工作树上。
        """
        for commands in (self.claude_stop_commands(), self.codex_stop_commands()):
            line = self.called_by(commands)
            self.assertRegex(line, r"(^|[/\\\s])uv(\.exe)?\s")
            self.assertIn("run", line)
            self.assertIn("--no-project", line)
            for platform_only in (".venv/Scripts", ".venv\\Scripts", ".venv/bin"):
                self.assertNotIn(platform_only, line)

    def test_the_reminder_comes_out_as_utf8_whatever_the_console_codepage_is(self):
        """这句话经配置里那条命令出来必须是 UTF-8 字节。

        `uv run --no-project python` 拿的是系统解释器，它在这台 Windows 上把
        `sys.stdout.encoding` 定成 `gbk`：`systemMessage` 里的中文按 GBK 编出去，
        读的一端按 UTF-8 解，屏幕上只有命令名和数字还认得出来，正文是一片乱码。
        这条提醒的全部价值就在那一句话本身，读不出来等于没说。`-X utf8` 把输出端
        钉死，和测试入口固定 `PYTHONIOENCODING` 是同一条。

        解释器那一段照配置原样取出来跑一遍：只比对 `-X utf8` 写在不写在，认不出
        「写了却没生效」，而这里要的正是出来的字节。
        """
        claude = self.called_by_argv(self.claude_stop_argv())
        codex = self.called_by(self.codex_stop_commands()).split()
        for argv in (claude, codex):
            script = next(i for i, part in enumerate(argv) if "release_due.py" in part)
            self.assertIn("-X", argv[:script], argv)
            self.assertEqual(argv[argv.index("-X") + 1], "utf8", argv)
        interpreter = claude[:next(i for i, part in enumerate(claude)
                                   if "release_due.py" in part)]
        spoken = subprocess.run([*interpreter, "-c", "print('该发下一版了')"],
                                cwd=str(self.ROOT), capture_output=True, check=False)
        self.assertEqual(spoken.returncode, 0, spoken.stderr)
        self.assertEqual(spoken.stdout.strip(), "该发下一版了".encode("utf-8"))

    def test_the_configured_command_runs_without_leaving_a_venv_behind(self):
        """把配置里那条命令原样跑一遍，落在一个装成项目的空目录里。

        两件事一起验。一是它跑得起来：这条提醒设计成不该说时一个字都不打印，所以
        「命令根本跑不起来」和「没什么要说的」在屏幕上长得一模一样，字符串比对认不出
        这种失败——装没装 `uv`、PATH 上是不是别的东西，只有真执行一次才知道；裸
        `python` 曾解析到 MSIX 别名，路径在、一执行就报错，正是这一类。二是它什么都
        不留下：工作目录摆着 `pyproject.toml`，跑完那里不能多出一个 `.venv`。
        """
        argv = [part.replace("${CLAUDE_PROJECT_DIR}", str(self.ROOT))
                for part in self.called_by_argv(self.claude_stop_argv())]
        with tempfile.TemporaryDirectory() as elsewhere:
            looks_like_a_project = Path(elsewhere).resolve()
            (looks_like_a_project / "pyproject.toml").write_text(
                '[project]\nname = "bait"\nversion = "0"\n', encoding="utf-8")
            done = subprocess.run(
                argv, cwd=str(looks_like_a_project),
                input=json.dumps({"hook_event_name": "Stop",
                                  "cwd": str(looks_like_a_project)}),
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", check=False)
            left_behind = sorted(path.name for path in looks_like_a_project.iterdir())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "")
        self.assertEqual(left_behind, ["pyproject.toml"])


if __name__ == "__main__":
    unittest.main()
