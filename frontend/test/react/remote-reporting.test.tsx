import { describe, expect, it, vi } from 'vitest';

import { RemoteReportingSettings, type RemoteReportingState } from '../../src/react/settings/remote-reporting-settings';
import { click, mount, settle, switches } from './render';

const initial: RemoteReportingState = {
  usage: false, errors: false, available: false,
  fields: { usage: 'Peach 版本、操作系统类型', errors: '异常链、代码位置和结构化错误码' },
  notice: '不自动发送日志正文或媒体信息。',
};

describe('版本统计与错误诊断', () => {
  it('单个推荐开关保留已保存的关闭选择，开启后共同保存版本统计与错误诊断', async () => {
    const fetcher = vi.fn(async () => ({ ok: true, json: async () => ({ ...initial, usage: true, errors: true }) }));
    vi.stubGlobal('fetch', fetcher);
    const receipt = vi.fn();
    const host = await mount(<RemoteReportingSettings initial={initial} receipt={receipt} />);
    expect(switches(host).map((node) => node.checked)).toEqual([false]);
    expect(host.textContent).toContain('上传服务未配置。');
    expect(host.textContent).toContain('上传版本、系统信息、故障类型、代码位置与发生次数。');
    await click(switches(host)[0]!);
    await settle();
    const [url, init] = fetcher.mock.calls[0]! as unknown as [string, RequestInit];
    expect(url).toBe('/api/configuration/remote-reporting');
    expect(JSON.parse(String(init.body))).toEqual({ usage: true, errors: true });
    expect(receipt).toHaveBeenCalledWith('已保存诊断选项');
    expect(switches(host).map((node) => node.checked)).toEqual([true]);
  });

  it('保存失败保留已保存状态和错误，可以再次关闭', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 403, json: async () => ({ error: '请在本机保存' }) })
      .mockResolvedValueOnce({ ok: true, json: async () => initial });
    vi.stubGlobal('fetch', fetcher);
    const receipt = vi.fn();
    const host = await mount(<RemoteReportingSettings initial={{ ...initial, usage: true, errors: true }} receipt={receipt} />);
    for (const toggle of switches(host)) await click(toggle);
    await settle();
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('请在本机保存');
    expect(receipt).not.toHaveBeenCalled();
    await click(switches(host)[0]);
    await settle();
    expect(JSON.parse(String(fetcher.mock.calls[1]![1].body))).toEqual({ usage: false, errors: false });
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });
});
