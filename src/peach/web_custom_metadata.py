"""配置式元数据来源的管理与只读试查。"""
from . import custom_metadata


def q_custom_metadata(contract, args):
    return {'items': custom_metadata.load(contract.follow_secrets_root)}


def w_custom_metadata(contract, body):
    if body.get('action') == 'preview':
        record = custom_metadata.query(body, str(body.get('code', '')), secrets_root=contract.follow_secrets_root)
        return {'record': record.payload(), 'source': record.source, 'confidence': record.confidence}
    return {'items': custom_metadata.save(contract.follow_secrets_root, body)}
