import { describe, expect, it } from 'vitest';

import {
  selectionDock, selectionSurface, setSelectionMode, syncSelectionSurface, toggleSelection,
  type SelectionState,
} from '../../src/react/catalog-grid/selection-controller';

const emptyState = (): SelectionState => ({
  selectMode: false, selectSurface: '', lastSelectedId: null, followLastSelectedId: null,
});
const sets = () => ({ selected: new Set<number>(), followSelected: new Set<number>() });

describe('选择范围与批量计数', () => {
  it('Shift 连选依照可见目录顺序，保留屏幕外选择', () => {
    const picked = sets();
    picked.selected.add(99);
    let state = toggleSelection(picked, emptyState(), 'catalog', [4, 2, 7, 1], 2);
    state = toggleSelection(picked, state, 'catalog', [4, 2, 7, 1], 1, true);
    expect([...picked.selected]).toEqual([99, 2, 7, 1]);
    expect(state).toMatchObject({ selectMode: true, selectSurface: 'catalog', lastSelectedId: 1 });
    expect(selectionDock(picked, '/', '', '')).toEqual({ count: 4, context: 'catalog', junkDismissed: false });
    state = toggleSelection(picked, state, 'catalog', [4, 2, 7, 1], 2);
    expect(picked.selected.has(2)).toBe(false);
    expect(state.lastSelectedId).toBe(2);
  });

  it('Shift 起点已不在当前列表时只选择目标，不引入隐藏卡片', () => {
    const picked = sets();
    const state = { ...emptyState(), lastSelectedId: 77 };
    const next = toggleSelection(picked, state, 'catalog', [2, 7], 7, true);
    expect([...picked.selected]).toEqual([7]);
    expect(next.lastSelectedId).toBe(7);
  });

  it('关注连选使用单独的选择集与起点', () => {
    const picked = sets();
    picked.selected.add(5);
    let state = toggleSelection(picked, emptyState(), 'follow', [3, 5, 8], 8);
    state = toggleSelection(picked, state, 'follow', [3, 5, 8], 3, true);
    expect([...picked.followSelected]).toEqual([8, 3, 5]);
    expect([...picked.selected]).toEqual([5]);
    expect(state.followLastSelectedId).toBe(3);
    expect(state.lastSelectedId).toBeNull();
    expect(selectionDock(picked, '/follow', '', 'trash')).toEqual({ count: 3, context: 'follow', junkDismissed: false });
  });

  it('目录内部导航沿用选择，跨关注或垃圾文件范围清空常驻 Set', () => {
    const picked = sets(), catalogSet = picked.selected, followSet = picked.followSelected;
    picked.selected.add(1); picked.followSelected.add(2);
    const state = { ...emptyState(), selectMode: true, selectSurface: 'catalog', lastSelectedId: 1, followLastSelectedId: 2 };
    expect(syncSelectionSurface(picked, state, '/creators/abc', true)).toEqual(state);
    expect(syncSelectionSurface(picked, state, '/trash', true)).toEqual(state);
    expect(syncSelectionSurface(picked, state, '/follow', true)).toEqual(emptyState());
    expect(picked.selected).toBe(catalogSet); expect(picked.followSelected).toBe(followSet);
    expect([...catalogSet, ...followSet]).toEqual([]);
    expect(selectionSurface('/junk-files')).toBe('junk');
  });

  it('关闭模式可保留选择，禁止选择的页面清空模式与两个起点', () => {
    const picked = sets(); picked.selected.add(1);
    const state = { ...emptyState(), selectMode: true, selectSurface: 'catalog', lastSelectedId: 1 };
    expect(setSelectionMode(picked, state, 'catalog', false).lastSelectedId).toBe(1);
    expect(picked.selected.size).toBe(1);
    expect(syncSelectionSurface(picked, state, '/settings/library', false)).toEqual(emptyState());
    expect(picked.selected.size).toBe(0);
  });

  it('回收站与已排除垃圾使用各自批量动作语境', () => {
    const picked = sets(); picked.selected.add(4);
    expect(selectionDock(picked, '/trash', '', 'trash')).toEqual({ count: 1, context: 'trash', junkDismissed: false });
    expect(selectionDock(picked, '/junk-files', '?view=dismissed', 'ads'))
      .toEqual({ count: 1, context: 'junk', junkDismissed: true });
    expect(selectionDock(picked, '/junk-files', '?view=unknown', 'ads').junkDismissed).toBe(false);
  });
});
