"""单人部署使用的本机用户标识。"""
import json

DEFAULT_PROFILE_ID = 'local-default'


def read_settings(connection):
    """读取当前 profile 的设置对象，不改变存储。"""
    row = connection.execute("SELECT settings_json FROM profile WHERE id=?", (DEFAULT_PROFILE_ID,)).fetchone()
    if row is None:
        return {}
    try:
        value = json.loads(row[0] or "{}")
    except (ValueError, TypeError):
        value = {}
    return value if isinstance(value, dict) else {}
