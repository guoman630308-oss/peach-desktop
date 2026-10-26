from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BinaryChoice:
    path: Path
    source: str


def binary_diagnostics(choice: BinaryChoice | None, *, decoders=False) -> dict:
    """检查实际工具版本与 AV1 软件解码器；路径存在和可执行分别报告。"""
    facts = {'available': choice is not None, 'source': choice.source if choice else None,
             'path': str(choice.path) if choice else None, 'usable': False}
    if choice is None:
        return facts
    try:
        for option in ('-version', '-decoders') if decoders else ('-version',):
            result = subprocess.run([str(choice.path), '-hide_banner', option], capture_output=True,
                                    timeout=5, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if result.returncode:
                facts.update(returncode=result.returncode, error=(result.stderr or b'').decode('utf-8', 'replace')[-2000:])
                return facts
            output = result.stdout.decode('utf-8', 'replace')
            if option == '-version':
                facts['version'] = output.splitlines()[0][:300] if output.splitlines() else ''
            else:
                names = {parts[1] for line in output.splitlines()
                         if len(parts := line.split()) >= 2 and parts[0].startswith('V')}
                facts['av1_software'] = bool(names & {'libdav1d', 'libaom-av1'})
        facts['usable'] = bool(facts.get('version'))
    except (OSError, subprocess.TimeoutExpired) as error:
        facts.update(error=type(error).__name__, errno=getattr(error, 'errno', None),
                     winerror=getattr(error, 'winerror', None), timeout_seconds=5)
    return facts


class FFmpegResolver:
    """按显式配置、随包工具、数据目录与 PATH 查找媒体工具。"""

    def __init__(self, managed_root: Path):
        self.managed_root = managed_root

    def ffmpeg(self) -> BinaryChoice | None:
        return self._resolve("ffmpeg", "PEACH_FFMPEG")

    def ffprobe(self) -> BinaryChoice | None:
        return self._resolve("ffprobe", "PEACH_FFPROBE")

    def _resolve(self, executable: str, env_name: str) -> BinaryChoice | None:
        suffix = ".exe" if os.name == "nt" else ""
        configured = os.environ.get(env_name)
        if configured:
            path = Path(configured).expanduser()
            if path.is_file():
                return BinaryChoice(path.resolve(), "environment")

        if getattr(sys, "frozen", False):
            bundled = Path(sys.executable).parent / "tools" / "ffmpeg" / "bin" / f"{executable}{suffix}"
            if bundled.is_file():
                return BinaryChoice(bundled.resolve(), "peach-bundled")

        managed = self.managed_root / "bin" / f"{executable}{suffix}"
        if managed.is_file():
            return BinaryChoice(managed.resolve(), "peach-managed")

        found = shutil.which(executable)
        if found:
            return BinaryChoice(Path(found).resolve(), "path")

        return None
