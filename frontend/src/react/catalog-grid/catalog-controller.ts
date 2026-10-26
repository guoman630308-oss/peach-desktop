export interface CatalogRender {
  revision: number;
  settled: Promise<void>;
  mount: boolean;
  signal: AbortSignal | null;
  isCurrent(): boolean;
  finish(mounted: boolean): void;
}

interface Painting { revision: number; abort: AbortController }

/** 只管理网格代次、首屏取消与落地等待；请求、缓存与页面归现有 Query 和路由。 */
export class CatalogController {
  private currentRevision = 0;
  private currentPage = '';
  private painting: Painting | null = null;
  private waiters = new Map<number, () => void>();

  get revision(): number { return this.currentRevision; }
  get page(): string { return this.currentPage; }
  get pending(): boolean { return this.painting !== null; }

  taken(mounted: boolean): boolean { return mounted || this.pending; }

  /** 同一页已落地时只更新 props；首屏尚未完成时取消上一轮再打开。 */
  begin(page: string, mounted: boolean): CatalogRender {
    const changedPage = !!this.currentPage && this.currentPage !== page;
    if (changedPage) this.clear();
    // 每轮只有一个有效等待：被替代的调用返回，其落地回调仍按代次隔离。
    this.releaseWaiters();
    this.currentPage = page;
    const revision = ++this.currentRevision;
    const settled = new Promise<void>(resolve => this.waiters.set(revision, resolve));
    const mount = changedPage || !mounted || this.pending;
    let painting: Painting | null = null;
    if (mount) {
      this.painting?.abort.abort();
      painting = { revision, abort: new AbortController() };
      this.painting = painting;
    }
    const current = () => this.currentPage === page && this.currentRevision === revision
      && (!painting || !painting.abort.signal.aborted);
    return {
      revision, settled, mount, signal: painting?.abort.signal ?? null, isCurrent: current,
      finish: (landed: boolean) => {
        if (painting && this.painting === painting) {
          this.painting = null;
          if (!landed) this.releaseWaiters();
        }
      },
    };
  }

  /** 页面只交回自己的代次；旧页面不能放行后续筛选的等待。 */
  settle(revision: number): void {
    if (revision > this.currentRevision) return;
    for (const [requested, resolve] of this.waiters) {
      if (requested <= revision) { this.waiters.delete(requested); resolve(); }
    }
  }

  /** 离开网格时取消预取，释放全部等待；代次单调递增。 */
  clear(): void {
    this.painting?.abort.abort();
    this.painting = null;
    this.currentPage = '';
    this.releaseWaiters();
  }

  private releaseWaiters(): void {
    const waiters = [...this.waiters.values()];
    this.waiters.clear();
    waiters.forEach(resolve => resolve());
  }
}
