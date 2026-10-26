"""受限 JSON 来源：固定 HTTPS 端点、字段映射及候选身份核对。"""
from __future__ import annotations

import ipaddress
import hashlib
import json
import re
import secrets
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from curl_cffi import CurlOpt, ffi, lib, requests
from filelock import FileLock

from .fsutil import atomic_write_text
from .http import host_addresses, public_https_url
from .metadata import identifies_code, validate_provider_code
from .metadata_routes import CONTENT_TYPES
from .sources.base import SiteRecord

FIELDS = ('code', 'title', 'studio', 'series', 'release_date', 'performers', 'tags')
MAX_BYTES = 1024 * 1024
MAX_SOURCES = 12
_PATH = re.compile(r'[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*|\.\d+){0,7}')
_ID = re.compile(r'custom-[a-f0-9]{16}')


def validate(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError('来源配置必须是对象')
    name = str(raw.get('name', '')).strip()
    endpoint = str(raw.get('endpoint', '')).strip()
    if not name or len(name) > 80:
        raise ValueError('来源名称需要 1–80 个字符')
    if endpoint.count('{code}') != 1 or '{' in endpoint.replace('{code}', '') or '}' in endpoint.replace('{code}', ''):
        raise ValueError('接口地址需要包含一个 {code}')
    parsed = urlsplit(endpoint.replace('{code}', 'ABW-001'))
    if (not public_https_url(endpoint.replace('{code}', 'ABW-001')) or parsed.port not in (None, 443)
            or not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', parsed.hostname or '')
            or parsed.fragment or '{code}' in endpoint.split('://', 1)[-1].split('/', 1)[0]
            or len(endpoint) > 2048 or any(ord(c) < 33 for c in endpoint)):
        raise ValueError('接口需要使用固定公网域名的 HTTPS 地址，端口为 443')
    mapping = raw.get('fields')
    if not isinstance(mapping, dict) or not mapping.get('code'):
        raise ValueError('请填写响应中番号字段的路径')
    if set(mapping) - set(FIELDS) or any(not isinstance(v, str) or not _PATH.fullmatch(v) for v in mapping.values()):
        raise ValueError('字段映射只接受对象键或数组下标组成的点路径')
    kinds = raw.get('content_types', [])
    if not isinstance(kinds, list) or not kinds or any(kind not in CONTENT_TYPES for kind in kinds):
        raise ValueError('至少选择一种有效内容类型')
    if not isinstance(raw.get('enabled', False), bool):
        raise ValueError('启用状态必须为布尔值')
    network = raw.get('network', 'direct')
    if network not in ('direct', 'peach_proxy'):
        raise ValueError('请选择直连或 Peach 代理')
    code_format = raw.get('code_format', 'original')
    if code_format not in ('original', 'lowercase', 'compact', 'underscore'):
        raise ValueError('番号格式无效')
    return dict(name=name, endpoint=endpoint, fields=dict(mapping),
                content_types=list(dict.fromkeys(kinds)), enabled=raw.get('enabled', False), network=network,
                code_format=code_format)


def endpoint(config: dict, code: str) -> str:
    shape = config.get('code_format', 'original')
    value = code.lower() if shape == 'lowercase' else code.replace('-', '') if shape == 'compact' else (
        code.replace('-', '_') if shape == 'underscore' else code)
    return config['endpoint'].replace('{code}', quote(value, safe=''))


def source_key(config: dict) -> str:
    """查询规则的指纹隔离候选、成功缓存和缺失记忆。"""
    fields = {key: config.get(key) for key in ('endpoint', 'fields', 'code_format', 'network')}
    version = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:12]
    return f"{config['id']}-{version}"


def load(root: Path) -> list[dict]:
    path = Path(root) / 'custom-metadata.json'
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(rows, list) or len(rows) > MAX_SOURCES:
        raise ValueError('自定义来源配置无效')
    return [dict(validate(row), id=row['id']) for row in rows if _ID.fullmatch(str(row.get('id', '')))]


def save(root: Path, body: dict) -> list[dict]:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(str(root / 'custom-metadata.lock')):
        rows = load(root)
        identity = str(body.get('id', ''))
        if identity and not any(row['id'] == identity for row in rows):
            raise ValueError('来源不存在，请刷新')
        if body.get('action') == 'delete':
            if not identity:
                raise ValueError('请选择来源')
            rows = [row for row in rows if row['id'] != identity]
        else:
            incoming = dict(validate(body), id=identity or 'custom-' + secrets.token_hex(8))
            rows = [incoming if row['id'] == identity else row for row in rows]
            if not identity:
                rows.append(incoming)
            if len(rows) > MAX_SOURCES:
                raise ValueError(f'最多保存 {MAX_SOURCES} 个来源')
        atomic_write_text(root / 'custom-metadata.json', json.dumps(rows, ensure_ascii=False))
        return rows


def _value(document, path):
    for key in path.split('.'):
        if isinstance(document, dict):
            document = document.get(key)
        elif isinstance(document, list) and key.isdecimal() and int(key) < len(document):
            document = document[int(key)]
        else:
            return None
    return document


def parse(config: dict, code: str, document: object) -> SiteRecord:
    config = dict(validate(config), id=config.get('id', 'custom-preview'))
    values = {field: _value(document, path) for field, path in config['fields'].items()}
    def text(field):
        value = values.get(field)
        if value is None:
            return ''
        if not isinstance(value, (str, int, float)) or isinstance(value, bool) or len(str(value)) > 4096:
            raise ValueError(f'{field} 字段需要文本或数值')
        return str(value).strip()
    def strings(field):
        value = values.get(field)
        if value is None:
            return ()
        if not isinstance(value, list) or len(value) > 100 or any(not isinstance(v, str) or len(v) > 256 for v in value):
            raise ValueError(f'{field} 字段需要文本数组')
        return tuple(dict.fromkeys(v.strip() for v in value if v.strip()))
    returned_code = text('code')
    # 身份只核对响应字段，查询 URL 不能替来源证明自己答对了。
    if not returned_code or not identifies_code(code, {'id': returned_code}):
        raise ValueError('来源返回的番号与查询番号不一致')
    return SiteRecord(source=config['id'], provenance='custom-json', code=returned_code,
                      source_url=endpoint(config, code),
                      title=text('title'), studio=text('studio'), series=text('series'),
                      release_date=text('release_date'),
                      performers=tuple({'japanese_name': name} for name in strings('performers')),
                      tags=strings('tags') if 'tags' in values else None, confidence=0.5)


def _remaining_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('来源查询已超时')
    return remaining


def fetch_json(url: str, timeout: float, *, client_options=None):
    expires = time.monotonic() + timeout
    _remaining_seconds(expires)
    host = urlsplit(url).hostname or ''
    addresses = host_addresses(host)
    _remaining_seconds(expires)
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError('来源域名必须解析到公网地址')
    address = addresses[0]
    if ':' in address:
        address = f'[{address}]'
    # CONNECT_TO 固定已验证地址，HTTP 代理也只 CONNECT 到该公网 IP，TLS 仍校验原域名。
    options = dict(client_options or {'trust_env': False})
    # curl_cffi 0.16.3 的 CONNECT_TO 接收原生 slist；Session 关闭后才释放。
    target = ffi.gc(lib.curl_slist_append(ffi.NULL, f'{host}:443:{address}:443'.encode()), lib.curl_slist_free_all)
    curl_options = {CurlOpt.CONNECT_TO: target}
    if not options.get('proxy') and not options.get('trust_env'):
        curl_options[CurlOpt.NOPROXY] = '*'
    with requests.Session(curl_options=curl_options, **options) as client:
        try:
            with client.stream('GET', url, timeout=_remaining_seconds(expires), allow_redirects=False,
                               headers={'Accept': 'application/json'}) as response:
                if response.status_code != 200:
                    raise ValueError(f'来源响应 HTTP {response.status_code}')
                chunks, total = [], 0
                for chunk in response.iter_content():
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError('来源响应超过 1 MiB')
                    chunks.append(chunk)
                return json.loads(b''.join(chunks))
        except requests.exceptions.RequestException as error:
            raise ValueError('来源连接失败') from error


def query(config: dict, code: str, *, deadline=None, secrets_root=None) -> SiteRecord:
    checked = validate(config)
    code = validate_provider_code(code)
    expires = time.monotonic() + 15.0
    if deadline is not None:
        expires = min(expires, deadline)
    _remaining_seconds(expires)
    url = endpoint(checked, code)
    options = None
    if checked['network'] == 'peach_proxy':
        if secrets_root is None:
            raise ValueError('Peach 代理配置未取得')
        from .peach_proxy import client_options
        options = client_options(Path(secrets_root))
    try:
        document = fetch_json(url, _remaining_seconds(expires), client_options=options)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError('来源响应不是有效 JSON') from error
    return parse(config, code, document)
