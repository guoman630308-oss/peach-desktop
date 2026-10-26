import { beforeEach, expect, it, vi } from 'vitest';

import { ExpandableRanking } from '../../src/react/components/expandable-ranking';
import { click, mount } from './render';

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
});

const rows = (count: number) => Array.from(
  { length: count },
  (_value, index) => <li key={index}>{`第 ${index + 1} 名`}</li>,
);

it('行数没超过预览档时不标可展开，也不画展开键', async () => {
  const host = await mount(<ExpandableRanking previewCount={5}>{rows(5)}</ExpandableRanking>);
  const frame = host.querySelector('[data-expandable-ranking]')!;
  expect(frame.hasAttribute('data-expandable')).toBe(false);
  expect(host.querySelector('[data-ranking-toggle]')).toBeNull();
});

it('行数超过预览档时标出可展开，展开键切换方向', async () => {
  const host = await mount(<ExpandableRanking previewCount={5}>{rows(9)}</ExpandableRanking>);
  const frame = host.querySelector('[data-expandable-ranking]')!;
  expect(frame.hasAttribute('data-expandable')).toBe(true);
  const toggle = host.querySelector<HTMLButtonElement>('[data-ranking-toggle]')!;
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  await click(toggle);
  expect(toggle.getAttribute('aria-expanded')).toBe('true');
  expect(frame.hasAttribute('data-expanded')).toBe(true);
});
