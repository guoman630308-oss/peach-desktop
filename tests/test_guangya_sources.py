"""挂载来源配置、整理与回滚、文件删除及底层权限错误。"""
from contextlib import closing
from pathlib import Path, PureWindowsPath
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from peach import media_configuration, onboarding, organize, resource_identification, settings_file
from peach import web_batch, web_organize, web_resource_sync
from peach.media_sources import SCANNABLE_LOCATIONS, cost_for
from peach.web_contract import WebContract
from peach.web_settings import organize_templates, w_settings
from support.ledger import fresh_ledger


MOUNTED_SOURCES = ("guangya", "onedrive", "mounted")


class GuangyaSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_cloud_only_setup_roundtrips_windows_and_macos_mounts(self):
        for windows, path in ((True, "G:/Movies"), (False, "/Volumes/Guangya/Movies")):
            with self.subTest(windows=windows):
                config = settings_file.load_config(environ={"PEACH_DATA_ROOT": str(self.root)})
                answers = onboarding.Answers(self.root, (), "127.0.0.1", 8900, "peach", [
                    {"location": "guangya", "path": path, "root": "G:/Movies"}])
                prepared = onboarding.configure(config, answers, windows=windows)
                settings_file.write(prepared, force=True)
                loaded = settings_file.load_config(environ={"PEACH_DATA_ROOT": str(self.root)})
                self.assertEqual(loaded.locations, {"guangya": ("G:\\Movies",)})
                self.assertEqual(loaded.mounts, {} if windows else {"guangya": (path,)})
                self.assertEqual(media_configuration.rows(loaded, windows=windows)[0]["path"],
                                 "G:\\Movies" if windows else path)
        self.assertFalse((self.root / "database" / "ledger.db").exists())

    def test_setup_answers_accept_the_guangya_source(self):
        from peach.routes_pages import _read_answers
        config = settings_file.load_config(environ={"PEACH_DATA_ROOT": str(self.root)})
        answers, errors = _read_answers(config, {
            "data_root": str(self.root), "media_dir": ["/Volumes/Guangya"],
            "media_location": ["guangya"], "media_root": ["G:/"], "host": "1",
        }, windows=False)
        self.assertEqual(errors, {})
        self.assertEqual(answers.media_sources[0]["location"], "guangya")
        for location in MOUNTED_SOURCES:
            self.assertNotIn("只读", dict(media_configuration.SOURCE_OPTIONS)[location])

    def test_guangya_is_scannable_and_free(self):
        self.assertIn("guangya", SCANNABLE_LOCATIONS)
        self.assertEqual(cost_for("guangya"), "free")
        with patch.object(web_resource_sync, "LOCATION_ROOT_DECLARATIONS", {"guangya": ("G:/",)}):
            self.assertEqual(web_resource_sync.configured_resource_locations(), ("guangya",))

    def test_default_identification_reads_guangya_metadata(self):
        with closing(sqlite3.connect(fresh_ledger(self.root))) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("INSERT INTO asset(id,location,path,name,medium,size) "
                               "VALUES(1,'guangya',?,'FeatureFilm.mp4','video',123)",
                               ("G:\\FeatureFilm.mp4",))
            worklist = resource_identification.build_worklist(connection)
        self.assertEqual(len(worklist), 1)

    def test_organize_api_lists_mounts_and_executes_and_rolls_back_files(self):
        for location in MOUNTED_SOURCES:
            with self.subTest(location=location):
                root = self.root / location
                root.mkdir()
                media = root / "media"
                media.mkdir()
                original = media / "film.mp4"
                original.write_bytes(b"media")
                db = fresh_ledger(root)
                with closing(sqlite3.connect(db)) as connection:
                    connection.execute("INSERT INTO asset(id,location,path,name,medium,code) "
                        "VALUES(1,?,?,'film.mp4','video','ABC-123')", (location, "G:\\film.mp4"))
                    connection.commit()
                contract = WebContract(db, candidate_root=root / "generated")
                roots = {location: ("G:\\",)}
                templates = {location: {"file": "{number}", "dir": "{number}"}}
                w_settings(contract, {"organizeTemplates": templates})
                self.assertEqual(organize_templates(contract), templates)

                def translate(raw, mount=media):
                    return mount.joinpath(*PureWindowsPath(str(raw)).parts[1:])

                with patch.object(organize, "location_roots", return_value=roots), \
                        patch.object(web_organize, "location_roots", return_value=roots), \
                        patch.object(organize, "translate_ledger_path", side_effect=translate), \
                        patch.object(organize, "root_online", return_value=True):
                    available = web_organize.q_organize(contract)["locations"]
                    self.assertEqual([row["location"] for row in available], [location])
                    body = {"location": location, "confirm": True}
                    preview = web_organize.w_organize_preview(contract, body)
                    self.assertEqual(preview["counts"]["change"], 1)
                    web_organize.w_organize_apply(contract, body)
                    contract.organize_job.thread.join(timeout=15)
                    self.assertFalse(contract.organize_job.thread.is_alive())
                    state = contract.organize_job.snapshot()
                    self.assertEqual((state["status"], state.get("moved")), ("complete", 1), state)
                    self.assertEqual((media / "ABC-123" / "ABC-123.mp4").read_bytes(), b"media")
                    self.assertFalse(original.exists())
                    web_organize.w_organize_rollback(contract, {"confirm": True})
                    contract.organize_job.thread.join(timeout=15)
                    self.assertFalse(contract.organize_job.thread.is_alive())
                    state = contract.organize_job.snapshot()
                    self.assertEqual((state["status"], state.get("restored")), ("complete", 1), state)
                    self.assertEqual(original.read_bytes(), b"media")
                with closing(sqlite3.connect(db)) as connection:
                    self.assertEqual(connection.execute("SELECT path FROM asset").fetchone()[0],
                                     "G:\\film.mp4")

    def test_permanent_delete_quarantines_mount_files_and_removes_ledger_rows(self):
        with closing(sqlite3.connect(fresh_ledger(self.root))) as connection:
            connection.row_factory = sqlite3.Row
            for location in MOUNTED_SOURCES:
                for columns in ("id,location,path,snapshot_path", "id,path,snapshot_path"):
                    with self.subTest(location=location, columns=columns):
                        media = self.root / "film.mp4"
                        media.write_bytes(b"media")
                        connection.execute("INSERT INTO asset(id,location,path,name,medium) "
                            "VALUES(1,?,?,'film.mp4','video')", (location, str(media)))
                        result = web_batch.purge_assets(connection,
                            connection.execute(f"SELECT {columns} FROM asset").fetchall())
                        self.assertEqual((result["purged"], result["blocked"]), (1, []))
                        self.assertFalse(media.exists())
                        self.assertEqual(result["_staged"][0][1].read_bytes(), b"media")
                        self.assertEqual(connection.execute("SELECT count(*) FROM asset").fetchone()[0], 0)
                        web_batch._restore_staged_media(result["_staged"])
                        self.assertEqual(media.read_bytes(), b"media")

    def test_mount_write_error_keeps_the_media_and_ledger_row(self):
        with closing(sqlite3.connect(fresh_ledger(self.root))) as connection:
            connection.row_factory = sqlite3.Row
            for location in MOUNTED_SOURCES:
                with self.subTest(location=location):
                    media = self.root / "film.mp4"
                    media.write_bytes(b"media")
                    connection.execute("INSERT OR REPLACE INTO asset(id,location,path,name,medium) "
                        "VALUES(1,?,?,'film.mp4','video')", (location, str(media)))
                    with patch.object(web_batch.os, "rename", side_effect=PermissionError("写入权限不足")):
                        result = web_batch.purge_assets(connection,
                            connection.execute("SELECT id,location,path,snapshot_path FROM asset").fetchall())
                    self.assertEqual(result["purged"], 0)
                    self.assertIn("写入权限不足", result["blocked"][0]["reason"])
                    self.assertEqual(media.read_bytes(), b"media")
                    self.assertEqual(connection.execute("SELECT count(*) FROM asset").fetchone()[0], 1)

    def test_resource_scan_and_apply_clean_empty_mount_directories(self):
        contract = WebContract(fresh_ledger(self.root))
        for location in MOUNTED_SOURCES:
            with self.subTest(location=location):
                root = self.root / location
                empty = root / "empty"
                empty.mkdir(parents=True)
                with patch.object(web_resource_sync, "LOCATION_ROOT_DECLARATIONS", {location: (str(root),)}), \
                        patch.object(web_resource_sync, "source_is_online", return_value=True), \
                        patch.object(web_resource_sync, "vanished_asset_rows", return_value=[]), \
                        patch.object(web_resource_sync, "_source_roots", return_value=[root]):
                    scan = web_resource_sync._scan_sources(contract)
                    self.assertEqual(scan["empty_dirs"], {location: [str(empty)]})
                    self.assertEqual(scan["sources"][0]["empty"], 1)
                    state = {**scan, "result": {"sources": scan["sources"]}}
                    with patch.object(web_resource_sync, "_completed_scan", return_value=state):
                        result = web_resource_sync.w_resource_sync_apply(contract,
                            {"confirm": True, "scan_id": "scan", "clean_cache": False})
                    self.assertEqual(result["dirs_removed"], 1)
                    self.assertFalse(empty.exists())
                with patch.object(web_batch, "LOCATION_ROOT_DECLARATIONS", {location: (str(root),)}), \
                        patch.object(web_batch, "translate_ledger_path", return_value=root), \
                        patch.object(web_batch, "root_online", return_value=True):
                    self.assertEqual(web_batch._online_source_roots(), {location: (root,)})
