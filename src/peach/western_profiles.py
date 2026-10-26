"""Babepedia 档案资料、别名和本人链接；保留原始页面字段。"""
from __future__ import annotations

import re
import json
from datetime import date
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from .http import public_https_url
from .western_artwork import babepedia_page
from .performer_profiles import read_profile, write_profile
from .performer_alias_followup import land as land_aliases
from .social_links import canonical_url, handle, host_of, platform

MONTHS = {name: i for i, name in enumerate(('January', 'February', 'March', 'April', 'May',
    'June', 'July', 'August', 'September', 'October', 'November', 'December'), 1)}
LINK_NAMES = {'www': '官网', 'instagram': 'Instagram', 'x': 'X', 'tiktok': 'TikTok',
    'onlyfans': 'OnlyFans', 'onlyfansfree': 'OnlyFans', 'twitch': 'Twitch',
    'imdb': 'IMDb', 'iafd': 'IAFD', 'facebook': 'Facebook', 'youtube': 'YouTube'}


def profile(http, name: str, aliases=(), *, profile_url='') -> dict:
    """明确名字匹配后解析资料；缺失与无法解析的值保持空。"""
    soup, url, found, names = babepedia_page(http, name, aliases, profile_url=profile_url)
    fields = {}
    for item in soup.select('#personal-info-block .info-item'):
        label, value = item.select_one('.label'), item.select_one('.value')
        if label is not None and value is not None:
            key = label.get_text(' ', strip=True).rstrip(':')
            fields[key] = value.get_text(' ', strip=True)
    result = {'raw': {**fields, '名前': found, '別名': names}, 'tags': []}
    born = re.search(r'(\d{1,2})(?:st|nd|rd|th) of ([A-Za-z]+) (\d{4})', fields.get('Born', ''))
    if born and born[2] in MONTHS:
        try:
            result['birth_date'] = date(int(born[3]), MONTHS[born[2]], int(born[1])).isoformat()
        except ValueError:
            pass
    height = re.search(r'(\d{2,3})\s*cm\b', fields.get('Height', ''))
    if height:
        result['height_cm'] = int(height[1])
    sizes = re.match(r'(\d+(?:\.\d+)?)\s*[–-]\s*(\d+(?:\.\d+)?)\s*[–-]\s*(\d+(?:\.\d+)?)\s*(in|cm)\b', fields.get('Measurements', ''))
    if sizes:
        for key, number in zip(('bust_cm', 'waist_cm', 'hip_cm'), sizes.groups()[:3]):
            result[key] = round(float(number) * (2.54 if sizes[4] == 'in' else 1))
    # 采用页面明示的日本尺码，避免把英美罩杯当成日本罩杯。
    cup = re.search(r'\bJP:\s*\d+([A-Z]+)', fields.get('Bra/cup size', ''))
    if cup:
        result['cup'] = cup[1]
    # `2014 - 2017, 2019 - present` 是几段：出道取第一段的起点，引退取最后一段的终点。
    spans = re.findall(r'(\d{4})\s*[-–]\s*(present|\d{4})\b', fields.get('Years active', ''))
    if spans:
        result['debut_year'] = int(spans[0][0])
        if spans[-1][1] != 'present':
            result['active_until'] = int(spans[-1][1])
    birthplace = fields.get('Birthplace', '')
    if birthplace:
        result['birthplace'] = re.sub(r'\s*\(#\d+\)', '', birthplace).strip()
    links = []
    for anchor in soup.select('#socialicons a[href]'):
        target = urljoin(url, str(anchor['href']))
        icon = next((key for key in anchor.get('class', []) if key in LINK_NAMES), '')
        if not icon or not public_https_url(target):
            continue
        links.append({'url': target, 'label': LINK_NAMES[icon],
                      'link_kind': 'official' if icon == 'www' else
                      ('catalog' if icon in {'iafd', 'imdb'} else 'social')})
        if icon == 'www':
            result.setdefault('site_url', target)
    return {'matched_name': found, 'aliases': names, 'profile_url': url,
            'profile': result, 'links': links}


def land(connection, entity_id: int, expected_name: str, record: dict, *, batch: str) -> dict:
    """仅补空资料，别名走同名占用判据，现有链接保留。调用方持有事务。"""
    current = connection.execute('SELECT kind,canonical_name FROM entity WHERE id=?', (entity_id,)).fetchone()
    if not current or current[0] not in ('performer', 'creator') or current[1] != expected_name:
        raise ValueError('艺人身份已变化，请重新预览')
    held = read_profile(connection, entity_id)
    written = False
    if held is None:
        written = write_profile(connection, entity_id, record['profile'], source=batch,
                                source_url=record['profile_url'])
    aliases = land_aliases(connection, entity_id, expected_name, 'Babepedia', record['profile_url'],
                          [record['matched_name'], *record['aliases']], batch, allow_latin=True,
                          allow_creator=True)
    added, conflicts = [], []
    stamp = datetime.now(timezone.utc).isoformat()
    for link in record['links']:
        url = canonical_url(link['url'])
        existing = connection.execute('SELECT link_kind,url FROM entity_link WHERE entity_id=?', (entity_id,)).fetchall()
        if any(canonical_url(held) == url for _kind, held in existing):
            continue
        reason = link_conflict(link['link_kind'], url, existing)
        if reason:
            conflicts.append({'url': url, 'link_kind': link['link_kind'], 'reason': reason})
            continue
        metadata = json.dumps({'source':batch.split('@', 1)[0], 'batch':batch,
                               'source_url':record['profile_url']}, ensure_ascii=False)
        cursor = connection.execute('INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
            (entity_id, link['link_kind'], link['label'], url, urlsplit(url).hostname, metadata,stamp,stamp))
        added.append(cursor.lastrowid)
    return {'entity_id':entity_id,'name':expected_name,'profile_written':written,
            'preserved_profile':held is not None,'aliases':aliases,'added_links':added,
            'link_conflicts':conflicts,'source':batch}


def link_conflict(link_kind: str, url: str, existing: list) -> str:
    """资料页给的链接与账本已有链接说法不一时的原因；不冲突返回空串。

    已有官网就不再添第二个官网；同一平台已登记另一个账号、同一目录站已登记另一页，
    都是两处来源说法不一，留在回执里等人看。
    """
    if link_kind == 'official' and any(kind == 'official' for kind, _url in existing):
        return '已有官网'
    site = platform(url)
    for _kind, held in existing:
        if site and platform(held) == site and handle(held) != handle(url):
            return f'{site} 已登记另一个账号'
        if not site and link_kind != 'official' and host_of(held) == host_of(url):
            return f'{host_of(url)} 已登记另一页'
    return ''
