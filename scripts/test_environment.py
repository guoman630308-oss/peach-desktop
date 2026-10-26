"""正式测试入口的依赖准备，只在隔离工作树自动同步环境。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from peach.runtime_prepare import Dependencies, find_uv


def prepare(root: Path, main: Path, *, run=subprocess.run, which=shutil.which,
            environ=None) -> None:
    """准备完成才允许加载测试运行器；主检出依赖只读检查。"""
    root, main = root.resolve(), main.resolve()
    if not (root / "uv.lock").is_file():
        raise RuntimeError(f"未取得测试依赖锁文件：{root / 'uv.lock'}")
    environment = dict(os.environ if environ is None else environ)
    for name in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
        environment.pop(name, None)
    if root == main:
        step = Dependencies(root, run=run, which=which, environ=environment, frozen=False).check()
        if step.state != "current":
            raise RuntimeError(f"主检出依赖未就绪：{step.message}；请在隔离工作树测试，主检出依赖由重启流程同步。")
        print(step.message, flush=True)
        return
    if (root / ".venv").resolve() != root / ".venv":
        raise RuntimeError("工作树 .venv 指向外部目录；不能同步主检出或其他工作树的环境。")
    uv = find_uv(which=which, environ=environment)
    if uv is None:
        raise RuntimeError("未取得 uv：PATH 与 WinGet 安装目录里都没有。")
    print(f"按 uv.lock 同步测试环境：{root / '.venv'}", flush=True)
    result = run([str(uv), "sync", "--locked", "--all-extras", "--python", sys.executable,
                  "--project", str(root)], cwd=str(root), env=environment, check=False)
    if result.returncode:
        raise RuntimeError(f"测试依赖同步失败（退出码 {result.returncode}）；未启动测试。")


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("main_root", type=Path)
    args = parser.parse_args(argv)
    try:
        prepare(args.root, args.main_root)
    except (RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr, flush=True)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
