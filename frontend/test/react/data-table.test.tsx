import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { DataTable, type DataColumn } from '../../src/react/components/data-table';
import { buttonNamed, choose, click, mountRoot } from './render';

type Item = { id: number; name: string };
const columns: DataColumn<Item>[] = [{ id: 'name', label: '名字', value: (item) => item.name, render: (item) => item.name }];
const items = Array.from({ length: 23 }, (_, index) => ({ id: index + 1, name: `作品 ${23 - index}` }));
const checkbox = (host: ParentNode, name: string) => host.querySelector<HTMLInputElement>(`input[aria-label="${name}"]`);

function Fixture({ data = items, run, label = '测试作品' }: { data?: Item[]; run(ids: number[]): void; label?: string }) {
  const [selected, setSelected] = useState<string[]>([]);
  return <DataTable label={label} items={data} columns={columns} rowId={(item) => String(item.id)}
    rowLabel={(item) => item.name} selected={selected} onSelected={setSelected}
    actions={(chosen) => <button onClick={() => run(chosen.map((item) => item.id))}>处理所选</button>} />;
}

describe('共用数据表', () => {
  it('工具栏排序、方向和每页条数同步表头与全部行', async () => {
    const { host } = await mountRoot(<Fixture run={vi.fn()} />);
    const rows = () => [...host.querySelectorAll('tbody tr')];
    await choose(host.querySelector('[role="toolbar"] button[aria-haspopup="listbox"]'), '名字');
    expect(rows()[0]!.textContent).toContain('作品 1');
    expect(host.querySelector('th[data-key="name"]')?.getAttribute('aria-sort')).toBe('ascending');
    await click(host.querySelector('button[aria-label="测试作品改为降序"]'));
    expect(rows()[0]!.textContent).toContain('作品 23');
    expect(host.querySelector('th[data-key="name"]')?.getAttribute('aria-sort')).toBe('descending');
    await choose(host.querySelector('[data-table-pagination] button[aria-haspopup="listbox"]'), '每页 50 条');
    expect(rows()).toHaveLength(23);
    expect(host.textContent).toContain('1–23 / 23 条记录');
  });

  it('排序作用在全集，选择跨页保留，批量范围排除消失的行', async () => {
    const run = vi.fn();
    const { host, rerender } = await mountRoot(<Fixture run={run} />);
    const rows = () => [...host.querySelectorAll('tbody tr')];
    expect(rows()).toHaveLength(20);
    expect(buttonNamed('处理所选', host)).toBeNull();
    await click(checkbox(host, '选择 作品 23'));
    await click(buttonNamed('下一页', host));
    await click(checkbox(host, '选择 作品 3'));
    await click(buttonNamed('处理所选', host));
    expect(run).toHaveBeenLastCalledWith([1, 21]);
    await click(host.querySelector('th[data-key="name"]'));
    expect(rows()[0]!.textContent).toContain('作品 1');
    expect(rows()[19]!.textContent).toContain('作品 20');
    expect(host.textContent).toContain('已选 2 条记录');
    await click(buttonNamed('下一页', host));
    await rerender(<Fixture data={[items[0]!, items[1]!]} run={run} />);
    expect(rows()).toHaveLength(2);
    expect(host.textContent).toContain('1–2 / 2 条记录');
    await click(buttonNamed('处理所选', host));
    expect(run).toHaveBeenLastCalledWith([1]);
    await click(buttonNamed('取消选择', host));
    expect(host.querySelector('[data-selection-dock]:not([aria-hidden="true"])')).toBeNull();
  });

  it('全选覆盖所有页，再次全选取消整批选择', async () => {
    const run = vi.fn();
    const { host } = await mountRoot(<Fixture run={run} />);
    await click(checkbox(host, '全选测试作品'));
    await click(buttonNamed('处理所选', host));
    expect(run).toHaveBeenLastCalledWith(items.map((item) => item.id));
    await click(buttonNamed('下一页', host));
    expect(checkbox(host, '选择 作品 3')?.checked).toBe(true);
    await click(checkbox(host, '全选测试作品'));
    expect(host.querySelector('[data-selection-dock]:not([aria-hidden="true"])')).toBeNull();
  });

  it('同页另一张表开始选择时只保留当前表的批量操作', async () => {
    const { host } = await mountRoot(<><Fixture label="第一张表" run={vi.fn()} /><Fixture label="第二张表" run={vi.fn()} /></>);
    const first = host.querySelector('[aria-label="第一张表"]')!;
    const second = host.querySelector('[aria-label="第二张表"]')!;
    await click(checkbox(first, '选择 作品 23'));
    expect(checkbox(first, '选择 作品 23')?.checked).toBe(true);
    await click(checkbox(second, '选择 作品 23'));
    expect(checkbox(first, '选择 作品 23')?.checked).toBe(false);
    expect(checkbox(second, '选择 作品 23')?.checked).toBe(true);
    expect(host.querySelectorAll('[data-selection-dock]:not([aria-hidden="true"])')).toHaveLength(1);
  });
});
