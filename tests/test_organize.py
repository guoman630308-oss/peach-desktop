"""按模板整理：模板渲染、逃逸防护、计划生成、执行与回滚（ADR-0039）。"""
from __future__ import annotations

import sqlite3
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path, PureWindowsPath
from unittest import mock

from peach import organize
from peach.organize_templates import (
    TemplateError, render, sanitise_component, validate_template,
)
from support.ledger import fresh_ledger

ROOT_DECLARATION = "R:\\media"
DECLARED = {"local": (ROOT_DECLARATION,)}

VALUES = {
    "number": "ABC-123", "title": "标题", "studio": "厂牌", "series": "系列",
    "year": "2024", "actors": "甲、乙", "cd": "2", "mosaic": "无码",
    "definition": "1080p",
}


class TemplateRenderTests(unittest.TestCase):
    CASES = [
        # (模板, 取值覆盖, 期望)
        ("{number}", {}, "ABC-123"),
        ("{number} {title}", {}, "ABC-123 标题"),
        ("{number}[ {title}]", {"title": ""}, "ABC-123"),
        ("{number}[ {title}]", {}, "ABC-123 标题"),
        ("{number}[ CD{cd}]", {"cd": ""}, "ABC-123"),
        ("{number}[ CD{cd}]", {}, "ABC-123 CD2"),
        ("[{mosaic} ]{number}", {"mosaic": ""}, "ABC-123"),
        ("[{studio} ][{series} ]{number}", {"series": ""}, "厂牌 ABC-123"),
        ("{number} [{definition}]", {}, "ABC-123 1080p"),
        # 方括号是分组记号，不进结果；一个占位符都没有的分组就是一段普通字面量。
        ("{number} [完整版]", {}, "ABC-123 完整版"),
        # 转义写法给出字面的括号。
        ("{number} {{x}}", {}, "ABC-123 {x}"),
        ("{number} [[x]]", {}, "ABC-123 [x]"),
        # 出演者与年份是最常见的两组可选信息。
        ("{number}[ ({year})][ {actors}]", {"year": ""}, "ABC-123 甲、乙"),
    ]

    def test_render_table(self):
        for template, overrides, expected in self.CASES:
            with self.subTest(template=template, overrides=overrides):
                values = {**VALUES, **overrides}
                text, _missing = render(template, values)
                self.assertEqual(text, expected)

    def test_missing_required_field_is_reported(self):
        text, missing = render("{number} {title}", {**VALUES, "title": ""})
        self.assertEqual(missing, frozenset({"title"}))
        self.assertEqual(text, "ABC-123")

    def test_optional_field_is_not_required(self):
        _text, missing = render("{number}[ {title}]", {**VALUES, "title": ""})
        self.assertEqual(missing, frozenset())

    def test_directory_template_keeps_layers(self):
        text, _missing = render("{studio}/{number}", VALUES, directory=True)
        self.assertEqual(text, "厂牌/ABC-123")


class SanitiseTests(unittest.TestCase):
    def test_illegal_characters_become_underscore(self):
        self.assertEqual(sanitise_component('a<b>c:d"e/f\\g|h?i*j'),
                         "a_b_c_d_e_f_g_h_i_j")

    def test_trailing_dot_and_space_are_dropped(self):
        self.assertEqual(sanitise_component("名字. "), "名字")

    def test_reserved_device_name_is_prefixed(self):
        self.assertEqual(sanitise_component("CON"), "_CON")
        self.assertEqual(sanitise_component("nul.mp4"), "_nul.mp4")

    def test_long_value_is_truncated_from_the_middle(self):
        value = sanitise_component("甲" * 400)
        self.assertLessEqual(len(value), 120)
        self.assertIn("…", value)


class TemplateEscapeTests(unittest.TestCase):
    REJECTED = [
        "..\\{number}",
        "../{number}",
        "C:\\{number}",
        "/{number}",
        "{number}/{title}",          # 文件名模板不带分隔符
        "{unknown}",
        "{number",
        "{number}]",
        "[{number}",
    ]

    def test_escaping_templates_are_rejected(self):
        for template in self.REJECTED:
            with self.subTest(template=template):
                with self.assertRaises(TemplateError):
                    validate_template(template)

    def test_directory_template_may_layer_but_not_escape(self):
        self.assertEqual(validate_template("{studio}\\{number}", directory=True),
                         "{studio}/{number}")
        with self.assertRaises(TemplateError):
            validate_template("{studio}/../{number}", directory=True)

    def test_value_cannot_introduce_a_layer(self):
        text, _missing = render("{title}", {"title": "a/../b"})
        self.assertEqual(text, "a_.._b")


def _seed(db_path: Path, rows: list[dict]) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        for row in rows:
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,code,catalog_title,"
                "studio,series,release_date,width,height) "
                "VALUES(:id,'local',:path,:name,'video',:code,:title,:studio,NULL,"
                ":release_date,:width,:height)",
                {"series": None, "code": None, "title": None, "studio": None,
                 "release_date": None, "width": None, "height": None, **row})
        connection.commit()


def _plan(db_path: Path, **kwargs) -> dict:
    with closing(sqlite3.connect(db_path)) as connection:
        return organize.build_plan(connection, location="local", roots=DECLARED,
                                   online=True, **kwargs)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.holder = Path(tempfile.mkdtemp(prefix="peach-organize-")).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.holder, ignore_errors=True))
        self.db_path = fresh_ledger(self.holder)

    def test_rename_in_place_and_move_into_template_directory(self):
        _seed(self.db_path, [
            {"id": 1, "path": "R:\\media\\旧\\[site.com]abc123.mp4",
             "name": "[site.com]abc123.mp4", "code": "ABC-123", "title": "标题",
             "studio": "厂牌"},
        ])
        plan = _plan(self.db_path, file_template="{number}[ {title}]")
        self.assertEqual(plan["counts"]["change"], 1)
        self.assertEqual(plan["rows"][0]["action"], "rename")
        self.assertEqual(plan["rows"][0]["target_path"],
                         "R:\\media\\旧\\ABC-123 标题.mp4")

        plan = _plan(self.db_path, file_template="{number}", dir_template="{studio}")
        self.assertEqual(plan["rows"][0]["action"], "move")
        self.assertEqual(plan["rows"][0]["target_path"], "R:\\media\\厂牌\\ABC-123.mp4")

    def test_row_already_named_as_asked_is_not_listed(self):
        _seed(self.db_path, [
            {"id": 1, "path": "R:\\media\\ABC-123.mp4", "name": "ABC-123.mp4",
             "code": "ABC-123"},
        ])
        plan = _plan(self.db_path, file_template="{number}")
        self.assertEqual(plan["counts"]["unchanged"], 1)
        self.assertEqual(plan["rows"], [])

    def test_missing_field_skips_the_row(self):
        _seed(self.db_path, [
            {"id": 1, "path": "R:\\media\\x.mp4", "name": "x.mp4", "code": None},
        ])
        plan = _plan(self.db_path, file_template="{number}")
        self.assertEqual(plan["rows"][0]["action"], "skip")
        self.assertEqual(plan["rows"][0]["reason"], organize.SKIP_MISSING_FIELD)

    def test_two_rows_landing_on_one_name_keep_the_first(self):
        _seed(self.db_path, [
            {"id": 1, "path": "R:\\media\\a.mp4", "name": "a.mp4", "code": "ABC-123"},
            {"id": 2, "path": "R:\\media\\b.mp4", "name": "b.mp4", "code": "ABC-123"},
        ])
        plan = _plan(self.db_path, file_template="{number}")
        self.assertEqual(plan["counts"]["change"], 1)
        skipped = [row for row in plan["rows"] if row["action"] == "skip"]
        self.assertEqual(skipped[0]["reason"], organize.SKIP_TARGET_EXISTS)

    def test_scoped_plan_only_contains_requested_assets(self):
        _seed(self.db_path, [
            {"id": 1, "path": "R:\\media\\a.mp4", "name": "a.mp4", "code": "ABC-123"},
            {"id": 2, "path": "R:\\media\\b.mp4", "name": "b.mp4", "code": "DEF-456"},
        ])
        plan = _plan(self.db_path, file_template="{number}", asset_ids=[2])
        self.assertEqual(plan["counts"], {"total": 1, "unchanged": 0, "change": 1, "skip": 0})
        self.assertEqual([row["asset_id"] for row in plan["rows"]], [2])

    def test_offline_source_skips_every_row(self):
        _seed(self.db_path, [
            {"id": 1, "path": "R:\\media\\a.mp4", "name": "a.mp4", "code": "ABC-123"},
        ])
        with closing(sqlite3.connect(self.db_path)) as connection:
            plan = organize.build_plan(connection, location="local", roots=DECLARED,
                                       online=False, file_template="{number}")
        self.assertEqual(plan["counts"]["skip"], 1)
        self.assertEqual(plan["reasons"], {organize.SKIP_OFFLINE: 1})

    def test_path_outside_the_declared_root_is_skipped(self):
        _seed(self.db_path, [
            {"id": 1, "path": "B:\\其它\\a.mp4", "name": "a.mp4", "code": "ABC-123"},
        ])
        plan = _plan(self.db_path, file_template="{number}")
        self.assertEqual(plan["rows"][0]["reason"], organize.SKIP_OUTSIDE_ROOT)

    def test_plan_csv_carries_every_column(self):
        _seed(self.db_path, [
            {"id": 1, "path": "R:\\media\\a.mp4", "name": "a.mp4", "code": "ABC-123"},
        ])
        plan = _plan(self.db_path, file_template="{number}")
        path = organize.write_plan(self.holder / "plan.csv", plan["rows"])
        header = path.read_text(encoding="utf-8-sig").splitlines()[0]
        self.assertEqual(header.split(","), organize.PLAN_FIELDS)


class ApplyTests(unittest.TestCase):
    """执行与回滚都真的动文件：`translate_ledger_path` 指到临时目录，两个平台一致。"""

    def test_committed_moves_keep_one_journal_and_resume_without_renaming_twice(self):
        for index in (1, 2):
            self._file(f'old/{index}.mp4')
        _seed(self.db_path, [dict(id=index, path=f'R:\\media\\old\\{index}.mp4',
                                  name=f'{index}.mp4', code=f'ABC-00{index}') for index in (1, 2)])
        plan = _plan(self.db_path, file_template='{number}')
        def stop(**fields):
            raise InterruptedError('模拟整理中断')
        with closing(sqlite3.connect(self.db_path)) as connection, self.assertRaises(InterruptedError):
            organize.apply_plan(connection, plan['rows'], generated_root=self.holder, stamp='resume', progress=stop)
        with closing(sqlite3.connect(self.db_path)) as connection:
            result = organize.apply_plan(connection, plan['rows'], generated_root=self.holder, stamp='resume')
        self.assertEqual((result['moved'], result['failed']), (2, 0))
        journal = json.loads(Path(result['log_path']).read_text(encoding='utf-8'))
        self.assertEqual(len(journal['entries']), 2)
        self.assertEqual(len(list(organize.batch_log_dir(self.holder).glob('*.json'))), 1)

    def setUp(self):
        self.holder = Path(tempfile.mkdtemp(prefix="peach-organize-apply-")).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.holder, ignore_errors=True))
        self.media = self.holder / "media"
        self.media.mkdir()
        self.db_path = fresh_ledger(self.holder)
        patcher = mock.patch.object(organize, "translate_ledger_path", self._translate)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _translate(self, raw) -> Path:
        return self.media.joinpath(*PureWindowsPath(str(raw)).parts[2:])

    def _file(self, relative: str) -> Path:
        path = self.media / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
        return path

    def _apply(self, plan):
        with closing(sqlite3.connect(self.db_path)) as connection:
            return organize.apply_plan(connection, plan["rows"],
                                       generated_root=self.holder)

    def test_apply_moves_the_file_and_updates_the_ledger(self):
        self._file("旧/abc123.mp4")
        _seed(self.db_path, [
            {"id": 1, "path": "R:\\media\\旧\\abc123.mp4", "name": "abc123.mp4",
             "code": "ABC-123", "studio": "厂牌"},
        ])
        plan = _plan(self.db_path, file_template="{number}", dir_template="{studio}")
        result = self._apply(plan)
        self.assertEqual((result["moved"], result["failed"]), (1, 0))
        self.assertTrue((self.media / "厂牌" / "ABC-123.mp4").is_file())
        self.assertFalse((self.media / "旧" / "abc123.mp4").exists())
        with closing(sqlite3.connect(self.db_path)) as connection:
            row = connection.execute("SELECT path,name FROM asset WHERE id=1").fetchone()
        self.assertEqual(row, ("R:\\media\\厂牌\\ABC-123.mp4", "ABC-123.mp4"))

    def test_rollback_puts_everything_back(self):
        self._file("旧/abc123.mp4")
        _seed(self.db_path, [
            {"id": 1, "path": "R:\\media\\旧\\abc123.mp4", "name": "abc123.mp4",
             "code": "ABC-123", "studio": "厂牌"},
        ])
        self._apply(_plan(self.db_path, file_template="{number}", dir_template="{studio}"))
        batch = organize.latest_batch(self.holder)
        self.assertIsNotNone(batch)
        with closing(sqlite3.connect(self.db_path)) as connection:
            result = organize.rollback_batch(connection, batch)
        self.assertEqual((result["restored"], result["failed"]), (1, 0))
        self.assertTrue((self.media / "旧" / "abc123.mp4").is_file())
        with closing(sqlite3.connect(self.db_path)) as connection:
            path = connection.execute("SELECT path FROM asset WHERE id=1").fetchone()[0]
        self.assertEqual(path, "R:\\media\\旧\\abc123.mp4")
        # 整批退回去之后它不该再出现在「回滚上一批」里。
        self.assertIsNone(organize.latest_batch(self.holder))

    def test_target_taken_on_disk_fails_only_that_row(self):
        self._file("旧/abc123.mp4")
        self._file("ABC-123.mp4")
        _seed(self.db_path, [
            {"id": 1, "path": "R:\\media\\旧\\abc123.mp4", "name": "abc123.mp4",
             "code": "ABC-123"},
        ])
        plan = {"rows": [{"asset_id": 1, "action": "move",
                          "current_path": "R:\\media\\旧\\abc123.mp4",
                          "target_path": "R:\\media\\ABC-123.mp4"}]}
        result = self._apply(plan)
        self.assertEqual((result["moved"], result["failed"]), (0, 1))
        self.assertEqual(result["failures"][0]["reason"], organize.SKIP_TARGET_EXISTS)
        self.assertTrue((self.media / "旧" / "abc123.mp4").is_file())

    def test_cross_volume_row_is_refused(self):
        self._file("旧/abc123.mp4")
        _seed(self.db_path, [
            {"id": 1, "path": "R:\\media\\旧\\abc123.mp4", "name": "abc123.mp4",
             "code": "ABC-123"},
        ])
        plan = {"rows": [{"asset_id": 1, "action": "move",
                          "current_path": "R:\\media\\旧\\abc123.mp4",
                          "target_path": "B:\\media\\ABC-123.mp4"}]}
        result = self._apply(plan)
        self.assertEqual(result["failures"][0]["reason"], organize.SKIP_CROSS_VOLUME)
        self.assertTrue((self.media / "旧" / "abc123.mp4").is_file())

    def _pikpak_plan(self):
        self._file("旧/abc123.mp4")
        _seed(self.db_path, [
            {"id": 1, "path": "A:\\x\\旧\\abc123.mp4", "name": "abc123.mp4", "code": "ABC-123"},
        ])
        roots = mock.patch.object(organize, "location_roots",
                                  lambda: {"local": (ROOT_DECLARATION,), "pikpak": ("A:\\",)})
        roots.start()
        self.addCleanup(roots.stop)
        return {"rows": [{"asset_id": 1, "action": "rename",
                          "current_path": "A:\\x\\旧\\abc123.mp4",
                          "target_path": "A:\\x\\旧\\ABC-123.mp4"}]}

    def test_pikpak_batch_without_a_verified_rename_is_refused_whole(self):
        plan = self._pikpak_plan()
        with self.assertRaisesRegex(organize.OrganizeError, "pikpak"):
            self._apply(plan)
        self.assertTrue((self.media / "旧" / "abc123.mp4").is_file())
        with self.assertRaises(organize.UnverifiedRename):
            organize._rename("A:\\x\\旧\\abc123.mp4", "A:\\x\\旧\\ABC-123.mp4")
        self.assertTrue((self.media / "旧" / "abc123.mp4").is_file())

    def test_injected_verified_rename_carries_pikpak_rows(self):
        plan = self._pikpak_plan()
        calls = []

        def verified(source, target):
            calls.append((source, target))
            self._translate(source).rename(self._translate(target))

        with organize.verified_renames("pikpak", verified):
            result = self._apply(plan)
        self.assertEqual((result["moved"], result["failed"]), (1, 0))
        self.assertEqual(calls, [("A:\\x\\旧\\abc123.mp4", "A:\\x\\旧\\ABC-123.mp4")])
        self.assertTrue((self.media / "旧" / "ABC-123.mp4").is_file())
        self.assertTrue(organize.unverified_location("pikpak"))

    def test_missing_source_is_reported_not_raised(self):
        _seed(self.db_path, [
            {"id": 1, "path": "R:\\media\\旧\\abc123.mp4", "name": "abc123.mp4",
             "code": "ABC-123"},
        ])
        plan = _plan(self.db_path, file_template="{number}")
        result = self._apply(plan)
        self.assertEqual((result["moved"], result["failed"]), (0, 1))
        self.assertEqual(result["failures"][0]["reason"], "源文件不存在")


class EndpointTests(unittest.TestCase):
    """整理端点：预览只读、执行要确认、模板跟着账本走。"""

    def setUp(self):
        from peach.web_state import WebContract

        self.holder = Path(tempfile.mkdtemp(prefix="peach-organize-web-")).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.holder, ignore_errors=True))
        self.db_path = fresh_ledger(self.holder)
        _seed(self.db_path, [
            {"id": 1, "path": "R:\\media\\旧\\abc123.mp4", "name": "abc123.mp4",
             "code": "ABC-123", "title": "标题"},
        ])
        self.contract = WebContract(self.db_path, candidate_root=self.holder)
        for target, value in (("location_roots", lambda: DECLARED),
                              ("root_online", lambda _root: True)):
            patcher = mock.patch.object(organize, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_preview_writes_a_plan_and_counts_changes(self):
        from peach.web_organize import w_organize_preview

        result = w_organize_preview(self.contract, {
            "location": "local", "file_template": "{number}[ {title}]"})
        self.assertEqual(result["counts"]["change"], 1)
        self.assertEqual(result["rows"][0]["target_path"], "R:\\media\\旧\\ABC-123 标题.mp4")
        self.assertTrue(Path(result["plan_csv"]).is_file())

    def test_apply_without_confirmation_is_refused(self):
        from peach.web_organize import w_organize_apply

        with self.assertRaises(ValueError):
            w_organize_apply(self.contract, {"location": "local",
                                             "file_template": "{number}"})

    def test_apply_on_a_source_without_verified_rename_is_refused(self):
        from peach.web_organize import w_organize_apply

        with self.assertRaisesRegex(ValueError, "pikpak"):
            w_organize_apply(self.contract, {"location": "pikpak", "confirm": True,
                                             "file_template": "{number}"})

    def test_rollback_without_a_batch_is_refused(self):
        from peach.web_organize import w_organize_rollback

        with self.assertRaises(ValueError):
            w_organize_rollback(self.contract, {"confirm": True})

    def test_templates_round_trip_through_the_ledger(self):
        from peach.web_settings import organize_templates, w_settings

        w_settings(self.contract, {"organizeTemplates": {
            "115": {"file": "{number}[ {title}]", "dir": "{studio}"}}})
        self.assertEqual(organize_templates(self.contract),
                         {"115": {"file": "{number}[ {title}]", "dir": "{studio}"}})

    def test_escaping_template_is_refused_on_save(self):
        from peach.web_settings import w_settings

        with self.assertRaises(TemplateError):
            w_settings(self.contract, {"organizeTemplates": {
                "115": {"file": "{number}", "dir": "../{studio}"}}})

    def test_status_lists_placeholders_and_presets(self):
        from peach.web_organize import q_organize

        payload = q_organize(self.contract)
        self.assertEqual(payload["status"], "idle")
        self.assertTrue(payload["presets"])
        self.assertIn("number", [item["key"] for item in payload["placeholders"]])


if __name__ == "__main__":
    unittest.main()
