-- rule34.xxx 标签不区分大小写。旧唯一键直接保存用户粘贴的大小写，导致同一作者在
-- 分两批添加时成为两条来源。本迁移把大小写重复项合回最早的来源，同时保留候选、
-- 已看/忽略/保存状态与已生成的 online asset 关联。

CREATE TEMP TABLE _follow_source_merge AS
SELECT source.id AS loser_id, grouped.keep_id
FROM follow_source AS source
JOIN (
  SELECT provider, lower(ref) AS canonical_ref, min(id) AS keep_id
  FROM follow_source
  WHERE provider='rule34xxx'
  GROUP BY provider, lower(ref)
  HAVING count(*) > 1
) AS grouped
  ON grouped.provider=source.provider
 AND grouped.canonical_ref=lower(source.ref)
WHERE source.id<>grouped.keep_id;

INSERT INTO follow_item(
  source_id,external_id,title,url,media_url,thumb_url,published_at,
  published_precision,version,duration,release_key,variant_kind,variant_label,
  group_hint,status,asset_id,evidence_path,metadata_json,first_seen_at,last_seen_at
)
SELECT
  merge.keep_id,item.external_id,item.title,item.url,item.media_url,item.thumb_url,
  item.published_at,item.published_precision,item.version,item.duration,item.release_key,
  item.variant_kind,item.variant_label,item.group_hint,item.status,item.asset_id,
  item.evidence_path,item.metadata_json,item.first_seen_at,item.last_seen_at
FROM _follow_source_merge AS merge
JOIN follow_item AS item ON item.source_id=merge.loser_id
WHERE 1
ON CONFLICT(source_id,external_id) DO UPDATE SET
  status=CASE
    WHEN follow_item.status='saved' OR excluded.status='saved' THEN 'saved'
    WHEN follow_item.status='new' THEN excluded.status
    ELSE follow_item.status
  END,
  asset_id=coalesce(follow_item.asset_id,excluded.asset_id),
  evidence_path=coalesce(follow_item.evidence_path,excluded.evidence_path),
  first_seen_at=min(follow_item.first_seen_at,excluded.first_seen_at),
  last_seen_at=max(follow_item.last_seen_at,excluded.last_seen_at);

UPDATE follow_source AS kept
SET entity_id=coalesce(kept.entity_id,(
      SELECT source.entity_id FROM follow_source AS source
      WHERE source.provider=kept.provider AND lower(source.ref)=lower(kept.ref)
        AND source.entity_id IS NOT NULL
      ORDER BY source.updated_at DESC LIMIT 1
    )),
    enabled=(
      SELECT max(source.enabled) FROM follow_source AS source
      WHERE source.provider=kept.provider AND lower(source.ref)=lower(kept.ref)
    ),
    backfill_page=(
      SELECT max(source.backfill_page) FROM follow_source AS source
      WHERE source.provider=kept.provider AND lower(source.ref)=lower(kept.ref)
    ),
    last_checked_at=(
      SELECT max(source.last_checked_at) FROM follow_source AS source
      WHERE source.provider=kept.provider AND lower(source.ref)=lower(kept.ref)
    ),
    etag=(
      SELECT source.etag FROM follow_source AS source
      WHERE source.provider=kept.provider AND lower(source.ref)=lower(kept.ref)
      ORDER BY coalesce(source.last_checked_at,source.updated_at) DESC LIMIT 1
    ),
    last_modified=(
      SELECT source.last_modified FROM follow_source AS source
      WHERE source.provider=kept.provider AND lower(source.ref)=lower(kept.ref)
      ORDER BY coalesce(source.last_checked_at,source.updated_at) DESC LIMIT 1
    ),
    last_status=(
      SELECT source.last_status FROM follow_source AS source
      WHERE source.provider=kept.provider AND lower(source.ref)=lower(kept.ref)
      ORDER BY coalesce(source.last_checked_at,source.updated_at) DESC LIMIT 1
    ),
    last_error=(
      SELECT source.last_error FROM follow_source AS source
      WHERE source.provider=kept.provider AND lower(source.ref)=lower(kept.ref)
      ORDER BY coalesce(source.last_checked_at,source.updated_at) DESC LIMIT 1
    ),
    metadata_json=coalesce((
      SELECT nullif(source.metadata_json,'{}') FROM follow_source AS source
      WHERE source.provider=kept.provider AND lower(source.ref)=lower(kept.ref)
      ORDER BY source.updated_at DESC LIMIT 1
    ),kept.metadata_json),
    updated_at=(
      SELECT max(source.updated_at) FROM follow_source AS source
      WHERE source.provider=kept.provider AND lower(source.ref)=lower(kept.ref)
    )
WHERE kept.id IN (SELECT keep_id FROM _follow_source_merge);

DELETE FROM follow_source
WHERE id IN (SELECT loser_id FROM _follow_source_merge);

UPDATE follow_source
SET ref=lower(ref),
    updated_at=max(updated_at,created_at)
WHERE provider='rule34xxx';

DROP TABLE _follow_source_merge;
