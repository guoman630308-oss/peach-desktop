from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__
from .config import PROJECT_ROOT


def discover_repository_root(
    resource_root: Path = PROJECT_ROOT,
    *,
    executable: Path | None = None,
) -> Path:
    """Find the source repository used by the tray without inventing one."""

    resource_root = resource_root.resolve()
    if (resource_root / ".git").exists():
        return resource_root

    executable = (executable or Path(sys.executable)).resolve()
    for parent in executable.parents:
        if (parent / ".git").exists():
            return parent
    return resource_root


#: `build_behind` 的取值：None 表示不适用（源码运行或独立测试包），`UNKNOWN_BUILD_AGE`
#: 表示托盘是打包的、却数不出它落后多少——身份未取得，或那个提交不在本检出的历史里。
#: 两种情况都当作陈旧：数不出来不等于没落后，而重建一次的代价只是一次构建。
UNKNOWN_BUILD_AGE = -1


@dataclass(frozen=True)
class VersionSnapshot:
    package_version: str
    branch: str
    commit: str
    dirty: bool
    remote_configured: bool
    upstream: str | None
    #: 当前托盘 EXE 是从哪个提交打包出来的。源码运行、独立测试包与身份未取得时为 None。
    build_commit: str | None = None
    #: 那份构建落后检出多少个提交。
    build_behind: int | None = None

    @property
    def build_stale(self) -> bool:
        """打包托盘跑的是构建那一刻的代码副本；检出往前走了，它不会跟着变。"""
        return self.build_behind is not None and self.build_behind != 0

    @property
    def build_label(self) -> str:
        dirty = " · 有未提交修改" if self.dirty else ""
        base = f"{self.branch}@{self.commit}{dirty}"
        if not self.build_stale:
            return base
        if self.build_commit is None:
            return f"{base} · 托盘构建身份未取得"
        if self.build_behind is not None and self.build_behind > 0:
            return f"{base} · 托盘构建 {self.build_commit[:8]}，落后 {self.build_behind} 个提交"
        return f"{base} · 托盘构建 {self.build_commit[:8]} 不在本检出历史里"

    @property
    def channel_label(self) -> str:
        if self.commit == "release":
            return "更新通道 · GitHub 测试版"
        if not self.remote_configured:
            return "本地开发版 · 未配置更新源"
        if not self.upstream:
            return "已配置更新源 · 未跟踪分支"
        return f"更新通道 · {self.upstream}"


@dataclass(frozen=True)
class UpdateResult:
    state: str
    message: str
    snapshot: VersionSnapshot
    ahead: int = 0
    behind: int = 0
    #: 这次快进改动了哪些仓库相对路径。只有 `updated` 会非空，调用方据此判断
    #: 光重启子服务够不够。
    changed_paths: tuple[str, ...] = ()


class VersionManager:
    """Read-only Git/package version inspection and explicit update checks."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        execute: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.root = (root if root is not None else discover_repository_root()).resolve()
        self._execute = execute

    def _git(self, *args: str, timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        return self._execute(
            [
                "git", "-c", f"safe.directory={self.root.as_posix()}",
                "-C", str(self.root), *args,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=creationflags,
        )

    def _text(self, *args: str) -> str:
        result = self._git(*args)
        return result.stdout.strip() if result.returncode == 0 else ""

    def inspect(self) -> VersionSnapshot:
        package_version = __version__
        from .distribution import standalone
        if standalone():
            return VersionSnapshot(package_version, "测试包", "release", False, False, None)
        commit = self._text("rev-parse", "--short=8", "HEAD") or "unknown"
        branch = self._text("branch", "--show-current") or "detached"
        dirty = bool(self._text("status", "--porcelain"))
        remote_configured = self._git("remote", "get-url", "origin").returncode == 0
        upstream = self._text("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") or None
        if remote_configured and upstream is None and branch != "detached":
            candidate = f"origin/{branch}"
            if self._git("show-ref", "--verify", f"refs/remotes/{candidate}").returncode == 0:
                upstream = candidate
        build_commit, build_behind = self.build_age()
        return VersionSnapshot(package_version, branch, commit, dirty, remote_configured,
                               upstream, build_commit, build_behind)

    def head_commit(self) -> str:
        """检出当前的 HEAD 完整 sha。只读本地，不联网，够廉价到能反复问。"""
        return self._text("rev-parse", "HEAD")

    def build_age(self) -> tuple[str | None, int | None]:
        """当前进程的构建提交，以及它落后检出多少个提交。

        源码运行的托盘没有第二个版本：它 import 的就是检出里的模块，永远不落后。
        """
        from .buildinfo import frozen_build
        if not getattr(sys, "frozen", False):
            return None, None
        info = frozen_build()
        commit = info.commit if info else None
        if not commit or not self._known_commit(commit):
            return commit, UNKNOWN_BUILD_AGE
        counted = self._text("rev-list", "--count", f"{commit}..HEAD")
        return commit, int(counted) if counted.isdigit() else UNKNOWN_BUILD_AGE

    def _known_commit(self, commit: str) -> bool:
        return self._git("cat-file", "-e", f"{commit}^{{commit}}").returncode == 0

    def stale_build_paths(self, build_commit: str | None) -> tuple[str, ...]:
        """从构建那个提交到现在，检出改了哪些仓库相对路径。

        数不出来时返回 `src/peach/`：它必然命中 `WINDOWS_TRAY_INPUTS`，于是「身份未取得」
        走的是重建而不是静悄悄地跳过。托盘旧着不动才是真正会骗人的那个结果。
        """
        if not build_commit or not self._known_commit(build_commit):
            return ("src/peach/",)
        return tuple(
            line for line in
            self._text("diff", "--name-only", f"{build_commit}..HEAD").splitlines()
            if line
        )

    def check(self) -> UpdateResult:
        before = self.inspect()
        from .distribution import standalone
        if standalone():
            from .release_updates import check
            release = check()
            latest = f"最新测试版：{release['latest_version']}。" if release['latest_version'] else ""
            return UpdateResult(release["state"], f"当前版本：{before.package_version}。{latest}{release['message']}", before)
        if not before.remote_configured:
            return UpdateResult(
                "unconfigured",
                f"Peach {before.package_version}（{before.build_label}）\n未配置更新源；当前为本地开发版。",
                before,
            )
        fetched = self._git("fetch", "--quiet", "--prune", "origin", timeout=30)
        if fetched.returncode != 0:
            return UpdateResult("error", "无法连接更新源；本地版本未改变。", before)
        current = self.inspect()
        if not current.upstream:
            return UpdateResult(
                "untracked",
                "更新源已连接，但当前分支没有对应的跟踪分支。",
                current,
            )
        counts = self._text("rev-list", "--left-right", "--count", f"HEAD...{current.upstream}").split()
        if len(counts) != 2 or not all(value.isdigit() for value in counts):
            return UpdateResult("error", "更新源已连接，但无法比较版本。", current)
        ahead, behind = map(int, counts)
        if behind and ahead:
            state = "diverged"
            message = f"本地领先 {ahead} 个提交，远端领先 {behind} 个提交；需要人工合并。"
        elif behind:
            state = "available"
            message = f"发现 {behind} 个新提交。为保护并行工作树，只检查、不自动安装。"
        elif ahead:
            state = "ahead"
            message = f"当前已比更新通道领先 {ahead} 个提交。"
        else:
            state = "current"
            message = f"Peach {current.package_version} 已是当前更新通道的最新版本。"
        return UpdateResult(state, message, current, ahead=ahead, behind=behind)

    def update(self) -> UpdateResult:
        """把本地检出快进到更新通道，只做快进。

        不 stash、不 rebase、不 `--force`：并行工作树和主检出共用同一个对象库与
        reflog，任何一种改写历史的「顺手解决」都会把别的分支一起拖下水。凡是快进
        做不到的，一律原样报出来交给人。

        `check()` 已经 fetch 过并给出了状态，这里只在 `available` 时才动工作区；
        其余状态（未配置、无跟踪分支、已最新、本地领先、两边分叉）直接透传，
        调用方不需要再分一次类。
        """
        checked = self.check()
        from .distribution import standalone
        if standalone():
            return checked
        if checked.state != "available":
            return checked
        snapshot = checked.snapshot
        if snapshot.dirty:
            return UpdateResult(
                "blocked",
                f"更新通道有 {checked.behind} 个新提交，但本地检出有未提交修改；"
                "先处理掉再同步。",
                snapshot, behind=checked.behind,
            )
        before = self._text("rev-parse", "HEAD")
        merged = self._git("merge", "--ff-only", str(snapshot.upstream), timeout=60)
        if merged.returncode != 0:
            return UpdateResult(
                "blocked",
                f"无法快进到 {snapshot.upstream}；本地检出未改变，需要人工处理。",
                snapshot, behind=checked.behind,
            )
        current = self.inspect()
        changed = tuple(
            line for line in self._text("diff", "--name-only", f"{before}..HEAD").splitlines()
            if line
        )
        return UpdateResult(
            "updated",
            f"已同步 {checked.behind} 个提交：{before[:8]} → {current.commit}。",
            current, behind=checked.behind, changed_paths=changed,
        )
