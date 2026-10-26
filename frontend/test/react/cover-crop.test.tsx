/* 裁剪封面：点开之后框摆在哪、确认与恢复默认各发什么。
 *
 * 裁的是取景框不是图片，所以这里看的是「发出去的那组坐标对不对」。 */
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

import { PANEL_ASPECT } from '../../src/jav-artwork';
import { CoverCrop } from '../../src/react/cover-crop/cover-crop-page';
import { queryClient } from '../../src/react/query';

import { buttonNamed, click, mount, settle } from './render';

afterEach(() => { queryClient.clear() });

notifyManager.setScheduler((notify) => notify());

type Call = [string, RequestInit];

const SAVED_BOX = { x0: 420, y0: 0, x1: 800, y1: 540, method: 'manual', px: [800, 540] };

function server() {
  const calls: Call[] = [];
  const fetched = vi.fn(async (input: string, init?: RequestInit) => {
    calls.push([input, init || {}]);
    return { ok: true, status: 200, json: async () => ({ ok: true, code: 'ABW-232', poster_box: SAVED_BOX }) };
  });
  vi.stubGlobal('fetch', fetched);
  return calls;
}

const sentBox = (calls: Call[], n = 0) => JSON.parse(String(calls[n]?.[1]?.body)).box;

/** happy-dom 不真取图，所以自己报一次尺寸：框的一切都从这一步开始。 */
async function reportSize(width: number, height: number) {
  const image = document.querySelector('img');
  if (!image) throw new Error('取景图没有画出来');
  Object.defineProperty(image, 'naturalWidth', { value: width, configurable: true });
  Object.defineProperty(image, 'naturalHeight', { value: height, configurable: true });
  Object.defineProperty(image, 'clientWidth', { value: width, configurable: true });
  Object.defineProperty(image, 'clientHeight', { value: height, configurable: true });
  image.dispatchEvent(new Event('load', { bubbles: false }));
  await settle();
}

async function openCrop(props: Record<string, unknown> = {}) {
  const saved = vi.fn();
  const host = await mount(
    <QueryClientProvider client={queryClient}>
      <CoverCrop code="ABW-232" coverUrl="/cover?code=ABW-232" box={null} onSaved={saved}
        {...props} />
    </QueryClientProvider>,
  );
  await click(host.querySelector('[data-cover-crop]'));
  await settle();
  return { host, saved };
}

it('详情页那枚键说得出它要裁的是哪一张', async () => {
  const { host } = await openCrop();
  expect(host.querySelector('[data-cover-crop]')?.getAttribute('aria-label'))
    .toBe('裁剪 ABW-232 的封面');
  expect(document.querySelector('[role="dialog"]')).toBeTruthy();
});

it('没有生效的框时默认框满高贴右缘，按正封比例取宽', async () => {
  const calls = server();
  const { saved } = await openCrop();
  await reportSize(800, 540);
  await click(buttonNamed('用这一块'));
  await settle();
  expect(calls[0]?.[0]).toContain('/api/cover-crop');
  // 540×0.704≈380，从右缘量回去就是 420；纵向一个像素都不裁。
  expect(sentBox(calls)).toEqual({ x0: 420, y0: 0, x1: 800, y1: 540 });
  // 宿主拿到的是写端点回的那个框：背后网格就地换上它，不必重取。
  expect(saved).toHaveBeenCalledExactlyOnceWith(SAVED_BOX);
});

it('已经有框就摆在那个框上，人改的是现状不是从头来过', async () => {
  const calls = server();
  await openCrop({ box: { x0: 300, y0: 20, x1: 700, y1: 500, method: 'manual', px: [800, 540] } });
  await reportSize(800, 540);
  await click(buttonNamed('用这一块'));
  await settle();
  expect(sentBox(calls)).toEqual({ x0: 300, y0: 20, x1: 700, y1: 500 });
});

it('框是按另一张封面算的就不认，退回默认', async () => {
  const calls = server();
  await openCrop({ box: { x0: 300, y0: 20, x1: 700, y1: 500, method: 'fold', px: [1600, 1080] } });
  await reportSize(800, 540);
  await click(buttonNamed('用这一块'));
  await settle();
  expect(sentBox(calls).x1).toBe(800);
});

it('恢复默认发的是空框，由后端按折痕判据重算', async () => {
  const calls = server();
  const { saved } = await openCrop();
  await reportSize(800, 540);
  await click(buttonNamed('恢复默认'));
  await settle();
  expect(sentBox(calls)).toBeNull();
  expect(saved).toHaveBeenCalledTimes(1);
});

it('正封比例是后端那一份，两边不各写一个数', () => {
  expect(PANEL_ASPECT).toBe(0.704);
});
