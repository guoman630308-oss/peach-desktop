-- 种子版本、内容校验与累计撤销清单；不随任务历史清理。
CREATE TABLE seed_pack_history(
    version TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK(revision >= 0),
    content_sha256 TEXT NOT NULL,
    withdrawals_json TEXT NOT NULL DEFAULT '[]',
    imported_at TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_seed_pack_history_revision
ON seed_pack_history(revision) WHERE revision > 0;

INSERT INTO ledger_revision(tbl) VALUES('seed_pack_history');
CREATE TRIGGER rev_seed_pack_history_insert AFTER INSERT ON seed_pack_history BEGIN
    UPDATE ledger_revision SET n=n+1 WHERE tbl='seed_pack_history';
END;
CREATE TRIGGER rev_seed_pack_history_update AFTER UPDATE ON seed_pack_history BEGIN
    UPDATE ledger_revision SET n=n+1 WHERE tbl='seed_pack_history';
END;
CREATE TRIGGER rev_seed_pack_history_delete AFTER DELETE ON seed_pack_history BEGIN
    UPDATE ledger_revision SET n=n+1 WHERE tbl='seed_pack_history';
END;
