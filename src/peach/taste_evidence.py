"""规范身份、馆藏、浏览与关注证据的独立汇总。"""
from collections import Counter, defaultdict
import hashlib
import json
import math
import re
import sqlite3
from contextlib import closing
import ahocorasick

from .catalog_rules import LENGTH_TAGS, TECH_TAGS
from .entities import male_performer_ids, normalize_entity_name
from .taste_sites import account_key, host, identity_context, linked_identities, mixed_adult, MIXED_DOMAINS

KINDS = ("performer", "creator", "tag", "studio", "series")


def era(value):
    match = re.match(r"(\d{4})", str(value or ""))
    return str(int(match[1]) // 5 * 5) if match else None


def length_band(value):
    seconds = float(value or 0)
    return str(next((index for index, bound in enumerate((1800, 5400, 9000)) if seconds < bound), 3)) if seconds > 0 else None


class IdentityIndex:
    def __init__(self, connection):
        self.excluded = male_performer_ids(connection)
        self.entities = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM entity")
                         if row['id'] not in self.excluded}
        self.canonical, self.aliases = defaultdict(set), defaultdict(set)
        self.names = defaultdict(dict)
        for row in self.entities.values():
            self.canonical[(row["kind"], normalize_entity_name(row["canonical_name"]))].add(row["id"])
        for eid, alias in connection.execute("SELECT entity_id,alias FROM entity_alias"):
            if eid in self.entities:
                self.aliases[(self.entities[eid]["kind"], normalize_entity_name(alias))].add(eid)

    def resolve(self, kind, name):
        key = kind, normalize_entity_name(name)
        ids = self.canonical.get(key) or self.aliases.get(key) or set()
        return self.entities[next(iter(ids))] if len(ids) == 1 else None

    def identity(self, kind, name):
        entity = self.resolve(kind, name)
        eid = entity["id"] if entity else f"name:{normalize_entity_name(name)}"
        self.names[kind][eid] = entity["canonical_name"] if entity else name
        return eid

    def title_matcher(self):
        matcher = ahocorasick.Automaton()
        for kind, label in self.canonical.keys() | self.aliases.keys():
            ids = self.canonical.get((kind, label)) or self.aliases[(kind, label)]
            if kind in {"performer", "creator", "studio", "series"} and len(ids) == 1 and len(label) >= (4 if label.isascii() else 2):
                existing = set(matcher.get(label, (label, ()))[1])
                matcher.add_word(label, (label, tuple(existing | ids)))
        if not len(matcher):
            return None
        matcher.make_automaton()
        return matcher


def title_entities(title, matcher, index):
    if matcher is None:
        return
    folded = title.casefold()
    for end, (label, ids) in matcher.iter_long(folded):
        start = end - len(label) + 1
        edges = folded[max(0, start - 1):start] + folded[end + 1:end + 2]
        if label.isascii() and any(character.isalnum() or character == "_" for character in edges):
            continue
        for eid in ids:
            row = index.entities[eid]
            yield eid, row["kind"], row["canonical_name"]


def collection_evidence(connection, index, counters):
    from .taste_history import _positive_behavior_score
    columns = {row[1] for row in connection.execute("PRAGMA table_info(asset)")}
    fields = [name for name in ("id", "creator", "studio", "duration", "release_date", "play_seconds", "play_count",
              "o_count", "rating", "feedback", "last_played") if name in columns]
    condition = "a.disposal IS NULL" if "disposal" in columns else "1=1"
    rows = {row["id"]: dict(row) for row in connection.execute(
        "SELECT " + ",".join("a." + field for field in fields) + ",COALESCE(p.liked,0) liked "
        "FROM asset a LEFT JOIN asset_preference p ON p.asset_id=a.id AND p.profile_id='local-default' WHERE " + condition)}
    linked = defaultdict(lambda: defaultdict(set))
    for aid, eid, kind, name in connection.execute(
        "SELECT ae.asset_id,e.id,e.kind,e.canonical_name FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id"):
        if aid in rows and eid not in index.excluded and kind in KINDS and name not in TECH_TAGS | LENGTH_TAGS:
            linked[aid][kind].add(eid)
            index.names[kind][eid] = name
    for aid, row in rows.items():
        if row.get("feedback") == "dislike":
            continue
        for kind in ("creator", "studio"):
            if not linked[aid][kind] and row.get(kind):
                linked[aid][kind].add(index.identity(kind, row[kind]))
        behavior = _positive_behavior_score(row, exclude_disliked=True)
        features = dict(linked[aid])
        features["era"] = {era(row.get("release_date"))} - {None}
        features["length"] = {length_band(row.get("duration"))} - {None}
        for kind, values in features.items():
            for eid in values:
                counters["馆藏积累"][kind][eid] += 1
                counters["观看与反馈"][kind][eid] += behavior


def browser_entities(url, title, index, matcher, accounts, decisions, account_hosts=None):
    from .taste_history import _is_taste_domain, _url_candidates
    from .genre_taxonomy import resolve_genre
    website = host(url)
    account_host = website.partition('.')[2] if website.endswith(('.fanbox.cc', '.tumblr.com')) else website
    matched = linked_identities(url, accounts) if account_hosts is None or account_host in account_hosts else ()
    yield from matched
    domain, tags, creators = _url_candidates(url, title)
    adult = _is_taste_domain(domain or "") or mixed_adult(url, title)
    social = not adult and website in MIXED_DOMAINS and account_key(url) is not None
    if not (adult or social):
        return
    yield from title_entities(title, matcher, index)
    if social and not (adult or matched):
        return
    for tag in tags:
        mapped = resolve_genre(tag.replace("_", " "), decisions)
        for label in (tag, mapped) if mapped else (tag,):
            for kind in KINDS:
                entity = index.resolve(kind, label)
                if entity and entity["canonical_name"] not in TECH_TAGS | LENGTH_TAGS:
                    yield entity["id"], kind, entity["canonical_name"]
    for creator in creators:
        for kind in ("performer", "creator"):
            entity = index.resolve(kind, creator)
            if entity:
                yield entity["id"], kind, entity["canonical_name"]


def browser_evidence(connection, store, index, accounts, counters, *, cache=True):
    from .taste_history import preference_history_visits, store_version
    from .taste_preferences import _policy_fingerprint
    from .fsutil import atomic_write_text
    from .genre_decisions import load_genre_decisions
    if not store.is_file():
        return
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    decisions = load_genre_decisions(connection) if "genre_decision" in tables else {}
    inputs = [store_version(store), _policy_fingerprint(), list(index.entities.values()),
              sorted((kind, label, sorted(ids)) for (kind, label), ids in index.aliases.items()),
              sorted((key, sorted(values)) for key, values in accounts.items()), decisions]
    fingerprint = hashlib.sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    cache_path = store.parent / 'browser-preference-cache.json'
    try:
        cached = json.loads(cache_path.read_text(encoding='utf-8')) if cache else {}
        if cached.get('version') == 1 and cached.get('fingerprint') == fingerprint:
            for kind, rows in cached['dimensions'].items():
                for eid, name, count in rows:
                    index.names[kind][eid] = name
                    counters['浏览记录'][kind][eid] = count
            return
    except (OSError, ValueError, KeyError, TypeError):
        pass
    visits = preference_history_visits(store)
    matcher = index.title_matcher()
    days = defaultdict(lambda: defaultdict(set))
    identities = {}
    account_hosts = {key[0] for key in accounts}
    for visited_at, url, title in visits:
        key = url, title
        if key not in identities:
            identities[key] = tuple(browser_entities(url, title, index, matcher, accounts, decisions, account_hosts))
        for eid, kind, name in identities[key]:
            index.names[kind][eid] = name
            days[kind][eid].add(visited_at[:10])
    for kind, identities in days.items():
        for eid, dates in identities.items():
            counters["浏览记录"][kind][eid] = len(dates)
    payload = {'version': 1, 'fingerprint': fingerprint, 'dimensions': {kind: [
        [eid, index.names[kind][eid], len(dates)] for eid, dates in identities.items()]
        for kind, identities in days.items()}}
    try:
        if cache:
            atomic_write_text(cache_path, json.dumps(payload, ensure_ascii=False), mode=0o600)
    except OSError:
        pass


def preference_evidence(connection, store, *, cache=True):
    index = IdentityIndex(connection)
    counters = {source: defaultdict(Counter) for source in ("观看与反馈", "馆藏积累", "浏览记录", "浏览器书签", "关注")}
    collection_evidence(connection, index, counters)
    accounts, followed = identity_context(connection)
    for kind, identities in followed.items():
        for (eid, name), count in identities.items():
            if eid in index.excluded:
                continue
            index.names[kind][eid] = name
            counters["关注"][kind][eid] = count
    browser_evidence(connection, store, index, accounts, counters, cache=cache)
    if store.is_file():
        from .taste_history import _readonly_uri
        from .taste_bookmarks import bookmark_rows
        from .genre_decisions import load_genre_decisions
        try:
            with closing(sqlite3.connect(_readonly_uri(store), uri=True)) as private:
                rows = {row[2]: row[3] for row in bookmark_rows(private)}
        except sqlite3.DatabaseError:
            rows = {}
        matcher = index.title_matcher()
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        decisions = load_genre_decisions(connection) if "genre_decision" in tables else {}
        for url, title in rows.items():
            for eid, kind, name in set(browser_entities(url, title, index, matcher, accounts, decisions)):
                index.names[kind][eid] = name
                counters["浏览器书签"][kind][eid] += 1
    strengths, evidence = {}, defaultdict(dict)
    for kind in (*KINDS, "era", "length"):
        values = set().union(*(set(by_kind[kind]) for by_kind in counters.values()))
        strengths[kind] = {}
        for eid in values:
            if eid in index.excluded:
                continue
            raw = (2.5 * math.log2(1 + counters["观看与反馈"][kind][eid])
                   + .45 * math.log2(1 + counters["馆藏积累"][kind][eid])
                   + .7 * math.log2(1 + counters["浏览记录"][kind][eid])
                   + 1.2 * math.log2(1 + counters["浏览器书签"][kind][eid])
                   + 2 * counters["关注"][kind][eid])
            if raw > 0:
                strengths[kind][eid] = 1 - math.exp(-raw / 6)
                evidence[kind][eid] = [source for source, by_kind in counters.items() if by_kind[kind][eid] > 0]
    return strengths, index.names, evidence
