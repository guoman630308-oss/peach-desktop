/* 左侧导航的顺序：直接拖动排序，逐行上移、下移、隐藏，再从下面那张页面清单里加回来。
 *
 * 写入与侧栏那一列的拖动共用 `sidebar/sidebar-order.ts`：先落本地那份（侧栏按 store 的通知当场
 * 重排），再写 `/api/settings`。 */
import { useRef, useState, type DragEvent, type KeyboardEvent } from 'react';

import { dismissMenu, presentMenu } from '@peach/legacy/ui';

import { moveSidebarKey, useCommitSidebarOrder } from '../sidebar/sidebar-order';
import { Icon } from './icon';
import type { SettingsPanelHost } from './settings-panel-api';

/* 首页那一项的键是空串；空串写进属性和 dataTransfer 都等于没写，给它一个占位。 */
const HOME = '__home__';
const optionKey = (key: string) => key === '' ? HOME : key;

export function SidebarOrder({ host }: { host: SettingsPanelHost }) {
  const commit = useCommitSidebarOrder(host.store);
  const order = host.store.value.sidebarOrder;
  const byKey = new Map(host.navCatalog.map((item) => [item[0], item]));
  const visible = order.map((key) => byKey.get(key)).filter((item) => item !== undefined);
  const available = host.navCatalog.filter(([key]) => !order.includes(key));
  const [picked, setPicked] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [dragging, setDragging] = useState<string | null>(null);
  const [drop, setDrop] = useState<{ key: string; after: boolean } | null>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const menu = useRef<HTMLDivElement | null>(null);

  const move = (key: string, target: string, after: boolean) => {
    const next = moveSidebarKey(order, key, target, after);
    if (next) commit(next);
  };
  const step = (key: string, delta: number) => {
    const from = order.indexOf(key), to = from + delta;
    if (from < 0 || to < 0 || to >= order.length) return;
    const next = [...order];
    [next[from], next[to]] = [next[to], next[from]];
    commit(next);
  };

  /* 清单里当前挑中的那一项；挑中的那一项被加进侧栏之后退回清单第一项。 */
  const chosen = available.find(([key]) => optionKey(key) === picked) ?? available[0];
  const closeMenu = () => {
    if (menu.current) dismissMenu(menu.current);
    setMenuOpen(false);
  };
  const toggleMenu = () => {
    const node = menu.current;
    if (!node) return;
    const opening = !menuOpen;
    if (opening) presentMenu(node); else dismissMenu(node);
    setMenuOpen(opening);
    if (opening) node.querySelector<HTMLElement>('[aria-selected="true"]')?.focus();
  };
  const optionKeys = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'Escape') { event.preventDefault(); closeMenu(); trigger.current?.focus(); return }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    event.preventDefault();
    const all = [...(menu.current?.querySelectorAll<HTMLElement>('[role="option"]') ?? [])];
    const at = all.indexOf(event.currentTarget);
    all[(at + (event.key === 'ArrowDown' ? 1 : -1) + all.length) % all.length]?.focus();
  };

  const clearDrag = () => { setDragging(null); setDrop(null) };
  const rowDrag = (key: string) => ({
    draggable: true,
    onDragStart: (event: DragEvent<HTMLDivElement>) => {
      setDragging(key);
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', optionKey(key));
    },
    onDragOver: (event: DragEvent<HTMLDivElement>) => {
      if (dragging === null || dragging === key) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      const box = event.currentTarget.getBoundingClientRect();
      const after = event.clientY > box.top + event.currentTarget.offsetHeight / 2;
      if (drop?.key !== key || drop.after !== after) setDrop({ key, after });
    },
    onDrop: (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      const from = dragging, after = drop?.key === key ? drop.after : false;
      clearDrag();
      if (from !== null && from !== key) move(from, key, after);
    },
    onDragEnd: clearDrag,
  });

  return (
    <div data-sidebar-order="" id="sidebarOrderSetting">
      {visible.map(([key, label, glyph], index) => (
        <div key={optionKey(key)} data-sidebar-row={key} {...rowDrag(key)}
          data-dragging={dragging === key ? '' : undefined}
          data-drop={drop?.key === key ? (drop.after ? 'after' : 'before') : undefined}>
          <span data-sidebar-label=""><i data-sidebar-grip="" aria-hidden="true"><Icon name="grip-vertical" /></i>
            <Icon name={glyph ?? ''} /><b>{label}</b></span>
          <span data-sidebar-actions="">
            <button type="button" data-sidebar-key={key} data-sidebar-move="-1" aria-label={`上移 ${label}`} title="上移"
              disabled={index === 0} onClick={() => step(key, -1)}><Icon name="chevron-up" /></button>
            <button type="button" data-sidebar-key={key} data-sidebar-move="1" aria-label={`下移 ${label}`} title="下移"
              disabled={index === visible.length - 1} onClick={() => step(key, 1)}><Icon name="chevron-down" /></button>
            <button type="button" data-sidebar-key={key} data-sidebar-hide="" aria-label={`隐藏 ${label}`} title="隐藏"
              disabled={visible.length === 1}
              onClick={() => { if (order.length > 1) commit(order.filter((item) => item !== key)) }}><Icon name="eye-off" /></button>
          </span>
        </div>
      ))}
      <div data-sidebar-add-row="">
        <div data-sidebar-add-picker=""
          onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) closeMenu() }}>
          <button type="button" data-sidebar-add-trigger="" aria-haspopup="listbox" aria-expanded={menuOpen}
            disabled={!chosen} ref={trigger} onClick={toggleMenu}>
            {chosen
              ? <><Icon name={chosen[2] ?? ''} /><span data-sidebar-add-label="">{chosen[1]}</span><Icon name="chevron-down" /></>
              : <><Icon name="check" /><span>全部页面都已显示</span></>}
          </button>
          {available.length
            ? <div className="popmenu" data-sidebar-add-menu="" role="listbox" aria-label="选择要添加的页面" hidden ref={menu}>
                {available.map(([key, label, glyph]) => (
                  <button type="button" role="option" key={optionKey(key)} data-sidebar-add-option={optionKey(key)}
                    aria-selected={chosen?.[0] === key} tabIndex={chosen?.[0] === key ? 0 : -1} onKeyDown={optionKeys}
                    onClick={() => { setPicked(optionKey(key)); closeMenu(); trigger.current?.focus() }}>
                    <Icon name={glyph ?? ''} /><span>{label}</span>
                  </button>
                ))}
              </div>
            : null}
        </div>
        <button type="button" className="geist-button primary" data-sidebar-add="" disabled={!chosen}
          onClick={() => { if (chosen && !order.includes(chosen[0])) commit([...order, chosen[0]]) }}>添加</button>
      </div>
    </div>
  );
}
