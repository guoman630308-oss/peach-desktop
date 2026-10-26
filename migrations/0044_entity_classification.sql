-- 身份类型、职业、发行市场与账号角色是带来源的独立断言。
CREATE TABLE entity_classification(
  entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  facet TEXT NOT NULL CHECK(facet IN ('identity','occupation','market','account_role')),
  value TEXT NOT NULL,
  source TEXT NOT NULL,
  source_url TEXT NOT NULL DEFAULT '',
  evidence TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('candidate','observed','approved','rejected')),
  confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
  checked_at TEXT NOT NULL,
  PRIMARY KEY(entity_id,facet,value,source)
);
CREATE INDEX idx_entity_classification_filter ON entity_classification(facet,value,status,entity_id);
CREATE TABLE entity_identity_link(
  left_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  right_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  relation TEXT NOT NULL CHECK(relation IN ('same_person','operates_account')),
  source TEXT NOT NULL,
  source_url TEXT NOT NULL,
  evidence TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('candidate','observed','approved','rejected')),
  checked_at TEXT NOT NULL,
  CHECK(left_id<>right_id),
  PRIMARY KEY(left_id,right_id,relation,source)
);
CREATE INDEX idx_entity_identity_link_right ON entity_identity_link(right_id);
CREATE TRIGGER entity_classification_cleanup AFTER DELETE ON entity BEGIN
  DELETE FROM entity_classification WHERE entity_id=OLD.id;
  DELETE FROM entity_identity_link WHERE left_id=OLD.id OR right_id=OLD.id;
END;
INSERT INTO ledger_revision(tbl) VALUES('entity_classification'),('entity_identity_link');
CREATE TRIGGER rev_entity_classification_insert AFTER INSERT ON entity_classification BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_classification';
END;
CREATE TRIGGER rev_entity_classification_update AFTER UPDATE ON entity_classification BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_classification';
END;
CREATE TRIGGER rev_entity_classification_delete AFTER DELETE ON entity_classification BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_classification';
END;
CREATE TRIGGER rev_entity_identity_link_insert AFTER INSERT ON entity_identity_link BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_identity_link';
END;
CREATE TRIGGER rev_entity_identity_link_update AFTER UPDATE ON entity_identity_link BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_identity_link';
END;
CREATE TRIGGER rev_entity_identity_link_delete AFTER DELETE ON entity_identity_link BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_identity_link';
END;
