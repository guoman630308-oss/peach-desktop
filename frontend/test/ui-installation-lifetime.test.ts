import { afterEach, expect, it, vi } from 'vitest';
import { wireAnchoredMenu } from '../src/ui-kit/anchored-menu';
import { wireUiSounds } from '../src/ui-kit/sounds';
import { wireImageFallbacks } from '../src/card-art/image-fallback';
import { initBoardControls } from '../src/board-controls';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); document.body.innerHTML = ''; });

it('同一宿主的声音与图片委托各安装一次', () => {
  const root = document.createElement('div');
  const add = vi.spyOn(root, 'addEventListener');
  wireUiSounds(root); wireUiSounds(root);
  wireImageFallbacks(root); wireImageFallbacks(root);
  expect(add.mock.calls.map(([name]) => name)).toEqual(['click', 'change', 'error']);
});

it('Board 控件重复安装沿用全站监听与提示框', () => {
  const add = vi.spyOn(document, 'addEventListener');
  initBoardControls();
  const installed = add.mock.calls.length;
  initBoardControls();
  expect(add.mock.calls.length).toBe(installed);
  expect(document.querySelectorAll('#board-control-tooltip')).toHaveLength(1);
});

it('锚定菜单销毁移除监听，同一按钮重挂只有一份开关', () => {
  const mount = document.createElement('div');
  const toggle = document.createElement('button'), menu = document.createElement('div');
  menu.hidden = true; mount.append(toggle, menu); document.body.append(mount);
  const remove = vi.spyOn(window, 'removeEventListener');
  const first = wireAnchoredMenu(mount, toggle, menu);
  first.setOpen(true); first.dispose(); first.dispose();
  toggle.click(); first.setOpen(true);
  expect([first.isOpen(), menu.hidden]).toEqual([false, true]);
  expect(remove.mock.calls.filter(([type]) => type === 'resize')).toHaveLength(1);
  expect(remove.mock.calls.filter(([type]) => type === 'scroll')).toHaveLength(1);
  const second = wireAnchoredMenu(mount, toggle, menu);
  toggle.click(); expect(second.isOpen()).toBe(true);
  mount.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  expect(second.isOpen()).toBe(false);
  second.dispose();
});
