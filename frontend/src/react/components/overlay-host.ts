/* 弹层该挂到哪个容器里。
 *
 * 默认那一个在 `body` 末尾（`entry.tsx` 的 `overlayContainer`），对整页 island 够用。
 * 作品详情页不是：遗留壳用 `showModal()` 开一个原生 `<dialog>` 装它，而顶层元素排在
 * 所有 z-index 之上——弹层落在 `body` 上就被详情页整个盖住，而盖住的样子和「弹层没
 * 画出来」在屏幕上看不出区别。所以从触发它的那个节点往上找最近的已打开 dialog，
 * 挂进去；找不到就还是 `body`。
 *
 * token、Preflight 与焦点规则都作用在 `.peach-react` 上，所以容器自己带这个类。
 * 每个 dialog 复用同一个容器，弹层关掉之后它留在那儿，下一次不必再建。
 */
function hostIn(parent: HTMLElement): HTMLElement {
  const existing = parent.querySelector<HTMLElement>(':scope > [data-react-overlays]');
  if (existing) return existing;
  const host = parent.ownerDocument.createElement('div');
  host.className = 'peach-react';
  host.dataset.reactOverlays = '';
  parent.append(host);
  return host;
}

/** `node` 所在的那一层该用的弹层容器。 */
export function overlayHost(node: Element | null | undefined): HTMLElement {
  const document = node?.ownerDocument ?? globalThis.document;
  const modal = node?.closest('dialog[open]');
  return hostIn((modal as HTMLElement | null) ?? document.body);
}
