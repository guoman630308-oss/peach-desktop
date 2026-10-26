/* React 子树用例共用的挂载、输入与假 fetch。 */
import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, vi } from 'vitest';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) act(() => root.unmount());
  document.body.innerHTML = '';
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/** 挂一棵根并把重画与卸载留给用例：用例要换一份 props 重画，或看卸载之后还发不发请求时用它。 */
export async function mountRoot(element: ReactElement): Promise<{
  host: HTMLElement; rerender(next: ReactElement): Promise<void>; unmount(): Promise<void>;
}> {
  const host = document.createElement('div');
  host.className = 'peach-react';
  document.body.append(host);
  const root = createRoot(host);
  roots.push(root);
  await act(async () => root.render(element));
  return {
    host,
    rerender: async (next) => { await act(async () => root.render(next)) },
    unmount: async () => {
      const at = roots.indexOf(root);
      if (at >= 0) roots.splice(at, 1);
      await act(async () => root.unmount());
    },
  };
}

export async function mount(element: ReactElement): Promise<HTMLElement> {
  return (await mountRoot(element)).host;
}

/** 请求链上有好几段 `await`（fetch、json、setState），等它们都落地再断言。 */
export const settle = () => act(async () => {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
});

/** 一问还在路上的服务端回话：用例手动放行，模拟真网络里「点下去」和「重读回来」之间那段空档。
 *  假 fetch 在同一个 act 里就回话，会把这段空档里才看得见的缺陷盖住。 */
export function pending<T>() {
  let release!: (value: T) => void;
  const answer = new Promise<T>((done) => { release = done });
  return { answer, release: (value: T) => act(async () => { release(value) }) };
}

/** React 用自己记下的值判断输入有没有变，只派发事件不经过原生 setter 时它会忽略。 */
export async function type(input: HTMLInputElement | null | undefined, value: string) {
  if (!input) throw new Error('输入框没有画出来');
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

export const click = (el: Element | null | undefined) => act(async () => {
  if (!el) throw new Error('要点的元素没有画出来');
  (el as HTMLElement).click();
});

export const submit = (form: Element | null | undefined) => act(async () => {
  if (!form) throw new Error('表单没有画出来');
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
});

/** 按可见文字找按钮。弹出层挂在 `body` 末尾，所以默认从整页找。 */
export const buttonNamed = (name: string, root: ParentNode = document) =>
  [...root.querySelectorAll('button')].find((button) => button.textContent?.trim() === name) ?? null;

/** 分区以标题作无障碍名称。 */
export const section = (root: ParentNode, title: string) =>
  root.querySelector(`:is(form,section)[aria-label="${title}"]`);

export const switches = (root: ParentNode) => [...root.querySelectorAll<HTMLInputElement>('input[role="switch"]')];

/** 打开 React Aria 下拉并点选一项。 */
export async function choose(trigger: Element | null | undefined, option: string) {
  await click(trigger);
  await click([...document.querySelectorAll('[role="option"]')].find((node) => node.textContent?.trim() === option));
}

type FetchCall = [string, RequestInit | undefined];
export const fetchMock = (status: number, body: unknown) => vi.fn<(...call: FetchCall) => Promise<unknown>>(
  async () => ({ ok: status < 400, status, json: async () => body }),
);

export const sentBody = (fetcher: ReturnType<typeof fetchMock>, index = 0) =>
  JSON.parse(String(fetcher.mock.calls[index]?.[1]?.body));
