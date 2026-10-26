/** 应用生命周期内的监听、观察者与调度。销毁后排队的回调不再执行。 */
export class ApplicationEffects {
  private cleanups = new Set<() => void>();
  private active = true;
  private disposed = false;
  private abort = new AbortController();

  get isActive(): boolean { return this.active && !this.disposed; }
  get signal(): AbortSignal { return this.abort.signal; }
  pause(): void { this.active = false; }
  resume(): void { if (!this.disposed) this.active = true; }

  own(dispose: () => void): () => void {
    let owned = true;
    const release = () => { if (!owned) return; owned = false; this.cleanups.delete(release); dispose(); };
    if (this.disposed) release(); else this.cleanups.add(release);
    return release;
  }

  guard<Args extends unknown[], Result>(callback: (...args: Args) => Result): (...args: Args) => Result | undefined {
    return (...args) => this.isActive ? callback(...args) : undefined;
  }

  handler(target: object, key: string, listener: EventListener): void {
    const object = target as unknown as Record<string, unknown>;
    const previous = object[key];
    const current: EventListener = event => { if (this.isActive) listener.call(target, event); };
    object[key] = current;
    this.own(() => { if (object[key] === current) object[key] = previous; });
  }

  listen(target: EventTarget, type: string, listener: EventListenerOrEventListenerObject,
    options?: boolean | AddEventListenerOptions): void {
    const current: EventListener = event => {
      if (!this.isActive) return;
      if (typeof listener === 'function') listener.call(target, event); else listener.handleEvent(event);
    };
    const capture = typeof options === 'boolean' ? options : options?.capture ?? false;
    target.addEventListener(type, current, options);
    this.own(() => target.removeEventListener(type, current, capture));
  }

  delay(callback: (...args: unknown[]) => void, timeout = 0, ...args: unknown[]): number {
    if (!this.isActive) return 0;
    let release = () => {};
    const handle = window.setTimeout(() => { release(); if (this.isActive) callback(...args); }, timeout);
    release = this.own(() => clearTimeout(handle));
    return handle;
  }

  frame(callback: FrameRequestCallback): number {
    if (!this.isActive) return 0;
    let release = () => {};
    const handle = requestAnimationFrame(time => { release(); if (this.isActive) callback(time); });
    release = this.own(() => cancelAnimationFrame(handle));
    return handle;
  }

  mutation(callback: MutationCallback): MutationObserver {
    const observer = new MutationObserver((records, current) => { if (this.isActive) callback(records, current); });
    const observe = observer.observe.bind(observer);
    observer.observe = (...args) => { if (this.isActive) observe(...args); };
    this.own(() => observer.disconnect());
    return observer;
  }

  resize(callback: ResizeObserverCallback): ResizeObserver {
    const observer = new ResizeObserver((entries, current) => { if (this.isActive) callback(entries, current); });
    const observe = observer.observe.bind(observer);
    observer.observe = (...args) => { if (this.isActive) observe(...args); };
    this.own(() => observer.disconnect());
    return observer;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.active = false;
    this.abort.abort();
    const errors: unknown[] = [];
    for (const cleanup of [...this.cleanups].reverse()) {
      try { cleanup(); } catch (error) { errors.push(error); }
    }
    if (errors.length) throw new AggregateError(errors, '应用资源清理未全部完成');
  }
}

export let applicationEffects = new ApplicationEffects();
export function renewApplicationEffects(): ApplicationEffects {
  applicationEffects.dispose();
  applicationEffects = new ApplicationEffects();
  return applicationEffects;
}
