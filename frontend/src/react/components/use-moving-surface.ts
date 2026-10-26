import { spring } from 'motion';
import { animate } from 'motion/mini';
import { useLayoutEffect, useState } from 'react';

/** Fluid 的短程、无回弹档；页面语义与配色仍由 BoardUI 提供。 */
export const SURFACE_SPRING = {
  selection: { type: spring, duration: 0.16, bounce: 0 },
  hover: { type: spring, duration: 0.08, bounce: 0 },
} as const;

/** 装饰节点不加入 React Aria 的 collection；生命周期与测量均归这个 hook。 */
export function useMovingSurface(mode: 'selection' | 'hover') {
  const [host, setHost] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (!host) return;
    const pane = document.createElement('span');
    pane.setAttribute('aria-hidden', 'true');
    pane.dataset.movingSurface = mode;
    pane.hidden = true;
    host.prepend(pane);
    host.dataset.surfaceHost = mode;
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    const mouse = matchMedia('(hover: hover) and (pointer: fine)');
    let pointerInput = false;
    let hovered: HTMLElement | null = null;
    let previous: HTMLElement | null = null;
    let destination = '';
    let animation: ReturnType<typeof animate> | undefined;
    const selector = mode === 'selection'
      ? '[role="tab"],label[data-rac]'
      : '[role="option"],button';
    const items = () => [...host.querySelectorAll<HTMLElement>(selector)]
      .filter((node) => node.getBoundingClientRect().width > 0);
    const hide = () => {
      animation?.cancel();
      pane.hidden = true;
      previous = null;
      destination = '';
      delete host.dataset.surfaceReady;
    };
    const place = (smooth = false) => {
      const rows = items();
      const selected = rows.find((node) => node.hasAttribute('data-selected')
        || node.getAttribute('aria-selected') === 'true');
      const target = mode === 'selection' ? selected : hovered;
      if (!target || !host.contains(target) || !rows.includes(target)
        || (mode === 'hover' && (!mouse.matches || !pointerInput || rows.length > 8
          || target.matches(':disabled,[aria-disabled="true"],[data-disabled]')))) {
        hide();
        return;
      }
      const box = target.getBoundingClientRect();
      const origin = host.getBoundingClientRect();
      const sx = origin.width / host.offsetWidth || 1;
      const sy = origin.height / host.offsetHeight || 1;
      const x = (box.left - origin.left) / sx + host.scrollLeft - host.clientLeft;
      const y = (box.top - origin.top) / sy + host.scrollTop - host.clientTop;
      const width = box.width / sx;
      const height = box.height / sy;
      const next = `${x},${y},${width},${height}`;
      // 同一个位置的重复通知不截断正在移动的底板；键盘和媒体设置变化仍即时落位。
      if (pointerInput && mouse.matches && !reduced.matches && previous === target && destination === next) return;
      const live = pane.hidden ? null : pane.getBoundingClientRect();
      animation?.cancel();
      pane.hidden = false;
      pane.style.width = `${width}px`;
      pane.style.height = `${height}px`;
      const transform = `translate(${x}px, ${y}px) scale(1, 1)`;
      if (smooth && pointerInput && !reduced.matches && live && previous !== target) {
        const fromX = (live.left - origin.left) / sx + host.scrollLeft - host.clientLeft;
        const fromY = (live.top - origin.top) / sy + host.scrollTop - host.clientTop;
        const from = `translate(${fromX}px, ${fromY}px) scale(${live.width / sx / width}, ${live.height / sy / height})`;
        animation = animate(pane, { transform: [from, transform] }, SURFACE_SPRING[mode]);
      } else {
        pane.style.transform = transform;
      }
      previous = target;
      destination = next;
      host.dataset.surfaceReady = '';
    };
    const onPointer = (event: PointerEvent) => {
      pointerInput = event.pointerType === 'mouse' && mouse.matches;
      if (mode === 'hover') {
        const target = event.target instanceof Element ? event.target.closest<HTMLElement>(selector) : null;
        if (target === hovered && pointerInput) return;
        hovered = target;
        place(true);
      }
    };
    const onKey = () => {
      pointerInput = false;
      if (mode === 'hover') hide();
      else place();
    };
    const onLeave = () => { hovered = null; if (mode === 'hover') hide(); };
    const measure = () => place();
    const mutation = new MutationObserver(() => place(true));
    const resize = new ResizeObserver(measure);
    const observeItems = () => { resize.disconnect(); resize.observe(host); items().forEach((item) => resize.observe(item)); };
    const children = new MutationObserver(() => { observeItems(); place(); });
    mutation.observe(host, { subtree: true, attributes: true,
      attributeFilter: ['data-selected', 'aria-selected', 'data-disabled', 'aria-disabled'] });
    children.observe(host, { childList: true, subtree: true });
    observeItems();
    place();
    host.addEventListener('pointerdown', onPointer, true);
    host.addEventListener('pointermove', onPointer);
    host.addEventListener('pointerleave', onLeave);
    host.addEventListener('keydown', onKey, true);
    host.addEventListener('scroll', measure, true);
    reduced.addEventListener('change', measure);
    mouse.addEventListener('change', measure);
    return () => {
      animation?.cancel();
      mutation.disconnect(); children.disconnect(); resize.disconnect();
      host.removeEventListener('pointerdown', onPointer, true);
      host.removeEventListener('pointermove', onPointer);
      host.removeEventListener('pointerleave', onLeave);
      host.removeEventListener('keydown', onKey, true);
      host.removeEventListener('scroll', measure, true);
      reduced.removeEventListener('change', measure);
      mouse.removeEventListener('change', measure);
      pane.remove();
      delete host.dataset.surfaceHost;
      delete host.dataset.surfaceReady;
    };
  }, [host, mode]);
  return setHost;
}
