/* 播放器右键菜单。
 *
 * 项目照 YouTube 播放器 f572e43c 的 .ytp-contextmenu 取舍：循环播放、迷你播放器（小窗里是展开）、
 * 画中画、复制视频网址、复制当前时间的视频网址、播放统计；嵌入代码、调试信息和排查播放问题
 * Peach 没有对应能力，不列。 */
import { esc, icon } from '@peach/legacy/core';
import { dismissMenu, presentMenu, scrollMovesAnchor } from '@peach/legacy/ui';

import { playerHost } from './host';
import type { VjsPlayer } from './types';

/** 菜单里和小窗有关的那两项要问小窗的持有方。 */
export interface PlayerMenuHooks {
  /** 这个播放器此刻在不在小窗里。 */
  inMiniplayer(player: VjsPlayer): boolean;
  /** 这个播放器放的是作品还是关注条目：复制出来的地址按它走。 */
  kind(player: VjsPlayer): 'item' | 'follow';
  /** 小窗里的「展开」。 */
  expand(): void;
  /** 详情里的「迷你播放器」：越过播放态判定，把正在放的这一条送进小窗。 */
  toMiniplayer(): void;
}

let hooks: PlayerMenuHooks | null = null;
export function configurePlayerMenu(next: PlayerMenuHooks): void { hooks = next }

/* 菜单节点归这个模块：第一次用到时建在 body 上，舞台开着时临时挪进舞台，关掉再放回来。
   它不进任何 React 树——被挪来挪去的节点，React 卸载时会找不到它的父级。 */
function menuElement(): HTMLElement {
  let menu = document.getElementById('playerMenu');
  if (!menu) {
    menu = document.createElement('div');
    menu.className = 'popmenu';
    menu.setAttribute('data-player-context-menu', '');
    menu.id = 'playerMenu';
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', '播放器菜单');
    menu.hidden = true;
    document.body.append(menu);
  }
  return menu;
}

let cleanup: (() => void) | null = null;

/** 给图标的 `<svg>` 挂一个空的 data 属性：填色图标与勾选记号各有一条样式。 */
const mark = (svg: string, attribute: string) => attribute ? svg.replace('<svg', `<svg ${attribute}=""`) : svg;

export function closePlayerMenu(): void {
  const menu = document.getElementById('playerMenu');
  if (menu?.matches(':popover-open')) menu.hidePopover();
  if (menu && menu.parentElement !== document.body) document.body.append(menu);
  if (menu) dismissMenu(menu, () => { menu.innerHTML = '' });
  if (cleanup) { cleanup(); cleanup = null }
}

async function copyTextToClipboard(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true } catch {
    const area = document.createElement('textarea');
    area.value = text; area.setAttribute('readonly', ''); area.style.position = 'fixed'; area.style.opacity = '0';
    document.body.append(area); area.select();
    let ok = false; try { ok = document.execCommand('copy') } catch { /* 浏览器不给 */ }
    area.remove(); return ok;
  }
}

interface MenuItem { icon: string; label: string; fill?: boolean; checked?: boolean; run(): void }

function playerMenuItems(player: VjsPlayer): MenuItem[] {
  const mini = !!hooks?.inMiniplayer(player);
  const item = player.peachItem;
  const kind = hooks?.kind(player) ?? 'item';
  const url = (withTime: boolean) => {
    const link = new URL(kind === 'follow' ? `/posts/${item?.id}` : `/assets/${item?.id}`, location.origin);
    if (kind !== 'follow' && location.pathname === link.pathname) {
      const context = new URLSearchParams(location.search);
      for (const key of ['mix', 'parts', 'editions', 'playlist']) {
        const value = context.get(key);
        if (value && /^[1-9]\d*$/.test(value)) link.searchParams.set(key, value);
      }
    }
    if (withTime) link.searchParams.set('t', String(Math.floor(player.currentTime() || 0)));
    return link.href;
  };
  const copy = (withTime: boolean, receipt: string) => {
    void copyTextToClipboard(url(withTime)).then((ok) =>
      playerHost().toast(ok ? receipt : '复制失败，请手动复制地址栏', { timeout: 4000, warn: !ok }));
  };
  const items: MenuItem[] = [
    { icon: 'repeat', label: '循环播放', checked: !!player.loop(), run: () => player.loop(!player.loop()) },
    mini ? { icon: 'maximize-2', label: '展开', run: () => hooks?.expand() }
      : { icon: 'picture-in-picture-2', label: '迷你播放器', run: () => hooks?.toMiniplayer() },
  ];
  if (document.pictureInPictureEnabled) {
    items.push({ icon: 'player-pip', fill: true, label: '画中画',
      run: () => player.el().querySelector<HTMLElement>('.vjs-picture-in-picture-control')?.click() });
  }
  items.push({ icon: 'link', label: '复制视频网址', run: () => copy(false, '已复制视频网址') });
  items.push({ icon: 'link', label: '复制当前时间的视频网址', run: () => copy(true, '已复制当前时间的视频网址') });
  const stats = document.getElementById('playerStatsBtn');
  if (!mini && stats && !stats.hidden) items.push({ icon: 'chart', label: '播放统计', run: () => stats.click() });
  return items;
}

export function openPlayerMenu(player: VjsPlayer, x: number, y: number): void {
  const menu = menuElement();
  closePlayerMenu();
  /* 舞台是模态 dialog，在顶层：菜单要进它里面才盖得住，否则落在遮罩底下。 */
  const stage = playerHost().stage();
  if (stage instanceof HTMLDialogElement && stage.open) stage.append(menu);
  menu.setAttribute('popover', 'manual');
  const items = playerMenuItems(player);
  menu.innerHTML = items.map((item, index) => {
    const checkable = item.checked !== undefined;
    return `<button type="button" role="${checkable ? 'menuitemcheckbox' : 'menuitem'}"${checkable ? ` aria-checked="${item.checked}"` : ''} data-player-menu="${index}">${
      mark(icon(item.icon), item.fill ? 'data-player-menu-fill' : '')}<span>${esc(item.label)}</span>${checkable ? mark(icon('check'), 'data-player-menu-check') : ''}</button>`;
  }).join('');
  presentMenu(menu); menu.showPopover();
  // 量 offsetWidth／offsetHeight：进场动画起手是 scale(.95)，getBoundingClientRect 量到的是缩过的框。
  menu.style.left = `${Math.max(8, Math.min(x, innerWidth - menu.offsetWidth - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, innerHeight - menu.offsetHeight - 8))}px`;
  const buttons = [...menu.querySelectorAll<HTMLElement>('[data-player-menu]')];
  buttons.forEach((button) => {
    button.onclick = (event) => {
      event.stopPropagation(); const item = items[Number(button.dataset.playerMenu)]; closePlayerMenu(); item?.run();
    };
  });
  const onDown = (event: Event) => { if (!menu.contains(event.target as Node)) closePlayerMenu() };
  const onKey = (event: KeyboardEvent) => {
    /* 这一下 Escape 只关菜单：`preventDefault` 同时拦住舞台 dialog 的 `cancel`，舞台留着。 */
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closePlayerMenu(); return }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    event.preventDefault();
    const current = buttons.indexOf(document.activeElement as HTMLElement);
    buttons[(current + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus();
  };
  const onScroll = (event: Event) => { if (scrollMovesAnchor(event, player.el())) closePlayerMenu() };
  setTimeout(() => {
    document.addEventListener('pointerdown', onDown, true);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('scroll', onScroll, { capture: true, passive: true });
  }, 0);
  cleanup = () => {
    document.removeEventListener('pointerdown', onDown, true);
    document.removeEventListener('keydown', onKey, true);
    window.removeEventListener('scroll', onScroll, true);
  };
  buttons[0]?.focus();
}

export function wirePlayerContextMenu(player: VjsPlayer): void {
  player.el().addEventListener('contextmenu', (event) => {
    if ((event.target as Element).closest('.vjs-peach-settings-menu')) return;
    event.preventDefault(); openPlayerMenu(player, event.clientX, event.clientY);
  });
  player.on('dispose', closePlayerMenu);
}
