const links = new Map<string, number>();
const aliases = new Map<string, Set<number>>();
const names = new Map<string, string>();
const kinds: Record<string, string> = {
  performers: 'performer', studios: 'studio', creators: 'creator', agencies: 'agency', tags: 'tag', series: 'series',
};

export function registerEntityLink(kind: string, name: string, id: unknown): void {
  if (typeof id === 'number' && Number.isSafeInteger(id) && id > 0 && name) {
    links.set(`${kind}/${name}`, id);
    names.set(`${kind}/${id}`, name);
  }
}
export const entityLinkName = (kind: string, id: number): string | undefined => names.get(`${kind}/${id}`);

export function entityLinkId(kind: string, name: string): number | undefined {
  const key = `${kind}/${name}`;
  const canonical = links.get(key);
  if (canonical) return canonical;
  const matches = aliases.get(key);
  return matches?.size === 1 ? [...matches][0] : undefined;
}

function registerAlias(kind: string, name: string, id: unknown): void {
  if (typeof id !== 'number' || !Number.isSafeInteger(id) || id <= 0 || !name) return;
  const key = `${kind}/${name}`;
  const matches = aliases.get(key) || new Set<number>();
  matches.add(id);
  aliases.set(key, matches);
}

/** 已取得的实体身份用于生成地址；没有身份的来源字串不制造 ID。 */
export function registerResponseEntities(path: string, payload: unknown): void {
  if (!payload || typeof payload !== 'object') return;
  const data = payload as Record<string, any>;
  const url = new URL(path, 'https://peach.invalid');
  if (!['/api/entity', '/api/index', '/api/entity/shapes', '/api/tops', '/api/facets', '/api/suggest'].includes(url.pathname)) return;
  const requested = url.searchParams.get('kind') || url.pathname.slice(5);
  const kind = kinds[requested] || requested;
  if (data.canonical_name && data.id) {
    for (const name of Array.isArray(data.aliases) ? data.aliases : []) registerAlias(data.kind || kind, name, data.id);
    registerEntityLink(data.kind || kind, data.canonical_name, data.id);
  }
  for (const entity of Array.isArray(data.entities) ? data.entities : []) {
    for (const name of (entity.names || []).slice(1)) registerAlias(entity.kind, name, entity.id);
    registerEntityLink(entity.kind, entity.names?.[0], entity.id);
  }
  for (const item of Array.isArray(data.items) ? data.items : []) {
    if (kind) registerEntityLink(item.entity_kind || kind, item.k || item.name, item.entity_id);
  }
  if (['/api/tops', '/api/facets'].includes(url.pathname)) {
    for (const [section, entityKind] of Object.entries(kinds)) {
      for (const item of Array.isArray(data[section]) ? data[section] : []) {
        registerEntityLink(entityKind, item.k || item.name, item.entity_id || item.id);
      }
    }
  }
  if (url.pathname === '/api/suggest') {
    for (const group of Array.isArray(data.groups) ? data.groups : []) {
      for (const item of Array.isArray(group.items) ? group.items : []) {
        registerEntityLink(group.kind, item.value, item.entity_id);
      }
    }
  }
}
