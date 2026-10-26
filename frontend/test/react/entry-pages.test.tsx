import { describe, expect, it } from 'vitest';

import { ErrorPage } from '../../src/react/pages/error/error-page';
import { LoginPage, WRONG_PASSWORD } from '../../src/react/pages/login/login-page';
import { click, mount, type } from './render';

const tokenInput = (root: ParentNode) => root.querySelector<HTMLInputElement>('input[name="token"]');
const alerts = (root: ParentNode) => [...root.querySelectorAll('[role="alert"]')];
const entries = (root: ParentNode) => [...new FormData(root.querySelector('form')!).entries()];

describe('登录页', () => {
  it('原生表单提交 token、next 与勾选时的 days=30', async () => {
    const host = await mount(<LoginPage data={{ next: '/stats?x=1' }} />);
    const form = host.querySelector('form')!;
    expect(form.getAttribute('method')).toBe('post');
    expect(form.getAttribute('action')).toBe('/login');
    const input = tokenInput(host)!;
    expect(input.type).toBe('password');
    expect(input.required).toBe(true);
    expect(input.maxLength).toBe(256);
    expect(input.getAttribute('autocomplete')).toBe('current-password');
    expect(document.activeElement).toBe(input);
    expect(input.getAttribute('aria-invalid')).toBeNull();
    expect(alerts(host)).toHaveLength(0);
    await type(input, 'secret');
    expect(entries(host)).toEqual([['token', 'secret'], ['next', '/stats?x=1'], ['days', '30']]);
    await click(host.querySelector('input[name="days"]'));
    expect(entries(host)).toEqual([['token', 'secret'], ['next', '/stats?x=1']]);
    expect(form.checkValidity()).toBe(true);
  });

  it('没给 next 时回首页', async () => {
    const host = await mount(<LoginPage data={{}} />);
    expect(entries(host)).toContainEqual(['next', '/']);
  });

  it('口令不对时框标错、原因由读屏播报、焦点落在框里，改了内容就撤掉', async () => {
    const host = await mount(<LoginPage data={{ next: '/', invalid: 'true' }} />);
    const input = tokenInput(host)!;
    expect(document.activeElement).toBe(input);
    expect(input.getAttribute('aria-invalid')).toBe('true');
    const [alert] = alerts(host);
    expect(alert?.textContent).toBe(WRONG_PASSWORD);
    const described = (input.getAttribute('aria-describedby') ?? '').split(' ');
    expect(described.some((id) => document.getElementById(id)?.contains(alert!))).toBe(true);
    await type(input, 'again');
    expect(input.getAttribute('aria-invalid')).toBeNull();
    expect(alerts(host)).toHaveLength(0);
    expect(host.querySelector('form')!.checkValidity()).toBe(true);
  });

  it('限流与参数错误的原因照同一处写', async () => {
    for (const reason of ['尝试次数较多，请一分钟后重试', '请选择有效的保持登录时间']) {
      const host = await mount(<LoginPage data={{ next: '/', error: reason }} />);
      const input = tokenInput(host)!;
      expect(input.getAttribute('aria-invalid')).toBe('true');
      expect(alerts(host).map((node) => node.textContent)).toEqual([reason]);
      host.remove();
    }
  });
});

describe('错误页', () => {
  const cases = [
    { status: '403', title: '这里不能打开', lede: '请在运行 Peach 的电脑上打开配置' },
    { status: '404', title: '四〇四', lede: null },
    { status: '409', title: '现在不能这样做', lede: '请先完成首次设置' },
    { status: '500', title: '出了点问题', lede: '服务出错' },
  ];
  for (const { status, title, lede } of cases) {
    it(`${status} 的标题与说明`, async () => {
      const detail = lede ?? '这个地址下没有页面。';
      const host = await mount(<ErrorPage data={{ status, detail }} />);
      expect(host.querySelector('h1')?.textContent).toBe(title);
      expect(document.title).toBe(`Peach · ${title}`);
      expect(host.textContent?.includes(detail)).toBe(lede !== null);
      const home = host.querySelector('a');
      expect(home?.getAttribute('href')).toBe('/');
      expect(home?.textContent).toBe('返回首页');
    });
  }
});
