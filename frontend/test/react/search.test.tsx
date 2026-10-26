/* 顶栏搜索（`search` 岛）：下拉栏里摆什么（`menuModel`／`shufflePicks`），以及几条只在时序上才看得见
 * 的行为——占位推荐词、空输入回车、作品项开详情、慢回来的旧补全、失焦之后才回来的补全。键盘上下、
 * 组字、Escape 与删记录在真浏览器里验，见 `e2e/search.test.ts`。 */
import { act } from 'react';
import { describe, expect, it, vi, type Mock } from 'vitest';

import { SearchPage } from '../../src/react/search/search-page';
import {
  PICKS, SUGGEST_DEBOUNCE, menuModel, shufflePicks, type SearchApi, type SearchProps, type SuggestResponse, type SuggestGroup,
} from '../../src/react/search/search';

import { mount, pending, settle } from './render';

const performer = { kind: 'performer', label: '女优', total: 1, items: [{ value: '七海ひな', n: 12 }] };
const tag = { kind: 'tag', label: '标签', total: 1, items: [{ value: '七海の夏', n: 3 }] };
const asset = { kind: 'asset', label: '视频', total: 1, items: [{ value: 'ABW-101', id: 7, code: 'ABW-101', who: '七海ひな' }] };

describe('下拉栏里摆什么', () => {
  const base = { history: [], picks: [], suggestFor: '', groups: [], tabs: [], kind: '' };

  it('有输入时搜索记录跟着这个词筛，全半角与大小写折叠后再比', () => {
    const model = menuModel({ ...base, query: 'studio', history: ['ＳＴＵＤＩＯ Ａ', '七海ひな'] });
    expect(model.recent?.options.map((o) => o.value)).toEqual(['ＳＴＵＤＩＯ Ａ']);
  });

  it('补全只认这个词自己的那一份；选了一类时搜索记录让位', () => {
    const groups = [performer, tag];
    const tabs = groups.map(({ kind, label, total }) => ({ kind, label, total }));
    const stale = menuModel({ ...base, query: '七海', history: ['七海'], suggestFor: '七', groups, tabs });
    expect([stale.left, stale.right, stale.tabs]).toEqual([[], [], null]);

    const fresh = menuModel({ ...base, query: '七海', history: ['七海'], suggestFor: '七海', groups, tabs });
    expect(fresh.tabs?.map((t) => t.kind)).toEqual(['performer', 'tag']);
    expect(fresh.options.map((o) => o.value)).toEqual(['七海', '七海ひな', '七海の夏']);

    const one = menuModel({ ...base, query: '七海', history: ['七海'], suggestFor: '七海', groups, tabs, kind: 'tag' });
    expect(one.recent).toBeNull();
    expect(one.options.map((o) => o.value)).toEqual(['七海の夏']);
  });

  it('这个词的补全回来了却一条没有才算「没有找到」，还在路上的不算', () => {
    expect(menuModel({ ...base, query: 'zzz', suggestFor: 'zzz' }).empty).toBe(true);
    expect(menuModel({ ...base, query: 'zzz', suggestFor: 'zz' }).empty).toBe(false);
    expect(menuModel({ ...base, query: '七海', suggestFor: '七海', groups: [performer] }).empty).toBe(false);
    expect(menuModel({ ...base, query: '' }).empty).toBe(false);
  });

  it('作品一类单独排在右栏；只命中一类时没有页签', () => {
    const model = menuModel({ ...base, query: 'ABW', suggestFor: 'ABW', groups: [asset], tabs: [asset] });
    expect([model.left.length, model.right[0]?.kind, model.tabs]).toEqual([0, 'asset', null]);
  });

  it('视频总数超过预览时，完整结果入口排在作品之后并参与键盘选择', () => {
    const group = { ...asset, total: 53 };
    const model = menuModel({ ...base, query: 'ABW', suggestFor: 'ABW', groups: [group], tabs: [group] });
    expect(model.right[0]?.more).toEqual({ index: 1, type: 'more', value: 'ABW', total: 53 });
    expect(model.options.map((o) => o.type)).toEqual(['suggest', 'more']);
  });

  it('推荐不再推搜过的词，一次最多几条', () => {
    const pool = ['剧情', '单体作品', '高画质', '中出', '巨乳', '美少女', '独占'];
    const picks = shufflePicks(pool, ['高画质'], () => 0.5);
    expect(picks).toHaveLength(PICKS);
    expect(picks).not.toContain('高画质');
  });
});

interface Stage {
  input: HTMLInputElement;
  menu: HTMLElement;
  actions: { [K in keyof SearchProps['actions']]: Mock<NonNullable<SearchProps['actions'][K]>> };
  posts: unknown[];
  api: SearchApi | null;
  key(key: string): Promise<void>;
  typeInto(value: string): Promise<void>;
}

/** 壳那一侧：一个静态输入框、一块带 `data-search-menu` 的面，岛挂在面里。 */
async function stage({ pool = ['剧情'], loadPool = async () => pool,
  recommendations = undefined as (() => Promise<SuggestGroup[]>) | undefined,
  suggest = async (q: string): Promise<SuggestResponse> => ({ q, groups: [] }) } = {}): Promise<Stage> {
  const posts: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url, 'http://peach.test');
    let body: unknown = { ok: true };
    if (path.pathname === '/api/search-history' && init?.method === 'POST') posts.push(JSON.parse(String(init.body)));
    else if (path.pathname === '/api/search-history') body = { items: [] };
    else if (path.pathname === '/api/suggest') body = await suggest(path.searchParams.get('q') || '');
    return { ok: true, status: 200, json: async () => body };
  }));
  const input = document.createElement('input');
  const menu = document.createElement('div');
  menu.dataset.searchMenu = '';
  menu.hidden = true;
  document.body.append(input, menu);
  const actions: Stage['actions'] = {
    search: vi.fn<SearchProps['actions']['search']>(),
    openItem: vi.fn<SearchProps['actions']['openItem']>(),
    openEntity: vi.fn<SearchProps['actions']['openEntity']>(),
    openPath: vi.fn<NonNullable<SearchProps['actions']['openPath']>>(),
    openFollow: vi.fn<NonNullable<SearchProps['actions']['openFollow']>>(),
  };
  const out: Stage = {
    input, menu, actions, posts, api: null,
    key: (key) => act(async () => {
      input.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    }),
    typeInto: (value) => act(async () => {
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }),
  };
  const helpers: SearchProps['helpers'] = {
    pool: loadPool, recommendations, coverHtml: card => `<span class="pic"><img src="/cover?code=${card?.code || ''}" alt=""></span>`,
    present: (el) => { el.hidden = false }, dismiss: (el) => { el.hidden = true },
    wireScroller: () => {}, typed: () => {}, composing: () => {}, clearField: (el) => { el.value = '' },
  };
  const host = await mount(<SearchPage input={input} historyLimit={10} actions={actions} helpers={helpers}
    expose={(api) => { out.api = api }} />);
  menu.append(host);
  return out;
}

const debounce = () => act(async () => { vi.advanceTimersByTime(SUGGEST_DEBOUNCE) });

describe('输入框上的时序', () => {
  it('空搜索推荐显示封面，键盘选择新作打开作品详情', async () => {
    const s = await stage({ recommendations: async () => [{ kind: 'release', label: '订阅新作',
      items: [{ value: 'ABW-101', href: '/works/ABW-101', card: { id: 88, code: 'ABW-101' } }],
      href: '/subscriptions?tab=feeds', action: '管理订阅',
    }] });
    await act(async () => s.input.focus());
    await settle();
    expect(s.menu.querySelector('[data-search-option="work"] img')).not.toBeNull();
    expect(s.menu.querySelector('h3 a')?.getAttribute('href')).toBe('/subscriptions?tab=feeds');
    await s.key('ArrowDown');
    await s.key('Enter');
    expect(s.actions.openPath).toHaveBeenCalledWith('/works/ABW-101');
    expect(s.actions.openItem).not.toHaveBeenCalled();
    expect(s.actions.search).not.toHaveBeenCalled();
  });

  it('关闭菜单后到达的图片推荐不会再打开菜单', async () => {
    const answer = pending<SuggestGroup[]>();
    const s = await stage({ recommendations: () => answer.answer });
    await act(async () => s.input.focus());
    await settle();
    await act(async () => s.api?.close());
    await answer.release([{ kind: 'asset', label: '作品推荐', items: [{ value: '作品', id: 11 }] }]);
    await settle();
    expect(s.menu.hidden).toBe(true);
  });
  it('选择关注图片以指定附件进入图片详情', async () => {
    const s = await stage({ recommendations: async () => [{ kind: 'photo', label: '关注图片',
      items: [{ value: '图片作品', id: 88, href: '/posts/88', followMedia: 'images', mediaIndex: 2,
        card: { id: 88, thumb_url: '/image-thumb' } }],
    }] });
    await act(async () => s.input.focus());
    await settle();
    await s.key('ArrowDown');
    await s.key('Enter');
    expect(s.actions.openFollow).toHaveBeenCalledWith(88, 'images', 2);
    expect(s.actions.openPath).not.toHaveBeenCalled();
  });

  it('单类视频预览请求二十条，完整结果入口用当前搜索词', async () => {
    vi.useFakeTimers();
    const s = await stage({ suggest: async (q) => ({ q, groups: [{ ...asset, total: 53 }] }) });
    await act(async () => s.input.focus());
    await settle();
    await s.typeInto('ABW');
    await debounce();
    await settle();
    const requests = vi.mocked(fetch).mock.calls.map(([url]) => String(url));
    expect(requests.some((url) => url.includes('/api/suggest?q=ABW&limit=20'))).toBe(true);
    await act(async () => s.menu.querySelector<HTMLButtonElement>('[data-search-option="more"]')!.click());
    expect(s.actions.search).toHaveBeenCalledWith('ABW');
    expect(s.actions.openItem).not.toHaveBeenCalled();
    expect(s.menu.hidden).toBe(true);
  });
  it('聚焦时占位换成一个真搜得出东西的词；空输入回车就搜它，并记进搜索记录', async () => {
    const s = await stage({ pool: ['温泉旅行'] });
    await act(async () => s.input.focus());
    await settle();
    expect([s.input.placeholder, s.input.dataset.suggestion]).toEqual(['温泉旅行', '温泉旅行']);
    expect(s.menu.hidden).toBe(false);
    await s.key('Enter');
    await settle();
    expect(s.actions.search).toHaveBeenCalledWith('温泉旅行');
    expect(s.input.value).toBe('温泉旅行');
    expect(s.posts).toEqual([{ query: '温泉旅行' }]);
    expect(s.menu.hidden).toBe(true);
  });

  it('选中一部作品回车是打开它，不拿整句标题去搜', async () => {
    vi.useFakeTimers();
    const s = await stage({ suggest: async (q) => ({ q, groups: [asset] }) });
    await act(async () => s.input.focus());
    await settle();
    await s.typeInto('ABW');
    await debounce();
    await settle();
    await s.key('ArrowDown');
    await s.key('Enter');
    expect(s.actions.openItem).toHaveBeenCalledWith(7);
    expect([s.actions.search, s.actions.openEntity].map((fn) => fn.mock.calls.length)).toEqual([0, 0]);
    expect(s.input.value).toBe('ABW');
  });

  it('连敲两个字时先发的那次后回来，不盖掉后一次', async () => {
    vi.useFakeTimers();
    const answers = new Map<string, ReturnType<typeof pending<SuggestResponse>>>();
    const s = await stage({ suggest: (q) => { const p = pending<SuggestResponse>(); answers.set(q, p); return p.answer } });
    await act(async () => s.input.focus());
    await settle();
    await s.typeInto('七');
    await debounce();
    await s.typeInto('七海');
    await debounce();
    await answers.get('七海')!.release({ q: '七海', groups: [performer, tag] });
    await settle();
    await answers.get('七')!.release({ q: '七', groups: [tag] });
    await settle();
    const values = [...s.menu.querySelectorAll('[data-search-option]')].map((row) => row.getAttribute('data-search-value'));
    expect(values).toEqual(['七海ひな', '七海の夏']);
  });

  it('聚焦时的推荐池晚于敲字回来，不再按同一个词补问一次', async () => {
    vi.useFakeTimers();
    const asked: string[] = [];
    const slowPool = pending<string[]>();
    const s = await stage({ loadPool: () => slowPool.answer,
      suggest: async (q) => { asked.push(q); return { q, groups: [performer] } } });
    await act(async () => s.input.focus());
    await s.typeInto('七海');
    await debounce();
    await settle();
    expect(asked).toEqual(['七海']);
    await slowPool.release(['剧情']);
    await debounce();
    await settle();
    expect(asked).toEqual(['七海']);
    expect(s.menu.hidden).toBe(false);
  });

  it('组字期间的输入不问补全，组完才问：拿半截拼音去查，查的是一个不存在的词', async () => {
    vi.useFakeTimers();
    const asked: string[] = [];
    const s = await stage({ suggest: async (q) => { asked.push(q); return { q, groups: [] } } });
    await act(async () => s.input.focus());
    await settle();
    await act(async () => {
      s.input.value = 'zhon';
      s.input.dispatchEvent(new InputEvent('input', { bubbles: true, isComposing: true }));
    });
    await debounce();
    await settle();
    expect(asked).toEqual([]);
    await act(async () => {
      s.input.value = '中';
      s.input.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: '中' }));
    });
    await debounce();
    await settle();
    expect(asked).toEqual(['中']);
  });

  it('补全回来时焦点已经走了，下拉栏不再掀开', async () => {
    vi.useFakeTimers();
    const answer = pending<SuggestResponse>();
    const s = await stage({ suggest: () => answer.answer });
    await act(async () => s.input.focus());
    await settle();
    await s.typeInto('七海');
    await debounce();
    await act(async () => { s.input.blur(); s.api?.close() });
    await answer.release({ q: '七海', groups: [performer] });
    await settle();
    expect(s.menu.hidden).toBe(true);
  });
});
