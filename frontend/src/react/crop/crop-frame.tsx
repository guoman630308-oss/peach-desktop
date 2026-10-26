/* 在一张图上框一块。头像和封面共用这一份。
 *
 * 不引裁剪库：要的就是「一个能拖能缩的矩形」，算术全在 `crop-geometry.ts` 里，
 * 画面是一张图加五个绝对定位的块。引一个库进来换到的只有把手样式，代价是一条
 * 新依赖和一套它自己的坐标约定。
 *
 * 框的坐标一律是**源图像素**，进出这一层时各换算一次；屏幕上量到的显示像素不
 * 往外传。确认之后由后端按同一组整数裁，两边不会各取各的整。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  CSSProperties, PointerEvent as ReactPointerEvent, KeyboardEvent as ReactKeyboardEvent,
} from 'react';

import {
  boxPercent, clampBox, handleOutside, isUsableSize, moveBox, resizeFromCorner, scaleBox, toNatural,
  type CropBox, type CropSize,
} from '../../crop-geometry';

export interface CropFrameProps {
  src: string;
  /** 宽/高。给了就锁比例，拖动只改位置和大小、不改形状。 */
  aspect: number | null;
  box: CropBox | null;
  size: CropSize | null;
  onBox(box: CropBox): void;
  onSize(size: CropSize): void;
  /** 无障碍名称，说清在框哪张图的哪一块。 */
  label: string;
}

/** 键盘一步走框宽的这个比例。一屏之内按住方向键能从一边走到另一边，又不至于
 *  一下越过一张脸。 */
const KEY_STEP = 0.02;
/** 滚轮一格缩放的倍率。 */
const WHEEL_STEP = 1.08;

export function CropFrame({ src, aspect, box, size, onBox, onSize, label }: CropFrameProps) {
  const image = useRef<HTMLImageElement>(null);
  const drag = useRef<{ mode: 'move' | 'resize'; x: number; y: number; box: CropBox } | null>(null);
  const [failed, setFailed] = useState(false);
  /* 图当前的显示尺寸，只用来判断框在屏幕上够不够大、角柄放框里还是框外。 */
  const [shown, setShown] = useState<CropSize | null>(null);

  useEffect(() => { setFailed(false) }, [src]);
  useEffect(() => {
    const node = image.current;
    if (!node) return undefined;
    const observer = new ResizeObserver(() => setShown({ width: node.clientWidth, height: node.clientHeight }));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  /** 显示像素的位移 → 源图像素。图片按 `max-w-full` 缩放，两个轴的比例一样。 */
  const scale = useCallback((dx: number, dy: number) => {
    const node = image.current;
    if (!node || !size) return { dx: 0, dy: 0 };
    return {
      dx: toNatural(dx, node.clientWidth, size.width),
      dy: toNatural(dy, node.clientHeight, size.height),
    };
  }, [size]);

  function start(mode: 'move' | 'resize') {
    return (event: ReactPointerEvent) => {
      if (!box || !size || event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      event.currentTarget.setPointerCapture(event.pointerId);
      drag.current = { mode, x: event.clientX, y: event.clientY, box };
    };
  }

  function move(event: ReactPointerEvent) {
    const active = drag.current;
    const node = image.current;
    if (!active || !node || !size) return;
    const shift = scale(event.clientX - active.x, event.clientY - active.y);
    if (active.mode === 'move') {
      onBox(moveBox(active.box, shift.dx, shift.dy, size));
      return;
    }
    onBox(resizeFromCorner(active.box, active.box.x0 + (active.box.x1 - active.box.x0) + shift.dx,
      active.box.y0 + (active.box.y1 - active.box.y0) + shift.dy, size, aspect));
  }

  function end(event: ReactPointerEvent) {
    if (!drag.current) return;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function keys(event: ReactKeyboardEvent) {
    if (!box || !size) return;
    const step = Math.max(1, (box.x1 - box.x0) * KEY_STEP);
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step],
    };
    if (moves[event.key]) {
      event.preventDefault();
      onBox(moveBox(box, moves[event.key][0], moves[event.key][1], size));
      return;
    }
    if (event.key === '+' || event.key === '=' || event.key === '-') {
      event.preventDefault();
      onBox(scaleBox(box, event.key === '-' ? 1 / WHEEL_STEP : WHEEL_STEP, size, aspect));
    }
  }

  const rect = box && size ? boxPercent(clampBox(box, size, aspect), size) : null;
  const outside = rect ? handleOutside(rect, shown) : false;
  /* 位置只经自定义属性进 CSS：六个百分数挂在容器上，压暗块与框本身各自用类名取用，
     样式仍然全在类里（`docs/FRONTEND.md`）。 */
  const frame = rect ? {
    '--crop-l': `${rect.left}%`, '--crop-t': `${rect.top}%`,
    '--crop-w': `${rect.width}%`, '--crop-h': `${rect.height}%`,
    '--crop-r': `${rect.left + rect.width}%`, '--crop-b': `${rect.top + rect.height}%`,
  } as CSSProperties : undefined;
  return (
    <div className="relative inline-block max-w-full select-none" style={frame}>
      <img ref={image} src={src} alt="" draggable={false}
        onLoad={(event) => {
          const node = event.currentTarget;
          if (isUsableSize({ width: node.naturalWidth, height: node.naturalHeight })) {
            onSize({ width: node.naturalWidth, height: node.naturalHeight });
          }
        }}
        onError={() => setFailed(true)}
        className="block max-h-crop-frame max-w-full object-contain" />
      {failed || !rect ? null : (
        <>
          {/* 框外压暗。四块而不是一圈巨大的 box-shadow：后者在这一层的圆角与
              overflow 下会溢到弹层外面去。 */}
          <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-(--crop-t) bg-scrim" />
          <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 top-(--crop-b) bg-scrim" />
          <div aria-hidden className="pointer-events-none absolute left-0 top-(--crop-t) h-(--crop-h) w-(--crop-l) bg-scrim" />
          <div aria-hidden className="pointer-events-none absolute right-0 left-(--crop-r) top-(--crop-t) h-(--crop-h) bg-scrim" />
          {/* 框本身。整块可拖，右下角那枚小方块改大小；方向键与加减号做同样两件事。
              框在屏幕上很小（极扁、极长的图）时角柄整枚挪到框外右下，框里留给拖动。 */}
          <div role="group" tabIndex={0} aria-label={label} data-crop-box
            onPointerDown={start('move')} onPointerMove={move}
            onPointerUp={end} onPointerCancel={end} onKeyDown={keys}
            onWheel={(event) => {
              if (!box || !size) return;
              onBox(scaleBox(box, event.deltaY > 0 ? WHEEL_STEP : 1 / WHEEL_STEP, size, aspect));
            }}
            className="absolute left-(--crop-l) top-(--crop-t) h-(--crop-h) w-(--crop-w) cursor-move touch-none outline-none ring-2 ring-border-focus-ring ring-inset focus-visible:ring-4">
            <span data-crop-handle data-outside={outside || undefined} onPointerDown={start('resize')}
              onPointerMove={move} onPointerUp={end} onPointerCancel={end}
              className="absolute -right-1.5 -bottom-1.5 data-outside:-right-3.5 data-outside:-bottom-3.5 size-3 cursor-nwse-resize touch-none rounded-xs border border-separator-border bg-background-full" />
          </div>
        </>
      )}
      {failed
        ? <p className="p-4 text-body-2-regular text-text-secondary">这张图读不出来，换一张再框。</p>
        : null}
    </div>
  );
}
