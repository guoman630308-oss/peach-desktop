/* 界面偏好在这台浏览器上的那一份真相（`peach.settings.v1`）。
 *
 * 值是壳在启动时归一化好的那个对象本身（`application/preferences.js` 的 `appSettings`）：壳里六十来处读写都
 * 直接改它的字段，改完调 `save()` 落盘。落盘同时换一个版本号、通知订阅者——设置面板
 * （`react/settings-panel/`）用 `useSyncExternalStore` 读版本号，壳在哪里改了一项，开着的面板当场跟上，
 * 两边读的是同一个对象，不各存一份。
 *
 * 快照是版本号而不是对象：壳的写法是原地改字段，对象引用从头到尾不变，拿它当快照 React 永远
 * 以为没变。本模块由 Application 与 React 页面直接引用，启动时同步可用；面板只拿壳递过去的实例，自己不建第二个。 */
export interface SettingsStore<T extends object> {
  /** 活的设置对象，读到的就是此刻的值。 */
  readonly value: T;
  /** 每次 `save()` 或 `notify()` 加一。 */
  version(): number;
  subscribe(listener: () => void): () => void;
  /** 写进 localStorage，再通知订阅者。 */
  save(): void;
  /** 值不在这份对象里、但面板要跟着重画时（高对比度、SFW 这两样各有自己的键）只通知不落盘。 */
  notify(): void;
}

export function createSettingsStore<T extends object>(key: string, value: T): SettingsStore<T> {
  const listeners = new Set<() => void>();
  let version = 0;
  const notify = () => {
    version += 1;
    listeners.forEach((listener) => listener());
  };
  return {
    value,
    version: () => version,
    subscribe(listener) {
      listeners.add(listener);
      return () => { listeners.delete(listener) };
    },
    save() {
      localStorage.setItem(key, JSON.stringify(value));
      notify();
    },
    notify,
  };
}
