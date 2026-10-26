"""关注来源归到哪位作者、这位作者叫什么、名片里哪几条是本人账号。

关注页的作者分组（`web_follow`）和关注作者建档（`follow_creators`，ADR-0096）共用这一套判定，
所以它放在 web 层之外。
"""
from __future__ import annotations

import json
import urllib.parse

from .follow_avatar import MAX_PROFILE_IDENTITIES, profile_identities
from .follow_sources import KemonoConnector
from .follow_store import author_display_text, normalized_author_name


def author_display_name(row) -> str:
    """一条追更来源上那个可读的作者拼写。

    名字怎么算「同一个人」由 `follow_store` 定义（那是别名表的主键口径）；
    这里只决定「这一行显示哪个字段」。
    """
    if row["entity_id"] and row["entity_name"]:
        return str(row["entity_name"])
    return author_display_text(row["label"] or row["ref"] or "",
                               provider=str(row["provider"] or ""))


def source_metadata(row) -> dict:
    try:
        payload = json.loads(row["metadata_json"] or "{}")
    except (KeyError, TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def author_key(row, aliases: dict[str, str] | None = None) -> str:
    """把一条来源归到「哪个作者」。

    这跟 ADR-0019 的变体分组**不是同一个轴**：那个是同一条发布的多个变体，
    这个是同一个作者在不同站点上的多条来源。用户在 Kemono 和 Pawchive 上关注的
    `LazyProcrastinator · fanbox`、在 Rule34Video 和 Rule34.xxx 上关注的
    `lazyprocrastinator`，是四条来源、一个人。

    实体已经绑上就用实体 id——那是规范身份，比名字可靠。没绑才退回名字归一化：
    去掉「· 服务名」后缀，再去掉大小写、空格、连字符这些不影响身份的噪声。
    归一化只做到这一步，不做模糊匹配：把两个碰巧相似的名字并成一个人，
    比让用户自己看到两行严重得多。
    """
    entity = row["entity_id"]
    if entity:
        return f"entity:{entity}"
    return name_key(row, aliases)


def name_key(row, aliases: dict[str, str] | None = None) -> str:
    """这条来源不看实体绑定时归到哪个作者：`name:` 加归一化的名字，名字都没有就按来源。

    关注作者建档（ADR-0096）靠它认出「新加的这条来源和已经建档的那几条本来就是同一组」。
    """
    recorded = str(source_metadata(row).get("author_key") or "").strip()
    if recorded:
        normalized = recorded
    else:
        label = str(row["label"] or row["ref"] or "")
        normalized = normalized_author_name(label, provider=str(row["provider"] or ""))
    if normalized:
        normalized = (aliases or {}).get(normalized, normalized)
        return f"name:{normalized}"
    return f"source:{row['id']}"


def official_fanbox_identity(metadata: dict) -> str:
    """名片链接里的 FANBOX 创作者 id，没有就回空串。

    它直接就是 `creator.get` 的参数，一个请求到头像，所以单独走一格。pixiv 的数字
    id 不在这里：有 pixiv 不等于开了 FANBOX，由 `official_profile_identities` 和
    X、Patreon 并排试。SubscribeStar 没有不带凭据就能读的头像接口，**未取得**，
    只当身份证据用。
    """
    links = metadata.get("official_links")
    if not isinstance(links, list):
        return ""
    handles = {str(link.get("service") or ""): str(link.get("handle") or "")
               for link in links if isinstance(link, dict)}
    return handles.get("fanbox") or ""


def official_profile_identities(metadata: dict) -> str:
    """名片上 X、Patreon 与 pixiv 的手柄，拼成 `/follow-avatar?service=profile` 的 id。

    几家都交给服务端，由它各取最大一档再留像素最多的那张；形状不合法的那条直接略过。
    """
    links = metadata.get("official_links")
    pairs = [f"{link.get('service')}:{link.get('handle')}"
             for link in (links if isinstance(links, list) else ())
             if isinstance(link, dict)]
    usable = [pair for pair in dict.fromkeys(pairs) if profile_identities(pair)]
    return ",".join(usable[:MAX_PROFILE_IDENTITIES])


def trusted_profile_links(row) -> list[dict]:
    """这条来源名片里能当作者本人账号的那几条。

    F95 的名片取自开楼正文，是作者自己贴的。booru 的 `official_links` 是作品出处，
    合作作品会指向另一位作者，所以只留手柄就是这条来源作者名的那几条。
    """
    metadata = source_metadata(row)
    raw = metadata.get("official_links")
    links = [link for link in (raw if isinstance(raw, list) else ()) if isinstance(link, dict)]
    if str(row["provider"] or "") == "f95zone":
        return links
    expected = {
        normalized_author_name(str(row["ref"] or "")),
        normalized_author_name(str(metadata.get("author_key") or "")),
    }
    return [link for link in links
            if normalized_author_name(str(link.get("handle") or "")) in expected]


def official_avatar_url(row) -> str | None:
    """Local resolver for an avatar from the creator's official profile.

    FANBOX archive refs carry the Pixiv user id, which is enough for Peach's fixed-host
    resolver to locate the public FANBOX profile and its official ``user.iconUrl``.
    A forum source has no such ref, so it goes through the profile links parsed out of
    the opening post instead.  Services without a verified resolver keep the archive
    fallback, and sources with neither fall back to the author initial.
    """
    provider = str(row["provider"] or "")
    if provider in KemonoConnector.HOSTS:
        service, _, user = str(row["ref"] or "").partition("/")
        if service != "fanbox" or not user.isdigit():
            return None
        return "/follow-avatar?" + urllib.parse.urlencode(
            {"service": service, "id": user})
    metadata = {**source_metadata(row), "official_links": trusted_profile_links(row)}
    identity = official_fanbox_identity(metadata)
    if identity:
        return "/follow-avatar?" + urllib.parse.urlencode(
            {"service": "fanbox", "id": identity})
    profiles = official_profile_identities(metadata)
    if not profiles:
        return None
    return "/follow-avatar?" + urllib.parse.urlencode(
        {"service": "profile", "id": profiles})


def creator_avatar_refs(contract) -> dict:
    """按已绑定实体汇总官方与归档头像，本机代理处理实际取图。"""
    from .follow_assets import mirror_avatar_url

    def read():
        with contract.read_connection() as connection:
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='follow_source'").fetchone():
                return {}
            sources = connection.execute(
                "SELECT s.* FROM follow_source s JOIN entity e ON e.id=s.entity_id "
                "WHERE e.kind='creator' ORDER BY s.provider,s.ref").fetchall()
        groups = {}
        for row in sources:
            urls = groups.setdefault(row['entity_id'], [[], []])
            official = official_avatar_url(row)
            if official:
                urls[0].append(official)
            if mirror_avatar_url(row['provider'], row['ref']):
                urls[1].append('/follow-avatar?' + urllib.parse.urlencode(
                    {'provider': row['provider'], 'ref': row['ref']}))
        return {identity: list(dict.fromkeys(official + archive))
                for identity, (official, archive) in groups.items()}

    return contract.cached('creator_avatar_refs', read)


def author_name(rows, key: str, canonical: dict[str, str]) -> str:
    """一位作者（同一作者键下的几条来源）叫什么。

    次序跟关注页那一份分组标题一致：实体名最可靠，其次是别名表定的规范名，再次是有
    官方主页那条来源的写法；都没有才在各条标签里选大写最多的那个——
    `LazyProcrastinator` 比 `lazyprocrastinator` 更像作者自己写的名字。
    """
    entity = next((str(row["entity_name"]) for row in rows
                   if row["entity_id"] and row["entity_name"]), "")
    labels = [(name, bool(official_avatar_url(row))) for row in rows
              if (name := author_display_name(row))]
    official = next((name for name, has_official in labels if has_official), "")
    best = max((name for name, _ in labels),
               key=lambda text: sum(ch.isupper() for ch in text), default="")
    return entity or canonical.get(key) or official or best or key
