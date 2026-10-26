"""公司资料的公开字段、逐字段来源和填空写入。

厂牌与事务所资料保存在 entity.metadata_json.company_profile；不参与身份归并。
成立日期、品牌启动与运营公司各有字段。每格保存来源 URL、原文和批次，自动结果只填空。

观测结果只来自本模块的通用判据：概要表、定义列表与成对区块里的标签，以及服务条款里的
订立方原句。落库时按采集保存的原始页面重跑一遍（`replay`），重跑得不出的格不写；
页面上读不出、只能由人判断的集团归属与分工关系留作 `candidate`，不进入显示契约。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .http import public_https_url
from .kanji import fold_glyphs
from .social_links import name_key

KEY = 'company_profile'
FIELDS = ('legal_name', 'founded', 'launched', 'country', 'location', 'operator', 'parent', 'group', 'distributor')
LABELS = {
    '会社名': 'legal_name', '社名': 'legal_name', '商号': 'legal_name',
    '法人名': 'legal_name', 'companyname': 'legal_name',
    '設立': 'founded', '設立年月日': 'founded', '設立年月': 'founded',
    '創立': 'founded', '創業': 'founded', 'founded': 'founded',
    'ブランド設立': 'launched', 'レーベル設立': 'launched', 'ブランド開始': 'launched',
    '所在地': 'location', '本社所在地': 'location', '本店所在地': 'location',
    '住所': 'location', '本社': 'location', 'address': 'location', 'headquarters': 'location',
    '運営会社': 'operator', '運営法人': 'operator', 'operatedby': 'operator',
    '親会社': 'parent', 'parentcompany': 'parent',
    '国': 'country', 'country': 'country',
}
#: 法人名里的公司形态词：比名字时剥掉，`SODクリエイト株式会社` 与别名 `SODクリエイト` 同键。
CORPORATE_FORM = re.compile(r'株式会社|有限会社|合同会社|合資会社|合名会社|[（(]\s*[株有]\s*[)）]'
                            r'|\b(?:inc|llc|ltd|corp|co|kft|gmbh)\b\.?', re.I)
#: 英文服务条款开头订立方那一句：运营主体是括号或句号前的那一段。
TERMS_OPERATOR = re.compile(r'These terms of (?:service|use) are entered into between you and ([^.()]+?)\s*(?:\(|\.)', re.I)
COMPANY_LINK = re.compile(r'会社概要|企業情報|会社案内|特定商取引|about\s*(?:us)?|company\s*(?:profile)?|terms\s*(?:of|and|&)?\s*(?:service|use|conditions)|privacy\s*policy', re.I)
SOCIAL_HOSTS = {'x.com', 'twitter.com', 'instagram.com', 'youtube.com', 'tiktok.com', 'facebook.com', 'threads.net', 'threads.com'}


def _clean(value: str) -> str:
    return re.sub(r'\s+', ' ', value).strip()


def date_text(value: str) -> str | None:
    """只接受明确日期或年份；不从版权、周年数字或公司简介里猜成立年。"""
    value = value.strip()
    era = re.fullmatch(r'(昭和|平成|令和)(元|\d{1,2})年(.*)', value)
    if era:
        offset = {'昭和': 1925, '平成': 1988, '令和': 2018}[era[1]]
        value = f"{offset + (1 if era[2] == '元' else int(era[2]))}年{era[3]}"
    match = re.fullmatch(r'(18\d{2}|19\d{2}|20\d{2})(?:[年/.-](\d{1,2})(?:[月/.-](\d{1,2})日?)?月?)?(?:年)?', value)
    if not match:
        return None
    year, month, day = match.groups()
    try:
        datetime(int(year), int(month or 1), int(day or 1))
    except ValueError:
        return None
    return year + (f'-{int(month):02d}' if month else '') + (f'-{int(day):02d}' if day else '')


def _labelled_pairs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """公司概要中的两列表格、定义列表与成对区块。"""
    pairs = []
    for row in soup.select('tr'):
        cells = row.find_all(['th', 'td'], recursive=False)
        if len(cells) == 2:
            pairs.append((cells[0].get_text(' ', strip=True), cells[1].get_text(' ', strip=True)))
    for label in soup.select('dt'):
        value = label.find_next_sibling('dd')
        if value:
            pairs.append((label.get_text(' ', strip=True), value.get_text(' ', strip=True)))
    for row in soup.select('div, li'):
        cells = row.find_all(['div', 'span', 'p'], recursive=False)
        if len(cells) == 2 and re.sub(r'[\s:：]', '', cells[0].get_text()).casefold() in LABELS:
            pairs.append((cells[0].get_text(' ', strip=True), cells[1].get_text(' ', strip=True)))
    return pairs


def _page_links(soup: BeautifulSoup, source_url: str) -> tuple[list, list]:
    """同站公司资料入口与社媒候选；候选账号需要独立核对归属。"""
    pages, socials = [], []
    host = (urlsplit(source_url).hostname or '').removeprefix('www.')
    for anchor in soup.select('a[href]'):
        target = urljoin(source_url, str(anchor['href']))
        target_host = (urlsplit(target).hostname or '').removeprefix('www.')
        text = _clean(anchor.get_text(' ', strip=True))
        if public_https_url(target) and target_host == host and len(text) < 65 and COMPANY_LINK.search(text):
            if target not in pages:
                pages.append(target)
        if public_https_url(target) and target_host in SOCIAL_HOSTS:
            path = urlsplit(target).path.strip('/')
            if path and not re.search(r'(?:^|/)(?:intent|share|sharer|status|watch|search|embed)(?:/|$)', path):
                socials.append({'url': target, 'label': text, 'source_url': source_url})
    return pages[:3], socials


def extract(html: str, source_url: str) -> dict:
    """读取官网明确标注的字段，歧义字段留在 conflicts 中。"""
    soup = BeautifulSoup(html, 'html.parser')
    facts, conflicts = {}, []
    for label, raw in _labelled_pairs(soup):
        key = LABELS.get(re.sub(r'[\s:：]', '', label).casefold())
        raw = _clean(raw)
        if not key or not raw or len(raw) > 240:
            continue
        value = date_text(raw) if key in {'founded', 'launched'} else raw
        if key == 'location':
            value = re.split(r'\s*(?:TEL\s*[:：]|FAX\s*[:：]|■|丸ノ内線)', raw, maxsplit=1, flags=re.I)[0].strip()
        if not value:
            continue
        fact = {'value': value, 'source_url': source_url, 'evidence': f'{_clean(label)}：{raw}', 'status': 'observed'}
        if key in facts and facts[key]['value'] != value:
            conflicts.append({'field': key, 'values': [facts.pop(key), fact]})
        elif not any(item['field'] == key for item in conflicts):
            facts[key] = fact
    pages, socials = _page_links(soup, source_url)
    return {'facts': facts, 'conflicts': conflicts, 'company_pages': pages, 'socials': socials}


def company_key(name: str) -> str:
    """比法人名与实体名的键：剥掉公司形态词，再按全半角、空白、大小写与字形折叠。"""
    return fold_glyphs(name_key(CORPORATE_FORM.sub(' ', str(name or ''))))


def _table_legal_names(table) -> list[str]:
    return [_clean(value) for label, value in _labelled_pairs(table)
            if LABELS.get(re.sub(r'[\s:：]', '', label).casefold()) == 'legal_name']


def extract_for_entity(html: str, source_url: str, names: list[str]) -> dict:
    """一页列着几家公司的概要表时，只读法人名对得上这个实体名字的那一张。

    法人名剥掉公司形态词后要与实体的规范名或别名相等；恰好一张对上才读，对不上或
    对上多张时整页不出事实，避免把集团里另一家公司的成立日期记到这里。
    """
    soup = BeautifulSoup(html, 'html.parser')
    tables = [table for table in soup.select('table') if _table_legal_names(table)]
    if len(tables) < 2:
        return extract(html, source_url)
    keys = {company_key(name) for name in names} - {''}
    own = [table for table in tables if any(company_key(value) in keys for value in _table_legal_names(table))]
    if len(own) == 1:
        parsed = extract(str(own[0]), source_url)
        parsed['company_pages'], parsed['socials'] = _page_links(soup, source_url)
        return parsed
    pages, socials = _page_links(soup, source_url)
    return {'facts': {}, 'conflicts': [], 'company_pages': pages, 'socials': socials}


def terms_facts(html: str, source_url: str) -> dict:
    """服务条款页里「与你订立本条款的是某公司」那一句：运营主体取自原句。"""
    if 'terms' not in urlsplit(source_url).path.casefold():
        return {}
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup(['script', 'style']):
        node.decompose()
    match = TERMS_OPERATOR.search(_clean(soup.get_text(' ', strip=True)))
    if not match or not match[1].strip():
        return {}
    return {'operator': {'value': match[1].strip(), 'source_url': source_url,
                         'evidence': match[0], 'status': 'observed'}}


def page_facts(html: str, source_url: str, names: list[str]) -> dict:
    """一张官网页面上由代码判据读出的全部事实、歧义与后续页面。"""
    parsed = extract_for_entity(html, source_url, names)
    for key, fact in terms_facts(html, source_url).items():
        held = parsed['facts'].get(key)
        if held and held['value'] != fact['value']:
            parsed['conflicts'].append({'field': key, 'values': [parsed['facts'].pop(key), fact]})
        elif not any(item['field'] == key for item in parsed['conflicts']):
            parsed['facts'][key] = fact
    return parsed


def merge_page(result: dict, parsed: dict) -> None:
    """把一页的结论并进这一家：同一字段在两页上说法不同就记成歧义。"""
    for key, fact in parsed['facts'].items():
        held = result['facts'].get(key)
        if held and held['value'] != fact['value']:
            result['conflicts'].append({'field': key, 'values': [held, fact]})
        else:
            result['facts'][key] = fact
    result['conflicts'].extend(parsed['conflicts'])


def settle(result: dict) -> None:
    """有歧义的字段整格不出。"""
    for conflict in result['conflicts']:
        result['facts'].pop(conflict['field'], None)


def site_host(url: str) -> str:
    return (urlsplit(url).hostname or '').casefold().removeprefix('www.')


def replay(pages: list[dict], names: list[str], hosts: set[str]) -> dict:
    """按采集时保存的原始页面重跑判据；只有这里得出的事实才算观测结果。

    只读采集器自己取回的页面：HTTP 200、落在这家已登记官网的主机上。清单里另外
    添进来的第三方页面（工商登记、新闻稿站）不算。
    """
    result = {'facts': {}, 'conflicts': []}
    for page in pages:
        cache, final = page.get('cache'), page.get('final_url') or page.get('url')
        if not cache or not final or page.get('http_status') != 200 or site_host(final) not in hosts:
            continue
        try:
            html = Path(cache).read_text(encoding='utf-8')
        except OSError:
            continue
        merge_page(result, page_facts(html, final, names))
    settle(result)
    return result


def entity_names(connection: sqlite3.Connection, entity_id: int) -> list[str] | None:
    """公司实体的规范名与别名；实体不在或不是厂牌、事务所时返回 None。"""
    row = connection.execute('SELECT kind,canonical_name FROM entity WHERE id=?', (entity_id,)).fetchone()
    if not row or row[0] not in {'studio', 'agency'}:
        return None
    return [str(row[1]), *(str(alias) for (alias,) in connection.execute(
        'SELECT alias FROM entity_alias WHERE entity_id=? ORDER BY alias', (entity_id,)))]


def official_hosts(connection: sqlite3.Connection, entity_id: int, *, skip_batch: str = '') -> set[str]:
    """这家已登记官网的主机（去掉 `www.`）；存档地址与 `skip_batch` 这一批写下的不算。"""
    hosts = set()
    for url, raw in connection.execute(
            "SELECT url,metadata_json FROM entity_link WHERE entity_id=? AND link_kind='official'", (entity_id,)):
        try:
            batch = json.loads(raw or '{}').get('batch')
        except ValueError:
            batch = None
        if 'web.archive.org' not in url and not (skip_batch and batch == skip_batch):
            hosts.add(site_host(url))
    return hosts - {''}


def public_profile(metadata: dict) -> dict:
    """API 的值与逐字段来源；未知字段不进入显示契约。"""
    profile = metadata.get(KEY)
    if not isinstance(profile, dict):
        return {}
    return {key: dict(value) for key, value in profile.items()
            if key in FIELDS and isinstance(value, dict) and isinstance(value.get('value'), str)
            and value.get('status') != 'candidate'
            and value['value'].strip() and public_https_url(str(value.get('source_url', '')))}


def fill(connection: sqlite3.Connection, entity_id: int, facts: dict, *, source: str, batch: str) -> dict:
    """给公司资料填空，保留其他元信息和既有字段；候选格可由观测结果接替。调用方负责事务。"""
    row = connection.execute('SELECT kind,metadata_json FROM entity WHERE id=?', (entity_id,)).fetchone()
    if row is None or row[0] not in {'studio', 'agency'}:
        return {'written': [], 'conflicts': []}
    metadata = json.loads(row[1] or '{}')
    held = metadata.get(KEY, {})
    if not isinstance(held, dict):
        return {'written': [], 'conflicts': ['company_profile']}
    written, conflicts = [], []
    now = datetime.now(timezone.utc).isoformat()
    for key, fact in public_profile({KEY: facts}).items():
        if key in held and not (isinstance(held[key], dict) and held[key].get('status') == 'candidate'):
            if held[key] != fact and (not isinstance(held[key], dict) or held[key].get('value') != fact['value']):
                conflicts.append(key)
            continue
        held[key] = {**fact, 'source': source, 'batch': batch, 'fetched_at': now}
        written.append(key)
    if written:
        metadata[KEY] = held
        connection.execute('UPDATE entity SET metadata_json=?,updated_at=? WHERE id=?',
                           (json.dumps(metadata, ensure_ascii=False), now, entity_id))
    return {'written': written, 'conflicts': conflicts}


def planned_revert(connection: sqlite3.Connection, source: str, batch: str) -> list[dict]:
    """只列这批拥有的公司资料格（含已降为候选的格），其他元信息不受影响。"""
    found = []
    for row in connection.execute("SELECT id,canonical_name,metadata_json FROM entity WHERE kind IN ('studio','agency')"):
        profile = json.loads(row[2] or '{}').get(KEY)
        fields = [key for key, fact in (profile.items() if isinstance(profile, dict) else ())
                  if isinstance(fact, dict) and fact.get('source') == source
                  and (not batch or fact.get('batch') == batch)]
        if fields:
            found.append({'entity_id': row[0], 'entity': row[1], 'fields': fields})
    return found


def revert(connection: sqlite3.Connection, rows: list[dict]) -> int:
    """撤回已列出的字段，保留公司的其他资料。"""
    count = 0
    for item in rows:
        raw = connection.execute('SELECT metadata_json FROM entity WHERE id=?', (item['entity_id'],)).fetchone()
        metadata = json.loads(raw[0] or '{}')
        for key in item['fields']:
            metadata[KEY].pop(key, None)
            count += 1
        if not metadata[KEY]:
            metadata.pop(KEY)
        connection.execute('UPDATE entity SET metadata_json=? WHERE id=?', (json.dumps(metadata, ensure_ascii=False), item['entity_id']))
    return count
