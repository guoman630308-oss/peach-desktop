/* 馆藏卡片网格：一屏卡片怎么排（取数的查询串、折叠、Mix 落位、竖屏带落点），以及一张卡
 * 画出什么、点哪里去哪里。 */
import { act, useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { MediaCard, cardRatio, type MediaCardVariant } from '../../src/react/components/media-card';
import {
  MIX_SLOT, SHORTS_BATCH, arrangeTiles, catalogParams, collapseGroups, fetchCatalogPage, latestCatalogPages, mixSeed, nextOffset,
  shortsBoundaries, shortsParams, replaceCatalogItem, type GridPage,
} from '../../src/react/catalog-grid/catalog-grid';
import type { MediaCardActions, MediaCardHelpers, MediaCardLayout, MediaItem } from '../../src/react/catalog-grid/types';

import { click, mount } from './render';
import { queryClient } from '../../src/react/query';

const SMALL: MediaCardLayout = { active: false, size: 'small', portrait: false, javImage: 'cover' };

const item = (id: number, extra: Partial<MediaItem> = {}): MediaItem => ({ id, name: `作品 ${id}`, has_thumb: true, ...extra });
const page = (items: MediaItem[], offset = 0, extra: Partial<GridPage> = {}): GridPage => ({ items, offset, ...extra });

describe('目录取数', () => {
  it('详情修改同步到卡片分页和表格页，同页其它作品保持原值', () => {
    const cardKey = ['catalog-grid', 'catalog', 'cache-test', 0];
    const tableKey = ['catalog-grid', 'table', 'cache-test', 0];
    queryClient.setQueryData(cardKey, { pages: [page([item(100), item(101)], 0)], pageParams: [0] });
    queryClient.setQueryData(tableKey, { items: [item(100), item(101)], total: 2 });
    replaceCatalogItem(100, { feedback: 'seen', watch_later: true });
    const cards = queryClient.getQueryData<{ pages: GridPage[] }>(cardKey)!.pages[0]!.items;
    const rows = queryClient.getQueryData<{ items: MediaItem[] }>(tableKey)!.items;
    for (const items of [cards, rows]) {
      expect(items[0]).toMatchObject({ id: 100, feedback: 'seen', watch_later: true });
      expect(items[1]!.feedback).toBeUndefined();
    }
    queryClient.removeQueries({ queryKey: cardKey });
    queryClient.removeQueries({ queryKey: tableKey });
  });
  it('刷新代次认领同一集合的完整分页，筛选不同不共用', () => {
    const old = { pages: [page([item(801)]), page([item(802)], 60)], pageParams: [0,60] };
    queryClient.setQueryData(['catalog-grid','catalog','q=wall-retain',1], old);
    const retained = latestCatalogPages(['catalog-grid','catalog','q=wall-retain',2]);
    expect(retained).toBe(old);
    expect(retained?.pages).toHaveLength(2);
    expect(latestCatalogPages(['catalog-grid','catalog','q=another-wall',2])).toBeUndefined();
    queryClient.removeQueries({ queryKey: ['catalog-grid','catalog','q=wall-retain'] });
  });
  it('查询串只带有值的筛选，排除竖屏只在壳说要排除时加', () => {
    expect(catalogParams({ sort: 'seed', q: '', tag: '苗条' }, false)).toBe('sort=seed&tag=%E8%8B%97%E6%9D%A1');
    expect(new URLSearchParams(catalogParams({ sort: 'seed' }, true)).get('exclude_vertical')).toBe('1');
    expect(new URLSearchParams(catalogParams({ q: '竖屏' }, false)).has('exclude_vertical')).toBe(false);
  });

  it('一页取「每批条数」那么多，往后的页不数总数', async () => {
    const fetcher = vi.fn(async (_url: string) => ({ ok: true, status: 200, json: async () => ({ items: [item(1)], has_more: true }) }));
    vi.stubGlobal('fetch', fetcher);
    await fetchCatalogPage('sort=seed', 0, 48, false, new AbortController().signal);
    await fetchCatalogPage('sort=seed', 48, 48, false, new AbortController().signal);
    const first = new URL(fetcher.mock.calls[0]![0], 'http://peach.test').searchParams;
    const second = new URL(fetcher.mock.calls[1]![0], 'http://peach.test').searchParams;
    expect([first.get('limit'), first.get('offset'), first.has('count')]).toEqual(['48', '0', false]);
    expect([second.get('limit'), second.get('offset'), second.get('count')]).toEqual(['48', '48', '0']);
  });

  it('第一页为空时再问一次整个馆藏是不是空的，回收站不问', async () => {
    const fetcher = vi.fn(async (_url: string) => ({ ok: true, status: 200, json: async () => ({ items: [], total: 0 }) }));
    vi.stubGlobal('fetch', fetcher);
    const result = await fetchCatalogPage('tag=x', 0, 60, false, new AbortController().signal);
    expect(fetcher.mock.calls[1]![0]).toBe('/api/items?limit=1&thumb=0');
    expect(result.libraryEmpty).toBe(true);

    fetcher.mockClear();
    await fetchCatalogPage('state=trash', 0, 60, true, new AbortController().signal);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('下一页从哪儿取：第一页看总数，往后看 has_more', () => {
    const next = nextOffset(60);
    expect(next(page([item(1)], 0, { total: 100 }))).toBe(60);
    expect(next(page([item(1)], 0, { total: 1 }))).toBeUndefined();
    expect(next(page([item(1)], 60, { has_more: true }))).toBe(120);
    expect(next(page([item(1)], 60, { has_more: false }))).toBeUndefined();
  });

  it('竖屏带跟着主列表的筛选走，一条取一批竖屏', () => {
    const params = new URLSearchParams(shortsParams({ sort: 'seed', tag: '' }, 36));
    expect(Object.fromEntries(params)).toEqual({ sort: 'seed', orient: '竖屏', limit: String(SHORTS_BATCH), offset: '36' });
  });
});

describe('一屏卡片的排法', () => {
  const parts = (key: string, seed: number) => ({ key, seed_id: seed, count: 2 });
  const editions = (key: string, seed: number) => ({ key, seed_id: seed, count: 2, editions: ['原版', '无码'] });

  it('分卷与版次各自折叠，跨页也只留第一次出现的那张；开关关掉就不折', () => {
    const seen = { parts: new Set<string>(), editions: new Set<string>() };
    const first = [item(1, { part_group: parts('P', 1) }), item(2, { part_group: parts('P', 1) }), item(3, { edition_group: editions('E', 3) })];
    expect(collapseGroups(first, seen, true).map((x) => x.id)).toEqual([1, 3]);
    expect(collapseGroups([item(4, { edition_group: editions('E', 3) }), item(5)], seen, true).map((x) => x.id)).toEqual([5]);
    expect(collapseGroups(first, { parts: new Set(), editions: new Set() }, false).map((x) => x.id)).toEqual([1, 2, 3]);
  });

  it('每一页够 8 张就在第 8 位插一张 Mix；翻页重复出现的条目只画一次', () => {
    const named = (id: number) => item(id, { creator: `作者 ${id}` });
    const first = Array.from({ length: 20 }, (_, index) => named(index + 1));
    const second = [named(20), ...Array.from({ length: 9 }, (_, index) => named(index + 21))];
    const { tiles, pageStarts } = arrangeTiles([page(first), page(second, 20)], { collapse: true, mix: true, javImage: 'cover' });
    expect(tiles[MIX_SLOT]?.kind).toBe('mix');
    expect(pageStarts).toEqual([0, 21]);
    const ids = tiles.flatMap((tile) => (tile.kind === 'card' ? [tile.item.id] : []));
    expect(ids.filter((id) => id === 20)).toHaveLength(1);
    expect(tiles[pageStarts[1]! + MIX_SLOT]?.kind).toBe('mix');
    expect(arrangeTiles([page(first)], { collapse: true, mix: false, javImage: 'cover' }).tiles.some((tile) => tile.kind === 'mix')).toBe(false);
  });

  it('Mix 的种子不取它挨着的那张：从 Mix 位往下隔一屏找有图有署名的，都没署名就取末尾', () => {
    const visible = Array.from({ length: 20 }, (_, index) => item(index + 1, { creator: `作者 ${index + 1}` }));
    expect(mixSeed(visible, 'cover')?.id).toBe(MIX_SLOT + 9);
    const anonymous = Array.from({ length: 20 }, (_, index) => item(index + 1, { has_thumb: false }));
    expect(mixSeed(anonymous, 'cover')?.id).toBe(20);
  });

  it('竖屏带落在这一页新增那几行的行边界上，两端各留至少一行', () => {
    expect(shortsBoundaries(20, 0, 4)).toEqual([4, 8, 12, 16]);
    expect(shortsBoundaries(20, 10, 4)).toEqual([12, 16]);
    expect(shortsBoundaries(4, 0, 4)).toEqual([]);
  });
});

describe('比例', () => {
  const jav = item(1, { is_jav: true, code: 'ABC-001', has_cover: true });
  const ratio = (target: MediaItem, variant: MediaCardVariant, layout: Partial<MediaCardLayout>) =>
    cardRatio(target, variant, { ...SMALL, ...layout });

  it('一个列表里的卡等高：比例只由语境决定，不看单条视频的宽高', () => {
    expect(ratio(item(2, { width: 1080, height: 1920 }), 'grid', {})).toBeCloseTo(16 / 9);
    expect(ratio(item(2), 'short', {})).toBeCloseTo(9 / 16);
    expect(ratio(item(2), 'grid', { portrait: true })).toBeCloseTo(9 / 16);
  });

  it('大图统一所有作品的画面框，未启用的视图使用横框', () => {
    expect(ratio(jav, 'grid', { active: true, size: 'big' })).toBe(0.75);
    expect(ratio(item(2), 'grid', { active: true, size: 'big' })).toBe(0.75);
    expect(ratio(jav, 'grid', { active: false, size: 'big' })).toBeCloseTo(16 / 9);
    expect(ratio(jav, 'grid', { active: true, size: 'small' })).toBeCloseTo(16 / 9);
  });
});

describe('作品卡', () => {
  const helpers: MediaCardHelpers = {
    badgeHtml: (location) => `<span>${location}</span>`,
    titleHtml: (_it, raw) => raw,
    displayName: (_it, raw) => raw,
    tagLabel: (tag) => `#${tag}`,
  };
  const actionsFor = (): MediaCardActions => ({
    open: vi.fn(), openResource: vi.fn(), openShort: vi.fn(), openShorts: vi.fn(), openMix: vi.fn(),
    openEntity: vi.fn(), openUnowned: vi.fn(), toggleTag: vi.fn(), toggleSelection: vi.fn(),
    watchLater: vi.fn(async (_it: MediaItem, onChange: (on: boolean) => void) => onChange(true)),
    resourceOperation: vi.fn(async () => {}), mixRelated: vi.fn(async () => []), canFlip: () => true,
  });
  const render = async (target: MediaItem, options: { variant?: MediaCardVariant; selectMode?: boolean; layout?: MediaCardLayout } = {}) => {
    const actions = actionsFor();
    const onOpen = vi.fn();
    const host = await mount(
      <MediaCard item={target} variant={options.variant || 'grid'} layout={options.layout || SMALL} selected={false}
        selectMode={!!options.selectMode} seekSeconds={10} helpers={helpers} actions={actions} onOpen={onOpen} />,
    );
    return { host, actions, onOpen, card: host.querySelector<HTMLElement>('[data-media-card]')! };
  };

  it('点标题打开，点署名进资料页，点「未归属」进那一批，点标签在这一屏筛它', async () => {
    const credited = await render(item(7, { creator: '某某', tags: ['苗条'] }));
    await click(credited.host.querySelector('[data-media-title]'));
    expect(credited.onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }), credited.card);
    await click(credited.host.querySelector('[data-media-who]'));
    expect(credited.actions.openEntity).toHaveBeenCalledWith('creator', '某某');
    await click(credited.host.querySelector('[data-media-tag]'));
    expect(credited.actions.toggleTag).toHaveBeenCalledWith('苗条');
    expect(credited.onOpen).toHaveBeenCalledTimes(1);

    const unowned = await render(item(8));
    await click(unowned.host.querySelector('[data-open-unowned]'));
    expect(unowned.actions.openUnowned).toHaveBeenCalledTimes(1);
    expect(unowned.onOpen).not.toHaveBeenCalled();
  });

  it('多选模式或按着修饰键时，点哪里都是切换选中；Shift 是连选', async () => {
    const plain = await render(item(9));
    const title = plain.card.querySelector<HTMLElement>('[data-media-title]')!;
    await act(async () => { title.dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true })) });
    expect(plain.actions.toggleSelection).toHaveBeenCalledWith(9, true);
    const selecting = await render(item(10), { selectMode: true });
    expect(selecting.card.querySelector('[data-media-later]')).toBeNull();
    expect(selecting.card.querySelector('[data-media-seek]')).toBeNull();
    expect(selecting.card.querySelector('[data-media-check]')).not.toBeNull();
    expect(plain.card.querySelector('[data-media-later]')).not.toBeNull();
    await click(selecting.host.querySelector('[data-media-title]'));
    expect(selecting.actions.toggleSelection).toHaveBeenCalledWith(10, false);
    expect([plain.onOpen, selecting.onOpen].every((fn) => fn.mock.calls.length === 0)).toBe(true);
  });

  it('片名空着时标题与读屏名写「未命名视频」', async () => {
    const { host } = await render(item(12, { name: '' }));
    expect(host.querySelector('[data-media-title]')?.textContent).toBe('未命名视频');
    expect(host.querySelector('[data-media-open]')?.getAttribute('aria-label')).toBe('打开 未命名视频详情');
  });

  it('大小不明写「大小未知」，看过几次写出来，标签取作品自己的标签、至多三枚', async () => {
    const { host } = await render(item(11, { size: 0, play_count: 3, performers: ['甲'], tags: ['a', 'b', 'c', 'd'] }));
    expect(host.querySelector('[data-media-size]')?.textContent).toBe('大小未知');
    expect(host.querySelector('[data-media-watch-count]')?.textContent).toBe('看过 3');
    expect([...host.querySelectorAll('[data-media-tag]')].map((tag) => tag.textContent)).toEqual(['#a', '#b', '#c']);
  });

  it('悬停控件各用只代表自己的那枚字形：快退、快进是转向箭头，打开详情是摊开', async () => {
    const { host } = await render(item(13));
    const glyphs = [...host.querySelectorAll('[data-media-seek] button')]
      .map((button) => [button.getAttribute('aria-label'), button.querySelector('use')?.getAttribute('href')]);
    expect(glyphs).toEqual([
      [expect.stringContaining('后退'), '#i-rotate-ccw'],
      [expect.stringContaining('前进'), '#i-rotate-cw'],
      ['打开详情', '#i-expand'],
    ]);
  });

  it('关注来源保存的条目用来源的缩略图和标签，标签不可点', async () => {
    const { host } = await render(item(12, { follow_thumb_url: 'https://example.test/t.jpg', follow_item_id: 5, tags: ['本地'], follow_tags: ['来源'] }));
    expect(host.querySelector('[data-media-art] img')?.getAttribute('src')).toBe('https://example.test/t.jpg');
    const tag = host.querySelector<HTMLButtonElement>('[data-media-tag]');
    expect([tag?.textContent, tag?.disabled]).toEqual(['#来源', true]);
  });

  it('看到一半的画一条进度，分卷组不画；比例写进封面格', async () => {
    const { host } = await render(item(13, { duration: 600, play_seconds: 150 }));
    const bar = host.querySelector('[data-media-progress]');
    expect([bar?.getAttribute('role'), bar?.getAttribute('aria-label'), bar?.getAttribute('aria-valuenow')]).toEqual(['progressbar', '观看进度', '25']);
    expect(host.querySelector<HTMLElement>('[data-media-pic]')!.style.getPropertyValue('--card-ratio')).toBe(String(16 / 9));

    const group = await render(item(14, { duration: 600, play_seconds: 150, part_group: { key: 'P', seed_id: 14, count: 2 } }));
    expect(group.host.querySelector('[data-media-progress]')).toBeNull();
    expect(group.card.dataset.partSeed).toBe('14');
    expect(group.card.hasAttribute('data-stacked')).toBe(true);
    expect(group.host.querySelector('[data-media-group]')?.textContent).toBe('2 卷');
    const versions = await render(item(16, {
      part_group: { key: 'P', seed_id: 14, count: 2 },
      edition_group: { key: 'E', seed_id: 13, count: 2, editions: ['1080p', '4K 修复'] },
    }));
    expect([...versions.host.querySelectorAll('[data-media-group]')].map((node) => node.textContent)).toEqual(['2 个版本']);
    expect(versions.host.querySelector('[data-media-open]')?.getAttribute('aria-label')).toMatch(/版本$/);
  });

  it('待删的作品留在原位，标上回收站', async () => {
    const { card, host } = await render(item(15, { disposal: 'trash' }));
    expect(card.hasAttribute('data-pending-delete')).toBe(true);
    expect(host.querySelector('[data-media-trash-mark]')?.textContent).toBe('回收站');
  });

  it('稍后看只写这一条，不打开详情；写成后按钮按下', async () => {
    const { host, actions, onOpen } = await render(item(16));
    await click(host.querySelector('[data-later]'));
    expect(actions.watchLater).toHaveBeenCalledWith(expect.objectContaining({ id: 16 }), expect.any(Function));
    expect(onOpen).not.toHaveBeenCalled();
    expect(host.querySelector('[data-later]')?.getAttribute('aria-pressed')).toBe('true');
  });

  it('回收站里的资源按类型起名，卡上那枚键还原这一条，不打开它', async () => {
    const archive = await render(item(17, { medium: 'archive', name: '资料.zip', disposal: 'trash' }), { variant: 'resource' });
    expect(archive.host.querySelector('[data-media-who]')?.textContent).toBe('压缩包');
    await click(archive.host.querySelector('[data-media-resource-action]'));
    expect(archive.actions.resourceOperation).toHaveBeenCalledWith(expect.objectContaining({ id: 17 }), 'restore');
    expect(archive.onOpen).not.toHaveBeenCalled();

    const shortcut = await render(item(18, { medium: 'other', name: '入口.URL' }), { variant: 'resource' });
    expect(shortcut.host.querySelector('[data-media-who]')?.textContent).toBe('网址快捷方式');
    expect(shortcut.host.querySelector('[data-media-resource-action]')?.getAttribute('title')).toBe('移入回收站');
  });

  it('换大图／小图留着原来那张封面，原地换取景；换了作品才换图', async () => {
    const BIG: MediaCardLayout = { active: true, size: 'big', portrait: false, javImage: 'cover' };
    let show!: (next: { layout: MediaCardLayout; code: string }) => void;
    function Harness() {
      const [state, set] = useState({ layout: BIG, code: 'ABC-001' });
      show = set;
      return <MediaCard item={item(19, { is_jav: true, code: state.code, has_cover: true })} variant="grid"
        layout={state.layout} selected={false} selectMode={false} seekSeconds={10} helpers={helpers}
        actions={actionsFor()} onOpen={vi.fn()} />;
    }
    const host = await mount(<Harness />);
    const cover = host.querySelector<HTMLImageElement>('[data-media-art] img')!;
    const firstSrc = cover.getAttribute('src');
    expect([cover.classList.contains('front'), cover.dataset.javImageLayout]).toEqual([true, 'big']);

    await act(async () => show({ layout: { ...BIG, size: 'small' }, code: 'ABC-001' }));
    expect(host.querySelector('[data-media-art] img')).toBe(cover);
    expect([cover.classList.contains('whole'), cover.classList.contains('front'), cover.dataset.javImageLayout]).toEqual([true, false, 'small']);
    expect(cover.getAttribute('src')).toBe(firstSrc);
    expect(host.querySelector<HTMLElement>('[data-media-pic]')!.style.getPropertyValue('--card-ratio')).toBe(String(16 / 9));

    await act(async () => show({ layout: { ...BIG, size: 'small' }, code: 'ABC-002' }));
    expect(host.querySelector('[data-media-art] img')).not.toBe(cover);
    expect(host.querySelector('[data-media-art] img')?.getAttribute('src')).toContain('ABC-002');
  });
});
