import { act } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { CONFIGURATION_URL, PICK_FOLDER_URL } from '../../src/configuration-endpoints';
import type { ConfigurationData } from '../../src/react/bundle';
import { CloudDriveGuide } from '../../src/react/settings/clouddrive-guide';
import { MEDIA_SOURCE_ICONS } from '../../src/ui-kit/media-source-icons';
import { MediaSettings } from '../../src/react/settings/media-settings';
import { buttonNamed, choose, click, fetchMock, mount, section, sentBody, settle, submit } from './render';

vi.mock('../../src/react/settings/service-preferences', () => ({ VideoThumbnailRow: () => null }));
vi.mock('@peach/legacy/ui', async (original) => ({
  ...await original<Record<string, unknown>>(),
  MEDIA_SOURCE_ICONS: (await import('../../src/ui-kit/media-source-icons')).MEDIA_SOURCE_ICONS,
}));

const data = (over: Partial<ConfigurationData> = {}): ConfigurationData => ({
  editable: true, notice: '', revision: 'rev-1', media_dirs: ['D:\\Media'], library_count: 1, port: 9123, facts: [], ...over,
});

const open = (over: Partial<ConfigurationData> = {}, receipt = vi.fn()) =>
  mount(<MediaSettings data={data(over)} receipt={receipt} />);
const rows = (root: ParentNode) => [...root.querySelectorAll('[data-folder-row]')];
const paths = (root: ParentNode) => [...root.querySelectorAll<HTMLInputElement>('input[aria-label^="媒体目录 "]')];

it('媒体表单保留重启提示，访问端口由网络分区管理', async () => {
  const host = await open({ port_editable: false });
  expect(host.querySelector('#configPort')).toBeNull();
  expect(host.textContent).toContain('保存后 Peach 会重新启动');
  expect(host.querySelector('form')).not.toBeNull();
});

it('网盘来源缺依赖时给新窗口下载链接，已装的不提示', async () => {
  const host = await open({
    media_sources: [{ location: '115', root: 'B:\\', path: 'B:\\' }],
    mount_dependencies: [
      { name: 'CloudDrive', available: false, download_url: 'https://www.clouddrive2.com/download.html' },
      { name: 'WinFsp', available: true, download_url: 'https://winfsp.dev/rel/' },
    ],
  });
  const link = host.querySelector('a[href="https://www.clouddrive2.com/download.html"]');
  expect(link?.getAttribute('target')).toBe('_blank');
  expect(link?.querySelector('svg')).not.toBeNull();
  expect(host.textContent).not.toContain('下载 WinFsp');
});

it('不能编辑时只说原因，不画表单', async () => {
  const host = await open({ editable: false, notice: '请在运行 Peach 的电脑上打开配置' });
  expect(host.querySelector('form')).toBeNull();
  expect(host.querySelector('[role="note"]')?.textContent).toContain('请在运行 Peach 的电脑上打开配置');
});

describe('媒体目录', () => {
  it.each([['guangya', '光鸭云盘'], ['onedrive', 'OneDrive'], ['mounted', '其他已挂载网盘']])(
    '%s 来源回填名称、挂载帮助和现有图标，保存带上来源', async (location, label) => {
    const fetcher = fetchMock(200, { saved: true, url: '/', revision: 'rev-2' });
    vi.stubGlobal('fetch', fetcher);
    const host = await open({ windows: true, media_sources: [
      { location, root: 'G:\\Movies', path: 'G:\\Movies', online: true },
    ] });
    expect(paths(host)[0]?.value).toBe('G:\\Movies');
    expect(host.textContent).toContain(label);
    expect(host.textContent).not.toContain('（只读）');
    if (location === 'mounted') expect(host.querySelector('use[href="#i-database"]')).not.toBeNull();
    else expect(host.querySelector('img')?.getAttribute('src')).toBe(MEDIA_SOURCE_ICONS[location]);
    expect(host.textContent).toContain('CloudDrive2 v1.1.0');
    await submit(host.querySelector('form'));
    expect(sentBody(fetcher).media_sources).toEqual([
      { location, root: 'G:\\Movies', path: 'G:\\Movies', library: '', library_icon: '',
        cost: location === 'guangya' ? 'free' : 'metered' },
    ]);
  });

  it('Windows 只填本机路径，选了网盘才出 CloudDrive 帮助', async () => {
    const host = await open({ windows: true, media_sources: [{ location: 'local', root: 'D:/', path: 'D:/' }] });
    expect(host.textContent).not.toContain('Windows 中的对应路径');
    expect(host.textContent).not.toContain('挂载帮助');
    await choose(rows(host)[0]?.querySelector('[aria-haspopup="listbox"]'), 'CloudDrive · 115');
    expect(host.textContent).toContain('挂载帮助');
  });

  it('其它系统每行只展示媒体目录和各自的访问状态', async () => {
    const host = await open({ windows: false, media_sources: [
      { location: '115', root: 'B:/', path: '/Volumes/115', online: true },
      { location: 'pikpak', root: 'A:/', path: '/Volumes/PikPak', online: false },
      { location: 'local', root: 'D:/', path: '/Volumes/Media' },
    ] });
    expect(rows(host)).toHaveLength(3);
    expect(rows(host).some((row) => row.textContent?.includes('高级路径设置'))).toBe(false);
    const internalPaths = [...host.querySelectorAll('input[placeholder="自动配置"]')];
    expect(internalPaths).toHaveLength(0);
    expect(rows(host).map((row) => [row.querySelector('[data-mount]')?.getAttribute('data-mount'), row.querySelector('[data-mount]')?.textContent]))
      .toEqual([['online', '在线'], ['offline', '离线'], ['unknown', '未检测']]);
    expect(section(host, '挂载状态')).toBeNull();
  });

  /* 只读时单列的「挂载状态」与它的刷新键在 `configuration.test.tsx` 里：刷新换进的是整页那一个
     `queryKey`，只挂这一个分区看不出换进去之后谁读到了新的。 */

  it('按来源回填全部挂载点，提交带上盘符映射与媒体库', async () => {
    const fetcher = fetchMock(200, { saved: true, url: '/', revision: 'rev-2' });
    vi.stubGlobal('fetch', fetcher);
    const host = await open({ windows: false, media_sources: [
      { location: '115', root: 'B:\\', path: '/Volumes/115', online: false, library: '收藏', library_icon: 'heart' },
      { location: 'pikpak', root: 'A:\\', path: '/Volumes/PikPak', online: true },
    ] });
    expect(paths(host).map((input) => input.value)).toEqual(['/Volumes/115', '/Volumes/PikPak']);
    await submit(host.querySelector('form'));
    await settle();
    expect(sentBody(fetcher).media_sources).toEqual([
      { location: '115', root: 'B:\\', path: '/Volumes/115', library: '收藏', library_icon: 'heart', cost: 'free' },
      { location: 'pikpak', root: 'A:\\', path: '/Volumes/PikPak', library: '', library_icon: '', cost: 'metered' },
    ]);
  });

  it('已保存的目录回填代理保护，用户可改为正常读取', async () => {
    const fetcher = fetchMock(200, { saved: true, url: '/', revision: 'rev-2' });
    vi.stubGlobal('fetch', fetcher);
    const host = await open({ windows: true, media_sources: [
      { location: 'onedrive', root: 'O:\\Movies', path: 'O:\\Movies', cost: 'metered' },
    ] });
    const policy = rows(host)[0]?.querySelector('[data-media-read-policy] [aria-haspopup="listbox"]');
    expect(policy?.textContent).toContain('代理流量保护');
    await choose(policy, '正常读取');
    await submit(host.querySelector('form'));
    expect(sentBody(fetcher).media_sources).toEqual([
      { location: 'onedrive', root: 'O:\\Movies', path: 'O:\\Movies', library: '', library_icon: '', cost: 'free' },
    ]);
  });

  it('切换到网盘来源后必须明确选择读取策略才能保存', async () => {
    const fetcher = fetchMock(200, { saved: true, url: '/', revision: 'rev-2' });
    vi.stubGlobal('fetch', fetcher);
    const host = await open({ windows: true, media_sources: [
      { location: 'local', root: 'O:\\Movies', path: 'O:\\Movies', cost: 'free' },
    ] });
    await choose(rows(host)[0]?.querySelector('[aria-haspopup="listbox"]'), 'CloudDrive · PikPak');
    const policy = rows(host)[0]?.querySelector('[data-media-read-policy] [aria-haspopup="listbox"]');
    expect(policy?.textContent).toContain('选择读取策略');
    await submit(host.querySelector('form'));
    expect(fetcher).not.toHaveBeenCalled();
    expect(host.textContent).toContain('请选择媒体读取策略');
    await choose(policy, '正常读取');
    await submit(host.querySelector('form'));
    expect(sentBody(fetcher).media_sources[0]).toMatchObject({ location: 'pikpak', cost: 'free' });
  });

  it('一行一个文件夹，只有一行时没有移除键', async () => {
    const host = await open();
    expect(paths(host).map((input) => input.value)).toEqual(['D:\\Media']);
    expect(host.querySelector('[aria-label="移除这个文件夹"]')).toBeNull();
  });

  it('新加的一行接过焦点，之后每行都能移除', async () => {
    const host = await open();
    await click(buttonNamed('添加文件夹', host));
    expect(paths(host)).toHaveLength(2);
    expect(document.activeElement).toBe(paths(host)[1]);
    const removes = host.querySelectorAll('[aria-label="移除这个文件夹"]');
    expect(removes).toHaveLength(2);
    await click(removes[0]);
    expect(paths(host).map((input) => input.value)).toEqual(['']);
  });
});

describe('选择文件夹', () => {
  it('让这台电脑弹系统对话框，选中的路径填回这一行', async () => {
    const fetcher = fetchMock(200, { path: 'E:\\Movies' });
    vi.stubGlobal('fetch', fetcher);
    const host = await open();
    const pick = host.querySelector('[aria-label="选择文件夹"]')!;
    await click(pick);
    await settle();
    const [url, init] = fetcher.mock.calls[0] ?? [];
    expect(url).toBe(PICK_FOLDER_URL);
    expect(init?.method).toBe('POST');
    expect(sentBody(fetcher)).toEqual({ initial: 'D:\\Media' });
    expect(paths(host)[0]?.value).toBe('E:\\Movies');
    expect(pick.getAttribute('aria-busy'), '对话框关了忙态要撤掉').toBeNull();
  });

  it('取消什么也不改；打不开对话框时原因写在这一行', async () => {
    vi.stubGlobal('fetch', fetchMock(200, { path: null }));
    const host = await open();
    await click(host.querySelector('[aria-label="选择文件夹"]'));
    await settle();
    expect(paths(host)[0]?.value).toBe('D:\\Media');
    expect(paths(host)[0]?.getAttribute('aria-invalid')).toBeNull();
    vi.stubGlobal('fetch', fetchMock(501, { error: '这个系统上没有可用的文件夹对话框' }));
    await click(host.querySelector('[aria-label="选择文件夹"]'));
    await settle();
    expect(rows(host)[0]?.textContent).toContain('没有可用的文件夹对话框');
    expect(paths(host)[0]?.getAttribute('aria-invalid')).toBe('true');
  });
});

describe('保存', () => {
  it('提交带上指纹、全部文件夹、端口与是否扫描，成功后回执并留下新地址', async () => {
    const fetcher = fetchMock(200, { saved: true, url: 'http://127.0.0.1:9124/', revision: 'rev-2' });
    vi.stubGlobal('fetch', fetcher);
    const receipt = vi.fn();
    const host = await open({}, receipt);
    expect(host.querySelector('#configPort')).toBeNull();
    await submit(host.querySelector('form'));
    await settle();
    const [url, init] = fetcher.mock.calls[0] ?? [];
    expect(url).toBe(CONFIGURATION_URL);
    expect(init?.method).toBe('POST');
    expect(sentBody(fetcher)).toEqual({ revision: 'rev-1', media_dirs: ['D:\\Media'], port: '9123', scan_now: false });
    expect(receipt).toHaveBeenCalledWith('已保存配置');
    expect(host.querySelector('[role="status"]')?.textContent).toContain('正在重新启动');
    expect(host.querySelector('a[href="http://127.0.0.1:9124/"]')?.textContent).toBe('进入馆藏');
    expect(host.querySelector('form'), '保存后表单不再留在页面上').toBeNull();
  });

  it('服务端按字段退回的原因写回原位，不发回执', async () => {
    vi.stubGlobal('fetch', fetchMock(400, {
      error: '有几项需要修改',
      errors: { media_dirs: ['', '和上面的文件夹重复了'] },
    }));
    const receipt = vi.fn();
    const host = await open({ media_dirs: ['D:\\Media', 'D:\\Media'] }, receipt);
    await submit(host.querySelector('form'));
    await settle();
    expect(paths(host).map((input) => input.getAttribute('aria-invalid'))).toEqual([null, 'true']);
    expect(rows(host)[1]?.textContent).toContain('和上面的文件夹重复了');
    expect(host.querySelector('#configPort')).toBeNull();
    expect(receipt).not.toHaveBeenCalled();
    expect(host.textContent, '字段级原因不再叠一条整体提示').not.toContain('没有保存');
  });

  it('其它失败留在表单里等重试', async () => {
    vi.stubGlobal('fetch', fetchMock(409, { error: '配置已变更，请刷新后再保存' }));
    const host = await open();
    await submit(host.querySelector('form'));
    await settle();
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('配置已变更');
    expect(host.querySelector('form')).not.toBeNull();
    expect(buttonNamed('保存配置', host)?.getAttribute('aria-busy'), '失败后忙态要撤掉').toBeNull();
  });
});

it('CloudDrive 建议按硬盘分三档，页内只留怎么填，原理链到仓库文档', async () => {
  const host = await mount(<CloudDriveGuide />);
  const tiers = host.querySelectorAll('[aria-label="按缓存所在硬盘分档"] > li');
  expect(tiers).toHaveLength(3);
  expect([...tiers[0]!.querySelectorAll('dt')].map((dt) => dt.textContent))
    .toEqual(['缓存上限', '读取长度（默认 / 最小）', '同时处理视频']);
  expect(host.textContent).toContain('256 / 128 KB');
  expect(host.textContent).toContain('上限不要填 0');
  expect(host.querySelector('details')?.open).toBe(false);
  expect([...host.querySelectorAll('a')].map((a) => a.getAttribute('href'))).toEqual([
    'https://www.clouddrive2.com/help.html',
    'https://github.com/peach-mitao/peach/blob/master/docs/CLOUDDRIVE.md',
  ]);
});

it('折叠开合走共用 Collapse：点开长到内容高度，收起时高度收回 0 并锁住正文', async () => {
  vi.useFakeTimers();
  const host = await mount(<CloudDriveGuide />);
  const details = host.querySelector('details')!;
  const summary = details.querySelector('summary')!;
  const body = document.getElementById(summary.getAttribute('aria-controls')!)!;
  expect(body.parentElement).toBe(details);
  expect(body.inert).toBe(true);
  await click(summary);
  expect(details.open).toBe(true);
  expect(summary.getAttribute('aria-expanded')).toBe('true');
  expect(body.classList.contains('ui-fcollapse')).toBe(true);
  expect(body.inert).toBe(false);
  await act(async () => { vi.advanceTimersByTime(260); });
  expect(body.style.height).toBe('auto');
  await click(summary);
  expect(summary.getAttribute('aria-expanded')).toBe('false');
  expect(body.style.height).toBe('0px');
  expect(body.inert).toBe(true);
});
