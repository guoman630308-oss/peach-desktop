import { describe, expect, it, vi } from 'vitest';

import { LibrarySharingSettings, type LibrarySharingState } from '../../src/react/settings/library-sharing-settings';
import { buttonNamed, click, mount, settle, switches } from './render';

const initial: LibrarySharingState = { enabled: false, available: true, schema: 4,
  fields: ['作品编号、标签、评分与全部偏好维度'], notice: '匿名处理后上传馆藏与各类偏好明细。' };

describe('馆藏与口味分享的明确同意', () => {
  it('开关始终可见，上传预览和明确同意完整后才能开启', async () => {
    const fetcher = vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith('/preview')
      ? { data: { library: [{ code: 'EXM-001' }], preferences: {}, coverage: { uncoded_items: 0 } }, bytes: 80, token: 'preview-token', schema: 4 }
      : { ...initial, enabled: true } }));
    vi.stubGlobal('fetch', fetcher);
    const receipt = vi.fn();
    const host = await mount(<LibrarySharingSettings initial={initial} receipt={receipt} />);
    expect(fetcher).not.toHaveBeenCalled();
    expect(host.textContent).toContain(initial.notice);
    expect(switches(host)[0]?.checked).toBe(false);
    await click(switches(host)[0]);
    expect(fetcher).not.toHaveBeenCalled();
    expect(switches(host)[0]?.checked).toBe(false);
    expect(host.textContent).not.toContain(initial.fields[0]);
    expect(host.querySelector('input[type="checkbox"]:not([role="switch"])')).toBeNull();
    await click(buttonNamed('预览上传内容', host));
    await settle();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain('KiB。不含账号、设备、文件路径和浏览器历史记录等个人数据。');
    expect((buttonNamed('同意并开启上传', host) as HTMLButtonElement).disabled).toBe(true);
    await click(host.querySelector('input[type="checkbox"]:not([role="switch"])'));
    await click(buttonNamed('同意并开启上传', host));
    await settle();
    const call = fetcher.mock.calls[1]! as unknown as [string, RequestInit];
    expect(JSON.parse(String(call[1].body))).toEqual({ enabled: true, schema: 4, consent: true, preview_token: 'preview-token' });
    expect(receipt).toHaveBeenCalledWith('已保存馆藏与口味上传选项');
    expect(switches(host)[0]?.checked).toBe(true);
    expect(host.textContent).not.toContain(initial.fields[0]);
    expect(host.querySelector('pre')).toBeNull();
    expect(host.querySelector('input[type="checkbox"]:not([role="switch"])')).toBeNull();
    expect(buttonNamed('预览上传内容', host)).not.toBeNull();
  });

  it('关闭不要求重新预览或勾选', async () => {
    const fetcher = vi.fn(async () => ({ ok: true, json: async () => initial }));
    vi.stubGlobal('fetch', fetcher);
    const host = await mount(<LibrarySharingSettings initial={{ ...initial, enabled: true }} receipt={vi.fn()} />);
    await click(switches(host)[0]!);
    await settle();
    const call = fetcher.mock.calls[0]! as unknown as [string, RequestInit];
    expect(JSON.parse(String(call[1].body)).enabled).toBe(false);
    expect(switches(host)[0]?.checked).toBe(false);
  });

  it('预览失败时保留关闭状态，可重试且不报保存成功', async () => {
    const fetcher = vi.fn(async () => ({ ok: false, status: 500, json: async () => ({ detail: '读取失败' }) }));
    vi.stubGlobal('fetch', fetcher);
    const receipt = vi.fn();
    const host = await mount(<LibrarySharingSettings initial={initial} receipt={receipt} />);
    await click(switches(host)[0]);
    await click(buttonNamed('预览上传内容', host));
    await settle();
    expect(switches(host)[0]?.checked).toBe(false);
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('读取失败');
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(receipt).not.toHaveBeenCalled();
  });
});
