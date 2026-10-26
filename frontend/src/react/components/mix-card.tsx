/* 一叠视频的卡片：播放列表页的每一份列表，和首页那张 Mix 卡是同一件东西。
 *
 * 封面后面压着两层纸边，说明它是一叠；悬停时逐张翻过这一叠里的画面（`use-stack-flip.ts`）。
 * 封面右下角是条数徽标，下面一行是这一叠里出镜最多的那几位的头像，标题与一行来源，
 * 最右边留一格给次要动作（点点点菜单）。整块封面是「打开」的点击区，头像各自通往资料页。
 *
 * 卡是媒体卡，不是 Board 的填充卡：透明底、无内边距，面只在封面那一块。纸边、翻页、
 * 徽标底色与叠放头像的让位几何写在 `../styles.css` 的 `[data-mix-*]` 那一组规则里：
 * 伪元素、`color-mix` 与相邻兄弟选择器在工具类里写不出来。 */
import { useCallback, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { avatarInner, representativeOf } from '@peach/card-art';
import { firstGrapheme } from '@peach/legacy/core';

import { ArtSlot } from './art-slot';
import { spriteGlyph } from './sprite-glyph';
import { useStackFlip } from './use-stack-flip';

export { MIX_FLIP_FACES, MIX_FLIP_LEAD_MS, MIX_FLIP_MS } from './use-stack-flip';

const PLAY = spriteGlyph('play');

/** 署名行的一位。`kind` 是 `performer`／`creator`。 */
export interface MixCardFace {
  kind: string;
  id: number;
  name: string;
  has_image?: boolean;
  image_version?: string;
  avatar_focus?: unknown;
}

export interface MixCardProps {
  /** 标题，同时是点击区与菜单的无障碍名称里那个名字。 */
  name: string;
  /** 标题下那一行：这一叠从哪儿来。 */
  caption: ReactNode;
  /** 徽标上的条数。 */
  count: number;
  /** 静止封面的地址。没有就写「无预览」。 */
  poster: string | null;
  /** 悬停时翻过的那几张图的地址，按顺序；最多取前 `MIX_FLIP_FACES` 张。 */
  flipImages(): Promise<readonly string[]>;
  /** 此刻能不能翻（壳的多选、遮挡、减少动效、滚动中）。 */
  canFlip(): boolean;
  /** 署名行的人，最多画五位；一位都没有时画标题首字。 */
  faces: readonly MixCardFace[];
  /** 点头像：去这个人的资料页。 */
  onOpenEntity(kind: string, name: string): void;
  /** 点封面。不给就是这一叠没有去处（空列表），点击区不可点。参数是这张卡本身，壳拿它当
   *  打开动画的起点。 */
  onOpen?: (card: HTMLElement) => void;
  /** 封面点击区的无障碍名称，例如「打开播放列表 周末慢看」。 */
  openLabel: string;
  /** 署名行最右那一格：次要动作的菜单。 */
  menu?: ReactNode;
  /** 封面格的宽高比。不给就是 16:9；首页那张跟着这一屏卡片的版式走。 */
  ratio?: number;
  /** 静止封面改用遗留层拼的那段图片 HTML（番号作品的取景链要接管它），封面格同时带上作品卡
   *  的封面格钩子（`data-media-pic`），模糊衬底与取景和作品卡同一条路。 */
  artwork?: { kind: string; html: string };
  /** 静止封面去掉版式后的身份与原地换版式，见 `./art-slot.tsx`。 */
  artworkIdentity?: string;
  relayoutArt?(root: HTMLElement): void;
  /** 翻页的每一张也用 HTML 画，和静止封面长得一样；参数是 `flipImages` 给的那个地址。 */
  faceHtml?(src: string): string;
  /** 徽标上的字。不给就是「N 个视频」。 */
  badge?: string;
  /** 署名行换成一枚播放字形，不画头像：首页那张 Mix 说的是「以它为种子的相似作品」。 */
  glyph?: boolean;
  /** 整张卡都是点击区（首页那张）。不给就只有封面可点，署名行的头像各自通往资料页。 */
  wholeCard?: boolean;
}

/** 静止封面。取不到时把图撤掉，只剩黑底，同遗留层 `data-drop="self"`。 */
function Poster({ src }: { src: string }) {
  const [broken, setBroken] = useState(false);
  if (broken) return null;
  return (
    <img data-mix-poster="" src={src} width={640} height={360} alt="" loading="lazy" onError={() => setBroken(true)}
      className="absolute inset-0 block size-full object-contain" />
  );
}

/** 圆框共用的那一副：38px 正圆，图铺满，首字母居中。 */
const RING = 'relative inline-grid size-9.5 flex-none place-items-center overflow-hidden rounded-full'
  + ' bg-background-secondary-default text-body-2-regular text-text-secondary'
  + ' [&_img]:absolute [&_img]:inset-0 [&_img]:block [&_img]:size-full [&_img]:object-cover';

/** 圆框里那段 HTML 由 `avatarInner` 拼：有图走图，没图退首字母，代表作头像查同一张代表作表。 */
function Avatars({ faces, fallback, onOpenEntity }: {
  faces: readonly MixCardFace[]; fallback: string; onOpenEntity: MixCardProps['onOpenEntity'];
}) {
  if (!faces.length) {
    return <span data-mix-initial="" aria-hidden className={`${RING} mt-0.5`}>{fallback}</span>;
  }
  return (
    <div data-mix-avatars="">
      {faces.slice(0, 5).map((face) => (
        <button key={`${face.kind}:${face.id}`} type="button" title={`打开资料页：${face.name}`}
          aria-label={`打开资料页：${face.name}`} onClick={() => onOpenEntity(face.kind, face.name)}
          className={`${RING} cursor-pointer outline-none`}
          dangerouslySetInnerHTML={{ __html: avatarInner(face.name, face, representativeOf(face.name), face.kind) }} />
      ))}
    </div>
  );
}

export function MixCard({
  name, caption, count, poster, flipImages, canFlip, faces, onOpenEntity, onOpen, openLabel, menu,
  ratio, artwork, artworkIdentity, relayoutArt, faceHtml, badge, glyph, wholeCard,
  ...data
}: MixCardProps & Record<`data-${string}`, string>) {
  const flip = useStackFlip({ load: flipImages, canFlip });
  /* 壳在滚动、换页和开多选时按 `_stopHover` 收掉一切悬停动效，这张卡的翻页也在其中。 */
  const stop = useRef(flip.onPointerLeave);
  stop.current = flip.onPointerLeave;
  const card = useRef<HTMLElement | null>(null);
  const attach = useCallback((el: (HTMLElement & { _stopHover?: () => void }) | null) => {
    card.current = el;
    if (el) el._stopHover = () => stop.current();
  }, []);
  const open = onOpen ? () => { if (card.current) onOpen(card.current) } : undefined;
  const cover = artwork !== undefined || ratio !== undefined;
  return (
    <article {...data} ref={attach} data-mix-card="" onMouseEnter={flip.onPointerEnter}
      onMouseLeave={flip.onPointerLeave} onClick={wholeCard ? open : undefined}
      className="relative flex min-w-0 cursor-pointer flex-col gap-2">
      <div data-mix-stack="" className="relative isolate rounded-surface">
        <div data-mix-cover="" data-media-pic={cover ? '' : undefined}
          style={ratio !== undefined ? { '--card-ratio': String(ratio) } as CSSProperties : undefined}
          className="relative z-1 flex aspect-video items-center justify-center overflow-hidden">
          {artwork?.html
            ? <ArtSlot artwork={artwork} identity={artworkIdentity} relayout={relayoutArt} />
            : poster
              ? <Poster key={poster} src={poster} />
              : <span className="text-caption-1-regular tracking-caps text-text-secondary uppercase">无预览</span>}
          <div data-mix-faces="" hidden={!flip.faces.length}>
            {flip.faces.map((src, index) => (
              <div key={src} data-mix-face={index === flip.current ? 'on' : index === flip.leaving ? 'off' : ''}>
                {faceHtml
                  ? <span data-media-art="" dangerouslySetInnerHTML={{ __html: faceHtml(src) }} />
                  : <img src={src} alt="" loading="eager" />}
              </div>
            ))}
          </div>
          <button type="button" data-mix-open="" aria-label={openLabel} disabled={!onOpen} onClick={wholeCard ? undefined : open}
            className="absolute inset-0 z-1 cursor-pointer rounded-surface outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-border-focus-ring disabled:cursor-default" />
          <span data-mix-badge=""
            className="absolute right-2.25 bottom-2.25 z-6 flex min-h-7 items-center gap-1.5 rounded-2lg px-2.25 py-1 text-caption-1-semibold text-text-white">
            <PLAY aria-hidden className="size-3.75" />
            {badge ?? `${count.toLocaleString()} 个视频`}
          </span>
        </div>
      </div>
      <div className="flex min-w-0 items-start gap-2.25">
        {glyph
          ? <span data-mix-glyph="" aria-hidden><PLAY /></span>
          : <Avatars faces={faces} fallback={firstGrapheme(name)} onOpenEntity={onOpenEntity} />}
        <div className="flex min-w-0 flex-1 flex-col">
          <b data-mix-title="" className="truncate text-body-bold text-text-primary">{name}</b>
          <span data-mix-caption="" className="mt-0.5 truncate text-caption-1-regular text-text-secondary">{caption}</span>
        </div>
        {menu}
      </div>
    </article>
  );
}
