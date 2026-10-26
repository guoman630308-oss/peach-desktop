import { describe, expect, it, vi } from 'vitest';
import { createEntityController, type EntityContext, type EntityControllerPorts } from '../../src/react/entity-page/entity-controller';
import { createEntityLoading } from '../../src/react/entity-page/entity-loading';
import type { EntityPageHelpers } from '../../src/react/entity-page/entity-page';
import { entityPortraitImg } from '../../src/react/entity-page/entity-portrait';
import { createEntityEntryState, emptyEntityFilters, entityFilterSearch, entityViewPath, parseEntityFilters, parseMediaView } from '../../src/react/entity-page/entity-route';
import { createPhotoSurfaceController, photoViewActive } from '../../src/react/entity-page/photo-surface';
import { createIndexController, indexPath, personAvatar } from '../../src/react/index/index-controller';
import type { FollowFeedActions, FollowFeedHelpers } from '../../src/react/follow-feed/follow-feed';

const followHelpers: FollowFeedHelpers = { workMark: () => '', tagLabel: tag => tag, wireDrag: vi.fn(), wireScroller: vi.fn(),
  listSkeletonHtml: () => '', jobProgress: vi.fn() };
const followActions: FollowFeedActions = { route: vi.fn(), shuffle: vi.fn(), loaded: vi.fn(), openDetail: vi.fn(), openAuthor: vi.fn(), openManage: vi.fn(),
  toggleSelection: vi.fn(), setImagesOnly: vi.fn(), setPhotoLayout: vi.fn(), canFlip: () => true, toast: vi.fn(),
  failure: vi.fn(), checkReport: vi.fn() };
const helpers: EntityPageHelpers = { portraitImg: entityPortraitImg, wireDrag: vi.fn(), wireScroller: vi.fn(), wireFeedRow: vi.fn(),
  feedRowHtml: () => '', receipt: vi.fn(), failure: vi.fn(), aliasForm: async () => {}, sourceToolsHtml: () => '',
  wireSourceTools: vi.fn(), comboItems: () => [], sortKeys: () => [] };
const preference = { sort: 'rating', dir: 'asc' };

function setup() {
  let context: EntityContext | null = { kind: 'creator', name: '甲', filters: { ...emptyEntityFilters(), tag: 'one' } };
  let seed = '7', live = true, current = true, photos = false;
  const ports: EntityControllerPorts = {
    context: () => context, live: () => live, current: () => current, route: vi.fn(), suffix: () => '?media=online#works',
    restoreHomeContext: vi.fn(), releaseBodyPreviews: vi.fn(), push: vi.fn(), state: () => ({ seed, jav: false }),
    preferences: () => ({ revision: 3, feedRevision: 2, photoSize: 'small', photoLayout: 'fixed', followImagesOnly: false,
      javLayout: 'cover', javLayouts: [], photoLayouts: [], states: [], peopleLayout: 'compact',
      layout: { active: true, size: 'small', portrait: false, javImage: 'cover' }, selectMode: true, selected: new Set([11]),
      seekSeconds: 6, groupCollapse: false, wireDrag: vi.fn(), skeletonHtml: () => '', canLoadMore: () => true,
      follow: { helpers: followHelpers, actions: followActions } }),
    changeFilter: change => { const filters = { ...context?.filters }; change(filters); if (context) context = { ...context, filters } },
    rollSeed: () => '31', writeSeed: value => { seed = value }, setJavLayout: vi.fn(), javLayout: () => 'poster',
    setPhotoLayout: vi.fn(), syncPhotoWalls: vi.fn(), openEntity: vi.fn(), photoViewActive: () => photos,
    recordPainted: (_view, wall) => { photos = wall }, syncDensity: vi.fn(), scheduleSticky: vi.fn(),
    releasePage: vi.fn(), showMissing: vi.fn(), dropBars: vi.fn(),
  };
  const delegated = { toggleTag: vi.fn(), openFollowAuthor: vi.fn(), javContext: vi.fn() };
  return { ports, controller: createEntityController(ports, helpers, delegated),
    context: (value: EntityContext | null) => { context = value },
    live: (value: boolean) => { live = value }, current: (value: boolean) => { current = value } };
}

describe('实体与索引域控制', () => {
  it('排序按地址与偏好解析，默认项省略，图集只在照片视图认数字 set', () => {
    expect(parseEntityFilters('?sort=short', preference)).toMatchObject({ sort: 'dur', dir: 'asc' });
    expect(parseEntityFilters('', { sort: 'new', dir: 'asc' })).toMatchObject({ sort: 'new', dir: 'asc' });
    expect(entityFilterSearch(emptyEntityFilters())).toBe('');
    expect(parseMediaView('?media=photos&set=12')).toEqual({ media: 'photos', set: 12 });
    expect(parseMediaView('?media=photos&set=-1')).toEqual({ media: 'photos', set: 0 });
    expect(parseMediaView('?media=online&set=12')).toEqual({ media: 'online', set: 0 });
    expect(entityViewPath('creator', '甲/乙', emptyEntityFilters(), { media: 'photos', set: 12 }))
      .toBe('/creators/%E7%94%B2%2F%E4%B9%99?media=photos&set=12');
  });

  it('空筛选进入标记消费一次，后退和深链读 URL，创作者自身不叠 creator 筛选', () => {
    const entry = createEntityEntryState();
    expect(entry.fresh('creator', '甲')).toBe('/creators/%E7%94%B2');
    expect(entry.read('creator', '甲', '', preference).filters).toEqual(emptyEntityFilters());
    expect(entry.read('creator', '甲', '?creator=乙&tag=one&media=online', preference))
      .toMatchObject({ filters: { creator: '', tag: 'one' }, media: { media: 'online', set: 0 } });
    entry.fresh('performer', '甲');
    entry.read('performer', '乙', '', preference);
    expect(entry.read('performer', '甲', '?sort=short', preference).filters.sort).toBe('dur');
  });

  it('route 只推当前实体，POP 不写地址，props 使用当前种子与偏好', () => {
    const { controller, ports, live } = setup(), filters = emptyEntityFilters();
    expect(controller.route('creator', '甲', filters, { media: 'online', set: 0 }, { push: false })).toBe(true);
    expect(ports.route).not.toHaveBeenCalled();
    expect(ports.push).toHaveBeenCalledWith({ filters, media: { media: 'online', set: 0 }, seed: '7', jav: false });
    live(false);
    vi.mocked(ports.push).mockClear();
    expect(controller.route('creator', '甲', filters)).toBe(false);
    expect(ports.route).toHaveBeenCalledWith('/creators/%E7%94%B2');
    expect(ports.push).not.toHaveBeenCalled();
    const props = controller.props('creator', '甲', filters, { media: 'videos', set: 0 });
    expect(props).toMatchObject({ revision: 3, photoSize: 'small', seed: '7', jav: false, filters });
  });

  it('同一动作对象现读筛选，换一批写种子，替换名字保留查询，头像刷新失效顶栏缓存', () => {
    const { controller, ports, context } = setup(), actions = controller.actions('creator', '甲');
    expect(controller.actions('creator', '甲')).toBe(actions);
    context({ kind: 'creator', name: '甲', filters: { ...emptyEntityFilters(), sort: 'rating', dir: 'desc', tag: 'two' } });
    actions.setSort('rating');
    expect(ports.push).toHaveBeenLastCalledWith(expect.objectContaining({ filters: expect.objectContaining({ dir: 'asc', tag: 'two' }) }));
    expect(actions.reshuffleVideos()).toBe('31');
    expect(ports.push).toHaveBeenLastCalledWith(expect.objectContaining({ seed: '31' }));
    actions.openEntity('creator', '乙', true);
    expect(ports.route).toHaveBeenLastCalledWith('/creators/%E4%B9%99?media=online#works', true);
    actions.avatarChanged();
    expect(ports.dropBars).toHaveBeenCalledOnce();
  });

  it('照片视图切入与切出才同步密度，未挂载的 missing 微任务无操作', async () => {
    const { controller, ports, current } = setup(), actions = controller.actions('creator', '甲');
    actions.painted('photos', true); actions.painted('photos', true); actions.painted('videos', false);
    expect(ports.syncDensity).toHaveBeenCalledTimes(2);
    expect(ports.scheduleSticky).toHaveBeenCalledTimes(3);
    actions.missing(); current(false);
    await Promise.resolve();
    expect(ports.releasePage).not.toHaveBeenCalled();
    current(true); actions.missing();
    await Promise.resolve();
    expect(ports.showMissing).toHaveBeenCalledWith('creator');
  });

  it('异步名单等待与重读都尊重页面取消权', async () => {
    let resolveWait = () => {}, resolveLoad = () => {}, current = true;
    const wait = new Promise<void>(resolve => { resolveWait = resolve });
    const load = new Promise<void>(resolve => { resolveLoad = resolve });
    const ports = { shapesReady: () => false, waitShapes: () => wait, paintSkeleton: vi.fn(), resetView: vi.fn(),
      loadShapes: () => load, syncParts: vi.fn() };
    const loading = createEntityLoading(ports);
    const first = loading('creator', '甲', () => current);
    current = false; resolveWait(); await first;
    expect(ports.paintSkeleton).not.toHaveBeenCalled();
    current = true; await loading('creator', '乙', () => current);
    expect(ports.paintSkeleton).toHaveBeenCalledWith('creator', '乙');
    current = false; resolveLoad(); await Promise.resolve();
    expect(ports.syncParts).not.toHaveBeenCalled();
  });

  it('索引地址只写适用维度，在线作者跳转先收索引再写关注目标', () => {
    expect(indexPath({ kind: 'studios', q: '甲 / 乙', scope: 'online', view: 'cloud', category: 'all' }))
      .toBe('/studios?q=%E7%94%B2+%2F+%E4%B9%99');
    expect(indexPath({ kind: 'tags', q: '', scope: 'online', view: 'alphabet', category: 'artist' }))
      .toBe('/tags?view=alphabet&scope=online&category=artist');
    const calls: string[] = [];
    const ports = { route: vi.fn(), followView: () => ({ status: '' as const, media: 'videos' as const, author: '', provider: '',
      work: 'old', tags: [], durMin: 0, durMax: 0, sort: 'new' as const, dir: 'desc' as const, seed: 7 }),
    adoptFollowView: vi.fn(), hideIndex: () => { calls.push('hide') }, openPage: (path: string) => { calls.push(path) } };
    createIndexController(ports).openFollowAuthor('甲');
    expect(calls).toEqual(['hide', '/follow?author=%E7%94%B2']);
    expect(ports.adoptFollowView).toHaveBeenCalledWith(expect.objectContaining({ work: '' }));
  });

  it('公司不以代表作作头像，人物占位说明和版本保留，创作者回落关注头像', () => {
    const person = { k: '甲', n: 1, id: 3, has_avatar: true, rep: 9, avatar_stand_in: true };
    expect(personAvatar(person, 'studio', true).html).not.toContain('/avatar');
    expect(personAvatar(person, 'creator', false).html).toContain('代表作画面，非本人');
    const host = document.createElement('div');
    host.innerHTML = personAvatar({ ...person, has_image: true, image_version: 'v 2' }, 'creator', false).html;
    expect(new URL(host.querySelector('img')!.src).searchParams.get('v')).toBe('v 2');
    const entity = { id: 3, canonical_name: '甲', asset_count: 0, has_avatar: true, representative_asset_id: 9 };
    expect(entityPortraitImg('studio', entity)).toBe('');
    expect(entityPortraitImg('creator', { ...entity, has_avatar: false,
      follow: { key: 'k', n: 1, providers: [], avatar: 'https://example.test/a', avatar_fallback: 'https://example.test/b', held: [] } }))
      .toContain('referrerpolicy="no-referrer"');
  });

  it('异步照片墙未入 DOM 仍使用照片密度，尺寸同步仅推偏好不替换节点', () => {
    const fallback = vi.fn(() => false);
    expect(photoViewActive({ entityWall: true, entityCurrent: true, indexVisible: true, pathname: '/creators/甲',
      followMedia: 'videos', statsVisible: false }, fallback)).toBe(true);
    expect(fallback).not.toHaveBeenCalled();
    const wall = document.createElement('div'), child = document.createElement('img'); wall.append(child);
    const ports = { preferences: () => ({ size: 'big' as const, layout: 'masonry' as const, imagesOnly: true }),
      walls: () => [wall], pushEntity: vi.fn(), pushFollow: vi.fn(), syncDensity: vi.fn(), storeSize: vi.fn() };
    createPhotoSurfaceController(ports).setSize('big');
    expect(wall.firstChild).toBe(child);
    expect(wall.dataset).toMatchObject({ size: 'big', layout: 'fixed', imagesOnly: 'true' });
    expect(ports.pushEntity).toHaveBeenCalledWith({ photoSize: 'big', photoLayout: 'masonry', followImagesOnly: true });
    expect(ports.pushFollow).toHaveBeenCalledWith({ photoSize: 'big', photoLayout: 'masonry', imagesOnly: true });
  });

  it('厂牌大位和圆框分别取 large 与 ring，标识可回落实体图但不能回落代表作', () => {
    const company = { k: 'Peach & Co', n: 3, entity_id: 41, has_logo: true, logo_version: 'logo 2',
      has_image: true, image_version: 'face 2', has_avatar: true, rep: 91,
      avatar_focus: { axis: 'y', pct: 20 }, avatar_stand_in: true };
    for (const big of [true, false]) {
      const host = document.createElement('div');
      host.innerHTML = personAvatar(company, 'studio', big).html;
      const image = host.querySelector('img')!, url = new URL(image.src);
      expect(url.pathname).toBe('/logo');
      expect(url.searchParams.get('studio')).toBe(company.k);
      expect(url.searchParams.get('variant')).toBe(big ? 'large' : 'ring');
      expect(url.searchParams.get('v')).toBe('logo 2');
      expect(image.getAttribute('data-fallbacks')).toContain('/entity-image?kind=studio');
      expect(image.getAttribute('data-fallbacks')).not.toContain('/avatar');
      expect(image.getAttribute('data-fallback-note')).toBeNull();
      expect(image.style.objectPosition).toBe('');
    }
    const host = document.createElement('div');
    host.innerHTML = entityPortraitImg('studio', { id: 41, canonical_name: company.k, asset_count: 3,
      has_logo: true, logo_version: 'logo 2', has_image: true, image_version: 'face 2',
      has_avatar: true, representative_asset_id: 91, avatar_stand_in: true });
    const portrait = host.querySelector('img')!;
    expect(new URL(portrait.src).searchParams.get('variant')).toBe('large');
    expect(portrait.getAttribute('data-fallbacks')).not.toContain('/avatar');
  });
});
