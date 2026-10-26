/* 实体页正文岛：三个视图里各画什么、点下去交给壳的是什么，以及换筛选时骨架与卡片的交接。
 *
 * 名册与卡片本身各有自己的用例（`index-page.test.tsx`、`catalog-grid.test.tsx`），这里只看正文
 * 这一层把谁接到了哪儿。骨架淡出的几何、续页与 Shift 连选由 e2e 在真浏览器里量。 */
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { act, useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type {
  EntityBodyActions, EntityBodyHelpers, EntityBodyProps, EntityPhotos,
} from '../../src/react/entity-body/entity-body';
import { EntityBodyPage } from '../../src/react/entity-body/entity-body-page';
import type { MediaItem, MediaPage } from '../../src/react/catalog-grid/types';
import { openPhotoLightbox } from '../../src/react/photo-lightbox/photo-lightbox-dialog';
import { queryClient } from '../../src/react/query';
import { click, mount } from './render';

/* 灯箱自己的开合、翻页与缩放在 `photo-lightbox.test.tsx`；这里只看墙把哪一列、从第几张交给它。 */
vi.mock('../../src/react/photo-lightbox/photo-lightbox-dialog', () => ({ openPhotoLightbox: vi.fn(async () => {}) }));

afterEach(() => { queryClient.clear() });
notifyManager.setScheduler((notify) => notify());

const item = (id: number): MediaItem => ({ id, name: `作品 ${id}`, has_thumb: true });
const page = (ids: number[], total = ids.length): MediaPage => ({ items: ids.map(item), total });

function helpers(): EntityBodyHelpers {
  return {
    badgeHtml: () => '', titleHtml: (_it, raw) => raw, displayName: (_it, raw) => raw,
    tagLabel: (tag) => tag,
    personAvatar: vi.fn(() => ({ html: '<span class="ini">A</span>', face: '' })),
  };
}

function actions(): EntityBodyActions {
  return {
    open: vi.fn(), openResource: vi.fn(), openShort: vi.fn(), openShorts: vi.fn(), openMix: vi.fn(),
    openEntity: vi.fn(), openUnowned: vi.fn(), toggleTag: vi.fn(), toggleSelection: vi.fn(),
    watchLater: vi.fn(async () => {}), resourceOperation: vi.fn(async () => {}), mixRelated: vi.fn(async () => []),
    canFlip: () => true, revealSource: vi.fn(async () => ''), loadMorePhotos: vi.fn(async () => {}),
  };
}

function props(patch: Partial<EntityBodyProps> = {}): EntityBodyProps {
  return {
    kind: 'performer', name: '篠田ゆう', view: 'videos', roster: null, items: page([1, 2, 3]), revision: 1,
    fetchPage: vi.fn(async () => page([])), photos: null, photoSize: 'small', photoLayout: 'masonry',
    helpers: helpers(), actions: actions(),
    layout: { active: false, size: 'small', portrait: false, javImage: 'cover' },
    selectMode: false, selected: new Set(), seekSeconds: 10, wireDrag: vi.fn(),
    skeletonHtml: () => '<div data-test-skeleton>骨架</div>', groupCollapse: true, canLoadMore: () => false,
    ...patch,
  };
}

let push: (patch: Partial<EntityBodyProps>) => void = () => {};
/** 壳经 `updateManagedRoute` 推进来的补丁，这里用一层状态代替。 */
function Shell(given: EntityBodyProps) {
  const [current, set] = useState(given);
  push = (patch) => set((value) => ({ ...value, ...patch }));
  return <EntityBodyPage {...current} />;
}
const open = (given: EntityBodyProps) =>
  mount(<QueryClientProvider client={queryClient}><Shell {...given} /></QueryClientProvider>);

const cards = (host: HTMLElement) =>
  [...host.querySelectorAll<HTMLElement>('[data-entity-grid] [data-media-grid] > [data-media-card]')]
    .map((card) => card.dataset.id);

describe('名册', () => {
  it('事务所页摆索引页那一格艺人，竖幅按检出的脸取景，点一格回壳打开这个人', async () => {
    const given = props({
      kind: 'agency', view: 'people',
      roster: { kind: 'performers', people: [{ k: '河北彩花', n: 12 }, { k: '小湊よつ葉', n: 3 }], layout: 'big' },
      helpers: { ...helpers(), personAvatar: vi.fn(() => ({ html: '<img src="/entity-image?id=1" alt="">', face: '40% 20%' })) },
    });
    const host = await open(given);
    const grid = host.querySelector<HTMLElement>('[data-index-grid]');
    expect(grid?.dataset.cells).toBe('people');
    expect(grid?.dataset.layout).toBe('big');
    const ring = host.querySelector<HTMLElement>('[data-person-ring]');
    expect(ring?.dataset.fitNative).toBe('portrait');
    expect(ring?.style.getPropertyValue('--face')).toBe('40% 20%');
    expect([...host.querySelectorAll('[data-index-cell]')].map((cell) => cell.querySelector('[data-index-readout]')?.textContent))
      .toEqual(['12 个视频', '3 个视频']);
    await click(host.querySelector('[data-index-cell][data-k="小湊よつ葉"]'));
    expect(given.actions.openEntity).toHaveBeenCalledWith('performer', '小湊よつ葉');
    expect(host.querySelector('[data-entity-grid]')).toBeNull();
  });

  it('片商页的名册是旗下厂牌，一格是公司的方标', async () => {
    const given = props({
      kind: 'studio', view: 'people', roster: { kind: 'studios', people: [{ k: 'S1 NO.1 STYLE', n: 40 }], layout: 'compact' },
    });
    const host = await open(given);
    expect(host.querySelector<HTMLElement>('[data-index-grid]')?.dataset.cells).toBe('company');
    expect(host.querySelector<HTMLElement>('[data-person-ring]')?.dataset.fitNative).toBe('mark');
    expect(given.helpers.personAvatar).toHaveBeenCalledWith(expect.objectContaining({ k: 'S1 NO.1 STYLE' }), 'studio', false);
    await click(host.querySelector('[data-index-cell]'));
    expect(given.actions.openEntity).toHaveBeenCalledWith('studio', 'S1 NO.1 STYLE');
  });

  it('上千人的名册先摆 120 格，「载入更多」一次再接 120 格，摆完就收起', async () => {
    const people = Array.from({ length: 250 }, (_, at) => ({ k: `艺人${at}`, n: 1 }));
    const host = await open(props({ kind: 'agency', view: 'people', roster: { kind: 'performers', people, layout: 'big' } }));
    const count = () => host.querySelectorAll('[data-index-cell]').length;
    expect(count()).toBe(120);
    await click(host.querySelector('[data-entity-more]'));
    expect(count()).toBe(240);
    await click(host.querySelector('[data-entity-more]'));
    expect(count()).toBe(250);
    expect(host.querySelector('[data-entity-more]')).toBeNull();
  });
});

describe('作品网格', () => {
  it('第一页由壳给，挂上就是卡片，不再取一遍', async () => {
    const given = props();
    const host = await open(given);
    expect(cards(host)).toEqual(['1', '2', '3']);
    expect(host.querySelector('[data-test-skeleton]')).toBeNull();
    expect(given.fetchPage).not.toHaveBeenCalled();
  });

  it('壳说正在取（items 为 null）就换成骨架，新列表推进来后换成新卡片', async () => {
    const host = await open(props());
    await act(async () => push({ items: null, revision: 2 }));
    expect(cards(host)).toEqual([]);
    expect(host.querySelector('[data-entity-grid] [data-test-skeleton]')).not.toBeNull();
    await act(async () => push({ items: page([7, 8]), revision: 2 }));
    expect(cards(host)).toEqual(['7', '8']);
  });

  it('选择状态与版式原样转给卡片', async () => {
    const host = await open(props());
    await act(async () => push({ selectMode: true, selected: new Set([2]) }));
    expect(host.querySelector('[data-media-sections]')?.hasAttribute('data-select-mode')).toBe(true);
    expect([...host.querySelectorAll<HTMLElement>('[data-media-card][data-selected]')].map((card) => card.dataset.id))
      .toEqual(['2']);
  });
});

const SETS = [
  { code: 'SSIS-057', name: '雨の日', n: 3, release_date: '2021-05-18', site_label: 'DMM' },
  { code: 'SSIS-001', name: '初夏', n: 2, release_date: '2021-02-19', site_label: 'DMM' },
];
const photos = (patch: Partial<EntityPhotos> = {}): EntityPhotos => ({
  revision: 1, codeSets: SETS, items: [{ id: 101, name: '101.jpg' }, { id: 102, name: '102.jpg' }],
  total: 2, hasMore: false, ...patch,
});
const openPhotos = (patch: Partial<EntityBodyProps> = {}) => {
  const given = props({ view: 'photos', photos: photos(), ...patch });
  return open(given).then((host) => ({ host, given }));
};
/** 每一段的段头标签和它下面那面墙的格数，按页面顺序。 */
const groups = (host: HTMLElement) => [...host.querySelectorAll('[data-photo-group]')].map((head) => [
  head.querySelector('b')?.textContent, head.nextElementSibling?.querySelectorAll('[data-photo-cell]').length,
]);
const indexes = (host: HTMLElement) =>
  [...host.querySelectorAll<HTMLElement>('[data-photo-cell]')].map((cell) => Number(cell.dataset.photoIndex));

describe('照片墙', () => {
  it('各部样张一部一段排在本地图片前面，段头写番号、标题与「来源 样张 · 发行日 · 张数」', async () => {
    const { host } = await openPhotos();
    expect(groups(host)).toEqual([['SSIS-057', 3], ['SSIS-001', 2], ['本地图片', 2]]);
    const head = host.querySelector('[data-photo-group]')!;
    expect(head.querySelector('[data-photo-group-title]')?.getAttribute('title')).toBe('雨の日');
    expect(head.querySelector('[data-photo-group-meta]')?.textContent).toBe('DMM 样张 · 2021-05-18 · 3 张');
    expect(host.querySelectorAll('[data-photo-group]')[2].querySelector('[data-photo-group-meta]')?.textContent)
      .toBe('2 张');
    expect(indexes(host)).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(host.querySelectorAll('[data-local-wall] [data-photo-cell]').length).toBe(2);
  });

  it('缩略图走服务端缓存的那一口，原图只归灯箱；图还没到时 alt 是文件名', async () => {
    const { host } = await openPhotos();
    const imgs = [...host.querySelectorAll('img')];
    expect(imgs[0].getAttribute('src')).toBe('/sample-thumb?code=SSIS-057&n=1');
    expect(imgs[5].getAttribute('src')).toBe('/photo-thumb?id=101');
    expect(imgs[5].getAttribute('alt')).toBe('101.jpg');
    expect(imgs.every((img) => img.getAttribute('loading') === 'lazy' && !img.hasAttribute('data-drop'))).toBe(true);
  });

  it('只有本地图片时不出段头，目录图集那一屏也是', async () => {
    const { host } = await openPhotos({ photos: photos({ codeSets: [] }) });
    expect(host.querySelector('[data-photo-group]')).toBeNull();
    expect(indexes(host)).toEqual([0, 1]);
  });

  it('点一格把整面墙交给灯箱，序号跨段连续；样张走番号与序号，本地图带着定位', async () => {
    const { host, given } = await openPhotos();
    await click(host.querySelector('[data-photo-index="3"]'));
    const [index, slides, reveal] = vi.mocked(openPhotoLightbox).mock.calls.at(-1)!;
    expect(index).toBe(3);
    expect(slides).toHaveLength(7);
    expect(slides[3]).toEqual({
      src: '/sample-image?code=SSIS-001&n=1', thumb: '/sample-thumb?code=SSIS-001&n=1', name: 'SSIS-001 样张 1',
      asset: null, source: 'DMM',
    });
    expect(slides[5]).toEqual({
      src: '/photo?id=101', thumb: '/photo-thumb?id=101', name: '101.jpg', asset: { id: 101, name: '101.jpg' },
    });
    await reveal!.revealSource(101);
    expect(given.actions.revealSource).toHaveBeenCalledWith(101);
  });

  it('本地图取不到整格走，样张取不到只摘图留格；别的格子序号不变', async () => {
    const { host } = await openPhotos();
    await act(async () => {
      host.querySelector('[data-photo-index="5"] img')!.dispatchEvent(new Event('error'));
      host.querySelector('[data-photo-index="1"] img')!.dispatchEvent(new Event('error'));
    });
    expect(indexes(host)).toEqual([0, 1, 2, 3, 4, 6]);
    expect(host.querySelector('[data-photo-index="1"] img')).toBeNull();
  });

  it('大小档与版式只改墙上的属性，不重画已经取回的缩略图', async () => {
    const { host } = await openPhotos();
    const first = host.querySelector('[data-photo-cell] img');
    await act(async () => push({ photoSize: 'big', photoLayout: 'fixed' }));
    const walls = [...host.querySelectorAll<HTMLElement>('[data-photo-wall]')];
    expect(walls.map((wall) => `${wall.dataset.size}/${wall.dataset.layout}`)).toEqual(Array(3).fill('big/fixed'));
    expect(host.querySelector('[data-photo-cell] img')).toBe(first);
  });

  it('还有下一页时出「载入更多」，点下去回壳取；接上的一页排在本地图片末尾', async () => {
    const { host, given } = await openPhotos({ photos: photos({ hasMore: true }), canLoadMore: () => true });
    await click(host.querySelector('[data-entity-more]'));
    expect(given.actions.loadMorePhotos).toHaveBeenCalled();
    await act(async () => push({ photos: photos({ items: [...photos().items, { id: 103, name: '103.jpg' }] }) }));
    expect(indexes(host)).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
    expect(host.querySelector('[data-entity-more]')).toBeNull();
  });
});
