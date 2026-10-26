/* 首页上方那一整块：两排头像（女优、厂牌），加一块两排的玻璃浮层（上排四枚视图与标签，下排读数、
 * 换一批、卡片版式与排序键），再加正文里网格上面那条交集条。
 *
 * 状态全在壳里：筛选、当前视图、排序、成员与读数都当 props 递进来，点下去的动作回壳，壳在发请求之前
 * 先推一份新的按下态，所以 `aria-pressed` 与滑动玻璃当场就到位，不等这一趟取数。
 *
 * 加一条筛选不重画这几排：成员（`tiers.key` 与标签的顺序）不变，React 只改按下态，人横着续到第
 * 六十枚、停在第 600 像素上的那一排原样留着。换了口径或换了一批，壳给新的 `tiers.key`，两排从头摆。
 * 详情浮窗打开期间壳不推任何东西，关掉回来还是同一批节点（`buildBars` 那段注释讲了为什么）。 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { fitSkeleton, popBadges, popCount } from '@peach/legacy/ui';

import { FilterGlassRows, FilterPill } from '../components/filter-glass';
import { useViewGlide } from '../components/use-view-glide';
import { LibraryShortcuts } from '../library-shortcuts/shortcut-controls';
import { FolderBrowseToggle } from '../catalog-folders/catalog-folders-page';
import { useCatalogView } from '../catalog-grid/catalog-view';
import { CatalogViewToggle } from '../catalog-grid/catalog-view-toggle';
import { Combo, Segments, Shuffle, SortKeys } from '../entity-filter/entity-filter-page';
import {
  EMPTY_SLOTS, ROW_AHEAD, ROW_BATCH, ROW_FIRST, type CatalogFilterProps, type CatalogTag, type TierKind,
  type TierPerformer, type TierStudio,
} from './catalog-filter';

/* 上一次写出去的读数。读数那一格每次取数都换成微光再建回来，靠它分清「换了个数」和「刚出现」：
   只有前者按位错峰长出来。跨查询、跨重挂都要记得，所以放在模块里。 */
let lastReadout = '';

export function CatalogFilterPage(props: CatalogFilterProps) {
  const loading = !props.tiers;
  const combo = useComboHost(props.comboHost);
  /* 收起时（资料页、索引页与管理页）只留两排头像：回到同一口径的首页时那一批原样还在，不重摆、不重取图。
     浮层那两排和资料页、索引页的浮层是同一组组件、同一组属性，藏着一份在页面上，就是两套同名的控件。 */
  return (
    <div data-catalog-root="" data-loading={loading || undefined} data-refreshing={props.refreshing || undefined}
      className="contents">
      {combo ? createPortal(<Combo items={props.combo} hidden={props.comboHidden} actions={props.actions} />, combo) : null}
      <Tiers {...props} />
      {props.offscreen ? null : <Glass {...props} />}
    </div>
  );
}

/** 玻璃浮层两排：上排四枚视图与标签，下排读数、换一批、版式与排序键。 */
function Glass(props: CatalogFilterProps) {
  const { actions, helpers } = props;
  const pane = useRef<HTMLSpanElement>(null);
  const viewRow = useRef<HTMLDivElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const tagRow = useRef<HTMLDivElement>(null);
  const bottomRow = useRef<HTMLDivElement>(null);
  const [singleRow, setSingleRow] = useState(false);
  const glide = useViewGlide(pane, viewRow, '[data-catalog-view][aria-pressed="true"]', props.state);

  /* 标签那一格是 `overflow-x:auto` 加隐藏滚动条，鼠标够不着，所以登记全站横向行那套拖动加滚轮。
     窄屏下滚的是外面那层（视图跟着一起走），两层都登记；下排窄屏下整条横滚，同一套。 */
  useEffect(() => {
    helpers.wireDrag(tagRow.current);
    helpers.wireScroller(scroll.current);
    helpers.wireDrag(bottomRow.current);
  }, [helpers]);

  const loading = !props.tiers;
  const tags = usePaged(tagRow, props.tags, props.tiers?.key ?? '', props.tagFirst);
  useLayoutEffect(() => {
    const frame = scroll.current?.closest<HTMLElement>('[data-catalog-frame]');
    if (!frame) return;
    const width = (element: HTMLElement | null) => {
      if (!element) return 0;
      const style = getComputedStyle(element);
      return [...element.children].reduce((total, child) => total + child.getBoundingClientRect().width, 0)
        + Math.max(0, element.children.length - 1) * (parseFloat(style.columnGap) || 0);
    };
    const measure = () => setSingleRow(frame.clientWidth >= width(viewRow.current) + width(tagRow.current)
      + width(bottomRow.current) + 64);
    const observer = new ResizeObserver(measure);
    observer.observe(frame);
    measure();
    return () => observer.disconnect();
  }, [tags, props.count, props.layout, props.sorts]);
  /* 计数徽标变了值的那几枚弹一下（遗留层 `popBadges`，按上一次的值判断，不按节点新不新）。 */
  useLayoutEffect(() => { popBadges(tagRow.current, 'tagbar') }, [tags]);

  return (
    <FilterGlassRows label="筛选与排序" topLabel="视图与标签"
      attrs={{ 'data-filter-frame': '', 'data-catalog-frame': '', ...(singleRow ? { 'data-single-row': '' } : {}) }}
      className="mx-4" topClassName="gap-1.5 overflow-visible" bottomClassName={props.trash ? 'hidden' : 'min-h-12'}
      bottomRef={bottomRow} busy={props.count === null || props.refreshing}
      panes={<span ref={pane} data-view-glide="" aria-hidden="true" />}
      top={
        <div ref={scroll} data-catalog-scroll="">
          <div ref={viewRow} role="group" aria-label="视图" data-catalog-views="" onPointerLeave={glide.leave}>
            {props.views.map((view) => (
              <a key={view.k} href={view.href} data-catalog-view={view.k} data-entity-press=""
                aria-pressed={props.state === view.k} onPointerEnter={glide.hover}
                onClick={(event) => { event.preventDefault(); actions.setView(view.k) }}>
                {view.label}
              </a>
            ))}
            <span data-entity-sep="" aria-hidden="true" />
          </div>
          <div ref={tagRow} data-catalog-tags="">
            {props.empty || loading ? <Placeholders kind="tag" /> : null}
            {tags.map((tag) => <TagPill key={tag.k} tag={tag} onPress={actions.toggleTag} />)}
          </div>
        </div>
      }
      bottom={props.trash ? null : <Head {...props} />}
    />
  );
}

function TagPill({ tag, onPress }: { tag: CatalogTag; onPress(tag: string): void }) {
  return (
    <FilterPill data-catalog-tag={tag.k} data-entity-press="" pressed={tag.selected} onPress={() => onPress(tag.k)}>
      {tag.label}{tag.n == null ? null : <span data-count-badge={tag.k}>{tag.n.toLocaleString()}</span>}
    </FilterPill>
  );
}

/** 两排头像。空的一排仍占 28px，画出来就是一条什么都没有的空带，所以没人就不画那一排。 */
function Tiers(props: CatalogFilterProps) {
  const { actions, helpers } = props;
  const tiers = props.tiers ?? { key: '', performers: [], studios: [] };
  const empty = props.empty || !props.tiers;
  const performers = useRef<HTMLDivElement>(null);
  const studios = useRef<HTMLDivElement>(null);
  const more = useCallback((kind: TierKind) => () => actions.moreTops(kind), [actions]);
  /* 占位那几排没有下一页可要。 */
  const shownPerformers = usePaged(performers, tiers.performers, tiers.key, ROW_FIRST,
    empty ? undefined : more('performers'));
  const shownStudios = usePaged(studios, tiers.studios, tiers.key, ROW_FIRST, empty ? undefined : more('studios'));
  const hasPerformers = empty || tiers.performers.length > 0;
  const hasStudios = empty || tiers.studios.length > 0;
  useEffect(() => {
    helpers.wireDrag(performers.current);
    helpers.wireDrag(studios.current);
  }, [helpers, hasPerformers, hasStudios]);
  return (
    <div data-catalog-tiers="" hidden={!hasPerformers && !hasStudios}>
      {hasPerformers ? (
        <div ref={performers} data-catalog-tier="performers">
          {empty ? <Placeholders kind="performer" /> : (shownPerformers as TierPerformer[]).map((person) => (
            <button key={person.name} type="button" data-tier-performer="" data-entity-kind="performer"
              data-entity-name={person.name} onClick={() => actions.openEntity('performer', person.name)}>
              <span data-tier-ring="" dangerouslySetInnerHTML={{ __html: person.ringHtml }} />
              <span data-tier-name="">{person.name}</span>
            </button>
          ))}
        </div>
      ) : null}
      {hasStudios ? (
        <div ref={studios} data-catalog-tier="studios">
          {empty ? <Placeholders kind="studio" /> : (shownStudios as TierStudio[]).map((studio) => (
            <button key={studio.name} type="button" data-tier-studio="" data-entity-kind="studio"
              data-entity-name={studio.name} onClick={() => actions.openEntity('studio', studio.name)}>
              <StudioMark studio={studio} />{studio.name}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** 厂牌标识。兜底只剩「装了但读不出来」这一种：文件坏了，或归一漏掉、图小到看不出是什么；
 *  没装标识的壳根本不给地址，不出这张图。 */
function StudioMark({ studio }: { studio: TierStudio }) {
  const [broken, setBroken] = useState(false);
  return (
    <span data-tier-mark="">
      {studio.logo && !broken ? (
        <img src={studio.logo} alt="" onError={() => setBroken(true)}
          onLoad={(event) => { if (event.currentTarget.naturalWidth < 32) setBroken(true) }} />
      ) : studio.fallback}
    </span>
  );
}

/** 空馆藏的占位：每排六十四格，由容器裁到可用宽度。它不是控件，不接指针。 */
function Placeholders({ kind }: { kind: 'performer' | 'studio' | 'tag' }) {
  const slots = Array.from({ length: EMPTY_SLOTS }, (_, index) => index);
  if (kind === 'tag') {
    return <span data-catalog-placeholder="tag" aria-hidden="true">{slots.map((i) => <span key={i} />)}</span>;
  }
  return (
    <>
      {slots.map((i) => (kind === 'performer' ? (
        <span key={i} data-catalog-placeholder="performer" aria-hidden="true"><span /><span>&nbsp;</span></span>
      ) : (
        <span key={i} data-catalog-placeholder="studio" aria-hidden="true"><span /><span>&nbsp;</span></span>
      )))}
    </>
  );
}

/** 下排：读数、换一批、版式与排序。回收站不出这一排。 */
function Head({ count, refreshing, layout, sorts, actions }: CatalogFilterProps) {
  const view = useCatalogView();
  const [shuffling, setShuffling] = useState(false);
  const shuffle = () => {
    setShuffling(true);
    actions.reshuffle().finally(() => setShuffling(false));
  };
  return (
    <>
      <span data-catalog-summary=""><Readout count={count} /><LibraryShortcuts currentHref={actions.currentHref} /></span>
      <span data-entity-sorts="">
        <Shuffle onPress={shuffle} busy={shuffling || refreshing} />
        {/* 按名字换键：JAV 与首页各一组，React Aria 的单选组挂上之后不改 `name`。 */}
        {layout && view === 'cards' ? <Segments key={layout.name} name={layout.name} label={layout.label} value={layout.value} options={layout.options}
          onChange={actions.setLayout} /> : null}
        <CatalogViewToggle />
        <FolderBrowseToggle />
        {view === 'cards' ? <SortKeys sorts={sorts} onSort={actions.setSort} /> : null}
      </span>
    </>
  );
}


/** 读数：`1,234 个符合 · 显示 24`。等这一趟取数时换成一条宽度定死的微光，180ms 内就到手的一帧都不露。 */
function Readout({ count }: { count: CatalogFilterProps['count'] }) {
  const readout = useRef<HTMLSpanElement>(null);
  const skeleton = useRef<HTMLSpanElement>(null);
  const text = count ? `${count.total.toLocaleString()} 个符合 · 显示 ${count.shown.toLocaleString()}` : '';
  useLayoutEffect(() => {
    if (count === false) return;
    if (!count) { fitSkeleton(skeleton.current); return }
    const el = readout.current;
    if (!el) return;
    if (lastReadout && el.dataset.popCount === undefined) el.dataset.popCount = lastReadout;
    lastReadout = text;
    popCount(el, text);
  }, [count, text]);
  if (count === false) return <span key="failed" data-catalog-readout="" />;
  return count
    ? <span key="readout" ref={readout} data-catalog-readout="" data-count-readout="" role="status" />
    : (
      <span key="skeleton" data-catalog-readout="">
        <span ref={skeleton} data-skeleton="count" aria-hidden
          className="relative inline-block h-3.5 w-37.5 rounded-md align-middle skeleton-sheen" />
      </span>
    );
}

/** 交集条的宿主：`#combo` 里另建一层 `.peach-react`，样式作用域要它。卸载时撤掉，行本身还给壳。 */
function useComboHost(row: Element | null): HTMLElement | null {
  const [host, setHost] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (!row) return undefined;
    const el = row.ownerDocument.createElement('div');
    el.className = 'peach-react';
    row.replaceChildren(el);
    setHost(el);
    return () => { el.remove(); setHost(null) };
  }, [row]);
  return host;
}

interface Page<T> { key: string; limit: number; fetched: T[]; drained: boolean }

/** 一排先摆 `first` 枚，横滚到离右端 320px 就再续 12 枚，续到这一排真的溢出为止：宽屏上一批可能
 *  还填不满一行，而没溢出就滚不动，滚不动就再没有第二次 `scroll` 来接着续。手上这份用完再经
 *  `more` 要下一页，要回空的就是到底了。`key` 变了从头摆。 */
function usePaged<T>(row: RefObject<HTMLElement | null>, items: T[], key: string, first: number,
  more?: () => Promise<unknown[]>): T[] {
  const initial = (): Page<T> => ({ key, limit: first, fetched: [], drained: !more });
  const [page, setPage] = useState(initial);
  const current = page.key === key ? page : initial();
  if (current !== page) setPage(current);
  const all = useMemo(() => (current.fetched.length ? items.concat(current.fetched) : items),
    [items, current.fetched]);
  const fetching = useRef<string | null>(null);
  const state = useRef({ current, all, more });
  state.current = { current, all, more };
  const fill = useCallback(() => {
    const el = row.current;
    const { current: now, all: list, more: next } = state.current;
    if (!el || !el.clientWidth || el.scrollLeft + el.clientWidth < el.scrollWidth - ROW_AHEAD) return;
    if (now.limit < list.length) {
      setPage((p) => (p.key === now.key ? { ...p, limit: p.limit + ROW_BATCH } : p));
      return;
    }
    if (now.drained || !next || fetching.current === now.key) return;
    fetching.current = now.key;
    /* 要下一页的这段时间里人还在滚，`fetching` 挡住同一份名单重入，免得同一页要两遍。按 `key` 记：
     * 旧名单那一页还在路上时名单换了，新名单照样续页，不然旧请求回来被丢掉后这一排就停在第一页。 */
    void next().catch(() => []).then((rows) => {
      if (fetching.current === now.key) fetching.current = null;
      setPage((p) => {
        if (p.key !== now.key) return p;
        return rows.length ? { ...p, fetched: p.fetched.concat(rows as T[]) } : { ...p, drained: true };
      });
    });
  }, [row]);
  useLayoutEffect(fill);
  useEffect(() => {
    const el = row.current;
    el?.addEventListener('scroll', fill, { passive: true });
    return () => el?.removeEventListener('scroll', fill);
  });
  return all.slice(0, current.limit);
}
