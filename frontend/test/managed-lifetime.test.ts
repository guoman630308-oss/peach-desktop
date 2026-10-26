import { expect, it, vi } from 'vitest';

it('应用收起全部页面时同时取消未落地预取，晚到响应不挂宿主', async () => {
  vi.resetModules();
  const managed = await import('../src/history/managed');
  const container = document.createElement('div');
  container.textContent = '正在读取'; document.body.append(container);
  let signal: AbortSignal | undefined;
  let release = () => {}, entered = () => {};
  const gate = new Promise<void>(resolve => { release = resolve; });
  const started = new Promise<void>(resolve => { entered = resolve; });
  managed.connectManagedRoutes(Promise.resolve(async (_path, _props, current) => {
    signal = current; entered(); await gate;
  }));
  const place = vi.fn(() => container);
  const opened = managed.openManagedRoute('/', {}, { container, isCurrent: () => true, place });
  await started;
  managed.releaseAllManagedRoutes();
  expect(signal?.aborted).toBe(true);
  release(); expect(await opened).toBe(false);
  expect(place).not.toHaveBeenCalled(); expect(container.textContent).toBe('正在读取');
  expect(managed.managedEntries()).toEqual([]); container.remove();
});

it('应用收起全部登记只撤页面宿主，保留静态常驻容器', async () => {
  vi.resetModules();
  const managed = await import('../src/history/managed');
  const page = document.createElement('div'), resident = document.createElement('div');
  document.body.append(page, resident);
  managed.connectManagedRoutes(Promise.resolve(async () => {}));
  await managed.openManagedRoute('/', {}, { container: page, isCurrent: () => true });
  await managed.openResidentSurface('batch-dock', resident);
  expect(managed.managedEntries()).toHaveLength(2);
  managed.releaseAllManagedRoutes(); managed.releaseAllManagedRoutes();
  expect(managed.managedEntries()).toEqual([]); expect(page.children).toHaveLength(0);
  expect(resident.isConnected).toBe(true); page.remove(); resident.remove();
});
