/* 实体资料页顶上的资料卡与新作行（ADR-0031 第 10c 步）：数据形状与纯函数。
 *
 * `/api/entity` 由壳取，连同这一页的新作一起当 props 递进来；写操作都由壳做成 `actions`
 * 递进来，岛不自己拼请求。这里只把服务端下发的那份资料排成页面要画的几段：名字下面那一行、
 * 右边的资料表、外链、外部入口和同台艺人。 */
import { brandIcon, foldName, linkMarkUrl, officialLinkText } from '@peach/legacy/core';

import type { FeedNew } from '../feed-new/feed-new';

/** 一条外链，形状同 `/api/entity` 的 `links[]`。 */
export interface HeroLink {
  link_id?: number | null;
  link_kind?: string;
  clickable?: boolean;
  gone?: boolean;
  retired_year?: number | string | null;
  label?: string;
  hostname?: string;
  url?: string;
}

/** 外部入口（`peach.entry_links`）：`pill` 排进外链那排最左，`mark` 另起一行印站点标识。 */
export interface HeroEntryLink {
  site: string;
  label: string;
  ordinal?: string;
  slot: string;
  mark?: string;
  url: string;
}

export interface HeroCostar {
  id: number;
  k: string;
  n?: number;
  rep?: number | null;
  has_image?: boolean;
  image_version?: string;
  has_avatar?: boolean;
  avatar_stand_in?: boolean;
  avatar_focus?: unknown;
}

/** 女优页头右侧的资料表（ADR-0069）。服务端只下发有值的项。 */
export interface HeroProfile {
  birth_date?: string;
  age?: number;
  height?: number;
  bust?: number;
  waist?: number;
  hip?: number;
  cup?: string;
  debut_date?: string;
  debut_title?: string;
  active?: { from?: string; to?: string; ongoing?: boolean };
  tags?: string[];
}

export interface HeroNameGroups {
  reading?: string;
  shown?: string[];
  total?: number;
  groups?: { label?: string; names: { name: string; reading?: string }[] }[];
}

/** 创作者名下的关注来源（ADR-0096）。`key` 是关注页的作者键，没绑来源时是空串；`held` 是
 *  关注里同名、等用户认是不是同一个人的那几组。 */
export interface HeroFollow {
  key: string;
  n: number;
  providers: string[];
  avatar: string;
  avatar_fallback: string;
  held: { key: string; name: string; providers: string[]; n: number; links: string[] }[];
}

/** `/api/entity` 里资料卡读到的那些字段。 */
export interface EntityHeroData {
  favorite?: boolean;
  company_profile?: Partial<Record<'legal_name' | 'founded' | 'launched' | 'country' | 'location' | 'operator' | 'parent' | 'group' | 'distributor', { value: string; source_url: string; evidence?: string }>>;
  identity_labels?: string[];
  external_refs?: { provider: string; external_kind: string; external_id: string }[];
  related_identities?: { id: number; kind: string; canonical_name: string; relation: string }[];
  id: number;
  canonical_name: string;
  aliases?: string[];
  display_aliases?: string[];
  user_aliases?: string[];
  asset_count: number;
  member_count?: number;
  labels?: unknown[];
  maker?: { name: string; id?: number } | null;
  agency?: { canonical_name: string; id?: number } | null;
  metadata?: { agency?: { name?: string } | null } | null;
  links?: HeroLink[];
  entry_links?: HeroEntryLink[];
  related_performers?: HeroCostar[];
  feed?: { following: boolean } | null;
  follow?: HeroFollow | null;
  profile?: HeroProfile | null;
  name_groups?: HeroNameGroups | null;
}

/** 写操作与站内跳转都归壳。 */
export interface EntityHeroActions {
  /** 站内跳转，走遗留路由的同一个入口。 */
  openEntity(kind: string, name: string): void;
  /** 把这个名字换成统称：确认、写回、回执与重进这一页都在壳里。 */
  chooseName(name: string): void;
  /** 打开「添加别名」弹层。 */
  addAlias(): void;
  /** 订阅或取消订阅新作。失败时壳已发过失败回执，这里会收到抛出的错误。 */
  follow(on: boolean): Promise<void>;
  /** 刚订上：等服务端这一轮拉取跑完，再把新作那一行重取一遍。 */
  refreshFeedAfterCheck(): void;
  /** 新作卡上的「不想看」「标为已看过」。 */
  feedAction(id: number, action: string): Promise<void>;
  /** 换完头像：重进这一页，头像索引在服务端已经换过。 */
  avatarPicked(): void;
  /** 看这一位名下来源的更新：本人的切到这一页的在线视图，别的作者键去关注页只看那一位。 */
  openFollowAuthor(key: string): void;
  /** 「是同一个人」：把同名的那组关注作者绑到这位名下。失败时壳已发过失败回执。 */
  confirmFollowAuthor(key: string, name: string): Promise<void>;
}

/** 仍由遗留层拼的那几段 HTML 与接线。 */
export interface EntityHeroHelpers {
  /** 大位那张图（`entityFaceImg`）：取不到就是空串，首字母垫底。 */
  portraitImg(): string;
  /** 横滚行接上拖动与滚轮（`wireDrag`）。 */
  wireScroller(row: Element | null): void;
  /** 新作那一行：拖动、滚轮，按设置接自动滚动。 */
  wireFeedRow(row: Element | null): void;
  /** 操作回执（`actionReceipt`），给了 `undo` 就带一颗撤销键。 */
  receipt(message: string, options?: { undo?: () => Promise<void> }): void;
}

export interface EntityHeroProps {
  kind: string;
  /** 地址栏上的那个名字：首字母垫底取它，和骨架同一个字。 */
  name: string;
  entity: EntityHeroData;
  /** 这一页的新作（`feed-new/feed-new.ts` 取回来的条目与壳拼好的整行 HTML）。 */
  feedNew: FeedNew | null;
  /** 壳在 `#index` 里留给新作那一行的 `section.feednew[data-feed-new]`。 */
  feedHost: HTMLElement | null;
  /** 这一页是不是 JAV 语境（`entityJavLayout`）。 */
  jav: boolean;
  actions: EntityHeroActions;
  helpers: EntityHeroHelpers;
}

export const FACT_TAGS_SHOWN = 4;

/** 公司页（厂牌、事务所）的门面是它自己的标识，方框；人是圆框，右下角有换头像的入口。 */
export const isCompany = (kind: string) => kind === 'studio' || kind === 'agency';
export const isPeople = (kind: string) => kind === 'performer' || kind === 'creator';

export const entityFeedTip = (on: boolean) => on
  ? '已订阅新作，点击取消订阅'
  : '订阅新作';

/** 这条实体名下已有的写法，统称排第一。 */
export function nameChoices(entity: EntityHeroData): string[] {
  return [entity.canonical_name, ...(entity.aliases || [])]
    .filter((option, index, all) => option && all.indexOf(option) === index);
}

/** 事务所：有实体时给去处，只有 `metadata.agency` 时只写名字（那是采到的原文，还没有对应身份）。 */
export function agencyOf(entity: EntityHeroData): { name: string; linked: boolean; id?: number } | null {
  if (entity.agency) return { name: entity.agency.canonical_name, linked: true, id: entity.agency.id };
  const name = entity.metadata?.agency?.name || '';
  return name ? { name, linked: false } : null;
}

/** 女优页外链一律是纯图标，名字只在 title 和读屏文字里。链到她所属事务所的那条（NAX 的
 *  label 就是 `New Actor eXperience`）写「<事务所> 官方资料」：图标看不出那是她的官方档案页。 */
export function performerLinkName(link: HeroLink, agency: string): string {
  const text = officialLinkText(link, 'performer'), home = foldName(agency);
  const own = home && [link.label, text].map(foldName).some((said) => said && (home.includes(said) || said.includes(home)));
  return own ? `${agency} 官方资料` : (text || link.label || '');
}

/** 链接前那枚圆标：常见社媒走雪碧图里的品牌字形，其余取服务端合成的站点圆标，取不到露出地球。 */
export type LinkMark = { brand: string } | { site: string } | { globe: true };

export type LinkView =
  | { type: 'icon'; url: string; title: string; mark: LinkMark }
  | { type: 'gone'; title: string }
  | { type: 'private'; label: string; sources?: string[] }
  | { type: 'url'; url: string; title: string; text: string; mark: LinkMark };

const siteMark = (link: HeroLink): LinkMark => ({ site: linkMarkUrl(link) });

/** 外链那一排：外部入口里的 `pill` 排最前，其后是数据库里的链接。
 *
 *  社媒收成纯图标，公司页的官网保留名字；女优页连官网也是图标，右边多了一张资料表，名字那一栏
 *  放不下一排带字的按钮。已隐退女优的失效链接留在原位、不可点，也不取站点图标。 */
export function heroLinks(entity: EntityHeroData, kind: string): LinkView[] {
  const agency = agencyOf(entity)?.name || '';
  const pills: LinkView[] = (entity.entry_links || []).filter((x) => x.slot === 'pill')
    .map((x) => ({ type: 'icon', url: x.url, title: x.label, mark: { brand: x.mark || '' } }));
  const names = [entity.canonical_name, ...(entity.aliases || [])];
  const links = (entity.links || []).map((x): LinkView => {
    if (x.gone) {
      const why = x.retired_year ? `已于 ${x.retired_year} 年隐退` : '链接已失效';
      return { type: 'gone', title: `${performerLinkName(x, agency)} · ${why}` };
    }
    const url = x.url || '';
    if (!(x.clickable && /^https?:\/\//i.test(url))) return { type: 'private', label: x.label || x.hostname || '已记录' };
    if (isPeople(kind) && x.link_kind !== 'social') {
      return { type: 'icon', url, title: performerLinkName(x, agency), mark: siteMark(x) };
    }
    if (x.link_kind === 'social') {
      const brand = brandIcon(url);
      return { type: 'icon', url, title: x.label || '', mark: brand ? { brand } : siteMark(x) };
    }
    return { type: 'url', url, title: x.label || '', text: officialLinkText(x, kind, names), mark: siteMark(x) };
  });
  return [...pills, ...foldPrivate(links)];
}

/** 不公开的来源点不开，只是「记过这么一处」：两处以上收成排在末尾的一枚「N 个不公开来源」，
 *  各处的名字进 title。夹在图标中间一处占一整行，会把一排图标外链切成好几段。 */
function foldPrivate(links: LinkView[]): LinkView[] {
  const hidden = links.filter((link): link is Extract<LinkView, { type: 'private' }> => link.type === 'private');
  if (hidden.length < 2) return links;
  return [
    ...links.filter((link) => link.type !== 'private'),
    { type: 'private', label: `${hidden.length.toLocaleString()} 个不公开来源`, sources: hidden.map((link) => link.label) },
  ];
}

/** 看片的那一行：站点自己的横向标识。 */
export const entryMarks = (entity: EntityHeroData) => (entity.entry_links || []).filter((x) => x.slot === 'mark');

export interface FactRow {
  glyph: string;
  label: string;
  /** 行内的几段：`num` 等宽数字，`sub` 次级字色。 */
  parts: { text: string; tone: 'num' | 'sub' | 'plain' }[];
  /** 出道片名常有四五十个字，单行截断，全名放在 title 里。 */
  clip?: string | undefined;
  tags?: string[] | undefined;
}

/** 公司资料每格表达独立事实；没有数据的格子不显示。 */
export function companyFactRows(profile: EntityHeroData['company_profile']): FactRow[] {
  const fields = [
    ['legal_name', '公司名称', 'briefcase'], ['founded', '公司成立', 'calendar'],
    ['launched', '品牌启动', 'flag'], ['country', '国家／地区', 'globe'],
    ['location', '所在地', 'globe'], ['operator', '运营公司', 'briefcase'], ['parent', '母公司', 'briefcase'],
    ['group', '所属集团', 'briefcase'], ['distributor', '发行商', 'film'],
  ] as const;
  return fields.flatMap(([key, label, glyph]) => profile?.[key]?.value?.trim()
    ? [{ label, glyph, parts: [{ text: profile[key]!.value, tone: 'plain' as const }] }]
    : []);
}

/** 资料表的五项：生日、身材、出道、生涯、标签。有哪项画哪项。仍在活跃的写「至今」。 */
export function factRows(p: HeroProfile | null | undefined): FactRow[] {
  if (!p) return [];
  const rows: FactRow[] = [];
  // 年龄由服务端按生日算、与生日成对下发；只拿到生日时那一行只写生日。
  if (p.birth_date) rows.push({ glyph: 'cake', label: '生日', parts: [
    { text: p.birth_date, tone: 'num' },
    ...(Number.isFinite(p.age) ? [{ text: `· ${p.age} 岁`, tone: 'sub' as const }] : [])] });
  const size = ([['T', p.height], ['B', p.bust], ['W', p.waist], ['H', p.hip]] as const)
    .filter(([, value]) => value).map(([letter, value]) => `${letter}${value}`).join(' · ');
  const cup = p.cup ? `${size ? '· ' : ''}${p.cup} 罩杯` : '';
  if (size || cup) rows.push({ glyph: 'ruler', label: '身材', parts: [
    ...(size ? [{ text: size, tone: 'num' as const }] : []), ...(cup ? [{ text: cup, tone: 'sub' as const }] : [])] });
  if (p.debut_date || p.debut_title) rows.push({ glyph: 'flag', label: '出道', clip: p.debut_title || undefined, parts: [
    ...(p.debut_date ? [{ text: p.debut_date, tone: 'num' as const }] : []),
    ...(p.debut_title ? [{ text: p.debut_title, tone: 'sub' as const }] : [])] });
  const active = p.active || {};
  if (active.from) rows.push({ glyph: 'calendar-range', label: '生涯', parts: [{
    text: `${active.from}${active.ongoing ? ' – 至今' : active.to ? ` – ${active.to}` : ''}`, tone: 'num' }] });
  if ((p.tags || []).length) rows.push({ glyph: 'tags', label: '标签', parts: [], tags: p.tags });
  return rows;
}

/** 公司页名字下面那一行先列几个别名，余下的收进「+N」。 */
export const COMPANY_ALIASES_SHOWN = 3;

/** 标签那一格只列前几个，余下的收进「+N」；只多出一个时直接列出来，「+1」和那一个标签一样宽。
 *  公司别名按 `COMPANY_ALIASES_SHOWN` 用同一条规则。 */
export function shownTags(tags: string[], limit = FACT_TAGS_SHOWN): { shown: string[]; rest: number } {
  const cut = tags.length > limit + 1 ? limit : tags.length;
  return { shown: tags.slice(0, cut), rest: tags.length - cut };
}

/** 名字下面那一行的别名：读音在最前，接着是前几个名义，余下的收进「+N」。 */
export function nameLine(groups: HeroNameGroups | null | undefined): { names: string[]; rest: number; total: number } | null {
  if (!groups) return null;
  const shown = groups.shown || [];
  const names = [groups.reading || '', ...shown].filter(Boolean);
  const rest = (groups.total || 0) - shown.length;
  return names.length || rest > 0 ? { names, rest, total: groups.total || 0 } : null;
}
