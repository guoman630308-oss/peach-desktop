/* 顶栏搜索（`search` 岛，ADR-0031 第 11b 步）：数据形状与菜单模型。
 *
 * 输入框 `#q` 是壳的静态节点，岛不画它，只在上面接事件：它在 React 包到之前就得能敲字，壳里的
 * 路由、窄屏开合与清空时的残影（`clearSearchField`）也都直接读写它。下拉栏 `#searchMenu` 的内容、
 * 搜索记录、推荐词、按字补全、键盘选中与提交都归岛；提交之后跳页回壳（`actions.search`），同
 * 打开作品、打开资料页一样走壳的路由函数。
 *
 * 下拉栏的开合动效是全站菜单那一副（遗留层 `presentMenu`／`dismissMenu`，由 `helpers` 递进来）：
 * `hidden` 始终是「看不见了」，壳的全局 Escape 与外点收起照旧读它。 */

/** 补全里的一张小封面：作品本身，或人名那一行右边的近作。 */
export interface SuggestCard {
  id: number;
  code?: string | null;
  [key: string]: unknown;
}

export interface SuggestItem {
  value: string;
  /** 命中的是别名时，那个别名；跟在统称后面当注脚。 */
  matched?: string | null;
  n?: number;
  /** 作品那一类：点开是详情，不是一个搜索词。 */
  id?: number;
  who?: string | null;
  code?: string | null;
  title?: string | null;
  card?: SuggestCard | null;
  /** 人那一类：一行里摆得下几部近作就摆几部。 */
  works?: SuggestCard[];
  agency?: string | null;
  href?: string;
  avatarHtml?: string;
  subtitle?: string;
  followMedia?: 'videos' | 'images';
  mediaIndex?: number | null;
  [key: string]: unknown;
}

export interface SuggestGroup {
  kind: string;
  label: string;
  total?: number;
  items: SuggestItem[];
  href?: string;
  action?: string;
}

export interface SuggestTab {
  kind: string;
  label: string;
  total?: number;
}

export interface SuggestResponse {
  q?: string;
  groups?: SuggestGroup[];
}

/** 跳页与打开：都回壳，走它的路由函数。 */
export interface SearchActions {
  /** 按这个词搜：壳关掉详情、写 `?q=` 并重取目录。 */
  search(query: string): void;
  openItem(id: number): void;
  openEntity(kind: string, name: string): void;
  openPath?(path: string): void;
  openFollow?(id: number, media: 'videos' | 'images', mediaIndex: number | null): void;
}

/** 仍由遗留层给的几样。 */
export interface SearchHelpers {
  /** 空输入时的推荐词池：与当前目录同一组筛选，只留真搜得出东西的词（`catalogSuggestions`）。 */
  pool(): Promise<string[]>;
  recommendations?(): Promise<SuggestGroup[]>;
  /** 小封面那一格（卡片的 `.pic`）连同里面那张图（`coverImage` 与 JAV 默认封面那套取景）；两样都没有是「无预览」。 */
  coverHtml(card: SuggestCard | null | undefined): string;
  present(menu: HTMLElement): void;
  dismiss(menu: HTMLElement): void;
  /** 窄屏页签排不下时右缘渐隐（`wireHorizontalScroller`）。 */
  wireScroller(row: Element | null): void;
  /** 输入框的字变了：离开搜索结果时那段残影记的是哪句话，由壳记（`rememberSearchValue`）。 */
  typed(input: HTMLInputElement): void;
  /** 开始组字：正在散开的残影当场收掉。 */
  composing(): void;
  /** 有字时按 Escape：清空并留一段残影（`clearSearchField`）。 */
  clearField(input: HTMLInputElement): void;
}

/** 壳手里的命令式入口，挂上时由 `expose` 交出去，卸载时交回 null。 */
export interface SearchApi {
  /** 收起下拉栏、清掉键盘选中。全局 Escape、外点与失焦都走这里。 */
  close(): void;
}

export interface SearchProps {
  /** 顶栏那个静态的 `#q`。 */
  input: HTMLInputElement;
  /** 搜索记录留几条（设置页那一项）；0 就是不记也不显示。 */
  historyLimit: number;
  actions: SearchActions;
  helpers: SearchHelpers;
  expose(api: SearchApi | null): void;
}

/** 敲一下就查一次的补全：150ms 内继续敲就换掉上一次的排期，只发一次请求。 */
export const SUGGEST_DEBOUNCE = 150;
/** 多类预览每类五条；单类预览与分类页签各展示二十条。 */
export const SUGGEST_EACH = 5;
export const SUGGEST_ONE_KIND = 20;
/** 有脸的那几类点开的是资料页；标签没有资料页，点它照旧是按这个词搜。 */
export const SUGGEST_PROFILE_KINDS = new Set(['performer', 'creator', 'studio', 'agency', 'series']);
/** 下拉栏按宽度分两栏：左栏是身份和词，右栏是作品封面格。 */
export const SUGGEST_RIGHT_KINDS = new Set(['asset', 'jav', 'post', 'photo', 'release']);
/** 空输入时推荐几条。 */
export const PICKS = 5;

/** 名字比较一律折叠全半角与大小写，同遗留层 `foldName`。 */
export const fold = (value: unknown): string => String(value ?? '').normalize('NFKC').trim().toLocaleLowerCase();

/** 下拉栏里能被键盘选中的一行。`index` 是它在整栏里的次序，上下键按它走。 */
export type SearchOption =
  | { index: number; type: 'history' | 'pick'; value: string }
  | { index: number; type: 'more'; value: string; total: number }
  | { index: number; type: 'suggest'; value: string; kind: string; item: SuggestItem };

export interface MenuSection {
  kind?: string;
  label: string;
  options: SearchOption[];
  more?: SearchOption;
  href?: string;
  action?: string;
}

export interface MenuModel {
  /** 命中不止一类时的页签；只有一类的话「全部」和那一类是同一屏。 */
  tabs: SuggestTab[] | null;
  /** 有输入且没选页签时，列在最上面的搜索记录。 */
  recent: MenuSection | null;
  /** 两栏：左栏身份和词，右栏作品。空输入时是记录与推荐两组短词。 */
  left: MenuSection[];
  right: MenuSection[];
  /** 这个词的补全已经回来、一条都没有：栏里写一行「没有找到」，分得清是没结果还是还没搜。 */
  empty: boolean;
  options: SearchOption[];
}

export interface MenuInput {
  query: string;
  history: readonly string[];
  /** 画的那一刻洗好的推荐（`shufflePicks`）。 */
  picks: readonly string[];
  /** 补全是这个词的才算数；慢回来的上一个词不画。 */
  suggestFor: string;
  groups: readonly SuggestGroup[];
  tabs: readonly SuggestTab[];
  kind: string;
  recommendations?: readonly SuggestGroup[];
}

/** 从一刻的状态算出下拉栏里有什么，以及键盘上下时的次序。页签管整栏，所以排在最上面；选了一类
 *  时搜索记录让位。 */
export function menuModel({ query, history, picks, suggestFor, groups, tabs, kind, recommendations = [] }: MenuInput): MenuModel {
  let index = 0;
  const options: SearchOption[] = [];
  const add = <T extends Omit<SearchOption, 'index'>>(option: T): SearchOption => {
    const next = { ...option, index: index++ } as SearchOption;
    options.push(next);
    return next;
  };
  // 有输入时历史跟着筛：这一刻用户在找一个词，不是在回顾自己搜过什么。
  const matching = history.filter((value) => !query || fold(value).includes(fold(query)));
  if (!query) {
    if (recommendations.length) {
      const sections = recommendations.map(group => ({ ...group,
        options: group.items.map(item => add({ type: 'suggest', value: item.value, kind: group.kind, item })),
      }));
      const recent = matching.length ? { label: '搜索记录', options: matching.map(value => add({ type: 'history', value })) } : null;
      return { tabs: null, recent,
        left: sections.filter(group => !SUGGEST_RIGHT_KINDS.has(group.kind)),
        right: sections.filter(group => SUGGEST_RIGHT_KINDS.has(group.kind)), empty: false, options };
    }
    const recent = matching.length ? { label: '搜索记录', options: matching.map((value) => add({ type: 'history', value })) } : null;
    const suggested = picks.length ? { label: '推荐', options: picks.map((value) => add({ type: 'pick', value })) } : null;
    return { tabs: null, recent: null, left: recent ? [recent] : [], right: suggested ? [suggested] : [], empty: false, options };
  }
  const fresh = suggestFor === query;
  const recent = !kind && matching.length
    ? { label: '搜索记录', options: matching.map((value) => add({ type: 'history', value })) } : null;
  // 选了一类就只画这一类；拉满那一类的请求还在路上时，先用「全部」里的那几条顶着。
  const shown = (fresh ? groups : []).filter((group) => !kind || group.kind === kind);
  const section = (group: SuggestGroup): MenuSection => {
    const rows = group.items.map((item) => add({ type: 'suggest', value: item.value, kind: group.kind, item }));
    const more = group.kind === 'asset' && (group.total ?? 0) > rows.length
      ? add({ type: 'more', value: query, total: group.total! }) : undefined;
    return { kind: group.kind, label: group.label, options: rows, more };
  };
  const left = shown.filter((group) => !SUGGEST_RIGHT_KINDS.has(group.kind)).map(section);
  const right = shown.filter((group) => SUGGEST_RIGHT_KINDS.has(group.kind)).map(section);
  const empty = fresh && !shown.some((group) => group.items.length);
  return { tabs: fresh && tabs.length > 1 ? [...tabs] : null, recent, left, right, empty, options };
}

/** 空输入时从词池里洗出几条推荐，搜过的不再推。每画一次下拉栏洗一次；删一条记录不算重画，
 *  所以这一批在画的那一刻定下，删掉的那条记录即便也在词池里，也不会顶进推荐。 */
export function shufflePicks(pool: readonly string[], history: readonly string[],
  random: () => number = Math.random): string[] {
  const out = [...pool];
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1));
    [out[i], out[j]] = [out[j]!, out[i]!];
  }
  return out.filter((value) => !history.some((h) => fold(h) === fold(value))).slice(0, PICKS);
}
