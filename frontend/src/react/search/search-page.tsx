/* 顶栏搜索的下拉栏与键盘（`search` 岛）。
 *
 * 状态放在一个随组件活着的可变仓里，输入框上的原生事件改它再重画：`#q` 是壳的节点，事件只能用
 * `addEventListener` 接，处理器里要读的是这一刻的值，不是挂上那一刻闭包里的那份。输入法组字期间
 * 的按键读原生事件的 `isComposing`：方向键在挑候选字、回车在定字，都不是给这个菜单的。
 *
 * BoardUI 注册表里没有 Combobox／Command（`https://www.boardui.com/r/registry.json`，2026-10-01），
 * React Aria 的 ComboBox 要自己画输入框、选项是 listbox 里的 option：选项里嵌的删记录键、近作
 * 小封面键和最上面那排页签都放不进去。所以这里按遗留层那一套逐条照做：上下环绕着选、回车提交、
 * Escape 先关下拉再清字、空输入回车用占位里那个推荐词。 */
import { useLayoutEffect, useReducer, useRef, type MouseEvent, type ReactNode } from 'react';
import { avatarInner } from '@peach/card-art';

import { apiGet, apiSend } from '../../api';
import { TabCount } from '../components/board-tabs';
import {
  menuModel, shufflePicks, SUGGEST_DEBOUNCE, SUGGEST_EACH, SUGGEST_ONE_KIND, SUGGEST_PROFILE_KINDS, fold,
  type MenuModel, type MenuSection, type SearchApi, type SearchOption, type SearchProps, type SuggestCard,
  type SuggestGroup, type SuggestItem, type SuggestResponse, type SuggestTab,
} from './search';

interface Store {
  history: string[];
  pool: string[];
  poolRequest: number;
  recommendations: SuggestGroup[];
  recommendationRequest: number;
  /** 画的那一刻输入框里的词与洗好的推荐。 */
  query: string;
  picks: string[];
  suggestFor: string;
  groups: SuggestGroup[];
  tabs: SuggestTab[];
  suggestRequest: number;
  /** 最近一次不分类补全问的词：聚焦时的数据晚到，框里还是这个词就不再问一遍。 */
  asked: string;
  timer: number;
  kind: string;
  active: number;
  /** 这一次选中是键盘挪的：画完把那一行滚进视野。 */
  reveal: boolean;
  /** 选了一类：画完把下拉栏滚回顶上。 */
  rewind: boolean;
}

const emptyStore = (): Store => ({
  history: [], pool: [], poolRequest: 0, recommendations: [], recommendationRequest: 0, query: '', picks: [], suggestFor: '', groups: [], tabs: [],
  suggestRequest: 0, asked: '', timer: 0, kind: '', active: -1, reveal: false, rewind: false,
});

const modelOf = (s: Store): MenuModel => menuModel({
  query: s.query, history: s.history, picks: s.picks, suggestFor: s.suggestFor, groups: s.groups, tabs: s.tabs,
  kind: s.kind,
  recommendations: s.recommendations,
});
const hasContent = (m: MenuModel) => !!(m.tabs || m.recent || m.left.length || m.right.length || m.empty);

const Sprite = ({ name }: { name: string }) => (
  <svg viewBox="0 0 24 24" aria-hidden="true"><use href={`#i-${name}`} /></svg>
);

export function SearchPage(props: SearchProps) {
  const latest = useRef(props);
  latest.current = props;
  const store = useRef<Store | null>(null);
  store.current ??= emptyStore();
  const s = store.current;
  const root = useRef<HTMLDivElement>(null);
  const [, bump] = useReducer((n: number) => n + 1, 0);

  /* 处理器一次建好、挂在输入框上；读的都是 `latest` 与仓里这一刻的值。 */
  const ops = useRef<ReturnType<typeof createOps> | null>(null);
  ops.current ??= createOps(() => latest.current, s, () => root.current?.closest<HTMLElement>('[data-search-menu]') ?? null, bump);
  const op = ops.current;

  useLayoutEffect(() => {
    const { input } = latest.current;
    const onInput = (event: Event) => op.input(event as InputEvent);
    const onStart = () => latest.current.helpers.composing();
    const onKey = (event: KeyboardEvent) => op.key(event);
    const onFocus = () => op.focus();
    input.addEventListener('input', onInput);
    input.addEventListener('compositionend', onInput);
    input.addEventListener('compositionstart', onStart);
    input.addEventListener('keydown', onKey);
    input.addEventListener('focus', onFocus);
    const api: SearchApi = { close: op.close };
    latest.current.expose(api);
    return () => {
      input.removeEventListener('input', onInput);
      input.removeEventListener('compositionend', onInput);
      input.removeEventListener('compositionstart', onStart);
      input.removeEventListener('keydown', onKey);
      input.removeEventListener('focus', onFocus);
      clearTimeout(s.timer);
      s.recommendationRequest++;
      latest.current.expose(null);
    };
  }, [op, s]);

  useLayoutEffect(() => {
    const menu = root.current?.closest<HTMLElement>('[data-search-menu]');
    if (s.rewind && menu) menu.scrollTop = 0;
    s.rewind = false;
    if (s.reveal) root.current?.querySelector('[data-search-option][data-active]')?.scrollIntoView({ block: 'nearest' });
    s.reveal = false;
  });

  const model = modelOf(s);
  const limit = props.historyLimit;
  const option = (entry: SearchOption): ReactNode => (
    <Row key={`${entry.type}:${entry.value}:${entry.index}`} entry={entry} active={entry.index === s.active}
      helpers={props.helpers} onPick={op.pick} onPeek={op.peek} onRemove={op.remove} />
  );
  const section = (group: MenuSection) => (
    <section key={group.kind ?? group.label} data-search-group="" data-kind={group.kind}>
      <h3>{group.label}{group.href ? (
        <a href={group.href} onMouseDown={keep} onClick={event => {
          if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
          event.preventDefault(); op.openPath(group.href!);
        }}>{group.action || '查看全部'}</a>
      ) : null}</h3>
      {group.kind ? <div data-search-items="">{group.options.map(option)}</div> : group.options.map(option)}
      {group.more ? option(group.more) : null}
    </section>
  );
  const columns = (left: MenuSection[], right: MenuSection[]) => (left.length || right.length ? (
    <div data-search-results="" data-split={left.length && right.length ? '' : undefined}>
      {left.length ? <div data-search-col="">{left.map(section)}</div> : null}
      {right.length ? <div data-search-col="">{right.map(section)}</div> : null}
    </div>
  ) : null);
  return (
    <div ref={root} data-search-root="" data-search-recommendations={!s.query && s.recommendations.length ? '' : undefined} data-history-limit={limit}>
      {model.tabs ? <Tabs tabs={model.tabs} kind={s.kind} onPick={op.pickKind} wire={props.helpers.wireScroller} /> : null}
      {s.query && model.recent ? section(model.recent) : null}
      {columns(model.left, model.right)}
      {!s.query && model.recent ? section(model.recent) : null}
      {model.empty ? <p data-search-empty="" role="status">没有找到</p> : null}
    </div>
  );
}

function createOps(props: () => SearchProps, s: Store, menu: () => HTMLElement | null, bump: () => void) {
  const input = () => props().input;
  const focused = () => document.activeElement === input();
  const readHistory = () => s.history.slice(0, props().historyLimit);
  const writeHistory = (list: string[]) => { s.history = list.slice(0, props().historyLimit) };

  const loadHistory = async () => {
    const limit = props().historyLimit;
    if (!limit) { s.history = []; return }
    try {
      const data = await apiGet<{ items?: unknown }>(`/api/search-history?limit=${limit}`);
      s.history = Array.isArray(data.items) ? data.items.map(String) : [];
    } catch {
      // 记录读不到就用手上这份：搜索本身不受影响。
    }
  };
  /* 搜索本身是只读能力；数据库暂时只读时，历史记录降级为本次页面内存，写失败不妨碍搜索。 */
  const rememberSearch = (query: string) => {
    if (!query || !props().historyLimit) return;
    writeHistory([query, ...readHistory().filter((value) => fold(value) !== fold(query))]);
    void apiSend('/api/search-history', { query }).catch(() => null);
  };
  const loadPool = async () => {
    const request = ++s.poolRequest;
    s.pool = [];
    const field = input();
    field.dataset.suggestion = '';
    field.placeholder = '搜索馆藏';
    try {
      const names = await props().helpers.pool();
      if (request !== s.poolRequest) return;
      s.pool = names;
      const pick = names[Math.floor(Math.random() * names.length)] || '';
      field.dataset.suggestion = pick;
      field.placeholder = pick || '搜索馆藏';
    } catch {
      // 推荐不可用时仍可直接输入搜索。
    }
  };
  const loadRecommendations = async () => {
    if (!props().helpers.recommendations) return;
    const request = ++s.recommendationRequest;
    try {
      const groups = await props().helpers.recommendations!();
      if (request !== s.recommendationRequest) return;
      s.recommendations = groups;
      if (focused() && !input().value.trim()) paint();
    } catch {
      // 推荐读取失败仍可搜索，保留手上的推荐。
    }
  };
  /* 分组顺序和每组的名字都由 `/api/suggest` 给出，这里照抄。慢的旧响应不许盖掉新的：连敲两个字时
     先发的那次完全可能后回来。 */
  const loadSuggestions = async (query: string, kind = '') => {
    const request = ++s.suggestRequest;
    if (!kind) s.asked = query;
    try {
      const data = await apiGet<SuggestResponse>(`/api/suggest?q=${encodeURIComponent(query)}`
        + (kind ? `&kind=${kind}` : '') + `&limit=${SUGGEST_ONE_KIND}`);
      if (request !== s.suggestRequest) return;
      s.suggestFor = data.q || '';
      s.groups = data.groups || [];
      if (!kind && s.groups.length > 1) {
        s.groups = s.groups.map((group) => ({ ...group, items: group.items.slice(0, SUGGEST_EACH) }));
      }
      if (!kind) s.tabs = s.groups.map(({ kind: k, label, total }) => ({ kind: k, label, total }));
    } catch {
      if (request === s.suggestRequest) { s.suggestFor = query; s.groups = [] }
    }
  };

  const isOpen = () => { const el = menu(); return !!el && !el.hidden };
  const close = () => {
    s.recommendationRequest++;
    const el = menu();
    if (el) props().helpers.dismiss(el);
    s.active = -1;
    bump();
  };
  /** 按此刻的输入与手上的数据画一遍：有东西就掀开，没有就收起。推荐每画一次洗一次。 */
  const paint = () => {
    s.query = input().value.trim();
    s.picks = s.query ? [] : shufflePicks(s.pool, readHistory());
    s.active = -1;
    const el = menu();
    if (el) {
      if (hasContent(modelOf(s))) props().helpers.present(el);
      else props().helpers.dismiss(el);
    }
    bump();
  };
  /* 每一下输入都排一次补全，但只发一次请求。先按手头已有的内容重画一遍；请求返回时核对焦点，
     点击页面或移到其他控件后不再打开建议。 */
  const refresh = () => {
    s.active = -1;
    // 换了词就回到「全部」：上一个词选中的那一类，这个词下可能一条都没有。
    s.kind = '';
    clearTimeout(s.timer);
    const query = input().value.trim();
    if (!query) { s.suggestFor = ''; s.groups = [] }
    if (isOpen()) paint();
    if (!query) return;
    s.timer = window.setTimeout(() => void loadSuggestions(query).then(() => { if (focused()) paint() }), SUGGEST_DEBOUNCE);
  };
  const pickKind = (kind: string) => {
    const query = input().value.trim();
    if (!query || kind === s.kind) return;
    s.kind = kind;
    clearTimeout(s.timer);
    paint();
    s.rewind = true;
    void loadSuggestions(query, kind).then(() => { if (focused() && s.kind === kind) paint() });
  };
  const options = () => (isOpen() ? modelOf(s).options : []);
  const move = (step: number) => {
    const list = options();
    if (!list.length) return false;
    s.active = (s.active + step + list.length) % list.length;
    s.reveal = true;
    bump();
    return true;
  };
  const runSearch = (useSuggestion: boolean, committed: boolean) => {
    const field = input();
    let query = field.value.trim();
    if (useSuggestion && !query) { query = field.dataset.suggestion || ''; field.value = query }
    if (committed) rememberSearch(query);
    props().actions.search(query);
  };
  /* 人、公司和系列点开就是资料页，不绕一趟搜索：按名字搜出来的是一屏作品，而用户点的是「这个人」。
     记进搜索记录的是这个名字，下次聚焦还找得回来。 */
  const openEntity = (kind: string, name: string) => {
    input().blur();
    rememberSearch(name);
    props().actions.openEntity(kind, name);
  };
  /** 选中一行，点它和回车落在同一处。作品开详情，有资料页的开资料页，其余按这个词搜。 */
  const choose = (entry: SearchOption) => {
    if (entry.type === 'suggest' && entry.item.followMedia && entry.item.id && props().actions.openFollow) {
      input().blur();
      props().actions.openFollow!(entry.item.id, entry.item.followMedia, entry.item.mediaIndex ?? null);
      return;
    }
    if (entry.type === 'suggest' && entry.item.href) { openPath(entry.item.href); return }
    if (entry.type === 'more') { runSearch(false, true); input().blur(); return }
    if (entry.type === 'suggest' && entry.kind === 'asset' && entry.item.id != null) {
      input().blur();
      props().actions.openItem(entry.item.id);
      return;
    }
    if (entry.type === 'suggest' && SUGGEST_PROFILE_KINDS.has(entry.kind)) { openEntity(entry.kind, entry.value); return }
    input().value = entry.value;
    runSearch(false, true);
  };
  const openPath = (path: string) => { close(); input().blur(); props().actions.openPath?.(path) };
  const pick = (entry: SearchOption) => { close(); choose(entry) };
  const peek = (card: SuggestCard) => { close(); input().blur(); props().actions.openItem(card.id) };
  const remove = async (value: string) => {
    await apiSend('/api/search-history', { operation: 'remove', query: value }).catch(() => null);
    writeHistory(readHistory().filter((row) => fold(row) !== fold(value)));
    // 行没了，键盘选中的下标就指不回同一项，归零重来。推荐不重洗：删一条记录却换了一批推荐，看着像列表自己跳了。
    s.active = -1;
    bump();
  };

  const key = (event: KeyboardEvent) => {
    if (event.isComposing) return;
    const field = input();
    if (event.key === 'Escape') {
      if (isOpen()) { close(); event.preventDefault(); return }
      if (field.value) {
        props().helpers.clearField(field);
        s.active = -1;
        event.preventDefault();
        refresh();
        return;
      }
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (move(event.key === 'ArrowDown' ? 1 : -1)) event.preventDefault();
      return;
    }
    if (event.key !== 'Enter') return;
    event.preventDefault();
    const picked = options()[s.active];
    close();
    // 选中某一项时用它原样搜索；没选中才回退到「空输入按 Enter 用推荐词」。
    if (picked) {
      choose(picked);
      if (picked.type !== 'suggest' || picked.kind !== 'asset') field.blur();
      return;
    }
    runSearch(true, true);
    field.blur();
  };
  const onInput = (event: InputEvent) => {
    if (event.isComposing) return;
    props().helpers.typed(input());
    refresh();
  };
  const focus = () => {
    void loadRecommendations();
    void Promise.all([loadHistory(), loadPool()]).then(() => {
      if (!focused()) return;
      // 带着 `?q=` 进来再点回输入框时，框里已经有词，补全该跟着这个词给。这两份数据回来之前
      // 人可能已经敲完字、那个词的补全也问过了，同一个词不再问第二遍。
      paint();
      if (input().value.trim() !== s.asked) refresh();
    });
  };
  return { input: onInput, key, focus, close, pick, peek, remove, pickKind, openPath };
}

function Tabs({ tabs, kind, onPick, wire }: {
  tabs: SuggestTab[]; kind: string; onPick(kind: string): void; wire(row: Element | null): void;
}) {
  const row = useRef<HTMLDivElement>(null);
  // 窄屏页签排不下时右缘渐隐，看得出还能往右拨。
  useLayoutEffect(() => { wire(row.current) }, [wire, tabs]);
  const tab = (k: string, label: string, total?: number) => (
    <button key={k || 'all'} type="button" role="tab" data-suggest-kind={k} aria-selected={kind === k}
      // 页签选择保留输入焦点，便于继续输入与使用方向键。
      onMouseDown={(event) => event.preventDefault()} onClick={() => onPick(k)}>
      {label}{total ? <TabCount value={total} /> : null}
    </button>
  );
  return (
    <div ref={row} data-search-tabs="" role="tablist" aria-label="按种类看补全">
      {tab('', '全部')}
      {tabs.map((t) => tab(t.kind, t.label, t.total))}
    </div>
  );
}

const keep = (event: MouseEvent) => event.preventDefault();

function Row({ entry, active, helpers, onPick, onPeek, onRemove }: {
  entry: SearchOption; active: boolean; helpers: SearchProps['helpers'];
  onPick(entry: SearchOption): void; onPeek(card: SuggestCard): void; onRemove(value: string): Promise<void>;
}) {
  const shared = {
    'data-search-value': entry.value, 'data-active': active ? '' : undefined,
    onClick: (event: MouseEvent) => {
      if ((event.target as Element).closest('[data-remove-history]')) return;
      onPick(entry);
    },
  };
  if (entry.type === 'more') {
    return (
      <button type="button" data-search-option="more" {...shared} onMouseDown={keep}>
        查看全部 {entry.total.toLocaleString()} 个视频
        <Sprite name="chevron-right" />
      </button>
    );
  }
  if (entry.type !== 'suggest') {
    return (
      <div data-search-option="" {...shared}>
        <Sprite name={entry.type === 'history' ? 'history' : 'sparkles'} />
        <span>{entry.value}</span>
        {entry.type === 'history' ? (
          /* 按下就 preventDefault，不让删除键把焦点从输入框抢走：抢走会触发 `#q` 的失焦，那个处理器
             140ms 后收起整个下拉栏，于是「删一条记录」实际等于「关掉整个下拉栏」。 */
          <button type="button" data-remove-history={entry.value} aria-label={`删除历史 ${entry.value}`}
            onMouseDown={keep} onClick={(event) => { event.stopPropagation(); void onRemove(entry.value) }}>
            <Sprite name="x" />
          </button>
        ) : null}
      </div>
    );
  }
  const { item, kind } = entry;
  const opens = SUGGEST_PROFILE_KINDS.has(kind) ? kind : undefined;
  if (['asset', 'jav', 'post', 'photo', 'release'].includes(kind)) {
    /* 作品点开是详情，不是一个搜索词：整句标题填回搜索框，下一次搜索会因为其中任何一个字符对不上
       而落空。 */
    const byline = [item.who, item.code ? item.title : ''].filter(Boolean).join(' · ');
    return (
      <button type="button" data-search-option="work" data-open-item={kind === 'asset' ? item.id : undefined} {...shared} onMouseDown={keep}>
        <Cover card={item.card} helpers={helpers} />
        <span data-search-meta=""><span data-search-name=""><span>{item.value}</span></span><span data-search-sub="">{byline}</span></span>
      </button>
    );
  }
  const matched = item.matched ? <span data-search-matched="">{item.matched}</span> : null;
  if (kind === 'performer' || kind === 'creator' || kind === 'author') {
    // 一行里摆得下几部近作就摆几部；窄下拉整排收起，数据照给。
    const works = item.works ?? [];
    const sub = item.subtitle || [`${(item.n ?? 0).toLocaleString()} 个视频`, item.agency].filter(Boolean).join(' · ');
    return (
      <div data-search-option="person" data-open-entity={opens} {...shared} onMouseDown={keep}>
        <Face item={item} kind={kind} />
        <button type="button" data-search-person="" data-search-meta="" aria-label={`查看 ${item.value}`}>
          <span data-search-name=""><span>{item.value}</span>{matched}</span>
          <span data-search-sub="">{sub}</span>
        </button>
        {works.length ? (
          <span data-search-peeks="">
            {works.map((card) => (
              <button key={card.id} type="button" data-open-work={card.id} aria-label={`打开 ${card.code || item.value}`}
                onMouseDown={keep} onClick={(event) => { event.stopPropagation(); onPeek(card) }}>
                <Cover card={card} helpers={helpers} />
              </button>
            ))}
          </span>
        ) : null}
      </div>
    );
  }
  return (
    <div data-search-option="" data-open-entity={opens} {...shared}>
      {kind === 'studio' || kind === 'agency' ? <Face item={item} kind={kind} /> : null}
      <span>{item.value}</span>
      {matched}
      {item.n ? <span data-search-n="">{item.n.toLocaleString()}</span> : null}
    </div>
  );
}

/** 小图和卡片同一套取景：卡片那一格（`.pic`）连同里面那张图都归遗留层，这里只定它有多宽。 */
function Cover({ card, helpers }: { card: SuggestCard | null | undefined; helpers: SearchProps['helpers'] }) {
  return <span data-search-pic="" dangerouslySetInnerHTML={{ __html: helpers.coverHtml(card) }} />;
}

/** 门面一律圆片，走索引页同一条兜底链：人是实体图 → 代表作头像，厂牌是标识，事务所是官网
 *  站点圆标；都取不到就是首字母。一屏几十个，取派生件。 */
function Face({ item, kind }: { item: SuggestItem; kind: string }) {
  if (item.avatarHtml) return <span data-search-face="" dangerouslySetInnerHTML={{ __html: item.avatarHtml }} />;
  const ref = { id: item.entity_id as number | null | undefined, has_image: item.has_image as boolean | undefined,
    image_version: item.image_version as string | undefined, avatar_focus: item.avatar_focus,
    avatar_stand_in: item.avatar_stand_in as boolean | undefined };
  const html = avatarInner(item.value, ref, (item.rep as number | null | undefined) || null, kind,
    (item.mark as number | null | undefined) || null, item.has_logo ? item.value : '', 'icon', undefined, true);
  return <span data-search-face="" data-kind={kind} dangerouslySetInnerHTML={{ __html: html }} />;
}
