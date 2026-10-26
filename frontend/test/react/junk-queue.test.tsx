/* 垃圾文件队列：取数的查询串、地址与分类链接、计数行骨架与等待态逐项相同、一张卡画出什么、
 * 三颗键把什么交给壳、失败与为空时留下什么，以及一段段往下露。 */
import { act, useState } from 'react';
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { cleanJunkKind, junkCountSkeletonHtml, junkPath, junkRoute } from '../../src/junk-queue';
import { JunkQueuePage } from '../../src/react/junk-queue/junk-queue-page';
import {
  junkQueryString, type JunkItem, type JunkPage, type JunkQueueActions, type JunkQueueProps,
} from '../../src/react/junk-queue/junk-queue';
import { queryClient } from '../../src/react/query';
import { click, fetchMock, mount, pending, settle } from './render';

afterEach(() => { queryClient.clear() });
notifyManager.setScheduler((notify) => notify());

const junk = (id: number, extra: Partial<JunkItem> = {}): JunkItem =>
  ({ id, name: `广告 ${id}.mp4`, junk_kind: 'video', why: '文件名像推广', size: 2 * 1048576, location: 'local', ...extra });

const answer = (items: JunkItem[], extra: Partial<JunkPage> = {}): JunkPage =>
  ({ items, total: items.length, all_total: items.length, dismissed_total: 0, counts: { video: items.length }, ...extra });

function actions(patch: Partial<JunkQueueActions> = {}): JunkQueueActions {
  return {
    navigate: vi.fn(), toggleSelection: vi.fn(), open: vi.fn(),
    reveal: vi.fn(async () => ''), operate: vi.fn(async () => {}), ...patch,
  };
}

function props(patch: Partial<JunkQueueProps> = {}): JunkQueueProps {
  const countRow = document.createElement('div');
  countRow.id = 'count';
  document.body.append(countRow);
  return {
    kind: '', view: 'pending', helpers: { badgeHtml: () => '<span>本地</span>' }, actions: actions(),
    batchSize: 60, revision: 1, selectMode: false, selected: new Set(), countRow,
    skeletonHtml: () => '<div class="grid"><div class="skeletoncard"></div></div>',
    settled: vi.fn(), ...patch,
  };
}

/** 挂上队列并留一个改 props 的口子：壳推新的地址、代次与选择时走的就是它。 */
async function open(initial: JunkQueueProps) {
  let push!: (next: Partial<JunkQueueProps>) => void;
  function Harness() {
    const [current, setCurrent] = useState(initial);
    push = (next) => setCurrent((before) => ({ ...before, ...next }));
    return <QueryClientProvider client={queryClient}><JunkQueuePage {...current} /></QueryClientProvider>;
  }
  const host = await mount(<Harness />);
  await settle();
  return { host, update: (next: Partial<JunkQueueProps>) => act(async () => push(next)) };
}

/** 两版计数行的可见结构。类名按词比较；覆盖式滚动条是挂上以后才接的（属性与两条隐藏的轨道），
 *  不属于骨架。 */
function countShape(root: Element | null | undefined): string {
  const copy = root?.cloneNode(true) as Element | undefined;
  if (!copy) return '';
  copy.querySelectorAll('.ovtrack,.ui-ov-edges').forEach((track) => track.remove());
  copy.querySelectorAll('[data-overlay-scrollbar]').forEach((el) => el.removeAttribute('data-overlay-scrollbar'));
  copy.querySelectorAll('[class]').forEach((el) => el.setAttribute('class', el.className.toString().trim().split(/\s+/).join(' ')));
  return copy.outerHTML;
}

const cards = (root: ParentNode) => [...root.querySelectorAll<HTMLElement>('[data-junk-card]')];
const actionButton = (card: Element, action: string) => card.querySelector<HTMLButtonElement>(`[data-junk-action="${action}"]`);

describe('地址与取数', () => {
  it('分类与视图从地址读，认不出的分类落回全部；默认那一档不写进地址', () => {
    expect(junkRoute('?type=image&view=dismissed')).toEqual({ kind: 'image', view: 'dismissed' });
    expect(junkRoute('?type=bogus')).toEqual({ kind: '', view: 'pending' });
    expect(cleanJunkKind(null)).toBe('');
    expect(junkPath()).toBe('/junk-files');
    expect(junkPath('video', 'dismissed')).toBe('/junk-files?type=video&view=dismissed');
  });

  it('一次取 200 条，分类只在选了某一类时带上', async () => {
    expect(junkQueryString({ kind: '', view: 'pending' })).toBe('limit=200&status=pending');
    expect(junkQueryString({ kind: 'archive', view: 'dismissed' })).toBe('limit=200&status=dismissed&kind=archive');
    const fetcher = fetchMock(200, answer([junk(1)]));
    vi.stubGlobal('fetch', fetcher);
    await open(props({ kind: 'video' }));
    expect(fetcher.mock.calls[0]![0]).toBe('/api/ads?limit=200&status=pending&kind=video');
  });
});

describe('计数行', () => {
  it('壳铺的骨架与 island 等数据时那一版逐项相同，接管那一拍不跳', async () => {
    const route = { kind: 'image', view: 'pending' } as const;
    const staticRow = document.createElement('div');
    staticRow.innerHTML = junkCountSkeletonHtml(route);
    const waiting = pending<unknown>();
    vi.stubGlobal('fetch', vi.fn(() => waiting.answer));
    const p = props(route);
    p.countRow!.innerHTML = junkCountSkeletonHtml(route);
    await open(p);
    const live = p.countRow!.querySelector('[data-junk-count]');
    expect(p.countRow!.querySelector('[data-junk-count-skeleton]')).toBeNull();
    expect(countShape(live)).toBe(countShape(staticRow.querySelector('[data-junk-count]')));
    expect(p.countRow!.getAttribute('aria-busy')).toBe('true');
    // 等数据时网格是骨架，不先闪空态。
    expect(document.querySelector('.skeletoncard')).not.toBeNull();
    expect(document.querySelector('[data-empty-state]')).toBeNull();
  });

  it('数据到了：摘要写这一视图的条数，计数为 0 的类不画徽标，末尾一项带已排除的数', async () => {
    vi.stubGlobal('fetch', fetchMock(200, answer([junk(1), junk(2)], {
      total: 2, all_total: 5, dismissed_total: 3, counts: { video: 2, image: 3 },
    })));
    const p = props();
    await open(p);
    const row = p.countRow!;
    expect(row.hasAttribute('aria-busy')).toBe(false);
    expect(row.querySelector('[data-collection-summary] strong')?.textContent).toBe('2 个');
    const badge = (key: string) => row.querySelector(`[data-count-badge="junk:${key}"]`)?.textContent ?? null;
    expect([badge(''), badge('video'), badge('image'), badge('archive'), badge('dismissed')]).toEqual(['5', '2', '3', null, '3']);
    // 徽标交给遗留层 `popBadges` 记数：变了的那几枚弹，没动的原地不动。
    expect(row.querySelector('[data-count-badge="junk:video"]')?.classList.contains('countbadge')).toBe(true);
  });

  it('分类条是导航不是 tablist，每一类一枚文件类型字形', async () => {
    vi.stubGlobal('fetch', fetchMock(200, answer([])));
    const p = props();
    await open(p);
    const nav = p.countRow!.querySelector('nav[data-junk-filters]');
    expect(nav?.getAttribute('aria-label')).toBe('垃圾文件分类');
    expect(nav?.getAttribute('role')).toBeNull();
    expect([...nav!.querySelectorAll('a use')].map((use) => use.getAttribute('href'))).toEqual([
      '#i-layout-grid', '#i-play', '#i-pics', '#i-file-archive', '#i-file-audio', '#i-globe', '#i-hard-drive', '#i-eye-off',
    ]);
  });

  it('分类是可深链的链接：普通左键交给壳换页，按着修饰键照浏览器默认', async () => {
    vi.stubGlobal('fetch', fetchMock(200, answer([])));
    const navigate = vi.fn();
    const p = props({ view: 'dismissed', actions: actions({ navigate }) });
    await open(p);
    const links = [...p.countRow!.querySelectorAll<HTMLAnchorElement>('[data-junk-filters] a')];
    expect(links.map((link) => link.getAttribute('href'))).toEqual([
      '/junk-files?view=dismissed', '/junk-files?type=video&view=dismissed', '/junk-files?type=image&view=dismissed',
      '/junk-files?type=archive&view=dismissed', '/junk-files?type=audio&view=dismissed',
      '/junk-files?type=url&view=dismissed', '/junk-files?type=other&view=dismissed', '/junk-files',
    ]);
    expect(links.at(-1)?.textContent).toBe('返回待判断');
    expect(links.at(-1)?.getAttribute('aria-current')).toBe('page');
    await click(links[2]);
    expect(navigate).toHaveBeenCalledWith('/junk-files?type=image&view=dismissed');
    const modified = new MouseEvent('click', { bubbles: true, cancelable: true, ctrlKey: true });
    await act(async () => { links[3]!.dispatchEvent(modified) });
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(modified.defaultPrevented).toBe(false);
  });

  it('读不到时摘要不再转圈，网格那一格给重试', async () => {
    vi.stubGlobal('fetch', fetchMock(500, { error: '服务端出错' }));
    const p = props();
    const { host } = await open(p);
    expect(p.countRow!.querySelector('[data-skeleton]')).toBeNull();
    expect(p.countRow!.querySelector('[data-collection-summary] strong')?.textContent).toBe('—');
    expect(host.querySelector('[data-media-error] [data-note-action]')?.textContent).toBe('重试');
    expect(cards(host)).toHaveLength(0);
  });
});

describe('一张卡', () => {
  it('视频与图片的标题能点开，其余只是文字；没有大小写「大小未知」', async () => {
    vi.stubGlobal('fetch', fetchMock(200, answer([
      junk(1), junk(2, { junk_kind: 'archive', name: '推广.zip', size: 0, why: '' }),
      junk(3, { junk_kind: 'image', name: '横幅.jpg' }), junk(4, { junk_kind: 'audio', name: '广告.mp3' }),
    ])));
    const open_ = vi.fn();
    const { host } = await open(props({ actions: actions({ open: open_ }) }));
    const [video, archive, image, audio] = cards(host);
    expect(video!.dataset.junkKind).toBe('video');
    expect(video!.querySelector('[data-media-art] img')?.getAttribute('src')).toBe('/thumb?id=1&c=4');
    expect(image!.querySelector('[data-media-art] img')?.getAttribute('src')).toBe('/photo-thumb?id=3');
    expect(image!.querySelector('[data-junk-title]')?.tagName).toBe('BUTTON');
    // 文件类型标的是文件：压缩包与音频各有自己的字形，音频不借音量键。
    expect(archive!.querySelector('[data-media-glyph] use')?.getAttribute('href')).toBe('#i-file-archive');
    expect(audio!.querySelector('[data-media-glyph] use')?.getAttribute('href')).toBe('#i-file-audio');
    // 文件名从中间截断，首尾都留着；选中勾与作品卡同一套，垃圾卡上没有稍后看。
    expect(cards(host).every((card) => card.querySelector('[data-junk-title]')!.hasAttribute('data-middle-truncate'))).toBe(true);
    expect(video!.querySelector('[data-media-check]')).not.toBeNull();
    expect(host.querySelector('[data-later]')).toBeNull();
    expect(video!.querySelector('[data-junk-facts]')?.textContent).toBe('视频文件名像推广2 MB');
    expect(archive!.querySelector('[data-junk-title]')?.tagName).toBe('SPAN');
    expect(archive!.querySelector('[data-media-art]')).toBeNull();
    expect(archive!.querySelector('[data-media-glyph]')?.textContent).toBe('压缩包');
    expect(archive!.querySelector('[data-junk-size]')?.textContent).toBe('大小未知');
    await click(video!.querySelector('button[data-junk-title]'));
    expect(open_).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }), video);
  });

  it('中间那颗键随视图换：待判断是「不是垃圾」，已排除是「重新判断」', async () => {
    vi.stubGlobal('fetch', fetchMock(200, answer([junk(1)])));
    const { host, update } = await open(props());
    const labels = () => [...cards(host)[0]!.querySelectorAll('[data-junk-action] [data-junk-label]')].map((el) => el.textContent);
    expect(labels()).toEqual(['打开位置', '不是垃圾', '移入回收站']);
    await update({ view: 'dismissed' });
    await settle();
    expect(labels()).toEqual(['打开位置', '重新判断', '移入回收站']);
  });

  it('处置交给壳：请求期间那颗键忙，失败后恢复成可点', async () => {
    vi.stubGlobal('fetch', fetchMock(200, answer([junk(1)])));
    const reply = pending<void>();
    let fail!: (error: Error) => void;
    const operate = vi.fn()
      .mockImplementationOnce(() => reply.answer)
      .mockImplementationOnce(() => new Promise<void>((_, reject) => { fail = reject }));
    const { host } = await open(props({ actions: actions({ operate }) }));
    const dispose = () => actionButton(cards(host)[0]!, 'dispose')!;
    await click(dispose());
    expect(operate).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }), 'dispose');
    expect(dispose().getAttribute('aria-busy')).toBe('true');
    await reply.release();
    await settle();
    expect(dispose().hasAttribute('aria-busy')).toBe(false);

    await click(actionButton(cards(host)[0]!, 'dismiss-junk'));
    expect(operate).toHaveBeenLastCalledWith(expect.objectContaining({ id: 1 }), 'dismiss-junk');
    await act(async () => { fail(new Error('写入失败')) });
    await settle();
    expect(actionButton(cards(host)[0]!, 'dismiss-junk')!.hasAttribute('aria-busy')).toBe(false);
  });

  it('打开位置失败时把原因写在卡上的状态行，成功不留字', async () => {
    vi.stubGlobal('fetch', fetchMock(200, answer([junk(1)])));
    const reveal = vi.fn().mockResolvedValueOnce('来源不在线').mockResolvedValueOnce('');
    const { host } = await open(props({ actions: actions({ reveal }) }));
    const state = () => cards(host)[0]!.querySelector('[data-junk-state]')!.textContent;
    await click(actionButton(cards(host)[0]!, 'reveal'));
    await settle();
    expect(state()).toBe('来源不在线');
    await click(actionButton(cards(host)[0]!, 'reveal'));
    await settle();
    expect(state()).toBe('');
  });

  it('多选时点卡片任何地方都是切换选中，选中写在卡上', async () => {
    vi.stubGlobal('fetch', fetchMock(200, answer([junk(1), junk(2)])));
    const toggleSelection = vi.fn();
    const open_ = vi.fn();
    const { host, update } = await open(props({ actions: actions({ toggleSelection, open: open_ }) }));
    await update({ selectMode: true, selected: new Set([2]) });
    expect(cards(host).map((card) => card.hasAttribute('data-selected'))).toEqual([false, true]);
    expect(host.querySelector('[data-junk-grid]')?.hasAttribute('data-select-mode')).toBe(true);
    await click(cards(host)[0]!.querySelector('button[data-junk-title]'));
    await click(cards(host)[0]!.querySelector('[data-junk-meta]'));
    expect(toggleSelection.mock.calls).toEqual([[1, false], [1, false]]);
    expect(open_).not.toHaveBeenCalled();
  });
});

describe('一屏卡片', () => {
  it('为空时按视图说明为什么是空的', async () => {
    vi.stubGlobal('fetch', fetchMock(200, answer([])));
    const { host, update } = await open(props());
    expect(host.querySelector('h3')?.textContent).toBe('没有待判断的垃圾文件');
    await update({ view: 'dismissed' });
    await settle();
    expect(host.querySelector('h3')?.textContent).toBe('没有已排除的文件');
  });

  it('整批在手上，按每批条数一段段露，露完不留哨兵', async () => {
    vi.stubGlobal('fetch', fetchMock(200, answer([1, 2, 3, 4, 5].map((id) => junk(id)))));
    const { host } = await open(props({ batchSize: 2 }));
    expect(cards(host)).toHaveLength(2);
    await click(host.querySelector('[data-load-more]'));
    expect(cards(host)).toHaveLength(4);
    await click(host.querySelector('[data-load-more]'));
    expect(cards(host)).toHaveLength(5);
    expect(host.querySelector('[data-load-more]')).toBeNull();
  });

  it('壳处置完推进代次：重读期间旧的一份留在屏上，已露出的几段不收回', async () => {
    const first = answer([1, 2, 3].map((id) => junk(id)));
    const reread = pending<unknown>();
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => first })
      .mockImplementationOnce(() => reread.answer);
    vi.stubGlobal('fetch', fetcher);
    const settled = vi.fn();
    const { host, update } = await open(props({ batchSize: 1, settled }));
    await click(host.querySelector('[data-load-more]'));
    expect(cards(host)).toHaveLength(2);
    // 首屏骨架的淡出层等动画结束才撤，测试环境里没有动画，等它的 1 秒兜底。
    await act(() => new Promise((done) => { setTimeout(done, 1050) }));
    expect(host.querySelector('.skeletoncard')).toBeNull();
    await update({ revision: 2 });
    expect(cards(host)).toHaveLength(2);
    expect(host.querySelector('.skeletoncard')).toBeNull();
    await reread.release({ ok: true, status: 200, json: async () => answer([2, 3].map((id) => junk(id))) });
    await settle();
    expect(cards(host).map((card) => card.dataset.id)).toEqual(['2', '3']);
    expect(settled).toHaveBeenLastCalledWith(2);
  });
});
