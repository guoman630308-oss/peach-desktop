"""本机启动、卸载范围与公共代理的隔离回归。"""
import json
import base64
import ctypes
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from peach import (desktop_startup, desktop_uninstall, entry_links, peach_proxy,
                   scraping_access, settings_file)


class DesktopSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.data = self.root / 'data'
        self.data.mkdir()
        self.program = self.root / 'Peach'
        (self.program / '_internal').mkdir(parents=True)
        (self.program / '_internal/standalone.txt').touch()
        (self.program / 'Peach.exe').touch()
        self.config = SimpleNamespace(data_root=self.data, path=self.data / 'config.toml', locations={'local': (str(self.root / 'media'),)}, mounts={},
                                      directory=lambda key: self.data / key)
        # 读到的快捷方式状态是模块级的，跨用例留着会让「起了几个进程」数不准。
        desktop_startup._READ_CACHE.clear()
        self.addCleanup(desktop_startup._READ_CACHE.clear)

    def test_shortcut_timeout_has_an_actionable_error_and_original_cause(self):
        failure = subprocess.TimeoutExpired(['powershell.exe'], 60)
        with patch('peach.desktop_startup.subprocess.run', side_effect=failure):
            with self.assertRaisesRegex(OSError, '快捷方式操作超时.*60 秒') as raised:
                desktop_startup.shortcut('write', self.root / 'Peach.lnk',
                                         target=str(self.program / 'Peach.exe'))
        self.assertIs(raised.exception.__cause__, failure)

    def test_uninstall_plan_keeps_media_and_can_preserve_all_data(self):
        keep = desktop_uninstall.plan(self.config, delete_data=False, program=self.program)
        self.assertEqual(keep['directories'], [])
        generated_backup = self.data / 'config.toml.before-access-fix-20260906-135757'
        generated_backup.write_text('fixture')
        manual_backup = self.data / 'config.toml.bak'
        manual_backup.write_text('fixture')
        unrelated = self.data / 'personal.txt'
        unrelated.write_text('preserve')
        full = desktop_uninstall.plan(self.config, delete_data=True, program=self.program)
        self.assertEqual(len(full['directories']), len(settings_file.DIRECTORY_KEYS))
        self.assertNotIn(str(self.root / 'media'), full['directories'])
        self.assertIn(str(generated_backup), full['files'])
        self.assertNotIn(str(manual_backup), full['files'],
                         'config.toml.bak 是用户按运维文档自己复制的回退副本')
        self.assertNotIn(str(unrelated), full['files'])

    def test_uninstall_refuses_media_overlap_external_storage_and_source_tree(self):
        self.config.locations = {'local': (str(self.data / 'sources/media'),)}
        with self.assertRaisesRegex(ValueError, '媒体'):
            desktop_uninstall.plan(self.config, delete_data=True, program=self.program)
        self.config.locations = {}
        self.config.directory = lambda key: self.root / key
        with self.assertRaisesRegex(ValueError, '外部'):
            desktop_uninstall.plan(self.config, delete_data=True, program=self.program)
        (self.program / '.git').mkdir()
        with self.assertRaisesRegex(ValueError, '程序目录'):
            desktop_uninstall.plan(self.config, delete_data=False, program=self.program)

    def test_proxy_private_address_is_shared_but_direct_source_bypasses_it(self):
        peach_proxy.save(self.root, {'mode': 'proxy', 'proxy': 'http://user:password@127.0.0.1:7890'})
        self.assertNotIn('password', json.dumps(peach_proxy.describe(self.root)))
        peach_proxy.save(self.root, {'mode': 'proxy', 'proxy': ''})
        with patch('peach.scraping_access.httpx.Client') as factory:
            scraping_access.client_for(self.root, 'dmm')
            self.assertIn('proxy', factory.call_args.kwargs)
            scraping_access.save(self.root, 'dmm', {'network': 'direct'})
            scraping_access.client_for(self.root, 'dmm')
            self.assertNotIn('proxy', factory.call_args.kwargs)
            self.assertFalse(factory.call_args.kwargs['trust_env'])

    def test_legacy_proxy_conflict_requires_selection_without_writing_secrets(self):
        from peach.follow_secrets import CredentialStore
        store = CredentialStore(self.root)
        for source, port in [('dmm',7890),('mgstage',7891)]:
            path = store.path_for('scraping-' + source)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({'network':'proxy','proxy':f'http://127.0.0.1:{port}'}))
        self.assertTrue(peach_proxy.describe(self.root)['needs_selection'])
        with self.assertRaisesRegex(ValueError, '来源代理'):
            peach_proxy.client_options(self.root)
        peach_proxy.save(self.root, {'mode':'direct'})
        self.assertEqual(peach_proxy.client_options(self.root), {'trust_env':False})

    def test_startup_preserves_explicit_data_root_and_validates_booleans(self):
        program, args, _ = desktop_startup.target(self.config, executable=self.program / 'Peach.exe')
        self.assertEqual(args, ['--data-root', str(self.data)])
        self.assertEqual(program.parent, self.program)
        with self.assertRaises(ValueError):
            desktop_startup.save(self.config, enabled='false', silent=True, desktop=False)
        # 三个开关一视同仁。`desktop` 缺省会静默删掉桌面图标，所以它不给默认值，
        # 路由漏传就在这里变成 400，而不是替用户做决定。
        with self.assertRaises(ValueError):
            desktop_startup.save(self.config, enabled=False, silent=True, desktop=None)

    def test_configuration_actions_require_local_origin_and_uninstall_confirmation(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from peach import routes_configuration
        from peach.routes_auth import require_auth
        app = FastAPI()
        app.include_router(routes_configuration.router)
        app.dependency_overrides[require_auth] = lambda: None
        app.state.web_contract = SimpleNamespace()
        with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1',123)) as client, \
                patch.object(routes_configuration, 'managed_configuration', return_value=False), \
                patch.object(settings_file, 'load_config', return_value=self.config), \
                patch.object(desktop_uninstall, 'request', return_value={'accepted':True}) as remove:
            self.assertEqual(client.post('/api/configuration/uninstall',json={'delete_data':True}).status_code,400)
            self.assertEqual(client.post('/api/configuration/uninstall',json={'delete_data':True,'confirmation':'卸载 Peach'},
                                         headers={'origin':'https://other.invalid'}).status_code,403)
            remove.assert_not_called()
            self.assertEqual(client.post('/api/configuration/uninstall',json={'delete_data':False,'confirmation':'卸载 Peach'}).status_code,200)
            remove.assert_called_once_with(self.config,False)
            self.assertEqual(client.post('/api/configuration/startup',json={'enabled':'false','silent':True}).status_code,400)
            self.assertEqual(client.post('/api/configuration/peach-proxy',json={'mode':'proxy','proxy':'invalid'}).status_code,400)
            # 外部入口：域名不成形时整批不写，落下去的会是一排点过去必然落空的入口。
            sites = {site.key: {'host': site.host}
                     for site in entry_links.SITES if site.mirrored}
            self.assertEqual(client.post('/api/configuration/entry-links',json={'sites':sites}).status_code,200)
            broken = {key: dict(row) for key, row in sites.items()}
            broken['javdb']['host'] = 'javdb.com/actors'
            self.assertEqual(client.post('/api/configuration/entry-links',json={'sites':broken}).status_code,400)
            self.assertEqual(entry_links.read(self.config.directory('state'))['javdb']['host'],
                             'javdb.com')

    def test_startup_does_not_overwrite_another_installation(self):
        with patch.object(desktop_startup, 'startup_directory', return_value=self.root), patch.object(desktop_startup, 'shortcut', side_effect=[
            {'enabled':False}, {'enabled':True,'target':str(self.root / 'Other.exe')}]), self.assertRaisesRegex(ValueError, '另一份'):
            desktop_startup.windows_entry(self.config, self.program / 'Peach.exe')

    def test_a_shortcut_aimed_at_the_packaged_entry_still_counts_as_this_installation(self):
        """`dist/Peach/Peach.exe` 和 `pythonw.exe -c "…peach.tray…"` 是同一份 Peach 的两个入口。

        源码安装里 `target()` 只给出后者。按字面比目标路径，配置页会把自己先前放在桌面上
        的图标判成「另一份 Peach 安装」并锁住那颗开关，用户连关掉它都不行；本机的桌面图标
        就是这么被锁住的（0.29.0 上线后实测）。所以源码安装的归属判到整个项目目录。
        """
        program = Path(sys.executable).with_name('pythonw.exe')
        packaged = (settings_file.PROJECT_ROOT / 'dist/Peach/Peach.exe').resolve()
        with patch.object(desktop_startup.distribution, 'standalone', return_value=False):
            self.assertTrue(desktop_startup.owned_target(packaged, program))
            self.assertFalse(desktop_startup.owned_target((self.program / 'Peach.exe').resolve(), program))
        # 独立发行版没有项目目录这层放宽：那份包只认自己那个 exe。
        with patch.object(desktop_startup.distribution, 'standalone', return_value=True):
            self.assertFalse(desktop_startup.owned_target(packaged, program))

    @unittest.skipUnless(os.name == 'nt', 'Windows known folder')
    def test_desktop_directory_comes_from_the_known_folder_not_the_home_path(self):
        """桌面目录问 shell 要，不拼 `%USERPROFILE%\\Desktop`。

        开了 OneDrive 备份的 Windows 11 把桌面重定向到 `%USERPROFILE%\\OneDrive\\Desktop`，
        拼出来那个目录用户根本看不到。判据取 shell 自己的答案：`SHGetKnownFolderPath`
        一旦失败，函数会静默退回拼路径，只断言「返回了个存在的目录」是看不出来的。
        """
        shell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        result = subprocess.run(
            [str(shell), '-NoProfile', '-NonInteractive', '-Command',
             "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);[Environment]::GetFolderPath('Desktop')"],
            capture_output=True, text=True, encoding='utf-8', timeout=25,
            creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        self.assertEqual(desktop_startup.desktop_directory(), Path(result.stdout.strip()))

    @unittest.skipUnless(os.name == 'nt', 'Windows 原生快捷方式')
    def test_desktop_shortcut_has_its_own_switch_and_yields_to_another_installation(self):
        """桌面图标由第三个开关单独管，参数固定 `--show`，遇到别人的图标不覆盖。

        `--show` 跟「静默启动」无关：静默只管开机那一次，桌面图标是用户特意去点的。托盘
        已经在跑时 `tray.main()` 拿不到单实例锁，`--show` 那一支正好打开网页。

        桌面上摆着另一份安装的 `Peach.lnk` 时，要求开启照实拒绝，要求关闭则跳过它——那个
        图标本来就不是这份安装放的，不能因为它挡在那儿连「开机自启」都存不下。
        """
        startup = self.root / 'startup'
        startup.mkdir()
        program = self.program / 'Peach.exe'
        icon = self.root / 'Peach.lnk'
        with patch.object(desktop_startup, 'desktop_directory', return_value=self.root), \
                patch.object(desktop_startup, 'startup_directory', return_value=startup), \
                patch.object(desktop_startup, 'target', return_value=(program, ['--data-root', str(self.data)], self.program)):
            state = desktop_startup.save(self.config, enabled=False, silent=True, desktop=True)
            self.assertTrue(state['desktop'])
            self.assertTrue(icon.is_file(), sorted(item.name for item in self.root.iterdir()))
            self.assertIn('--show', desktop_startup.shortcut('read', icon)['arguments'])
            self.assertEqual(sorted(item.name for item in startup.iterdir()), [])
            self.assertTrue(desktop_startup.desktop_snapshot(self.config)['desktop'])

            other = self.program / 'Other.exe'
            other.touch()
            desktop_startup.shortcut('write', icon, target=str(other), directory=str(self.program), expected=str(program))
            with self.assertRaisesRegex(ValueError, '另一份'):
                desktop_startup.save(self.config, enabled=False, silent=True, desktop=True)
            self.assertEqual(desktop_startup.desktop_snapshot(self.config),
                             {'desktop': False, 'desktop_message': '桌面快捷方式属于另一份 Peach 安装'})
            desktop_startup.save(self.config, enabled=True, silent=True, desktop=False)
            self.assertEqual(len(list(startup.iterdir())), 1)
            self.assertTrue(icon.is_file())

    def _shortcut_round_trip(self, name):
        path = self.root / name
        program = str(self.program / 'Peach.exe')
        written = desktop_startup.shortcut('write', path, target=program, arguments='--silent', directory=str(self.program))
        # 写完立刻核对文件就在这个路径上。只在 remove 之后断言不存在的话，`Save()` 把
        # 快捷方式落到别处或压根没落地都照样通过，runner 上红的正是这一段。
        self.assertTrue(written['enabled'])
        self.assertTrue(path.is_file(), sorted(item.name for item in self.root.iterdir()))
        result = desktop_startup.shortcut('read', path)
        self.assertEqual(Path(result['target']).resolve(), Path(program))
        self.assertEqual(result['arguments'], '--silent')
        desktop_startup.shortcut('remove', path, expected=result['target'])
        self.assertFalse(path.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows 原生快捷方式')
    def test_native_shortcut_round_trip_in_temporary_directory(self):
        self._shortcut_round_trip('startup fixture.lnk')

    @unittest.skipUnless(os.name == 'nt', 'Windows 原生快捷方式')
    def test_native_shortcut_round_trip_survives_a_non_ascii_name(self):
        """中文名的往返，判据按系统 ANSI 码页给，不按平台给。

        `WScript.Shell` 的 `Save()` 把路径降级到 `GetACP()`，编不出来的字符按字一个换成
        `?`，而 `?` 是 Windows 的非法文件名字符，于是报 `Unable to save shortcut
        "...\\?? fixture.lnk"`。本机 ACP 是 936，中文编得出来；GitHub 的 windows-latest
        是西欧码页，同一个名字在那儿必失败。本机用泰文和希伯来文复现过同一条错误消息
        （2026-09-07），确认是码页而不是权限、COM 或 stdin 解码。

        生产的启动项文件名是 `Peach-<12 位十六进制>.lnk`，只有用户目录段可能带非 ASCII；
        中文用户名的机器 ACP 就是 936，编得出来。这里不为 WSH 的这条限制换实现。
        """
        name = '启动 fixture.lnk'
        codepage = ctypes.windll.kernel32.GetACP()
        try:
            name.encode(f'cp{codepage}')
        except (UnicodeEncodeError, LookupError):
            self.skipTest(f'系统 ANSI 码页 cp{codepage} 表示不了「{name}」，WScript.Shell 存不下')
        self._shortcut_round_trip(name)

    def test_the_startup_entry_state_is_warmed_up_before_anyone_opens_settings(self):
        """服务起来时后台先问一遍启动项，别把那 2.5 秒留给点开设置的那一下。

        条件跟 `configurable` 的前两条对齐：不是托盘管的、或还没过首次配置，这一格本来
        就出不来，没有值得预热的东西，起一个 `powershell.exe` 只是白耗。预热失败也不许
        影响服务起不起得来——配置页自己现问一遍就是了。
        """
        api = (Path(__file__).resolve().parents[1] / 'src/peach/api.py').read_text(encoding='utf-8')
        self.assertIn('if not (managed_configuration() and settings.configured):\n            return', api)
        self.assertIn('await asyncio.to_thread(desktop_startup.snapshot)', api)
        self.assertIn('warmup = asyncio.create_task(warm_startup_entries())', api)
        self.assertIn('warmup.cancel()', api)
        # 起服务这一步不等它：预热要是同步跑，省下的那 2.5 秒只是挪到了开机时。
        self.assertNotIn('await warm_startup_entries()', api)

    @unittest.skipUnless(os.name == 'nt', 'Windows 原生快捷方式')
    def test_reading_shortcuts_spends_a_powershell_process_only_when_it_has_to(self):
        """读 `.lnk` 是配置页首屏唯一一件慢事，两道近路都要成立。

        起一个 `powershell.exe` 要 0.8–1.7 秒，配置页那一格最多读三个快捷方式，加起来
        2.5 秒，正好挡在用户点开设置之后。所以文件不在就不起进程（脚本对这一支本来也
        只回一句「没有」），真存在的按 `(mtime, size)` 记住结论。

        指纹一变必须重读：快捷方式被改写之后还报旧的目标，配置页上那个开关就会一直
        显示成用户没设过的那一档。
        """
        program = str(self.program / 'Peach.exe')
        path = self.root / 'cached fixture.lnk'
        calls = []
        real = subprocess.run

        def counted(*args, **kwargs):
            calls.append(args[0])
            return real(*args, **kwargs)

        with patch.object(desktop_startup.subprocess, 'run', counted):
            missing = desktop_startup.shortcut('read', self.root / '从来没有过.lnk')
            self.assertEqual(missing, {'ok': True, 'enabled': False, 'target': '', 'arguments': ''})
            self.assertEqual(calls, [])
            desktop_startup.shortcut('write', path, target=program, arguments='--silent',
                                     directory=str(self.program))
            written = len(calls)
            first = desktop_startup.shortcut('read', path)
            self.assertEqual(len(calls), written + 1)
            self.assertEqual(desktop_startup.shortcut('read', path), first)
            self.assertEqual(len(calls), written + 1, '同一个快捷方式读第二遍不该再起一个进程')
            other = self.program / 'Other.exe'
            other.touch()
            desktop_startup.shortcut('write', path, target=str(other), arguments='--show',
                                     directory=str(self.program), expected=first['target'])
            again = desktop_startup.shortcut('read', path)
        self.assertEqual(Path(again['target']).resolve(), other.resolve())
        self.assertEqual(again['arguments'], '--show')

    @unittest.skipUnless(os.name == 'nt', 'Windows 原生快捷方式')
    def test_a_failed_shortcut_call_reports_what_powershell_said(self):
        """失败的消息带上脚本自报的原因、异常类型和行号，不只给一句「请检查权限」。

        权限不足、`WScript.Shell` COM 起不来、路径没落地、扩展名不是 `.lnk`——这几种
        在只有一句提示的消息里长得一模一样。CI 的 Windows runner 上这一步失败过，日志里
        除了那句提示什么都没有，无从判断是哪一种。

        判据落在脚本 `catch` 出来的那一行 JSON 上，不看退出码也不看 stderr：Windows
        PowerShell 5.1 成功那一次也往 stderr 写 `#< CLIXML` 的进度流，照它取原因只会拿到
        「正在准备首次使用模块。」。
        """
        with self.assertRaises(OSError) as raised:
            desktop_startup.shortcut('write', self.root / '不是快捷方式.txt',
                                     target=str(self.program / 'Peach.exe'))
        message = str(raised.exception)
        self.assertIn('启动项未能保存', message)
        self.assertIn('Expected shortcut', message)
        self.assertRegex(message, r'第 \d+ 行')
        self.assertNotIn('CLIXML', message)

    @unittest.skipUnless(os.name == 'nt', 'Windows 系统卸载助手')
    def test_native_uninstall_removes_only_temporary_owned_program_and_data(self):
        for key in settings_file.DIRECTORY_KEYS:
            path = self.config.directory(key)
            path.mkdir()
            (path / 'fixture.txt').write_text('fixture')
        self.config.path.write_text('fixture')
        generated_backup = self.data / 'config.toml.before-access-fix-20260906-135757'
        generated_backup.write_text('fixture')
        unrelated = self.data / 'personal.txt'
        unrelated.write_text('preserve')
        media = self.root / 'media'
        media.mkdir()
        (media / 'video.mp4').write_text('preserve')
        # 走带 `quiet` 的那条：脚本一旦进 catch，不带它就在桌面上弹一个真的模态框。
        log = self.root / 'uninstall.log'
        result = self._run_native_uninstall(
            desktop_uninstall.plan(self.config, delete_data=True, program=self.program), log=log)
        self.assertEqual(result.returncode, 0,
                         result.stderr.decode(errors='replace')
                         + (log.read_text(encoding='utf-8') if log.exists() else ''))
        self.assertFalse(self.program.exists())
        self.assertFalse(self.config.path.exists())
        self.assertFalse(generated_backup.exists())
        self.assertTrue(unrelated.is_file())
        self.assertTrue((media / 'video.mp4').is_file())

    def _run_native_uninstall(self, job, log=None):
        shell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        payload = dict(job, pid=2147483647, quiet=True)
        if log is not None:
            payload['log'] = str(log)
        # 删不掉文件的用例清场三轮、等两次各 2 秒的重试，本机分片并行时约 6 秒。
        # 30 秒的上限同时守着清场只读同名进程的路径：逐个读七百多个进程的路径一遍
        # 就要七秒以上，三轮加等待会超时。
        return subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-EncodedCommand',
                               base64.b64encode(desktop_uninstall._SCRIPT.encode('utf-16-le')).decode('ascii')],
                              input=json.dumps(payload).encode('utf-8'), capture_output=True,
                              timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)

    @unittest.skipUnless(os.name == 'nt', 'Windows 系统卸载助手')
    def test_native_uninstall_kills_strays_running_from_the_program(self):
        stray = self.program / 'Stray.exe'
        shutil.copyfile(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/cmd.exe', stray)
        held = subprocess.Popen([str(stray), '/c', 'ping', '-n', '60', '127.0.0.1'],
                                creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(held.kill)
        result = self._run_native_uninstall(
            desktop_uninstall.plan(self.config, delete_data=False, program=self.program))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.program.exists())
        self.assertTrue(self.root.exists(), '只删除 Peach 解压目录，不删除用户选择的父目录')
        self.assertIsNotNone(held.poll())

    @unittest.skipUnless(os.name == 'nt', 'Windows 系统卸载助手')
    def test_native_uninstall_withdraws_what_the_installer_registered(self):
        import uuid
        import winreg
        key = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{{{str(uuid.uuid4()).upper()}}}_is1"
        winreg.CloseKey(winreg.CreateKey(winreg.HKEY_CURRENT_USER, key))
        self.addCleanup(lambda: subprocess.run(['reg', 'delete', 'HKCU\\' + key, '/f'], capture_output=True))
        owned, foreign = self.root / 'menu' / 'Peach.lnk', self.root / 'other' / 'Peach.lnk'
        owned.parent.mkdir(); foreign.parent.mkdir()
        desktop_startup.shortcut('write', owned, target=str(self.program / 'Peach.exe'))
        desktop_startup.shortcut('write', foreign, target=str(self.root / 'Elsewhere' / 'Peach.exe'))
        job = desktop_uninstall.plan(self.config, delete_data=False, program=self.program)
        result = self._run_native_uninstall(dict(job, installer_key=key, installer_menu=str(owned)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(owned.exists())
        with self.assertRaises(FileNotFoundError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, key)
        (self.program / '_internal').mkdir(parents=True)
        (self.program / '_internal/standalone.txt').touch()
        result = self._run_native_uninstall(dict(job, installer_menu=str(foreign)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(foreign.exists(), '同名开始菜单项指向别处时属于另一份安装')

    @unittest.skipUnless(os.name == 'nt', 'Windows 系统卸载助手')
    def test_native_uninstall_retries_until_the_file_is_released(self):
        blocked = self.program / '_internal' / 'locked.bin'
        blocked.write_text('fixture')
        handle = blocked.open('rb')
        self.addCleanup(handle.close)
        timer = threading.Timer(1.0, handle.close)
        timer.start()
        self.addCleanup(timer.join)
        result = self._run_native_uninstall(
            desktop_uninstall.plan(self.config, delete_data=False, program=self.program))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.program.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows 系统卸载助手')
    def test_native_uninstall_names_the_blocked_file_in_the_error_log(self):
        blocked = self.program / '_internal' / 'locked.bin'
        blocked.write_text('fixture')
        handle = blocked.open('rb')
        self.addCleanup(handle.close)
        log = self.root / 'uninstall.log'
        self.addCleanup(log.unlink, missing_ok=True)
        result = self._run_native_uninstall(
            desktop_uninstall.plan(self.config, delete_data=False, program=self.program), log=log)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(self.program.exists())
        self.assertIn('locked.bin', log.read_text(encoding='utf-8'))
