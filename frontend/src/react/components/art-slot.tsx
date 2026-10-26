/* 卡片封面格里那张图（`[data-media-art]`）。
 *
 * 图是遗留层拼的 HTML 片段，加载后壳在同一个 `<img>` 上改取景、把派生档换成原件（见
 * `../catalog-grid/artwork.ts`）。大图、小图拼出的片段只差取景类名，按片段整段重写就是把
 * 每张图都换成新元素：派生档重新解码、取景等 `load` 才重算，整屏一起闪一下。所以按
 * `identity`（同一条作品按小图拼出的片段）认图：身份不变时留着上一段片段，版式交给
 * `relayout` 在同一张图上换，换了图才整段重写。 */
import { useLayoutEffect, useRef, useState } from 'react';

export function ArtSlot({ artwork, identity = artwork.html, relayout }: {
  artwork: { kind: string; html: string };
  identity?: string;
  relayout?(root: HTMLElement): void;
}) {
  const [kept, keep] = useState({ identity, artwork });
  if (kept.identity !== identity) keep({ identity, artwork });
  const shown = kept.identity === identity ? kept.artwork : artwork;
  const slot = useRef<HTMLSpanElement>(null);
  const latest = useRef(relayout);
  latest.current = relayout;
  // 片段一变就交给壳比对：它按图上记着的版式判断，同一版式什么都不做。
  useLayoutEffect(() => {
    if (slot.current) latest.current?.(slot.current);
  }, [artwork.html]);
  return <span ref={slot} data-media-art={shown.kind} dangerouslySetInnerHTML={{ __html: shown.html }} />;
}
