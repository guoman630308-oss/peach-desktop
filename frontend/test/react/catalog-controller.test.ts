import { describe, expect, it } from 'vitest';

import { CatalogController } from '../../src/react/catalog-grid/catalog-controller';

describe('网格代次与落地等待', () => {
  it('首屏提供取消信号，落地后同页筛选只更新 props', async () => {
    const controller = new CatalogController();
    const first = controller.begin('/', false);
    expect(first.mount).toBe(true); expect(first.signal?.aborted).toBe(false);
    expect(controller.taken(false)).toBe(true);
    controller.settle(first.revision); await first.settled; first.finish(true);
    const second = controller.begin('/', true);
    expect(second.mount).toBe(false); expect(second.signal).toBeNull();
    expect(controller.pending).toBe(false); expect(controller.taken(true)).toBe(true);
    controller.settle(second.revision); await second.settled;
  });

  it('连续首屏筛选取消旧预取，旧回调不能放行新代次', async () => {
    const controller = new CatalogController();
    const old = controller.begin('/', false), latest = controller.begin('/', false);
    let latestSettled = false; void latest.settled.then(() => { latestSettled = true; });
    expect(old.signal?.aborted).toBe(true); expect(old.isCurrent()).toBe(false);
    expect(latest.isCurrent()).toBe(true); await old.settled;
    old.finish(false); controller.settle(old.revision); await Promise.resolve();
    expect(latestSettled).toBe(false); expect(controller.pending).toBe(true);
    controller.settle(latest.revision); await latest.settled; latest.finish(true);
    expect(latestSettled).toBe(true); expect(controller.pending).toBe(false);
  });

  it('切到垃圾队列取消目录代次，即使旧容器已挂载也必须重开', async () => {
    const controller = new CatalogController(), catalog = controller.begin('/', false);
    const junk = controller.begin('/junk-files', true);
    expect(junk.mount).toBe(true); expect(controller.page).toBe('/junk-files');
    expect(catalog.signal?.aborted).toBe(true); await catalog.settled;
    controller.settle(catalog.revision);
    expect(junk.isCurrent()).toBe(true);
    controller.settle(junk.revision); await junk.settled;
  });

  it('离开页面释放等待并取消预取，代次在重新进入后继续增长', async () => {
    const controller = new CatalogController(), first = controller.begin('/', false);
    controller.clear(); await first.settled;
    expect(first.signal?.aborted).toBe(true); expect(first.isCurrent()).toBe(false);
    expect(controller.taken(false)).toBe(false); expect(controller.page).toBe('');
    const next = controller.begin('/', false);
    expect(next.revision).toBeGreaterThan(first.revision); expect(next.isCurrent()).toBe(true);
    controller.clear(); await next.settled;
  });

  it('失败首屏释放自己的等待，旧失败不影响当前预取', async () => {
    const controller = new CatalogController(), first = controller.begin('/', false);
    first.finish(false); await first.settled;
    expect(controller.pending).toBe(false);
    const next = controller.begin('/', false);
    first.finish(false);
    expect(controller.pending).toBe(true);
    next.finish(false); await next.settled;
    expect(controller.pending).toBe(false);
  });

  it('快速筛选的每个过期等待都有终点，未知未来代次不能放行当前屏', async () => {
    const controller = new CatalogController(), obsolete: Promise<void>[] = [];
    for (let i = 0; i < 50; i++) obsolete.push(controller.begin('/', false).settled);
    let settled = false; const latest = controller.begin('/', false);
    void latest.settled.then(() => { settled = true; });
    await Promise.all(obsolete);
    controller.settle(latest.revision + 1); await Promise.resolve();
    expect(settled).toBe(false);
    controller.clear(); await latest.settled; expect(settled).toBe(true);
  });
});
