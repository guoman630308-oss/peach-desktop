"""版本号推进与版本标签：发布的唯一入口。

一次发布走两条命令，中间夹一次人的确认：

    python scripts/release_tag.py --bump auto --apply   # 推版本号、起草那一节
    #  → 人看一眼 `CHANGELOG.md` 新那一节的措辞，这是使用者唯一读到的说明
    python scripts/release_tag.py --ship --apply        # 提交、推送、等 CI、打标签

确认点只有一个，剩下的判据都由脚本挡：`--ship` 拒绝夹带别的改动，拒绝在 Test 转绿前
打标签，中途停下再跑一次就接着走。人要决定的只是措辞对不对，不是流程走到哪一步了。

版本号只在这里动：每个 `X.Y.Z` 都对应一份可下载的制品，日常集成的提交由 commit
标识（ADR-0012）。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "scripts"
from . import changelog, version_bump

ROOT = Path(__file__).resolve().parents[1]

#: 主检出的 master 上该落提交的只有本脚本与 `agent_worktree.py integrate`。它们调用 git 时
#: 带上这个配置，`scripts/githooks/pre-commit` 据此放行，别的提交与手工 merge 一律拒收。
#: 它住在这里而不是 `agent_worktree`：`--verify-sha` 跟着 Release 工作流跑，那一步只有裸
#: Python，依赖要到后面的构建步骤才装；反过来引用就把 `test_evidence` 那条链（filelock）拘进来，
#: 标签推上去也发不出东西。
MASTER_WRITER = "peach.masterWriter"

#: 定版只该改这两份：版本号一行，变更日志一节。`--ship` 拒绝夹带别的文件。
RELEASE_FILES = (version_bump.VERSION_FILE, changelog.CHANGELOG)

#: `--ship` 等 Test 工作流的默认秒数上限，以及两次查询之间的间隔。
TEST_TIMEOUT = 1800.0
TEST_POLL = 30.0


def command(*args: str, strip: bool = True) -> str:
    """`strip=False` 给首列本身就是空格的输出用：`git status --porcelain` 的第一行是
    ` M CHANGELOG.md`，整体 strip 会连状态列的空格一起吃掉，按列取路径就少一个字。"""
    output = subprocess.run(args, cwd=ROOT, check=True, capture_output=True,
                            text=True, encoding="utf-8").stdout
    return output.strip() if strip else output


def api(repo: str, endpoint: str):
    return json.loads(command("gh", "api", f"repos/{repo}/{endpoint}"))


def latest_run(runs: list[dict], sha: str) -> dict | None:
    """这个提交在 master 上最新的一次 Test；没有记录时是 None。"""
    matching = [run for run in runs if run.get("head_sha") == sha
                and run.get("head_branch") == "master" and run.get("event") == "push"]
    if not matching:
        return None
    return max(matching, key=lambda run: (run["id"], run.get("run_attempt", 1)))


def require_success(runs: list[dict], sha: str) -> dict:
    latest = latest_run(runs, sha)
    if latest is None:
        raise ValueError("此提交没有 master 的 Test 工作流记录")
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise ValueError(f"此提交最新 Test 尚未通过：{latest.get('html_url', '')}")
    return latest


def test_runs(repo: str, sha: str) -> list[dict]:
    endpoint = f"actions/workflows/test.yml/runs?head_sha={sha}&event=push&per_page=100"
    return api(repo, endpoint)["workflow_runs"]


def await_test(repo: str, sha: str, *, timeout: float = TEST_TIMEOUT, poll: float = TEST_POLL,
               sleep=time.sleep, clock=time.monotonic) -> dict:
    """等这个提交的 Test 出结果。

    跑完了但不是绿的立刻抛，不耗到超时——红的等多久都不会变绿。超时也抛，那时版本
    提交已经在 master 上，等 CI 自己跑完再跑一次 `--ship --apply` 就接着走。
    """
    deadline = clock() + timeout
    while True:
        runs = test_runs(repo, sha)
        latest = latest_run(runs, sha)
        if latest is not None and latest.get("status") == "completed":
            return require_success(runs, sha)
        if clock() >= deadline:
            raise ValueError(
                f"等了 {timeout / 60:.0f} 分钟，{sha[:8]} 的 Test 还没有结果；"
                "版本提交已在 master 上，等它跑完再跑一次 --ship --apply")
        sleep(poll)


def verify(repo: str, sha: str) -> dict:
    comparison = api(repo, f"compare/master...{sha}")
    if comparison["status"] not in {"identical", "behind"}:
        raise ValueError("发布提交不属于远端 master 历史")
    checked = require_success(test_runs(repo, sha), sha)
    return {"sha": sha, "test": checked["html_url"]}


def _require_clean_master() -> None:
    if command("git", "status", "--porcelain"):
        raise ValueError("工作区不干净")
    if command("git", "branch", "--show-current") != "master":
        raise ValueError("请从 master 主检出执行")


def _require_free_tag(repo: str, tag: str) -> None:
    # matching-refs 返回空列表代表不存在；网络或权限错误不能被当成空列表。
    if any(ref["ref"] == f"refs/tags/{tag}" for ref in api(repo, f"git/matching-refs/tags/{tag}")):
        raise ValueError(f"{tag} 已存在，不覆盖；发布新版本请先推进 __version__")


def prepare(repo: str, part: str, *, apply: bool) -> dict:
    """为下一次发布推进版本号并起草变更日志。

    只写文件，不提交：变更日志的措辞要人过一遍，脚本自己提交等于鼓励不看。
    """
    _require_clean_master()
    planned = version_bump.plan_bump(ROOT, part)
    version = str(planned["version"])
    tag = f"v{version}"
    _require_free_tag(repo, tag)
    result = {**planned, "tag": tag, "repo": repo, "applied": apply}
    if not apply:
        return result
    from .release_review import review
    result["upstream_review"] = review(ROOT, repo)
    version_bump.write_version(ROOT, str(planned["bump"]))
    changelog.release(ROOT, version, repo=repo, spec=str(planned["range"]))
    # 那一节原样带在输出里：确认措辞的人不该再去翻文件才知道自己在确认什么。
    result["section"] = changelog.section_of(
        (ROOT / changelog.CHANGELOG).read_text(encoding="utf-8"), version)
    result["next"] = [
        f"核对上面 {version} 那一节的措辞，它是使用者唯一读到的说明",
        "确认后跑 --ship --apply：提交、推送、等 Test 转绿、打标签一次做完",
    ]
    return result


def _require_master() -> None:
    if command("git", "branch", "--show-current") != "master":
        raise ValueError("请从 master 主检出执行")


def _pending_files() -> list[str]:
    """工作区里有改动的文件名，按 `git status --porcelain` 的第三列取。"""
    return sorted(line[3:].strip().strip('"')
                  for line in command("git", "status", "--porcelain", strip=False).splitlines()
                  if line.strip())


def _staged_release(repo: str) -> tuple[str, str, list[str]]:
    """定版是否成立：版本号、标签，以及等着提交的文件。

    四道判据一起看：在 master 上、变更日志有这一节、标签本地远端都空着、工作区除了
    那两份定版文件没有别的。最后一条是为了不把顺手改的东西夹进发布提交——发布提交
    是标签指向的那一个，夹带什么就等于发出去什么。
    """
    _require_master()
    version = version_bump.read_version(ROOT)
    tag = "v" + version
    document = (ROOT / changelog.CHANGELOG).read_text(encoding="utf-8")
    if not changelog.has_section(document, version):
        raise ValueError(f"{changelog.CHANGELOG} 缺少 {version} 一节；先用 --bump 起草再润色")
    changelog.require_labeled_entries(document, version)
    _require_free_tag(repo, tag)
    if command("git", "tag", "--list", tag):
        raise ValueError(f"本地 {tag} 已存在，请先检查它的归属")
    pending = _pending_files()
    if pending and pending != sorted(RELEASE_FILES):
        raise ValueError("工作区除了版本号与变更日志还有别的改动：" + "、".join(pending))
    return version, tag, pending


def ship(repo: str, *, apply: bool, timeout: float = TEST_TIMEOUT) -> dict:
    """把定好版的工作区一路送到标签：提交、推送、等 Test 转绿、打标签。

    每一步先看当下的状态再决定做不做，所以中途停下（网络断、CI 还在跑、超时）再跑一次
    就接着走：已提交的不重提，已推送的不重推，Test 没绿绝不打标签。
    """
    version, tag, pending = _staged_release(repo)
    head = command("git", "rev-parse", "HEAD")
    remote = api(repo, "git/ref/heads/master")["object"]["sha"]
    result = {"version": version, "tag": tag, "repo": repo, "applied": apply,
              "commit": bool(pending), "push": bool(pending) or remote != head}
    if not apply:
        return result
    if pending:
        command("git", "add", *RELEASE_FILES)
        command("git", "-c", f"{MASTER_WRITER}=release", "commit",
                "-m", f"chore(release): 版本 {version}")
    sha = command("git", "rev-parse", "HEAD")
    if api(repo, "git/ref/heads/master")["object"]["sha"] != sha:
        command("git", "push", f"https://github.com/{repo}.git", "refs/heads/master")
    checked = await_test(repo, sha, timeout=timeout)
    command("git", "tag", "-a", tag, sha, "-m", f"Peach {tag} Windows 测试版")
    command("git", "push", f"https://github.com/{repo}.git", f"refs/tags/{tag}")
    return {**result, "sha": sha, "test": checked["html_url"],
            "release": f"https://github.com/{repo}/releases/tag/{tag}"}


def plan(repo: str) -> dict:
    _require_clean_master()
    sha = command("git", "rev-parse", "HEAD")
    if api(repo, "git/ref/heads/master")["object"]["sha"] != sha:
        raise ValueError("本地 HEAD 与 GitHub master 不一致，请先完成同步")
    version = version_bump.read_version(ROOT)
    tag = "v" + version
    document = (ROOT / changelog.CHANGELOG).read_text(encoding="utf-8")
    if not changelog.has_section(document, version):
        raise ValueError(f"{changelog.CHANGELOG} 缺少 {version} 一节；先用 --bump 起草再润色")
    _require_free_tag(repo, tag)
    if command("git", "tag", "--list", tag):
        raise ValueError(f"本地 {tag} 已存在，请先检查它的归属")
    return {**verify(repo, sha), "tag": tag, "repo": repo}


def plan_commit(repo: str, sha: str) -> dict:
    """发布明确选定的主线提交，版本与说明均取自这个提交。"""
    _require_clean_master()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("--release-sha 必须是完整的 40 位提交 SHA")
    if command("git", "rev-parse", f"{sha}^{{commit}}") != sha:
        raise ValueError("--release-sha 必须指向提交对象")
    text = command("git", "show", f"{sha}:{version_bump.VERSION_FILE}")
    match = version_bump.VERSION_PATTERN.search(text)
    if match is None:
        raise ValueError("选定提交没有有效的 __version__")
    version = ".".join(match.group(index) for index in (2, 3, 4))
    document = command("git", "show", f"{sha}:{changelog.CHANGELOG}")
    if not changelog.has_section(document, version):
        raise ValueError(f"选定提交的 {changelog.CHANGELOG} 缺少 {version} 一节")
    changelog.require_labeled_entries(document, version)
    tag = f"v{version}"
    _require_free_tag(repo, tag)
    if command("git", "tag", "--list", tag):
        raise ValueError(f"本地 {tag} 已存在，请先检查它的归属")
    return {**verify(repo, sha), "tag": tag, "version": version, "repo": repo,
            "section": changelog.section_of(document, version)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default="peach-mitao/peach")
    parser.add_argument("--apply", action="store_true",
                        help="真的动手：带 --bump 时写版本号与变更日志，带 --ship 时走完发布，"
                             "都不带时创建并推送标签")
    parser.add_argument("--timeout", type=float, default=TEST_TIMEOUT,
                        help=f"--ship 等 Test 工作流的秒数上限，默认 {TEST_TIMEOUT:.0f}")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--bump", choices=("auto", "patch", "minor", "major"),
                      help="推进 __version__ 并起草变更日志，为下一次发布做准备")
    mode.add_argument("--ship", action="store_true",
                      help="把定好版的工作区送到标签：提交、推送、等 Test 转绿、打标签")
    mode.add_argument("--verify-sha", help="仅核对指定提交的主线归属与 CI，供 Release 工作流使用")
    mode.add_argument("--release-sha", help="为完整 SHA 指定的已通过 CI 的主线提交补打标签")
    args = parser.parse_args(argv)
    try:
        if args.verify_sha:
            print(json.dumps(verify(args.repo, args.verify_sha), ensure_ascii=False, indent=2))
            return 0
        if args.bump:
            print(json.dumps(prepare(args.repo, args.bump, apply=args.apply),
                             ensure_ascii=False, indent=2))
            return 0
        if args.ship:
            print(json.dumps(ship(args.repo, apply=args.apply, timeout=args.timeout),
                             ensure_ascii=False, indent=2))
            return 0
        result = (plan_commit(args.repo, args.release_sha)
                  if args.release_sha is not None else plan(args.repo))
        if args.apply:
            # 固定提交在动手前复核归属、CI、版本说明与标签占用。
            if args.release_sha is not None:
                result = plan_commit(args.repo, args.release_sha)
            elif api(args.repo, "git/ref/heads/master")["object"]["sha"] != result["sha"]:
                raise ValueError("检查期间 master 已更新，请重新执行")
            command("git", "tag", "-a", result["tag"], result["sha"],
                    "-m", f"Peach {result['tag']} Windows 测试版")
            command("git", "push", f"https://github.com/{args.repo}.git",
                    f"refs/tags/{result['tag']}")
        print(json.dumps({**result, "applied": args.apply}, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, version_bump.VersionError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        print(f"发布检查未通过：{detail}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
