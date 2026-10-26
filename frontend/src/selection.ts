/** 选择仅使用当前展示顺序，业务身份与默认选择归调用方。 */
export function selectRange<T>(selected: Set<T>, order: T[], anchor: T | null, key: T, range: boolean, checked = range || !selected.has(key)): T {
  const first = anchor === null ? -1 : order.indexOf(anchor), last = order.indexOf(key);
  if (range && first >= 0 && last >= 0) order.slice(Math.min(first,last),Math.max(first,last)+1).forEach(id=>selected.add(id));
  else if (checked) selected.add(key); else selected.delete(key);
  return key;
}
export function selectionSummary<T>(selected: Set<T>, order: T[]) {
  const count = order.filter(key=>selected.has(key)).length;
  return { count, all: count > 0 && count === order.length, mixed: count > 0 && count < order.length };
}
export function selectGroup<T>(selected: Set<T>, order: T[], checked: boolean) {
  order.forEach(key=>checked ? selected.add(key) : selected.delete(key));
}
export function syncSelectionToolbar({count, label, all, summary, actions, locked=false}: {
  count?: HTMLElement | null; label: string; all?: HTMLInputElement | null;
  summary: {count:number;all:boolean;mixed:boolean}; actions: Iterable<HTMLButtonElement>; locked?: boolean;
}) {
  if(count)count.textContent=label;
  if(all){all.checked=summary.all;all.indeterminate=summary.mixed}
  for(const action of actions)action.disabled=!summary.count||locked;
}
