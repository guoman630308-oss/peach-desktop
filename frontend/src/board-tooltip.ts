import { scrollMovesAnchor } from '@peach/legacy/ui';

const normalized = (value: string) => value.replace(/\s+/g, ' ').trim();

function visibleText(node: HTMLElement): string {
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
  let text = '', part: Node | null;
  while ((part = walker.nextNode())) {
    const parent = part.parentElement;
    if (!parent || parent.closest('svg,[hidden],[aria-hidden=true],.sr-only')) continue;
    const range = document.createRange(); range.selectNodeContents(part);
    if ([...range.getClientRects()].some(rect => rect.width > 0 && rect.height > 0)
      && getComputedStyle(parent).visibility === 'visible') text += part.textContent;
  }
  return normalized(text);
}

function anchorRect(node: HTMLElement): DOMRect | null {
  const box = node.getBoundingClientRect();
  if (box.width && box.height) {
    if (node.matches('a') && visibleText(node) && !clipped(node)) {
      const range = document.createRange(); range.selectNodeContents(node);
      const text = range.getBoundingClientRect();
      if (text.width && text.height) return text;
    }
    return box;
  }
  const boxes = [...node.querySelectorAll('*')].flatMap(child => [...child.getClientRects()])
    .filter(rect => rect.width > 0 && rect.height > 0);
  if (!boxes.length) return null;
  const left = Math.min(...boxes.map(rect => rect.left)), top = Math.min(...boxes.map(rect => rect.top));
  return new DOMRect(left, top, Math.max(...boxes.map(rect => rect.right)) - left,
    Math.max(...boxes.map(rect => rect.bottom)) - top);
}

function clipped(node: HTMLElement): boolean {
  if (node.matches('.middle-truncated') || node.querySelector('.middle-truncated')) return true;
  if ([node, ...node.querySelectorAll<HTMLElement>('*')].some(part => part.clientWidth > 0
    && (part.scrollWidth > part.clientWidth + 1 || part.scrollHeight > part.clientHeight + 1))) return true;
  const range = document.createRange(); range.selectNodeContents(node);
  const rect = range.getBoundingClientRect();
  for (let parent: HTMLElement | null = node; parent && parent !== document.body; parent = parent.parentElement) {
    const style = getComputedStyle(parent), box = parent.getBoundingClientRect();
    if (box.width && /hidden|clip|auto|scroll/.test(style.overflowX)
      && (rect.left < box.left - 1 || rect.right > box.right + 1)) return true;
    if (box.height && /hidden|clip|auto|scroll/.test(style.overflowY)
      && (rect.top < box.top - 1 || rect.bottom > box.bottom + 1)) return true;
  }
  return false;
}

/** 全站 title 共用提示；完整可见文字无需复述，图标、补充说明和截断原文保留。 */
export function initBoardTooltip() {
  const tip = document.createElement('div');
  tip.className = 'ui-board-tooltip'; tip.id = 'board-control-tooltip'; tip.role = 'tooltip';
  tip.popover = 'manual'; tip.hidden = true; document.body.append(tip);
  let target: HTMLElement | null = null, title = '', described: string | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const place = (next: HTMLElement) => {
    const rect = anchorRect(next), box = tip.getBoundingClientRect();
    if (!rect) return;
    tip.style.left = `${Math.max(8, Math.min(innerWidth - box.width - 8, rect.left + (rect.width - box.width) / 2))}px`;
    tip.style.top = `${rect.top >= box.height + 16 ? rect.top - box.height - 8 : Math.min(innerHeight - box.height - 8, rect.bottom + 8)}px`;
  };
  const hide = () => {
    clearTimeout(timer); if (tip.matches(':popover-open')) tip.hidePopover(); tip.hidden = true;
    if (target) {
      if (!target.hasAttribute('title')) target.title = title;
      if (described === null) target.removeAttribute('aria-describedby');
      else target.setAttribute('aria-describedby', described);
    }
    target = null;
  };
  const show = (node: EventTarget | null, delay: number) => {
    if (!(node instanceof Element)) return;
    const next = node.closest<HTMLElement>('[title]');
    if (target?.contains(node) && (!next || next.contains(target))) return;
    if (!next || next.closest('.vjs-control') || !next.title.trim()) return;
    hide(); target = next; title = next.title; described = next.getAttribute('aria-describedby');
    next.removeAttribute('title');
    if (visibleText(next) === normalized(title) && !clipped(next)) return;
    timer = setTimeout(() => {
      if (target !== next || !next.isConnected || !anchorRect(next)) return;
      (next.closest('dialog[open]') ?? document.body).append(tip);
      tip.textContent = title; tip.hidden = false; tip.showPopover(); place(next);
      next.setAttribute('aria-describedby', [described, tip.id].filter(Boolean).join(' '));
    }, delay);
  };
  document.addEventListener('pointerover', event => show(event.target, 400));
  document.addEventListener('pointerout', event => { if (target && !target.contains(event.relatedTarget as Node | null)) hide() });
  document.addEventListener('focusin', event => show(event.target, 0));
  document.addEventListener('focusout', hide);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') hide() });
  document.addEventListener('scroll', event => {
    if (!target || !scrollMovesAnchor(event, target)) return;
    const rect = anchorRect(target);
    if (target.matches(':focus-visible') && rect && rect.bottom > 0 && rect.top < innerHeight) {
      if (tip.matches(':popover-open')) place(target);
    } else hide();
  }, true);
  window.addEventListener('resize', hide);
}
