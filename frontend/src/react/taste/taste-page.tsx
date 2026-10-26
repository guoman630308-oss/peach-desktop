/* 口味页：两套证据说同一件事。
 *
 * 左边是浏览器记录（这台机器上读到的、或从别处导进来的私有导出），右边是 Peach 自己的
 * 观看行为。两套各有自己的读数、图和名次，由顶上的页签整块切换——它们是两份证据，不是
 * 同一份数据的两种画法，并排摆会被读成互相印证。
 *
 * 分析范围带在 `queryKey` 上（`['taste', window]`），切换时 `keepPreviousData` 留住上一份：
 * 这一屏的结构不变，只有数在变，退回骨架等于把已经读到的东西收走再放回来。范围本身是
 * 组件状态，不进地址栏：路由仍归遗留壳，进这一页就是从「全部时间」重新看一遍。这一页不设
 * `staleTime`，所以换到哪个范围都重取一趟——导入或移除之后回到先前看过的范围，读到的是写
 * 之后的数，而不是缓存里那份写之前的。
 *
 * 「读取浏览器历史」是后台任务，关掉页面照样在跑。首屏读到的旧终态不冒充新结果：只有本次
 * 点过读取、或者本次亲眼见过它在跑，终态才发回执并让 dashboard 重取（ADR-0031）。 */
import { useRef, useState } from 'react';
import type { ChangeEvent } from 'react';
import {
  RiArrowDownSLine, RiArrowRightSLine, RiCompassLine, RiDatabase2Line, RiDeleteBinLine,
  RiEyeLine, RiHistoryLine, RiPriceTag3Line, RiSearchLine, RiThumbDownLine,
  RiThumbUpLine, RiUploadLine, RiBookmarkLine,
} from '@remixicon/react';
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query';
import { Popover, Tab, TabPanel, Tabs } from 'react-aria-components';
import { MenuDialog as Dialog } from '../components/menu-dialog';

import { avatarInner } from '@peach/card-art';
import { firstGrapheme, fmtSize, siteMarkUrl } from '@peach/legacy/core';
import { confirmModal } from '@peach/legacy/ui';

import { Button } from '@/components/base/buttons/button';
import {
  MENU_ITEM, MENU_ITEM_INTERACTIVE, MENU_POPOVER_SURFACE,
} from '@/components/base/dropdown/menu-styles';
import { Select, SelectItem } from '@/components/base/select/select';
import { cx } from '@/utils/cx';

import { errorMessage } from '../../api';
import { useBackgroundJob } from '../background-job';
import type { TasteProps } from '../bundle';
import { cardClass } from '../components/card';
import { EmptyState } from '../components/empty-state';
import { ExpandableRanking } from '../components/expandable-ranking';
import { LEGACY_AVATAR_IMG } from '../components/legacy-avatar';
import { TaskProgress } from '../components/task-progress';
import { Note } from '../components/note';
import { Page } from '../components/page';
import { Progress } from '../components/progress';
import { SEGMENT, SEGMENTED_TRACK, SegmentedTabList as TabList } from '../components/segmented';
import { StatCard, statCardClass, STAT_STRIP } from '../components/stat-card';
import { queryClient } from '../query';
import { Disclosure } from '../settings/section';
import { busyProps } from '../settings/use-action';
import { ActivityHeat } from '../charts/heat-card';
import { CreatorSankey, RankedBars, TasteRadar } from './charts';
import { BrowserSources, SOURCE_TILE } from './browser-sources';
import {
  DEFAULT_WINDOW, fetchTaste, fetchTasteJob, historySpan, IDLE_POLL_MS, importTasteExport, radarRows,
  rankDetail, rankShares, removeTasteSource, startTasteRefresh, tasteDate, tasteDimensions, tasteHours,
  tasteKey, TASTE_REFRESH_KEY, TASTE_WINDOWS, type RankRow, type TasteData, type TasteJob,
} from './taste';

/** 收起时露这么多条名次。二十条一次铺开会把下面几块整个顶到屏外。 */
const RANK_PREVIEW = 10;

const MENU_ROW = cx(MENU_ITEM, MENU_ITEM_INTERACTIVE, 'text-body-2-medium');
const CARD = `${cardClass()} flex flex-col gap-4`;
/** 卡里的一格：旧 `.tastelead`／`.tastesource` 用的是浮层那一档面，比卡面亮一级。 */
const TILE = 'rounded-xl bg-background-primary-default p-3';

/* 中文正文写成常量：JSX 里换行的文字会在接缝处多出一个空格，中文句子里看得见。 */
const REFRESH_TITLE = '读取运行 Peach 的这台电脑上的浏览历史与书签';
const NO_SOURCE_HINT = '导入或读取浏览记录后，这里会列出已采集设备。';
const NO_LEAD_HINT = '馆藏里暂时没有对得上浏览信号的标签。';
const NO_CATEGORY_HINT = '采集浏览记录后，这里会显示聚合后的口味证据。';
const GAP_HINT = '这些词在浏览记录中出现，但 Peach 观看记录还没有对应证据';
const NO_BROWSER_TAG_HINT = '采集浏览记录后，这里会列出浏览过的标签。';
const NO_BROWSER_CREATOR_HINT = '采集浏览记录后，这里会列出浏览过的创作者。';
const NO_DOMAIN_HINT = '采集浏览记录后，这里会列出常访问的网站。';
const NO_PEACH_TAG_HINT = '在 Peach 里播放或评价作品后，这里会列出对应的标签。';
const NO_PEACH_CREATOR_HINT = '在 Peach 里播放或评价作品后，这里会列出对应的创作者。';
const NO_PEACH_PERFORMER_HINT = '在 Peach 里播放或评价作品后，这里会列出对应的女优。';
const GUIDE_LOCAL = '在运行 Peach 的电脑上使用浏览器：点上面的「读取浏览器记录」。';
const GUIDE_REMOTE = '记录在其他设备上：导出文件后，选择「导入历史文件」或「导入书签文件」。书签支持浏览器导出的 HTML 文件和 Chromium Bookmarks JSON 文件。';
const GUIDE_REFRESH = '需要刷新时再次读取或导入；数据源可在页面底部移除。';
const REMOVE_BODY = '删除这个来源的已导入记录，原始文件保留。若浏览器仍启用，下次读取会再次导入。';

/** 跳过指南是这台浏览器的持久偏好，和分析结果无关，所以不进 Query。 */
export const TASTE_GUIDE_KEY = 'peach-taste-guide-dismissed';

type Glyph = typeof RiEyeLine;

/** 一格读数：一个名字、一个大数、一句它的来历。和统计页的读数卡是同一张卡。 */
function SummaryCard({ icon: Icon, term, figure, detail, detailTitle, accent }:
{ icon: Glyph; term: string; figure: string; detail?: string; detailTitle?: string; accent: number }) {
  return (
    <div className={statCardClass()}>
      <StatCard label={term} icon={Icon} accent={accent} figure={figure}
        footer={<span className="block min-w-0 truncate" title={detailTitle || detail}>{detail ?? ''}</span>} />
    </div>
  );
}

/** 站点圆标：先垫首字母，服务端那枚圆标叠上去；取不到就把 `<img>` 摘掉，露出首字母。
 *
 *  圆标走 `/site-mark`，浏览器不向对方站点也不向任何第三方图标代理发请求——那种请求
 *  会逐个报出这一列里的每一个站，换回来的只是一枚 16px 位图。 */
function SiteAvatar({ name, domain }: { name: string; domain: string }) {
  return (
    <span className="relative inline-grid size-8 shrink-0 place-items-center overflow-hidden rounded-lg bg-background-tertiary-default text-caption-1-medium text-text-secondary">
      {firstGrapheme(name).toUpperCase()}
      <img src={siteMarkUrl({ domain })} alt="" loading="lazy" width={20} height={20}
        onError={(event) => event.currentTarget.remove()}
        className="absolute size-5 object-contain" />
    </span>
  );
}

/** 实体圆标。`card-art` 的 `avatarInner` 是唯一那份回落链实现，页面不重画一遍。 */
function EntityAvatar({ html }: { html: string }) {
  return (
    <span className={`relative inline-grid size-8 shrink-0 place-items-center overflow-hidden rounded-full bg-background-tertiary-default text-caption-1-medium text-text-secondary ${LEGACY_AVATAR_IMG}`}
      dangerouslySetInnerHTML={{ __html: html }} />
  );
}

interface RankListProps {
  rows: RankRow[];
  /** 点得动时交回给壳的信号类型（`tag`／`creator`／`performer`）。空串表示这一榜不可点。 */
  kind: string;
  /** 每行左边画什么：实体圆标、站点圆标，或什么都不画。 */
  visual: 'entity' | 'domain' | 'creator' | 'none';
  empty: string;
  onSignal: TasteProps['onSignal'];
}

/** 一榜名次。点得动的那些是按钮，其余是行——馆藏里没有对应条目时点进去只会是一张空页。 */
function RankList({ rows, kind, visual, empty, onSignal }: RankListProps) {
  if (!rows.length) {
    return <EmptyState shell="plain" icon={RiSearchLine} title="暂无足够证据">{empty}</EmptyState>;
  }
  const shares = rankShares(rows);
  return (
    <ExpandableRanking previewCount={RANK_PREVIEW}
      className="inline-grid w-full gap-x-7 gap-y-2 sm:grid-cols-2">
        {rows.map((row, index) => {
          const clickable = !!kind && !!row.peach_items;
          const domain = String(row.source_domain || '');
          const body = (
            <>
              {/* 名次条铺满整行：旧 `.taste-rank-track` 是 `position:absolute;inset:0` 加 14%
                  不透明度的一块底，不是文字下面那道细线——一眼看的是行有多长。 */}
              <svg viewBox="0 0 100 1" preserveAspectRatio="none" aria-hidden
                className="absolute inset-0 -z-10 size-full overflow-hidden rounded-lg opacity-15">
                <rect width={shares[index]} height={1} className="fill-chart-4" />
              </svg>
              <span className="w-6 shrink-0 text-caption-1-regular tabular-nums text-text-secondary">
                {index + 1}
              </span>
              {visual === 'domain' ? <SiteAvatar name={row.name} domain={row.name} /> : null}
              {visual === 'creator' && !row.entity_id && !row.has_avatar && domain
                ? <SiteAvatar name={row.name} domain={domain} /> : null}
              {visual !== 'none' && visual !== 'domain'
                && (row.entity_id || row.has_avatar || !domain)
                ? <EntityAvatar html={avatarInner(
                    row.name,
                    row.entity_id
                      ? { id: row.entity_id, has_image: !!row.has_image, image_version: row.image_version,
                        avatar_focus: row.avatar_focus, avatar_stand_in: row.avatar_stand_in }
                      : null,
                    row.has_avatar ? row.representative_asset_id ?? null : null,
                    visual === 'creator' ? 'creator' : kind || 'performer',
                  )} /> : null}
              <span className="flex min-w-0 grow flex-col gap-0.5">
                <b className="min-w-0 text-body-medium break-words text-text-primary">{row.name}</b>
                <small className="text-caption-1-regular text-text-secondary">{rankDetail(row)}</small>
              </span>
              {clickable ? <RiArrowRightSLine aria-hidden className="size-4 shrink-0 text-text-tertiary" /> : null}
            </>
          );
          const shape = 'relative isolate flex min-h-11 w-full min-w-0 items-center gap-2.5'
            + ' rounded-lg px-3 py-2 text-left';
          return (
            <li key={`${row.name}:${index}`} className="min-w-0">
              {clickable
                ? <button type="button" onClick={() => onSignal(kind, row.name)}
                    className={`${shape} cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring`}>
                    {body}
                  </button>
                : <div className={shape}>{body}</div>}
            </li>
          );
        })}
    </ExpandableRanking>
  );
}

/** 一侧证据的维度面板。页签走 React Aria，切的是同一块地方的几层名次。 */
function DimensionPanels(
  { label, panels, onSignal }:
  {
    label: string;
    panels: { id: string; name: string; props: Omit<RankListProps, 'onSignal'> }[];
  } & Pick<TasteProps, 'onSignal'>,
) {
  return (
    /* 旧 `.insightpanel`：一张 16px 的填充卡，页签是卡内顶上那条分段控件。 */
    <Tabs className={`${cardClass({ padding: 'none' })} flex flex-col`}>
      <div className="px-4 pt-3">
        <TabList aria-label={label} className={SEGMENTED_TRACK}>
          {panels.map((panel) => <Tab key={panel.id} id={panel.id} className={SEGMENT}>{panel.name}</Tab>)}
        </TabList>
      </div>
      {panels.map((panel) => (
        <TabPanel key={panel.id} id={panel.id} className="px-4 pt-3.5 pb-4">
          <RankList {...panel.props} onSignal={onSignal} />
        </TabPanel>
      ))}
    </Tabs>
  );
}

/** 口味总结：一句结论、几条要点，加上点进去就能做的事。 */
function AnalysisCard({ data, onSignal, navigate }:
{ data: TasteData } & Pick<TasteProps, 'onSignal' | 'navigate'>) {
  const analysis = data.analysis;
  if (!analysis?.headline) return null;
  const confidence = analysis.confidence || {};
  const leads = [
    ...(analysis.explore || []).map((item) => (
      { key: `tag:${item.tag}`, title: item.title, detail: item.detail, act: () => onSignal('tag', item.tag) })),
    ...(analysis.next_steps || []).map((item) => (
      { key: `route:${item.route}`, title: item.title, detail: item.detail, act: () => navigate(item.route) })),
  ];
  return (
    <section className={CARD} aria-label="口味总结">
      <header className="flex flex-col gap-2">
        <h3 className="text-title-2-medium text-text-primary">口味总结</h3>
        {/* 结论句是这张卡的主角：旧 `.tasteleads .tastelede p` 是 20/30 的主文字色，比正文
            大一档、限宽 64ch。「有多少把握」是它的注脚，旧版就是一句灰字配一枚 8px 圆点。 */}
        <p className="max-w-prose text-title-2-regular wrap-anywhere text-text-primary">{analysis.headline}</p>
        <p className="flex flex-wrap items-center gap-2 text-body-2-regular text-text-secondary">
          <i aria-hidden className={`size-2 shrink-0 rounded-full ${confidence.level === 'high'
            ? 'bg-status-lime-text'
            : confidence.level === 'medium' ? 'bg-status-yellow-text' : 'bg-text-tertiary'}`} />
          <span className="min-w-0 wrap-anywhere">{confidence.label || '仍在学习'}</span>
        </p>
      </header>
      {analysis.points?.length ? (
        <div className="inline-grid w-full gap-3 sm:grid-cols-2">
          {analysis.points.map((point) => (
            <div key={point.label} className={`flex min-w-0 flex-col gap-1 ${TILE}`}>
              <span className="text-caption-1-regular text-text-secondary">{point.label}</span>
              <b className="text-body-2-medium break-words text-text-primary">{point.text}</b>
            </div>
          ))}
        </div>
      ) : null}
      {leads.length ? (
        <div className="flex flex-col gap-2">
          {leads.map((lead) => (
            <button key={lead.key} type="button" onClick={lead.act}
              className={`flex w-full min-w-0 cursor-pointer items-center gap-3 ${TILE} text-left outline-none hover:bg-background-primary-hover focus-visible:ring-2 focus-visible:ring-border-focus-ring`}>
              <span className="flex min-w-0 grow flex-col gap-1">
                <b className="text-body-2-medium wrap-anywhere text-text-primary">{lead.title}</b>
                <small className="text-caption-1-regular wrap-anywhere text-text-secondary">{lead.detail}</small>
              </span>
              <RiArrowRightSLine aria-hidden className="size-4 shrink-0 text-text-tertiary" />
            </button>
          ))}
        </div>
      ) : <EmptyState shell="plain" icon={RiSearchLine} title="还没有可探索的入口">{NO_LEAD_HINT}</EmptyState>}
    </section>
  );
}

/** 浏览记录怎么来。已经采集到记录、或跳过之后都不再出现，`?onboarding=1` 进来时直接展开。 */
function HistoryGuide({ onboarding, done }: { onboarding: boolean; done: boolean }) {
  const [skipped, setSkipped] = useState(() => {
    try { return localStorage.getItem(TASTE_GUIDE_KEY) === '1' } catch { return false }
  });
  if (done || skipped) return null;
  const skip = () => {
    try { localStorage.setItem(TASTE_GUIDE_KEY, '1') } catch { /* 存储不可用时这一次仍然收起 */ }
    setSkipped(true);
  };
  return (
    <div className={cardClass({ padding: 'none', className: 'p-4' })}>
      <Disclosure summary="浏览器历史记录导入指南" defaultOpen={onboarding}>
        <p className="text-body-2-regular text-text-secondary">{GUIDE_LOCAL}</p>
        <p className="text-body-2-regular text-text-secondary">{GUIDE_REMOTE}</p>
        <ul className="flex list-disc flex-col gap-1 pl-5 text-body-2-regular text-text-secondary">
          <li>
            Chrome：在 <a href="https://takeout.google.com/" target="_blank" rel="noopener noreferrer"
              className="text-text-primary underline-offset-4 hover:underline">Google Takeout</a>
            {' '}选择 Chrome 历史记录，下载 ZIP 后直接导入。
          </li>
          <li>
            其他浏览器：用 <a href="https://github.com/purarue/browserexport" target="_blank" rel="noopener noreferrer"
              className="text-text-primary underline-offset-4 hover:underline">browserexport</a>
            {' '}导出历史记录，再导入导出文件。
          </li>
        </ul>
        <p className="text-body-2-regular text-text-secondary">{GUIDE_REFRESH}</p>
        <span className="self-start">
          <Button variant="secondary" size="small" onClick={skip}>跳过</Button>
        </span>
      </Disclosure>
    </div>
  );
}

/** 主键加一个下拉：读取和导入是同一件事的两种来路，摊成两颗按钮读不出哪个是常用的那一个。
 *
 *  菜单第一项和左边那颗同名同事——键盘和读屏用户只走菜单这一条路，少列一项就是少一个动作。 */
function HistoryActions(
  { busy, onRefresh, onImport, onImportBookmarks }:
  { busy: boolean; onRefresh(): void; onImport(): void; onImportBookmarks(): void },
) {
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const pick = (act: () => void) => { setOpen(false); act() };
  return (
    <>
      <span data-button-group data-split-button data-variant="primary">
        <Button leadingIcon={RiCompassLine} title={REFRESH_TITLE} onClick={onRefresh} {...busyProps(busy)}>
          读取浏览器记录
        </Button>
        <Button ref={trigger} iconOnly leadingIcon={RiArrowDownSLine}
          aria-label="更多取得浏览记录的方式" aria-haspopup="dialog" aria-expanded={open}
          {...busyProps(busy)} onClick={() => { if (!busy) setOpen(true) }} />
      </span>
      <Popover triggerRef={trigger} isOpen={open} onOpenChange={setOpen}
        placement="bottom end" offset={4} className={MENU_POPOVER_SURFACE}>
        <Dialog aria-label="更多取得浏览记录的方式" className="flex w-52 flex-col gap-1 outline-none">
          <button type="button" className={MENU_ROW} onClick={() => pick(onRefresh)}>
            <RiCompassLine aria-hidden className="size-4 shrink-0" />读取浏览器记录
          </button>
          <button type="button" className={MENU_ROW} onClick={() => pick(onImport)}>
            <RiUploadLine aria-hidden className="size-4 shrink-0" />导入历史文件
          </button>
          <button type="button" className={MENU_ROW} onClick={() => pick(onImportBookmarks)}>
            <RiBookmarkLine aria-hidden className="size-4 shrink-0" />导入书签文件
          </button>
        </Dialog>
      </Popover>
    </>
  );
}

/** 已采集设备。移除只影响分析，原始导出文件仍在本机。 */
function SourceList(
  { sources, browsers, window: range, toast }:
  { sources: NonNullable<TasteData['sources']>; browsers: NonNullable<TasteData['browsers']>; window: string } & Pick<TasteProps, 'toast'>,
) {
  const remove = useMutation({
    mutationFn: (sourceKey: string) => removeTasteSource(sourceKey, range),
    onSuccess: (result) => {
      // 服务端连这一范围的新 dashboard 一起回，换进缓存即可，不为一次移除把整页重取一遍。
      queryClient.setQueryData(tasteKey(range), result.dashboard);
      toast('已移除口味数据源');
    },
  });
  return (
    <BrowserSources browsers={browsers} sources={sources} range={range}>
      {remove.error ? <div className="sm:col-span-2"><Note tone="error">{errorMessage(remove.error)}</Note></div> : null}
      {sources.length ? (
        <>
          {sources.filter(source => !browsers.some(browser => browser.source_keys?.includes(source.source_key))).map((source) => (
            <div key={source.source_key} data-taste-source="import"
              className={SOURCE_TILE}>
              <span className="inline-grid size-8 shrink-0 place-items-center rounded-lg text-text-secondary">
                {source.browser === 'browserexport'
                  ? <RiUploadLine aria-hidden className="size-4" />
                  : <RiDatabase2Line aria-hidden className="size-4" />}
              </span>
              <span className="flex min-w-0 grow flex-col gap-0.5">
                <b className="min-w-0 text-body-2-medium break-words text-text-primary">{source.profile}</b>
                <small className="min-w-0 text-caption-1-regular break-words text-text-secondary">
                  {`导入文件 · ${source.host} · ${Number(source.visits || source.bookmarks || 0).toLocaleString()} ${source.bookmarks ? '枚书签' : '条历史'}`}
                </small>
              </span>
              <Button variant="secondary" size="small" aria-label={`移除 ${source.profile}`} className="shrink-0"
                iconOnly leadingIcon={RiDeleteBinLine} {...busyProps(remove.isPending)}
                onClick={() => {
                  if (remove.isPending) return;
                  void confirmModal({
                    title: '移除口味数据源', body: REMOVE_BODY, confirmLabel: '移除口味数据源',
                    onConfirm: () => remove.mutateAsync(source.source_key),
                  });
                }} />
            </div>
          ))}
        </>
      ) : !browsers.length ? <EmptyState shell="plain" icon={RiDatabase2Line} title="还没有数据源">{NO_SOURCE_HINT}</EmptyState> : null}
    </BrowserSources>
  );
}

export function TastePage(props: TasteProps) {
  const { onSignal, navigate, toast, onboarding } = props;
  const [range, setRange] = useState(DEFAULT_WINDOW);
  const [evidence, setEvidence] = useState('browser');
  const file = useRef<HTMLInputElement>(null);
  const bookmarkFile = useRef<HTMLInputElement>(null);

  const taste = useQuery({
    queryKey: tasteKey(range),
    queryFn: ({ signal }) => fetchTaste(range, signal),
    /* 换范围时留住上一份：这一屏的结构不变，只有数在变。退回骨架就是把已经读到的东西
       收走再放回来，而服务端那一层缓存多半立刻就回来了。 */
    placeholderData: keepPreviousData,
  });
  const { job, running, outcome, start: refresh } = useBackgroundJob<TasteJob>({
    queryKey: TASTE_REFRESH_KEY,
    queryFn: ({ signal }) => fetchTasteJob(signal),
    start: () => startTasteRefresh(range),
    /* 读取可以从引导页或上一次会话里起来，闲着也隔一会儿问一次。 */
    idlePollMs: IDLE_POLL_MS,
    onFinish: (state) => {
      if (state.status === 'failed') return;
      void queryClient.invalidateQueries({ queryKey: ['taste'] });
      toast(state.warnings?.length ? '部分浏览记录未取得' : '已更新口味分析');
    },
  });
  const load = useMutation({
    mutationFn: (chosen: File) => importTasteExport(chosen),
    onSuccess: (result) => {
      /* 服务端回的是「全部时间」那一份，页面跟着切过去：刚导进来的记录多半不在当前
         这个窗口里，留在原范围上会看见一份没有任何变化的结果。先把它换进缓存再切范围，
         顺序反过来的话切过去那一刻「全部时间」还是空的，页面会为它多发一趟请求，
         回来的那一份又把刚换进去的这一份盖掉。 */
      queryClient.setQueryData(tasteKey('all'), result.dashboard);
      setRange('all');
      toast('已导入口味数据');
    },
  });

  const pickFile = (event: ChangeEvent<HTMLInputElement>) => {
    const chosen = event.currentTarget.files?.[0];
    event.currentTarget.value = '';
    if (!chosen || load.isPending) return;
    load.reset();
    load.mutate(chosen);
  };
  const startRefresh = () => {
    if (running || refresh.isPending) return;
    refresh.reset();
    refresh.mutate();
  };

  const data = taste.data;
  if (!data) {
    return (
      <Page>
        <Note tone="error" title="口味分析读取失败">
          {taste.error ? errorMessage(taste.error) : '读取口味分析失败'}
        </Note>
      </Page>
    );
  }
  const summary = data.summary || {};
  const coverage = data.coverage || {};
  const rank = data.rankings || {};
  const storage = data.storage || {};
  const categories = tasteDimensions(rank.browser_categories || []);
  const radar = radarRows(categories).length > 0;
  const gaps = data.gaps || [];
  const tagged = coverage.tagged || 0;
  const identified = coverage.identified || 0;
  const problem = refresh.error ? errorMessage(refresh.error)
    : load.error ? errorMessage(load.error)
    : outcome?.status === 'failed' ? (outcome.error || '读取未取得')
    : '';

  return (
    <Page>
      <Tabs selectedKey={evidence} onSelectionChange={(key) => setEvidence(String(key))}
        className="flex flex-col gap-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {/* 旧 `.insightswitch`：证据来源是两个互斥视图，形态是分段控件不是两枚裸按钮。 */}
          <TabList aria-label="口味证据来源" className={SEGMENTED_TRACK}>
            <Tab id="browser" className={SEGMENT}>浏览器记录</Tab>
            <Tab id="peach" className={SEGMENT}>Peach 内部</Tab>
          </TabList>
          <div className="flex flex-wrap items-center gap-2">
            <Select aria-label="分析范围" selectedKey={range}
              onSelectionChange={(key) => { if (key !== null) setRange(String(key)) }}>
              {TASTE_WINDOWS.map(([key, name]) => <SelectItem key={key} id={key}>{name}</SelectItem>)}
            </Select>
            <HistoryActions busy={running || refresh.isPending || load.isPending}
              onRefresh={startRefresh} onImport={() => file.current?.click()}
              onImportBookmarks={() => bookmarkFile.current?.click()} />
            {/* 原生文件选择器长相不可控，按钮归 BoardUI，输入框只留着接文件。 */}
            <input ref={file} type="file" tabIndex={-1} aria-hidden className="hidden"
              onChange={pickFile} />
            <input ref={bookmarkFile} type="file" tabIndex={-1} aria-hidden className="hidden"
              data-bookmark-import onChange={pickFile} />
          </div>
        </div>
        <HistoryGuide onboarding={onboarding}
          done={!!(summary.history_sources || data.updated_at)} />
        {/* 这一趟在后台跑，关掉页面还在继续，所以状态留在页面上而不是只让按钮转一下。 */}
        <div aria-live="polite" className="flex flex-col gap-3 empty:hidden">
          {running ? <TaskProgress label={job?.message || '正在读取浏览记录并更新口味分析'}
            value={job?.checked} total={job?.total} /> : null}
          {load.isPending ? <TaskProgress label="正在导入口味数据" /> : null}
          {problem ? <Note tone="error">{problem}</Note> : null}
          {outcome?.warnings?.length ? <Note tone="warning">{outcome.warnings.join('；')}</Note> : null}
        </div>
        <TabPanel id="browser" className="flex flex-col gap-5">
          <div className={STAT_STRIP}>
            <SummaryCard accent={0} icon={RiHistoryLine} term="浏览记录"
              figure={Number(summary.history_visits || 0).toLocaleString()}
              detail={historySpan(summary)} detailTitle={`${tasteDate(summary.range_start)}—${tasteDate(summary.range_end)}`} />
            <SummaryCard accent={1} icon={RiPriceTag3Line} term="口味维度"
              figure={categories.length.toLocaleString()} detail={categories[0]?.name || '尚无主维度'} />
            <SummaryCard accent={2} icon={RiSearchLine} term="浏览线索" figure={gaps.length.toLocaleString()} />
            <SummaryCard accent={3} icon={RiBookmarkLine} term="浏览器书签"
              figure={Number(summary.bookmarks || 0).toLocaleString()}
              detail={`${Number(storage.exports || 0).toLocaleString()} 份私有导出 · ${fmtSize(storage.bytes || 0)}`} />
          </div>
          {/* 维度不足三个画不成雷达：左栏只留标题与更新时间，排行条占满整张卡。 */}
          <section className={radar ? `${CARD} md:flex-row md:gap-6` : CARD} aria-label="浏览器画像">
            <div className={radar
              ? 'flex shrink-0 flex-col gap-2 pb-5 md:w-80 md:pr-6 md:pb-0'
              : 'flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1'}>
              <span className="text-caption-1-regular text-text-secondary">浏览器画像</span>
              {radar ? <TasteRadar rows={categories} label="主要口味维度" /> : null}
              <small className="text-caption-1-regular text-text-secondary">
                {data.updated_at ? `更新于 ${tasteDate(data.updated_at)}` : '尚未采集浏览记录'}
              </small>
            </div>
            {/* 右栏跟着雷达那一栏等高（行内默认拉伸），排行条在纵向 flex 里撑满这一栏。 */}
            <div className="flex min-w-0 grow flex-col">
              {categories.length
                ? <RankedBars rows={categories} label="口味维度排名" />
                : <EmptyState shell="plain" icon={RiSearchLine} title="暂无口味维度">{NO_CATEGORY_HINT}</EmptyState>}
            </div>
          </section>
          <ActivityHeat activity={data.activity} title="浏览活跃时间" dailyTitle="每日活跃"
            words={{ unit: '口味网站访问', series: '访问', cellUnit: '次访问' }} tone={4}
            empty="还没有可用于分析的口味网站访问记录。" />
          <CreatorSankey flows={data.creator_flows} />
          <DimensionPanels label="浏览器口味维度" onSignal={onSignal}
            panels={[
              { id: 'tags', name: '标签', props: { rows: rank.browser_tags || [], kind: 'tag', visual: 'none', empty: NO_BROWSER_TAG_HINT } },
              { id: 'creators', name: '创作者', props: { rows: rank.browser_creators || [], kind: 'creator', visual: 'creator', empty: NO_BROWSER_CREATOR_HINT } },
              { id: 'performers', name: '女优', props: { rows: rank.browser_performers || [], kind: 'performer', visual: 'entity', empty: '还没有与已建档女优关联的浏览记录。' } },
              { id: 'domains', name: '常访问网站', props: { rows: rank.domains || [], kind: '', visual: 'domain', empty: NO_DOMAIN_HINT } },
              { id: 'gaps', name: '浏览线索', props: { rows: gaps, kind: '', visual: 'none', empty: GAP_HINT } },
            ]} />
        </TabPanel>
        <TabPanel id="peach" className="flex flex-col gap-5">
          <div className={STAT_STRIP}>
            <SummaryCard accent={0} icon={RiEyeLine} term="Peach 看过"
              figure={Number(summary.peach_items || 0).toLocaleString()}
              detail={tasteHours(summary.peach_seconds || 0)} />
            <SummaryCard accent={1} icon={RiThumbUpLine} term="喜欢" figure={Number(summary.liked || 0).toLocaleString()} />
            <SummaryCard accent={2} icon={RiThumbDownLine} term="不合口味" figure={Number(summary.disliked || 0).toLocaleString()} />
            <SummaryCard accent={3} icon={RiPriceTag3Line} term="有标签" figure={tagged.toLocaleString()} />
          </div>
          <section className={`${CARD} md:flex-row md:gap-6`} aria-label="Peach 观看">
            <div className="flex shrink-0 flex-col gap-0.5 pb-5 md:w-72 md:pr-6 md:pb-0">
              <span className="text-caption-1-regular text-text-secondary">Peach 观看</span>
              <b className="text-display-4-medium tabular-nums text-text-primary">
                {Number(summary.peach_items || 0).toLocaleString()}
              </b>
              <span className="text-body-2-regular text-text-secondary">个作品有内部行为证据</span>
            </div>
            <div className="flex min-w-0 grow flex-col">
              <CoverageMetric term="有标签" value={tagged} rest={coverage.untagged || 0} />
              <CoverageMetric term="有身份" value={identified} rest={coverage.unidentified || 0} />
            </div>
          </section>
          <DimensionPanels label="Peach 口味维度" onSignal={onSignal}
            panels={[
              { id: 'tags', name: '标签', props: { rows: rank.peach_tags || [], kind: 'tag', visual: 'none', empty: NO_PEACH_TAG_HINT } },
              { id: 'creators', name: '创作者', props: { rows: rank.peach_creators || [], kind: 'creator', visual: 'creator', empty: NO_PEACH_CREATOR_HINT } },
              { id: 'performers', name: '女优', props: { rows: rank.peach_performers || [], kind: 'performer', visual: 'entity', empty: NO_PEACH_PERFORMER_HINT } },
            ]} />
        </TabPanel>
      </Tabs>
      <AnalysisCard data={data} onSignal={onSignal} navigate={navigate} />
      <SourceList sources={data.sources || []} browsers={data.browsers || []} window={range} toast={toast} />
    </Page>
  );
}

/** 一条覆盖率：读数、待补的数量与那条条共用同一对分子分母。 */
function CoverageMetric({ term, value, rest }: { term: string; value: number; rest: number }) {
  const total = Math.max(value + rest, 1);
  return (
    <div className="flex flex-col gap-1.5 border-b border-separator-border py-3 last:border-b-0">
      <p className="flex items-baseline justify-between gap-3 text-body-2-regular text-text-primary">
        <span>{term}</span>
        <b className="tabular-nums">
          {value.toLocaleString()}
          <span className="ml-1.5 text-caption-1-regular text-text-secondary">{`${rest.toLocaleString()} 项待补`}</span>
        </b>
      </p>
      <Progress label={`${term}：${value.toLocaleString()} / ${total.toLocaleString()}`}
        value={value} max={total} />
    </div>
  );
}
