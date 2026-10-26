"""把 Dependabot 的清单升级接管到本地分支，顺手重算派生产物。默认只列计划，`--apply` 才提交。

Dependabot 只改 manifest 和 lock。根 `package.json` 的 npm 包还有一层派生产物——
`web/vendor/**` 的字节、`web/index.html` 里那行版本注释、每个 `ORIGIN.md` 的哈希与
lock integrity——它算不出来，于是 `npm run check:vendor` 在它的 PR 上必红（实际发生过：
lucide-static 1.38.0 → 1.40.0 的 PR #4）。`frontend/package.json` 同理，产物是
`web/dist/` 中的主界面与独立页面包。

修不了那个 PR 本身：Dependabot 触发的 workflow 拿到的 token 是只读的，往
`dependabot/**` 推回重算的产物得靠 `pull_request_target` 或一个 PAT，两条都是给 CI 加
提权面。所以接管在本地做——凭据是你自己的，CI 一行都不用改。

uv 与 github-actions 没有派生产物，也在这里接管：master 在本机集成、通常领先 origin，
在网页上合并会让两边分叉，回并又被 `scripts/githooks/` 拒收。

每个 PR 做完这些：

1. `git fetch` 它的 head 分支，把它对清单的改动三方合并套到当前分支；
2. 按清单重算派生产物（npm），或核对锁文件（uv）；
3. 列出真实改动的文件，`--apply` 时只暂存这些并提交，一个 PR 一个提交。

`--all-open` 按编号从小到大接管全部 open 的 Dependabot PR。两个 PR 改到相邻行时三方
合并报冲突，脚本停在那个 PR，报出冲突文件并备好提交说明；解完冲突提交后再跑一次，
已经在 HEAD 上的升级会跳过。

测试不在这里跑：仓库只有一个测试入口，另拼一条会让 `test_evidence` 的记录对不上。
脚本最后印出该跑的命令与推送后收尾的 `gh pr close`。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "scripts"
from . import co_author

ROOT = Path(__file__).resolve().parents[1]

#: 每份清单的接管方式。`manifests` 是从 Dependabot 分支套过来的文件，`derived` 是重算后
#: 可能变的路径，`commands` 是重算命令，`checks` 只核对不产出文件。以 `/` 结尾的条目按
#: 目录算，其余按整条路径比。
RECIPES = {
    "docker": {
        "label": "应用与工作流 Docker 镜像",
        "manifests": ("Dockerfile", ".github/actions/actionlint/Dockerfile"),
        "derived": (),
        "commands": (),
        "checks": (),
    },
    "web": {
        "label": "根 package.json（本地固定 web 依赖）",
        "manifests": ("package.json", "package-lock.json"),
        "derived": ("web/vendor/", "web/index.html", "web/dist/", "docs/REUSE.md",
                    "frontend/src/player/videojs.ts", "tests/test_web_ui.py",
                    "frontend/src/react/photo-lightbox/photo-lightbox.ts",
                    "frontend/test/react/photo-lightbox.test.tsx"),
        "commands": (("npm", "ci", "--ignore-scripts"), ("npm", "run", "vendor:web"),
                     ("npm", "--prefix", "frontend", "ci"),
                     ("npm", "--prefix", "frontend", "run", "build")),
        "checks": (),
    },
    "frontend": {
        "label": "frontend/package.json（island 层构建依赖）",
        "manifests": ("frontend/package.json", "frontend/package-lock.json"),
        "derived": ("web/dist/",),
        "commands": (("npm", "--prefix", "frontend", "ci"),
                     ("npm", "--prefix", "frontend", "run", "build")),
        "checks": (),
    },
    "uv": {
        "label": "Python 依赖（pyproject.toml 与 uv.lock）",
        "manifests": ("pyproject.toml", "uv.lock"),
        "derived": (),
        "commands": (),
        "checks": (("uv", "lock", "--check"),),
    },
    "actions": {
        "label": "GitHub Actions 工作流",
        "manifests": (".github/workflows/",),
        "derived": (),
        "commands": (),
        "checks": (),
    },
}

#: 推送后关 PR 时的留言，按有没有派生产物分两种说法。
CLOSE_COMMENT = {
    True: "派生产物需要本地重算，已在工作树接管进 master",
    False: "本地 master 领先 origin，网页合并会让两边分叉，已在工作树接管进 master",
}


class ConflictError(RuntimeError):
    """三方合并留下了冲突，`files` 是冲突文件。"""

    def __init__(self, message: str, files: list[str]):
        super().__init__(message)
        self.files = files


def run(command: tuple[str, ...] | list[str], *, capture: bool = True,
        root: Path = ROOT) -> str:
    """在仓库根目录跑一条命令，失败即抛。

    第一个词过一遍 `shutil.which`：Windows 上 `npm` 是 `npm.cmd`，而 `CreateProcess`
    不查 `PATHEXT`，照字面传就是 FileNotFoundError。
    """
    executable = shutil.which(command[0]) or command[0]
    result = subprocess.run([executable, *command[1:]], cwd=root, capture_output=capture,
                            text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise RuntimeError(f"{' '.join(command)} 退出码 {result.returncode}\n{detail}")
    return (result.stdout or "") if capture else ""


def matches(entry: str, path: str) -> bool:
    return path.startswith(entry) if entry.endswith("/") else path == entry


def recipe_for(paths: list[str]) -> tuple[str, dict]:
    """一批改动文件属于哪份清单。

    同时动了两份就拒绝：那不是 Dependabot 的形状（一个 PR 只碰一个 ecosystem 的一个
    directory），硬按一份重算会把另一份的产物留在旧版本上。清单之外的文件也拒绝：
    认不出来的改动不替人决定怎么接。
    """
    matched = [key for key, recipe in RECIPES.items()
               if any(matches(entry, path) for entry in recipe["manifests"] for path in paths)]
    if not matched:
        known = "、".join(entry for recipe in RECIPES.values() for entry in recipe["manifests"])
        raise RuntimeError(f"这个分支没有改动认得的清单（认得的是 {known}）")
    if len(matched) > 1:
        raise RuntimeError(f"这个分支同时改了 {len(matched)} 份清单，逐份接管：{matched}")
    recipe = RECIPES[matched[0]]
    stray = [path for path in paths if not any(matches(entry, path) for entry in recipe["manifests"])]
    if stray:
        raise RuntimeError(f"这个分支改了清单之外的文件：{stray}")
    return matched[0], recipe


def open_pull_requests() -> list[dict]:
    """全部 open 的 Dependabot PR，按编号从小到大。"""
    payload = json.loads(run(("gh", "pr", "list", "--state", "open", "--author", "app/dependabot",
                              "--limit", "100", "--json", "number,title,headRefName")))
    return sorted(payload, key=lambda item: item["number"])


def pull_request(pr: str) -> dict:
    payload = json.loads(run(("gh", "pr", "view", pr, "--json", "number,title,headRefName,state")))
    if payload["state"] != "OPEN":
        raise RuntimeError(f"PR #{pr} 状态是 {payload['state']}，不是 OPEN")
    return payload


def fetch(branch: str, *, root: Path = ROOT) -> None:
    run(("git", "fetch", "--quiet", "origin", branch), root=root)


def branch_changes(branch: str, *, root: Path = ROOT) -> list[str]:
    """那个分支自己改了哪些文件。

    判据是它与 `HEAD` 的合并基，不是 `HEAD` 本身：Dependabot 的分支从几天前的 master
    分出去，直接 `git diff origin/<branch>` 会把这几天 master 上的每一次改动也算进来，
    于是每个 PR 看上去都动了清单。
    """
    return [line for line in
            run(("git", "diff", "--name-only", "--merge-base", "HEAD", f"origin/{branch}"),
                root=root).splitlines()
            if line]


def bring_over(branch: str, manifests: tuple[str, ...], *, root: Path = ROOT,
               refresh: bool = True) -> None:
    """取回那个分支，把它对清单的改动套到工作区，不暂存。

    套的是它自合并基以来的 diff，不是整份文件：分支的清单停在几天前的 master 上，
    整份签出会把这几天 master 往清单里加的依赖一并抹掉（实际发生过：#20 的
    `frontend/package.json` 没有后来加进来的 `sonner`）。两边改到同一行或相邻行时
    `--3way` 留下冲突，抛 `ConflictError` 报出文件，不替人挑一边。补丁先落成文件再给
    `git apply`：Windows 上文本模式的 stdin 会把 `\\n` 写成 `\\r\\n`，补丁就对不上了。
    `--3way` 要求工作区与暂存区一致，连着套几个分支时，每套一个先暂存再套下一个。
    刚 fetch 过的调用方传 `refresh=False`。
    """
    if refresh:
        fetch(branch, root=root)
    patch = run(("git", "diff", "--merge-base", "HEAD", f"origin/{branch}", "--", *manifests),
                root=root)
    if not patch:
        return
    path = root / "build" / "adopt.patch"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(patch, encoding="utf-8", newline="\n")
    try:
        run(("git", "apply", "--3way", str(path)), root=root)
    except RuntimeError as exc:
        conflicts = [line for line in
                     run(("git", "diff", "--name-only", "--diff-filter=U"), root=root).splitlines()
                     if line]
        if conflicts:
            raise ConflictError(f"origin/{branch} 与当前分支改到同一处，三方合并留下冲突",
                                conflicts) from exc
        raise
    finally:
        path.unlink()
    run(("git", "reset", "--quiet", "--", *manifests), root=root)


def working_changes(*, root: Path = ROOT) -> list[str]:
    tracked = run(("git", "diff", "--name-only", "HEAD"), root=root).splitlines()
    added = run(("git", "ls-files", "--others", "--exclude-standard"), root=root).splitlines()
    return sorted({line for line in (*tracked, *added) if line})


def commit_message(key: str, versions: list[str], pr: str | None,
                   signature: str) -> str:
    """接管提交的说明。README-Impact 与 Co-Authored-By 必须同一个 trailer 块、中间不空行。

    署名由 `--co-author` 传进来，脚本不替谁署名：跑它的可能是任一个智能体，写死一个
    工具名就是往提交历史里记错人。形态由 `co_author.FORM` 判，这里提前判一次，免得
    错在 `ready` 才报出来。
    """
    if co_author.FORM.fullmatch(signature) is None:
        raise ValueError("--co-author 形态须为 工具 (模型 版本) <厂商 noreply>，如 "
                         + co_author.EXAMPLE.partition(": ")[2])
    recipe = RECIPES[key]
    origin = f"Dependabot PR #{pr}" if pr else "Dependabot 分支"
    if recipe["commands"]:
        rebuild = "`" + "`、`".join(" ".join(item) for item in recipe["commands"]) + "`"
        why = (f"{origin} 只改了 manifest 与 lock。派生产物由 {rebuild} 重算，"
               "它在只读 token 下算不出来，所以接管到本地分支一起提交。")
    else:
        why = (f"{origin} 没有派生产物。本地 master 领先 origin 时在网页上合并会让两边分叉，"
               "所以接管到本地分支。")
    return (f"chore(deps): 接管 {recipe['label']} 的升级\n\n{why}\n\n"
            + ("升级：" + "、".join(versions) + "\n\n" if versions else "")
            + "README-Impact: none; 依赖版本与派生产物，README 不涉及。\n"
            + f"Co-Authored-By: {signature}\n")


def manifest_versions(key: str, *, root: Path = ROOT) -> list[str]:
    """从套过来的清单里读出被改掉的版本，只为写进提交说明。"""
    lines = []
    for name in RECIPES[key]["manifests"]:
        if not name.endswith("package.json"):
            continue
        before = json.loads(run(("git", "show", f"HEAD:{name}"), root=root) or "{}")
        after = json.loads((root / name).read_text(encoding="utf-8"))
        for section in ("dependencies", "devDependencies"):
            old, new = before.get(section, {}), after.get(section, {})
            lines += [f"{package} {old.get(package, '新增')} → {version}"
                      for package, version in new.items() if old.get(package) != version]
    return lines


def owned_by(recipe: dict, path: str) -> bool:
    """这个路径是不是本次接管该动的：清单本身或它的派生产物。"""
    return any(matches(entry, path) for entry in (*recipe["manifests"], *recipe["derived"]))


def close_command(pr: int, key: str) -> str:
    comment = CLOSE_COMMENT[bool(RECIPES[key]["commands"])]
    return f'gh pr close {pr} --delete-branch --comment "{comment}"'


def adopted(number: int, branch: str, *, root: Path = ROOT) -> bool:
    """那个分支分叉之后，HEAD 上有没有提交说明写着接管了这个 PR。

    不拿三方合并的结果判：上一轮停在冲突、人解完提交后，前面那些 PR 再套一次会跟解冲突
    留下的相邻行再撞一次，「已接管的跳过」就成了「再停一次」。
    """
    base = run(("git", "merge-base", "HEAD", f"origin/{branch}"), root=root).strip()
    log = run(("git", "log", "--format=%B", f"{base}..HEAD"), root=root)
    return re.search(rf"Dependabot PR #{number}(?!\d)", log) is not None


def adopt(pr: dict, *, apply: bool, signature: str, root: Path = ROOT) -> dict:
    """接管一个 PR。`apply` 时暂存并提交；否则改动留在工作区未暂存。

    升级已经在 HEAD 上（上次停在冲突、解完提交后再跑）时什么都不做，报 `already`。
    """
    branch, number = pr["headRefName"], pr.get("number")
    fetch(branch, root=root)
    key, recipe = recipe_for(branch_changes(branch, root=root))
    result = {"pr": number, "branch": branch, "recipe": key}
    if number and adopted(number, branch, root=root):
        return {**result, "already": True}
    bring_over(branch, recipe["manifests"], root=root, refresh=False)
    if not working_changes(root=root):
        return {**result, "already": True}
    versions = manifest_versions(key, root=root) or ([pr["title"]] if pr.get("title") else [])
    for command in recipe["commands"]:
        run(command, capture=False, root=root)
    for command in recipe["checks"]:
        run(command, root=root)
    # 只认真实变了的那些。清单动了产物却一个字节没变是常事（补丁版没碰 vendored 的
    # 那几个文件），照 `derived` 前缀盲暂存会把无关文件带上。
    touched = working_changes(root=root)
    landed = sorted(path for path in touched if owned_by(recipe, path))
    stray = sorted(path for path in touched if not owned_by(recipe, path))
    result.update(versions=versions, files=landed, unexpected=stray, applied=False)
    if stray:
        raise RuntimeError(f"PR #{number} 重算动到了清单与派生产物之外的文件，先看清楚：{stray}")
    if apply:
        run(("git", "add", "--", *landed), root=root)
        message = root / "build" / f"adopt-{key}.txt"
        message.parent.mkdir(parents=True, exist_ok=True)
        message.write_text(commit_message(key, versions, number and str(number), signature),
                           encoding="utf-8", newline="\n")
        run(("git", "commit", "--quiet", "-F", str(message)), root=root)
        message.unlink()
        result.update(applied=True, head=run(("git", "rev-parse", "HEAD"), root=root).strip())
    return result


def adopt_all(prs: list[dict], *, signature: str, root: Path = ROOT) -> dict:
    """逐个接管并提交；停在第一个冲突或失败的 PR，已提交的留着。"""
    commit_message("uv", [], None, signature)  # 署名写错就一个都别动
    done: list[dict] = []
    for pr in prs:
        try:
            done.append(adopt(pr, apply=True, signature=signature, root=root))
        except ConflictError as exc:
            key, recipe = recipe_for(branch_changes(pr["headRefName"], root=root))
            message = root / "build" / f"adopt-pr-{pr['number']}.txt"
            message.write_text(commit_message(key, [pr["title"]], str(pr["number"]), signature),
                               encoding="utf-8", newline="\n")
            rebuild = [" ".join(command) for command in (*recipe["commands"], *recipe["checks"])]
            return {"ok": False, "error": str(exc), "stopped_at": pr["number"],
                    "conflicts": exc.files, "done": done,
                    "next": [f"解开冲突：{'、'.join(exc.files)}",
                             *(f"重算或核对：{command}" for command in rebuild),
                             f"git add -- {' '.join(exc.files)}"
                             + ("，连同重算改到的派生产物（看 git status）" if recipe["commands"] else ""),
                             f"git commit -F {message.relative_to(root).as_posix()}",
                             "再跑一次 --all-open --apply，已接管的会跳过"]}
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc), "stopped_at": pr["number"], "done": done}
    next_steps = ["测试：& .\\scripts\\test.ps1 full",
                  "交付：scripts/agent_worktree.py ready，再由协调者 integrate",
                  "推送 master 之后逐个收尾：",
                  *(close_command(item["pr"], item["recipe"]) for item in done)]
    return {"ok": True, "done": done, "next": next_steps if done else []}


def plan_all(prs: list[dict], *, root: Path = ROOT) -> dict:
    """只列要接管的 PR 与各自的清单，不动工作区。"""
    items = []
    for pr in prs:
        fetch(pr["headRefName"], root=root)
        paths = branch_changes(pr["headRefName"], root=root)
        try:
            key, _ = recipe_for(paths)
            items.append({"pr": pr["number"], "title": pr["title"], "recipe": key, "files": paths})
        except RuntimeError as exc:
            items.append({"pr": pr["number"], "title": pr["title"], "files": paths,
                          "error": str(exc)})
    return {"ok": all("error" not in item for item in items), "prs": items,
            "next": ["加 --apply 逐个接管并提交"] if items else []}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pr", help="Dependabot 的 PR 编号")
    source.add_argument("--branch", help="Dependabot 的分支名，跳过 gh 查询")
    source.add_argument("--all-open", action="store_true",
                        help="按编号从小到大接管全部 open 的 Dependabot PR")
    parser.add_argument("--apply", action="store_true", help="暂存并提交；缺省只列计划")
    parser.add_argument("--co-author", required=True,
                        help="本次提交的署名，形如 "
                             "Claude Code (Opus 5) <noreply@anthropic.com>")
    args = parser.parse_args(argv)

    try:
        if run(("git", "rev-parse", "--git-dir")).strip() == run(("git", "rev-parse", "--git-common-dir")).strip():
            raise RuntimeError("这是主检出。主检出只做集成，先 "
                               "`scripts/agent_worktree.py create --agent <名> --task deps-<包名>`。")
        if run(("git", "status", "--porcelain")).strip():
            raise RuntimeError("工作区不干净。接管会往里写重算出来的产物，先把手上的改动收掉。")
        if args.all_open:
            prs = open_pull_requests()
            plan = (adopt_all(prs, signature=args.co_author) if args.apply else plan_all(prs))
        else:
            pr = pull_request(args.pr) if args.pr else {"headRefName": args.branch}
            item = adopt(pr, apply=args.apply, signature=args.co_author)
            plan = {"ok": True, **item,
                    "next": ["测试：& .\\scripts\\test.ps1 full",
                             "交付：scripts/agent_worktree.py ready，再由协调者 integrate"]}
            if item.get("already"):
                plan["next"] = []
            elif not args.apply:
                plan["next"].insert(0, "改动留在工作区未暂存："
                                       f"`git restore -- {' '.join(item['files'])}` 撤销，加 --apply 提交")
            elif args.pr:
                plan["next"].append("推送 master 之后收尾：" + close_command(item["pr"], item["recipe"]))
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0 if plan["ok"] else 1
    except ConflictError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "conflicts": exc.files},
                         ensure_ascii=False, indent=2))
        return 1
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
