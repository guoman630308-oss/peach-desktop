import { expect, it, vi } from 'vitest';

import { LIBRARY_ICON_CHOICES, LibraryIconPicker, libraryAutoChoice } from '../../src/react/settings/library-icon-picker';
import { buttonNamed, click, mount } from './render';

const trigger = (root: ParentNode) => root.querySelector('[aria-haspopup="dialog"]');
const radio = (value: string) => document.querySelector<HTMLInputElement>(`input[type="radio"][value="${value}"]`);

it('42 枚候选各不相同；不另选时网盘库用站标，本地路径就是默认磁盘', async () => {
  const keys: string[] = LIBRARY_ICON_CHOICES.map(([key]) => key);
  expect(keys).toHaveLength(42);
  expect(new Set(keys).size).toBe(42);
  expect(keys).not.toContain('115');
  expect(keys).not.toContain('pikpak');
  expect(libraryAutoChoice('local')).toEqual(['hard-drive', '默认']);
  expect(libraryAutoChoice('115')).toEqual(['fixture-115', '自动识别']);
  const local = await mount(<LibraryIconPicker label="媒体库图标 1" value="" kind="local" onChange={vi.fn()} />);
  expect(trigger(local)?.textContent).toBe('默认');
  expect(trigger(local)?.querySelector('use')?.getAttribute('href')).toBe('#i-hard-drive');
  const cloud = await mount(<LibraryIconPicker label="媒体库图标 2" value="" kind="115" onChange={vi.fn()} />);
  expect(trigger(cloud)?.textContent).toBe('自动识别');
  await click(trigger(cloud));
  // 格子里只放图标，名字在无障碍名称上。
  expect(document.querySelectorAll('input[type="radio"]')).toHaveLength(42);
  expect(radio('auto')?.getAttribute('aria-label')).toBe('自动识别');
  expect(radio('auto')?.checked).toBe(true);
  expect(radio('auto')?.closest('label')?.querySelector('use')?.getAttribute('href')).toBe('#i-fixture-115');
  expect(radio('cherry')?.getAttribute('aria-label')).toBe('樱桃');
});

it('点选只改草稿，取消保持原值，应用才提交', async () => {
  const change = vi.fn();
  const host = await mount(<LibraryIconPicker label="媒体库图标 1" value="heart" onChange={change} />);
  await click(trigger(host));
  expect(radio('heart')?.checked).toBe(true);
  await click(radio('cherry'));
  expect(change).not.toHaveBeenCalled();
  await click(buttonNamed('取消'));
  await click(trigger(host));
  expect(radio('heart')?.checked).toBe(true);
  await click(radio('cherry'));
  await click(buttonNamed('应用'));
  expect(change).toHaveBeenCalledWith('cherry');
});
