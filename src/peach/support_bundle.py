"""无需服务或第三方依赖的脱敏诊断包。"""
from __future__ import annotations

import io
import argparse
import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import error_reporting

LOG_NAMES = ("tray.log", "tray-lifecycle.log", "tray-setup.err.log", "tray-setup.out.log",
             "tray-http.err.log", "tray-http.out.log", "tray-https.err.log", "tray-https.out.log",
             'tray-restart.out.log', 'tray-scan.out.log', 'windows-source-sync.log', 'cloudflare-tunnel.log')
STATE_NAMES = ('library-processing.json', 'runtime-prepare.json', 'standalone-update.json',
               'automatic-updates.json', 'windows-source-sync.json', 'cloudflare-tunnel.json')
TAIL_BYTES = 96 * 1024


def _processing_log(text: str) -> str:
    """处理证据保留资产编号和错误，媒体名称留在本机。"""
    def hide_names(value):
        if isinstance(value, dict):
            return {key: '<media>' if key in ('title', 'current_asset_name') else hide_names(item)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [hide_names(item) for item in value]
        return value

    try:
        values = [json.loads(text)]
    except ValueError:
        values = []
        for line in text.splitlines():
            try:
                values.append(json.loads(line))
            except ValueError:
                continue
    lines = [json.dumps(error_reporting.scrub(hide_names(value)), ensure_ascii=False)
             for value in values if isinstance(value, (dict, list))]
    return '\n'.join(lines) + ('\n' if lines else '')


def redact_log(text: str) -> str:
    """JSON 行按字段脱敏，保留异常链；普通文本按行处理。"""
    lines = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            lines.append(error_reporting.redact(line))
        else:
            lines.append(json.dumps(error_reporting.scrub(value), ensure_ascii=False))
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _recent(directory: Path, pattern: str, limit: int, entries: list) -> list[Path]:
    found = []
    for path in directory.glob(pattern):
        try:
            found.append((path.stat().st_mtime, path))
        except OSError as error:
            entries.append({'name': path.name, 'status': type(error).__name__})
    return [path for _, path in sorted(found, reverse=True)[:limit]]


def _log_candidates(directory: Path, entries: list) -> list[Path]:
    candidates = [directory / name for name in LOG_NAMES]
    for name in LOG_NAMES:
        candidates += _recent(directory, Path(name).stem + '.until-*.log', 2, entries)
    return candidates + _recent(directory, 'errors-*.log', 8, entries) + _recent(directory, 'crash-*.log', 4, entries)


def _archive_files(archive, directory, candidates, prefix, entries, *, structured=False):
    for path in dict.fromkeys(candidates):
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
                entries.append({'name': path.name, 'status': 'skipped_link'})
                continue
            if not path.is_file():
                continue
            with path.open('rb') as stream:
                size = stream.seek(0, os.SEEK_END)
                stream.seek(max(0, size - TAIL_BYTES))
                raw = stream.read(TAIL_BYTES)
            text = raw.decode('utf-8', errors='replace')
            if size > TAIL_BYTES:
                text = text.partition('\n')[2]
            content = _processing_log(text) if structured else redact_log(text)
            name = path.relative_to(directory).as_posix()
            archive.writestr(f'{prefix}/{name}', content)
            entries.append({'name': name, 'section': prefix, 'truncated': size > TAIL_BYTES,
                            'status': 'included' if content or not raw else 'unreadable_json'})
        except OSError as error:
            entries.append({'name': path.name, 'status': type(error).__name__})


def _archive_state(archive, directory, entries):
    candidates = [directory / name for name in STATE_NAMES]
    candidates += _recent(directory, 'library-processing-*.issues.jsonl', 8, entries)
    _archive_files(archive, directory, candidates, 'processing', entries, structured=True)
    migrations = _recent(directory, 'update-backups/*/migration.log', 2, entries)
    _archive_files(archive, directory, migrations, 'updates', entries)


def bundle_bytes(*, report: dict | None = None, config=None) -> bytes:
    facts = {"created_at": datetime.now(timezone.utc).isoformat(), "system": error_reporting.system_facts()}
    directories = error_reporting.log_directories()
    processing_directory = None
    try:
        from . import settings_file
        config = config or settings_file.load_config()
        facts["configuration"] = {"status": "available", "present": config.present,
                                  "port": config.server.port}
        directories.insert(0, config.directory("logs"))
        processing_directory = config.directory('state')
        from .ffmpeg import FFmpegResolver, binary_diagnostics
        resolver = FFmpegResolver(config.directory('tools') / 'ffmpeg')
        facts['media_tools'] = {'ffmpeg': binary_diagnostics(resolver.ffmpeg(), decoders=True),
                                'ffprobe': binary_diagnostics(resolver.ffprobe())}
    except Exception as error:
        facts["configuration"] = {"status": "unavailable", **error_reporting.exception_facts(error)}
        try:
            root, _ = settings_file.discover_data_root()
            processing_directory = root / 'state'
        except Exception:
            pass
    if report:
        # 报告分享仅保留系统检查，库清单、媒体挂载明细和来源原文留在本机页面。
        facts["checks"] = {key: {field: value.get(field) for field in ("status", "reason", "action")}
                           for key, value in report.get("checks", {}).items()
                           if key not in ("media_mounts",)}
        source_fields = ('source', 'status', 'reason', 'last_check_at', 'elapsed_s',
                         'last_success_at', 'cooldown_until')
        facts['sources'] = [{key: row.get(key) for key in source_fields}
                            for row in report.get('sources', []) if isinstance(row, dict)]
    output = io.BytesIO()
    entries = []
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, directory in enumerate(dict.fromkeys(directories)):
            try:
                candidates = _log_candidates(directory, entries)
                _archive_files(archive, directory, candidates, f'logs/{index}', entries)
            except OSError as error:
                entries.append({'name': '<logs>', 'status': type(error).__name__})
        if processing_directory is not None:
            try:
                _archive_state(archive, processing_directory, entries)
            except OSError as error:
                entries.append({'name': '<state>', 'status': type(error).__name__})
        facts["logs"] = entries
        archive.writestr("report.json", json.dumps(error_reporting.scrub(facts), ensure_ascii=False, indent=2))
    return output.getvalue()


def save_bundle(destination: Path | None = None) -> Path:
    payload = bundle_bytes()
    if destination is not None:
        with Path(destination).open("xb") as stream:
            stream.write(payload)
        return Path(destination)
    name = datetime.now(timezone.utc).strftime("peach-support-%Y%m%d-%H%M%S-%f.zip")
    for directory in error_reporting.log_directories():
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / name
            with target.open("xb") as stream:
                stream.write(payload)
            return target
        except OSError:
            continue
    raise OSError("诊断报告无法写入数据目录或临时目录")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="保存本机脱敏诊断包，无需启动 Peach 服务")
    parser.add_argument("destination", nargs="?", type=Path)
    args = parser.parse_args(argv)
    print(save_bundle(args.destination))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
