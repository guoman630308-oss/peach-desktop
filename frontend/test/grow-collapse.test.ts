import { afterEach, expect, it, vi } from 'vitest';
import { growCollapse, setCollapseOpen } from '../src/ui-kit';

afterEach(() => { vi.useRealTimers(); document.body.replaceChildren(); });

it('从起始高度长到内容高度，跑完交回 auto 并装回展开态', () => {
  vi.useFakeTimers();
  const body = document.createElement('div'); body.className = 'ui-fcollapse ui-fcollapse-settled'; document.body.append(body);
  Object.defineProperty(body, 'scrollHeight', { configurable: true, value: 480 });
  growCollapse(body, 120);
  expect(body.classList.contains('ui-fcollapse-settled')).toBe(false);
  expect(body.style.height).toBe('480px');
  body.dispatchEvent(Object.assign(new Event('transitionend'), { propertyName: 'opacity' }));
  expect(body.style.height).toBe('480px');
  vi.advanceTimersByTime(260);
  expect(body.style.height).toBe('auto');
  expect(body.classList.contains('ui-fcollapse-settled')).toBe(true);
});

it('过渡途中又被收起时不收尾', () => {
  vi.useFakeTimers();
  const body = document.createElement('div'); body.className = 'ui-fcollapse'; document.body.append(body);
  let open = true;
  growCollapse(body, 0, () => open);
  open = false; body.style.height = '0px';
  vi.advanceTimersByTime(260);
  expect(body.style.height).toBe('0px');
  expect(body.classList.contains('ui-fcollapse-settled')).toBe(false);
});

const collapse = () => {
  const details = document.createElement('details');
  const body = document.createElement('div');
  details.append(document.createElement('summary'), body); document.body.append(details);
  return { details, body };
};

it('收起时高度先收到 0，过渡跑完才合上 details', () => {
  vi.useFakeTimers();
  const { details, body } = collapse();
  setCollapseOpen(details, body, true);
  expect(details.open).toBe(true);
  expect(body.classList.contains('ui-fcollapse')).toBe(true);
  vi.advanceTimersByTime(260);
  setCollapseOpen(details, body, false);
  expect(body.style.height).toBe('0px');
  expect(body.inert).toBe(true);
  expect(details.open).toBe(true);
  vi.advanceTimersByTime(260);
  expect(details.open).toBe(false);
  expect(body.style.height).toBe('');
});

it('收到一半又点开时，收起那次的收尾不再合上 details', () => {
  vi.useFakeTimers();
  const { details, body } = collapse();
  setCollapseOpen(details, body, true);
  vi.advanceTimersByTime(260);
  setCollapseOpen(details, body, false);
  setCollapseOpen(details, body, true);
  vi.advanceTimersByTime(260);
  expect(details.open).toBe(true);
  expect(body.inert).toBe(false);
  expect(body.style.height).toBe('auto');
});
