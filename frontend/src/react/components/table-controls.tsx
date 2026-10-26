import { RiArrowDownLine, RiArrowUpLine } from '@remixicon/react';
import type { SortingState } from '@tanstack/react-table';
import { Button } from '@/components/base/buttons/button';
import { Select, SelectItem } from '@/components/base/select/select';
import { Pagination } from './table-pagination';

export function DataTableSort({ label, options, sorting, onChange }: {
  label: string; options: { id: string; label: string }[]; sorting: SortingState;
  onChange(next: SortingState): void;
}) {
  const current = sorting[0];
  return <span className="flex items-center gap-2">
    <Select aria-label={`${label}排序`} selectedKey={current?.id ?? 'default'}
      onSelectionChange={(key) => {
        if (key !== null) onChange(key === 'default' ? [] : [{ id: String(key), desc: current?.desc ?? false }]);
      }}>
      <SelectItem id="default">默认顺序</SelectItem>
      {options.map((option) => <SelectItem key={option.id} id={option.id}>{option.label}</SelectItem>)}
    </Select>
    <Button variant="secondary" iconOnly disabled={!current}
      leadingIcon={current?.desc ? RiArrowDownLine : RiArrowUpLine}
      aria-label={`${label}改为${current?.desc ? '升序' : '降序'}`}
      onClick={() => { if (current) onChange([{ ...current, desc: !current.desc }]); }} />
  </span>;
}

/** 范围、每页条数和页码沿用关注列表的同一行布局。 */
export function DataTableFooter({ label, total, pagination, onChange, unit = '条记录', pageUnit = '条' }: {
  label: string; total: number; pagination: { pageIndex: number; pageSize: number };
  onChange(next: { pageIndex: number; pageSize: number }): void;
  unit?: string; pageUnit?: string;
}) {
  const pages = Math.max(1, Math.ceil(total / pagination.pageSize));
  const pageIndex = Math.min(pagination.pageIndex, pages - 1);
  const start = total ? pageIndex * pagination.pageSize + 1 : 0;
  const end = Math.min((pageIndex + 1) * pagination.pageSize, total);
  return <div data-table-pagination className="flex flex-wrap items-center justify-between gap-3">
    <span className="text-body-2-regular tabular-nums text-text-secondary">
      {`${start.toLocaleString()}–${end.toLocaleString()} / ${total.toLocaleString()} ${unit}`}
    </span>
    <Select aria-label="每页显示数量" size="sm" selectedKey={String(pagination.pageSize)}
      onSelectionChange={(key) => { if (key !== null) onChange({ pageIndex: 0, pageSize: Number(key) }); }}>
      {[10, 20, 50, 100].map((size) => <SelectItem key={size} id={String(size)}>{`每页 ${size} ${pageUnit}`}</SelectItem>)}
    </Select>
    <Pagination label={`${label}分页`} page={pageIndex + 1} pages={pages}
      onPage={(page) => onChange({ ...pagination, pageIndex: page - 1 })} />
  </div>;
}
