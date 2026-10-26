"""页面读取已提交结果的轻量版本；任务心跳不参与资料失效。"""
from .review_csv import candidate_root_version
from .web_state import path_version


RESULT_TABLES = {
    'catalog': ('asset', 'asset_entity', 'asset_tag', 'asset_subtitle', 'entity', 'entity_alias',
                'entity_link', 'entity_external_ref', 'entity_membership', 'performer_profile', 'asset_quality_goal',
                'playlist', 'playlist_item'),
    'follow': ('follow_item', 'follow_source', 'follow_author_alias'),
    'discoveries': ('feed_source', 'feed_item', 'feed_discovery', 'feed_discovery_entity', 'watch_queue'),
    'review': ('review_decision', 'genre_decision'),
}


def result_versions(contract):
    """复用账本触发器和文件版本，跨线程、CLI 写入和进程重启均可比较。"""
    result = {key: str(contract.ledger_revision(tables)) for key, tables in RESULT_TABLES.items()}
    result['review'] += ':' + str(candidate_root_version(contract.candidate_root))
    result['artwork'] = ':'.join(str(path_version(root)) for root in (
        contract.cover_root, contract.avatar_root, contract.logo_root, contract.poster_root,
        contract.timeline_root))
    return result
