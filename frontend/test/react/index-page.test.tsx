/* 五张索引页：过滤框、页面级 Tabs、版式、标签多选与分页。
 *
 * 首屏和真实路径一样先落进共用缓存（遗留层 `prefetch` 那一步），页面挂上去直接读；换档之后
 * 的那一问走假 fetch。去处（资料页、目录、关注页）与地址栏都经 props 交还给壳，这里只看
 * 页面把什么交了出去。 */
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { act, useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { refitNativeImages } from '@peach/card-art';

import { indexKey, indexRoute, prefetchIndex, type IndexProps, type IndexRoute } from '../../src/react/index/index-data';
import { rememberHistoryPages, shellNavigate } from '../../src/history';
import { IndexPage } from '../../src/react/index/index-page';
import { onlineAuthorsKey, onlineTagsKey } from '../../src/react/follow/online-vocab';
import { queryClient } from '../../src/react/query';
import { buttonNamed, click, mount, pending, settle, type } from './render';

/* 重量已加载完的图走 `card-art` 那一份；量出来的几何由 `test/card-art/` 守，这里只看什么时候量。 */
vi.mock('@peach/card-art', async (importOriginal) => ({
  ...await importOriginal<typeof import('@peach/card-art')>(), refitNativeImages: vi.fn(),
}));

afterEach(() => { queryClient.clear() });
notifyManager.setScheduler((notify) => notify());

const person = (k: string, patch: Record<string, unknown> = {}) => ({ k, n: 3, entity_id: 1, ...patch });
const tag = (k: string, cat = 'role') => ({ k, n: 2, cat });

function seed<T>(key: readonly unknown[], items: T[], more = false, categories?: Record<string, number>) {
  queryClient.setQueryData(key, { pages: [{ items, has_more: more, categories }], pageParams: [0] });
}

/** 按地址回话的假 fetch；`answer` 返回 `undefined` 时那一问一直挂着。 */
function serve(answer: (url: string) => unknown) {
  const fetcher = vi.fn(async (url: string) => {
    const body = await answer(url);
    return { ok: true, status: 200, json: async () => body };
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}

const urls = (fetcher: ReturnType<typeof serve>) => fetcher.mock.calls.map(([url]) => String(url));

it('历史返回复用分页缓存，缓存过期时取齐原有页数', async () => {
  shellNavigate('/performers?q=return');
  const original = window.history.state;
  rememberHistoryPages(3);
  shellNavigate('/performers/7');
  window.history.replaceState(original, '', '/performers?q=return');
  window.dispatchEvent(new PopStateEvent('popstate', { state: original }));
  const key = indexKey('performers', 'return', 'all');
  const cached = { pages: [{ items: [person('A')], has_more: true }], pageParams: [0] };
  queryClient.setQueryData(key, cached);
  const fetcher = serve(url => {
    const offset = Number(new URL(url, 'https://peach.test').searchParams.get('offset'));
    return { items: [person(String(offset)), person(String(offset + 1))], has_more: offset < 4 };
  });
  const route = { kind: 'performers', q: 'return', scope: 'local', view: 'alphabet', category: 'all' } as const;
  await prefetchIndex(route, new AbortController().signal);
  expect(fetcher).not.toHaveBeenCalled();
  expect(queryClient.getQueryData(key)).toEqual(cached);
  await queryClient.invalidateQueries({ queryKey: key, exact: true, refetchType: 'none' });
  await prefetchIndex(route, new AbortController().signal);
  expect(urls(fetcher).map(url => new URL(url, 'https://peach.test').searchParams.get('offset'))).toEqual(['0', '2', '4']);
  fetcher.mockClear();
  queryClient.removeQueries({ queryKey: key, exact: true });
  await prefetchIndex(route, new AbortController().signal);
  expect(urls(fetcher).map(url => new URL(url, 'https://peach.test').searchParams.get('offset'))).toEqual(['0', '2', '4']);
});

function props(route: Partial<IndexRoute> = {}, patch: Partial<IndexProps> = {}): IndexProps {
  return {
    kind: 'performers', q: '', scope: 'local', view: 'alphabet', category: 'all', ...route,
    layout: 'big', selectMode: false,
    route: vi.fn(), savePreference: vi.fn(), exitSelectMode: vi.fn(),
    personAvatar: vi.fn(() => ({ html: '<span class="ini">A</span>', face: '' })),
    authorAvatar: vi.fn(() => '<span class="ini">B</span>'),
    tagLabel: (value: string) => value,
    openEntity: vi.fn(), showTags: vi.fn(), openFollowAuthor: vi.fn(), openFollowTag: vi.fn(),
    configurable: true, ...patch,
  };
}

let setSelectMode: (on: boolean) => void = () => {};
/** 选择键归壳：壳经 `updateManagedRoute` 推进来的就是这一项，这里用一层状态代替。 */
function Shell(given: IndexProps) {
  const [selectMode, set] = useState(given.selectMode);
  setSelectMode = set;
  return <IndexPage {...given} selectMode={selectMode} />;
}

const open = (given: IndexProps) =>
  mount(<QueryClientProvider client={queryClient}><Shell {...given} /></QueryClientProvider>);

const cells = (host: ParentNode) => [...host.querySelectorAll('[data-index-cell]')];
const search = (host: ParentNode) => host.querySelector<HTMLInputElement>('[data-index-search] input')!;
const tab = (host: ParentNode, value: string) => host.querySelector(`[role="tab"][data-tab="${value}"]`);
const alphaTag = (host: ParentNode, k: string) => host.querySelector(`[data-alpha-tag][data-k="${k}"]`);

describe('地址栏上的类型', () => {
  it('认不出的类型回到「全部」，名册页不带词表那一档', () => {
    expect(indexRoute({ kind: 'tags', q: '', scope: 'local', view: 'alphabet', category: 'bogus' }).category).toBe('all');
    expect(indexRoute({ kind: 'tags', q: '', scope: 'local', view: 'alphabet', category: 'role' }).category).toBe('role');
    // 在线那一套的分类来自上游 tag_type，本地没有 `artist` 这一类。
    expect(indexRoute({ kind: 'tags', q: '', scope: 'local', view: 'alphabet', category: 'artist' }).category).toBe('all');
    expect(indexRoute({ kind: 'tags', q: '', scope: 'online', view: 'alphabet', category: 'artist' }).category).toBe('artist');
    expect(indexRoute({ kind: 'studios', q: '', scope: 'online', view: 'alphabet', category: 'role' }))
      .toMatchObject({ scope: 'local', category: 'all' });
  });
});

describe('名册', () => {
  it('订阅标记随名册状态显示，标记区域仍打开人物资料', async () => {
    seed(indexKey('performers', ''), [person('甲', { following: true }), person('乙', { entity_id: 2, following: false })]);
    const given = props();
    const host = await open(given);
    const marker = host.querySelector('[data-index-feed]');
    expect(marker?.getAttribute('aria-label')).toBe('已订阅新作');
    expect(host.querySelectorAll('[data-index-feed]')).toHaveLength(1);
    await click(marker);
    expect(given.openEntity).toHaveBeenCalledWith('performer', '甲');
    await act(async () => { seed(indexKey('performers', ''), [person('甲', { following: false })]) });
    await settle();
    expect(host.querySelector('[data-index-feed]')).toBeNull();
  });
  it('浏览分类写回地址并隔离分页缓存，筛选只显示有内容的分类', async () => {
    seed(indexKey('performers', ''), [person('动画作者', { entity_kind:'creator', identity_labels: ['动画作者'] })], false, { animation:1 });
    const fetcher = serve(() => ({ items: [person('动画作者', { entity_kind:'creator', identity_labels: ['动画作者'] })], categories:{ animation:1 }, has_more: false }));
    const given = props();
    const host = await open(given);
    expect(host.querySelector('[data-index-title]')?.textContent).toBe('艺人');
    expect(cells(host)[0]?.textContent).toContain('动画作者');
    expect(host.querySelector('[aria-label="身份分类"]')?.textContent).toBe('全部动画作者');
    await click(buttonNamed('动画作者', host));
    await settle();
    expect(given.route).toHaveBeenLastCalledWith(expect.objectContaining({ category: 'animation' }), { replace: false });
    expect(urls(fetcher)[0]).toContain('category=animation');
    expect(cells(host)[0]?.textContent).toContain('动画作者');
    expect(indexRoute({ kind: 'performers', q: '', scope: 'online', view: 'alphabet', category: 'artist' }).category).toBe('all');
  });
  it('艺人分类显示有内容的分类，卖家地址不接受女优筛选', async () => {
    seed(indexKey('performers', ''), [person('女优', { identity_labels:['女优','西方'] })], false, { japanese_av:1, western:1 });
    const host = await open(props());
    expect(host.querySelector('[aria-label="身份分类"]')?.textContent).toBe('全部女优西方');
    expect(indexRoute({ kind:'creators', q:'', scope:'local', view:'alphabet', category:'japanese_av' }).category).toBe('all');
  });
  it('换分类等新一页时，分类按钮行沿用上一份计数，不塌缩', async () => {
    seed(indexKey('performers', ''), [person('女优', { identity_labels:['女优'] })], false, { japanese_av:1, amateur:2, western:1 });
    const answer = pending<unknown>();
    serve(() => answer.answer);
    const host = await open(props());
    const row = () => host.querySelector('[aria-label="身份分类"]')?.textContent;
    expect(row()).toBe('全部女优素人西方');
    await click(buttonNamed('素人', host));
    await settle();
    expect(row()).toBe('全部女优素人西方');
    expect(buttonNamed('素人', host)?.getAttribute('aria-pressed')).toBe('true');
    await answer.release({ items: [person('素人')], categories: { japanese_av:1, amateur:2 }, has_more: false });
    await settle();
    expect(row()).toBe('全部女优素人');
  });
  it('旧的卖家动画作者链接规范成艺人分类后，用 replace 改写地址', async () => {
    seed(indexKey('performers', '', 'animation'), [person('动画作者')], false, { animation:1 });
    const given = props({ kind: 'creators', category: 'animation' });
    const host = await open(given);
    expect(host.querySelector('[data-index-title]')?.textContent).toBe('艺人');
    expect(given.route).toHaveBeenCalledTimes(1);
    expect(given.route).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'performers', scope: 'local', category: 'animation' }), { replace: true });
  });
  it('地址本来就规范时不改写', async () => {
    seed(indexKey('performers', ''), [person('女优')]);
    const given = props();
    await open(given);
    expect(given.route).not.toHaveBeenCalled();
  });
  it('艺人、卖家、在线共用一排导航，卖家没有重复身份筛选', async () => {
    seed(indexKey('performers',''),[person('真人账号')]);
    seed(indexKey('creators',''),[person('卖家账号')]);
    seed(onlineAuthorsKey(''),[]);
    serve(() => ({items:[],has_more:false}));
    const given = props();
    const host = await open(given);
    expect([...host.querySelectorAll('[role="tab"]')].map(item => item.textContent?.trim())).toEqual(['艺人','卖家','在线']);
    expect(host.querySelectorAll('[role="tablist"]')).toHaveLength(1);
    await click(tab(host,'creators'));
    await settle();
    expect(host.querySelector('[data-index-title]')?.textContent).toBe('卖家');
    expect(host.querySelector('[aria-label="身份分类"]')).toBeNull();
    await click(tab(host,'online'));
    await settle();
    expect(given.route).toHaveBeenLastCalledWith(expect.objectContaining({kind:'performers',scope:'online',category:'all'}),{replace:false});
    await click(tab(host,'performers'));
    await settle();
    expect(given.route).toHaveBeenLastCalledWith(expect.objectContaining({kind:'performers',scope:'local',category:'all'}),{replace:false});
    expect(indexRoute({kind:'creators',q:'',scope:'local',view:'alphabet',category:'animation'})).toMatchObject({kind:'performers',category:'animation'});
  });
  it('艺人名册的真人账号保留账号头像及资料页入口', async () => {
    seed(indexKey('performers',''),[person('真人账号',{entity_kind:'creator',identity_labels:['网黄博主']})],false,{blogger:1});
    const given = props();
    const host = await open(given);
    expect(given.personAvatar).toHaveBeenCalledWith(expect.objectContaining({k:'真人账号'}),'creator',true);
    await click(cells(host)[0]);
    expect(given.openEntity).toHaveBeenCalledWith('creator','真人账号');
    expect(host.querySelector('[aria-label="身份分类"]')?.textContent).toBe('全部网黄博主');
  });
  it('首屏读缓存，读数带加号，大图版式按大格取头像', async () => {
    seed(indexKey('performers', ''), [person('甲'), person('乙')], true);
    const given = props();
    const host = await open(given);
    expect(cells(host)).toHaveLength(2);
    expect(host.querySelector('[data-index-count]')?.textContent).toBe('2+ 项');
    expect(given.personAvatar).toHaveBeenCalledWith(expect.objectContaining({ k: '甲' }), 'performer', true);
    await click(cells(host)[0]);
    expect(given.openEntity).toHaveBeenCalledWith('performer', '甲');
  });

  it('格子底下的作品数带单位、按千分位写；页头读数同样分位', async () => {
    seed(indexKey('performers', ''), [person('甲', { n: 128400 }), person('乙', { n: 0 })]);
    const host = await open(props());
    expect(cells(host)[0]?.textContent).toContain('128,400 个视频');
    expect(cells(host)[1]?.textContent).toContain('0 个视频');
  });

  it('名册没读到时页头不写「0 项」，空态配出错的字形', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 500, json: async () => ({ error: '读不出来' }) })));
    const host = await open(props());
    await settle();
    expect(host.textContent).toContain('没能读取');
    expect(host.querySelector('[data-index-count]')?.textContent ?? '').toBe('');
    expect(host.textContent).not.toContain('0 项');
  });

  it('字母表里截断的标签名悬停看得到全名', async () => {
    const long = 'とても長い日本語のタグ名がここに続いて途中で切れてしまうもの';
    seed(indexKey('tags', '', 'all'), [tag(long)]);
    const host = await open(props({ kind: 'tags' }));
    expect(alphaTag(host, long)?.getAttribute('title')).toBe(long);
  });

  it('换版式写回偏好、按圆框取头像，并把已经加载完的图重量一遍', async () => {
    seed(indexKey('performers', ''), [person('甲')]);
    const given = props();
    const host = await open(given);
    vi.mocked(refitNativeImages).mockClear();
    await click(host.querySelector('[data-index-layout] input[value="compact"]'));
    expect(given.savePreference).toHaveBeenCalledWith({ layout: 'compact' });
    expect(given.personAvatar).toHaveBeenLastCalledWith(expect.objectContaining({ k: '甲' }), 'performer', false);
    expect(refitNativeImages).toHaveBeenCalledTimes(1);
  });

  it('「载入更多」取下一页：路上按灰并报忙，到手后没有下一页就收起', async () => {
    seed(indexKey('performers', ''), [person('甲')], true);
    const next = pending<unknown>();
    const fetcher = serve(() => next.answer);
    const host = await open(props());
    const more = host.querySelector<HTMLButtonElement>('[data-index-more]')!;
    await click(more);
    expect(more.disabled).toBe(true);
    expect(more.getAttribute('aria-busy')).toBe('true');
    expect(urls(fetcher)[0]).toContain('offset=1');
    await next.release({ items: [person('乙')], has_more: false });
    await settle();
    expect(cells(host)).toHaveLength(2);
    expect(host.querySelector('[data-index-more]')).toBeNull();
  });

  it('厂牌与事务所是两条地址；事务所的读数数的是人', async () => {
    seed(indexKey('studios', ''), [person('S1')]);
    const fetcher = serve(() => ({ items: [person('T-POWERS', { members: 13 })], has_more: false }));
    const given = props({ kind: 'studios' });
    const host = await open(given);
    await click(tab(host, 'agencies'));
    await settle();
    expect(given.route).toHaveBeenLastCalledWith(expect.objectContaining({ kind: 'agencies' }), { replace: false });
    expect(urls(fetcher)[0]).toContain('kind=agencies');
    expect(host.querySelector('[data-index-title]')?.textContent).toBe('事务所');
    expect(cells(host)[0]?.textContent).toContain('13 人');
  });

  it('艺人切到在线名册：先退出选择模式，没建档的点开去关注页', async () => {
    seed(indexKey('performers', ''), [person('甲')]);
    const alice = { k: 'Alice', key: 'x:alice', n: 4, entity_id: null, held_by: '', avatar: '', avatar_fallback: '', providers: [] };
    seed(onlineAuthorsKey(''), [alice]);
    // 换档时缓存里那一份已经过期，后台照样重问一次。
    const fetcher = serve(() => ({ items: [alice], has_more: false }));
    const given = props();
    const host = await open(given);
    await click(tab(host, 'online'));
    await settle();
    expect(given.exitSelectMode).toHaveBeenCalledTimes(1);
    expect(given.route).toHaveBeenLastCalledWith(expect.objectContaining({ scope: 'online' }), { replace: false });
    expect(urls(fetcher)[0]).toContain('/api/follow/authors?');
    const cell = host.querySelector('[data-follow-author="x:alice"]');
    expect(cell?.textContent).toContain('4 项更新');
    await click(cell);
    expect(given.openFollowAuthor).toHaveBeenCalledWith('x:alice');
    expect(given.openEntity).not.toHaveBeenCalled();
  });

  it('在线名册里建过档的去创作者资料页；同名待确认的读数带「待确认」，去数据库里那位的资料页', async () => {
    const bound = { k: 'Jul3D', key: 'entity:9', n: 3, entity_id: 9, held_by: '', avatar: '', avatar_fallback: '', providers: [] };
    const held = { k: 'InitialA', key: 'name:initiala', n: 8, entity_id: null, held_by: 'InitialA',
      avatar: '', avatar_fallback: '', providers: [] };
    seed(indexKey('performers', ''), [person('甲')]);
    seed(onlineAuthorsKey(''), [bound, held]);
    serve(() => ({ items: [bound, held], has_more: false }));
    const given = props();
    const host = await open(given);
    await click(tab(host, 'online'));
    await settle();
    await click(host.querySelector('[data-follow-author="entity:9"]'));
    expect(given.openEntity).toHaveBeenLastCalledWith('creator', 'Jul3D');
    const pending = host.querySelector('[data-follow-author="name:initiala"]');
    expect(pending?.textContent).toContain('8 项更新 · 待确认');
    await click(pending);
    expect(given.openEntity).toHaveBeenLastCalledWith('creator', 'InitialA');
    expect(given.openFollowAuthor).not.toHaveBeenCalled();
  });
});

describe('过滤框', () => {
  it('停手 300ms 才查，一路改写地址；新结果到手前留着上一份，输入框不被换掉', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    seed(indexKey('performers', ''), [person('甲'), person('乙')]);
    const answer = pending<unknown>();
    const fetcher = serve(() => answer.answer);
    const given = props();
    const host = await open(given);
    const input = search(host);
    await type(input, '甲');
    expect(given.route).not.toHaveBeenCalled();
    await act(async () => { vi.advanceTimersByTime(300) });
    expect(given.route).toHaveBeenCalledWith(expect.objectContaining({ q: '甲' }), { replace: true });
    expect(urls(fetcher)[0]).toContain('q=%E7%94%B2');
    // 这一问还在路上：上一份名单照旧摆着，没有骨架盖上来，也没有「载入更多」。
    expect(cells(host)).toHaveLength(2);
    expect(host.querySelector('[data-index-body] [aria-busy="true"]')).toBeNull();
    await answer.release({ items: [person('甲')], has_more: false });
    await settle();
    expect(cells(host)).toHaveLength(1);
    expect(search(host)).toBe(input);
  });

  it('回车当场就查；输入法组字中的输入与回车都放过去', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    seed(indexKey('performers', ''), [person('甲')]);
    serve(() => ({ items: [], has_more: false }));
    const given = props();
    const host = await open(given);
    const input = search(host);
    await act(async () => { input.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true })) });
    await type(input, 'zhon');
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, isComposing: true }));
      vi.advanceTimersByTime(300);
    });
    expect(given.route).not.toHaveBeenCalled();
    await type(input, '中');
    await act(async () => { input.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true })) });
    await act(async () => { vi.advanceTimersByTime(300) });
    expect(given.route).toHaveBeenLastCalledWith(expect.objectContaining({ q: '中' }), { replace: true });
    await type(input, '中文');
    await act(async () => { input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })) });
    expect(given.route).toHaveBeenLastCalledWith(expect.objectContaining({ q: '中文' }), { replace: true });
  });
});

describe('标签', () => {
  it('浮层只摆数得到的类型；点一类写回地址', async () => {
    seed(indexKey('tags', '', 'all'), [tag('痴女'), tag('秘书OL', 'scene')], false, { role: 1, scene: 1 });
    serve(() => ({ items: [tag('痴女')], has_more: false, categories: { role: 1 } }));
    const given = props({ kind: 'tags' });
    const host = await open(given);
    const pills = [...host.querySelectorAll('[data-tag-pill]')].map((pill) => pill.textContent);
    expect(pills).toEqual(['全部', '角色设定', '情境场所']);
    await click([...host.querySelectorAll('[data-tag-pill]')][1]);
    expect(given.route).toHaveBeenLastCalledWith(expect.objectContaining({ category: 'role' }), { replace: false });
  });

  it('不在选择模式时点一枚就回目录，只按这一枚筛', async () => {
    seed(indexKey('tags', '', 'all'), [tag('痴女')]);
    const given = props({ kind: 'tags' });
    const host = await open(given);
    await click(alphaTag(host, '痴女'));
    expect(given.showTags).toHaveBeenCalledWith(['痴女'], 'all');
  });

  it('选择模式下点选、广泛匹配与「显示结果」都只动这一屏，壳关掉选择键时所选跟着清空', async () => {
    seed(indexKey('tags', '', 'all'), [tag('痴女'), tag('秘书OL')]);
    const given = props({ kind: 'tags' }, { selectMode: true });
    const host = await open(given);
    expect(host.querySelector('[aria-label="所选标签操作"]')).toBeNull();
    await click(alphaTag(host, '痴女'));
    await click(alphaTag(host, '秘书OL'));
    expect(alphaTag(host, '痴女')?.getAttribute('aria-pressed')).toBe('true');
    expect(host.textContent).toContain('已选 2 个标签');
    // 广泛匹配缺省开着；关掉就是「必须同时包含」。
    await click(host.querySelector('[aria-label="所选标签操作"] input[type="checkbox"]'));
    await click(buttonNamed('显示结果', host));
    expect(given.showTags).toHaveBeenCalledWith(['痴女', '秘书OL'], 'all');
    expect(given.route).not.toHaveBeenCalled();

    await click(alphaTag(host, '痴女'));
    expect(host.textContent).toContain('已选 1 个标签');
    await act(async () => setSelectMode(false));
    await act(async () => setSelectMode(true));
    expect(alphaTag(host, '痴女')?.getAttribute('aria-pressed')).toBe('false');
    expect(host.textContent).not.toContain('已选');
  });

  it('切到在线词表直接给字母表，点开去关注页', async () => {
    seed(indexKey('tags', '', 'all'), [tag('痴女')]);
    seed(onlineTagsKey('', 'all'), [tag('long_hair', 'general')]);
    const fetcher = serve(() => ({ items: [tag('long_hair', 'general')], has_more: false }));
    const given = props({ kind: 'tags', view: 'cloud' }, { selectMode: true });
    const host = await open(given);
    expect(host.querySelector('[data-tag-cloud]')).not.toBeNull();
    await click(tab(host, 'online'));
    await settle();
    expect(given.route).toHaveBeenLastCalledWith(
      expect.objectContaining({ scope: 'online', view: 'alphabet', category: 'all' }), { replace: false });
    expect(urls(fetcher)[0]).toContain('/api/follow/tags?types=all');
    await click(alphaTag(host, 'long_hair'));
    expect(given.openFollowTag).toHaveBeenCalledWith('long_hair');
    expect(given.showTags).not.toHaveBeenCalled();
    // 多选是本地目录语义：在线那一套不给操作条。
    expect(host.querySelector('[aria-label="所选标签操作"]')).toBeNull();
  });
});
