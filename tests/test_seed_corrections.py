"""种子纠错只撤销自己的精确断言，包历史阻止回放。"""
import copy
import sqlite3
from unittest import mock

from peach import seed_pack
from peach.performer_profiles import read_profile, write_profile
from test_seed_pack import SeedPackCase, entity, alias, ref, link, membership, label_maker, STAMP


class CorrectionTests(SeedPackCase):
    def setUp(self):
        super().setUp()
        with self.source:
            self.person = entity(self.source, "performer", "天川そら")
            ref(self.source, self.person, "minnano-av", "123")
        with self.target:
            self.recipient = entity(self.target, "performer", "天川そら")

    def pack(self, revision, withdrawals=()):
        return seed_pack.export_pack(self.source, version=f"2026-10-{revision:02}",
                                     revision=revision, withdrawals=list(withdrawals))

    def withdrawal(self, field, value, *, name="天川そら", key="correction-1"):
        return {"id": key, "identity": {"kind": "performer", "name": name,
                                           "refs": [{"provider": "minnano-av", "kind": "performer", "id": "123"}]},
                "field": field, "value": value, "reason": "来源核对确认指向错误", "evidence": "review:verified.csv"}

    def land(self, pack):
        with self.target:
            return seed_pack.land(self.target, pack)

    def test_explicit_alias_ref_and_link_withdrawals_allow_corrected_ref(self):
        with self.source:
            alias(self.source, self.person, "错误名字", "review:source")
            ref(self.source, self.person, "javdb", "wrong")
            link(self.source, self.person, "social", "https://x.com/wrong")
        self.land(self.pack(1))
        withdrawals = [self.withdrawal("alias", "错误名字", key="bad-alias"),
                       self.withdrawal("ref", {"provider": "javdb", "kind": "performer", "id": "wrong"}, key="bad-ref"),
                       self.withdrawal("link", {"kind": "social", "url": "https://x.com/wrong"}, key="bad-link")]
        with self.source:
            self.source.execute("DELETE FROM entity_alias")
            self.source.execute("DELETE FROM entity_external_ref WHERE provider='javdb'")
            self.source.execute("DELETE FROM entity_link")
            ref(self.source, self.person, "javdb", "correct")
        fixed = self.pack(2, withdrawals)
        report = self.land(fixed)
        self.assertEqual(report["withdrawn"], 3)
        self.assertIsNone(self.target.execute("SELECT 1 FROM entity_alias WHERE alias='错误名字'").fetchone())
        self.assertEqual(self.target.execute("SELECT external_id FROM entity_external_ref WHERE provider='javdb'").fetchone()[0], "correct")
        self.assertEqual(self.target.execute("SELECT count(*) FROM entity_link").fetchone()[0], 0)
        self.assertEqual(seed_pack.written(self.land(fixed)), 0)

    def test_withdrawal_preserves_each_nonseed_owner(self):
        for owner in ("user:manual", "review:verified", "auto:performer-profile@4"):
            with self.subTest(owner=owner), self.target:
                self.target.execute("DELETE FROM entity_alias")
                alias(self.target, self.recipient, "错误名字", owner)
                report = seed_pack.land(self.target, self.pack(1, [self.withdrawal("alias", "错误名字")]))
                self.assertEqual(report["withdrawn"], 0)
                self.assertEqual(self.target.execute("SELECT source FROM entity_alias").fetchone()[0], owner)
                self.assertEqual(len(report["withdrawal_conflicts"]), 1)

    def test_profile_correction_uses_revision_even_with_same_fetched_at(self):
        with self.source:
            write_profile(self.source, self.person, {"height_cm": 164}, source="review:profile", source_url="https://example.com/profile", fetched_at=STAMP)
        self.land(self.pack(1))
        with self.source:
            self.source.execute("UPDATE performer_profile SET height_cm=165")
        report = self.land(self.pack(2))
        self.assertEqual(read_profile(self.target, self.recipient)["height_cm"], 165)
        self.assertEqual(report["refreshed"], 1)
        self.assertEqual(seed_pack.written(self.land(self.pack(2))), 0)

    def test_cumulative_withdrawal_supports_skipped_revision_and_rejects_replay(self):
        with self.source:
            alias(self.source, self.person, "错误名字", "review:source")
        initial = self.pack(1)
        self.land(initial)
        with self.source:
            self.source.execute("DELETE FROM entity_alias")
        withdrawal = self.withdrawal("alias", "错误名字")
        self.land(self.pack(3, [withdrawal]))
        with self.assertRaises(ValueError):
            self.land(initial)
        with self.assertRaises(ValueError):
            self.land(seed_pack.export_pack(self.source, version="2099-01-01"))
        self.assertIsNone(self.target.execute("SELECT 1 FROM entity_alias WHERE alias='错误名字'").fetchone())
        with self.assertRaises(ValueError):
            self.land(self.pack(4))

    def test_ambiguous_withdrawal_leaves_rows_and_records_review(self):
        with self.target:
            other = entity(self.target, "performer", "另一位艺人")
            ref(self.target, other, "minnano-av", "123")
            alias(self.target, self.recipient, "错误名字", "auto:seed@old")
        report = self.land(self.pack(1, [self.withdrawal("alias", "错误名字")]))
        self.assertEqual(report["withdrawn"], 0)
        self.assertEqual(len(report["withdrawal_conflicts"]), 1)
        self.assertEqual(self.target.execute("SELECT count(*) FROM entity_alias").fetchone()[0], 1)

    def test_absence_is_not_a_withdrawal(self):
        with self.source:
            alias(self.source, self.person, "已登记别名", "review:source")
        self.land(self.pack(1))
        with self.source:
            self.source.execute("DELETE FROM entity_alias")
        self.land(self.pack(2))
        self.assertEqual(self.target.execute("SELECT count(*) FROM entity_alias WHERE alias='已登记别名'").fetchone()[0], 1)

    def test_modified_content_and_invalid_pack_are_rejected_atomically(self):
        pack = self.pack(1)
        broken = copy.deepcopy(pack)
        broken["entities"][0]["aliases"].append({"alias": "无效包的名字", "source": "review:source"})
        before = list(self.target.iterdump())
        with self.assertRaises(ValueError):
            self.land(broken)
        self.assertEqual(list(self.target.iterdump()), before)
        self.land(pack)
        with self.source:
            alias(self.source, self.person, "同版本新内容", "review:source")
        with self.assertRaises(ValueError):
            self.land(self.pack(1))

    def test_schema_rejects_bool_revision_unknown_fields_and_duplicate_ref_identity(self):
        for mutate in (
                lambda p: p.update(revision=True),
                lambda p: p.update(unexpected="value"),
                lambda p: p["entities"].append({**copy.deepcopy(p["entities"][0]), "name": "另一位艺人"})):
            with self.subTest(mutate=mutate):
                pack = self.pack(1)
                mutate(pack)
                before = list(self.target.iterdump())
                with self.assertRaises(ValueError):
                    self.land(pack)
                self.assertEqual(list(self.target.iterdump()), before)

    def test_same_package_cannot_assert_its_withdrawn_value(self):
        with self.source:
            alias(self.source, self.person, "错误名字", "review:source")
        with self.assertRaises(ValueError):
            self.pack(1, [self.withdrawal("alias", "错误名字")])

    def test_export_inherits_withdrawals_from_explicit_previous_package(self):
        previous = self.pack(1, [self.withdrawal("alias", "错误名字")])
        later = seed_pack.export_pack(self.source, version="2026-10-04", revision=4, previous=previous)
        self.assertEqual(later["withdrawals"], previous["withdrawals"])

    def test_profile_and_both_pointer_types_are_precisely_withdrawn(self):
        with self.source:
            agency = entity(self.source, "agency", "事务所甲")
            maker = entity(self.source, "studio", "片商甲")
            label = entity(self.source, "studio", "厂牌甲")
            membership(self.source, self.person, agency, "review:source")
            label_maker(self.source, label, maker, "review:source")
            write_profile(self.source, self.person, {"height_cm": 164}, source="review:source", source_url="https://example.com/profile", fetched_at=STAMP)
        with self.target:
            entity(self.target, "agency", "事务所甲")
            entity(self.target, "studio", "片商甲")
            entity(self.target, "studio", "厂牌甲")
        first = self.pack(1)
        self.land(first)
        person = next(item for item in first["entities"] if item["kind"] == "performer")
        studio = next(item for item in first["entities"] if item["name"] == "厂牌甲")
        withdrawals = [self.withdrawal("profile", person["profile"], key="bad-profile"),
                       self.withdrawal("agency", person["agency"], key="bad-agency"),
                       {"id": "bad-maker", "identity": studio["identity"], "field": "maker", "value": studio["maker"],
                        "reason": "已核对归属", "evidence": "review:verified.csv"}]
        with self.source:
            self.source.execute("DELETE FROM performer_profile")
            self.source.execute("DELETE FROM entity_membership")
            self.source.execute("DELETE FROM label_maker")
        self.assertEqual(self.land(self.pack(2, withdrawals))["withdrawn"], 3)
        self.assertIsNone(read_profile(self.target, self.recipient))
        self.assertEqual(self.target.execute("SELECT count(*) FROM entity_membership").fetchone()[0], 0)
        self.assertEqual(self.target.execute("SELECT count(*) FROM label_maker").fetchone()[0], 0)

    def test_profile_withdrawal_requires_all_nonempty_columns(self):
        with self.target:
            write_profile(self.target, self.recipient, {"height_cm": 164, "birthplace": "東京都"}, source="auto:seed@old", source_url="https://example.com/profile", fetched_at=STAMP)
        partial = {"fields": {"height_cm": 164}, "source": "review:source", "source_url": "https://example.com/profile", "fetched_at": STAMP}
        report = self.land(self.pack(1, [self.withdrawal("profile", partial)]))
        self.assertEqual(report["withdrawn"], 0)
        self.assertEqual(len(report["withdrawal_conflicts"]), 1)
        self.assertEqual(read_profile(self.target, self.recipient)["birthplace"], "東京都")

    def test_runtime_error_rolls_back_withdrawals_facts_and_history(self):
        with self.source:
            alias(self.source, self.person, "错误名字", "review:source")
        self.land(self.pack(1))
        with self.source:
            self.source.execute("DELETE FROM entity_alias")
        fixed = self.pack(2, [self.withdrawal("alias", "错误名字")])
        before = list(self.target.iterdump())
        with mock.patch.object(seed_pack.seed_corrections, "remember", side_effect=sqlite3.OperationalError("simulated failure")):
            with self.assertRaises(sqlite3.OperationalError):
                seed_pack.land(self.target, fixed)
        self.assertEqual(list(self.target.iterdump()), before)

    def test_nonseed_profile_is_not_corrected_by_new_revision(self):
        with self.source:
            write_profile(self.source, self.person, {"height_cm": 164}, source="review:source", source_url="https://example.com/profile", fetched_at=STAMP)
        with self.target:
            write_profile(self.target, self.recipient, {"height_cm": 180}, source="auto:performer-profile@5", source_url="https://example.com/local", fetched_at=STAMP)
        self.land(self.pack(1))
        self.assertEqual(read_profile(self.target, self.recipient)["height_cm"], 180)

    def test_schema_failure_after_valid_entity_cannot_land_any_rows(self):
        pack = seed_pack.export_pack(self.source, version="2026-10-01")
        pack["entities"].append({"kind": "performer", "name": "另一位", "aliases": False, "refs": [], "links": []})
        before = list(self.target.iterdump())
        with self.assertRaises(ValueError):
            seed_pack.land(self.target, pack)
        self.assertEqual(list(self.target.iterdump()), before)

    def test_conflicting_reference_identities_are_rejected_without_hash_gate(self):
        pack = seed_pack.export_pack(self.source, version="2026-10-01")
        pack["entities"].append({"kind": "performer", "name": "另一位艺人", "aliases": [],
                                 "refs": copy.deepcopy(pack["entities"][0]["refs"]), "links": []})
        before = list(self.target.iterdump())
        with self.assertRaisesRegex(ValueError, "编号身份冲突"):
            seed_pack.land(self.target, pack)
        self.assertEqual(list(self.target.iterdump()), before)

    def test_alias_confidence_rejects_boolean_and_out_of_range_values(self):
        with self.source:
            alias(self.source, self.person, "来源别名", "review:source")
        for confidence in (True, None, -0.1, 1.1):
            with self.subTest(confidence=confidence):
                pack = self.pack(1)
                pack["entities"][0]["aliases"][0]["confidence"] = confidence
                pack["content_sha256"] = seed_pack.seed_validation.digest(pack)
                before = list(self.target.iterdump())
                with self.assertRaises(ValueError):
                    seed_pack.land(self.target, pack)
                self.assertEqual(list(self.target.iterdump()), before)

    def test_caller_rollback_restores_facts_and_version_history(self):
        with self.assertRaises(RuntimeError):
            with self.target:
                seed_pack.land(self.target, self.pack(1))
                raise RuntimeError("caller abort")
        self.assertEqual(self.target.execute("SELECT count(*) FROM entity_external_ref").fetchone()[0], 0)
        self.assertEqual(self.target.execute("SELECT count(*) FROM seed_pack_history").fetchone()[0], 0)

    def test_source_confidence_round_trip_preserves_manual_values(self):
        with self.source:
            alias(self.source, self.person, "来源别名", "r18:performer")
            self.source.execute("UPDATE entity_alias SET confidence=0.42")
        pack = self.pack(1)
        self.assertEqual(pack["entities"][0]["aliases"][0]["confidence"], 0.42)
        self.land(pack)
        held = self.target.execute("SELECT confidence,source FROM entity_alias WHERE alias='来源别名'").fetchone()
        self.assertEqual(tuple(held), (0.42, "auto:seed@2026-10-01"))
        with self.target:
            self.target.execute("UPDATE entity_alias SET confidence=0.61,source='user:manual' WHERE alias='来源别名'")
        with self.source:
            self.source.execute("UPDATE entity_alias SET confidence=0.3")
        self.land(self.pack(2))
        self.assertEqual(tuple(self.target.execute("SELECT confidence,source FROM entity_alias WHERE alias='来源别名'").fetchone()), (0.61, "user:manual"))

    def test_source_confidence_corrects_seed_owned_legacy_confidence(self):
        with self.source:
            alias(self.source, self.person, "来源别名", "r18:performer")
            self.source.execute("UPDATE entity_alias SET confidence=0.42")
        legacy = seed_pack.export_pack(self.source, version="2026-09-25")
        self.land(legacy)
        self.assertEqual(self.target.execute("SELECT confidence FROM entity_alias WHERE alias='来源别名'").fetchone()[0], 1)
        report = self.land(self.pack(1))
        self.assertEqual(report["refreshed"], 1)
        self.assertEqual(self.target.execute("SELECT confidence FROM entity_alias WHERE alias='来源别名'").fetchone()[0], 0.42)
        self.assertEqual(seed_pack.written(self.land(self.pack(1))), 0)

    def test_stash_aliases_are_not_exported_as_public_v2_facts(self):
        with self.source:
            alias(self.source, self.person, "本机导入别名", "stash:performer")
        self.assertTrue(seed_pack.export_pack(self.source, version="2026-09-25")["entities"][0]["aliases"])
        pack = self.pack(1)
        self.assertEqual(pack["entities"][0]["aliases"], [])
        self.assertEqual(pack["withdrawals"], [])

    def test_local_import_only_people_remain_local(self):
        local_people = (("古河君", "古河X沐沐", "61"),
                        ("小芽芽不乖", "91社区21442644", "63"))
        with self.source:
            for name, other_name, external_id in local_people:
                person = entity(self.source, "performer", name)
                alias(self.source, person, other_name, "stash:performer")
                ref(self.source, person, "stash", external_id)
        snapshot = {
            table: [tuple(row) for row in self.source.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in ("entity", "entity_alias", "entity_external_ref")}
        pack = self.pack(1)
        self.assertEqual([item["name"] for item in pack["entities"]], ["天川そら"])
        self.assertEqual(pack["counts"]["entities"], 1)
        self.assertEqual(pack["withdrawals"], [])
        self.assertEqual(snapshot, {
            table: [tuple(row) for row in self.source.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in snapshot})

