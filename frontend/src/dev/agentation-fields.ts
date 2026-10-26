/** 标注工具里的字段有独立表单身份，现有 id/name 由工具自己管理。 */
export function identifyAgentationFields(host: HTMLElement): () => void {
  let sequence = 0;
  const watched = new WeakSet<Node>();
  const identify = (root: HTMLElement | ShadowRoot) => {
    if (!watched.has(root)) {
      watched.add(root);
      observer.observe(root, { childList: true, subtree: true });
    }
    for (const field of root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(
      'input,textarea,select',
    )) {
      if (!field.id && !field.name) field.name = `peach-agentation-field-${++sequence}`;
    }
    for (const child of root.querySelectorAll('*')) {
      if (child.shadowRoot) identify(child.shadowRoot);
    }
  };
  const observer = new MutationObserver(() => identify(host));
  identify(host);
  return () => observer.disconnect();
}
