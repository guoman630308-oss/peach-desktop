/* 关注管理页的「JAV 订阅源」页签：上面一张「添加 JAV 订阅」卡，下面一张和关注列表同一个外观的表，
 * 一行一条源，名字前是她的资料图，列出类型、来源、状态、频率、上次拉取与新增，行尾是启用开关、
 * 拉取键与移除键（ADR-0042）。行尾这三样和关注列表那一行一一对应，两张表看起来是一回事。
 *
 * 订阅从人物页的「订阅新作」或这里按名字进，两条路都不收地址（ADR-0047、ADR-0083）。拉回来的
 * 新作排在首页与人物页筛选栏下面那一行，不在这里列——这里再列一遍就成了第二个入口，两处的
 * 已读状态会各说各话。
 * 服务端是唯一真相：开关与移除之后重取这一份，不在前端按响应拼一份新的本地状态。
 *
 * 勾选和关注列表同一套：勾在行首，点一行的空白处也是选这一行，选中了底部浮出批量操作。 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  createColumnHelper, createPaginatedRowModel, createSortedRowModel, flexRender, rowPaginationFeature,
  rowSelectionFeature, rowSortingFeature, tableFeatures, useTable,
  type RowSelectionState, type SortingState,
} from '@tanstack/react-table';
import { RiDeleteBinLine, RiRefreshLine, RiRssLine } from '@remixicon/react';
import { VisuallyHidden } from 'react-aria-components';

import { avatarInner } from '@peach/card-art';
import { mapLimit } from '@peach/legacy/core';
import { confirmModal } from '@peach/legacy/ui';

import { Chip } from '@/components/base/badges/chip';
import { Button } from '@/components/base/buttons/button';
import { Checkbox } from '@/components/base/checkbox/checkbox';
import { Switch } from '@/components/base/switch/switch';
import {
  Table, TableBody, TableCell, TableColumn, TableHeader, TableRow,
} from '@/components/base/table/table';
import { ChevronSortDown } from '@/components/foundations/icons/chevrons';

import { errorMessage } from '../../api';
import { DataTableFrame } from '../components/data-table-frame';
import { DataTableFooter, DataTableSort } from '../components/table-controls';
import { cardClass } from '../components/card';
import { EmptyState } from '../components/empty-state';
import { LEGACY_AVATAR_IMG } from '../components/legacy-avatar';
import { Note } from '../components/note';
import { SelectionDock } from '../components/selection-dock';
import { queryClient } from '../query';
import { ErrorText, Help } from '../settings/section';
import { busyProps, useAction } from '../settings/use-action';
import { localTime } from '../time';
import { AddFeed } from './add-feed';
import {
  checkFeeds, errorHeadline, FEEDS_KEY, feedIntervalText, fetchFeeds, removeFeed, setFeedEnabled,
  pageWindow, type FeedSource,
} from './follow-manage';

const feedTableFeatures = tableFeatures({
  rowPaginationFeature,
  rowSelectionFeature,
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
  paginatedRowModel: createPaginatedRowModel(),
});

const COLUMN_LABELS: Record<string, string> = {
  select: '选择',
  name: '名称',
  kind: '类型',
  origin: '来源',
  status: '状态',
  interval: '频率',
  fetched: '上次拉取',
  fresh: '上次新增',
  actions: '操作',
};

/** 表头里不占字的列：勾选那一格自己会说「选择 谁」，行尾三个控件也各带自己的名字。 */
const SILENT_COLUMNS = new Set(['select', 'actions']);

const reload = () => queryClient.invalidateQueries({ queryKey: FEEDS_KEY, exact: true });

const feedName = (source: FeedSource) => source.name || source.url;


/** 来源那一格只摆站名：地址整条写出来会把这张表撑到一屏之外，点开就是原页面。 */
function originHost(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

/** 一条源此刻的状态：拉不动最要紧，其次才是开着还是停着。 */
function StatusChip({ source }: { source: FeedSource }) {
  if (source.last_error) return <Chip variant="caption" color="rose">拉取失败</Chip>;
  if (!source.enabled) return <Chip variant="caption" color="neutral">已暂停</Chip>;
  return <Chip variant="caption" color="lime">正常</Chip>;
}

/** 名字前的圆框：和关注列表的创作者圆标同一个尺寸与底色。里面那段由 `card-art` 的 `avatarInner` 拼：
 *  有资料图按人脸取景出图，判据是服务端的 `has_image`；没有就是首字母。整格不进读屏，名字就在旁边。 */
function FeedAvatar({ source }: { source: FeedSource }) {
  const entity = source.entity_id
    ? {
        id: source.entity_id, has_image: !!source.has_image, image_version: source.image_version,
        avatar_focus: source.avatar_focus,
      }
    : null;
  return (
    <span aria-hidden
      className={`relative inline-grid size-8 shrink-0 place-items-center overflow-hidden rounded-full bg-background-tertiary-default text-caption-1-semibold text-text-secondary ${LEGACY_AVATAR_IMG}`}
      dangerouslySetInnerHTML={{ __html: avatarInner(feedName(source), entity, null, 'performer') }} />
  );
}

/** 这一页在站上挂的另一个名字。一个人常有两页，本名页和旧艺名页在表里同名，
 *  只有这一段说得出哪一行是哪一页。只在这位不止一行时写：站上的标题栏常把几种写法
 *  连成一串（「涼森玲夢, 涼森れむ」），只有一行的人再摆一遍只是噪音；和统称一样时也不重复。 */
const pageAlias = (source: FeedSource, shared: ReadonlySet<number>) =>
  (source.entity_id !== null && shared.has(source.entity_id)
    && source.page_name && source.page_name !== source.name ? source.page_name : '');

/** 挂着不止一条源的人。 */
function sharedEntities(sources: FeedSource[]): ReadonlySet<number> {
  const seen = new Set<number>();
  const shared = new Set<number>();
  for (const { entity_id: id } of sources) {
    if (id === null) continue;
    if (seen.has(id)) shared.add(id); else seen.add(id);
  }
  return shared;
}

interface RowHandlers {
  readOnly: boolean;
  /** 挂着不止一条源的人，名字旁的页名只给他们写。 */
  shared: ReadonlySet<number>;
  /** 正在跑的那个动作的键（`useAction` 的 `busy`），行尾的拉取键按它挂忙态。 */
  busy: string | null;
  toggle(source: FeedSource, enabled: boolean): void;
  fetch(source: FeedSource): void;
  remove(source: FeedSource): void;
}

/** 移除前先弹确认，和关注列表那一行同一套：删的是这条源和它的去重记忆，拉回来的新作留着
 *  （`feed_discovery.source_id` 置空）。写入交给弹层，忙态和失败原因都落在弹层里。 */
function confirmRemove(sources: FeedSource[], write: () => Promise<unknown>) {
  const one = sources.length === 1 ? sources[0] : null;
  return confirmModal({
    title: one ? '移除订阅源' : `移除 ${sources.length} 条订阅源`,
    body: one
      ? `将移除订阅源「${feedName(one)}」，之后不再拉取它的新作；已经拉到的新作保留。`
      : '将移除所选订阅源，之后不再拉取它们的新作；已经拉到的新作保留。',
    confirmLabel: one ? '移除订阅源' : '移除所选订阅源', danger: true,
    onConfirm: write,
  });
}

export function FeedSources({ readOnly, toast }: {
  readOnly: boolean; toast(message: string): void;
}) {
  const feeds = useQuery({ queryKey: FEEDS_KEY, queryFn: ({ signal }) => fetchFeeds(signal) });
  const action = useAction();
  const data = feeds.data;
  const sources = useMemo(() => data?.sources ?? [], [data]);
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set<number>());
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [sorting, setSorting] = useState<SortingState>([]);
  const win = pageWindow(sources.length, pageSize, page);
  useEffect(() => { setPage((current) => Math.min(current, win.pages)) }, [win.pages]);

  /* 删掉的源不该还占着计数：清单换一份就把已经不在里面的 ID 丢掉。 */
  useEffect(() => {
    setSelected((now) => {
      const alive = new Set(sources.map((source) => source.id));
      const next = new Set([...now].filter((id) => alive.has(id)));
      return next.size === now.size ? now : next;
    });
  }, [sources]);

  const bulk = useMutation({
    mutationFn: async (work: { ids: number[]; action: 'enabled' | 'paused' | 'remove' }) => {
      const results = await mapLimit(work.ids, 4, async (id: number) => {
        if (work.action === 'remove') await removeFeed(id);
        else await setFeedEnabled(id, work.action === 'enabled');
      });
      return { ...work, results };
    },
    onSuccess: (result) => {
      const failed = result.results.filter((row) => !row.ok);
      const done = result.ids.length - failed.length;
      setSelected(new Set());
      void reload();
      if (result.action === 'remove' && done) toast(`已移除 ${done} 条订阅源`);
      if (failed.length) {
        const first = failed[0]!;
        action.setError(`${failed.length} 条没有写入：${first.ok ? '' : errorMessage(first.error)}`);
      }
    },
    onError: (cause) => action.setError(errorMessage(cause)),
  });

  /* 列定义只建一次，行里的控件到点击那一刻再从这里取最新的处理器与只读态。 */
  const shared = useMemo(() => sharedEntities(sources), [sources]);
  const handlers = useRef<RowHandlers>({
    readOnly, shared, busy: null, toggle: () => {}, fetch: () => {}, remove: () => {},
  });
  handlers.current = {
    readOnly,
    shared,
    busy: action.busy,
    toggle: (source, enabled) => void action.run(`enabled-${source.id}`,
      (signal) => setFeedEnabled(source.id, enabled, signal), () => void reload()),
    /* 只拉这一条，停着的也拉：点这枚键的人就是想现在看她有没有新作，不必先去开开关。 */
    fetch: (source) => void action.run(`check-${source.id}`,
      (signal) => checkFeeds(signal, [source.id]), () => void reload()),
    remove: (source) => void confirmRemove([source], async () => {
      await removeFeed(source.id);
      toast(`已移除订阅源「${feedName(source)}」`);
      void reload();
    }),
  };

  const columns = useMemo(() => {
    const column = createColumnHelper<typeof feedTableFeatures, FeedSource>();
    const label = (id: string) => () => COLUMN_LABELS[id] || id;
    return column.columns([
      column.display({
        id: 'select',
        header: label('select'),
        /* `slot={null}`：表格自带一个叫 selection 的插槽，摆进去的勾不声明归属就会被它
           拦下报错。这一列的勾归 TanStack Table 那份行选择管，不走 Table 自己的选择。 */
        cell: (context) => (
          <Checkbox slot={null} isSelected={context.row.getIsSelected()}
            onChange={(on) => context.row.toggleSelected(on)}
            aria-label={`选择 ${feedName(context.row.original)}`} />
        ),
      }),
      column.accessor(feedName, {
        id: 'name',
        header: label('name'),
        /* 名字、频率与时间都不换行：窄屏上这张表靠 Table 自带的容器横着滚，让格子换行只会
           把「三上悠亜」竖着摆成四行，滚动反而没了用处。 */
        cell: (context) => {
          const alias = pageAlias(context.row.original, handlers.current.shared);
          return (
            <span className="flex items-center gap-2 whitespace-nowrap text-body-medium text-text-primary">
              <FeedAvatar source={context.row.original} />
              {context.getValue()}
              {alias ? (
                <span className="text-body-2-regular text-text-secondary" title={`站上这一页挂在「${alias}」名下`}>
                  {alias}
                </span>
              ) : null}
            </span>
          );
        },
      }),
      column.accessor((row) => row.kind_label, {
        id: 'kind',
        header: label('kind'),
        cell: (context) => <span className="whitespace-nowrap">{context.getValue()}</span>,
      }),
      column.accessor((row) => row.url, {
        id: 'origin',
        header: label('origin'),
        cell: (context) => (
          <a href={context.getValue()} target="_blank" rel="noreferrer noopener" title="打开来源页面"
            className="text-text-primary underline-offset-2 hover:underline">
            {originHost(context.getValue())}
          </a>
        ),
      }),
      column.accessor((source) => source.last_error ? '拉取失败' : source.enabled ? '已启用' : '已暂停', {
        id: 'status',
        header: label('status'),
        cell: (context) => <StatusChip source={context.row.original} />,
      }),
      column.accessor((row) => row.interval_minutes, {
        id: 'interval',
        header: label('interval'),
        cell: (context) => <span className="whitespace-nowrap">{feedIntervalText(context.getValue())}</span>,
      }),
      column.accessor((row) => row.last_fetched_at ? Date.parse(row.last_fetched_at) || 0 : 0, {
        id: 'fetched',
        header: label('fetched'),
        cell: (context) => <span className="whitespace-nowrap tabular-nums">{context.row.original.last_fetched_at ? localTime(context.row.original.last_fetched_at) : '还没拉过'}</span>,
      }),
      column.accessor((row) => row.last_new_count, {
        id: 'fresh',
        header: label('fresh'),
        cell: (context) => <span className="whitespace-nowrap tabular-nums">{`${context.getValue()} 条`}</span>,
      }),
      column.display({
        id: 'actions',
        header: label('actions'),
        /* 行尾和关注列表表格同一个次序：启用开关、拉取键、移除键挨在一起。两枚键次级描边、
           刷新与垃圾桶字形，xs 那一档和开关一样高。 */
        cell: (context) => (
          <span className="flex items-center gap-1">
            <Switch aria-label={`启用 ${feedName(context.row.original)}`}
              isSelected={context.row.original.enabled} isDisabled={handlers.current.readOnly}
              onChange={(enabled) => handlers.current.toggle(context.row.original, enabled)} />
            <Button variant="secondary" size="xs" iconOnly leadingIcon={RiRefreshLine}
              aria-label={`拉取 ${feedName(context.row.original)}`}
              disabled={handlers.current.readOnly}
              {...busyProps(handlers.current.busy === `check-${context.row.original.id}`)}
              onClick={() => handlers.current.fetch(context.row.original)} />
            <Button variant="secondary" size="xs" iconOnly leadingIcon={RiDeleteBinLine}
              aria-label={`移除 ${feedName(context.row.original)}`}
              disabled={handlers.current.readOnly}
              onClick={() => handlers.current.remove(context.row.original)} />
          </span>
        ),
      }),
    ]);
  }, []);

  const rowSelection = useMemo<RowSelectionState>(
    () => Object.fromEntries([...selected].map((id) => [String(id), true])), [selected]);
  const table = useTable({
    features: feedTableFeatures,
    data: sources,
    columns,
    getRowId: (row) => String(row.id),
    state: { rowSelection, sorting, pagination: { pageIndex: win.page - 1, pageSize } },
    onSortingChange: (next) => { setSorting(next); setPage(1); },
    enableRowSelection: true,
    onRowSelectionChange: (updater) => {
      const next = typeof updater === 'function' ? updater(rowSelection) : updater;
      setSelected(new Set(Object.entries(next).filter(([, on]) => on).map(([id]) => Number(id))));
    },
  });

  if (!data) {
    return feeds.error
      ? <Note tone="error" title="打不开订阅源">{errorMessage(feeds.error)}</Note>
      : <Help>正在读订阅源。</Help>;
  }

  const check = () => void action.run('check', (signal) => checkFeeds(signal), () => void reload());
  const toggleRow = (id: number) => setSelected((now) => {
    const next = new Set(now);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const chosen = sources.filter((source) => selected.has(source.id));
  const failing = sources.filter((source) => source.last_error);

  return (
    <div className="flex flex-col gap-3">
      <AddFeed readOnly={readOnly} toast={toast} onAdded={() => void reload()} />
      <section aria-label="JAV 订阅列表"
        className={cardClass({ padding: 'none', className: 'flex flex-col gap-4 px-6 py-5 max-sm:px-4' })}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-title-2-medium text-text-primary">JAV 订阅源</h3>
        <span className="text-body-2-regular text-text-secondary">{`${sources.length.toLocaleString()} 个订阅源 · ${(Number(data.unread) || 0).toLocaleString()} 条未看`}</span>
        <Button onClick={check} disabled={readOnly} {...busyProps(action.busy === 'check')}>立即拉取</Button>
        <DataTableSort label="JAV 订阅源" sorting={sorting} onChange={(next) => { setSorting(next); setPage(1); }}
          options={['name', 'kind', 'origin', 'status', 'interval', 'fetched', 'fresh'].map((id) => ({ id, label: COLUMN_LABELS[id]! }))} />
      </div>

      {failing.length || action.error ? (
        <div className="flex flex-col gap-3">
          {/* 拉不动的源并成一条：每条源一行「源名：报错首行」，几条源同时坏也不把表格推出首屏；
              表格里那一行的状态徽章同时标着「拉取失败」。 */}
          {failing.length ? (
            <Note tone="error" title={`${failing.length.toLocaleString()} 条订阅源拉取失败`}
              extra={<ul className="flex flex-col gap-0.5 text-body-2-regular">
                {failing.map((source) => (
                  <li key={source.id} className="min-w-0 truncate" title={source.last_error || ''}>
                    {`${feedName(source)}：${errorHeadline(source.last_error || '') || '未说明原因'}`}
                  </li>
                ))}
              </ul>}>
              这些订阅源这一轮没有取到新条目。
            </Note>
          ) : null}
          {action.error ? <ErrorText>{action.error}</ErrorText> : null}
        </div>
      ) : null}

        <SelectionDock visible={chosen.length > 0} label="订阅源批量操作" count={`已选 ${chosen.length} 条订阅源`}>
          <Button variant="secondary" size="small" disabled={readOnly} {...busyProps(bulk.isPending)}
            onClick={() => bulk.mutate({ ids: chosen.map((row) => row.id), action: 'enabled' })}>启用</Button>
          <Button variant="secondary" size="small" disabled={readOnly} {...busyProps(bulk.isPending)}
            onClick={() => bulk.mutate({ ids: chosen.map((row) => row.id), action: 'paused' })}>暂停</Button>
          <Button variant="danger" size="small" disabled={readOnly} {...busyProps(bulk.isPending)}
            onClick={() => void confirmRemove(chosen,
              () => bulk.mutateAsync({ ids: chosen.map((row) => row.id), action: 'remove' }))}>移除</Button>
          <Button variant="ghost" size="small" onClick={() => setSelected(new Set())}>取消选择</Button>
        </SelectionDock>

      {sources.length ? (
        <DataTableFrame follow onRowClick={(key) => toggleRow(Number(key))}>
          <Table aria-label="JAV 订阅源" size="sm"
            sortDescriptor={sorting[0] ? { column: sorting[0].id, direction: sorting[0].desc ? 'descending' : 'ascending' } : undefined}
            onSortChange={(sort) => table.getColumn(String(sort.column))?.toggleSorting(sort.direction === 'descending')}>
            <TableHeader>
              {table.getHeaderGroups()[0]!.headers.map((header) => {
                const text = flexRender(header.column.columnDef.header, header.getContext());
                return (
                  <TableColumn key={header.id} id={header.id} isRowHeader={header.column.id === 'name'} allowsSorting={header.column.getCanSort()}>
                    {({ sortDirection }) => SILENT_COLUMNS.has(header.column.id) ? <VisuallyHidden>{text}</VisuallyHidden>
                      : <span className="flex items-center gap-0.5">{text}
                        {header.column.getCanSort() ? <span data-sort-indicator data-direction={sortDirection || undefined}><ChevronSortDown /></span> : null}
                      </span>}
                  </TableColumn>
                );
              })}
            </TableHeader>
            <TableBody>
              {table.getRowModel().rows.map((row) => (
                <TableRow key={row.id} id={row.id} data-follow-selected={row.getIsSelected() || undefined}>
                  {row.getAllCells().map((cell) => (
                    <TableCell key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </DataTableFrame>
      ) : (
        <EmptyState icon={RiRssLine} title="还没有订阅源" shell="plain">
          在上面按女优名添加，或在人物页点「订阅新作」。
        </EmptyState>
      )}
      {sources.length ? (
        <DataTableFooter label="JAV 订阅" total={win.total} unit="个订阅源"
          pagination={{ pageIndex: win.page - 1, pageSize }}
          onChange={(next) => { setPageSize(next.pageSize); setPage(next.pageIndex + 1); }} />
      ) : null}
      <footer className="-mx-6 -mb-5 flex flex-wrap items-center gap-2 border-t border-separator-border rounded-b-2xl bg-card-footer px-6 py-4 max-sm:-mx-4 max-sm:px-4">
        <span role="status" className="mr-auto text-body-2-regular text-text-secondary">
          {`未看 ${(Number(data.unread) || 0).toLocaleString()} · 已启用 ${sources.filter((source) => source.enabled).length.toLocaleString()} / ${sources.length.toLocaleString()}`}
        </span>
      </footer>
      </section>
    </div>
  );
}
