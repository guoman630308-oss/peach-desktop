import { useMemo, useState, type ReactNode } from 'react';
import {
  createColumnHelper, createPaginatedRowModel, createSortedRowModel, flexRender,
  rowPaginationFeature, rowSelectionFeature, rowSortingFeature, tableFeatures, useTable,
  type RowData, type RowSelectionState, type SortingState,
} from '@tanstack/react-table';
import { Button } from '@/components/base/buttons/button';
import { Checkbox } from '@/components/base/checkbox/checkbox';
import { Table, TableBody, TableCell, TableColumn, TableHeader, TableRow } from '@/components/base/table/table';
import { ChevronSortDown } from '@/components/foundations/icons/chevrons';
import { DataTableFrame } from './data-table-frame';
import { DataTableFooter, DataTableSort } from './table-controls';
import { SelectionDock } from './selection-dock';
import { cardClass } from './card';
import { useTableSelection } from './use-table-selection';

const features = tableFeatures({
  rowSortingFeature, rowSelectionFeature, rowPaginationFeature,
  sortedRowModel: createSortedRowModel(), paginatedRowModel: createPaginatedRowModel(),
});

export interface DataColumn<T> {
  id: string;
  label: string;
  value?(item: T): string | number;
  render(item: T): ReactNode;
}

interface DataTableProps<T> {
  label: string;
  items: T[];
  columns: DataColumn<T>[];
  rowId(item: T): string;
  rowLabel(item: T): string;
  selected: string[];
  onSelected(ids: string[]): void;
  actions(items: T[], clear: () => void): ReactNode;
  title?: string;
  titleId?: string;
  description?: ReactNode;
  footerActions?: ReactNode;
  panel?: boolean;
  unit?: string;
}

/** 数据表共用排序、跨页选择与分页；业务动作只接收仍在清单里的所选行。 */
export function DataTable<T extends RowData>({ label, items, columns, rowId, rowLabel, selected, onSelected, actions,
  title = label, titleId, description, footerActions, panel = true, unit = '条记录' }: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 20 });
  const activateSelection = useTableSelection(() => onSelected([]));
  const pages = Math.max(1, Math.ceil(items.length / pagination.pageSize));
  const pageIndex = Math.min(pagination.pageIndex, pages - 1);
  const rowSelection: RowSelectionState = Object.fromEntries(selected.map((id) => [id, true]));
  const definitions = useMemo(() => {
    const helper = createColumnHelper<typeof features, T>();
    return helper.columns([
      helper.display({ id: 'select', header: ({ table }) => (
        <Checkbox slot={null} aria-label={`全选${label}`} isSelected={table.getIsAllRowsSelected()}
          isIndeterminate={table.getIsSomeRowsSelected()} onChange={(on) => table.toggleAllRowsSelected(on)} />
      ), cell: ({ row }) => (
        <Checkbox slot={null} aria-label={`选择 ${rowLabel(row.original)}`} isSelected={row.getIsSelected()}
          onChange={(on) => row.toggleSelected(on)} />
      ) }),
      ...columns.map((column) => helper.accessor((item: T) => column.value?.(item) ?? '', {
        id: column.id, header: column.label, enableSorting: Boolean(column.value),
        sortFn: (a, b) => {
          const first = column.value!(a.original), second = column.value!(b.original);
          return typeof first === 'number' && typeof second === 'number'
            ? first - second : String(first).localeCompare(String(second), 'zh-CN', { numeric: true });
        },
        cell: ({ row }) => column.render(row.original),
      })),
    ]);
  }, [columns, label, rowLabel]);
  const table = useTable({
    features, data: items, columns: definitions, getRowId: rowId,
    state: { sorting, rowSelection, pagination: { ...pagination, pageIndex } },
    onSortingChange: (updater) => { setSorting(updater); setPagination((now) => ({ ...now, pageIndex: 0 })); },
    onPaginationChange: setPagination, enableRowSelection: true,
    onRowSelectionChange: (updater) => {
      const next = typeof updater === 'function' ? updater(rowSelection) : updater;
      if (Object.values(next).some(Boolean)) activateSelection();
      onSelected(items.filter((item) => next[rowId(item)]).map(rowId));
    },
  });
  const chosen = table.getSelectedRowModel().rows.map((row) => row.original);
  return (
    <div data-data-table-panel={panel || undefined} className={panel
      ? cardClass({ padding: 'none', bordered: 'line', className: 'flex flex-col gap-4 px-6 py-5 max-sm:px-4' })
      : 'flex min-w-0 flex-col gap-4'}>
      <div className="flex flex-wrap items-center gap-2" role="toolbar" aria-label={`${label}工具栏`}>
        <h3 id={titleId} className="mr-auto text-title-2-medium text-text-primary">{title}</h3>
        <span className="text-body-2-regular tabular-nums text-text-secondary">
          {`${items.length.toLocaleString()} ${unit}`}
        </span>
        <DataTableSort label={label} options={columns.filter((column) => column.value).map((column) => ({ id: column.id, label: column.label }))}
          sorting={sorting} onChange={(next) => { setSorting(next); setPagination((now) => ({ ...now, pageIndex: 0 })); }} />
      </div>
      {description ? <p className="text-body-2-regular text-text-secondary">{description}</p> : null}
      <SelectionDock label={`${label}批量操作`} visible={chosen.length > 0} count={`已选 ${chosen.length.toLocaleString()} ${unit}`}>
        {actions(chosen, () => onSelected([]))}
        <Button variant="ghost" size="small" onClick={() => onSelected([])}>取消选择</Button>
      </SelectionDock>
      <DataTableFrame follow onRowClick={(id) => table.getRow(id)?.toggleSelected()}>
        <Table aria-label={label} size="sm"
          sortDescriptor={sorting[0] ? { column: sorting[0].id, direction: sorting[0].desc ? 'descending' : 'ascending' } : undefined}
          onSortChange={(sort) => table.getColumn(String(sort.column))?.toggleSorting(sort.direction === 'descending')}>
          <TableHeader>
            {table.getHeaderGroups()[0]!.headers.map((header, index) => (
              <TableColumn key={header.id} id={header.id} isRowHeader={index === 1} allowsSorting={header.column.getCanSort()}>
                {({ sortDirection }) => <span className="flex items-center gap-0.5">
                  {flexRender(header.column.columnDef.header, header.getContext())}
                  {header.column.getCanSort() ? <span data-sort-indicator data-direction={sortDirection || undefined}><ChevronSortDown /></span> : null}
                </span>}
              </TableColumn>
            ))}
          </TableHeader>
          <TableBody renderEmptyState={() => '没有记录'}>
            {table.getRowModel().rows.map((row) => (
              <TableRow key={row.id} id={row.id} data-board-selected={row.getIsSelected() || undefined}>
                {row.getAllCells().map((cell) => <TableCell key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</TableCell>)}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </DataTableFrame>
      <DataTableFooter label={label} total={items.length} pagination={{ ...pagination, pageIndex }} onChange={setPagination} unit={unit} />
      {footerActions ? <div className="flex flex-wrap items-center justify-end gap-2 border-t border-separator-border pt-4">{footerActions}</div> : null}
    </div>
  );
}
