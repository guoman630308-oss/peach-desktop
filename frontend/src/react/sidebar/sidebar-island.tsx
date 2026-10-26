/* 侧栏（ADR-0031 第 11f 步）：左侧抽屉里滚动的那一层。
 *
 * 常驻组件 `sidebar`（`router/managed-routes.tsx` 的常驻表）：路由树把它画进壳常驻的 `#drawerScroll`，宿主就是
 * 那个节点本身（覆盖式滚动条挂在它上面，轨道住在 `#drawer` 里）。壳启动时先同步在宿主里写一份同结构的导航
 * 骨架（`sidebarSkeletonHtml`）；打开这一面时 `place` 清掉骨架，路由树在同一个任务里画出首帧（只有导航），
 * 壳紧接着经 `attached` 把品牌与开合键挪进标题行，再拿到句柄、把手上那份 props 推进来。
 *
 * 之后壳只经 `configureSidebar` 给的句柄说话：`render(props)` 与 `navChanged()` 写本模块的 store 再
 * `flushSync` 通知，返回时已经画好——`navChanged` 跑在壳 `route()` 的同步段里，玻璃拿到的是旧位置到新位置。
 * 换页、换筛选、换语境都落在同一个组件上，导航那一列和它上面的玻璃从头到尾是同一批节点——换了节点，
 * 玻璃的位移就从头起跑，看到的只是当前项换了个地方亮起来。
 *
 * 玻璃画在 `#drawer` 上（portal），不在滚动层里：它要跟着那一列纵滚，又不能被滚动层切掉回弹的那一截。
 * 位移与抻长交给 `useViewGlide` 的纵轴版，同筛选条那几排是同一种动法。
 *
 * 标题行 `[data-sidebar-head]` 画成一个没有子节点的空槽：品牌与开合键是壳的节点，由壳挪进来，组件不往这个
 * 槽里画任何东西，重画也就不碰它们。
 *
 * 分组的开合走共用 Collapse 的 `setCollapseOpen`（同设置页的 `Disclosure`）：原生 `details` 给键盘与
 * 无障碍语义，`open` 不写成受控属性，收起的那段过渡里它才不会被重新画成展开。
 *
 * 宿主不包 `.peach-react`：这一列一直在 Preflight 之外，按钮与字号继承的是遗留层的全局规则，样式全在
 * `sidebar.css`，不用工具类。 */
import {
  createContext, useContext, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore,
  type DragEvent, type MouseEvent, type ReactNode,
} from 'react';
import { createPortal, flushSync } from 'react-dom';

import { growCollapse, popBadges, setCollapseOpen } from '@peach/legacy/ui';

import { useViewGlide } from '../components/use-view-glide';
import { Icon } from '../settings-panel/icon';
import type {
  SidebarApi, SidebarChip, SidebarContent, SidebarDot, SidebarFacets, SidebarHost, SidebarProps,
} from './sidebar-api';
import { moveSidebarKey, useCommitSidebarOrder } from './sidebar-order';

/* 首页那一项的键是空串；空串写进 dataTransfer 等于没写，给它一个占位。 */
const HOME = '__home__';
/* 「展开全部」之前每组先露多少条。 */
const LIMITS = { creator: 26, tag: 30, tech: 16 } as const;
/* 时长拉条的量程（分钟）：右端拉到头是「不限」。 */
const DURATION_MAX = 180;

let host: SidebarHost | null = null;
/* store 里的一份：壳推来的 props 与导航按下态的代次（壳说「重读一遍」时加一，玻璃按新的按下项落位）。
   每次写入换一个新对象，订阅者按引用判断变没变。 */
let shown: { props: SidebarProps; epoch: number } = { props: { content: null, filters: {}, latest: null }, epoch: 0 };
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener) };
}

function notify(): void {
  for (const listener of [...listeners]) listener();
}

/* 这一面抛错、被错误边界卸掉之后没人订阅，推进来的只写进 store，不画、不抛。 */
const api: SidebarApi = {
  render(next) { shown = { ...shown, props: next }; flushSync(notify) },
  navChanged() { shown = { ...shown, epoch: shown.epoch + 1 }; flushSync(notify) },
};

/** 接上壳给的宿主，拿回侧栏的命令式入口。只调一次；壳的 `loadSidebar` 接着在路由树里打开这一面，首帧只画
 *  导航，画上之后调 `attached`、交出句柄，壳随即把手上那份 props 推进来。 */
export function configureSidebar(next: SidebarHost): SidebarApi {
  host = next;
  shown = { ...shown, props: { content: null, filters: {}, latest: null } };
  return api;
}

/** 常驻表里的那一面：读本模块的 store，宿主还没接上时不画。 */
export function SidebarSurface() {
  const current = useSyncExternalStore(subscribe, () => shown);
  return host ? <Sidebar host={host} props={current.props} epoch={current.epoch} /> : null;
}

/* ── 整列 ── */

function Sidebar({ host: at, props: view, epoch }: { host: SidebarHost; props: SidebarProps; epoch: number }) {
  const version = useSyncExternalStore(at.store.subscribe, at.store.version);
  return (
    <>
      <div data-sidebar-head="" />
      <Navigation host={at} epoch={epoch} version={version} />
      {view.content ? <Groups host={at} content={view.content} filters={view.filters} latest={view.latest} /> : null}
    </>
  );
}

/* ── 导航 ── */

function Navigation({ host: at, epoch, version }: { host: SidebarHost; epoch: number; version: number }) {
  const commit = useCommitSidebarOrder(at.store);
  const order = at.store.value.sidebarOrder;
  const byKey = new Map(at.navCatalog.map((item) => [item[0], item]));
  const items = order.map((key) => byKey.get(key)).filter((item) => item !== undefined);
  const pressed = items.map(([key]) => at.navOn(key));
  const column = useRef<HTMLDivElement | null>(null);
  const pane = useRef<HTMLSpanElement | null>(null);
  const glideKey = `${epoch}:${version}:${items.map(([key], index) => pressed[index] ? key || HOME : '').join(',')}`;
  const glide = useViewGlide(pane, column, '[data-nav][aria-pressed="true"]', glideKey, { axis: 'y' });
  const { sync } = glide;

  /* 抽屉开合（桌面上宽度在 60 与 260 之间过渡）、滚动层改尺寸、标题行里品牌挪进来，都只重量、
     不动画；开合之后悬停的那一格不算数，回到当前项。 */
  useEffect(() => {
    const scroll = at.scroll, drawer = scroll.parentElement;
    const head = scroll.querySelector<HTMLElement>(':scope > [data-sidebar-head]');
    const resize = new ResizeObserver(() => sync());
    [drawer, scroll, head].forEach((node) => { if (node) resize.observe(node) });
    document.addEventListener('board:sidebar', sync);
    return () => { resize.disconnect(); document.removeEventListener('board:sidebar', sync) };
  }, [at, sync]);

  const [dragging, setDragging] = useState<string | null>(null);
  const [drop, setDrop] = useState<{ key: string; after: boolean } | null>(null);
  const clearDrag = () => { setDragging(null); setDrop(null) };
  const drag = (key: string) => ({
    draggable: true,
    onDragStart: (event: DragEvent<HTMLButtonElement>) => {
      setDragging(key);
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', key || HOME);
    },
    onDragOver: (event: DragEvent<HTMLButtonElement>) => {
      if (dragging === null || dragging === key) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      const after = event.clientY > event.currentTarget.getBoundingClientRect().top + event.currentTarget.offsetHeight / 2;
      if (drop?.key !== key || drop.after !== after) setDrop({ key, after });
    },
    onDrop: (event: DragEvent<HTMLButtonElement>) => {
      event.preventDefault();
      const from = dragging, after = drop?.key === key ? drop.after : false;
      clearDrag();
      const next = from === null ? null : moveSidebarKey(order, from, key, after);
      if (next) commit(next);
    },
    onDragEnd: clearDrag,
  });

  const drawer = at.scroll.parentElement;
  return (
    <>
      <div data-sidebar-nav="" ref={column} onPointerLeave={glide.leave}>
        {items.map(([key, label, glyph], index) => (
          <button type="button" key={key || HOME} data-nav={key} aria-pressed={pressed[index]} aria-label={label}
            data-dragging={dragging === key ? '' : undefined}
            data-drop={drop?.key === key ? (drop.after ? 'after' : 'before') : undefined}
            onClick={() => at.navTo(key)} onPointerEnter={glide.hover} {...drag(key)}>
            {key === '' ? <img data-sidebar-home-logo="" src="/peach-logo.png" alt="" /> : <Icon name={glyph} />}
            <span>{label}</span>
          </button>
        ))}
      </div>
      {drawer ? createPortal(<span data-sidebar-glide="" aria-hidden="true" ref={pane} />, drawer) : null}
    </>
  );
}

/* ── 筛选分组 ── */

interface GroupSpec {
  title: string;
  kind: string;
  body: ReactNode;
  active: boolean;
}

const selectedValues = (filters: SidebarProps['filters'], key: string) =>
  String(filters[key] ?? '').split(',').filter(Boolean);

function countsOf(latest: SidebarFacets | null): Map<string, number> | null {
  if (!latest) return null;
  const counts = new Map<string, number>();
  const rows: [string, SidebarChip[]][] = [['loc', latest.locations], ['orient', latest.orientations],
    ['region', latest.regions], ['creator', latest.creators], ['tag', latest.tags], ['tag', latest.tech]];
  for (const [key, chips] of rows) for (const chip of chips) counts.set(`${key}\n${chip.value}`, chip.n ?? 0);
  return counts;
}

function Groups({ host: at, content, filters, latest }: {
  host: SidebarHost; content: SidebarContent; filters: SidebarProps['filters']; latest: SidebarFacets | null;
}) {
  const key = content.kind === 'catalog' ? content.key
    : `follow:${content.selected.join(',')}:${content.tags.map((row) => row.value).join(',')}`
      + `:${(content.providers || []).map((row) => row.value).join(',')}:${filters.provider ?? ''}`
      + `:${content.duration ? 1 : 0}:${filters.dur_min ?? ''}:${filters.dur_max ?? ''}`;
  /* 计数徽标弹不弹由 `popBadges` 按上一次见到的值判断，不按节点是不是新建的判断：每来一份新聚合
     问一次，换一条筛选时整列计数才不会一起弹。 */
  useLayoutEffect(() => { if (content.kind === 'catalog') popBadges(at.scroll, 'drawer') }, [at, key, content.kind]);
  const groups = content.kind === 'catalog'
    ? catalogGroups(at, content.facets, filters, latest, key)
    : followGroups(at, content, filters);
  let index = 0;
  return groups.map((group) => group && (
    <Group key={group.title} title={group.title} kind={group.kind} active={group.active} index={index++} epoch={key}>
      {group.body}
    </Group>
  ));
}

function catalogGroups(
  at: SidebarHost, facets: SidebarFacets, filters: SidebarProps['filters'], latest: SidebarFacets | null, epoch: string,
): (GroupSpec | null)[] {
  const counts = countsOf(latest);
  const chips = (rows: SidebarChip[], key: string, multi: boolean): GroupSpec['body'] | null => {
    if (!rows.length) return null;
    const selected = selectedValues(filters, key);
    return <ChipList rows={rows} filterKey={key} multi={multi} selected={selected} counts={counts} host={at} />;
  };
  const spec = (title: string, kind: string, rows: SidebarChip[], key: string, multi: boolean, more?: keyof typeof LIMITS) => {
    if (!rows.length) return null;
    const selected = selectedValues(filters, key);
    const active = rows.some((row) => selected.includes(row.value));
    if (!more) return { title, kind, active, body: chips(rows, key, multi) };
    const source = more === 'creator' ? latest?.creators : more === 'tag' ? latest?.tags : latest?.tech;
    return {
      title, kind, active,
      body: <MoreList epoch={epoch} title={title} rows={rows} latest={source ?? null} limit={LIMITS[more]} filterKey={key}
        selected={selected} counts={counts} host={at} expandable={more !== 'tech'} />,
    };
  };
  return [
    spec('来源', 'src', facets.locations, 'loc', true),
    facets.duration
      ? { title: '时长', kind: 'meta', active: false, body: <Duration host={at} filters={filters} /> }
      : null,
    /* 产地紧挨着来源：两者回答的都是「这批片打哪来」，一个说存储，一个说发行体系。可多选。 */
    spec('产地', 'general', facets.regions, 'region', true),
    spec('画幅', 'meta', facets.orientations, 'orient', false),
    spec('创作者', 'artist', facets.creators, 'creator', false, 'creator'),
    spec('内容标签', 'general', facets.tags, 'tag', false, 'tag'),
    spec('影片属性', 'meta', facets.tech, 'tag', false, 'tech'),
    facets.followTags.length ? {
      title: '关注标签', kind: 'online', active: false,
      body: <FollowChips rows={facets.followTags} host={at} selected={null} badges />,
    } : null,
  ];
}

/* 关注页与关注详情：次序同目录那一列，来源、时长在前，内容标签在后。来源与时长回的是关注页的筛选，
   不是目录的 `commitContextFilter`。 */
function followGroups(
  at: SidebarHost, content: Extract<SidebarContent, { kind: 'follow' }>, filters: SidebarProps['filters'],
): (GroupSpec | null)[] {
  const providers = content.providers || [];
  const provider = selectedValues(filters, 'provider');
  const selected = new Set(content.selected);
  return [
    providers.length ? {
      title: '来源', kind: 'src', active: providers.some((row) => provider.includes(row.value)),
      body: <ChipList rows={providers} filterKey="provider" multi={false} selected={provider} counts={null} host={at}
        onToggle={(_key, value) => at.selectFollowProvider(value)} />,
    } : null,
    content.duration
      ? { title: '时长', kind: 'meta', active: false, body: <Duration host={at} filters={filters} commit={at.setFollowDuration} /> }
      : null,
    content.tags.length ? {
      title: '内容标签', kind: 'online', active: content.tags.some((row) => selected.has(row.value)),
      body: <FollowChips rows={content.tags} host={at} selected={selected} badges={false} />,
    } : null,
  ];
}

/* 一进来只有正在生效的那几组是展开的。挑两组常驻展开等于替人决定他这次要按哪个维度筛，而侧栏一屏
   就那么长，展开的部分把别的组挤到看不见的地方去。人自己开合过的组按这一次会话记住。 */
function wantOpen(title: string, active: boolean): boolean {
  let saved: string | null = null;
  try { saved = sessionStorage.getItem(`peach.sidebar.group.${title}`) } catch { /* 存储不可用就按按下态 */ }
  return saved !== null ? saved === 'open' : active;
}

function Group({ title, kind, active, index, epoch, children }: {
  title: string; kind: string; active: boolean; index: number; epoch: string; children: ReactNode;
}) {
  const details = useRef<HTMLDetailsElement | null>(null);
  const body = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(() => wantOpen(title, active));
  const mounted = useRef(false);
  /* 第一次挂上时把初始状态落到 DOM；往后每来一份新聚合，没被人开合过的组重新按按下态定。 */
  useLayoutEffect(() => {
    const node = details.current, collapse = body.current;
    if (!node || !collapse) return;
    const want = mounted.current ? wantOpen(title, active) : open;
    mounted.current = true;
    if (node.open === want) return;
    node.open = want;
    collapse.inert = !want;
    collapse.style.height = '';
    if (collapse.classList.contains('ui-fcollapse')) collapse.classList.toggle('ui-fcollapse-settled', want);
    setOpen(want);
  }, [epoch]);
  const toggle = (event?: MouseEvent) => {
    event?.preventDefault();
    if (!details.current || !body.current) return;
    const next = !open;
    setCollapseOpen(details.current, body.current, next);
    setOpen(next);
    try { sessionStorage.setItem(`peach.sidebar.group.${title}`, next ? 'open' : 'closed') } catch { /* 只是不记住 */ }
  };
  const collapseId = `sidebar-collapse-${index}`;
  return (
    <details ref={details} data-sidebar-group={title} data-sidebar-kind={kind}>
      <summary role="button" data-sidebar-toggle="" aria-expanded={open} aria-controls={collapseId} onClick={toggle}>
        <Icon name="chevron-right" /><span>{title}</span>
      </summary>
      <div ref={body} data-sidebar-collapse="" id={collapseId} inert={!open}>
        <div data-sidebar-collapse-body="">
          <div data-sidebar-body="" id={`sidebar-group-${encodeURIComponent(title)}`}>
            <GroupToggle.Provider value={toggle}>{children}</GroupToggle.Provider>
          </div>
        </div>
      </div>
    </details>
  );
}

/* 名单末尾那枚「收起」收的是整组：组的开合在 `Group` 手里，经这一层递下去。 */
const GroupToggle = createContext<() => void>(() => {});

function Dot({ dot }: { dot?: SidebarDot }) {
  if (!dot) return null;
  if (dot.kind === 'glyph') return <Icon name={dot.name} />;
  if (dot.kind === 'image') return <img data-sidebar-source-icon="" src={dot.src} alt="" />;
  return <i data-sidebar-cost={dot.cost} />;
}

function ChipList({ rows, filterKey, multi, selected, counts, host: at, onToggle = at.toggleChip }: {
  rows: SidebarChip[]; filterKey: string; multi: boolean; selected: string[];
  counts: Map<string, number> | null; host: SidebarHost;
  /** 点下去回给谁：缺省是目录筛选，关注页换成它自己的。 */
  onToggle?: SidebarHost['toggleChip'];
}) {
  return (
    <div data-sidebar-chips="">
      {rows.map((row) => {
        const n = row.n === null ? null : counts ? counts.get(`${filterKey}\n${row.value}`) ?? 0 : row.n;
        return (
          <button type="button" key={row.value} data-sidebar-chip="" aria-pressed={selected.includes(row.value)}
            data-key={filterKey} data-val={row.value} data-multi={multi ? '1' : '0'}
            data-offline={row.offline ? '' : undefined} disabled={!!row.offline} title={row.offline || row.label}
            onClick={() => onToggle(filterKey, row.value, multi)}>
            <Dot dot={row.dot} />
            <span data-sidebar-chip-label="">{row.label}</span>
            {n === null ? null : <span data-sidebar-count="" data-count-badge={`${filterKey}:${row.value}`}>{n.toLocaleString()}</span>}
          </button>
        );
      })}
    </div>
  );
}

/* 展开键接在名单末尾，它说的是「这张名单还没完」——那句话要跟名单断掉的地方在一起。身量取排名那枚
   展开药丸：一个箭头就说得完的事不必再配一句字。摊开照最新那一份聚合重排整组。 */
function MoreList({ epoch, title, rows, latest, limit, filterKey, selected, counts, host: at, expandable }: {
  epoch: string; title: string; rows: SidebarChip[]; latest: SidebarChip[] | null; limit: number; filterKey: string;
  selected: string[]; counts: Map<string, number> | null; host: SidebarHost; expandable: boolean;
}) {
  const toggleGroup = useContext(GroupToggle);
  const [shown, setShown] = useState<{ rows: SidebarChip[]; all: boolean } | null>(null);
  /* 新的一份聚合到来时名单从头摊：成员换回这一份的前几条，展开键回到「展开全部」。 */
  const [seen, setSeen] = useState(epoch);
  if (seen !== epoch) { setSeen(epoch); setShown(null) }
  const grow = useRef<{ body: HTMLElement; before: number; scroller: HTMLElement; keep: number } | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);
  const expanded = !!shown?.all;
  const list = shown ? shown.rows : rows;

  /* 这一列的位置归人自己管：摊开的内容全在按下的这个点以下，把他挪过去等于替他决定现在要看第几条。
     名单一变长，浏览器会顺着焦点和锚定把这一列推走，所以记下再放回。 */
  useLayoutEffect(() => {
    const run = grow.current;
    if (!run) return;
    grow.current = null;
    const hold = () => { run.scroller.scrollTop = run.keep };
    if (expanded) {
      const details = run.body.closest('details');
      run.body.classList.add('ui-fcollapse');
      growCollapse(run.body, run.before, () => !!details?.open && !run.body.inert);
    }
    hold();
    requestAnimationFrame(hold);
  }, [shown, expanded]);

  const more = () => {
    const node = button.current;
    const body = node?.closest<HTMLElement>('[data-sidebar-collapse]');
    const scroller = at.scroll;
    if (!node || !body) return;
    grow.current = { body, before: body.getBoundingClientRect().height, scroller, keep: scroller.scrollTop };
    const source = latest ?? rows;
    setShown({ rows: source, all: !expanded });
    /* 收起收的是整组。名单已经摊到最长，把它退回二十几条只是换一个断点，人还站在同一列读不完的东西
       前面；他按这一下要的是把这一组放回去。 */
    if (expanded) toggleGroup();
  };
  return (
    <>
      <ChipList rows={expanded ? list : list.slice(0, limit)} filterKey={filterKey} multi={false} selected={selected}
        counts={counts} host={at} />
      {expandable && rows.length > limit
        ? <button type="button" ref={button} data-sidebar-more={filterKey} aria-expanded={expanded}
            aria-label={`${expanded ? '收起' : '展开全部'}${title}`} onClick={more}><Icon name="chevron-down" /></button>
        : null}
    </>
  );
}

function FollowChips({ rows, host: at, selected, badges }: {
  rows: SidebarChip[]; host: SidebarHost; selected: Set<string> | null; badges: boolean;
}) {
  return (
    <div data-sidebar-chips="">
      {rows.map((row) => (
        <button type="button" key={row.value} data-sidebar-chip="" data-online="" data-follow-drawer-tag={row.value}
          aria-pressed={selected ? selected.has(row.value) : undefined} title={row.label}
          onClick={() => (selected ? at.selectFollowTag(row.value) : at.openFollowTag(row.value))}>
          <span data-sidebar-chip-label="">{row.label}</span>
          <span data-sidebar-count="" data-count-badge={badges ? `follow:${row.value}` : undefined}>
            {badges ? (row.n ?? 0).toLocaleString() : row.n}
          </span>
        </button>
      ))}
    </div>
  );
}

/* ── 时长 ──
   时长只有一处读数：手柄上方那枚气泡。另起一行写「不限 — 不限」是同一件事说第二遍，而且滑块不动时
   它永远是那句话。两端都常显：这里是唯一报数的地方，藏起来的话不碰滑块就看不出当前筛的是哪一段。 */

const minutesOf = (seconds: unknown, fallback: number) =>
  seconds ? Math.min(DURATION_MAX, Number(seconds) / 60) : fallback;

function Duration({ host: at, filters, commit: onCommit = at.setDuration }: {
  host: SidebarHost; filters: SidebarProps['filters'];
  /** 松手时回给谁：缺省是目录筛选，关注页换成它自己的。 */
  commit?: SidebarHost['setDuration'];
}) {
  const source = `${filters.dur_min ?? ''}:${filters.dur_max ?? ''}`;
  const [seen, setSeen] = useState(source);
  const [range, setRange] = useState(() => [minutesOf(filters.dur_min, 0), minutesOf(filters.dur_max, DURATION_MAX)]);
  /* 刚动过的那枚压在上面：两端拖到一起时它们会叠，底下那枚报的是自己停下的位置。 */
  const [active, setActive] = useState<'min' | 'max'>('max');
  if (seen !== source) {
    setSeen(source);
    setRange([minutesOf(filters.dur_min, 0), minutesOf(filters.dur_max, DURATION_MAX)]);
  }
  const [lo, hi] = range;
  const group = useRef<HTMLDivElement | null>(null);
  const minInput = useRef<HTMLInputElement | null>(null);
  const maxInput = useRef<HTMLInputElement | null>(null);
  const latest = useRef(range);
  latest.current = range;

  const move = (end: 'min' | 'max', value: number) => {
    let [nextLo, nextHi] = end === 'min' ? [value, hi] : [lo, value];
    if (nextLo > nextHi) { if (end === 'min') nextHi = nextLo; else nextLo = nextHi }
    setActive(end);
    setRange([nextLo, nextHi]);
    return [nextLo, nextHi];
  };

  /* 拖动只改读数，松手（`change`）才提交：React 的 onChange 接的是每一步 input。 */
  useEffect(() => {
    const commit = () => onCommit(latest.current[0], latest.current[1]);
    const inputs = [minInput.current, maxInput.current];
    inputs.forEach((input) => input?.addEventListener('change', commit));
    return () => inputs.forEach((input) => input?.removeEventListener('change', commit));
  }, [onCommit]);

  /* 已选那一截与两枚气泡的位置写在样式变量与 `left` 上。气泡对着手柄居中，伸出轨道的那一截按实测
     溢出量收回来：侧栏只比轨道宽出一点点，越界的半截被侧栏裁掉，读数就只剩一半。量之前先把上一次的
     位移清掉，否则量到的是已经收过一次的位置，越拖越偏。 */
  useLayoutEffect(() => {
    const node = group.current;
    if (!node) return;
    node.style.setProperty('--lo', `${lo / DURATION_MAX * 100}%`);
    node.style.setProperty('--hi', `${hi / DURATION_MAX * 100}%`);
    const bounds = node.getBoundingClientRect();
    node.querySelectorAll<HTMLElement>('[data-sidebar-range-tip]').forEach((tip) => {
      const value = tip.dataset.rangeEnd === 'max' ? hi : lo;
      tip.style.left = `${value / DURATION_MAX * 100}%`;
      tip.style.setProperty('--range-tip-shift', '0px');
      const box = tip.getBoundingClientRect();
      const shift = box.right > bounds.right ? bounds.right - box.right : box.left < bounds.left ? bounds.left - box.left : 0;
      if (shift) tip.style.setProperty('--range-tip-shift', `${Math.round(shift)}px`);
    });
  }, [lo, hi]);

  const tip = (end: 'min' | 'max', value: number) => (
    <output data-sidebar-range-tip="" data-range-end={end} data-range-active={active === end ? '' : undefined} aria-hidden="true">
      {end === 'max' && value >= DURATION_MAX ? '不限' : `${value} 分钟`}
    </output>
  );
  return (
    <div data-sidebar-duration="">
      <div data-sidebar-range="" id="durationRange" ref={group}>
        <span data-sidebar-range-base="" />
        <span data-sidebar-range-fill="" />
        <input id="durMin" type="range" min="0" max={DURATION_MAX} step="5" value={lo} aria-label="最短时长（分钟）"
          ref={minInput} onChange={(event) => move('min', Number(event.currentTarget.value))} />
        <input id="durMax" type="range" min="0" max={DURATION_MAX} step="5" value={hi} aria-label="最长时长（分钟）"
          ref={maxInput} onChange={(event) => move('max', Number(event.currentTarget.value))} />
        {tip('min', lo)}
        {tip('max', hi)}
      </div>
    </div>
  );
}
