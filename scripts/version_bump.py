"""版本号档位判定与写入，供发布入口调用。

版本号只在发布点动：每个 `X.Y.Z` 都对应一个真实发布物，minor 位记的是「第几批带新
功能的发布」。日常集成的提交由 commit 标识——`build-info.json` 随包走，托盘按 commit
算自己落后几个提交——它们不需要各自占掉一个版本号。

档位判定的输入是「上一个版本标签到 HEAD」这段区间：`feat`／破坏性标记／新增迁移推
minor，其余推 patch，major 只在 1.0 那一次手工给（ADR-0012）。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

#: 版本号的唯一来源。GitHub Release 的 `v<版本>` tag 必须与它相等。
VERSION_FILE = "src/peach/__init__.py"
VERSION_PATTERN = re.compile(r'(__version__\s*=\s*")(\d+)\.(\d+)\.(\d+)(")')

#: 版本标签的形状。`--match` 用它排除别的标签，免得把某个临时标签当成上一个发布点。
TAG_GLOB = "v[0-9]*.[0-9]*.[0-9]*"

#: 决定运行时代码、依赖与发布构建的输入。清单之外的开发过程文件不触发版本发布。
#: 清单与 `WINDOWS_TRAY_INPUTS` 各自服务不同的问题：
#: 那份决定要不要重建 EXE，这份决定这批改动够不够格成为一个新版本。
RUNTIME_INPUTS = (
    "src/peach/",
    "web/",
    "frontend/",
    "migrations/",
    "resources/",
    "scripts/build_app_entry.py",
    "scripts/build_windows.ps1",
    "pyproject.toml",
    "uv.lock",
    ".github/workflows/release.yml",
)

#: Conventional Commits 的功能类型；`!` 是破坏性标记。pre-1.0 阶段两者都只推 minor。
FEATURE_SUBJECT = re.compile(r"^(feat|feature)(\(.*\))?!?:|^\w+(\(.*\))?!:")


class VersionError(RuntimeError):
    pass


def git(root: Path, *args: str) -> str:
    """在 `root` 上跑一条只读 git，返回 stdout。失败即抛，不静默当成空结果。"""
    done = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                          text=True, encoding="utf-8", check=False)
    if done.returncode != 0:
        raise VersionError(f"git {' '.join(args)} 失败：{done.stderr.strip()}")
    return done.stdout.strip()


def lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def last_tag(root: Path) -> str | None:
    """最近一个版本标签；一个都没有时返回 None。"""
    done = subprocess.run(["git", "describe", "--tags", "--abbrev=0", "--match", TAG_GLOB],
                          cwd=str(root), capture_output=True, text=True,
                          encoding="utf-8", check=False)
    tag = done.stdout.strip()
    return tag if done.returncode == 0 and tag else None


def release_range(root: Path) -> str:
    """上一个版本标签到 HEAD。没有标签时是整段历史。"""
    tag = last_tag(root)
    return f"{tag}..HEAD" if tag else "HEAD"


def subjects_in(root: Path, spec: str) -> list[str]:
    """区间里的提交主题，合并提交不算——它们的主题只说明分支名。"""
    return lines(git(root, "log", "--no-merges", "--format=%s", spec))


def changed_in(root: Path, spec: str) -> list[str]:
    return lines(git(root, "diff", "--name-only", _diff_spec(spec)))


def added_in(root: Path, spec: str) -> list[str]:
    return lines(git(root, "diff", "--name-only", "--diff-filter=A", _diff_spec(spec)))


#: 空树的对象名，git 里的常量。一个版本标签都没有时，「这批改动」就是整段历史。
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _diff_spec(spec: str) -> str:
    """`A..B` 交给 diff 时写成 `A...B`：只看这条线自己带来的改动。"""
    return spec.replace("..", "...") if ".." in spec else f"{EMPTY_TREE}..{spec}"


def runtime_inputs_changed(paths) -> bool:
    """这批改动里有没有会进到运行时产物的。"""
    normalized = [str(path).replace("\\", "/").strip("/") for path in paths]
    return any(path == prefix.rstrip("/") or path.startswith(prefix)
               for path in normalized for prefix in RUNTIME_INPUTS)


def bump_part_for(subjects, added_paths) -> str:
    """这批提交该推版本号的哪一位。

    有功能提交（`feat:`）或破坏性标记（`type!:`），或者新增了迁移文件，就是 minor；
    其余（修复、重构、文案、构建）是 patch。major 只在 1.0 那一次手工给，这里不判。
    """
    if any(FEATURE_SUBJECT.match(subject.strip()) for subject in subjects):
        return "minor"
    normalized = [str(path).replace("\\", "/").strip("/") for path in added_paths]
    if any(path.startswith("migrations/") for path in normalized):
        return "minor"
    return "patch"


def bump_version(text: str, part: str) -> tuple[str, str]:
    """把 `__version__` 那一行往前推一格，返回新正文与新版本号。

    纯函数，只认那一行：整份文件其余部分逐字节保留，换行也不碰。
    """
    match = VERSION_PATTERN.search(text)
    if match is None:
        raise VersionError(f"{VERSION_FILE} does not declare __version__")
    major, minor, patch = (int(match.group(index)) for index in (2, 3, 4))
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    elif part == "patch":
        patch += 1
    else:
        raise VersionError(f"unknown bump part: {part}")
    version = f"{major}.{minor}.{patch}"
    updated = text[:match.start()] + f"{match.group(1)}{version}{match.group(5)}" \
        + text[match.end():]
    return updated, version


def read_version(root: Path) -> str:
    match = VERSION_PATTERN.search((root / VERSION_FILE).read_bytes().decode("utf-8"))
    if match is None:
        raise VersionError(f"{VERSION_FILE} does not declare __version__")
    return f"{match.group(2)}.{match.group(3)}.{match.group(4)}"


def write_version(root: Path, part: str) -> str:
    """把新版本号写进版本文件并返回它。不提交——发布提交由调用方连同变更日志一起做。"""
    path = root / VERSION_FILE
    updated, version = bump_version(path.read_bytes().decode("utf-8"), part)
    path.write_bytes(updated.encode("utf-8"))
    return version


def plan_bump(root: Path, part: str = "auto") -> dict[str, object]:
    """算出这次发布该用哪一档、推到哪个版本号。只读，不写文件。"""
    spec = release_range(root)
    subjects = subjects_in(root, spec)
    if not subjects:
        raise VersionError(f"{spec} 里没有提交，没有可发布的内容")
    if not runtime_inputs_changed(changed_in(root, spec)):
        raise VersionError(f"{spec} 只碰了开发过程的文件，发布物与上一个标签相同")
    chosen = bump_part_for(subjects, added_in(root, spec)) if part == "auto" else part
    current = read_version(root)
    return {
        "range": spec,
        "bump": chosen,
        "current": current,
        "version": bump_version(f'__version__ = "{current}"', chosen)[1],
        "commits": len(subjects),
    }
