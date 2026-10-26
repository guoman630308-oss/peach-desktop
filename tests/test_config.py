import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from peach import config, settings_file
from peach.settings_file import SettingsFileError


class TempTree:
    """`<tmp>/peach-data` 加一个并列的 `<tmp>/app` 当项目根。

    临时目录一律先 `.resolve()`：CI runner 的临时路径都是别名（macOS `/var` 软链到
    `/private/var`，Windows 短名 `RUNNER~1` 展开成 `runneradmin`），不 resolve 的路径
    在开发机上过、在 CI 上红。
    """

    def __init__(self, with_data_root: bool = True):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name).resolve()
        self.project = self.root / "app"
        self.project.mkdir()
        self.data_root = self.root / "peach-data"
        if with_data_root:
            self.data_root.mkdir()

    def write(self, text: str) -> Path:
        path = self.data_root / settings_file.SETTINGS_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def load(self, environ: dict[str, str] | None = None, **kwargs):
        return settings_file.load_config(
            project_root=self.project, environ=environ or {}, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._temp.cleanup()


class ProjectRootTests(unittest.TestCase):
    def test_project_root_uses_pyinstaller_resource_directory_without_a_src_layer(self):
        # 锚点必须在当前平台上真的是绝对路径。`C:/repo` 在 POSIX 上是**相对**路径，
        # `_project_root` 里的 `.resolve()` 会给它前置 cwd，断言于是只在 Windows 成立。
        root = Path(tempfile.gettempdir()).resolve() / "repo"
        source = root / "src" / "peach" / "settings_file.py"
        self.assertEqual(settings_file._project_root(str(source)), root)
        # bundle_root 不经过 resolve，原样构造，两个平台比较结果一致。
        self.assertEqual(
            settings_file._project_root(str(source), "C:/bundle/_MEI123"),
            Path("C:/bundle/_MEI123"),
        )


class DataRootDiscoveryTests(unittest.TestCase):
    def test_saved_data_root_locations_reject_invalid_and_cyclic_paths(self):
        with TempTree() as tree:
            state = tree.data_root / "state"
            state.mkdir()
            locator = state / settings_file.DATA_ROOT_LOCATION
            for destination in ("", "relative", str(tree.data_root)):
                with self.subTest(destination=destination):
                    locator.write_text(destination, encoding="utf-8")
                    with self.assertRaises(SettingsFileError):
                        tree.load()

    def test_a_missing_selected_root_stays_selected(self):
        with TempTree() as tree:
            selected = tree.root / "custom-data"
            state = tree.data_root / "state"
            state.mkdir()
            (state / settings_file.DATA_ROOT_LOCATION).write_text(str(selected), encoding="utf-8")
            loaded = tree.load()
            self.assertEqual(loaded.data_root, selected)
            self.assertFalse(loaded.present)

    def test_environment_variable_wins_over_the_sibling_directory(self):
        with TempTree() as tree:
            elsewhere = tree.root / "elsewhere"
            found = settings_file.discover_data_root(
                tree.project, {"PEACH_DATA_ROOT": str(elsewhere)})
            self.assertEqual(found, (elsewhere, True))

    def test_sibling_peach_data_is_found_from_the_project_root(self):
        with TempTree() as tree:
            self.assertEqual(
                settings_file.discover_data_root(tree.project, {}),
                (tree.data_root, True),
            )

    def test_nested_checkout_still_finds_the_shared_data_root(self):
        """隔离工作树和打包产物都比项目根深一层以上，向上找必须仍然命中。"""
        with TempTree() as tree:
            nested = tree.root / "worktrees" / "task"
            nested.mkdir(parents=True)
            self.assertEqual(
                settings_file.discover_data_root(nested, {}), (tree.data_root, True))

    def test_frozen_build_searches_from_the_executable_directory(self):
        """单文件包的 `_MEIPASS` 是临时目录，起点必须是 EXE 所在目录才找得到 Peach 数据目录。"""
        with TempTree() as tree:
            executable = tree.project / "dist" / "Peach" / "Peach.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"")
            anchor = settings_file._search_anchor(
                tree.root / "temp" / "_MEI123", frozen=True, executable=str(executable))
            self.assertEqual(anchor, executable.parent)
            self.assertEqual(
                settings_file.discover_data_root(anchor, {}), (tree.data_root, True))

    def test_source_tree_searches_from_the_project_root(self):
        with TempTree() as tree:
            anchor = settings_file._search_anchor(
                tree.project, frozen=False, executable=str(tree.root / "python.exe"))
            self.assertEqual(anchor, tree.project)

    def test_missing_data_root_is_an_explicit_unconfigured_state(self):
        with TempTree(with_data_root=False) as tree:
            path, found = settings_file.discover_data_root(tree.project, {})
            self.assertFalse(found)
            # 找不到也要给出落点：`peach init` 按它建目录，页面按它显示提示。
            self.assertEqual(path, tree.root / "peach-data")


class DefaultsAreGenericTests(unittest.TestCase):
    """内建默认必须对一个全新用户成立，不能是某台机器的坐标。"""

    def test_fresh_machine_gets_neutral_defaults(self):
        with TempTree() as tree:
            loaded = tree.load()
        self.assertEqual(loaded.server.host, "127.0.0.1")
        self.assertEqual(loaded.server.port, 8900)
        self.assertEqual(loaded.server.mdns_name, "peach")
        self.assertEqual(loaded.server.review_writer_origin, "")
        self.assertEqual(loaded.server.review_writer_proxy, "")
        # 一台新机器什么都没挂：所有来源按脱盘处理，不报错也不猜路径。
        self.assertEqual(loaded.mounts, {})
        self.assertEqual(loaded.replication.smb_host, "")
        self.assertEqual(loaded.replication.smb_user, "")

    def test_replication_defaults_off_for_a_single_machine(self):
        with TempTree() as tree:
            self.assertFalse(tree.load().replication.enabled)

    def test_shared_root_defaults_beside_the_data_root(self):
        with TempTree() as tree:
            loaded = tree.load()
            self.assertEqual(loaded.shared_root, tree.root / "peach-sync")
            self.assertEqual(loaded.smb_share, "peach-sync")

    def test_directories_default_to_their_own_names_under_the_data_root(self):
        with TempTree() as tree:
            loaded = tree.load()
            for key in settings_file.DIRECTORY_KEYS:
                self.assertEqual(loaded.directory(key), tree.data_root / key)


class PriorityTests(unittest.TestCase):
    """环境变量 > 设置文件 > 内建默认。三层都要有断言，不然改错顺序没人发现。"""

    FILE = """
[server]
port = 9100
mdns_name = 'from-file'
[replication]
enabled = true
smb_host = 'file-host'
"""

    def test_file_overrides_builtin_defaults(self):
        with TempTree() as tree:
            tree.write(self.FILE)
            loaded = tree.load()
        self.assertEqual(loaded.server.port, 9100)
        self.assertEqual(loaded.server.mdns_name, "from-file")
        self.assertTrue(loaded.replication.enabled)
        # 文件里没写的项仍然取内建默认，不会因为文件存在就变空。
        self.assertEqual(loaded.server.host, "127.0.0.1")

    def test_environment_overrides_the_file(self):
        with TempTree() as tree:
            tree.write(self.FILE)
            loaded = tree.load({
                "PEACH_MDNS_NAME": "from-env",
                "PEACH_SHARED_SMB_HOST": "env-host",
            })
        self.assertEqual(loaded.server.mdns_name, "from-env")
        self.assertEqual(loaded.replication.smb_host, "env-host")
        self.assertEqual(loaded.server.port, 9100)

    def test_empty_environment_variable_does_not_blank_the_file_value(self):
        # 空字符串是「没设」，不是「设成空」：LaunchAgent 里留个空变量很常见。
        with TempTree() as tree:
            tree.write(self.FILE)
            loaded = tree.load({"PEACH_MDNS_NAME": ""})
        self.assertEqual(loaded.server.mdns_name, "from-file")

    def test_absolute_directory_moves_that_data_out_of_the_data_root(self):
        with TempTree() as tree:
            other = tree.root / "elsewhere" / "db"
            tree.write(f"[directories]\ndatabase = '{other.as_posix()}'\n")
            loaded = tree.load()
        self.assertEqual(loaded.directory("database"), other)
        self.assertEqual(loaded.directory("state"), tree.data_root / "state")


class BadFileTests(unittest.TestCase):
    def test_syntax_error_names_the_file_and_the_line(self):
        with TempTree() as tree:
            path = tree.write("[server]\nport = = 3\n")
            with self.assertRaises(SettingsFileError) as caught:
                tree.load()
        message = str(caught.exception)
        self.assertIn(str(path), message)
        # tomllib 的消息自带行号；没有行号的报错等于让人在几十行里自己找。
        self.assertIn("line 2", message)

    def test_wrong_type_names_the_key(self):
        with TempTree() as tree:
            tree.write("[server]\nport = 'eight thousand'\n")
            with self.assertRaises(SettingsFileError) as caught:
                tree.load()
        self.assertIn("server.port", str(caught.exception))

    def test_boolean_is_not_accepted_as_a_port(self):
        with TempTree() as tree:
            tree.write("[server]\nport = true\n")
            with self.assertRaises(SettingsFileError):
                tree.load()

    def test_unknown_directory_key_is_rejected_instead_of_ignored(self):
        # 打错一个键名却静默忽略，等于那份数据默默落回默认目录。
        with TempTree() as tree:
            tree.write("[directories]\ndatabses = 'db'\n")
            with self.assertRaises(SettingsFileError) as caught:
                tree.load()
        self.assertIn("databses", str(caught.exception))

    def test_phase_one_drive_letter_keys_are_rejected_with_an_upgrade_hint(self):
        """第一阶段的 `[media] R = ...` 是盘符键；静默忽略等于整台机器安静地脱盘。"""
        with TempTree() as tree:
            tree.write("[media]\nR = '/Volumes/Media'\n")
            with self.assertRaises(SettingsFileError) as caught:
                tree.load()
        message = str(caught.exception)
        self.assertIn("R", message)
        self.assertIn("media.mounts", message)

    def test_mount_for_an_undeclared_location_is_rejected(self):
        # 打错一个来源 ID 同样是安静地脱盘，只是范围小一点。
        with TempTree() as tree:
            tree.write("[media.mounts]\nlocla = '/mnt/res'\n")
            with self.assertRaises(SettingsFileError) as caught:
                tree.load()
        self.assertIn("locla", str(caught.exception))

    def test_mounts_are_keyed_by_location_id(self):
        with TempTree() as tree:
            tree.write("[media.mounts]\nlocal = '/mnt/res/media'\n")
            loaded = tree.load()
        self.assertEqual(loaded.mounts, {"local": ("/mnt/res/media",)})

    def test_a_newly_declared_location_can_be_mounted(self):
        with TempTree() as tree:
            tree.write(
                "[media.locations]\nnas = 'N:/'\n[media.mounts]\nnas = '/mnt/nas'\n")
            loaded = tree.load()
        self.assertEqual(loaded.locations["nas"], ("N:/",))
        self.assertEqual(loaded.mounts, {"nas": ("/mnt/nas",)})

    def test_a_declared_locations_table_is_authoritative(self):
        """`peach init` 的问答只声明 local；内建默认的示例盘符不得从旁边混进来。"""
        with TempTree() as tree:
            tree.write("[media.locations]\nlocal = 'D:\\Videos'\n")
            loaded = tree.load()
        self.assertEqual(loaded.locations, {"local": (r"D:\Videos",)})

    def test_a_location_may_declare_several_roots_with_matching_mounts(self):
        """两块硬盘都是 `local`：数据库路径前缀与媒体目录均写数组，按顺序对应，保存时保留。"""
        with TempTree() as tree:
            tree.write("[media.locations]\nlocal = ['D:\\Videos', 'E:\\Movies']\n"
                       "[media.mounts]\nlocal = ['/mnt/a', '/mnt/b']\n")
            loaded = tree.load()
            self.assertEqual(loaded.locations, {"local": (r"D:\Videos", r"E:\Movies")})
            self.assertEqual(loaded.mounts, {"local": ("/mnt/a", "/mnt/b")})
            rendered = settings_file.render(loaded)
            self.assertIn("local = ['D:\\Videos', 'E:\\Movies']", rendered)
            self.assertIn("local = ['/mnt/a', '/mnt/b']", rendered)
            tree.write(rendered)
            self.assertEqual(tree.load().locations, loaded.locations)

    def test_mount_count_must_match_the_declared_roots(self):
        # 媒体目录按顺序对应数据库路径前缀，数目不一致时拒绝保存。
        with TempTree() as tree:
            tree.write("[media.locations]\nlocal = ['D:\\Videos', 'E:\\Movies']\n"
                       "[media.mounts]\nlocal = '/mnt/a'\n")
            with self.assertRaises(SettingsFileError) as caught:
                tree.load()
        self.assertIn("2 个数据库路径前缀", str(caught.exception))
        with TempTree() as tree:
            tree.write("[media.locations]\nlocal = ['D:\\Videos', 'D:\\Videos']\n")
            with self.assertRaises(SettingsFileError) as caught:
                tree.load()
        self.assertIn("重复", str(caught.exception))
        with TempTree() as tree:
            tree.write("[media.locations]\nlocal = []\n")
            with self.assertRaises(SettingsFileError) as caught:
                tree.load()
        self.assertIn("没有配置目录前缀", str(caught.exception))

    def test_without_a_locations_table_the_builtin_defaults_apply(self):
        with TempTree() as tree:
            tree.write("[server]\nport = 9100\n")
            loaded = tree.load()
        self.assertEqual(loaded.locations, settings_file.DEFAULT_LOCATION_ROOTS)

    def test_lenient_load_falls_back_to_builtin_defaults(self):
        """坏文件不能让进程连 Peach 数据目录都不知道——发现顺序不看文件内容。"""
        with TempTree() as tree:
            tree.write("nonsense = =\n")
            loaded = tree.load(strict=False)
        self.assertEqual(loaded.data_root, tree.data_root)
        self.assertEqual(loaded.server.port, 8900)
        self.assertFalse(loaded.present)

    def test_lenient_load_also_survives_a_merge_time_rejection(self):
        """语法没错、内容被拒的文件同样得退得下来。

        `peach init --force` 是坏文件的唯一自救入口，它走的就是 `strict=False`。
        合并期的拒绝漏在退路之外的话，`[media] R = ...` 这种写法会把自救入口
        本身打崩——语法完全正确，抛错的是键空间校验。
        """
        for text in ("[media]\nR = '/mnt/res'\n",
                     "[media.mounts]\nlocla = '/mnt/res'\n",
                     "[directories]\ndatabses = 'db'\n"):
            with self.subTest(text=text):
                with TempTree() as tree:
                    tree.write(text)
                    with self.assertRaises(SettingsFileError):
                        tree.load()
                    loaded = tree.load(strict=False)
                self.assertEqual(loaded.data_root, tree.data_root)
                self.assertEqual(loaded.server.port, 8900)
                self.assertEqual(loaded.mounts, {})
                self.assertFalse(loaded.present)


class ConfiguredStateTests(unittest.TestCase):
    def test_existing_data_root_counts_as_configured_without_a_settings_file(self):
        # 现有部署还没生成过设置文件，但它们的账本在跑，不能被判成「未配置」。
        with TempTree() as tree:
            loaded = tree.load()
        self.assertFalse(loaded.present)
        self.assertTrue(loaded.configured)

    def test_fresh_clone_is_unconfigured(self):
        with TempTree(with_data_root=False) as tree:
            loaded = tree.load()
        self.assertFalse(loaded.configured)
        self.assertEqual(loaded.path.name, settings_file.SETTINGS_FILENAME)


class SerialisationTests(unittest.TestCase):
    def test_render_then_load_returns_the_same_values(self):
        with TempTree() as tree:
            captured = settings_file.capture_existing(
                tree.load(),
                overrides={
                    "mdns_name": "peach-two", "port": 9443,
                    "review_writer_origin": "https://192.0.2.5",
                    "smb_host": "peach-writer.local", "smb_user": "someone",
                },
                # 反斜杠和 Windows 盘符必须原样活过一次往返：序列化器要是把 `\m`
                # 当转义处理，挂载点就会变成另一个目录。
                mounts={"local": (r"D:\media",), "115": ("/mnt/115",)},
            )
            settings_file.write(captured)
            reloaded = tree.load()
        self.assertEqual(reloaded.server, captured.server)
        self.assertEqual(reloaded.replication, captured.replication)
        self.assertEqual(reloaded.mounts, {"local": (r"D:\media",), "115": ("/mnt/115",)})
        self.assertEqual(reloaded.locations, captured.locations)
        self.assertEqual(reloaded.directories, captured.directories)
        self.assertTrue(reloaded.present)

    def test_values_with_quotes_survive_the_round_trip(self):
        with TempTree() as tree:
            captured = settings_file.capture_existing(
                tree.load(), overrides={"smb_user": "it's me"})
            settings_file.write(captured)
            self.assertEqual(tree.load().replication.smb_user, "it's me")

    def test_write_refuses_to_overwrite_without_force(self):
        with TempTree() as tree:
            loaded = tree.load()
            settings_file.write(loaded)
            with self.assertRaises(SettingsFileError):
                settings_file.write(loaded)
            self.assertEqual(settings_file.write(loaded, force=True), loaded.path)

    def test_capture_keeps_existing_deployments_replicating(self):
        """第三阶段接上开关时，现有双机部署读到的必须还是 true。"""
        with TempTree() as tree:
            captured = settings_file.capture_existing(tree.load())
            self.assertTrue(captured.replication.enabled)
            # 共享目录和共享名落成显式值，别让人去猜默认规则。
            self.assertEqual(captured.replication.shared_root,
                             str(tree.root / "peach-sync"))
            self.assertEqual(captured.replication.smb_share, "peach-sync")

    def test_fresh_capture_leaves_replication_off(self):
        with TempTree() as tree:
            captured = settings_file.capture_existing(
                tree.load(), replication_enabled=False)
        self.assertFalse(captured.replication.enabled)


class ModuleConstantTests(unittest.TestCase):
    """`config` 的模块常量被大量 import；名字和相对关系都是契约。"""

    def test_paths_derive_from_the_active_data_root(self):
        self.assertEqual(config.DATABASE_PATH, config.DATA_ROOT / "database" / "ledger.db")
        self.assertEqual(config.DATABASE_PATH.parent, config.DATABASE_DIR)
        self.assertEqual(config.FFMPEG_DIR, config.TOOLS_DIR / "ffmpeg")
        self.assertEqual(config.COVER_DIR, config.GENERATED_DIR / "covers")
        # 迁移随代码走，不随数据走。
        self.assertEqual(config.MIGRATIONS_DIR, config.PROJECT_ROOT / "migrations")

    def test_mdns_hostname_follows_the_configured_name(self):
        self.assertEqual(config.MDNS_HOSTNAME, f"{config.MDNS_NAME}.local")

    def test_share_name_defaults_to_the_shared_directory_name(self):
        # 挂载点是 `/Volumes/<共享名>`：两者分开写就会挂上一个没人去读的路径。
        self.assertEqual(config.SHARED_SMB_SHARE, config.SHARED_DATA_ROOT.name)

    def test_location_declarations_stay_in_ledger_shape(self):
        # `asset.path` 与配置中的路径前缀均使用 Windows 格式，供目录访问权限检查匹配。
        self.assertEqual(set(config.LOCATION_ROOT_DECLARATIONS), {"local", "115", "pikpak"})
        self.assertEqual(
            tuple(root for roots in config.LOCATION_ROOT_DECLARATIONS.values() for root in roots),
            config.MEDIA_ROOT_DECLARATIONS,
        )

    def test_settings_dataclass_reports_the_configured_state(self):
        from peach.config import PeachSettings

        self.assertEqual(PeachSettings().configured, config.CONFIGURED)

    def test_share_coordinates_can_be_overridden_without_editing_source(self):
        # 主机名换了、共享改名、账号换一个，都不该要求改源码再发一次版。
        overrides = {
            "PEACH_SHARED_SMB_HOST": "198.51.100.9",
            "PEACH_SHARED_SMB_SHARE": "ledger-drop",
            "PEACH_SHARED_SMB_USER": "someone",
        }
        try:
            with patch.dict(os.environ, overrides):
                settings_file.reset_cache()
                reloaded = importlib.reload(config)
                self.assertEqual(reloaded.SHARED_SMB_HOST, "198.51.100.9")
                self.assertEqual(reloaded.SHARED_SMB_SHARE, "ledger-drop")
                self.assertEqual(reloaded.SHARED_SMB_USER, "someone")
        finally:
            settings_file.reset_cache()
            importlib.reload(config)
        self.assertNotEqual(config.SHARED_SMB_HOST, "198.51.100.9")


if __name__ == "__main__":
    unittest.main()
