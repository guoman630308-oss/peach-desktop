"""建一个 master 上已有首个提交的临时 git 仓库，供测试按模板复制。

`git init`、仓库配置、add、commit 一套需要多次子进程，Windows 上每次约 0.05 秒；
三份工作树与发布脚本的测试每个 `setUp` 都来一遍，一百多个用例就是半分钟。同样内容的
种子仓库只真的建一次，之后 `copytree` 一份，几毫秒。

复制是安全的：刚 init 的仓库里没有绝对路径——没有 worktree 注册，`core.hooksPath` 也
还没设，那些都是用例自己在副本上做的事。副本之间互不相识，提交 sha 相同只是因为内容
与时间戳相同，用例比对的都是各自副本里的状态。
"""
from __future__ import annotations

import atexit
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

_templates: dict[str, Path] = {}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def seed_repository(destination: Path, files: dict[str, str | bytes], message: str) -> Path:
    """在 `destination` 放一个仓库：`master` 上一个提交，内容就是 `files`。

    `destination` 不能已存在，副本整个目录由调用方的临时目录负责回收。同一份
    `files` 与 `message` 复用同一个模板。
    """
    key = hashlib.sha256(json.dumps(
        {"message": message,
         "files": {name: (body if isinstance(body, str) else "hex:" + body.hex())
                   for name, body in sorted(files.items())}},
        sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    template = _templates.get(key)
    if template is None:
        holder = Path(tempfile.mkdtemp(prefix="peach-repo-template-")).resolve()
        atexit.register(shutil.rmtree, holder, True)
        template = holder / "repo"
        template.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "master", str(template)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _git(template, "config", "user.email", "test@example.invalid")
        _git(template, "config", "user.name", "Peach Test")
        # 命令返回前完成自动维护，副本回收时没有后台 Git 继续写入。
        _git(template, "config", "maintenance.autoDetach", "false")
        _git(template, "config", "gc.autoDetach", "false")
        for name, body in files.items():
            path = template / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(body, str):
                path.write_text(body, encoding="utf-8")
            else:
                path.write_bytes(body)
        _git(template, "add", "--", *files)
        _git(template, "commit", "-q", "-m", message)
        _templates[key] = template
    # 跳过 `.lock`：git 自己的锁文件是模板那个进程的临时状态（`objects/maintenance.lock`、
    # `index.lock` 之类），复制途中它可能正好消失，`copytree` 就整条报错；真复制过去也只是
    # 给副本留一把假锁。副本要的是提交和对象，不是锁。
    shutil.copytree(template, destination, ignore=shutil.ignore_patterns("*.lock"))
    return destination
