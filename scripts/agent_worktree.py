from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "scripts"
from . import (changelog, check_readme_impact, co_author, commit_subject,
               test_evidence, test_runner, version_bump)
from .release_tag import MASTER_WRITER

#: `create` 的落点，建在主检出旁边（ADR-0017 的四个运行时目录之一）。
WORKTREE_ROOT = "peach-worktrees"

#: Claude Code 内置工作树的落点。分支集成后它自己不收：目录留在主检出里，成了一份看不出
#: 区别的旧副本（见 CLAUDE.md）。`tests/test_repo_hygiene.py` 拦得住，但拦住之后没有任何
#: 入口能清掉已经在那儿的，只能让人手删——`prune --apply` 顺带扫掉这块。
BUILTIN_WORKTREES = Path(".claude") / "worktrees"


class WorkspaceError(RuntimeError):
    pass


#: 版本号推进与打标签的唯一入口。集成不碰 `__version__`：主线上每个提交由 commit 标识，
#: 版本号留给真正发出去的那些（ADR-0012）。集成结果里的 `version` 只是当前值，供人核对。
RELEASE_TAG_ENTRY = "scripts/release_tag.py"

#: 仓库自带的 git hook。`create` 与 `integrate` 每次都把 `core.hooksPath` 指到这里：
#: 哪台机器先走一遍工作流就在哪台装上，不靠人记着去配。
HOOKS_PATH = "scripts/githooks"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = [
        "git", "-c", f"safe.directory={repo.as_posix()}", "-C", str(repo), *args,
    ]
    result = subprocess.run(command, text=True, encoding="utf-8", errors="replace",
                            capture_output=True, check=False)
    if check and result.returncode:
        raise WorkspaceError((result.stderr or result.stdout).strip())
    return result


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug:
        raise WorkspaceError("agent/task must contain letters or numbers")
    return slug


def _main_worktree(repo: Path) -> Path:
    common = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
    return common.parent if common.name == ".git" else common


def _lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in result.stdout.splitlines() if line]


def _install_hooks(main: Path) -> None:
    _git(main, "config", "core.hooksPath", HOOKS_PATH)


def create(repo: Path, agent: str, task: str, root: Path | None = None) -> dict[str, object]:
    main = _main_worktree(repo)
    agent_slug, task_slug = _slug(agent), _slug(task)
    branch = f"agent/{agent_slug}/{task_slug}"
    target_root = root or (main.parent / WORKTREE_ROOT)
    target = target_root / f"{agent_slug}-{task_slug}"
    if target.exists():
        raise WorkspaceError(f"target exists: {target}")
    if _git(main, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0:
        raise WorkspaceError(f"branch exists: {branch}")
    target_root.mkdir(parents=True, exist_ok=True)
    _git(main, "worktree", "add", "--lock", "--reason", "Peach active agent task",
         "-b", branch, str(target), "HEAD")
    _install_hooks(main)
    # 开工前就报出父进程已经不在的残留进程：别的会话还在跑的进程有父进程，不在这里出现。
    found = processes_under(_worktree_roots(main))
    return {
        "ok": True,
        "action": "create",
        "path": str(target),
        "branch": branch,
        "base": _git(target, "rev-parse", "HEAD").stdout.strip(),
        "main_dirty": bool(_git(main, "status", "--porcelain").stdout.strip()),
        "orphan_processes": found if isinstance(found, str) else
                            [row for row in found if row["orphan"]],
    }


def ready(repo: Path, target_branch: str = "master") -> dict[str, object]:
    branch = _git(repo, "branch", "--show-current").stdout.strip()
    if not branch.startswith("agent/"):
        raise WorkspaceError("ready must run from an agent/* branch")
    dirty = _lines(_git(repo, "status", "--porcelain"))
    if dirty:
        raise WorkspaceError("worktree is dirty; commit only owned paths first")
    base = _git(repo, "merge-base", target_branch, "HEAD").stdout.strip()
    commits = _lines(_git(repo, "rev-list", "--reverse", f"{base}..HEAD"))
    if not commits:
        raise WorkspaceError("branch has no commits")
    worker_files = set(_lines(_git(repo, "diff", "--name-only", f"{base}..HEAD")))
    target_files = set(_lines(_git(repo, "diff", "--name-only", f"{base}..{target_branch}")))
    require_verified(repo, target_branch, worker_files)
    return {
        "ok": True,
        "action": "ready",
        "branch": branch,
        "base": base,
        "head": _git(repo, "rev-parse", "HEAD").stdout.strip(),
        "commits": commits,
        "files": sorted(worker_files),
        "same_file_changes": sorted(worker_files & target_files),
    }


def require_verified(worker: Path, target_branch: str, paths: Iterable[str]) -> None:
    head = _git(worker, "rev-parse", "HEAD").stdout.strip()
    if _git(worker, "status", "--porcelain").stdout.strip():
        raise WorkspaceError("验证工作树必须干净")
    if _git(worker, "merge-base", "--is-ancestor", target_branch, "HEAD", check=False).returncode:
        raise WorkspaceError("目标分支已前进；请在工作树 rebase 后运行 auto 验证")
    problems = (check_readme_impact.check(worker, target_branch)
                + co_author.check(worker, target_branch)
                + commit_subject.check(worker, target_branch))
    if problems:
        raise WorkspaceError("; ".join(problems))
    scopes, _ = test_runner.scopes_for_changes(paths, contents=test_runner.changed_contents(worker, target_branch, paths))
    state = test_evidence.key(worker)
    if not test_evidence.covers(test_evidence.read(worker, state), scopes):
        raise WorkspaceError("缺少有效测试记录；请在工作树运行统一测试入口 auto")
    if _git(worker, "rev-parse", "HEAD").stdout.strip() != head or \
            _git(worker, "status", "--porcelain").stdout.strip():
        raise WorkspaceError("验证检查期间工作树改变，请重试")


def integrate(repo: Path, worker_branch: str, target_branch: str = "master", *,
              lock_timeout: float = 1800) -> dict[str, object]:
    folder = test_evidence.evidence_dir(repo)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with test_evidence.held(folder / "integration.lock",
                                branch=worker_branch, root=str(repo)), \
                test_evidence.held(folder / "full-suite.lock", wait_seconds=lock_timeout,
                                   scope="integrate", branch=worker_branch, root=str(repo)):
            return _integrate_locked(repo, worker_branch, target_branch)
    except test_evidence.Timeout as error:
        raise WorkspaceError(
            f"另一任务正在集成或全量验证（{test_evidence.describe_holder(Path(error.lock_file))}）；"
            "本次未修改分支，请等待后重试") from error


def _integrate_locked(repo: Path, worker_branch: str,
                      target_branch: str = "master") -> dict[str, object]:
    """把工作者分支并进 target。

    这台机器既是开发机又是生产机：提交先落本地再推 GitHub，本地永远领先。跑着的是哪一份
    由 commit 认定——`build-info.json` 随包走，托盘按它算自己落后多少个提交——所以集成
    不碰版本号，它在 `RELEASE_TAG_ENTRY` 那里一次发布推一格。
    """
    main = _main_worktree(repo)
    if repo.resolve() != main.resolve():
        raise WorkspaceError("integrate must run from the main integration worktree")
    if _git(main, "branch", "--show-current").stdout.strip() != target_branch:
        raise WorkspaceError(f"checkout {target_branch} before integrate")
    if _git(main, "status", "--porcelain").stdout.strip():
        raise WorkspaceError("integration worktree is dirty")
    before = _git(main, "rev-parse", "HEAD").stdout.strip()
    base = _git(main, "merge-base", target_branch, worker_branch).stdout.strip()
    worker_files = set(_lines(_git(main, "diff", "--name-only", f"{base}..{worker_branch}")))
    target_files = set(_lines(_git(main, "diff", "--name-only", f"{base}..{target_branch}")))
    overlap = sorted(worker_files & target_files)
    if overlap:
        raise WorkspaceError("same-file review required: " + ", ".join(overlap))
    workers = [Path(item["path"]) for item in _worktree_entries(main)
               if item.get("branch") == worker_branch]
    if len(workers) != 1 or _git(workers[0], "status", "--porcelain").stdout.strip():
        raise WorkspaceError("工作者必须有唯一、干净且已注册的工作树")
    worker_head = _git(main, "rev-parse", worker_branch).stdout.strip()
    require_verified(workers[0], target_branch, worker_files)
    if _git(main, "rev-parse", worker_branch).stdout.strip() != worker_head or \
            _git(workers[0], "rev-parse", "HEAD").stdout.strip() != worker_head:
        raise WorkspaceError("验证检查期间工作者分支改变，请重试")
    if _git(main, "rev-parse", "HEAD").stdout.strip() != before or \
            _git(main, "status", "--porcelain").stdout.strip():
        raise WorkspaceError("验证检查期间集成工作树改变，请重试")
    _install_hooks(main)
    _git(main, "-c", f"{MASTER_WRITER}=integrate", "merge", "--no-ff", "--no-edit",
         "-m", f"Merge branch '{worker_branch}'", worker_head)
    _git(main, "worktree", "unlock", str(workers[0]), check=False)
    return {
        "ok": True,
        "action": "integrate",
        "branch": worker_branch,
        "before": before,
        "after": _git(main, "rev-parse", "HEAD").stdout.strip(),
        "files": sorted(worker_files),
        "version": version_bump.read_version(main),
        "release_tag_entry": RELEASE_TAG_ENTRY,
        # 发布提示挂在这里，是因为集成的输出是人和智能体每次都会读的那一份。挂在别处
        # 就得有谁记着去查，而「记着」正是这条判据要替掉的东西。
        "release": changelog.due(main),
    }


def _worktree_entries(main: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    entry: dict[str, str] = {}
    for line in _lines(_git(main, "worktree", "list", "--porcelain")):
        if line.startswith("worktree "):
            entry = {"path": line[len("worktree "):]}
            entries.append(entry)
        elif line.startswith("branch "):
            entry["branch"] = line[len("branch "):].replace("refs/heads/", "")
        elif line.startswith("locked"):
            entry["locked"] = line
    return entries


def _force_writable(func, path, _exc) -> None:
    Path(path).chmod(stat.S_IWRITE)
    func(path)


def _delete_tree(path: Path) -> str:
    """删掉回收之后剩下的目录。删掉了返回空串，否则返回失败原因。

    Windows 上删不掉多半不是权限而是句柄：另一个进程正把它当工作目录开着。短暂重试
    能等到杀毒、索引器一类的临时句柄放手，等不到一个 cd 在里面的 shell——所以只重试
    很短一会儿就如实报出来，不做无限等待，也不去猜是谁占着。
    """
    why = ""
    for attempt in range(4):
        if not path.exists():
            return ""
        try:
            shutil.rmtree(path, onexc=_force_writable)
            return ""
        except OSError as error:
            why = f"{type(error).__name__}: {error}"
            time.sleep(0.2 * (attempt + 1))
    return why


def _worktree_roots(main: Path) -> tuple[Path, Path]:
    return main.parent / WORKTREE_ROOT, main / BUILTIN_WORKTREES


def _under(value: str, roots: list[str]) -> bool:
    if not value:
        return False
    here = os.path.normcase(os.path.normpath(value))
    return any(here == root or here.startswith(root + os.sep) for root in roots)


def processes_under(roots: Iterable[Path], *, sample: float = 0.0) -> list[dict] | str:
    """列出当前目录或命令行落在 roots 下的进程。

    工作树里起的调试服务、浏览器守护进程和挂在 stdin 上的 shell，在会话结束后照样活着：
    2026-09-07、09-21、09-22 三次都是它们占住目录，`prune --apply` 删不掉，整洁门槛报红，
    而当时只能手工读进程的 PEB 才找得到是谁。生产托盘与 serve 从主检出起，不在这两个落点下。

    先按当前目录和命令行筛（全机扫一遍约 0.1 秒），只对命中的进程取启动时间、父进程和
    内存：Windows 上对全部进程取这几项要十几秒，慢在拒绝访问的系统进程上。
    `caller` 标出调用者自己这条进程链，`orphan` 是父进程已经不在的进程——会话关掉后
    留下的多半是这种。`sample` 大于 0 时再采一次 CPU。缺 psutil 返回「未取得」说明。
    """
    try:
        import psutil
    except ImportError:
        return "未取得：当前 venv 缺 psutil，先运行 uv sync --locked --all-extras"
    prefixes = [os.path.normcase(os.path.normpath(str(root))) for root in roots]
    caller: set[int] = set()
    try:
        link = psutil.Process()
        while link is not None and link.pid not in caller:
            caller.add(link.pid)
            link = link.parent()
    except psutil.Error:
        pass
    hits = []
    for process in psutil.process_iter(["cwd", "cmdline"]):
        cwd = process.info.get("cwd") or ""
        cmdline = process.info.get("cmdline") or []
        if _under(cwd, prefixes) or any(_under(arg, prefixes) for arg in cmdline):
            hits.append((process, cwd, cmdline))
    if sample > 0:
        for process, _cwd, _cmdline in hits:
            try:
                process.cpu_percent(None)
            except psutil.Error:
                pass
        time.sleep(sample)
    rows = []
    now = time.time()
    for process, cwd, cmdline in hits:
        try:
            with process.oneshot():
                started = process.create_time()
                row = {
                    "pid": process.pid,
                    "name": process.name(),
                    "cmdline": " ".join(cmdline)[:300],
                    "cwd": cwd,
                    "age_minutes": round((now - started) / 60, 1),
                    "rss_mb": round(process.memory_info().rss / 2**20, 1),
                    "ppid": process.ppid(),
                    "orphan": process.parent() is None,
                    "caller": process.pid in caller,
                }
                if sample > 0:
                    row["cpu_percent"] = process.cpu_percent(None)
        except psutil.Error:
            continue
        rows.append(row)
    return sorted(rows, key=lambda row: row["rss_mb"], reverse=True)


def processes(repo: Path) -> dict[str, object]:
    """机器变卡或回收前，看两个工作树落点下还活着哪些进程。只报告，不结束任何进程。"""
    main = _main_worktree(repo)
    roots = _worktree_roots(main)
    report: dict[str, object] = {"ok": True, "action": "processes",
                                 "roots": [str(root) for root in roots]}
    try:
        import psutil
        memory = psutil.virtual_memory()
        report["machine"] = {"cpu_percent": psutil.cpu_percent(0.5),
                             "memory_percent": memory.percent,
                             "memory_available_gb": round(memory.available / 2**30, 1)}
    except ImportError:
        pass
    report["processes"] = processes_under(roots, sample=0.5)
    return report


def _reclaim(main: Path, path: Path, branch: str) -> tuple[str, dict[str, str]]:
    """回收单个工作树，把结果归到 reclaimed / residue / failed 之一。

    2026-09-01 实测：Windows 上 `.claude/worktrees/*` 的目录句柄被别的进程占着，
    `git worktree remove` 报 Permission denied，而 git 已经把文件删光、注册也摘掉了，
    只剩一个空目录。所以失败只波及它自己：注册已经摘掉的照常删分支，目录自己再删一遍；
    注册还在才算真没回收成。这一步抛错中止整轮的话，分支一条没删、后面的工作树一个没碰，
    要靠人反复重跑 prune，跑一次才推进一个。

    「注册摘了、目录还在」不该留给人：那个目录在主检出里长得和真工作树一模一样，`git`
    在里面全作用于主检出的 master。git 删不动就自己删，删完才算回收；真删不掉才进
    residue，那时候确实需要人去看是谁占着。
    """
    removal = _git(main, "worktree", "remove", str(path), check=False)
    why = (removal.stderr or removal.stdout).strip()
    registered = {Path(item["path"]).resolve() for item in _worktree_entries(main)}
    if removal.returncode and path.resolve() in registered:
        return "failed", {"path": str(path), "branch": branch, "why": why}
    if branch:
        _git(main, "branch", "-d", branch, check=False)
    if removal.returncode:
        left = _delete_tree(path)
        if left:
            return "residue", {"path": str(path), "branch": branch,
                               "why": f"注册已摘掉，目录删不掉，确认没人占用后手动删：{left}（{why}）",
                               "holders": processes_under([path])}
    return "reclaimed", {"path": str(path), "branch": branch}


def _sweep_unregistered(main: Path, here: Path, *, apply: bool
                        ) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    """扫掉两个工作树落点里已经不登记的空目录。

    两个落点都要扫：`create` 建工作树的 `WORKTREE_ROOT`，和 Claude Code 内置的
    `BUILTIN_WORKTREES`。这些目录不在 `git worktree list` 里，上面那轮按登记项遍历的
    回收永远碰不到它们，人却会走进去当工作树用——而在里面跑 git 全作用于主检出的
    master。漏掉 `create` 那一侧最贵：那里是智能体被告知要去干活的地方。
    2026-09-13 清点：`peach-worktrees/` 下四个未登记目录共 246 MB，两份是带 `.venv`
    的整树副本，其中一份的句柄还被一个跑在 8931 端口的预览服务占着。  # copy-lint-disable-line

    只删空的：里面还有文件就可能是别人正在用的检出，或者是没提交的东西，那种一律
    留着报出来，不替人做判断。
    """
    swept: list[str] = []
    residue: list[dict[str, str]] = []
    kept: list[dict[str, str]] = []
    registered = {Path(item["path"]).resolve() for item in _worktree_entries(main)}
    for root in _worktree_roots(main):
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if not path.is_dir() or path.resolve() in registered or path.resolve() == here:
                continue
            if any(child.is_file() for child in path.rglob("*")):
                kept.append({"path": str(path), "branch": "",
                             "why": "未登记的目录里还有文件，确认没人在用后手动删"})
                continue
            if not apply:
                swept.append(str(path))
                continue
            left = _delete_tree(path)
            if left:
                residue.append({"path": str(path), "branch": "", "why": "目录删不掉：" + left,
                                "holders": processes_under([path])})
            else:
                swept.append(str(path))
    return swept, residue, kept


def prune(repo: Path, target_branch: str = "master", *,
          apply: bool = False) -> dict[str, object]:
    """回收分支已并入 target、且工作区干净的隔离工作树。

    这个命令存在的理由：`create` 一直有人用，回收却从来没有入口。2026-08-29 清过一次
    到 3 个，两天后又长回 74 个、占 868 MB——因为那次是一次性动作，不是常设机制。

    默认只报告不动手。回收不可逆，而「分支已合入」并不等于「工作区里没有东西」：
    实测就有工作树的分支早已并入 master，里面却躺着一份成形的未提交改动。所以脏的
    一律拒收并单独列出，交给人看，不给 `--force` 这个口子。

    单个工作树回收失败不影响其它工作树：结果分成 reclaimed / swept / residue / failed /
    dirty / kept 六组，整轮照样 ok。理由见 `_reclaim`。`swept` 是两个工作树落点里
    已经不登记的空目录，见 `_sweep_unregistered`。
    """
    main = _main_worktree(repo)
    merged = {line.strip().lstrip("+* ") for line in
              _lines(_git(main, "branch", "--merged", target_branch,
                          "--format=%(refname:short)"))}
    merged.discard(target_branch)
    here = repo.resolve()

    reclaimed: list[str] = []
    residue: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    dirty: list[dict[str, str]] = []
    kept: list[dict[str, str]] = []

    for item in _worktree_entries(main):
        path = Path(item["path"])
        branch = item.get("branch", "")
        if path.resolve() == main.resolve() or path.resolve() == here:
            continue
        if item.get("locked"):
            kept.append({"path": str(path), "branch": branch, "why": "工作树被活动任务锁定"})
            continue
        if branch not in merged:
            kept.append({"path": str(path), "branch": branch, "why": "分支未并入 " + target_branch})
            continue
        if _git(main, "-C", str(path), "status", "--porcelain",
                check=False).stdout.strip():
            dirty.append({"path": str(path), "branch": branch, "why": "工作区有未提交改动"})
            continue
        if not apply:
            reclaimed.append(str(path))
            continue
        group, record = _reclaim(main, path, branch)
        if group == "reclaimed":
            reclaimed.append(str(path))
        elif group == "residue":
            residue.append(record)
        else:
            failed.append(record)

    swept, swept_residue, swept_kept = _sweep_unregistered(main, here, apply=apply)

    return {
        "ok": True,
        "action": "prune",
        "applied": apply,
        "reclaimed": sorted(reclaimed),
        "swept": sorted(swept),
        "residue": residue + swept_residue,
        "failed": failed,
        "dirty": dirty,
        "kept": kept + swept_kept,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Isolated Peach agent worktree coordinator")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("create")
    start.add_argument("--agent", required=True)
    start.add_argument("--task", required=True)
    start.add_argument("--root", type=Path)
    inspect = sub.add_parser("ready")
    inspect.add_argument("--target", default="master")
    merge = sub.add_parser("integrate")
    merge.add_argument("--branch", required=True)
    merge.add_argument("--target", default="master")
    sweep = sub.add_parser("prune")
    sweep.add_argument("--target", default="master")
    sweep.add_argument("--apply", action="store_true",
                       help="真的回收；不给这个参数就只报告")
    sub.add_parser("processes", help="列出两个工作树落点下还活着的进程，只报告不结束")
    args = parser.parse_args()
    try:
        if args.command == "create":
            result = create(args.repo, args.agent, args.task, args.root)
        elif args.command == "ready":
            result = ready(args.repo, args.target)
        elif args.command == "prune":
            result = prune(args.repo, args.target, apply=args.apply)
        elif args.command == "processes":
            result = processes(args.repo)
        else:
            result = integrate(args.repo, args.branch, args.target)
    except WorkspaceError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
