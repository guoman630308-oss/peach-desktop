-- 账本版本号：每张表的增删改都把这里对应一行加一。
--
-- 聚合页（口味、复核、垃圾复核）的缓存以版本号为键，只在账本真的变了之后才重算，不再按时间
-- 过期。服务进程内的写入本来就经 `after_commit` 清缓存；触发器兜的是进程外的写：CLI 的
-- `--apply` 脚本、推送发现的扫描连接、账本同步拉库。它们大多直接 `sqlite3.connect`，不经过
-- 服务的写事务，只有库本身能看见。
--
-- 不挂触发器的表：`task_run` 是任务中心每两秒一次的心跳与进度，和馆藏无关，跟着加一就等于
-- 长任务期间全程没有缓存；`schema_migration` 只在迁移时写；`asset_search*` 是全文索引的虚表
-- 与影子表，内容随 `asset` 走，也挂不上触发器。
--
-- 以后新建的表要在同一个迁移里补上这三个触发器和预置行，`tests/test_ledger_revision.py`
-- 在全新迁移的库上逐表核对。
CREATE TABLE ledger_revision(
  tbl TEXT PRIMARY KEY,
  n INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;

INSERT INTO ledger_revision(tbl) VALUES
  ('activity_event'),
  ('app_user'),
  ('asset'),
  ('asset_entity'),
  ('asset_preference'),
  ('asset_quality_goal'),
  ('asset_subtitle'),
  ('asset_tag'),
  ('asset_tag_preference'),
  ('code_sample_image'),
  ('entity'),
  ('entity_alias'),
  ('entity_external_ref'),
  ('entity_link'),
  ('entity_membership'),
  ('entity_search_term'),
  ('feed_discovery'),
  ('feed_discovery_entity'),
  ('feed_item'),
  ('feed_source'),
  ('follow_author_alias'),
  ('follow_item'),
  ('follow_playback'),
  ('follow_source'),
  ('genre_decision'),
  ('label_maker'),
  ('media_binding'),
  ('performer_profile'),
  ('playlist'),
  ('playlist_item'),
  ('profile'),
  ('provider_profile'),
  ('quest'),
  ('review_decision'),
  ('search_history'),
  ('source'),
  ('watch_queue');

CREATE TRIGGER rev_activity_event_insert AFTER INSERT ON activity_event BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='activity_event';
END;

CREATE TRIGGER rev_activity_event_update AFTER UPDATE ON activity_event BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='activity_event';
END;

CREATE TRIGGER rev_activity_event_delete AFTER DELETE ON activity_event BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='activity_event';
END;

CREATE TRIGGER rev_app_user_insert AFTER INSERT ON app_user BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='app_user';
END;

CREATE TRIGGER rev_app_user_update AFTER UPDATE ON app_user BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='app_user';
END;

CREATE TRIGGER rev_app_user_delete AFTER DELETE ON app_user BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='app_user';
END;

CREATE TRIGGER rev_asset_insert AFTER INSERT ON asset BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset';
END;

CREATE TRIGGER rev_asset_update AFTER UPDATE ON asset BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset';
END;

CREATE TRIGGER rev_asset_delete AFTER DELETE ON asset BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset';
END;

CREATE TRIGGER rev_asset_entity_insert AFTER INSERT ON asset_entity BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset_entity';
END;

CREATE TRIGGER rev_asset_entity_update AFTER UPDATE ON asset_entity BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset_entity';
END;

CREATE TRIGGER rev_asset_entity_delete AFTER DELETE ON asset_entity BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset_entity';
END;

CREATE TRIGGER rev_asset_preference_insert AFTER INSERT ON asset_preference BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset_preference';
END;

CREATE TRIGGER rev_asset_preference_update AFTER UPDATE ON asset_preference BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset_preference';
END;

CREATE TRIGGER rev_asset_preference_delete AFTER DELETE ON asset_preference BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset_preference';
END;

CREATE TRIGGER rev_asset_quality_goal_insert AFTER INSERT ON asset_quality_goal BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset_quality_goal';
END;

CREATE TRIGGER rev_asset_quality_goal_update AFTER UPDATE ON asset_quality_goal BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset_quality_goal';
END;

CREATE TRIGGER rev_asset_quality_goal_delete AFTER DELETE ON asset_quality_goal BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset_quality_goal';
END;

CREATE TRIGGER rev_asset_subtitle_insert AFTER INSERT ON asset_subtitle BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset_subtitle';
END;

CREATE TRIGGER rev_asset_subtitle_update AFTER UPDATE ON asset_subtitle BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset_subtitle';
END;

CREATE TRIGGER rev_asset_subtitle_delete AFTER DELETE ON asset_subtitle BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset_subtitle';
END;

CREATE TRIGGER rev_asset_tag_insert AFTER INSERT ON asset_tag BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset_tag';
END;

CREATE TRIGGER rev_asset_tag_update AFTER UPDATE ON asset_tag BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset_tag';
END;

CREATE TRIGGER rev_asset_tag_delete AFTER DELETE ON asset_tag BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset_tag';
END;

CREATE TRIGGER rev_asset_tag_preference_insert AFTER INSERT ON asset_tag_preference BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset_tag_preference';
END;

CREATE TRIGGER rev_asset_tag_preference_update AFTER UPDATE ON asset_tag_preference BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset_tag_preference';
END;

CREATE TRIGGER rev_asset_tag_preference_delete AFTER DELETE ON asset_tag_preference BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='asset_tag_preference';
END;

CREATE TRIGGER rev_code_sample_image_insert AFTER INSERT ON code_sample_image BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='code_sample_image';
END;

CREATE TRIGGER rev_code_sample_image_update AFTER UPDATE ON code_sample_image BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='code_sample_image';
END;

CREATE TRIGGER rev_code_sample_image_delete AFTER DELETE ON code_sample_image BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='code_sample_image';
END;

CREATE TRIGGER rev_entity_insert AFTER INSERT ON entity BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity';
END;

CREATE TRIGGER rev_entity_update AFTER UPDATE ON entity BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity';
END;

CREATE TRIGGER rev_entity_delete AFTER DELETE ON entity BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity';
END;

CREATE TRIGGER rev_entity_alias_insert AFTER INSERT ON entity_alias BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_alias';
END;

CREATE TRIGGER rev_entity_alias_update AFTER UPDATE ON entity_alias BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_alias';
END;

CREATE TRIGGER rev_entity_alias_delete AFTER DELETE ON entity_alias BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_alias';
END;

CREATE TRIGGER rev_entity_external_ref_insert AFTER INSERT ON entity_external_ref BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_external_ref';
END;

CREATE TRIGGER rev_entity_external_ref_update AFTER UPDATE ON entity_external_ref BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_external_ref';
END;

CREATE TRIGGER rev_entity_external_ref_delete AFTER DELETE ON entity_external_ref BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_external_ref';
END;

CREATE TRIGGER rev_entity_link_insert AFTER INSERT ON entity_link BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_link';
END;

CREATE TRIGGER rev_entity_link_update AFTER UPDATE ON entity_link BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_link';
END;

CREATE TRIGGER rev_entity_link_delete AFTER DELETE ON entity_link BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_link';
END;

CREATE TRIGGER rev_entity_membership_insert AFTER INSERT ON entity_membership BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_membership';
END;

CREATE TRIGGER rev_entity_membership_update AFTER UPDATE ON entity_membership BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_membership';
END;

CREATE TRIGGER rev_entity_membership_delete AFTER DELETE ON entity_membership BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_membership';
END;

CREATE TRIGGER rev_entity_search_term_insert AFTER INSERT ON entity_search_term BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_search_term';
END;

CREATE TRIGGER rev_entity_search_term_update AFTER UPDATE ON entity_search_term BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_search_term';
END;

CREATE TRIGGER rev_entity_search_term_delete AFTER DELETE ON entity_search_term BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='entity_search_term';
END;

CREATE TRIGGER rev_feed_discovery_insert AFTER INSERT ON feed_discovery BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='feed_discovery';
END;

CREATE TRIGGER rev_feed_discovery_update AFTER UPDATE ON feed_discovery BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='feed_discovery';
END;

CREATE TRIGGER rev_feed_discovery_delete AFTER DELETE ON feed_discovery BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='feed_discovery';
END;

CREATE TRIGGER rev_feed_discovery_entity_insert AFTER INSERT ON feed_discovery_entity BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='feed_discovery_entity';
END;

CREATE TRIGGER rev_feed_discovery_entity_update AFTER UPDATE ON feed_discovery_entity BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='feed_discovery_entity';
END;

CREATE TRIGGER rev_feed_discovery_entity_delete AFTER DELETE ON feed_discovery_entity BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='feed_discovery_entity';
END;

CREATE TRIGGER rev_feed_item_insert AFTER INSERT ON feed_item BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='feed_item';
END;

CREATE TRIGGER rev_feed_item_update AFTER UPDATE ON feed_item BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='feed_item';
END;

CREATE TRIGGER rev_feed_item_delete AFTER DELETE ON feed_item BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='feed_item';
END;

CREATE TRIGGER rev_feed_source_insert AFTER INSERT ON feed_source BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='feed_source';
END;

CREATE TRIGGER rev_feed_source_update AFTER UPDATE ON feed_source BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='feed_source';
END;

CREATE TRIGGER rev_feed_source_delete AFTER DELETE ON feed_source BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='feed_source';
END;

CREATE TRIGGER rev_follow_author_alias_insert AFTER INSERT ON follow_author_alias BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='follow_author_alias';
END;

CREATE TRIGGER rev_follow_author_alias_update AFTER UPDATE ON follow_author_alias BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='follow_author_alias';
END;

CREATE TRIGGER rev_follow_author_alias_delete AFTER DELETE ON follow_author_alias BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='follow_author_alias';
END;

CREATE TRIGGER rev_follow_item_insert AFTER INSERT ON follow_item BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='follow_item';
END;

CREATE TRIGGER rev_follow_item_update AFTER UPDATE ON follow_item BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='follow_item';
END;

CREATE TRIGGER rev_follow_item_delete AFTER DELETE ON follow_item BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='follow_item';
END;

CREATE TRIGGER rev_follow_playback_insert AFTER INSERT ON follow_playback BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='follow_playback';
END;

CREATE TRIGGER rev_follow_playback_update AFTER UPDATE ON follow_playback BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='follow_playback';
END;

CREATE TRIGGER rev_follow_playback_delete AFTER DELETE ON follow_playback BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='follow_playback';
END;

CREATE TRIGGER rev_follow_source_insert AFTER INSERT ON follow_source BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='follow_source';
END;

CREATE TRIGGER rev_follow_source_update AFTER UPDATE ON follow_source BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='follow_source';
END;

CREATE TRIGGER rev_follow_source_delete AFTER DELETE ON follow_source BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='follow_source';
END;

CREATE TRIGGER rev_genre_decision_insert AFTER INSERT ON genre_decision BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='genre_decision';
END;

CREATE TRIGGER rev_genre_decision_update AFTER UPDATE ON genre_decision BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='genre_decision';
END;

CREATE TRIGGER rev_genre_decision_delete AFTER DELETE ON genre_decision BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='genre_decision';
END;

CREATE TRIGGER rev_label_maker_insert AFTER INSERT ON label_maker BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='label_maker';
END;

CREATE TRIGGER rev_label_maker_update AFTER UPDATE ON label_maker BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='label_maker';
END;

CREATE TRIGGER rev_label_maker_delete AFTER DELETE ON label_maker BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='label_maker';
END;

CREATE TRIGGER rev_media_binding_insert AFTER INSERT ON media_binding BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='media_binding';
END;

CREATE TRIGGER rev_media_binding_update AFTER UPDATE ON media_binding BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='media_binding';
END;

CREATE TRIGGER rev_media_binding_delete AFTER DELETE ON media_binding BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='media_binding';
END;

CREATE TRIGGER rev_performer_profile_insert AFTER INSERT ON performer_profile BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='performer_profile';
END;

CREATE TRIGGER rev_performer_profile_update AFTER UPDATE ON performer_profile BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='performer_profile';
END;

CREATE TRIGGER rev_performer_profile_delete AFTER DELETE ON performer_profile BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='performer_profile';
END;

CREATE TRIGGER rev_playlist_insert AFTER INSERT ON playlist BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='playlist';
END;

CREATE TRIGGER rev_playlist_update AFTER UPDATE ON playlist BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='playlist';
END;

CREATE TRIGGER rev_playlist_delete AFTER DELETE ON playlist BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='playlist';
END;

CREATE TRIGGER rev_playlist_item_insert AFTER INSERT ON playlist_item BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='playlist_item';
END;

CREATE TRIGGER rev_playlist_item_update AFTER UPDATE ON playlist_item BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='playlist_item';
END;

CREATE TRIGGER rev_playlist_item_delete AFTER DELETE ON playlist_item BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='playlist_item';
END;

CREATE TRIGGER rev_profile_insert AFTER INSERT ON profile BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='profile';
END;

CREATE TRIGGER rev_profile_update AFTER UPDATE ON profile BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='profile';
END;

CREATE TRIGGER rev_profile_delete AFTER DELETE ON profile BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='profile';
END;

CREATE TRIGGER rev_provider_profile_insert AFTER INSERT ON provider_profile BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='provider_profile';
END;

CREATE TRIGGER rev_provider_profile_update AFTER UPDATE ON provider_profile BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='provider_profile';
END;

CREATE TRIGGER rev_provider_profile_delete AFTER DELETE ON provider_profile BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='provider_profile';
END;

CREATE TRIGGER rev_quest_insert AFTER INSERT ON quest BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='quest';
END;

CREATE TRIGGER rev_quest_update AFTER UPDATE ON quest BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='quest';
END;

CREATE TRIGGER rev_quest_delete AFTER DELETE ON quest BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='quest';
END;

CREATE TRIGGER rev_review_decision_insert AFTER INSERT ON review_decision BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='review_decision';
END;

CREATE TRIGGER rev_review_decision_update AFTER UPDATE ON review_decision BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='review_decision';
END;

CREATE TRIGGER rev_review_decision_delete AFTER DELETE ON review_decision BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='review_decision';
END;

CREATE TRIGGER rev_search_history_insert AFTER INSERT ON search_history BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='search_history';
END;

CREATE TRIGGER rev_search_history_update AFTER UPDATE ON search_history BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='search_history';
END;

CREATE TRIGGER rev_search_history_delete AFTER DELETE ON search_history BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='search_history';
END;

CREATE TRIGGER rev_source_insert AFTER INSERT ON source BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='source';
END;

CREATE TRIGGER rev_source_update AFTER UPDATE ON source BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='source';
END;

CREATE TRIGGER rev_source_delete AFTER DELETE ON source BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='source';
END;

CREATE TRIGGER rev_watch_queue_insert AFTER INSERT ON watch_queue BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='watch_queue';
END;

CREATE TRIGGER rev_watch_queue_update AFTER UPDATE ON watch_queue BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='watch_queue';
END;

CREATE TRIGGER rev_watch_queue_delete AFTER DELETE ON watch_queue BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='watch_queue';
END;
