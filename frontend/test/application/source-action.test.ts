import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRevealSourceAction } from '../../src/application/source-action';

afterEach(() => { document.body.innerHTML = '' });

function surface() {
  const button = document.createElement('button'), status = document.createElement('p');
  button.innerHTML = '<span>定位源文件 &amp; 目录</span>';
  status.textContent = '上一次的错误';
  document.body.append(button, status);
  button.focus();
  return { button, status, html: button.innerHTML };
}

describe('定位资产动作', () => {
  it('等待留在可聚焦按钮中，重复触发不重发；成功才回执并恢复原按钮', async () => {
    let release!: () => void;
    const request = vi.fn(() => new Promise<void>((done) => { release = done })), success = vi.fn();
    const reveal = createRevealSourceAction({ request, success, failureText: () => '' });
    const { button, status, html } = surface();
    const pending = reveal(41, status, { button });
    expect(request).toHaveBeenCalledWith(41);
    expect(status.textContent).toBe('');
    expect(button.getAttribute('aria-busy')).toBe('true');
    expect(button.getAttribute('aria-disabled')).toBe('true');
    expect(button.disabled).toBe(false);
    expect(document.activeElement).toBe(button);
    expect(button.querySelector('[role="status"]')?.getAttribute('aria-label')).toBe('正在定位');
    expect(button.textContent).toContain('定位源文件 & 目录');
    await reveal(41, status, { button });
    expect(request).toHaveBeenCalledTimes(1);
    expect(success).not.toHaveBeenCalled();
    release(); await pending;
    expect(success).toHaveBeenCalledTimes(1);
    expect(status.textContent).toBe('');
    expect(button.innerHTML).toBe(html);
    expect(button.hasAttribute('aria-busy')).toBe(false);
    expect(button.hasAttribute('aria-disabled')).toBe(false);
  });

  it('失败留在动作旁边，不发成功回执，忙态恢复后可以重试', async () => {
    const missing = new Error('源文件离线'), request = vi.fn().mockRejectedValueOnce(missing).mockResolvedValueOnce(undefined);
    const success = vi.fn(), failureText = vi.fn(() => '源文件不在这台机器上');
    const reveal = createRevealSourceAction({ request, success, failureText });
    const { button, status, html } = surface();
    await reveal(41, status, { button });
    expect(failureText).toHaveBeenCalledWith(missing);
    expect(status.textContent).toBe('源文件不在这台机器上');
    expect(success).not.toHaveBeenCalled();
    expect(button.innerHTML).toBe(html);
    expect(button.hasAttribute('aria-busy')).toBe(false);
    await reveal(41, status, { button });
    expect(request).toHaveBeenCalledTimes(2);
    expect(status.textContent).toBe('');
    expect(success).toHaveBeenCalledTimes(1);
  });

  it('React 调用方接收失败文本并自行画等待态，不需要提供按钮', async () => {
    const status = { textContent: '' }, success = vi.fn();
    const reveal = createRevealSourceAction({ request: async () => { throw new Error('offline') },
      success, failureText: () => '脱盘' });
    await reveal(42, status);
    expect(status.textContent).toBe('脱盘');
    expect(success).not.toHaveBeenCalled();
  });
});
