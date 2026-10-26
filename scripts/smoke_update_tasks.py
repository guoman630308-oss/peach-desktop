"""仅用标准库和临时数据验证制品的升级保护、覆盖安装与任务恢复。"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import urllib.request

from smoke_desktop import complete_first_run, smoke_directory, smoke_environment, wait_for, wait_health


def validate_installer_host():
    assert os.environ.get('GITHUB_ACTIONS') == 'true', '安装包验收只允许在干净的 Actions runner 上运行'
    import winreg
    key = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{02D9748A-78F6-4866-AC75-918177194DAB}_is1'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key):
            raise AssertionError('已有 Peach 安装登记，停止验收')
    except FileNotFoundError:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('--old-executable', type=Path)
    parser.add_argument('--installer', type=Path)
    parser.add_argument('--evidence-dir', type=Path, default=Path('smoke-evidence'))
    parser.add_argument('--apply', action='store_true', help='执行临时安装与任务恢复验收')
    args = parser.parse_args()
    if not args.apply:
        print('预演：在临时目录完成首启、模拟运行任务、覆盖安装并核验续跑；加 --apply 执行。')
        return 0
    executable = args.executable.resolve(strict=True)
    if args.installer:
        validate_installer_host()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    with smoke_directory(args.evidence_dir.resolve()) as root:
        application = root / '中文 程序目录/Peach'
        old = (args.old_executable or executable).resolve(strict=True)
        target = application / 'Peach.exe'
        data, media = root / 'data', root / 'media'
        media.mkdir()
        environment = smoke_environment(data)
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        log_path = root / 'runtime.log'
        command = [str(target), 'serve', '--host', '127.0.0.1', '--port', str(port),
                   '--no-mdns', '--no-ledger-sync']
        process = None
        with log_path.open('wb') as log:
            try:
                installer_command = None
                if args.installer:
                    installer_command = [str(args.installer.resolve()), '/VERYSILENT', '/SUPPRESSMSGBOXES',
                        '/NORESTART', f'/DIR={application}']
                    subprocess.run([*installer_command, f'/LOG={root / "initial-install.log"}'],
                                   env=environment, timeout=240, check=True)
                else:
                    shutil.copytree(old.parent, application)
                process = subprocess.Popen([*command, '--setup'], cwd=root, env=environment, stdout=log, stderr=log)
                wait_health(opener, base, process, log_path)
                complete_first_run(opener, base, media, port, 'update-task-smoke-password')
                process.terminate()
                process.wait(timeout=20)
                process = subprocess.Popen(command, cwd=root, env=environment, stdout=log, stderr=log)
                wait_health(opener, base, process, log_path)
                db = data / 'database/ledger.db'
                config_before = (data / 'config.toml').read_bytes()
                program_before = hashlib.sha256(target.read_bytes()).hexdigest()
                with closing(sqlite3.connect(db)) as connection, connection:
                    cursor = connection.execute(
                        "INSERT INTO task_run(task_key,trigger,status,mutex_key,pid,host,started_at,heartbeat_at) "
                        "VALUES('library-processing','manual','running','library-processing',?,?,datetime('now'),datetime('now'))",
                        (process.pid, socket.gethostname()))
                    run_id = cursor.lastrowid
                state = data / 'state/library-processing.json'
                state.write_text(json.dumps({'job_id': 'interrupted-fixture', 'status': 'running',
                    'requested_stage': 'scan', 'last_progress_at': 1}), encoding='utf-8')
                if installer_command:
                    subprocess.run([*installer_command, f'/LOG={root / "install.log"}'],
                                   env=environment, timeout=240, check=True)
                else:
                    error = root / 'preflight-error.txt'
                    helper = subprocess.run([str(executable), '--installer-prepare', str(application),
                        str(os.getpid()), str(error)], env=environment, timeout=150)
                    assert helper.returncode == 0, error.read_text(encoding='utf-8') if error.exists() else helper.returncode
                    assert hashlib.sha256(target.read_bytes()).hexdigest() == program_before
                process.wait(timeout=20)
                with closing(sqlite3.connect(db)) as connection, connection:
                    assert connection.execute('SELECT status FROM task_run WHERE id=?', (run_id,)).fetchone() == ('pending',)
                if not installer_command:
                    shutil.copytree(executable.parent, application, dirs_exist_ok=True)
                    subprocess.run([str(executable), '--installer-finish', str(application), str(os.getpid())],
                                   env=environment, timeout=30, check=True)
                assert (data / 'config.toml').read_bytes() == config_before
                recovery = data / 'state/job-recovery/library-processing.json'
                assert json.loads(recovery.read_text(encoding='utf-8'))['request'] == {'stage': 'scan'}
                process = subprocess.Popen(command, cwd=root, env=environment, stdout=log, stderr=log)
                wait_health(opener, base, process, log_path)
                def recovered():
                    result = json.loads(recovery.read_text(encoding='utf-8'))
                    assert result['status'] not in ('failed', 'blocked'), result
                    return result if result['status'] == 'complete' else None
                result = wait_for(recovered, '未完成任务自动恢复', timeout=60)
                assert result['job_id'] == 'interrupted-fixture'
                with closing(sqlite3.connect(db)) as connection:
                    assert connection.execute(
                        "SELECT COUNT(*) FROM task_run WHERE task_key='library-processing' AND status='succeeded'").fetchone()[0] == 1
                    assert connection.execute('SELECT status FROM task_run WHERE id=?', (run_id,)).fetchone() == ('succeeded',)
                print(json.dumps({'ok': True, 'checks': ['active-task-install-accepted', 'task-paused',
                    'configuration-retained', 'same-task-resumed-once',
                    'installer-overlay' if installer_command else 'zip-overlay']}), flush=True)
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=20)
                # Windows 在 Popen 释放前仍保留已退出进程的内核句柄。
                process = None
                if args.installer and (application / 'unins000.exe').exists():
                    subprocess.run([str(application / 'unins000.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES',
                                    '/NORESTART'], env=environment, timeout=120, check=True)
                    wait_for(lambda: not application.exists(), '卸载临时验收程序', timeout=120)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
