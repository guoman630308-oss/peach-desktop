/* 资料卡岛：名字下面那一行、资料表、外链怎么排；点下去交给壳的是什么；订阅开关成与不成时
 * 留下什么；新作那一行的两枚键；「+N」浮层的开合。
 *
 * 外观（圆框几何、人脸放大、窄屏单列、浮层位置）由 `frontend/e2e/design-*.test.ts` 读计算样式断言。 */
import { act } from 'react';
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EntityHeroPage } from '../../src/react/entity-hero/entity-hero-page';
import {
  factRows, heroLinks, nameChoices, nameLine, shownTags,
  type EntityHeroActions, type EntityHeroData, type EntityHeroHelpers, type EntityHeroProps,
} from '../../src/react/entity-hero/entity-hero';
import { queryClient } from '../../src/react/query';
import { click, mount, settle } from './render';

afterEach(() => { queryClient.clear() });
notifyManager.setScheduler((notify) => notify());

const performer = (extra: Partial<EntityHeroData> = {}): EntityHeroData => ({
  id: 7792, canonical_name: '葵つかさ', aliases: ['葵司'], asset_count: 1234,
  agency: { canonical_name: 'New Actor eXperience' },
  name_groups: { reading: 'あおいつかさ', shown: ['葵司'], total: 1, groups: [] },
  related_performers: [{ id: 1, k: '三上悠亜' }, { id: 2, k: '河北彩花' }],
  feed: { following: false },
  ...extra,
});

function actions(patch: Partial<EntityHeroActions> = {}): EntityHeroActions {
  return {
    openEntity: vi.fn(), chooseName: vi.fn(), addAlias: vi.fn(), follow: vi.fn(async () => {}),
    refreshFeedAfterCheck: vi.fn(), feedAction: vi.fn(async () => {}), avatarPicked: vi.fn(),
    openFollowAuthor: vi.fn(), confirmFollowAuthor: vi.fn(async () => {}), ...patch,
  };
}

function helpers(patch: Partial<EntityHeroHelpers> = {}): EntityHeroHelpers {
  return {
    portraitImg: () => '', wireScroller: vi.fn(), wireFeedRow: vi.fn(), receipt: vi.fn(), ...patch,
  };
}

function props(patch: Partial<EntityHeroProps> = {}): EntityHeroProps {
  return {
    kind: 'performer', name: '葵つかさ', entity: performer(), feedNew: null, feedHost: null, jav: true,
    actions: actions(), helpers: helpers(), ...patch,
  };
}

async function open(initial: EntityHeroProps) {
  const host = await mount(<QueryClientProvider client={queryClient}><EntityHeroPage {...initial} /></QueryClientProvider>);
  await settle();
  return host;
}

describe('排版用的纯函数', () => {
  it('厂牌与事务所显示独立的公司资料，不显示来源悬浮说明', async () => {
    const host = await open(props({ kind: 'studio', name: 'Brand', entity: {
      id: 1, canonical_name: 'Brand', asset_count: 3,
      company_profile: {
        founded: { value: '2002-05', source_url: 'https://brand.example/company' },
        launched: { value: '2003', source_url: 'https://brand.example/company' },
        operator: { value: 'Brand株式会社', source_url: 'https://brand.example/company' },
      },
    } }));
    const facts = host.querySelector('[data-entity-facts]')!;
    expect(facts.textContent).toContain('公司成立2002-05');
    expect(facts.textContent).toContain('品牌启动2003');
    expect(facts.textContent).toContain('运营公司Brand株式会社');
    expect(facts.querySelector('[title]')).toBeNull();
  });
  it('真人账号与女优共用资料、别名和图标行', async () => {
    const host = await open(props({ kind: 'creator', name: 'RiaKurumi', entity: performer({
      canonical_name: 'RiaKurumi', identity_labels: ['女优','网黄博主'],
      profile: { height: 149 }, name_groups: { shown: ['Ria Kurumi', '百田くるみ'], total: 2 },
    }) }));
    expect(host.querySelector('[data-entity-facts]')?.textContent).toContain('T149');
    expect(host.querySelector('[data-alias-names]')?.textContent).toContain('百田くるみ');
    expect(host.querySelector('[data-meta-item][title="视频"] use')?.getAttribute('href')).toBe('#i-film');
  });
  it('厂牌视频数与别名各自带图标，资料不填人物字段', async () => {
    const host = await open(props({ kind: 'studio', name: 'MOODYZ', entity: performer({
      canonical_name: 'MOODYZ', display_aliases: ['ムーディーズ'], agency: undefined,
    }) }));
    expect(host.querySelector('[data-meta-item][title="视频"] use')?.getAttribute('href')).toBe('#i-film');
    expect(host.querySelector('[data-company-names] use')?.getAttribute('href')).toBe('#i-id-card');
    expect(host.querySelector('[data-company-names]')?.textContent).toBe('ムーディーズ');
    expect(host.querySelector('[data-entity-facts]')).toBeNull();
  });
  it('分类带图标位于身份行最左侧，资料保留跨角色身份入口', async () => {
    const given = props({ entity: performer({
      identity_labels: ['女优','西方'],
      related_identities: [{ id: 2, kind: 'creator', canonical_name: '本人账号', relation: 'operates_account' }],
    }) });
    const host = await open(given);
    const classification = host.querySelector('[data-identity-classification]')!;
    expect(classification.textContent).toBe('女优 · 西方');
    expect(classification.querySelector('use')?.getAttribute('href')).toBe('#i-user-round');
    expect(classification.hasAttribute('title')).toBe(false);
    expect(host.querySelector('[data-entity-alias]')?.firstElementChild).toBe(classification);
    await click(host.querySelector('[data-entity-identity] button.underline'));
    expect(given.actions.openEntity).toHaveBeenCalledWith('creator', '本人账号');
  });
  it('名字下面那一行：读音在最前，余下的收进「+N」；什么都没有就不出这一项', () => {
    expect(nameLine({ reading: 'あおい', shown: ['葵司', 'Tsukasa Aoi'], total: 5 }))
      .toEqual({ names: ['あおい', '葵司', 'Tsukasa Aoi'], rest: 3, total: 5 });
    expect(nameLine({ shown: [], total: 0 })).toBeNull();
    expect(nameLine(null)).toBeNull();
  });

  it('标签只列前四个；只多出一个时直接列出来', () => {
    expect(shownTags(['a', 'b', 'c', 'd', 'e'])).toEqual({ shown: ['a', 'b', 'c', 'd', 'e'], rest: 0 });
    expect(shownTags(['a', 'b', 'c', 'd', 'e', 'f'])).toEqual({ shown: ['a', 'b', 'c', 'd'], rest: 2 });
  });

  it('资料表有哪项画哪项，仍在活跃的写「至今」，出道片名单独截断', () => {
    const rows = factRows({
      birth_date: '1990-08-14', age: 36, height: 163, bust: 88, cup: 'E',
      debut_date: '2012-03-01', debut_title: '很长的出道片名', active: { from: '2012', ongoing: true }, tags: ['巨乳'],
    });
    expect(rows.map((row) => row.label)).toEqual(['生日', '身材', '出道', '生涯', '标签']);
    // 一枚字形只代表一个意思：标签那一项与标签页同一枚。
    expect(rows.map((row) => row.glyph)).toEqual(['cake', 'ruler', 'flag', 'calendar-range', 'tags']);
    expect(rows[1]?.parts.map((part) => part.text)).toEqual(['T163 · B88', '· E 罩杯']);
    expect(rows[2]?.clip).toBe('很长的出道片名');
    expect(rows[3]?.parts[0]?.text).toBe('2012 – 至今');
    expect(factRows({ cup: 'C' })[0]?.parts).toEqual([{ text: 'C 罩杯', tone: 'sub' }]);
    expect(factRows(null)).toEqual([]);
  });

  it('外链：外部入口排最前；女优页连官网也是图标，链回她所属事务所的写「官方资料」；失效与私人来源不可点', () => {
    const links = heroLinks(performer({
      entry_links: [
        { site: 'minnano', label: 'minnano-av', slot: 'pill', mark: 'minnano', url: 'https://www.minnano-av.com/x' },
        { site: 'javdb', label: 'JavDB', slot: 'mark', mark: 'javdb', url: 'https://javdb.com/actors/x' },
      ],
      links: [
        { link_id: 3, link_kind: 'official', clickable: true, label: 'New Actor eXperience', url: 'https://www.nax.co.jp/talent/aoi' },
        { link_id: 4, link_kind: 'social', clickable: true, label: 'X', url: 'https://x.com/aoi' },
        { link_id: 5, gone: true, retired_year: 2024, label: '旧博客', url: 'https://old.example' },
        { link_id: 6, clickable: false, label: '私人记录' },
      ],
    }), 'performer');
    expect(links).toEqual([
      { type: 'icon', url: 'https://www.minnano-av.com/x', title: 'minnano-av', mark: { brand: 'minnano' } },
      { type: 'icon', url: 'https://www.nax.co.jp/talent/aoi', title: 'New Actor eXperience 官方资料', mark: { site: '/link-mark?id=3' } },
      { type: 'icon', url: 'https://x.com/aoi', title: 'X', mark: { brand: 'brand-x' } },
      { type: 'gone', title: '旧博客 · 已于 2024 年隐退' },
      { type: 'private', label: '私人记录' },
    ]);
  });

  it('生日写了而年龄没下发时只写生日，不拼出「undefined 岁」', () => {
    const [birth] = factRows({ birth_date: '1990-08-14' });
    expect(birth?.parts.map((part) => part.text)).toEqual(['1990-08-14']);
  });

  it('两处以上不公开来源合并成一枚排在末尾，各处名字留给 title；只有一处照旧', () => {
    const links = heroLinks(performer({ links: [
      { link_id: 1, clickable: false, label: '私人记录' },
      { link_id: 2, link_kind: 'social', clickable: true, label: 'X', url: 'https://x.com/aoi' },
      { link_id: 3, clickable: false, label: '论坛帖' },
      { link_id: 4, clickable: false, label: '群聊截图' },
    ] }), 'performer');
    expect(links.map((link) => link.type)).toEqual(['icon', 'private']);
    expect(links[1]).toEqual({ type: 'private', label: '3 个不公开来源', sources: ['私人记录', '论坛帖', '群聊截图'] });
    expect(heroLinks(performer({ links: [{ link_id: 1, clickable: false, label: '私人记录' }] }), 'performer'))
      .toEqual([{ type: 'private', label: '私人记录' }]);
  });

  it('公司页的官网保留名字，指回自家的写「官方网站」', () => {
    const [site] = heroLinks({
      id: 1, canonical_name: 'S1 NO.1 STYLE', asset_count: 1,
      links: [{ link_id: 9, link_kind: 'official', clickable: true, label: 'S1', url: 'https://www.s1s1s1.com/' }],
    }, 'studio');
    expect(site).toEqual({ type: 'url', url: 'https://www.s1s1s1.com/', title: 'S1', text: '官方网站', mark: { site: '/link-mark?id=9' } });
  });

  it('换统称的候选取完整的 aliases（罗马字也是她用过的写法），统称排第一，去重去空', () => {
    expect(nameChoices({ id: 1, canonical_name: '葵つかさ', asset_count: 0, aliases: ['葵司', 'Tsukasa Aoi', '葵司', ''] }))
      .toEqual(['葵つかさ', '葵司', 'Tsukasa Aoi']);
  });
});

describe('画出什么', () => {
  it('公司页名字行写展示别名，不写身份契约里的全部别名；事务所页数的是人，不画同台艺人，也不加类别名', async () => {
    const studio = await open(props({ kind: 'studio', name: 'S1', entity: {
      id: 3, canonical_name: 'S1', asset_count: 9, aliases: ['検索用の旧表記'], display_aliases: ['エスワン'],
    } }));
    const line = studio.querySelector('[data-entity-alias]')?.textContent ?? '';
    expect(line).toContain('エスワン');
    expect(line).not.toContain('検索用の旧表記');
    const agency = await open(props({ kind: 'agency', name: 'NAX', entity: {
      id: 4, canonical_name: 'NAX', asset_count: 30, member_count: 12, related_performers: [{ id: 1, k: '三上悠亜' }],
    } }));
    expect(agency.querySelector('[data-entity-alias]')?.textContent).toContain('12 位艺人');
    expect(agency.querySelector('[data-entity-foot]')).toBeNull();
    const performerLine = (await open(props())).querySelector('[data-entity-alias]')?.textContent ?? '';
    expect(performerLine).not.toContain('事务所');
  });

  it('公司别名先列三个，余下的收进「+N」，浮层里一个名字一行', async () => {
    const names = Array.from({ length: 30 }, (_, at) => `旧レーベル${at + 1}`);
    const host = await open(props({ kind: 'studio', name: 'S1', entity: {
      id: 3, canonical_name: 'S1', asset_count: 9, display_aliases: names,
    } }));
    const inline = host.querySelectorAll('[data-company-names] [data-alias-names] > span');
    expect([...inline].map((one) => one.textContent)).toEqual(['旧レーベル1', '旧レーベル2', '旧レーベル3']);
    expect(host.querySelector('[data-hero-more="alias"]')?.textContent).toBe('+27');
    const pop = document.getElementById('entityAliasPop')!;
    expect(pop.querySelectorAll('[data-pop-name]')).toHaveLength(30);
  });

  it('合并后的不公开来源写个数，各处名字一行一个放进 title', async () => {
    const host = await open(props({ entity: performer({ links: [
      { link_id: 1, clickable: false, label: '私人记录' },
      { link_id: 3, clickable: false, label: '论坛帖' },
    ] }) }));
    const chip = host.querySelector('[data-link="private"]');
    expect(chip?.textContent).toContain('2 个不公开来源');
    expect(chip?.getAttribute('title')).toBe('私人记录\n论坛帖');
  });

  it('纯图标外链的名字在 title 与读屏名称里；站点圆标由本机给、不带来源页地址，取不到时交兜底链撤掉；官网写字', async () => {
    const host = await open(props({ entity: performer({ links: [
      { link_id: 3, link_kind: 'official', clickable: true, label: 'New Actor eXperience', url: 'https://www.nax.co.jp/talent/aoi' },
    ] }) }));
    const icon = host.querySelector('a[data-link="icon"]')!;
    expect(icon.getAttribute('aria-label')).toBe(icon.getAttribute('title'));
    expect(icon.querySelector('[data-link-label]')).toBeNull();
    const mark = icon.querySelector('[data-link-icon] img')!;
    expect([mark.getAttribute('src'), mark.getAttribute('referrerpolicy'), mark.getAttribute('data-drop')])
      .toEqual(['/link-mark?id=3', 'no-referrer', 'self']);
    const studio = await open(props({ kind: 'studio', name: 'S1', entity: {
      id: 3, canonical_name: 'S1 NO.1 STYLE', asset_count: 9,
      links: [{ link_id: 9, link_kind: 'official', clickable: true, label: 'S1', url: 'https://www.s1s1s1.com/' }],
    } }));
    expect(studio.querySelector('a[data-link="url"] [data-link-label]')?.textContent).toBe('官方网站');
  });

  it('看片那一行照服务端下发的地址与序号排；MISSAV 没有图形标识，排字', async () => {
    const host = await open(props({ entity: performer({ entry_links: [
      { site: 'javdb', label: 'JavDB', slot: 'mark', mark: 'mark-javdb', url: 'https://javdb.com/actors/NPD3' },
      { site: 'missav', label: 'MISSAV', slot: 'mark', mark: '', ordinal: '2', url: 'https://missav.ws/actresses/x' },
    ] }) }));
    const marks = [...host.querySelectorAll('a[data-entry-mark]')];
    expect(marks.map((a) => a.getAttribute('href'))).toEqual(['https://javdb.com/actors/NPD3', 'https://missav.ws/actresses/x']);
    expect(marks[0]?.querySelector('use')?.getAttribute('href')).toBe('#i-mark-javdb');
    expect(marks[1]?.querySelector('[data-missav-mark]')?.textContent).toBe('MISSAV');
    expect(marks[1]?.querySelector('[data-entry-ordinal]')?.textContent).toBe('2');
  });

  it('只剩一个名字的人也有名字菜单：一行统称，末尾那项是添别名', async () => {
    const host = await open(props({ entity: performer({ aliases: [] }) }));
    await click(host.querySelector('[data-namepick-toggle]'));
    expect([...document.querySelectorAll('[data-namepick-name]')].map((item) => item.getAttribute('data-namepick-name')))
      .toEqual(['葵つかさ']);
    expect(document.querySelector('[data-namepick-alias]')?.getAttribute('role')).toBe('menuitem');
  });

  it('外链与同台艺人那两行交给壳接拖动与滚轮', async () => {
    const help = helpers();
    const host = await open(props({ helpers: help, entity: performer({ links: [
      { link_id: 4, link_kind: 'social', clickable: true, label: 'X', url: 'https://x.com/aoi' },
    ] }) }));
    const wired = vi.mocked(help.wireScroller).mock.calls.map(([row]) => row);
    expect(wired).toContain(host.querySelector('[data-entity-links]'));
    expect(wired).toContain(host.querySelector('[data-related-people]'));
  });

  it('同台艺人装了实体图才出图，地址带上换图版本；没装的只有首字母', async () => {
    const host = await open(props({ entity: performer({ related_performers: [
      { id: 1, k: '三上悠亜', has_image: true, image_version: '1727800000' }, { id: 2, k: '河北彩花' },
    ] }) }));
    expect(host.querySelector('[data-related-performer="三上悠亜"] img')?.getAttribute('src'))
      .toBe('/entity-image?kind=performer&id=1&v=1727800000');
    expect(host.querySelector('[data-related-performer="河北彩花"] img')).toBeNull();
  });
});

describe('点下去交给壳', () => {
  it('同台艺人与事务所走站内跳转，事务所链接不整页刷新', async () => {
    const acts = actions();
    const host = await open(props({ actions: acts }));
    await click(host.querySelector('[data-related-performer="河北彩花"]'));
    expect(acts.openEntity).toHaveBeenLastCalledWith('performer', '河北彩花');
    const agency = host.querySelector<HTMLAnchorElement>('a[data-agency]');
    expect(agency?.getAttribute('href')).toBe('/agencies/New%20Actor%20eXperience');
    const event = new MouseEvent('click', { bubbles: true, cancelable: true });
    await act(async () => { agency!.dispatchEvent(event) });
    expect(event.defaultPrevented).toBe(true);
    expect(acts.openEntity).toHaveBeenLastCalledWith('agency', 'New Actor eXperience');
  });

  it('事务所没有对应实体时只写名字；片商页带上母公司的去处', async () => {
    const host = await open(props({ entity: performer({ agency: null, metadata: { agency: { name: '无名事务所' } } }) }));
    expect(host.querySelector('a[data-agency]')).toBeNull();
    expect(host.querySelector('[data-entity-alias]')?.textContent).toContain('无名事务所');
    const studio = await open(props({
      kind: 'studio', name: 'MOODYZ', entity: { id: 2, canonical_name: 'MOODYZ', asset_count: 9, maker: { name: 'WILL' } },
    }));
    expect(studio.querySelector('a[data-studio-link="WILL"]')?.getAttribute('href')).toBe('/studios/WILL');
  });

  it('名字下拉：挑另一个写法交给壳换统称，挑当前那个不动，末尾那项是添别名', async () => {
    const acts = actions();
    const host = await open(props({ actions: acts }));
    await click(host.querySelector('[data-namepick-toggle]'));
    await click(document.querySelector('[data-namepick-name="葵つかさ"]'));
    expect(acts.chooseName).not.toHaveBeenCalled();
    await click(host.querySelector('[data-namepick-toggle]'));
    await click(document.querySelector('[data-namepick-name="葵司"]'));
    expect(acts.chooseName).toHaveBeenCalledWith('葵司');
    await click(host.querySelector('[data-namepick-toggle]'));
    await click(document.querySelector('[data-namepick-alias]'));
    expect(acts.addAlias).toHaveBeenCalledTimes(1);
  });
});

describe('关注里的来源', () => {
  const creator = (follow: EntityHeroData['follow']): EntityHeroData => ({
    id: 51, canonical_name: 'Auxtasy', asset_count: 0, follow,
  });
  const held = { key: 'name:auxtasy', name: 'Auxtasy', providers: ['Kemono', 'Patreon'], n: 37, links: [] };

  it('绑了来源的创作者写还没入库的更新数，点开交给壳去关注页筛这一位', async () => {
    const acts = actions();
    const host = await open(props({ kind: 'creator', name: 'Auxtasy', actions: acts, entity: creator({
      key: 'entity:51', n: 12, providers: ['Kemono'], avatar: '', avatar_fallback: '', held: [] }) }));
    const link = host.querySelector<HTMLAnchorElement>('[data-entity-follow] a')!;
    expect(link.textContent).toBe('12 项更新');
    await click(link);
    expect(acts.openFollowAuthor).toHaveBeenCalledWith('entity:51');
    expect(host.querySelector('[data-entity-held]')).toBeNull();
  });

  it('同名的那组只问不绑：按下「是同一个人」才交给壳，等的这一下键上是等待态', async () => {
    let finish: () => void = () => {};
    const acts = actions({ confirmFollowAuthor: vi.fn(() => new Promise<void>((resolve) => { finish = resolve })) });
    const host = await open(props({ kind: 'creator', name: 'Auxtasy', actions: acts, entity: creator({
      key: '', n: 0, providers: [], avatar: '', avatar_fallback: '', held: [held] }) }));
    expect(host.querySelector('[data-entity-follow]')).toBeNull();
    const note = host.querySelector('[data-entity-held]')!;
    expect(note.textContent).toContain('关注里有同名作者 Auxtasy');
    expect(note.textContent).toContain('Kemono · Patreon · 37 项更新');
    const button = note.querySelector('button')!;
    await click(button);
    expect(acts.confirmFollowAuthor).toHaveBeenCalledWith('name:auxtasy', 'Auxtasy');
    expect(button.getAttribute('aria-busy')).toBe('true');
    await act(async () => { finish() });
    expect(button.getAttribute('aria-busy')).toBeNull();
  });
});

describe('订阅新作', () => {
  const toggle = (host: HTMLElement) => host.querySelector<HTMLInputElement>('input[data-entity-feed]')!;

  it('写回成功才拨过去，发带撤销的回执并等这一轮拉取；撤销是另一次写回', async () => {
    const acts = actions();
    const help = helpers();
    const host = await open(props({ actions: acts, helpers: help }));
    await click(toggle(host));
    await settle();
    expect(acts.follow).toHaveBeenCalledWith(true);
    expect(toggle(host).checked).toBe(true);
    expect(acts.refreshFeedAfterCheck).toHaveBeenCalledTimes(1);
    const [message, options] = vi.mocked(help.receipt).mock.calls[0]!;
    expect(message).toBe('已订阅新作');
    await act(async () => { await options?.undo?.() });
    expect(acts.follow).toHaveBeenLastCalledWith(false);
    expect(toggle(host).checked).toBe(false);
  });

  it('写回失败时开关留在原位，不发回执', async () => {
    const acts = actions({ follow: vi.fn(async () => { throw new Error('502') }) });
    const help = helpers();
    const host = await open(props({ actions: acts, helpers: help }));
    await click(toggle(host));
    await settle();
    expect(toggle(host).checked).toBe(false);
    expect(help.receipt).not.toHaveBeenCalled();
    expect(acts.refreshFeedAfterCheck).not.toHaveBeenCalled();
  });
});

describe('新作那一行', () => {
  const row = '<div class="feednewrow">'
    + '<div data-feed-id="11"><button data-feed-action="ignore">不想看</button></div>'
    + '<div data-feed-id="12"><button data-feed-action="read">已看过</button></div></div>';

  it('一条都没有就整块不出', async () => {
    const feedHost = document.createElement('section');
    document.body.append(feedHost);
    await open(props({ feedHost, feedNew: { items: [], html: '' } }));
    expect(feedHost.hidden).toBe(true);
    expect(feedHost.querySelector('[data-feed-id]')).toBeNull();
  });

  it('忽略的当场消失，已看过的留在原位变淡，全部收完整块藏起', async () => {
    const feedHost = document.createElement('section');
    feedHost.hidden = true;
    document.body.append(feedHost);
    const acts = actions();
    const help = helpers();
    await open(props({ feedHost, actions: acts, helpers: help, feedNew: { items: [{ id: 11 }, { id: 12 }], html: row } }));
    expect(feedHost.hidden).toBe(false);
    expect(help.wireFeedRow).toHaveBeenCalledWith(feedHost.querySelector('.feednewrow'));
    await click(feedHost.querySelector('[data-feed-action="read"]'));
    await settle();
    expect(acts.feedAction).toHaveBeenLastCalledWith(12, 'read');
    expect(feedHost.querySelector('[data-feed-id="12"]')?.classList.contains('isread')).toBe(true);
    await click(feedHost.querySelector('[data-feed-action="ignore"]'));
    await settle();
    expect(acts.feedAction).toHaveBeenLastCalledWith(11, 'ignore');
    expect(feedHost.querySelector('[data-feed-id="11"]')).toBeNull();
    expect(feedHost.hidden).toBe(false);
    feedHost.querySelector<HTMLElement>('[data-feed-id="12"] button')!.dataset.feedAction = 'ignore';
    await click(feedHost.querySelector('[data-feed-id="12"] button'));
    await settle();
    expect(feedHost.querySelector('[data-feed-id]')).toBeNull();
    expect(feedHost.hidden).toBe(true);
  });

  it('想要是开关：同一颗键在想要与取消想要之间换，卡片不收也不变淡', async () => {
    const feedHost = document.createElement('section');
    document.body.append(feedHost);
    const acts = actions();
    const html = '<div class="feednewrow"><div data-feed-id="13">'
      + '<button data-feed-action="want" aria-pressed="false" title="想要">想要</button></div></div>';
    await open(props({ feedHost, actions: acts, feedNew: { items: [{ id: 13 }], html } }));
    const key = () => feedHost.querySelector<HTMLElement>('[data-feed-id="13"] button')!;
    await click(key());
    await settle();
    expect(acts.feedAction).toHaveBeenLastCalledWith(13, 'want');
    expect([key().dataset.feedAction, key().getAttribute('aria-pressed'), key().title]).toEqual(['unwant', 'true', '取消想要']);
    await click(key());
    await settle();
    expect(acts.feedAction).toHaveBeenLastCalledWith(13, 'unwant');
    expect([key().dataset.feedAction, key().getAttribute('aria-pressed'), key().title]).toEqual(['want', 'false', '想要']);
    expect(feedHost.querySelector('[data-feed-id="13"]')?.classList.contains('isread')).toBe(false);
    expect(feedHost.hidden).toBe(false);
  });
});

describe('「+N」浮层', () => {
  /* happy-dom 没有原生 popover：顶层开合记在一个属性上，`:popover-open` 读它。 */
  beforeEach(() => {
    const proto = HTMLElement.prototype as HTMLElement & Record<string, unknown>;
    const matches = Element.prototype.matches;
    vi.spyOn(Element.prototype, 'matches').mockImplementation(function (this: Element, selector: string) {
      return selector === ':popover-open' ? this.hasAttribute('data-test-popover-open') : matches.call(this, selector);
    });
    proto.showPopover = function (this: HTMLElement) { this.setAttribute('data-test-popover-open', '') };
    proto.hidePopover = function (this: HTMLElement) { this.removeAttribute('data-test-popover-open') };
  });
  afterEach(() => {
    const proto = HTMLElement.prototype as unknown as Record<string, unknown>;
    delete proto.showPopover;
    delete proto.hidePopover;
  });

  it('点一下钉住，再点收起；Escape 也收起', async () => {
    const host = await open(props({ entity: performer({
      name_groups: { shown: ['葵司'], total: 4, groups: [{ label: '旧名', names: [{ name: 'A' }, { name: 'B' }, { name: 'C' }] }] },
    }) }));
    const more = host.querySelector<HTMLButtonElement>('[data-hero-more="alias"]')!;
    expect(more.textContent).toBe('+3');
    expect(more.getAttribute('aria-label')).toBe('另外 3 个别名');
    const pop = document.getElementById('entityAliasPop')!;
    expect(pop.querySelector('[data-hero-pop-head]')?.textContent).toBe('4 个别名');
    await click(more);
    expect(more.getAttribute('aria-expanded')).toBe('true');
    await click(more);
    expect(more.getAttribute('aria-expanded')).toBe('false');
    await click(more);
    await act(async () => { more.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })) });
    expect(more.getAttribute('aria-expanded')).toBe('false');
  });
});
