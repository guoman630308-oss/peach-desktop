import { expect, it, vi } from 'vitest';
import { PortSettings } from '../../src/react/settings/port-settings';
import type { ConfigurationData } from '../../src/react/bundle';
import { fetchMock, mount, sentBody, settle, submit, type } from './render';
const data: ConfigurationData = { editable: true, notice: '', revision: 'rev-1',
  media_dirs: ['D:\\Media'], media_sources: [{ location: 'local', root: 'D:\\Media', path: 'D:\\Media' }],
  library_count: 1, port: 9123, facts: [] };
it('保存端口保留已保存的媒体目录与来源映射', async () => {
  const fetcher = fetchMock(200, { saved: true, revision: 'rev-2', url: 'http://127.0.0.1:9124/' });
  vi.stubGlobal('fetch', fetcher);
  const receipt = vi.fn();
  const host = await mount(<PortSettings data={data} receipt={receipt} />);
  await type(host.querySelector<HTMLInputElement>('#configPort'), '9124');
  await submit(host.querySelector('form'));
  await settle();
  expect(sentBody(fetcher)).toEqual({ revision: 'rev-1', media_dirs: data.media_dirs,
    media_sources: data.media_sources, port: '9124', scan_now: false });
  expect(receipt).toHaveBeenCalledWith('已保存配置');
  expect(host.querySelector('a')?.getAttribute('href')).toBe('http://127.0.0.1:9124/');
});
it('端口校验失败保留输入，错误显示在字段旁', async () => {
  vi.stubGlobal('fetch', fetchMock(400, { errors: { port: '端口范围无效' } }));
  const receipt = vi.fn();
  const host = await mount(<PortSettings data={data} receipt={receipt} />);
  await type(host.querySelector<HTMLInputElement>('#configPort'), '1');
  await submit(host.querySelector('form')); await settle();
  expect(host.querySelector('#configPort')?.getAttribute('aria-invalid')).toBe('true');
  expect(host.textContent).toContain('端口范围无效');
  expect(host.querySelector<HTMLInputElement>('#configPort')?.value).toBe('1');
  expect(receipt).not.toHaveBeenCalled();
});
