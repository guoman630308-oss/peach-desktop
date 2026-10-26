/* 裁剪封面：作品详情页标题旁那枚键点开的一屏。
 *
 * 裁的是**取景框**，不是图片。横版封套里正封那一块本来就不是一张新图，而是一组
 * 写在 `<番号>.poster.json` 里的坐标（`src/peach/jav_poster_crop.py`），页面按它取景。
 * 所以这一屏确认之后封面原图一个字节都没动，「恢复默认」就是把那组坐标按折痕判据
 * 重算一遍——不是把图还原，图从来没变过。
 *
 * 默认框满高贴右缘、按正封宽高比取宽：封套是「背面 | 书脊 | 正面」，人要改的多半
 * 只是折痕落在哪一列。比例可以解锁，因为版式不规整的封套确实存在。
 */
import { useRef, useState } from 'react';
import { RiCloseLine } from '@remixicon/react';
import { useMutation } from '@tanstack/react-query';
import { UNSAFE_PortalProvider } from 'react-aria';
import { Heading } from 'react-aria-components';

import { Button } from '@/components/base/buttons/button';
import { IconButton } from '@/components/base/buttons/icon-button';
import { Switch } from '@/components/base/switch/switch';

import { errorMessage } from '../../api';
import {
  clampBox, defaultPanelBox, isUsableSize, type CropBox, type CropSize,
} from '../../crop-geometry';
import { PANEL_ASPECT } from '../../jav-artwork';
import type { CoverCropProps } from '../bundle';
import { ModalFrame, ModalGlyph } from '../components/modal-frame';
import { Note } from '../components/note';
import { overlayHost } from '../components/overlay-host';
import { useOverlayScrollbar } from '../components/overlay-scrollbar';
import { CropFrame } from '../crop/crop-frame';
import { busyProps } from '../settings/use-action';
import { sendCoverCrop } from './cover-crop';

/** 雪碧图里的 Lucide `crop`。这枚键和旧版工具条的定位、同步删除排在一行，那两枚是
 *  15px、线宽 2 的线条字形；Remix 那枚是填充轮廓，并排看笔画粗一圈。 */
function CropIcon({ className }: { className: string }) {
  return (
    <svg aria-hidden viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round" className={className}>
      <use href="#i-crop" />
    </svg>
  );
}

export function CoverCrop({ code, coverUrl, box, onSaved }: CoverCropProps) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button ref={anchor} type="button" aria-haspopup="dialog" data-cover-crop
        aria-label={`裁剪 ${code} 的封面`} title="裁剪封面：框出正封那一块"
        onClick={() => setOpen(true)}
        className="flex size-7 cursor-pointer items-center justify-center rounded-lg text-foreground-icon-secondary outline-none transition-colors hover:bg-background-primary-hover hover:text-foreground-icon-primary focus-visible:ring-2 focus-visible:ring-border-focus-ring">
        <CropIcon className="size-3.75" />
      </button>
      {/* 详情页是 `showModal()` 开的原生 dialog，弹层要挂进同一层才盖得住它。 */}
      <UNSAFE_PortalProvider getContainer={() => overlayHost(anchor.current)}>
        <ModalFrame isOpen={open} onOpenChange={setOpen} width="cover-crop" label="裁剪封面">
          <CropBody code={code} coverUrl={coverUrl} box={box} onSaved={onSaved}
            close={() => setOpen(false)} />
        </ModalFrame>
      </UNSAFE_PortalProvider>
    </>
  );
}

function CropBody({ code, coverUrl, box: saved, onSaved, close }: CoverCropProps & { close(): void }) {
  const [size, setSize] = useState<CropSize | null>(null);
  const [box, setBox] = useState<CropBox | null>(null);
  const [locked, setLocked] = useState(true);
  const frame = useOverlayScrollbar<HTMLDivElement>();

  const submit = useMutation({
    mutationFn: (next: CropBox | null) => sendCoverCrop(code, next),
    onSuccess: (result) => { close(); onSaved(result.poster_box) },
  });

  /* 进来时先摆在当前生效的那个框上：人要改的是「现在这样」，从头开始框等于先把
     现状复原一遍。没有生效的框（这张封面判定为不该裁）才退回默认的满高贴右缘。 */
  function ready(next: CropSize) {
    setSize(next);
    const current = saved && saved.px?.[0] === next.width && saved.px?.[1] === next.height
      ? { x0: saved.x0, y0: saved.y0, x1: saved.x1, y1: saved.y1 }
      : defaultPanelBox(next, PANEL_ASPECT);
    setBox(clampBox(current, next, null));
  }

  const busy = submit.isPending;
  return (
    <>
      <div className="flex shrink-0 items-start gap-4 p-5">
        <ModalGlyph><CropIcon className="size-6" /></ModalGlyph>
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <Heading slot="title" className="text-title-3-semibold text-text-primary">裁剪封面</Heading>
          <p className="text-body-2-regular text-text-secondary">
            {code}：框出正封那一块，封面原图不动。拖动方框移动，滚轮或角上那枚方块改大小。
          </p>
          {submit.error ? <Note tone="error">{errorMessage(submit.error)}</Note> : null}
        </div>
        <IconButton icon={RiCloseLine} size="small" aria-label="关闭" onClick={close} />
      </div>
      <div ref={frame} className="flex min-h-0 flex-1 flex-col items-center gap-4 overflow-y-auto border-t border-separator-border px-5 py-4">
        <CropFrame src={coverUrl} aspect={locked ? PANEL_ASPECT : null} box={box} size={size}
          label={`${code} 的正封取景框`}
          onSize={(next) => { if (isUsableSize(next)) ready(next) }}
          onBox={setBox} />
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-3 border-t border-separator-border p-5">
        <Switch isSelected={locked} onChange={(next) => {
          setLocked(next);
          if (next && box && size) setBox(clampBox(box, size, PANEL_ASPECT));
        }}>
          <span className="text-body-2-regular text-text-secondary">锁定正封比例</span>
        </Switch>
        <div className="flex flex-1 flex-wrap items-center justify-end gap-3">
          <Button variant="secondary" {...busyProps(busy)}
            onClick={() => { if (!busy) submit.mutate(null) }}>恢复默认</Button>
          <Button disabled={!box || !size} {...busyProps(busy)}
            onClick={() => { if (box && !busy) submit.mutate(box) }}>用这一块</Button>
        </div>
      </div>
    </>
  );
}
