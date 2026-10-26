/* 创作者别名：同一个人在几个站上用几个名字，合并之后按一位创作者归组。
 *
 * 待合并是带依据的候选，合并永远由人点：服务端不自行升级，页面也不替他勾。 */
import { useMemo, useRef, useState } from 'react';
import { RiCloseLine, RiUserLine } from '@remixicon/react';
import { useMutation } from '@tanstack/react-query';
import {
  createColumnHelper, createPaginatedRowModel, createSortedRowModel, flexRender,
  rowPaginationFeature, rowSelectionFeature, rowSortingFeature, tableFeatures, useTable,
  type RowSelectionState, type SortingState,
} from '@tanstack/react-table';
import { confirmModal } from '@peach/legacy/ui';

import { Button } from '@/components/base/buttons/button';
import { Checkbox } from '@/components/base/checkbox/checkbox';
import { Input } from '@/components/base/input/input';
import {
  Table, TableBody, TableCell, TableColumn, TableHeader, TableRow,
} from '@/components/base/table/table';

import { errorMessage } from '../../api';
import { cardClass } from '../components/card';
import { DataTableFrame } from '../components/data-table-frame';
import { DataTableFooter, DataTableSort } from '../components/table-controls';
import { SelectionDock } from '../components/selection-dock';
import { useTableSelection } from '../components/use-table-selection';
import { ChevronSortDown } from '@/components/foundations/icons/chevrons';
import { EmptyState } from '../components/empty-state';
import { ErrorText, FieldLabel } from '../settings/section';
import { busyProps } from '../settings/use-action';
import {
  addAlias, reloadFollowManage, removeAlias,
  type AliasGroup, type AliasSuggestion, type FollowSource,
} from './follow-manage';
import { AuthorAvatar } from './source-view';

const REMOVE_BODY = (alias: string) => `将移除别名「${alias}」，对应来源恢复为独立创作者组。`;
const suggestionFeatures = tableFeatures({ rowSelectionFeature, rowSortingFeature, rowPaginationFeature,
  sortedRowModel: createSortedRowModel(), paginatedRowModel: createPaginatedRowModel() });
const savedFeatures = suggestionFeatures;

export interface AliasManagerProps {
  groups: AliasGroup[];
  suggestions: AliasSuggestion[];
  sources?: FollowSource[];
  readOnly: boolean;
  toast(message: string): void;
}

/** 一条待合并的身份：一个别名只可能归进一个规范名，所以用别名本身当键。 */
const suggestionKey = (item: AliasSuggestion) => item.alias;

function authorSources(sources: FollowSource[], name: string, canonicalKey?: string) {
  /* 建过档的作者键是 `entity:<id>`，别名表上记的仍是名字键：按名字键找不到就按名字认。 */
  const expected = canonicalKey ? `name:${canonicalKey}` : '';
  const anchor = (expected && sources.find((source) => source.author_key === expected))
    || sources.find((source) => source.author_name === name || source.entity_name === name);
  return anchor ? sources.filter((source) => source.author_key === anchor.author_key) : [];
}

function AuthorIdentity({ sources, name, canonicalKey }: {
  sources: FollowSource[]; name: string; canonicalKey?: string;
}) {
  return (
    <span className="flex items-center gap-2">
      <AuthorAvatar group={authorSources(sources, name, canonicalKey)} name={name} />
      <b className="text-body-medium">{name}</b>
    </span>
  );
}

export function AliasManager({ groups, suggestions, sources = [], readOnly, toast }: AliasManagerProps) {
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [savedSelection, setSavedSelection] = useState<RowSelectionState>({});
  const activateSuggestion = useTableSelection(() => setRowSelection({}));
  const activateSaved = useTableSelection(() => setSavedSelection({}));
  const [sorting, setSorting] = useState<SortingState>([]);
  const [savedSorting, setSavedSorting] = useState<SortingState>([]);
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 20 });
  const [savedPagination, setSavedPagination] = useState({ pageIndex: 0, pageSize: 20 });
  const suggestionPage = { ...pagination, pageIndex: Math.min(pagination.pageIndex, Math.max(0, Math.ceil(suggestions.length / pagination.pageSize) - 1)) };
  const savedPage = { ...savedPagination, pageIndex: Math.min(savedPagination.pageIndex, Math.max(0, Math.ceil(groups.length / savedPagination.pageSize) - 1)) };
  const [problem, setProblem] = useState('');
  const [canonical, setCanonical] = useState('');
  const [alias, setAlias] = useState('');

  /* 合并按顺序一条一条发：一次失败要报出「还有几组没合」，而并发发出去的话，说不清
     停在了哪里。 */
  const merge = useMutation({
    mutationFn: async (items: AliasSuggestion[]) => {
      let done = 0;
      for (const item of items) {
        try {
          await addAlias(item.canonical, item.alias);
        } catch (cause) {
          /* 半路断了要说清停在哪儿：已经合上的那几组先落回列表里去掉，再点一次只发剩下的。 */
          if (done) void reloadFollowManage();
          throw new Error(`${errorMessage(cause)}（还有 ${items.length - done} 组没有合并）`);
        }
        done += 1;
      }
      return { done, items };
    },
    onSuccess: (result) => {
      setProblem('');
      setRowSelection({});
      void reloadFollowManage();
      toast(`已合并 ${result.done} 组创作者别名`);
    },
    onError: (cause) => setProblem(errorMessage(cause)),
  });

  const save = useMutation({
    mutationFn: (pair: { canonical: string; alias: string }) => addAlias(pair.canonical, pair.alias),
    onSuccess: () => {
      setProblem('');
      setCanonical('');
      setAlias('');
      void reloadFollowManage();
      toast('已保存创作者别名');
    },
    onError: (cause) => setProblem(errorMessage(cause)),
  });

  const drop = useMutation({
    mutationFn: async (names: string | string[]) => {
      const pending = typeof names === 'string' ? [names] : names;
      let done = 0;
      try {
        for (const name of pending) { await removeAlias(name); done += 1; }
      } catch (cause) {
        throw new Error(`${errorMessage(cause)}（已移除 ${done} 个别名，还有 ${pending.length - done} 个未移除）`);
      } finally { void reloadFollowManage(); }
    },
    onSuccess: () => { setProblem(''); setSavedSelection({}); toast('已移除创作者别名') },
    onError: (cause) => setProblem(errorMessage(cause)),
  });

  const mergeSome = (items: AliasSuggestion[], all: boolean) => {
    if (!items.length) return;
    void confirmModal({
      title: all ? '合并全部创作者别名' : '合并所选创作者别名',
      body: `将合并以下 ${items.length} 组创作者：${items.map(
        (item) => `「${item.alias}」归入「${item.canonical}」`).join('；')}。`,
      confirmLabel: all ? '合并全部别名' : '合并所选别名',
      onConfirm: () => merge.mutateAsync(items),
    });
  };

  const removeSome = (name: string) => void confirmModal({
    title: '移除创作者别名', body: REMOVE_BODY(name),
    confirmLabel: '移除创作者别名',
    onConfirm: () => drop.mutateAsync(name),
  });
  const handlers = useRef({ sources, readOnly, merging: merge.isPending,
    removing: drop.isPending, merge: mergeSome, remove: removeSome });
  handlers.current = { sources, readOnly, merging: merge.isPending,
    removing: drop.isPending, merge: mergeSome, remove: removeSome };

  const suggestionColumns = useMemo(() => {
    const column = createColumnHelper<typeof suggestionFeatures, AliasSuggestion>();
    return column.columns([
      column.display({
        id: 'select',
        header: (context) => (
          <Checkbox slot={null} isSelected={context.table.getIsAllRowsSelected()}
            isIndeterminate={context.table.getSelectedRowModel().rows.length > 0 && !context.table.getIsAllRowsSelected()}
            aria-label="全选待合并别名"
            onChange={(on) => context.table.toggleAllRowsSelected(on)} />
        ),
        cell: (context) => (
          <Checkbox slot={null} isSelected={context.row.getIsSelected()}
            aria-label={`选择别名 ${context.row.original.alias}，归入 ${context.row.original.canonical}`}
            onChange={(on) => context.row.toggleSelected(on)} />
        ),
      }),
      column.accessor('canonical', {
        header: '规范创作者',
        cell: (context) => <AuthorIdentity sources={handlers.current.sources} name={context.getValue()} />,
      }),
      column.accessor('alias', { header: '平台别名', cell: (context) => context.getValue() }),
      column.accessor('evidence', {
        header: '依据',
        cell: (context) => <span className="text-body-2-regular text-text-secondary">{context.getValue()}</span>,
      }),
      column.display({
        id: 'actions', header: '操作',
        cell: (context) => (
          <Button variant="secondary" size="small" disabled={handlers.current.readOnly}
            {...busyProps(handlers.current.merging)}
            onClick={() => handlers.current.merge([context.row.original], false)}>合并</Button>
        ),
      }),
    ]);
  }, []);
  const savedColumns = useMemo(() => {
    const column = createColumnHelper<typeof savedFeatures, AliasGroup>();
    return column.columns([
      column.display({ id: 'select', header: ({ table }) => (
        <Checkbox slot={null} aria-label="全选已保存别名" isSelected={table.getIsAllRowsSelected()}
          isIndeterminate={table.getIsSomeRowsSelected()} onChange={(on) => table.toggleAllRowsSelected(on)} />
      ), cell: ({ row }) => (
        <Checkbox slot={null} aria-label={`选择已保存别名 ${row.original.canonical_name}`}
          isSelected={row.getIsSelected()} onChange={(on) => row.toggleSelected(on)} />
      ) }),
      column.accessor('canonical_name', {
        id: 'canonical', header: '规范创作者',
        cell: (context) => (
          <AuthorIdentity sources={handlers.current.sources} name={context.getValue()}
            canonicalKey={context.row.original.canonical_key} />
        ),
      }),
      column.accessor('aliases', {
        header: '平台别名',
        sortFn: (a, b) => a.original.aliases.map((item) => item.name).join(' ').localeCompare(b.original.aliases.map((item) => item.name).join(' '), 'zh-CN'),
        cell: (context) => (
          <span className="flex flex-wrap items-center gap-1.5">
            {context.getValue().map((item) => (
              <span key={item.key}
                className="inline-flex items-center gap-1 rounded-md bg-background-secondary-default px-1.5 py-1 text-caption-1-medium text-text-secondary">
                {item.name}
                <Button variant="ghost" size="xs" iconOnly leadingIcon={RiCloseLine}
                  aria-label={`移除别名 ${item.name}`} disabled={handlers.current.readOnly}
                  {...busyProps(handlers.current.removing)}
                  onClick={() => handlers.current.remove(item.name)} />
              </span>
            ))}
          </span>
        ),
      }),
    ]);
  }, []);
  const suggestionTable = useTable({
    features: suggestionFeatures,
    data: suggestions,
    columns: suggestionColumns,
    getRowId: suggestionKey,
    state: { rowSelection, sorting, pagination: suggestionPage },
    onRowSelectionChange: (next) => { activateSuggestion(); setRowSelection(next); },
    onSortingChange: (next) => { setSorting(next); setPagination((now) => ({ ...now, pageIndex: 0 })); },
    onPaginationChange: setPagination,
    enableRowSelection: true,
  });
  const savedTable = useTable({
    features: savedFeatures,
    data: groups,
    columns: savedColumns,
    getRowId: (group) => group.canonical_key,
    state: { rowSelection: savedSelection, sorting: savedSorting, pagination: savedPage },
    onRowSelectionChange: (next) => { activateSaved(); setSavedSelection(next); }, enableRowSelection: true,
    onSortingChange: (next) => { setSavedSorting(next); setSavedPagination((now) => ({ ...now, pageIndex: 0 })); },
    onPaginationChange: setSavedPagination,
  });
  const chosen = suggestionTable.getSelectedRowModel().rows.map((row) => row.original);
  const savedNames = savedTable.getSelectedRowModel().rows.flatMap((row) => row.original.aliases.map((item) => item.name));
  const busy = busyProps(merge.isPending);

  return (
    <div className={cardClass({ padding: 'none', className: 'flex flex-col gap-4 px-6 py-5 max-sm:px-4' })}>
      <h3 className="text-title-2-medium text-text-primary">创作者别名</h3>

      <div className="flex flex-col gap-2">
        <FieldLabel>手动添加别名</FieldLabel>
        <div className="flex flex-wrap items-end gap-2">
          <Input aria-label="规范创作者名" placeholder="规范创作者名" value={canonical}
            onChange={setCanonical} isDisabled={readOnly} className="w-56" />
          <Input aria-label="平台别名" placeholder="平台别名" value={alias}
            onChange={setAlias} isDisabled={readOnly} className="w-56" />
          <Button variant="primary" disabled={readOnly || !canonical.trim() || !alias.trim()}
            {...busyProps(save.isPending)}
            onClick={() => save.mutate({ canonical: canonical.trim(), alias: alias.trim() })}>
            保存别名
          </Button>
        </div>
      </div>

      {problem ? <ErrorText>{problem}</ErrorText> : null}

      {suggestions.length ? (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <FieldLabel>待合并</FieldLabel>
            <span className="mr-auto text-body-2-regular text-text-secondary">
              {`${suggestions.length} 组`}
            </span>
            <Button variant="primary" size="small" disabled={readOnly} {...busy}
              onClick={() => mergeSome(suggestions, true)}>{`全部合并（${suggestions.length}）`}</Button>
            <DataTableSort label="待合并创作者别名" sorting={sorting}
              options={[{ id: 'canonical', label: '规范创作者' }, { id: 'alias', label: '平台别名' }, { id: 'evidence', label: '依据' }]}
              onChange={(next) => { setSorting(next); setPagination((now) => ({ ...now, pageIndex: 0 })); }} />
          </div>
          <SelectionDock label="待合并别名批量操作" visible={chosen.length > 0} count={`已选 ${chosen.length} 组`}>
            <Button variant="secondary" size="small" disabled={readOnly} {...busy}
              onClick={() => mergeSome(chosen, false)}>{`合并所选（${chosen.length}）`}</Button>
            <Button variant="ghost" size="small" onClick={() => setRowSelection({})}>取消选择</Button>
          </SelectionDock>
          <DataTableFrame follow>
            <Table aria-label="待合并创作者别名" size="sm"
              sortDescriptor={sorting[0] ? { column: sorting[0].id, direction: sorting[0].desc ? 'descending' : 'ascending' } : undefined}
              onSortChange={(sort) => suggestionTable.getColumn(String(sort.column))?.toggleSorting(sort.direction === 'descending')}>
            <TableHeader>
              {suggestionTable.getHeaderGroups()[0]!.headers.map((header) => (
                <TableColumn key={header.id} id={header.id} isRowHeader={header.column.id === 'alias'} allowsSorting={header.column.getCanSort()}>
                  {({ sortDirection }) => <span className="flex items-center gap-0.5">
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    {header.column.getCanSort() ? <span data-sort-indicator data-direction={sortDirection || undefined}><ChevronSortDown /></span> : null}
                  </span>}
                </TableColumn>
              ))}
            </TableHeader>
            <TableBody>
              {suggestionTable.getRowModel().rows.map((row) => (
                <TableRow key={row.id} id={row.id} data-board-selected={row.getIsSelected() || undefined}>
                  {row.getAllCells().map((cell) => (
                    <TableCell key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
            </Table>
          </DataTableFrame>
          <DataTableFooter label="待合并创作者别名" unit="组" total={suggestions.length} pagination={suggestionPage} onChange={setPagination} />
        </div>
      ) : null}

      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <FieldLabel>已保存别名</FieldLabel>
          <span className="mr-auto text-body-2-regular text-text-secondary">{`${groups.length} 组`}</span>
          <DataTableSort label="已保存创作者别名" sorting={savedSorting}
            options={[{ id: 'canonical', label: '规范创作者' }, { id: 'aliases', label: '平台别名' }]}
            onChange={(next) => { setSavedSorting(next); setSavedPagination((now) => ({ ...now, pageIndex: 0 })); }} />
        </div>
        <SelectionDock label="已保存别名批量操作" visible={savedNames.length > 0} count={`已选 ${savedNames.length} 个别名`}>
          <Button variant="secondary" size="small" disabled={readOnly} {...busyProps(drop.isPending)}
            onClick={() => void confirmModal({ title: '移除所选创作者别名',
              body: `将移除以下 ${savedNames.length} 个别名，对应来源恢复为独立创作者组。`,
              items: savedNames,
              confirmLabel: '移除所选别名', onConfirm: () => drop.mutateAsync(savedNames) })}>
            {`移除所选别名（${savedNames.length}）`}
          </Button>
          <Button variant="ghost" size="small" onClick={() => setSavedSelection({})}>取消选择</Button>
        </SelectionDock>
        {groups.length ? (
          <>
          <DataTableFrame follow>
            <Table aria-label="已保存创作者别名" size="sm"
              sortDescriptor={savedSorting[0] ? { column: savedSorting[0].id, direction: savedSorting[0].desc ? 'descending' : 'ascending' } : undefined}
              onSortChange={(sort) => savedTable.getColumn(String(sort.column))?.toggleSorting(sort.direction === 'descending')}>
            <TableHeader>
              {savedTable.getHeaderGroups()[0]!.headers.map((header) => (
                <TableColumn key={header.id} id={header.id} isRowHeader={header.column.id === 'canonical'} allowsSorting={header.column.getCanSort()}>
                  {({ sortDirection }) => <span className="flex items-center gap-0.5">
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    {header.column.getCanSort() ? <span data-sort-indicator data-direction={sortDirection || undefined}><ChevronSortDown /></span> : null}
                  </span>}
                </TableColumn>
              ))}
            </TableHeader>
            <TableBody>
              {savedTable.getRowModel().rows.map((row) => (
                <TableRow key={row.id} id={row.id} data-board-selected={row.getIsSelected() || undefined}>
                  {row.getAllCells().map((cell) => (
                    <TableCell key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
            </Table>
          </DataTableFrame>
          <DataTableFooter label="已保存创作者别名" unit="组" total={groups.length} pagination={savedPage} onChange={setSavedPagination} />
          </>
        ) : (
          <EmptyState shell="plain" icon={RiUserLine} title="还没有保存创作者别名">
            填写规范创作者名和平台别名以添加。
          </EmptyState>
        )}
      </div>
    </div>
  );
}
