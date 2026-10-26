/* 五张索引页：艺人、创作者、厂牌、事务所四份名册，和标签词表。
 *
 * 地址栏是唯一真相。壳从地址读出 kind／q／scope／view／category 作初值挂上来，页面换档
 * （厂牌↔事务所、本地↔在线、类型、视图、过滤词）只改自己的状态并经 `route` 写回地址，
 * 不重挂：重挂会让页头连同过滤框里正在打的字一起被换掉，Tabs 那条蓝线从头起跑。
 *
 * 页头只有标题、读数、版式切换和过滤框；页面级的切换（厂牌／事务所、本地／在线）是页头
 * 下面那排 Tabs。标签页的读数住在浮层下排，页头不再重复一遍。 */
import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query';
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { Radio } from 'react-aria-components';

import { refitNativeImages } from '@peach/card-art';
import { peachHistory, rememberHistoryPages } from '@peach/history';
import { requestErrorMessage } from '@peach/legacy/core';
import { indexSkeletonHtml } from '@peach/legacy/ui';

import { Button, ButtonLink } from '@/components/base/buttons/button';
import { InputBase, TextField } from '@/components/base/input/input';

import { IDENTITY_ROW_CLASS } from '../../identity-filter';

import { BoardTabs, type BoardTab } from '../components/board-tabs';
import { EmptyState } from '../components/empty-state';
import { LibraryShortcuts } from '../library-shortcuts/shortcut-controls';
import { LoadingDots } from '../components/loading-dots';
import { SEGMENTED_TRACK, SEGMENT_ICON, SegmentedRadioGroup as RadioGroup } from '../components/segmented';
import { spriteGlyph } from '../components/sprite-glyph';
import type { OnlineAuthor, OnlineTag } from '../follow/online-vocab';
import {
  INDEX_TITLES, IDENTITY_CATEGORIES, ONLINE_TAG_CATEGORIES, TAG_CATEGORIES, countText, flatItems, indexQuery, indexRoute, indexSource, isCompany,
  isPeople, tagGroups, type IndexKind, type IndexPage as Page, type IndexPerson, type IndexProps,
  type IndexRoute, type IndexScope, type IndexTag, type PeopleLayout,
} from './index-data';
import { OnlineAuthors, PeopleGrid } from './index-people';
import { TagAlphabet, TagCloud, TagDock, TagFilters, type TagEntry } from './index-tags';

/* 厂牌与事务所是两种实体，不是同一份数据的两种筛选：厂牌出片，事务所出人。所以这个开关切的
   是地址。场记板归厂牌，公文包归事务所，字形各说各的那一件事。 */
const MAKER_TABS: BoardTab<'studios' | 'agencies'>[] = [
  { value: 'studios', label: '厂牌', symbol: 'clapperboard' },
  { value: 'agencies', label: '事务所', symbol: 'briefcase' },
];
/* 本地与在线是两套词表：计数口径、类别划分和点开去哪儿都不同，所以也是页面级的 Tabs。 */
const SCOPE_TABS: BoardTab<IndexScope>[] = [
  { value: 'local', label: '本地', symbol: 'hard-drive' },
  { value: 'online', label: '在线', symbol: 'rss' },
];
const PEOPLE_TABS: BoardTab<'performers' | 'creators' | 'online'>[] = [
  { value: 'performers', label: '艺人', symbol: 'user-round' },
  { value: 'creators', label: '卖家', symbol: 'user-round' },
  { value: 'online', label: '在线', symbol: 'rss' },
];

/* 公司那一格摆的是标识而不是脸，沿用艺人那套词就是提示说着「竖幅头像」、屏幕上摆着方标识。
   档位仍是同一个设置值，分开的只有说法。 */
const LAYOUTS: Record<'people' | 'company', readonly (readonly [PeopleLayout, string, string])[]> = {
  people: [['big', '大图 · 竖幅头像', 'maximize'], ['compact', '紧凑 · 圆形头像', 'layout-grid']],
  company: [['big', '大图 · 完整标识', 'maximize'], ['compact', '紧凑 · 圆形标识', 'layout-grid']],
};

function LayoutSwitch(
  { kind, layout, onChange }: { kind: IndexKind; layout: PeopleLayout; onChange(layout: PeopleLayout): void },
) {
  return (
    <RadioGroup aria-label={`${INDEX_TITLES[kind]}索引版式`} orientation="horizontal" value={layout}
      onChange={(next) => onChange(next as PeopleLayout)} data-index-layout=""
      className={`flex-none max-board-narrow:order-3 ${SEGMENTED_TRACK}`}>
      {LAYOUTS[isCompany(kind) ? 'company' : 'people'].map(([value, label, symbol]) => {
        const Glyph = spriteGlyph(symbol);
        return (
          <Radio key={value} value={value} aria-label={label} className={SEGMENT_ICON}>
            <span title={label} className="contents"><Glyph className="size-4" /></span>
          </Radio>
        );
      })}
    </RadioGroup>
  );
}

const SearchGlyph = spriteGlyph('search');

/* 只读筛选不配提交按钮：停手 300ms 就查，回车是「别等了，现在就查」。中文输入法选字过程中
   一样发 input，拿还没定型的拼音去筛，筛的是「zhon」这种半截输入；组完字由 compositionend
   接手，组字过程中的回车是在定字，也放过去。 */
function IndexSearch({ label, value, onQuery }: { label: string; value: string; onQuery(q: string): void }) {
  const [text, setText] = useState(value);
  const composing = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const later = (next: string) => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => onQuery(next.trim()), 300);
  };
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== 'Enter' || composing.current || event.nativeEvent.isComposing) return;
    event.preventDefault();
    clearTimeout(timer.current);
    onQuery(text.trim());
  };
  return (
    <div data-index-search="" className="ml-auto min-w-35 max-w-80 flex-1 max-board-narrow:order-1 max-board-narrow:basis-40"
      onCompositionStart={() => { composing.current = true }}
      onCompositionEnd={(event) => { composing.current = false; later((event.target as HTMLInputElement).value) }}>
      <TextField aria-label={label} value={text}
        onChange={(next) => { setText(next); if (!composing.current) later(next) }}>
        <InputBase type="search" leadingIcon={SearchGlyph} spellCheck={false} autoComplete="off" enterKeyHint="search"
          onKeyDown={onKeyDown} />
      </TextField>
    </div>
  );
}

type Item = IndexPerson | IndexTag | OnlineAuthor | OnlineTag;

export function IndexPage(props: IndexProps) {
  const [route, setRoute] = useState<IndexRoute>(() => indexRoute(
    { kind: props.kind, q: props.q, scope: props.scope, view: props.view, category: props.category },
  ));
  const [layout, setLayout] = useState<PeopleLayout>(props.layout);
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  const [match, setMatch] = useState<'any' | 'all'>('any');
  const root = useRef<HTMLDivElement>(null);
  const { kind, q, scope, view, category } = route;
  const title = INDEX_TITLES[kind];
  const onlineTags = kind === 'tags' && scope === 'online';
  const onlineAuthors = kind === 'performers' && scope === 'online';

  /* 地址给的那一份被规范过（旧链接换了名册、认不出的分类回到全部）：用 replace 改写地址，
     标题、侧栏和历史记录都跟页面上真正显示的那一份走，后退也不会再落回旧地址。
     写地址排到微任务里：壳写地址会同步画侧栏、路由根也同步提交，两处都用 `flushSync`，在提交阶段里画不出来。
     这一页已卸下就不写。 */
  useEffect(() => {
    if (route.kind === props.kind && route.scope === props.scope && route.category === props.category) return;
    let live = true;
    queueMicrotask(() => { if (live) props.route(route, { replace: true }) });
    return () => { live = false };
  }, []);

  /* 选择键归壳：关掉时所选跟着清空，下次打开从零开始，同目录页的多选。 */
  useEffect(() => { if (!props.selectMode) setPicked(new Set()) }, [props.selectMode]);

  /* 打字过滤是在同一份名单上重问一次：新结果到手前留着上一份，骨架不盖上来。换词表、类型、
     视图或公司类型问的是另一份东西，照常先铺骨架。 */
  const [refining, setRefining] = useState(false);
  const go = (patch: Partial<IndexRoute>, replace = false) => {
    const next = { ...route, ...patch };
    setRefining(Object.keys(patch).every((key) => key === 'q'));
    setRoute(next);
    props.route(next, { replace });
  };

  const query = indexQuery(indexSource(route));
  const result = useInfiniteQuery<Page<Item>>({
    queryKey: query.queryKey,
    queryFn: ({ pageParam, signal }) => query.queryFn({ pageParam: pageParam as number, signal }),
    initialPageParam: 0,
    getNextPageParam: query.getNextPageParam,
    placeholderData: refining ? keepPreviousData : undefined,
  });
  const items = flatItems(result.data);
  useLayoutEffect(() => {
    if (peachHistory.navigation.location.pathname === `/${kind}` && result.data && !result.isPlaceholderData)
      rememberHistoryPages(result.data.pages.length);
  }, [kind, result.data, result.isPlaceholderData]);
  const more = !!result.hasNextPage && !result.isPlaceholderData;
  const readout = countText(items.length, !!result.hasNextPage);
  /* 分类计数只随名册、词表和过滤词变，与选中哪一类无关。换分类时新一页还没到，沿用同一份
     名册上一次拿到的计数：分类按钮行不先塌成「全部」加当前项、等数据回来再弹开。头一回
     还没有计数时整排都摆出来，同壳铺的骨架那一排，计数到了再收掉空的那几类。 */
  const countsKey = `${kind}:${scope}:${q}`;
  const lastCounts = useRef<{ key: string; counts: Record<string, number> } | null>(null);
  const pageCounts = result.data?.pages[0]?.categories;
  if (pageCounts && !result.isPlaceholderData) lastCounts.current = { key: countsKey, counts: pageCounts };
  const categoryCounts = pageCounts ?? (lastCounts.current?.key === countsKey ? lastCounts.current.counts : undefined);
  const waiting = result.isPending && !categoryCounts;
  const hasEntries = ([key]: readonly [string, string]) => key === 'all' || waiting || Number(categoryCounts?.[key] || 0) > 0;

  /* 框换了大小，「这张图要不要补底」和人脸放大都得重算：图早加载完了，不会再自己发一次 load。
     赶在绘制之前，否则换版式那一帧是按旧框算的几何。 */
  useLayoutEffect(() => { if (root.current) refitNativeImages(root.current) }, [layout]);

  const tags = useMemo<TagEntry[]>(() => kind === 'tags'
    ? (items as IndexTag[]).map((tag) => ({ ...tag, label: props.tagLabel(tag.k) })) : [], [items, kind]);
  const groups = useMemo(() => kind === 'tags' && view === 'alphabet'
    ? tagGroups(tags, (tag) => props.tagLabel(tag)) : [], [tags, view, kind]);

  const changeLayout = (next: PeopleLayout) => { setLayout(next); props.savePreference({ layout: next }) };

  const tabs = kind === 'tags'
    ? <BoardTabs tabs={SCOPE_TABS} value={scope} label="词表" onChange={(next) => {
      setPicked(new Set());
      // 在线标签全是英文，字母表才是它的形态；切过去时顺手换上，不必再点一次。
      go({ scope: next, category: 'all', view: next === 'online' ? 'alphabet' : view });
    }} />
    : isCompany(kind)
        ? <BoardTabs tabs={MAKER_TABS} value={kind as 'studios' | 'agencies'} label="公司类型"
          onChange={(next) => go({ kind: next })} />
        : null;

  const pressTag = (tag: string) => {
    if (onlineTags) { props.openFollowTag(tag); return }
    if (!props.selectMode) {
      const identity = tags.find(item => item.k === tag);
      if (identity?.entity_id) props.openEntity('tag', tag);
      else props.showTags([tag], 'all');
      return;
    }
    setPicked((current) => {
      const next = new Set(current);
      if (!next.delete(tag)) next.add(tag);
      return next;
    });
  };

  const categories = (onlineTags ? ONLINE_TAG_CATEGORIES : TAG_CATEGORIES).filter(hasEntries);

  const body = () => {
    if (result.isPending) {
      return <div aria-busy="true" dangerouslySetInnerHTML={{ __html: indexSkeletonHtml({ kind, layout, mode: view }) }} />;
    }
    if (result.isError) {
      return (
        <EmptyState icon={spriteGlyph('alert')} title={`没能读取${title}`}
          actions={<Button variant="secondary" onClick={() => void result.refetch()}>重试</Button>}>
          {requestErrorMessage(result.error)}
        </EmptyState>
      );
    }
    if (!items.length) return <IndexEmpty kind={kind} filtered={!!q || category !== 'all'}
      online={onlineTags || onlineAuthors} configurable={props.configurable} />;
    if (onlineAuthors) return <OnlineAuthors items={items as OnlineAuthor[]} layout={layout} props={props} />;
    if (isPeople(kind)) return <PeopleGrid kind={kind} items={items as IndexPerson[]} layout={layout} props={props} />;
    const actions = { online: onlineTags, picked, press: pressTag };
    return view === 'alphabet' ? <TagAlphabet groups={groups} actions={actions} /> : <TagCloud tags={tags} actions={actions} />;
  };

  return (
    <div ref={root} data-index-page={kind} className="flex min-w-0 flex-col">
      <div data-index-head="" className="mb-5 flex min-w-0 flex-wrap items-center gap-3">
        <h2 data-index-title="" className="flex items-center text-display-4-medium whitespace-nowrap text-text-primary max-board-narrow:text-title-1-medium">
          {title}
        </h2>
        {isPeople(kind) ? (
          <span data-index-count="" className="text-caption-1-regular leading-5 whitespace-nowrap text-text-secondary tabular-nums">
            {/* 没取到就没有读数：「0 项」读起来是「名册是空的」，和报错说的是两件事。 */}
            {result.isPending || result.isError ? '' : readout}
          </span>
        ) : null}
        {isPeople(kind) ? <LayoutSwitch kind={kind} layout={layout} onChange={changeLayout} /> : null}
        <LibraryShortcuts kind={kind === 'tags' ? 'tag' : kind === 'studios' ? 'studio' : kind === 'agencies' ? 'agency' : kind === 'creators' ? 'creator' : 'performer'} />
        {/* 窄屏一行放不下四样东西：标题与过滤框一行，开关另起一行。 */}
        <span aria-hidden className="hidden h-0 basis-full max-board-narrow:order-2 max-board-narrow:block" />
        <IndexSearch label={`过滤${title}`} value={q} onQuery={(next) => { if (next !== q) go({ q: next }, !!next) }} />
      </div>
      {kind === 'performers' || kind === 'creators' ? (
        <BoardTabs tabs={PEOPLE_TABS} value={onlineAuthors ? 'online' : kind} label="人物名册"
          onChange={(next) => {
            if (next === 'online') props.exitSelectMode();
            go({ kind: next === 'online' ? 'performers' : next,
              scope: next === 'online' ? 'online' : 'local', category: 'all' });
          }} />
      ) : null}
      {tabs}
      {kind === 'performers' && scope === 'local' ? (
        <div aria-label="身份分类" className={IDENTITY_ROW_CLASS}>
          {IDENTITY_CATEGORIES.filter((entry) => entry[0] === category || hasEntries(entry)).map(([key, label]) => (
            <Button key={key} variant={category === key ? 'primary' : 'secondary'} size="small"
              aria-pressed={category === key} onClick={() => go({ category: key })}>{label}</Button>
          ))}
        </div>
      ) : null}
      {kind === 'tags' ? (
        <TagFilters categories={categories} category={category} online={onlineTags} readout={readout}
          letters={groups.map(([letter]) => letter)} view={view}
          onCategory={(next) => go({ category: next })}
          onView={(next) => go({ view: next })}
          onJump={(at) => root.current?.querySelector(`[data-alpha-group="${at}"]`)
            ?.scrollIntoView({ block: 'start', behavior: 'smooth' })} />
      ) : null}
      <div data-index-body="">{body()}</div>
      {more ? (
        <div className="mt-5 flex justify-center">
          {/* 按下去到下一批画出来之间要有东西在动：这一段是一次网络往返加一屏头像，光把键按灰了
              说不出「还在走」和「点了没反应」的区别。字留在键里，键宽不变、下面的内容不跟着跳。 */}
          <Button variant="secondary" data-index-more="" className="min-w-31"
            disabled={result.isFetchingNextPage} aria-busy={result.isFetchingNextPage || undefined}
            onClick={() => void result.fetchNextPage()}>
            {result.isFetchingNextPage ? <LoadingDots inline label="继续载入中…" /> : '载入更多'}
          </Button>
        </div>
      ) : null}
      {kind === 'tags' && !onlineTags && props.selectMode ? (
        <TagDock count={picked.size} match={match} onMatch={setMatch}
          onClear={() => setPicked(new Set())}
          onApply={() => { const chosen = [...picked]; setPicked(new Set()); props.showTags(chosen, match) }} />
      ) : null}
    </div>
  );
}

const LABELS: Record<IndexKind, string> = {
  tags: '标签', performers: '艺人', creators: '卖家', studios: '厂牌', agencies: '事务所',
};

/** 空馆藏、筛选无结果与在线来源还没有内容分别给出可执行的去处。文案同目录页的空态
 *  （`frontend/src/catalog-onboarding.ts` 的 `catalogEmptyHtml`）。 */
function IndexEmpty(
  { kind, filtered, online, configurable }: { kind: IndexKind; filtered: boolean; online: boolean; configurable: boolean },
) {
  if (filtered) {
    return (
      <EmptyState icon={spriteGlyph('search')} title="没有符合条件的内容"
        actions={<ButtonLink variant="primary" href="/?loc=&thumb=0">查看全部内容</ButtonLink>}>
        清除筛选或搜索条件后查看全部内容。
      </EmptyState>
    );
  }
  // 在线那一档数的是来源上的东西，名字也跟着来源的说法：艺人页在线摆的是关注来源里的创作者。
  const label = online && kind === 'performers' ? '创作者' : LABELS[kind];
  const follow = (
    <ButtonLink variant={!configurable || online ? 'primary' : 'secondary'} href="/subscriptions?tab=add">添加关注</ButtonLink>
  );
  return (
    <EmptyState icon={spriteGlyph(kind === 'tags' ? 'tags' : 'user-round')} title={`还没有${label}`}
      actions={online ? follow : <>
        {configurable ? <Button variant="primary" data-empty-settings="">添加内容</Button> : null}
        {follow}
      </>}>
      {online ? `添加关注来源并获取内容后，这里会显示来源上的${label}。` : '添加内容并补充资料后，这里会显示对应信息。'}
    </EmptyState>
  );
}
