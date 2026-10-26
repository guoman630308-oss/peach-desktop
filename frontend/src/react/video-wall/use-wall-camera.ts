import { useCallback, useEffect, useLayoutEffect, useRef, useState, type PointerEvent, type MouseEvent } from 'react';
import { animate } from 'motion';
import { clampWall, wallExtent, type WallCamera, type WallRect, type WallSize } from './wall-layout';

const INITIAL: WallCamera = { x: 16, y: 16, scale: 1 };

/** 镜头每帧只写世界层；离开裁剪缓冲区时才发布一次 React 状态。 */
export function useWallCamera(count: number, finite: boolean, initial: WallCamera = INITIAL, focused = false) {
  const viewport = useRef<HTMLDivElement>(null);
  const world = useRef<HTMLDivElement>(null);
  const size = useRef<WallSize>({ width: 0, height: 0 });
  const current = useRef<WallCamera>({ ...initial });
  const [view, setView] = useState({ camera: { ...initial }, size: size.current });
  const published = useRef<WallCamera>({ ...initial });
  const options = useRef({ count, finite, focused });
  options.current = { count, finite, focused };
  const frame = useRef<number | null>(null);
  const flight = useRef<{ stop():void } | null>(null);
  const didDrag = useRef(false);
  const pointer = useRef<{ id: number; x: number; y: number; camera: WallCamera;
    lastX: number; lastY: number; time: number; vx: number; vy: number; dragged: boolean } | null>(null);
  const stop = useCallback(() => {
    flight.current?.stop(); flight.current = null;
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
  }, []);
  const apply = useCallback((next: WallCamera, force = false, center = false) => {
    const { count: total, finite: bounded, focused: expanded } = options.current;
    const actual = clampWall(next, size.current, wallExtent(total), bounded && !expanded && !center);
    current.current = actual;
    const ratio = window.devicePixelRatio || 1;
    if (world.current) world.current.style.transform = `translate3d(${Math.round(actual.x * ratio) / ratio}px, ${Math.round(actual.y * ratio) / ratio}px, 0) scale(${actual.scale})`;
    if (viewport.current) {
      viewport.current.dataset.cameraX = String(actual.x);
      viewport.current.dataset.cameraY = String(actual.y);
    }
    const old = published.current;
    if (force || Math.abs(actual.x - old.x) > 80 || Math.abs(actual.y - old.y) > 80 || actual.scale !== old.scale) {
      published.current = actual;
      setView({ camera: actual, size: { ...size.current } });
    }
  }, []);
  useLayoutEffect(() => {
    const node = viewport.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return;
      size.current = { width: entry.contentRect.width, height: entry.contentRect.height };
      apply(current.current, true);
    });
    observer.observe(node);
    return () => { observer.disconnect(); stop() };
  }, [apply, stop]);
  useEffect(() => { apply(current.current, true) }, [count, finite, apply]);

  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || !event.isPrimary || pointer.current) return;
    stop(); didDrag.current = false;
    if (event.target instanceof Element && event.target.closest('button,input,[data-wall-queue]')) return;
    pointer.current = { id: event.pointerId, x: event.clientX, y: event.clientY, camera: current.current,
      lastX: event.clientX, lastY: event.clientY, time: event.timeStamp, vx: 0, vy: 0, dragged: false };
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const from = pointer.current;
    if (!from || from.id !== event.pointerId) return;
    const dx = event.clientX - from.x, dy = event.clientY - from.y;
    if (!from.dragged && Math.hypot(dx, dy) > 7) {
      from.dragged = true; didDrag.current = true;
      event.currentTarget.setPointerCapture(event.pointerId);
    }
    const elapsed = event.timeStamp - from.time;
    if (elapsed > 0) {
      from.vx = (event.clientX - from.lastX) / elapsed;
      from.vy = (event.clientY - from.lastY) / elapsed;
    }
    from.lastX = event.clientX; from.lastY = event.clientY; from.time = event.timeStamp;
    if (from.dragged) apply({ ...from.camera, x: from.camera.x + dx, y: from.camera.y + dy });
  };
  const onPointerUp = (event: PointerEvent<HTMLDivElement>) => {
    const from = pointer.current;
    if (!from || from.id !== event.pointerId) return;
    pointer.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (!from.dragged || reduced() || event.timeStamp - from.time > 80) return;
    let previous = performance.now(), vx = Math.max(-4, Math.min(4, from.vx)), vy = Math.max(-4, Math.min(4, from.vy));
    const coast = (now: number) => {
      const dt = Math.min(50, now - previous); previous = now;
      const old = current.current;
      apply({ ...old, x: old.x + vx * dt, y: old.y + vy * dt });
      vx = current.current.x === old.x ? 0 : vx * Math.exp(-dt / 280);
      vy = current.current.y === old.y ? 0 : vy * Math.exp(-dt / 280);
      if (Math.hypot(vx, vy) < .04) { frame.current = null; return }
      frame.current = requestAnimationFrame(coast);
    };
    frame.current = requestAnimationFrame(coast);
  };
  const onClickCapture = (event: MouseEvent<HTMLDivElement>) => {
    if (!didDrag.current || event.detail === 0) return;
    didDrag.current = false; event.preventDefault(); event.stopPropagation();
  };
  useEffect(() => {
    const node = viewport.current;
    if (!node) return;
    let pendingX = 0, pendingY = 0, previous = 0;
    const tick = (now: number) => {
      const step = 1 - Math.exp(-Math.min(50, previous ? now - previous : 16) / 80);
      previous = now;
      const old = current.current;
      apply({ ...old, x: old.x + pendingX * step, y: old.y + pendingY * step });
      pendingX = current.current.x === old.x ? 0 : pendingX * (1 - step);
      pendingY = current.current.y === old.y ? 0 : pendingY * (1 - step);
      if (Math.hypot(pendingX, pendingY) < .5) {
        apply({ ...current.current, x: current.current.x + pendingX, y: current.current.y + pendingY });
        pendingX = 0; pendingY = 0; frame.current = null; return;
      }
      frame.current = requestAnimationFrame(tick);
    };
    const wheel = (event: WheelEvent) => {
      if (event.ctrlKey || event.metaKey) return;
      if (event.target instanceof Element && event.target.closest('[data-wall-queue],[data-wall-player]')) return;
      event.preventDefault();
      if (node.closest('[data-video-wall][data-refreshing]')) return;
      if (pointer.current) return;
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? size.current.height : 1;
      const dx = (event.shiftKey && !event.deltaX ? event.deltaY : event.deltaX) * unit;
      const dy = (event.shiftKey && !event.deltaX ? 0 : event.deltaY) * unit;
      const smooth = !reduced() && (!dx || !dy) && Math.max(Math.abs(dx), Math.abs(dy)) >= 50;
      if (!smooth) {
        stop(); pendingX = 0; pendingY = 0;
        apply({ ...current.current, x: current.current.x - dx, y: current.current.y - dy }); return;
      }
      if (frame.current === null || (!pendingX && !pendingY)) { stop(); previous = 0 }
      pendingX -= dx; pendingY -= dy;
      if (frame.current === null) frame.current = requestAnimationFrame(tick);
    };
    const cancel = () => { stop(); pointer.current = null; pendingX = 0; pendingY = 0 };
    node.addEventListener('wheel', wheel, { passive: false });
    node.addEventListener('pointerdown', cancel);
    node.addEventListener('keydown', cancel);
    window.addEventListener('blur', cancel);
    return () => {
      cancel(); node.removeEventListener('wheel', wheel); node.removeEventListener('pointerdown', cancel);
      node.removeEventListener('keydown', cancel); window.removeEventListener('blur', cancel);
    };
  }, [apply, stop]);
  const reveal = (rect: WallRect, animated = false, fit = false) => {
    stop();
    const from = current.current;
    const scale = fit ? Math.min(from.scale, (size.current.width - 24) / rect.width, (size.current.height - 32) / rect.height) : from.scale;
    const target = { scale, x:size.current.width / 2 - (rect.x + rect.width / 2) * scale,
      y:size.current.height / 2 - (rect.y + rect.height / 2) * scale };
    if (!animated || reduced()) { apply(target, true, fit); return }
    // Folia useWallCameraPan 的 420ms 飞行；世界层每帧变换，磁贴位置由块内弹簧接管。
    flight.current = animate(0, 1, { duration:.42, ease:[.22,1,.36,1],
      onUpdate:progress => apply({ x:from.x + (target.x - from.x) * progress,
        y:from.y + (target.y - from.y) * progress, scale:from.scale + (target.scale - from.scale) * progress }, false, fit),
      onComplete:() => { flight.current = null; apply(target, true, fit) },
    });
  };
  const zoom = (factor: number) => {
    stop(); const old = current.current, scale = Math.min(1.6, Math.max(.65, old.scale * factor));
    apply({ scale, x: size.current.width / 2 - (size.current.width / 2 - old.x) * scale / old.scale,
      y: size.current.height / 2 - (size.current.height / 2 - old.y) * scale / old.scale }, true);
  };
  return { viewport, world, view, reveal, zoom, snapshot: () => ({ ...current.current }), reset: () => { stop(); apply({ ...INITIAL }, true) },
    pan: (x: number, y: number) => { stop(); apply({ ...current.current, x: current.current.x + x, y: current.current.y + y }, true) },
    handlers: { onPointerDown, onPointerMove, onPointerUp, onClickCapture,
      onPointerCancel: () => { pointer.current = null; stop() } } };
}
