"""跟账本走的界面设置。

只有侧栏顺序进这里。测试重点是两件事：白名单不能被绕过（别的设置不该悄悄跟着
同步过去），以及归一化必须挡住坏载荷——侧栏渲染不出来会让整个导航不可用。
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach import web_settings
from peach.web_contract import WebContract
from peach.web_settings import (
    DEFAULT_SIDEBAR_ORDER,
    normalise_sidebar_order,
    q_settings,
    w_settings,
)

PROFILE_SCHEMA = """
CREATE TABLE profile(
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  name TEXT NOT NULL,
  is_default INTEGER NOT NULL DEFAULT 0,
  settings_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
INSERT INTO profile(id,user_id,name,is_default,settings_json,created_at,updated_at)
VALUES('local-default','local','Default',1,'{}','2026-08-14T12:17:23Z','2026-08-14T12:17:23Z');
"""


class SidebarOrderNormalisationTests(unittest.TestCase):
    def test_unknown_keys_are_dropped_instead_of_rendered(self):
        """不认识的键可能来自旧版本或手改的载荷，留着会渲染出点不开的入口。"""
        self.assertEqual(
            normalise_sidebar_order(["", "tags", "nope", "performers"]),
            ["", "tags", "performers"],
        )

    def test_duplicates_collapse_and_order_is_kept(self):
        self.assertEqual(
            normalise_sidebar_order(["tags", "", "tags", "performers", ""]),
            ["tags", "", "performers"],
        )

    def test_an_empty_result_falls_back_to_the_default(self):
        """空侧栏没有可用性可言，宁可回到默认顺序。"""
        self.assertEqual(normalise_sidebar_order([]), list(DEFAULT_SIDEBAR_ORDER))
        self.assertEqual(normalise_sidebar_order(["nope"]), list(DEFAULT_SIDEBAR_ORDER))
        self.assertEqual(normalise_sidebar_order("tags"), list(DEFAULT_SIDEBAR_ORDER))
        self.assertEqual(normalise_sidebar_order(None), list(DEFAULT_SIDEBAR_ORDER))

    def test_optional_entries_are_accepted(self):
        """可选入口加进侧栏就是「显示」——顺序和显隐是同一个数组。"""
        self.assertEqual(
            normalise_sidebar_order(["", "trash", "quality"]),
            ["", "trash", "quality"],
        )

    def test_old_cleanup_entries_collapse_into_the_combined_destination(self):
        self.assertEqual(
            normalise_sidebar_order(["", "ads", "dupes", "trash"]),
            ["", "data-cleanup", "trash"],
        )


class SettingsRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "ledger.db"
        con = sqlite3.connect(self.db)
        con.executescript(PROFILE_SCHEMA)
        con.commit()
        con.close()
        self.contract = WebContract(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def _stored_json(self) -> dict:
        con = sqlite3.connect(self.db)
        try:
            raw = con.execute(
                "SELECT settings_json FROM profile WHERE id='local-default'").fetchone()[0]
        finally:
            con.close()
        return json.loads(raw)

    def test_an_unset_profile_reads_the_default_order(self):
        self.assertEqual(q_settings(self.contract),
                         {"sidebarOrder": list(DEFAULT_SIDEBAR_ORDER),
                          "avatarRefreshDays": 90, "faviconRefreshDays": 365,
                          "followInitialDays": 30, "postSetupTutorialDone": False,
                          "organizeTemplates": {}, "feedHideGroupCompilations": True,
                          "feedHideSoloCompilations": False, "feedHideExcerpts": True,
                          "searchHistoryLimit": None})

    def test_the_search_history_limit_stays_unset_until_a_device_writes_it(self):
        """没写过读出 None，前端据此把本机记着的那个数同步上来一次；写过之后各端读同一个数。"""
        self.assertIsNone(q_settings(self.contract)["searchHistoryLimit"])
        for limit in (0, 5, 50, "20"):
            w_settings(self.contract, {"searchHistoryLimit": limit})
            self.assertEqual(q_settings(self.contract)["searchHistoryLimit"], int(limit))
        for odd in (-1, 51, True, None, "many", 3.5, [5]):
            with self.assertRaises(ValueError, msg=repr(odd)):
                w_settings(self.contract, {"searchHistoryLimit": odd})
        self.assertEqual(self._stored_json(), {"searchHistoryLimit": 20})

    def test_the_compilation_switches_take_only_booleans(self):
        with self.assertRaises(ValueError):
            w_settings(self.contract, {"feedHideSoloCompilations": "yes"})
        self.assertEqual(web_settings.hidden_compilations(self.contract),
                         frozenset({"group", "excerpt"}))
        w_settings(self.contract, {"feedHideSoloCompilations": True,
                                   "feedHideGroupCompilations": False, "feedHideExcerpts": False})
        self.assertEqual(web_settings.hidden_compilations(self.contract), frozenset({"solo"}))

    def test_avatar_and_favicon_periods_are_read_only(self):
        settings = q_settings(self.contract)
        self.assertEqual(settings["avatarRefreshDays"], 90)
        self.assertEqual(settings["faviconRefreshDays"], 365)
        for key in ("avatarRefreshDays", "faviconRefreshDays", "metadataRefreshDays"):
            with self.assertRaises(ValueError):
                w_settings(self.contract, {key: 7})
        self.assertEqual(self._stored_json(), {})

    def test_a_written_order_survives_a_reread(self):
        order = ["", "follow", "tags", "trash"]
        self.assertEqual(w_settings(self.contract, {"sidebarOrder": order}),
                         {"ok": True, "sidebarOrder": order,
                          "avatarRefreshDays": 90, "faviconRefreshDays": 365,
                          "followInitialDays": 30, "postSetupTutorialDone": False,
                          "organizeTemplates": {}, "feedHideGroupCompilations": True,
                          "feedHideSoloCompilations": False, "feedHideExcerpts": True,
                          "searchHistoryLimit": None})
        self.assertEqual(q_settings(self.contract)["sidebarOrder"], order)
        self.assertEqual(self._stored_json()["sidebarOrder"], order)

    def test_the_tutorial_marker_is_a_boolean_that_survives_a_reread(self):
        """装完就是装完：换台设备打开不该再被教一遍，所以它也跟着账本走。"""
        self.assertFalse(q_settings(self.contract)["postSetupTutorialDone"])
        self.assertTrue(w_settings(self.contract, {"postSetupTutorialDone": True})
                        ["postSetupTutorialDone"])
        self.assertIs(self._stored_json()["postSetupTutorialDone"], True)
        for truthy in ("yes", 1, [1]):
            self.assertFalse(w_settings(self.contract, {"postSetupTutorialDone": truthy})
                             ["postSetupTutorialDone"], truthy)
        w_settings(self.contract, {"postSetupTutorialDone": True})
        self.assertEqual(w_settings(self.contract, {"sidebarOrder": ["", "tags"]})
                         ["postSetupTutorialDone"], True)

    def test_initial_history_range_is_persisted_without_changing_other_preferences(self):
        w_settings(self.contract, {"sidebarOrder": ["", "tags"]})
        for days in (0, 7, 30, 90):
            self.assertEqual(w_settings(self.contract, {"followInitialDays": days})['followInitialDays'], days)
            self.assertEqual(web_settings.follow_initial_days(self.contract), days)
        self.assertEqual(q_settings(self.contract)['sidebarOrder'], ['', 'tags'])
        for invalid in (-1, True, None, 'invalid', 3650):
            self.assertEqual(w_settings(self.contract, {"followInitialDays": invalid})['followInitialDays'], 30)

    def test_a_write_normalises_before_it_lands(self):
        """坏载荷不能进账本——存进去之后每次读都要再挡一遍。"""
        w_settings(self.contract, {"sidebarOrder": ["tags", "nope", "tags", ""]})
        self.assertEqual(self._stored_json()["sidebarOrder"], ["tags", ""])

    def test_settings_outside_the_allow_list_are_refused(self):
        """白名单不是黑名单：新增设置字段的人必须显式表态它该不该跨机同步。"""
        with self.assertRaises(ValueError) as caught:
            w_settings(self.contract, {"hoverDelaySeconds": 3})
        self.assertIn("hoverDelaySeconds", str(caught.exception))
        with self.assertRaises(ValueError):
            w_settings(self.contract, {"sidebarOrder": [""], "batchSize": 90})
        self.assertEqual(self._stored_json(), {}, "被拒的请求不该留下任何写入")

    def test_a_write_merges_instead_of_replacing_the_whole_blob(self):
        """请求没提到的键要保持原样，否则旧版本前端提交一次就会抹掉新字段。"""
        con = sqlite3.connect(self.db)
        con.execute("UPDATE profile SET settings_json=? WHERE id='local-default'",
                    (json.dumps({"futureKey": "keep me"}),))
        con.commit()
        con.close()
        w_settings(self.contract, {"sidebarOrder": ["", "tags"]})
        self.assertEqual(self._stored_json()["futureKey"], "keep me")

    def test_a_corrupt_blob_reads_as_unset_rather_than_breaking_the_page(self):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE profile SET settings_json='not json' WHERE id='local-default'")
        con.commit()
        con.close()
        self.assertEqual(q_settings(self.contract)["sidebarOrder"],
                         list(DEFAULT_SIDEBAR_ORDER))

    def test_a_non_object_body_is_a_type_error(self):
        with self.assertRaises(TypeError):
            w_settings(self.contract, ["", "tags"])
        with self.assertRaises(ValueError):
            w_settings(self.contract, {})


class ContractRegistrationTests(unittest.TestCase):
    def test_the_routes_are_registered_and_the_write_stays_behind_the_ledger_gate(self):
        """写侧栏顺序要写账本，所以它必须留在只读闸门后面。

        reader（macOS）因此改不了顺序，但读到的是 writer 的那一份——这正是
        「同一份习惯」的意思，不是缺陷。
        """
        from peach import web_contract

        self.assertIs(web_contract.GET_HANDLERS["/api/settings"], q_settings)
        self.assertIs(web_contract.POST_HANDLERS["/api/settings"], w_settings)
        self.assertNotIn(
            "/api/settings", web_contract.READ_ONLY_POST_ROUTES,
            "这个 POST 写数据库，不能放进只读白名单",
        )

    def test_the_sidebar_key_lists_match_the_web_surface(self):
        """键表是前后端共用的语义契约，两边各写一份就会漂。

        漂了不会报错：后端把前端没有的键判成合法、存进账本，前端渲染时又整个丢掉，
        表现是「排好的顺序刷新后自己变了」。所以逐字比对两份清单。
        """
        import re

        page = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "sidebar.ts").read_text(
            encoding="utf-8")

        def js_list(name):
            raw = re.search(rf"export const {name}: readonly string\[\] = \[(.*?)\];", page).group(1)
            return tuple(item.strip().strip("'") for item in raw.split(","))

        self.assertEqual(js_list("DEFAULT_SIDEBAR_ORDER"), DEFAULT_SIDEBAR_ORDER)
        # 默认那一份是首页、关注、JAV、三个索引、已标记、管理；播放列表和沉浸模式
        # 是从别处发起的动作，只在可加清单里。
        self.assertEqual(
            DEFAULT_SIDEBAR_ORDER,
            ("", "follow", "jav", "performers", "tags", "studios", "flagged", "manage"))
        self.assertNotIn("playlists", DEFAULT_SIDEBAR_ORDER)
        self.assertIn("playlists", web_settings.OPTIONAL_SIDEBAR_KEYS)
        self.assertIn("immerse", web_settings.OPTIONAL_SIDEBAR_KEYS)
        self.assertEqual(js_list("OPTIONAL_SIDEBAR_KEYS"),
                         web_settings.OPTIONAL_SIDEBAR_KEYS)

    def test_initial_history_choices_are_in_settings_and_match_the_server(self):
        import re
        root = Path(__file__).resolve().parents[1]
        page = (root / 'frontend/src/application/initial-follow-ranges.ts').read_text(encoding='utf-8')
        options = re.search(r'\bFOLLOW_INITIAL_RANGE_OPTIONS\s*=\s*\[(.*?)\]\s*as const', page, re.S).group(1)
        self.assertEqual(tuple(int(value) for value in re.findall(r"\['(\d+)',", options)),
                         web_settings.FOLLOW_INITIAL_DAYS)

if __name__ == "__main__":
    unittest.main()
