"""本机测试证据：代码内容、依赖环境和验证范围共同决定有效性。"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from filelock import FileLock, Timeout

from . import change_impact


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.strip()


def evidence_dir(root: Path) -> Path:
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    return common.parent / "build" / "agent-verification"


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


_STATE = re.compile(r"[0-9a-f]{64}")


def manifest(root: Path) -> dict:
    names = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    stages = git(root, "ls-files", "--stage", "-z").split("\0")
    modes = {entry.split("\t", 1)[1]: entry.split(" ", 1)[0] for entry in stages if "\t" in entry}
    # 未跟踪文件按 Git 的 filemode 策略预估暂存模式，暂存本身不使内容记录失效。
    policy = git(root, "config", "--get", "--default", "true", "core.filemode") == "true"
    files, signatures = {}, {}
    for name in sorted(set(names.split("\0")) - {""}):
        path = root / name
        if path.is_symlink():
            files[name] = "link:" + os.readlink(path)
        elif path.is_file():
            content = path.read_bytes()
            mode = stat.S_IMODE(path.stat().st_mode)
            index_mode = modes.get(name, "100755" if policy and mode & 0o111 else "100644")
            files[name] = [mode, hashlib.sha256(content).hexdigest(), index_mode]
            if name.endswith(".py") or name in {*change_impact.PACKAGE_FILES, "pyproject.toml"}:
                try:
                    signatures[name] = change_impact.signatures(name, content.decode("utf-8"))
                except UnicodeError:
                    pass
        elif not path.exists():
            files[name] = "deleted"
        else:
            raise ValueError(f"无法为文件生成验证快照：{name}")
    version = root / "src/peach/__init__.py"
    body = version.read_text(encoding="utf-8") if version.is_file() else ""
    body = re.sub(r'(?m)^__version__ = "\d+\.\d+\.\d+"$', '__version__ = "VERSION"', body)
    return {"files": files, "signatures": signatures, "version_body": digest(body)}


def snapshot(root: Path) -> str:
    return digest(manifest(root))


def interpreter(root: Path) -> Path:
    suffix = Path("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    local = root / ".venv" / suffix
    if local.is_file():
        return local.absolute()
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    shared = common.parent / ".venv" / suffix
    return shared.absolute() if shared.is_file() else Path(sys.executable).absolute()


@lru_cache(maxsize=16)
def python_identity(executable: str, stamp: int) -> dict:
    return json.loads(subprocess.run(
        [executable, "-c", "import json,sys,site; print(json.dumps({'version':sys.version,'sites':site.getsitepackages()}))"],
        check=True, capture_output=True, text=True, encoding="utf-8").stdout)


#: 外部工具的版本探针。工具身份取它自报的版本，不取 PATH 解析到的路径和文件字节：
#: 同一套 Git 安装在 PowerShell 里解析到 `Git\cmd\git.exe`、在 Git Bash 里解析到
#: `Git\mingw64\bin\git.exe`，两个前端字节不同、版本相同、行为也相同。按路径记身份的
#: 后果是记录绑在 shell 上——在一个 shell 里跑出记录，在另一个 shell 里 `integrate`
#: 就报「缺少有效测试记录」，而回到工作树跑 `auto` 又说「复用记录」，两句查的是两个键。
#: 探针只用最短的那条：`openssl version -a` 会连 OPENSSLDIR 一起打出来，那又是路径。
TOOL_PROBES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("uv", ("--version",)),
    ("node", ("--version",)),
    ("npm", ("--version",)),
    ("git", ("--version",)),
    ("ffmpeg", ("-version",)),
    ("ffprobe", ("-version",)),
    ("openssl", ("version",)),
)

#: 探到、但不进环境指纹的工具。uv 只负责建环境，装出来的解释器与包已经逐个进了指纹；
#: 把它自己的版本也记进去，winget 每升一次 uv 就让全部测试记录同时失效，而被验证的
#: 那套环境一个字节都没变。它仍然参与探针，启动受限时照样报得出来。
FINGERPRINT_EXCLUDED = ("uv",)


@lru_cache(maxsize=32)
def tool_identity(executable: str, stamp: int, probe: tuple[str, ...]) -> str:
    """工具自报的版本摘要。`stamp` 只用于让缓存跟着文件改动失效。

    非零退出码连着输出一起进摘要：探针不成立的工具彼此仍要能区分开，那本身就是环境差异。
    """
    try:
        done = subprocess.run([executable, *probe], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return "unspawnable"
    return digest([done.returncode, done.stdout.strip(), done.stderr.strip()])


def tool_identities() -> dict[str, str | None]:
    """PATH 上外部工具的可执行身份；存在但启动受限时明确记作 unspawnable。"""
    tools = {}
    for name, probe in TOOL_PROBES:
        executable = shutil.which(name)
        if not executable and name == "openssl" and sys.platform == "win32":
            bundled = Path("C:/Program Files/Git/usr/bin/openssl.exe")
            executable = str(bundled) if bundled.is_file() else None
        if not executable:
            tools[name] = None
            continue
        try:
            stamp = Path(executable).stat().st_mtime_ns
        except OSError:
            stamp = 0
        tools[name] = tool_identity(executable, stamp, probe)
    return tools


def unspawnable_tools() -> tuple[str, ...]:
    """返回能解析到路径、但当前进程权限无法启动的外部工具。"""
    return tuple(name for name, identity in tool_identities().items()
                 if identity == "unspawnable")


def environment(root: Path) -> str:
    tools = {name: identity for name, identity in tool_identities().items()
             if name not in FINGERPRINT_EXCLUDED}
    # 同一个目录可在 sys.path 中出现多次；依赖身份取集合，版本变化仍改变指纹。
    python = interpreter(root)
    identity = python_identity(str(python), python.stat().st_mtime_ns)
    installed = sorted({(d.metadata.get("Name", ""), d.version)
                        for d in importlib.metadata.distributions(path=identity["sites"])
                        if d.metadata.get("Name", "").casefold() != "peach"})
    node_lock = root / "frontend/node_modules/.package-lock.json"
    return digest({
        "schema": 7, "python": identity["version"], "executable": str(python),
        "platform": platform.platform(), "packages": installed, "tools": tools,
        "node_modules": hashlib.sha256(node_lock.read_bytes()).hexdigest() if node_lock.exists() else None,
        "flags": {k: v for k, v in os.environ.items()
                  if k.startswith(("PEACH_", "CI", "GITHUB_", "PYTHON")) and k != "PYTHONPATH"},
    })


def key(root: Path) -> str:
    return digest([snapshot(root), environment(root)])


def inputs(root: Path) -> dict:
    content, dependencies = manifest(root), environment(root)
    return {"manifest": content, "environment": dependencies,
            "state": digest([digest(content), dependencies])}


def recent_records(folder: Path, limit: int = 32) -> list[Path]:
    """最近写入的测试记录。同目录还有持锁说明 `*.lock.holder.json`，名字不是 state 摘要，
    不算记录；记录和说明都会被别的进程随时删掉，列出后才消失的文件跳过。"""
    found = []
    for path in folder.glob("*.json"):
        if not _STATE.fullmatch(path.stem):
            continue
        try:
            found.append((path.stat().st_mtime, path))
        except FileNotFoundError:
            continue
    return [path for _, path in sorted(found, reverse=True)[:limit]]


def baselines(root: Path, current: dict, *, partial: bool = False):
    for path in recent_records(evidence_dir(root)):
        record = read(root, path.stem)
        if not (record.get("passed") or (partial and record.get("coverage"))) \
                or (not partial and "full" not in record["passed"]) \
                or record.get("environment") != current["environment"]:
            continue
        old = record.get("manifest", {})
        new = current["manifest"]
        if not isinstance(old.get("files"), dict):
            continue
        changed = sorted(name for name in old["files"].keys() | new["files"].keys()
                         if old["files"].get(name) != new["files"].get(name))
        version = "src/peach/__init__.py"
        version_only = version in changed and change_impact.version_only(old, new)
        if version_only:
            changed.remove(version)
        yield record, changed, version_only


def read(root: Path, state: str) -> dict:
    try:
        record = json.loads((evidence_dir(root) / f"{state}.json").read_text(encoding="utf-8"))
        if not isinstance(record, dict) or not isinstance(record.get("validated"), dict):
            return {}
        if record.get("state") == state:
            record["passed"] = [scope for scope, stamp in record["validated"].items()
                                if isinstance(stamp, (float, int)) and 0 <= time.time() - stamp < 86400]
            record["coverage"] = {name: stamp for name, stamp in record.get("coverage", {}).items()
                                  if isinstance(stamp, (float, int)) and 0 <= time.time() - stamp < 86400}
            return record
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {}


def covers(record: dict, scopes: tuple[str, ...]) -> bool:
    passed = set(record.get("passed", ()))
    return "full" in passed or set(scopes).issubset(passed)


def write(root: Path, state: str, scopes: tuple[str, ...], *, success: bool,
          elapsed: float, slowest: list, count: int, previous: dict | None = None,
          context: dict | None = None, baseline: dict | None = None,
          cohort: str | None = None, coverage: dict | None = None) -> None:
    folder = evidence_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    prior = previous if previous is not None else read(root, state)
    failed = set(prior.get("failed_scopes", ()))
    if success:
        failed = set() if "full" in scopes else failed - set(scopes)
    else:
        failed.update(scopes)
    validated = dict(prior.get("validated", {}))
    if not success:
        validated = {scope: stamp for scope, stamp in validated.items()
                     if "full" not in scopes and scope != "full" and scope not in scopes}
    if success:
        if baseline:
            validated.update(baseline["validated"])
        validated.update({scope: time.time() for scope in scopes})
    record = {**(context or inputs(root)), "state": state, "validated": validated, "finished": time.time(),
              "cohort": cohort, "coverage": coverage or {},
              "baseline": baseline.get("state") if baseline else None,
              "failed_scopes": sorted(failed),
              "elapsed": elapsed, "count": count, "slowest": slowest}
    temporary = folder / f"{state}.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(folder / f"{state}.json")


def holder_path(lock_path: Path) -> Path:
    return lock_path.with_name(lock_path.name + ".holder.json")


@contextmanager
def held(lock_path: Path, *, wait_seconds: float = 0, **note: object):
    """拿锁，并在旁边留下「谁在持有」的记录，让等锁的人知道在等什么。

    互斥只靠 filelock：锁是操作系统层面的句柄，只能被活着的进程持有，进程退出就释放。
    记录是旁证，不参与互斥。持有者正常退出就删掉它；异常退出留下的旧记录会被下一位
    持有者进门时覆盖，而等锁那一侧只在锁真的被占着时才去读，所以读到的总是当前持有者。
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(lock_path)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        if wait_seconds <= 0:
            raise
        print(f"等待 {lock_path.name}（{describe_holder(lock_path)}），最长 {wait_seconds:g} 秒。",
              file=sys.stderr, flush=True)
        lock.acquire(timeout=wait_seconds)
    try:
        record = holder_path(lock_path)
        record_lock = FileLock(record.with_suffix(".lock"), timeout=5)
        with record_lock:
            record.write_text(json.dumps({
                "pid": os.getpid(),
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                **note,
            }, ensure_ascii=False), encoding="utf-8")
        try:
            yield
        finally:
            with record_lock:
                record.unlink(missing_ok=True)
    finally:
        lock.release()


def describe_holder(lock_path: Path) -> str:
    """等锁时打印用：`pid 8772、01:54:39 起、scope full、root C:\\…`。"""
    try:
        record = holder_path(lock_path)
        with FileLock(record.with_suffix(".lock"), timeout=1):
            note = json.loads(record.read_text(encoding="utf-8"))
    except Timeout:
        return "持有者信息暂未取得"
    except (OSError, ValueError):
        return "持有者刚退出或没留记录"
    parts = [f"pid {note.get('pid')}", f"{note.get('started_at')} 起"]
    parts.extend(f"{key} {note[key]}" for key in ("scope", "branch", "root") if note.get(key))
    return "、".join(parts)


def run_lock(repo: Path, state: str, **note: object):
    return held(evidence_dir(repo) / f"{state}.lock", **note)
