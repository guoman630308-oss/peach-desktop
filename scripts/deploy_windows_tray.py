"""换掉 Windows 生产托盘二进制的入口：构建、验证、原地替换、失败自动回滚。

托盘运行时持有自己的 `dist/Peach/Peach.exe`，所以整条链路只有一个能换文件的窗口——
旧托盘正常退出之后、新托盘启动之前。这个脚本按顺序走完它：干净检出 → 暂存目录里
构建 → 用暂存包自己跑一次打包迁移检查 → 停旧托盘、换二进制、起新托盘（新托盘起不来
就换回备份并重开旧的）→ 走项目 CA 严格校验读生产 HTTPS 口的 `/healthz`。

最后那步的判据看回话的是哪个进程，两条判据都在 `deploy()` 末尾，理由写在那里。

在主检出里运行，用 `.venv` 的 Python：

    .venv\\Scripts\\python.exe scripts\\deploy_windows_tray.py

换生产入口属于要当场授权的动作，脚本本身不代替那次授权。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import httpx

from peach import __version__
from peach.config import LOG_DIR, STATE_DIR
from peach.tray import build_service_specs
from peach.windows_restart import restart_tray
from peach.windows_update import WindowsUpdateInstaller


#: 换过二进制的那次启动比平常慢：单文件包要先把四十多 MB 解到 `%TEMP%` 才画得出托盘
#: 图标，再由它拉起两个服务。这里超时就会把一份好的二进制回滚掉，所以给足。
SWAP_RESTART_TIMEOUT = 90.0


def digest(path: Path) -> str:
    """文件的 sha256。分块读，因为托盘包是八十多 MB，不整份进内存。"""
    digested = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digested.update(block)
    return digested.hexdigest()


def serving_identity(*, timeout: float = 60.0,
                     sleep: Callable[[float], None] = time.sleep) -> dict | None:
    """轮询生产 HTTPS 口的 `/healthz`，返回它报的版本号与构建提交；没回话返回 None。

    地址和 CA 都取自 `build_service_specs()`，跟托盘自己探活用的是同一份规格。
    HTTPS 的结论必须由项目 CA 严格校验得出，明文口的 200 不算数：那条只是跳转进程。
    """
    spec = next(spec for spec in build_service_specs() if spec.name == "https")
    deadline = time.monotonic() + timeout
    while True:
        try:
            response = httpx.get(spec.health_url, timeout=5.0, verify=spec.verify,
                                 trust_env=False)
            if response.status_code == 200:
                payload = response.json()
                return {"version": str(payload.get("version")),
                        "build_commit": payload.get("build_commit")}
        except (httpx.HTTPError, OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            return None
        sleep(1.0)


def _outcome(ok: bool, step: str, message: str, **fields) -> dict:
    return {"ok": ok, "step": step, "message": message, **fields}


def _git(run: Callable[..., subprocess.CompletedProcess], root: Path,
         *args: str) -> subprocess.CompletedProcess:
    return run(["git", *args], cwd=str(root), capture_output=True, text=True, check=False)


def deploy(
    root: Path,
    *,
    target: Path,
    staged: Path | None = None,
    timeout: float = 60.0,
    installer: WindowsUpdateInstaller | None = None,
    restart: Callable[..., object] = restart_tray,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    identity: Callable[..., dict | None] = serving_identity,
) -> dict:
    """把 `root` 这份检出打成新托盘并换上 `target`，返回可直接打印的结果字典。

    每一步失败都在原地停住并说明停在哪：只有替换那一步动了生产入口，它自己带回滚，
    其余步骤失败时旧托盘一直在跑。`staged` 给定时跳过构建，复用已经打好的那份包。
    """
    root = root.resolve()
    target = target.resolve()

    status = _git(run, root, "status", "--porcelain")
    if status.returncode != 0:
        return _outcome(False, "checkout", f"读不到检出状态：{status.stderr.strip()}")
    if status.stdout.strip():
        return _outcome(False, "checkout",
                        "检出不干净：生产二进制只从干净的检出里打，先提交或清理再来。")
    commit = _git(run, root, "rev-parse", "HEAD").stdout.strip()
    branch = _git(run, root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if not commit:
        return _outcome(False, "checkout", "读不到 HEAD 提交号。")
    fields = {"commit": commit, "branch": branch, "target": str(target)}

    installer = installer or WindowsUpdateInstaller(
        root, state_dir=STATE_DIR, log_dir=LOG_DIR, current_executable=target,
    )
    if staged is None:
        built = installer.build_staged_tray(commit)
        if built is None:
            return _outcome(False, "build",
                            f"新托盘构建失败，生产入口未动；日志见 {installer.log_path}。",
                            **fields)
        staged = built
    staged = staged.resolve()
    if not staged.is_file():
        return _outcome(False, "build", f"暂存包不存在：{staged}", **fields)
    fields["staged"] = str(staged)

    if not installer.packaged_migrations_pass(staged):
        return _outcome(False, "validate",
                        f"暂存包没通过打包迁移资源检查，生产入口未动；日志见 {installer.log_path}。",
                        **fields)

    # 指纹只能在这里取：换二进制走的是 `os.replace`，暂存包被搬走，换完就没这个文件了。
    built = digest(staged)
    fields["built_digest"] = built

    result = restart(target, swap_from=staged, timeout=SWAP_RESTART_TIMEOUT)
    fields.update({
        "old_tray_pid": result.old_tray_pid, "new_tray_pid": result.new_tray_pid,
        "service_pids": list(result.service_pids), "backup": result.backup,
    })
    if not result.ok:
        return _outcome(False, "swap", result.message, **fields)

    served = identity(timeout=timeout)
    fields["expected_version"] = __version__
    if served is None:
        return _outcome(False, "verify",
                        "新托盘已就位，但生产 HTTPS 口的 /healthz 没在期限内回话。",
                        **fields)
    fields["version"] = served["version"]
    fields["served_commit"] = served["build_commit"]
    # 两种形态问的不是同一个进程，所以判据也不是同一条，但都不认版本号：版本号一次
    # 发布才推一格，同一个号下有很多个构建，比中了什么也没证明。
    #
    # 独立发行版里回话的就是冻结进程自己，它读得出 `build-info.json`，直接认构建提交。
    # 本机这套形态下回话的是 venv 里的源码进程——托盘按 `_peach_executable()` 的设计
    # 刻意把子服务交回项目 venv，那个进程根本没有第二个版本，`build_commit` 结构上恒为
    # None，拿它当判据的话，换得再对也必然判失败。这时认的是生产入口那个文件的指纹：
    # 它等于这次打出来的那份，而 `restart()` 是在换完之后才从这个文件启动托盘并等到两个
    # 子服务就绪的，所以「跑着的就是刚打的这份」这句话仍然成立。
    if served["build_commit"] is not None:
        if served["build_commit"] != commit:
            return _outcome(False, "verify",
                            f"新托盘已就位，但 /healthz 报的构建是 "
                            f"{served['build_commit']}，不是这次打的 {commit[:8]}。",
                            **fields)
    else:
        landed = digest(target)
        fields["target_digest"] = landed
        if landed != built:
            return _outcome(False, "verify",
                            f"新托盘已就位，但生产入口的指纹是 {landed[:12]}，"
                            f"不是这次打的 {built[:12]}。",
                            **fields)
    return _outcome(True, "verify",
                    f"生产入口已换成 {commit[:8]} 打出的托盘，/healthz 报 {served['version']}。",
                    **fields)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建并换上 Windows 生产托盘二进制")
    parser.add_argument("--target", type=Path,
                        default=PROJECT_ROOT / "dist" / "Peach" / "Peach.exe")
    parser.add_argument("--staged", type=Path, default=None,
                        help="复用这份已经打好的暂存包，跳过构建")
    parser.add_argument("--timeout", type=float, default=60.0,
                        help="等生产 HTTPS 口 /healthz 回话的秒数")
    args = parser.parse_args(argv)
    outcome = deploy(PROJECT_ROOT, target=args.target, staged=args.staged,
                     timeout=max(1.0, args.timeout))
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
