"""Windows WebView2 窗口；原版托盘持有服务，窗口使用独立配置目录。"""
from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
from urllib.parse import urlsplit
import webbrowser

from filelock import FileLock, Timeout

from .fsutil import atomic_write_text

LOGGER = logging.getLogger(__name__)
ACTIONS = {'open', 'configuration', 'hide', 'quit'}
BOOTSTRAP_PORT_ENV = 'PEACH_WINDOW_BOOTSTRAP_PORT'


def default_data_root() -> Path:
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'PeachDesktop' / 'peach-data'


def defer_download(form, sender, args) -> None:
    from System import Action
    # 模态保存对话框会继续分派消息；选择完成前必须挂起 WebView2 下载。
    deferral = args.GetDeferral()
    args.Handled = True

    def choose():
        try:
            form.browser.on_download_starting(sender, args)
        except Exception:
            args.Cancel = True
            LOGGER.exception('保存下载文件未完成')
        finally:
            deferral.Complete()
    try:
        form.BeginInvoke(Action(choose))
    except Exception:
        args.Cancel = True
        deferral.Complete()
        LOGGER.exception('无法打开保存对话框')


def prepare_proxy_environment() -> None:
    if sys.platform != 'win32' or not os.environ.get('NO_PROXY', '').strip():
        return
    if any(os.environ.get(scheme + '_PROXY') for scheme in ('HTTP', 'HTTPS', 'ALL')):
        return
    from urllib.request import getproxies_registry
    # 只有 NO_PROXY 时 urllib 会跳过注册表；补齐系统代理并保留两处的绕过规则。
    proxies = getproxies_registry()
    for scheme in ('http', 'https', 'all'):
        if proxies.get(scheme):
            os.environ[scheme.upper() + '_PROXY'] = proxies[scheme]
    if proxies.get('no'):
        os.environ['NO_PROXY'] = ','.join((os.environ['NO_PROXY'], proxies['no']))


def prepare_environment() -> None:
    prepare_proxy_environment()
    if not os.environ.get('PEACH_DATA_ROOT', '').strip():
        os.environ['PEACH_DATA_ROOT'] = str(default_data_root())
    if os.environ.get(BOOTSTRAP_PORT_ENV):
        return
    from .settings_file import load_config
    config = load_config()
    if not config.present:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            try:
                reservation.bind(('127.0.0.1', config.server.port))
            except OSError:
                reservation.bind(('127.0.0.1', 0))
            os.environ[BOOTSTRAP_PORT_ENV] = str(reservation.getsockname()[1])


def local_origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.username
            or parsed.password or parsed.port is None or not 1024 <= parsed.port <= 65535):
        raise ValueError('窗口只接受本机 Peach 服务地址')
    return parsed.scheme, parsed.hostname, parsed.port


def request_existing(root: Path, action: str, *, timeout: float = 3) -> bool:
    if action not in ACTIONS:
        raise ValueError('窗口指令无效')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            record = json.loads((root / 'state/webview-window.json').read_text(encoding='utf-8'))
            port, token = record['port'], record['token']
            if type(port) is not int or not 1024 <= port <= 65535 or not isinstance(token, str) or len(token) != 64:
                return False
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.connect(('127.0.0.1', port))
                client.settimeout(.5)
                client.send(json.dumps({'token': token, 'action': action}).encode())
                if client.recv(32) == b'ok':
                    return True
        except (OSError, ValueError, KeyError, TypeError):
            time.sleep(.1)
    return False


class NativeShell:
    def __init__(self, window, root: Path):
        self.window, self.root = window, root
        self.tray = None
        self.origin = None
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.configured = False
        self.exit_code = 1
        self.control = None
        self.tray_thread = None

    def open(self, url: str) -> None:
        if self.stopping.is_set():
            return
        self.origin = local_origin(url)
        if self.window.get_current_url() != url:
            self.window.load_url(url)
        self.window.restore()
        self.window.show()

    def close_window(self):
        if self.stopping.is_set():
            return True
        threading.Thread(target=self.hide, name='PeachWindowHide', daemon=True).start()
        return False

    def hide(self):
        try:
            self.window.evaluate_js('document.querySelectorAll("video,audio").forEach(media => media.pause())')
        finally:
            self.window.hide()

    def allows_url(self, url: str) -> bool:
        try:
            candidate = local_origin(url)
            if candidate == self.origin:
                return True
            if self.tray is not None:
                from .settings_file import load_config
                config = load_config()
                if config.present and candidate == ('http', '127.0.0.1', config.server.port):
                    self.origin = candidate
                    return True
        except ValueError:
            pass
        return False

    def tray_ready(self, tray):
        if self.stopping.is_set():
            tray.exit(reason='window-exit')
            raise SystemExit(0)
        self.tray = tray
        self.ready.set()

    def command(self, action: str):
        if action not in ACTIONS:
            raise ValueError('窗口指令无效')
        if action == 'quit':
            self.stopping.set()
            if self.tray is not None:
                self.tray.exit()
        elif action == 'hide':
            self.hide()
        elif (self.ready.wait(30) and not self.stopping.is_set()
              and self.tray is not None and self.tray.manager.wait_until_ready()):
            self.tray.gate.open('configuration' if action == 'configuration' else '')

    def start_control(self):
        self.control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.control.bind(('127.0.0.1', 0))
        self.control.settimeout(.5)
        token = secrets.token_hex(32)
        atomic_write_text(self.root / 'state/webview-window.json', json.dumps({
            'port': self.control.getsockname()[1], 'pid': os.getpid(), 'token': token}), mode=0o600)

        def listen():
            while not self.stopping.is_set():
                try:
                    body, peer = self.control.recvfrom(1024)
                    value = json.loads(body)
                    if (peer[0] != '127.0.0.1' or not isinstance(value, dict)
                            or not isinstance(value.get('token'), str)
                            or not hmac.compare_digest(value['token'], token)
                            or value.get('action') not in ACTIONS):
                        continue
                    self.control.sendto(b'ok', peer)
                    self.command(value['action'])
                except socket.timeout:
                    continue
                except Exception:
                    if not self.stopping.is_set():
                        LOGGER.warning('窗口控制请求未完成')
        threading.Thread(target=listen, name='PeachWindowControl', daemon=True).start()

    def configure_window(self):
        if self.configured:
            return
        from System import Action

        def attach():
            if self.configured:
                return
            form = self.window.native
            core = form.webview.CoreWebView2

            def navigate(_sender, args):
                url = str(args.Uri)
                if self.allows_url(url):
                    return
                args.Cancel = True
                if urlsplit(url).scheme in {'http', 'https'}:
                    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

            def popup(_sender, args):
                args.Handled = True
                url = str(args.Uri)
                if self.allows_url(url):
                    threading.Thread(target=self.open, args=(url,), daemon=True).start()
                elif urlsplit(url).scheme in {'http', 'https'}:
                    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

            def fullscreen(_sender, _args):
                if bool(core.ContainsFullScreenElement) != bool(form.is_fullscreen):
                    form.toggle_fullscreen()

            core.NavigationStarting += navigate
            core.NewWindowRequested -= form.browser.on_new_window_request
            core.NewWindowRequested += popup
            core.DownloadStarting -= form.browser.on_download_starting
            core.DownloadStarting += lambda sender, args: defer_download(form, sender, args)
            core.ContainsFullScreenElementChanged += fullscreen
            self.configured = True
        self.window.native.Invoke(Action(attach))

    def run_tray(self, arguments):
        from .tray import main
        try:
            self.exit_code = main(arguments, open_browser=self.open, ready=self.tray_ready, detach=False)
        finally:
            self.stopping.set()
            self.ready.set()
            self.window.destroy()

    def start(self, arguments):
        self.start_control()
        self.tray_thread = threading.Thread(target=self.run_tray, args=(arguments,), name='PeachTray')
        self.tray_thread.start()

    def close(self):
        self.stopping.set()
        if self.control is not None:
            self.control.close()
        if self.tray_thread is not None and self.tray_thread.is_alive():
            if self.tray is not None:
                self.tray.exit()
            self.tray_thread.join(30)
        (self.root / 'state/webview-window.json').unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Peach 独立窗口')
    parser.add_argument('--data-root', type=Path)
    parser.add_argument('--silent', action='store_true')
    parser.add_argument('--show', action='store_true')
    parser.add_argument('--quit', action='store_true')
    args = parser.parse_args(argv)
    if sys.platform != 'win32':
        raise RuntimeError('独立窗口测试包仅支持 Windows')
    root = (args.data_root or Path(os.environ.get('PEACH_DATA_ROOT') or default_data_root())).resolve()
    os.environ['PEACH_DATA_ROOT'] = str(root)
    prepare_environment()
    state_root = Path(os.environ.setdefault('PEACH_WINDOW_STATE_ROOT', str(root))).resolve()
    if args.quit:
        if not (state_root / 'state/webview-window.lock').exists():
            return 0
        request_existing(state_root, 'quit')
        try:
            with FileLock(state_root / 'state/webview-window.lock', timeout=45):
                return 0
        except Timeout:
            return 1
    from .tray_detached import handoff_tray
    from .settings_file import PROJECT_ROOT
    arguments = (['--silent'] if args.silent else []) + (['--show'] if args.show else [])
    if handoff_tray(arguments, PROJECT_ROOT, module='peach.native_shell'):
        return 0
    (state_root / 'state').mkdir(parents=True, exist_ok=True)
    lock = FileLock(state_root / 'state/webview-window.lock')
    try:
        lock.acquire(timeout=0)
    except Timeout:
        if request_existing(state_root, 'open'):
            return 0
        raise RuntimeError('已有独立窗口正在启动，请稍后重试')
    shell = None
    try:
        import webview
        debug_port = os.environ.get('PEACH_WEBVIEW_DEBUG_PORT', '')
        if debug_port:
            if not debug_port.isdecimal() or not 1024 <= int(debug_port) <= 65535:
                raise ValueError('WebView2 调试端口无效')
            webview.settings['REMOTE_DEBUGGING_PORT'] = int(debug_port)
        webview.settings['ALLOW_DOWNLOADS'] = True
        webview.settings['ALLOW_FILE_URLS'] = False
        webview.settings['IGNORE_SSL_ERRORS'] = False
        window = webview.create_window('Peach · 蜜桃', html='<html lang="zh-CN"><body style="background:#111;color:#eee;font:18px sans-serif;padding:40px">正在启动 Peach…</body></html>',
                                       width=1280, height=860, min_size=(800, 600), hidden=args.silent,
                                       background_color='#111111')
        shell = NativeShell(window, state_root)
        window.events.closing += shell.close_window
        window.events.loaded += shell.configure_window
        webview.start(lambda: shell.start(arguments), gui='edgechromium', private_mode=False,
                      storage_path=str(state_root / 'state/webview2'))
        return shell.exit_code
    finally:
        if shell is not None:
            shell.close()
        lock.release()


if __name__ == '__main__':
    raise SystemExit(main())
