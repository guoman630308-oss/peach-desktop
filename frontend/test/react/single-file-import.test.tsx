import { expect, it, vi } from 'vitest';
import { SingleFileImportDialog } from '../../src/react/library-processing/single-file-import-dialog';
import { buttonNamed, click, mount, submit, type } from './render';

it('选择视频后只提交该文件路径并交接到处理任务', async () => {
  const calls: { path: string; body: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body));
    calls.push({ path, body });
    return { ok: true, status: 200, json: async () => path === '/api/pick-media-file'
      ? { path: 'E:\\Media\\ABC-101.mp4' }
      : { status: 'running', job_id: 'single-video', asset_ids: [4] } };
  }));
  const close = vi.fn();
  const started = vi.fn();
  await mount(<SingleFileImportDialog close={close} onStarted={started} />);
  await click(buttonNamed('浏览'));
  expect(document.querySelector<HTMLInputElement>('input')?.value).toBe('E:\\Media\\ABC-101.mp4');
  await click(buttonNamed('添加并采集'));
  expect(calls).toEqual([
    { path: '/api/pick-media-file', body: {} },
    { path: '/api/library-processing/import-file', body: { path: 'E:\\Media\\ABC-101.mp4' } },
  ]);
  expect(started).toHaveBeenCalledWith({ status: 'running', job_id: 'single-video', asset_ids: [4] });
  expect(close).toHaveBeenCalledOnce();
});

it('手动路径可回车提交，失败后保留输入和错误供重试', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 400,
    json: async () => ({ error: '此文件不在媒体目录内' }) })));
  const close = vi.fn();
  await mount(<SingleFileImportDialog close={close} onStarted={vi.fn()} />);
  await type(document.querySelector('input'), 'E:\\Outside\\sample.mp4');
  await submit(document.querySelector('form'));
  expect(document.body.textContent).toContain('此文件不在媒体目录内');
  expect(document.querySelector<HTMLInputElement>('input')?.value).toBe('E:\\Outside\\sample.mp4');
  expect(close).not.toHaveBeenCalled();
});

it('取消文件选择不会发起入库任务', async () => {
  const fetcher = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ path: null }) }));
  vi.stubGlobal('fetch', fetcher);
  const started = vi.fn();
  await mount(<SingleFileImportDialog close={vi.fn()} onStarted={started} />);
  await click(buttonNamed('浏览'));
  expect(fetcher).toHaveBeenCalledOnce();
  expect(started).not.toHaveBeenCalled();
  expect(buttonNamed('添加并采集')?.hasAttribute('disabled')).toBe(true);
});
