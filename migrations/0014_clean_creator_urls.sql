INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)
SELECT id,'BT-btt.com',lower(trim('BT-btt.com')),'cleanup-0014',1.0 FROM entity WHERE kind='creator' AND canonical_name='BT-btt.com';
UPDATE entity SET canonical_name='BT-btt',normalized_name=lower(trim('BT-btt')),updated_at=datetime('now') WHERE kind='creator' AND canonical_name='BT-btt.com';
UPDATE asset SET creator='BT-btt' WHERE creator='BT-btt.com';

INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)
SELECT id,'MattieDoll - pornhub.com',lower(trim('MattieDoll - pornhub.com')),'cleanup-0014',1.0 FROM entity WHERE kind='creator' AND canonical_name='MattieDoll - pornhub.com';
UPDATE entity SET canonical_name='MattieDoll',normalized_name=lower(trim('MattieDoll')),updated_at=datetime('now') WHERE kind='creator' AND canonical_name='MattieDoll - pornhub.com';
UPDATE asset SET creator='MattieDoll' WHERE creator='MattieDoll - pornhub.com';

INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)
SELECT id,'StraplessDildo.com',lower(trim('StraplessDildo.com')),'cleanup-0014',1.0 FROM entity WHERE kind='creator' AND canonical_name='StraplessDildo.com';
UPDATE entity SET canonical_name='StraplessDildo',normalized_name=lower(trim('StraplessDildo')),updated_at=datetime('now') WHERE kind='creator' AND canonical_name='StraplessDildo.com';
UPDATE asset SET creator='StraplessDildo' WHERE creator='StraplessDildo.com';

INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)
SELECT id,'Vixen.com',lower(trim('Vixen.com')),'cleanup-0014',1.0 FROM entity WHERE kind='creator' AND canonical_name='Vixen.com';
UPDATE entity SET canonical_name='Vixen',normalized_name=lower(trim('Vixen')),updated_at=datetime('now') WHERE kind='creator' AND canonical_name='Vixen.com';
UPDATE asset SET creator='Vixen' WHERE creator='Vixen.com';

INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)
SELECT id,'dccdom.com@FKOS-004',lower(trim('dccdom.com@FKOS-004')),'cleanup-0014',1.0 FROM entity WHERE kind='creator' AND canonical_name='dccdom.com@FKOS-004';
UPDATE entity SET canonical_name='dccdom@FKOS-004',normalized_name=lower(trim('dccdom@FKOS-004')),updated_at=datetime('now') WHERE kind='creator' AND canonical_name='dccdom.com@FKOS-004';
UPDATE asset SET creator='dccdom@FKOS-004' WHERE creator='dccdom.com@FKOS-004';

INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)
SELECT id,'kpxvs.com-300MIUM-698',lower(trim('kpxvs.com-300MIUM-698')),'cleanup-0014',1.0 FROM entity WHERE kind='creator' AND canonical_name='kpxvs.com-300MIUM-698';
UPDATE entity SET canonical_name='kpxvs-300MIUM-698',normalized_name=lower(trim('kpxvs-300MIUM-698')),updated_at=datetime('now') WHERE kind='creator' AND canonical_name='kpxvs.com-300MIUM-698';
UPDATE asset SET creator='kpxvs-300MIUM-698' WHERE creator='kpxvs.com-300MIUM-698';

INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)
SELECT id,'mfgc8.com',lower(trim('mfgc8.com')),'cleanup-0014',1.0 FROM entity WHERE kind='creator' AND canonical_name='mfgc8.com';
UPDATE entity SET canonical_name='mfgc8',normalized_name=lower(trim('mfgc8')),updated_at=datetime('now') WHERE kind='creator' AND canonical_name='mfgc8.com';
UPDATE asset SET creator='mfgc8' WHERE creator='mfgc8.com';

INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)
SELECT id,'www.98T.la202202092146',lower(trim('www.98T.la202202092146')),'cleanup-0014',1.0 FROM entity WHERE kind='creator' AND canonical_name='www.98T.la202202092146';
UPDATE entity SET canonical_name='98T.la202202092146',normalized_name=lower(trim('98T.la202202092146')),updated_at=datetime('now') WHERE kind='creator' AND canonical_name='www.98T.la202202092146';
UPDATE asset SET creator='98T.la202202092146' WHERE creator='www.98T.la202202092146';
