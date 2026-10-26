/* 数据管理页：顶上那排读数与入口，链接、资源同步和整理三件要在这页上做的事。
 *
 * 会写真实 ledger 或删盘上东西的那几步（删失效链接、资源同步清理、整理的执行
 * 与回滚）都必须先过确认弹层：这里既量「确认了才发」，也量「取消了一个字节都不发」。
 * 外观由 `frontend/e2e/design-*.test.ts` 读 `getComputedStyle` 断言；这里只看结构、文字与请求。 */
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { act, type ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as legacyUi from '@peach/legacy/ui';

import type { DataCleanupSection } from '../../src/react/bundle';
import { DataCleanupPage } from '../../src/react/data-cleanup/data-cleanup-page';
import { junkBreakdown, prefetchDataCleanup, reviewSummary } from '../../src/react/data-cleanup/data-cleanup';
import { LinkManager } from '../../src/react/data-cleanup/link-manager';
import type { LinkCheckState } from '../../src/react/data-cleanup/links';
import { OrganizeCard } from '../../src/react/data-cleanup/organize-card';
import type { OrganizeData } from '../../src/react/data-cleanup/organize';
import { OrphanRecordsCard } from '../../src/react/data-cleanup/orphan-records-card';
import type { OrphanRecordsData } from '../../src/react/data-cleanup/orphan-records';
import { ResourceSyncCard } from '../../src/react/data-cleanup/resource-sync-card';
import { hasResourceRoots, RESOURCE_SCAN_KEY, type ResourceScanState } from '../../src/react/data-cleanup/resource-sync';
import { IDLE_REPAIR } from '../../src/react/media-repair/media-repair';
import type { MediaSourcesData } from '../../src/react/media-sources';
import { queryClient } from '../../src/react/query';
import { buttonNamed, click, mount, settle, type } from './render';

afterEach(() => {
  queryClient.clear();
  localStorage.clear();
  sessionStorage.clear();
});
notifyManager.setScheduler((notify) => notify());

const FAIL = Symbol('fail');
/** 一条端点回非 2xx。 */
const failWith = (status: number, body: unknown = {}) => ({ [FAIL]: true, status, body });

type Route = unknown | ((body: unknown, method: string) => unknown);

/** 按路径应答。没造数据的路径回 404 并记下来，用例据此核对页面没去问不该问的端点。 */
function serve(routes: Record<string, Route>) {
  const calls: { path: string; method: string; body: unknown }[] = [];
  const unknown: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path, method, body });
    if (!(path in routes)) {
      unknown.push(path);
      return { ok: false, status: 404, json: async () => ({}) };
    }
    const route = routes[path];
    const answer = typeof route === 'function' ? (route as (b: unknown, m: string) => unknown)(body, method) : route;
    if (answer && typeof answer === 'object' && FAIL in answer) {
      const { status, body: payload } = answer as ReturnType<typeof failWith>;
      return { ok: false, status, json: async () => payload };
    }
    return { ok: true, status: 200, json: async () => answer };
  }));
  return {
    calls, unknown,
    posts: (path: string) => calls.filter((call) => call.path === path && call.method === 'POST').map((call) => call.body),
  };
}

type Confirmation = Parameters<typeof legacyUi.confirmModal>[0];

/** 确认弹层：`answer` 为真时替人点确认。两种情况都记下弹层上写的是什么。 */
function confirmWith(answer: boolean) {
  const seen: Confirmation[] = [];
  vi.spyOn(legacyUi, 'confirmModal').mockImplementation((async (options: Confirmation) => {
    seen.push(options);
    if (answer) await options.onConfirm?.();
    return { confirmed: answer };
  }) as typeof legacyUi.confirmModal);
  return seen;
}

const wrap = (element: ReactElement) => mount(<QueryClientProvider client={queryClient}>{element}</QueryClientProvider>);

const withRoots: MediaSourcesData = {
  sources: [{ location: 'local', online: true, roots: ['R:\\media'] }, { location: '115', online: false, roots: ['B:\\'] },
    { location: 'online', online: true }],
};
const withoutRoots: MediaSourcesData = {
  sources: [{ location: 'local', online: true, roots: [] }, { location: 'online', online: true }],
};

const organizeIdle: OrganizeData = {
  status: 'idle', locations: [{ location: 'local', roots: ['R:\\media'] }],
  templates: { local: { file: '{number}', dir: '' } },
  presets: [{ label: '番号 + 标题', file: '{number}[ {title}]', dir: '{studio}' }],
  placeholders: [{ key: 'number', label: '番号' }, { key: 'title', label: '标题' }],
  last_batch: '',
};

/** 整页首屏要的每一条端点。 */
const pageRoutes = (sources: MediaSourcesData): Record<string, Route> => ({
  '/api/review?counts=1': { counts: { metadata_fields: 12, performer_avatars: 3, studio_logos: 2, creator_tags: 1 } },
  '/api/quality-goals?limit=200': { total: 7, items: [] },
  '/api/duplicates?limit=120': { total: 3, files: 8, reclaimable: 2 * 1073741824, groups: [] },
  '/api/ads?limit=1': { pending_total: 5, dismissed_total: 2, counts: { video: 4, url: 1 } },
  '/api/items?state=trash&limit=1': { total: 9, bytes: 3 * 1073741824 },
  '/api/sources': sources,
  '/api/library-processing': { status: 'idle' },
  '/api/media-repair': IDLE_REPAIR,
  '/api/libraries': { libraries: [] },
  '/api/organize': organizeIdle,
  '/api/links': { total: 0, entities: 0, by_kind: {}, top_hosts: [] },
  '/api/links/check': { status: 'idle', check_id: '', checked: 0, total: 0, gone: [], unclear: [], scope: '' },
  '/api/links/prune': { status: 'idle' },
  '/api/orphan-records': { total: 0, items: [] },
  '/api/resource-sync/scan': { status: 'idle', scan_id: '' },
  '/api/resource-sync/apply': { status: 'idle' },
});

async function openPage(sources: MediaSourcesData = withRoots, patch: Record<string, Route> = {}) {
  const server = serve({ ...pageRoutes(sources), ...patch });
  await prefetchDataCleanup(new AbortController().signal);
  const open = vi.fn<(section: DataCleanupSection) => void>();
  const host = await wrap(<DataCleanupPage toast={vi.fn()} failure={vi.fn()} open={open} />);
  await settle();
  return { host, open, server };
}

const entry = (host: ParentNode, section: DataCleanupSection) =>
  host.querySelector<HTMLButtonElement>(`[data-cleanup-go="${section}"]`);

describe('读数卡', () => {
  it('单面板分区的标题只在面板内显示', async () => {
    const { host } = await openPage();
    for (const id of ['link-manager', 'resource-sync']) {
      const section = host.querySelector(`#${id}`)!;
      const title = section.querySelector(`#${id}-title`)!;
      expect(title.tagName).toBe('H3');
      expect(title.parentElement).not.toBe(section);
      expect(section.querySelector(':scope > h2, :scope > h3')).toBeNull();
    }
  });

  it('复核列前三类、其余合成一项；垃圾分项只列有东西的类别', () => {
    expect(reviewSummary({ counts: { metadata_fields: 12, performer_avatars: 3, studio_logos: 2, creator_tags: 1 } }))
      .toEqual({ figure: '18 条待复核', meta: '元数据字段 12 · 女优头像 3 · 厂牌 Logo 2 · 其余 1' });
    expect(junkBreakdown({ pending_total: 5, dismissed_total: 2, counts: { video: 4, image: 0, url: 1 } }))
      .toBe('视频 4 · 网址 1 · 已忽略 2');
  });

  it('五张卡各读各的；点一张就换到那一页', async () => {
    const { host, open, server } = await openPage();
    expect(entry(host, 'review')?.textContent).toContain('18 条待复核');
    expect(entry(host, 'quality')?.textContent).toContain('7 个待升级');
    expect(entry(host, 'duplicates')?.textContent).toContain('3 组 · 8 个文件');
    expect(entry(host, 'duplicates')?.textContent).toContain('可回收 2.0 GB');
    expect(entry(host, 'ads')?.textContent).toContain('5 个待判断');
    expect(entry(host, 'trash')?.textContent).toContain('9 项在回收站');
    expect(entry(host, 'trash')?.textContent).toContain('占用 3.0 GB');
    for (const section of ['review', 'quality', 'duplicates', 'ads', 'trash'] as const) await click(entry(host, section));
    expect(open.mock.calls.map(([section]) => section)).toEqual(['review', 'quality', 'duplicates', 'ads', 'trash']);
    expect(server.unknown).toEqual([]);
  });

  it('一张卡取不到只把那一张写成「读取失败」', async () => {
    const { host } = await openPage(withRoots, { '/api/review?counts=1': failWith(500) });
    expect(entry(host, 'review')?.textContent).toContain('读取失败');
    expect(entry(host, 'trash')?.textContent).toContain('9 项在回收站');
  });

  it('回收站计数跟着侧栏选中的媒体库走', async () => {
    sessionStorage.setItem('peach.library', '网盘');
    const { server } = await openPage(withRoots, { '/api/items?state=trash&limit=1&library=%E7%BD%91%E7%9B%98': { total: 1, bytes: 0 } });
    expect(server.calls.map((call) => call.path)).toContain('/api/items?state=trash&limit=1&library=%E7%BD%91%E7%9B%98');
    expect(server.calls.map((call) => call.path)).not.toContain('/api/items?state=trash&limit=1');
  });
});

describe('资源同步', () => {
  it('只在有落盘来源配了根目录时出现，两份任务状态随首屏一起取回', async () => {
    expect(hasResourceRoots(withRoots)).toBe(true);
    expect(hasResourceRoots(withoutRoots)).toBe(false);
    const server = serve(pageRoutes(withRoots));
    await prefetchDataCleanup(new AbortController().signal);
    expect(server.calls.map((call) => call.path)).toEqual(expect.arrayContaining(['/api/resource-sync/scan', '/api/resource-sync/apply']));
    queryClient.clear();
    const shown = await openPage(withRoots);
    expect(shown.host.querySelector('#resource-sync')).not.toBeNull();
    queryClient.clear();
    const hidden = await openPage(withoutRoots);
    expect(hidden.host.querySelector('#resource-sync')).toBeNull();
    expect(hidden.server.calls.some((call) => call.path.startsWith('/api/resource-sync'))).toBe(false);
  });

  const scanned: ResourceScanState = {
    status: 'complete', scan_id: 'scan-7', missing: 4, empty: 3, unreadable: 0,
    cache: { files: 2, bytes: 1048576 * 5 },
    sources: [
      { location: 'local', online: true, total: 120, missing: 4, empty: 3, unreadable: 0 },
      { location: '115', online: false, total: 30, missing: 0, empty: 0, unreadable: 0 },
    ],
  };

  /** 执行那一条：POST 起任务，之后的 GET 回 `finished`；没起过就是空闲。 */
  const applyRoute = (finished: object) => {
    let started = false;
    return (_body: unknown, method: string) => {
      if (method === 'POST') {
        started = true;
        return { status: 'running', job_id: 'apply-1' };
      }
      return started ? { status: 'complete', job_id: 'apply-1', ...finished } : { status: 'idle' };
    };
  };

  it('扫完来源一行、待删三样一行；清理是危险确认，正文写明永久删除，确认后才发 apply', async () => {
    const seen = confirmWith(true);
    const toast = vi.fn();
    const server = serve({
      '/api/resource-sync/scan': scanned,
      '/api/resource-sync/apply': applyRoute({
        purged: 4, blocked: [], dirs_removed: 3, dir_errors: 0, cache_removed: 2, bytes_reclaimed: 1048576 * 5,
        cache_blocked: [],
      }),
      '/api/items?state=trash&limit=1': { total: 0, bytes: 0 },
    });
    const host = await wrap(<ResourceSyncCard toast={toast} />);
    await settle();
    expect(host.textContent).toContain('本地磁盘');
    expect(host.textContent).toContain('离线，已跳过');
    expect(host.textContent).toContain('馆藏中有 30 项');
    expect(host.textContent).toContain('空文件夹 3 个');
    expect(host.textContent).toContain('待永久删除');
    expect(host.textContent).toContain('保留来源根目录');
    expect(server.posts('/api/resource-sync/apply')).toEqual([]);
    await click(buttonNamed('清理失效条目', host));
    await settle();
    expect(seen).toHaveLength(1);
    expect(seen[0]!.title).toBe('清理失效条目');
    expect(seen[0]!.danger).toBe(true);
    expect(seen[0]!.body).toBe(
      '将永久删除文件已不在盘上的 4 条记录（含回收站里的）、3 个空文件夹，并清理 2 个闲置缓存。来源根目录保留。这一步不可撤销。');
    expect(server.posts('/api/resource-sync/apply')).toEqual([
      { confirm: true, clean_cache: true, scan_id: 'scan-7', background: true },
    ]);
    await settle();
    expect(toast).toHaveBeenCalledWith('已清理失效条目');
    expect(host.textContent).toContain('已永久删除 4 条失效记录和 3 个空文件夹，清理 2 个缓存，释放 5 MB。');
  });

  it('其余来源仍在扫描时显示已确认来源，任务失败也保留这些结果', async () => {
    const state = { status: 'running', scan_id: 'scan-partial', completed_sources: 1, total_sources: 2,
      sources: [scanned.sources![0]!] };
    serve({ '/api/resource-sync/scan': state, '/api/resource-sync/apply': { status: 'idle' } });
    const host = await wrap(<ResourceSyncCard toast={vi.fn()} />);
    await settle();
    expect(host.textContent).toContain('本地磁盘');
    expect(host.textContent).toContain('空文件夹 3 个');
    expect(buttonNamed('清理失效条目', host)).toBeNull();
    expect(host.textContent).not.toContain('可清理的缓存');
    await act(async () => { queryClient.setQueryData(RESOURCE_SCAN_KEY, {
      ...state, status: 'failed', error: '第二个来源无法读取',
    }); });
    expect(host.textContent).toContain('本地磁盘');
    expect(host.textContent).toContain('第二个来源无法读取');
  });

  it('带个人记录的单列一档：读数卡与确认框分开报删除和标为已消失的条数，结果单报接回的', async () => {
    const seen = confirmWith(true);
    serve({
      '/api/resource-sync/scan': {
        ...scanned, purge: 3, vanish: 2,
        sources: [{ ...scanned.sources![0]!, vanish: 2 }, scanned.sources![1]!],
      },
      '/api/resource-sync/apply': applyRoute({
        purged: 3, vanished: 1, reattached: 1, blocked: [], dirs_removed: 3, dir_errors: 0, cache_removed: 2,
        bytes_reclaimed: 0, cache_blocked: [],
      }),
      '/api/items?state=trash&limit=1': { total: 0, bytes: 0 },
    });
    const host = await wrap(<ResourceSyncCard toast={vi.fn()} />);
    await settle();
    expect(host.textContent).toContain('将标为已消失');
    expect(host.textContent).toContain('2 项带个人记录');
    await click(buttonNamed('清理失效条目', host));
    await settle();
    expect(seen[0]!.body).toBe(
      '将永久删除文件已不在盘上的 3 条记录（含回收站里的）、3 个空文件夹，并清理 2 个闲置缓存。来源根目录保留。'
      + '这几样不可撤销。2 条带个人记录的标为已消失，记录留着，可在孤儿记录里接到新文件或彻底删除。');
    await settle();
    expect(host.textContent).toContain(
      '1 条带个人记录的已标为已消失，可在孤儿记录里处理。1 条的记录已接到库里的另一个版本。');
  });

  it('复核时发现文件还在、目录删不掉的几样留在原地：报警告档，结果条写部分完成', async () => {
    confirmWith(true);
    const toast = vi.fn();
    serve({
      '/api/resource-sync/scan': scanned,
      '/api/resource-sync/apply': applyRoute({
        purged: 3, blocked: [{ id: 9, name: '回来了.mp4', reason: 'present' }], dirs_removed: 2, dir_errors: 1,
        cache_removed: 2, bytes_reclaimed: 0, cache_blocked: [],
      }),
      '/api/items?state=trash&limit=1': { total: 0, bytes: 0 },
    });
    const host = await wrap(<ResourceSyncCard toast={toast} />);
    await settle();
    await click(buttonNamed('清理失效条目', host));
    await settle();
    await settle();
    expect(toast).toHaveBeenCalledWith('已完成 7 项，2 项没有处理', { warning: true });
    expect(host.textContent).toContain('部分完成');
    expect(host.textContent).toContain('1 条记录没有删除（「回来了.mp4」），1 个文件夹没有删除，重新检查后可再试。');
  });

  it('取消确认就不发 apply', async () => {
    confirmWith(false);
    const server = serve({ '/api/resource-sync/scan': scanned, '/api/resource-sync/apply': { status: 'idle' } });
    const host = await wrap(<ResourceSyncCard toast={vi.fn()} />);
    await settle();
    await click(buttonNamed('清理失效条目', host));
    await settle();
    expect(server.posts('/api/resource-sync/apply')).toEqual([]);
  });

  it('没有要清的东西时不给清理键，只说一句', async () => {
    serve({
      '/api/resource-sync/scan': { ...scanned, missing: 0, empty: 0, cache: { files: 0, bytes: 0 } },
      '/api/resource-sync/apply': { status: 'idle' },
    });
    const host = await wrap(<ResourceSyncCard toast={vi.fn()} />);
    await settle();
    expect(buttonNamed('清理失效条目', host)).toBeNull();
    expect(host.textContent).toContain('已检查可访问的来源，没有待清理的记录、空文件夹或缓存。');
  });

  it('只有空文件夹要删时也给清理键', async () => {
    serve({
      '/api/resource-sync/scan': { ...scanned, missing: 0, empty: 2, cache: { files: 0, bytes: 0 } },
      '/api/resource-sync/apply': { status: 'idle' },
    });
    const host = await wrap(<ResourceSyncCard toast={vi.fn()} />);
    await settle();
    expect(buttonNamed('清理失效条目', host)).not.toBeNull();
  });
});

describe('链接管理', () => {
  const row = (id: number, patch: Partial<LinkCheckState['gone'][number]> = {}) => ({
    id, entity: `女优 ${id}`, link_kind: 'official', label: '官网', url: `https://example.com/talent/${id}`, note: '404', ...patch,
  });
  const checked: LinkCheckState = {
    status: 'complete', check_id: 'check-3', checked: 3, total: 3, scope: 'all',
    gone: [row(1)], unclear: [row(2, { note: '403', link_kind: 'social' }), row(3, { note: '500' })],
  };
  const stats = {
    total: 719, entities: 210, by_kind: { official: 224, social: 373, catalog: 100, source_reference: 22 },
    top_hosts: [['x.com', 300], ['instagram.com', 70], ['linktr.ee', 3], ['tiktok.com', 1]],
  };

  it('每一类链接各占一格，类型用界面上的名字，主要站点只列前三', async () => {
    serve({ '/api/links': stats, '/api/links/check': { ...checked, status: 'idle' }, '/api/links/prune': { status: 'idle' } });
    const host = await wrap(<LinkManager />);
    await settle();
    const cells = [...host.querySelectorAll('.link-stat-grid > div')].map((cell) => cell.textContent);
    expect(cells).toEqual([
      '链接总数719分布在 210 个女优、厂牌与系列', '官网/事务所224', '社交账号373', '作品资料站100', '资料出处22',
    ]);
    expect(host.textContent).toContain('x.com 300 · instagram.com 70 · linktr.ee 3');
    expect(host.textContent).not.toContain('tiktok.com');
  });

  it('失效与未访问成功分两张表；删除是危险确认，确认了才带着这一趟的检查号发', async () => {
    const seen = confirmWith(true);
    let started = false;
    const server = serve({
      '/api/links': stats, '/api/links/check': checked,
      '/api/links/prune': (_body: unknown, method: string) => {
        if (method === 'POST') started = true;
        return started ? { ok: true, status: 'running', job_id: 'prune-1' } : { status: 'idle' };
      },
    });
    const host = await wrap(<LinkManager />);
    await settle();
    expect(host.querySelector('[aria-label="地址已失效"]')?.textContent).toContain('女优 1');
    expect(host.querySelector('[aria-label="本次未访问成功"]')?.textContent).toContain('社交账号');
    await click(buttonNamed('删除 1 条失效链接', host));
    await settle();
    expect(seen).toHaveLength(1);
    expect(seen[0]!.danger).toBe(true);
    expect(seen[0]!.body).toBe('将删除 1 条失效链接。删除前会再次检查，删除后无法恢复。');
    expect(server.posts('/api/links/prune')).toEqual([{ confirm: true, check_id: 'check-3', background: true }]);
  });

  it('取消确认就不发删除', async () => {
    confirmWith(false);
    const server = serve({ '/api/links': stats, '/api/links/check': checked, '/api/links/prune': { status: 'idle' } });
    const host = await wrap(<LinkManager />);
    await settle();
    await click(buttonNamed('删除 1 条失效链接', host));
    await settle();
    expect(server.posts('/api/links/prune')).toEqual([]);
  });

  it('「全部重试」只重验未访问成功的那几条，带上这一趟的检查号', async () => {
    const server = serve({
      '/api/links': stats,
      '/api/links/check': (body: { status_only?: boolean }) => (body.status_only
        ? checked
        : { ...checked, status: 'running', scope: 'retry', checked: 0, total: 2 }),
      '/api/links/prune': { status: 'idle' },
    });
    const host = await wrap(<LinkManager />);
    await settle();
    await click(buttonNamed('全部重试（2）', host));
    await settle();
    expect(server.posts('/api/links/check').filter((body) => !(body as { status_only?: boolean }).status_only))
      .toEqual([{ retry: [2, 3], check_id: 'check-3' }]);
  });

  it('服务端拒绝时回 200 加 ok:false，照样当失败说出来', async () => {
    serve({
      '/api/links': stats,
      '/api/links/check': (body: { status_only?: boolean }) => (body.status_only
        ? { ...checked, status: 'idle' }
        : { ok: false, error: '清单已过期，请重新检查' }),
      '/api/links/prune': { status: 'idle' },
    });
    const host = await wrap(<LinkManager />);
    await settle();
    await click(buttonNamed('检查死链', host));
    await settle();
    expect(host.textContent).toContain('清单已过期，请重新检查');
  });
});

describe('孤儿记录', () => {
  const orphans: OrphanRecordsData = {
    total: 2,
    items: [
      {
        id: 7, name: 'ABC-123.mp4', code: 'ABC-123', location: 'local', duration: 3600, size: 10,
        vanished_at: 200, has_thumb: true, records: { asset_preference: 1, activity_event: 3, rating: 4, play_count: 2 },
        candidates: [
          { id: 9, location: 'local', name: 'ABC-123.mkv', duration: 3590, size: 20 },
          { id: 10, location: 'local', name: 'abc-123.mp4', duration: 1800, size: 5 },
        ],
      },
      {
        id: 12, name: '海边.mp4', code: '', location: 'local', duration: 600, size: 10,
        vanished_at: 100, has_thumb: false, records: { watch_queue: 1, last_played: 1 }, candidates: [],
      },
    ],
  };

  it('没有已消失的作品时整块不出现', async () => {
    const { host, server } = await openPage();
    expect(host.querySelector('#orphan-records')).toBeNull();
    expect(server.calls.map((call) => call.path)).toContain('/api/orphan-records');
  });

  it('每条列出带的记录与候选文件；没有候选的只给彻底删除', async () => {
    serve({ '/api/orphan-records': orphans });
    const host = await wrap(<OrphanRecordsCard toast={vi.fn()} />);
    await settle();
    const rows = [...host.querySelectorAll('[aria-label="文件已消失的作品"] tbody tr')];
    expect(rows).toHaveLength(2);
    expect(rows[0]!.textContent).toContain('喜欢与理由 1 · 观看历史 3 · 评分 4 · 播放 2 次');
    expect(rows[0]!.textContent).toContain('ABC-123.mkv · 60 分钟');
    expect(rows[0]!.querySelector('img')?.getAttribute('src')).toBe('/thumb?id=7&c=4');
    expect(rows[1]!.textContent).toContain('稍后看 1 · 播放过');
    expect(rows[1]!.textContent).toContain('没有番号或文件名对得上的文件');
    expect(buttonNamed('接到这个文件', rows[1]!)).toBeFalsy();
    expect(buttonNamed('彻底删除', rows[1]!)).toBeTruthy();
  });

  it('接到这个文件直接发，目标默认是时长最接近的那个', async () => {
    const server = serve({ '/api/orphan-records': orphans, '/api/orphan-records/attach': { ok: true, batch: 'user:reattach@1' } });
    const toast = vi.fn();
    const host = await wrap(<OrphanRecordsCard toast={toast} />);
    await settle();
    await click(buttonNamed('接到这个文件', host));
    await settle();
    expect(server.posts('/api/orphan-records/attach')).toEqual([{ id: 7, target: 9 }]);
    expect(toast).toHaveBeenCalledWith('记录已接到新文件，批次 user:reattach@1');
  });

  /** 点第二条的「彻底删除」，交回弹层上写的与发出去的批量请求。 */
  async function purgeSecond(answer: boolean) {
    const seen = confirmWith(answer);
    const server = serve({ '/api/orphan-records': orphans, '/api/batch': { ok: true, purged: 1, blocked: [] } });
    const host = await wrap(<OrphanRecordsCard toast={vi.fn()} />);
    await settle();
    await click(buttonNamed('彻底删除', host.querySelectorAll('[aria-label="文件已消失的作品"] tbody tr')[1]!));
    await settle();
    return { seen, posts: server.posts('/api/batch') };
  }

  it('彻底删除是危险确认，正文写明不可撤销，确认了才发批量删除', async () => {
    const { seen, posts } = await purgeSecond(true);
    expect(seen[0]!.danger).toBe(true);
    expect(seen[0]!.body).toBe('将删除「海边.mp4」这一条和它带的记录（稍后看 1 · 播放过）。此操作不可撤销。');
    expect(posts).toEqual([{ ids: [12], operation: 'delete' }]);
  });

  it('取消确认就不发删除', async () => {
    expect((await purgeSecond(false)).posts).toEqual([]);
  });

  it('批量删除覆盖跨页选择，取消确认不发请求，文件恢复时显示保留数量', async () => {
    const items = Array.from({ length: 23 }, (_, index) => ({ ...orphans.items[1]!, id: index + 30, name: `作品 ${index}` }));
    const server = serve({ '/api/orphan-records': { total: items.length, items },
      '/api/batch': { ok: true, purged: 1, blocked: [50] } });
    const toast = vi.fn();
    const host = await wrap(<OrphanRecordsCard toast={toast} />);
    await settle();
    await click(host.querySelector('input[aria-label="选择 作品 0"]'));
    await click(buttonNamed('下一页', host));
    await click(host.querySelector('input[aria-label="选择 作品 20"]'));
    confirmWith(false);
    await click(buttonNamed('彻底删除所选（2）', host));
    expect(server.posts('/api/batch')).toEqual([]);
    const seen = confirmWith(true);
    await click(buttonNamed('彻底删除所选（2）', host));
    await settle();
    expect(seen[0]!.items).toEqual(['作品 0', '作品 20']);
    expect(server.posts('/api/batch')).toEqual([{ ids: [30, 50], operation: 'delete' }]);
    expect(toast).toHaveBeenCalledWith('已彻底删除 1 条记录，1 条文件已恢复，未删除');
  });
});

describe('整理', () => {
  const plan = {
    counts: { change: 2, unchanged: 5 }, reasons: { 缺番号: 1 }, truncated: false,
    rows: [
      { current_path: 'R:\\media\\abc001.mp4', target_path: 'R:\\media\\S1\\ABC-001.mp4', action: 'move' },
      { current_path: 'R:\\media\\abc002.mp4', target_path: 'R:\\media\\S1\\ABC-002.mp4', action: 'move' },
    ],
  };

  function organizeServer(data: OrganizeData = organizeIdle) {
    return serve({
      '/api/organize': { ...data, status: 'running', job_id: 'org-1' },
      '/api/organize/preview': plan,
      '/api/organize/apply': { status: 'running', job_id: 'org-1' },
      '/api/organize/rollback': { status: 'running', job_id: 'org-2' },
      '/api/settings': { ok: true },
    });
  }

  it('模板框跟着存下的模板走；预览发的是这一格的来源与模板，预览不存模板', async () => {
    const server = organizeServer();
    queryClient.setQueryData(['organize'], organizeIdle);
    const host = await wrap(<OrganizeCard toast={vi.fn()} failure={vi.fn()} />);
    const file = host.querySelector<HTMLInputElement>('input[placeholder="{number}[ {title}]"]');
    expect(file?.value).toBe('{number}');
    await type(file, ' {number} {title} ');
    await click(buttonNamed('预览', host));
    await settle();
    expect(server.posts('/api/organize/preview')).toEqual([
      { location: 'local', file_template: '{number} {title}', dir_template: '' },
    ]);
    expect(server.posts('/api/settings')).toEqual([]);
    expect(host.textContent).toContain('2 个文件会改名或移动，5 个已经就是目标名字；跳过 缺番号 1。');
    expect(host.textContent).toContain('ABC-001.mp4');
  });

  it('执行先确认，确认了才发 apply，起跑后存下这次的模板', async () => {
    const seen = confirmWith(true);
    const server = organizeServer();
    queryClient.setQueryData(['organize'], organizeIdle);
    const host = await wrap(<OrganizeCard toast={vi.fn()} failure={vi.fn()} />);
    expect(buttonNamed('执行整理', host)).toBeNull();
    await click(buttonNamed('预览', host));
    await settle();
    await click(buttonNamed('执行整理', host));
    await settle();
    expect(seen.map((options) => options.title)).toEqual(['按模板整理文件']);
    expect(seen[0]!.body).toContain('「本地」上的文件名与目录');
    expect(server.posts('/api/organize/apply')).toEqual([
      { location: 'local', file_template: '{number}', dir_template: '', confirm: true },
    ]);
    expect(server.posts('/api/settings')).toEqual([
      { organizeTemplates: { local: { file: '{number}', dir: '' } } },
    ]);
  });

  it('有上一批才给回滚；回滚也先确认，取消就不发', async () => {
    const seen = confirmWith(false);
    const server = organizeServer({ ...organizeIdle, last_batch: 'batch-9' });
    queryClient.setQueryData(['organize'], { ...organizeIdle, last_batch: 'batch-9' });
    const host = await wrap(<OrganizeCard toast={vi.fn()} failure={vi.fn()} />);
    await click(buttonNamed('回滚上一批', host));
    await settle();
    expect(seen.map((options) => options.title)).toEqual(['回滚上一批整理']);
    expect(server.posts('/api/organize/rollback')).toEqual([]);
    expect(server.posts('/api/organize/apply')).toEqual([]);

    queryClient.clear();
    queryClient.setQueryData(['organize'], organizeIdle);
    const fresh = await wrap(<OrganizeCard toast={vi.fn()} failure={vi.fn()} />);
    expect(buttonNamed('回滚上一批', fresh)).toBeNull();
  });

  it('回滚确认了才发，请求体只有 confirm', async () => {
    confirmWith(true);
    const server = organizeServer({ ...organizeIdle, last_batch: 'batch-9' });
    queryClient.setQueryData(['organize'], { ...organizeIdle, last_batch: 'batch-9' });
    const host = await wrap(<OrganizeCard toast={vi.fn()} failure={vi.fn()} />);
    await click(buttonNamed('回滚上一批', host));
    await settle();
    expect(server.posts('/api/organize/rollback')).toEqual([{ confirm: true }]);
  });
});
