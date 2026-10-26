/* 实体页筛选浮层岛：三种视图下各摆出哪些键、点下去交给壳的是什么、照片换一批的忙态。
 *
 * 滑动玻璃的落位、吸顶、窄屏横滚与让位、骨架接管时的几何由 `frontend/e2e/entity-filter.test.ts`
 * 在真浏览器里量；jsdom 没有布局，这里不断言玻璃。 */
import { act } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { EntityFilterPage } from '../../src/react/entity-filter/entity-filter-page';
import {
  videoOnly, type EntityFilterActions, type EntityFilterHelpers, type EntityFilterProps, type EntityOnlineHead,
} from '../../src/react/entity-filter/entity-filter';
import { FOLLOW_FILTERS } from '../../src/react/follow-feed/follow-feed';
import { click, mount, settle } from './render';

const STATES = [{ k: '', label: '全部' }, { k: 'fresh', label: '没看过' }, { k: 'later', label: '稍后看' }, { k: 'flagged', label: '已标记' }];
const LAYOUTS = [['big', '大图', 'maximize'], ['small', '小图', 'layout-grid']] as const;

function actions(patch: Partial<EntityFilterActions> = {}): EntityFilterActions {
  return {
    setView: vi.fn(), setState: vi.fn(), toggleTag: vi.fn(), clearFilter: vi.fn(), clearAll: vi.fn(),
    setSort: vi.fn(), reshuffle: vi.fn(async () => {}), setJavLayout: vi.fn(), setPhotoLayout: vi.fn(),
    photoBack: vi.fn(), onlineMedia: vi.fn(), onlineShuffle: vi.fn(), onlineImagesOnly: vi.fn(), onlineStatus: vi.fn(),
    onlineProvider: vi.fn(), onlineTag: vi.fn(), onlineSort: vi.fn(), ...patch,
  };
}

function helpers(patch: Partial<EntityFilterHelpers> = {}): EntityFilterHelpers {
  return { wireDrag: vi.fn(), wireScroller: vi.fn(), sourceToolsHtml: () => '', wireSourceTools: vi.fn(), ...patch };
}

function props(patch: Partial<EntityFilterProps> = {}): EntityFilterProps {
  return {
    kind: 'performer', name: '篠田ゆう', view: 'videos',
    views: { label: '媒体类型', people: null, videos: { count: 24 }, photos: { count: 18 }, online: null },
    state: '', states: STATES,
    tags: [{ k: '巨乳', label: '巨乳', n: 1200, selected: false }, { k: '中出', label: '中出', n: 36, selected: true }],
    combo: [{ kind: 'untag', key: '中出', label: '中出' }, { kind: 'clear', key: 'studio', label: '厂牌 S1' }],
    readout: '视频 · 24 · 中出', busy: false,
    video: {
      sorts: [
        { key: 'release', label: '发行时间', pressed: true, dir: 'desc', ariaLabel: '按发行时间从旧到新排序' },
        { key: 'rating', label: '评分', pressed: false, dir: '', ariaLabel: '按评分从高到低排序' },
      ],
      jav: { layout: 'big', options: LAYOUTS },
    },
    photo: null, online: null, actions: actions(), helpers: helpers(), ...patch,
  };
}

const $ = (host: HTMLElement, selector: string) => host.querySelector<HTMLElement>(selector);
const $$ = (host: HTMLElement, selector: string) => [...host.querySelectorAll<HTMLElement>(selector)];

describe('只在作品视图上成立的那几样', () => {
  it('观看状态、标签与交集条只跟作品视图走', () => {
    expect(videoOnly('videos')).toBe(true);
    expect(videoOnly('photos')).toBe(false);
    expect(videoOnly('people')).toBe(false);
  });
});

describe('作品视图', () => {
  it('视图键、观看状态、标签与计数、交集条、排序与版式都摆出来，按下态照 props', async () => {
    const host = await mount(<EntityFilterPage {...props()} />);
    expect($$(host, '[data-media-view]').map((key) => [key.dataset.mediaView, key.getAttribute('aria-pressed')]))
      .toEqual([['videos', 'true'], ['photos', 'false']]);
    expect($(host, '[data-media-view="photos"]')?.getAttribute('aria-label')).toBe('照片 18');
    expect($(host, '[data-entity-states]')?.hidden).toBe(false);
    expect($(host, '[data-entity-state=""]')?.getAttribute('aria-pressed')).toBe('true');
    expect($$(host, '[data-entity-tag]').map((tag) => [tag.textContent, tag.getAttribute('aria-pressed')]))
      .toEqual([['巨乳1,200', 'false'], ['中出36', 'true']]);
    expect($(host, '[data-entity-combo]')?.hidden).toBe(false);
    expect($$(host, '[data-combo-chip]').map((chip) => chip.firstChild?.textContent)).toEqual(['中出', '厂牌 S1']);
    expect($(host, '[data-entity-readout]')?.textContent).toBe('视频 · 24 · 中出');
    expect($(host, '[data-entity-sort="release"]')?.querySelector('svg')).not.toBeNull();
    expect($(host, '[data-entity-sort="rating"]')?.querySelector('svg')).toBeNull();
    expect($(host, '[data-entity-sort="rating"]')?.getAttribute('aria-label')).toBe('按评分从高到低排序');
    expect($(host, '[data-entity-layout][aria-label="JAV 卡片版式"]')).not.toBeNull();
    expect($(host, '[data-photo-back]')).toBeNull();
  });

  it('点下去都回壳：换视图、换状态、标签、交集条撤一颗或全清、排序、换一批', async () => {
    const shell = actions();
    const host = await mount(<EntityFilterPage {...props({ actions: shell })} />);
    await click($(host, '[data-media-view="photos"]')!);
    await click($(host, '[data-entity-state="later"]')!);
    await click($(host, '[data-entity-state=""]')!);
    await click($(host, '[data-entity-tag="巨乳"]')!);
    await click($(host, '[aria-label="撤掉 中出"]')!);
    await click($(host, '[aria-label="撤掉 厂牌 S1"]')!);
    await click($(host, '[data-combo-clear]')!);
    await click($(host, '[data-entity-sort="rating"]')!);
    await click($(host, '[data-entity-batch]')!);
    expect(shell.setView).toHaveBeenCalledWith('photos');
    // 已经按下的那一枚再点不发请求。
    expect(shell.setState).toHaveBeenCalledTimes(1);
    expect(shell.setState).toHaveBeenCalledWith('later');
    expect(shell.toggleTag).toHaveBeenNthCalledWith(1, '巨乳');
    expect(shell.toggleTag).toHaveBeenNthCalledWith(2, '中出');
    expect(shell.clearFilter).toHaveBeenCalledWith('studio');
    expect(shell.clearAll).toHaveBeenCalledTimes(1);
    expect(shell.setSort).toHaveBeenCalledWith('rating');
    expect(shell.reshuffle).toHaveBeenCalledTimes(1);
  });

  it('等这一趟取数时读数换成微光，下排标 aria-busy', async () => {
    const host = await mount(<EntityFilterPage {...props({ busy: true })} />);
    expect($(host, '[data-entity-readout] [data-skeleton="count"]')).not.toBeNull();
    expect($(host, '[data-filter-row="bottom"]')?.getAttribute('aria-busy')).toBe('true');
  });

  it('横滚行登记给遗留层的拖动与滚轮', async () => {
    const wiring = helpers();
    const host = await mount(<EntityFilterPage {...props({ helpers: wiring })} />);
    expect(wiring.wireDrag).toHaveBeenCalledWith($(host, '[data-entity-tags]'));
    expect(wiring.wireDrag).toHaveBeenCalledWith($(host, '[data-filter-row="bottom"]'));
    expect(wiring.wireScroller).toHaveBeenCalledWith($(host, '[data-entity-scroll]'));
  });

  it('只有一类东西时没有视图键，也不画它后面那道分隔线', async () => {
    const host = await mount(<EntityFilterPage {...props({ views: null })} />);
    expect($(host, '[data-entity-media]')).toBeNull();
    expect($$(host, '[data-entity-filter-glass] [data-filter-row="top"] > [data-entity-sep]')).toHaveLength(0);
  });
});

describe('照片与名册视图', () => {
  it('照片视图收起观看状态、标签与交集条，下排只有换一批与图片布局', async () => {
    const host = await mount(<EntityFilterPage {...props({
      view: 'photos', readout: '照片 · 18 张', video: null,
      photo: { back: false, shuffle: true, layout: 'masonry', layouts: [['fixed', '固定比例', 'layout-grid'], ['masonry', '瀑布流', 'columns-2']], setId: 0 },
    })} />);
    expect($(host, '[data-entity-states]')?.hidden).toBe(true);
    expect($$(host, '[data-entity-tag]')).toHaveLength(0);
    expect($(host, '[data-entity-combo]')?.hidden).toBe(true);
    expect($$(host, '[data-entity-sort]')).toHaveLength(0);
    expect($(host, '[data-entity-layout][aria-label="图片布局"]')).not.toBeNull();
    expect($$(host, '[data-entity-batch]')).toHaveLength(1);
    expect($(host, '[data-photo-back]')).toBeNull();
  });

  it('照片换一批：键自己转圈、再按不重发，等壳的 Promise 落定才复原', async () => {
    let finish = () => {};
    const shell = actions({ reshuffle: vi.fn(() => new Promise<void>((resolve) => { finish = resolve; })) });
    const host = await mount(<EntityFilterPage {...props({
      view: 'photos', video: null, actions: shell,
      photo: { back: false, shuffle: true, layout: 'fixed', layouts: [['fixed', '固定比例', 'layout-grid']], setId: 0 },
    })} />);
    // 换一批洗的是这一批的成员，不是把同一批重新取一遍：字形是洗牌，不是转圈的刷新。
    expect($(host, '[data-entity-batch] use')?.getAttribute('href')).toBe('#i-shuffle');
    await click($(host, '[data-entity-batch]')!);
    expect($(host, '[data-entity-batch]')?.getAttribute('aria-busy')).toBe('true');
    await click($(host, '[data-entity-batch]')!);
    expect(shell.reshuffle).toHaveBeenCalledTimes(1);
    await act(async () => { finish(); });
    await settle();
    expect($(host, '[data-entity-batch]')?.hasAttribute('aria-busy')).toBe(false);
  });

  it('图集里左边一枚「全部照片」，右边接上源文件键；只有样张时没有换一批', async () => {
    const shell = actions();
    const wiring = helpers({ sourceToolsHtml: (id) => `<button type="button" data-src="${id}">定位</button>` });
    const host = await mount(<EntityFilterPage {...props({
      view: 'photos', video: null, actions: shell, helpers: wiring, readout: '夏の日 · 12 张',
      photo: { back: true, shuffle: false, layout: 'fixed', layouts: [['fixed', '固定比例', 'layout-grid']], setId: 42 },
    })} />);
    expect($$(host, '[data-entity-batch]')).toHaveLength(0);
    expect($(host, '[data-entity-srctools] [data-src="42"]')).not.toBeNull();
    expect(wiring.wireSourceTools).toHaveBeenCalledWith($(host, '[data-entity-srctools]'));
    await click($(host, '[data-photo-back]')!);
    expect(shell.photoBack).toHaveBeenCalledTimes(1);
  });

  it('名册视图下排提供读数与收藏入口，名册键在最左', async () => {
    const shell = actions();
    const host = await mount(<EntityFilterPage {...props({
      kind: 'agency', view: 'people', readout: '艺人 · 3', video: null, actions: shell,
      views: { label: '页面视图', people: { label: '艺人', count: 3, icon: 'user-round' }, videos: { count: 24 }, photos: null, online: null },
    })} />);
    expect($$(host, '[data-media-view]').map((key) => key.dataset.mediaView)).toEqual(['people', 'videos']);
    expect($(host, '[data-media-view="people"]')?.getAttribute('aria-pressed')).toBe('true');
    expect($(host, '[data-entity-media]')?.getAttribute('aria-label')).toBe('页面视图');
    expect($$(host, '[data-filter-row="bottom"] > *').map((node) => node.tagName)).toEqual(['H3', 'SPAN']);
    expect($(host, '[data-filter-row="bottom"] [aria-label="我的收藏"]')).not.toBeNull();
    await click($(host, '[data-media-view="videos"]')!);
    expect(shell.setView).toHaveBeenCalledWith('videos');
  });
});

describe('在线视图', () => {
  const head = (patch: Partial<EntityOnlineHead> = {}): EntityOnlineHead => ({
    media: 'videos', mediaCounts: { videos: 4, images: 9 }, photoLayout: 'masonry',
    photoLayouts: [['fixed', '固定比例', 'layout-grid'], ['masonry', '瀑布流', 'columns-2']], imagesOnly: false,
    status: '', statuses: FOLLOW_FILTERS, provider: '', providers: [['rule34video', 'Rule34Video']],
    tags: [{ k: 'cum', label: 'cum', n: 3, selected: true, cat: 'general' }],
    sorts: [{ key: 'new', label: '更新时间', pressed: true, dir: 'desc', ariaLabel: '更新时间' }], ...patch,
  });
  const online = (patch: Partial<EntityFilterProps> = {}) => props({
    kind: 'creator', name: 'Jul3D', view: 'online', readout: '在线 · 13 项更新', video: null, tags: [], combo: [],
    views: { label: '媒体类型', people: null, videos: { count: 2 }, photos: null, online: { count: 13 } }, ...patch,
  });

  it('在线键排在最末；状态换成关注的四档，来源与标签是这一位自己的，媒体与排序各自一组', async () => {
    const host = await mount(<EntityFilterPage {...online({ online: head() })} />);
    expect($$(host, '[data-entity-media]:not([data-online-media]) > [data-media-view]')
      .map((key) => key.dataset.mediaView)).toEqual(['videos', 'online']);
    expect($$(host, '[data-entity-state]').map((key) => key.dataset.entityState)).toEqual(['', 'new', 'saved', 'ignored']);
    expect($(host, '[data-entity-states]')?.hidden).toBe(false);
    expect($(host, '[data-follow-provider="rule34video"]')).not.toBeNull();
    expect($(host, '[data-follow-tag="cum"]')?.getAttribute('aria-pressed')).toBe('true');
    expect($$(host, '[data-online-media] > [data-media-view]').map((key) => key.dataset.mediaView)).toEqual(['videos', 'images']);
    expect($(host, '[data-follow-images-only]'), '视频那一档没有照片墙的控件').toBeNull();
    expect($(host, '[data-entity-layout][aria-label="图片布局"]')).toBeNull();
  });

  it('点下去回调各自的在线动作，不走作品视图那一套', async () => {
    const shell = actions();
    const host = await mount(<EntityFilterPage {...online({ actions: shell, online: head() })} />);
    await click($(host, '[data-entity-state="new"]')!);
    await click($(host, '[data-follow-provider="rule34video"]')!);
    await click($(host, '[data-follow-tag="cum"]')!);
    await click($(host, '[data-online-media] [data-media-view="images"]')!);
    await click($(host, '[data-entity-sort="new"]')!);
    expect(shell.onlineStatus).toHaveBeenCalledWith('new');
    expect(shell.onlineProvider).toHaveBeenCalledWith('rule34video');
    expect(shell.onlineTag).toHaveBeenCalledWith('cum');
    expect(shell.onlineMedia).toHaveBeenCalledWith('images');
    expect(shell.onlineSort).toHaveBeenCalledWith('new');
    expect([shell.setState, shell.toggleTag, shell.setSort].every((fn) => vi.mocked(fn).mock.calls.length === 0)).toBe(true);
  });

  it('图片那一档带上图片布局与仅显示图片，和关注页的照片墙同一组', async () => {
    const shell = actions();
    const host = await mount(<EntityFilterPage {...online({ actions: shell, online: head({ media: 'images', imagesOnly: true }) })} />);
    expect($(host, '[data-entity-layout][aria-label="图片布局"]')).not.toBeNull();
    expect($(host, '[data-follow-images-only]')?.getAttribute('aria-pressed')).toBe('true');
    await click($(host, '[data-follow-images-only]')!);
    expect(shell.onlineImagesOnly).toHaveBeenCalledWith(false);
  });

  it('只有照片、没有别的视图键时上排整行不出', async () => {
    const host = await mount(<EntityFilterPage {...props({
      view: 'photos', views: null, readout: '照片 · 18 张', video: null,
      photo: { back: false, shuffle: true, layout: 'masonry', layouts: [['masonry', '瀑布流', 'columns-2']], setId: 0 },
    })} />);
    expect($(host, '[data-filter-row="top"]')).toBeNull();
    expect($(host, '[data-filter-row="bottom"]')).not.toBeNull();
  });
});
