/* 卡片、头像与封面那几段 HTML：壳拼的 HTML 和各岛的 `dangerouslySetInnerHTML` 读同一份。
 *
 * 吐字符串而不是 React 元素：这些 `<img>` 落进页面以后还要被别人原地改。兜底链
 * （`./image-fallback.ts`）取不到图时换 `src`、摘掉或换成首字母，取景（`./framing.ts`）在
 * 加载后往图上写内联样式与类名。React 看不见这些改动：节点归它管，重画一次就冲掉取景，
 * 被兜底链摘掉的节点再交给它更新还会报错。 */
import { esc, firstGrapheme } from '@peach/legacy/core';

import { javImageKind } from '../jav-artwork';
import { imageFallbackAttrs } from './image-fallback';
import { representativeOf } from './representatives';

/** 人脸取景：`axis`／`pct` 是挪的那一半，`box` 是放大的那一半（`/api/entity` 的 `avatar_focus`）。 */
export interface FaceFocus {
  axis?: string;
  pct?: number;
  box?: { cx: number; cy: number; faceW: number; imgW: number; imgH: number } | null;
}

/** 头像引用：实体 id、有没有实体图、取景。两个版本号拼进地址的 `&v=`，见 `withVersion`。
 *  `avatar_stand_in` 是服务端说的「代表作画面不是本人」（`attach_avatar_availability`）。 */
export interface FaceRef {
  id?: number | null; has_image?: boolean; image_version?: string; logo_version?: string; avatar_focus?: unknown;
  avatar_stand_in?: boolean;
  rep?: number | null;
  has_avatar?: boolean;
  identity_categories?: string[];
  source_avatar_url?: string;
  source_avatar_fallbacks?: string[];
}

/** 代表作画面替账号顶着圆框时，悬停与读屏读到的那句。 */
export const STAND_IN_TITLE = '代表作画面，非本人';

/* 原地替换的图（实体图、封面、标识）地址都带服务端下发的内容版本（文件修改时间）：换头像、
   补高清封面、换标识都覆盖同一个文件，地址不跟着变的话，同一页里浏览器直接复用内存里那张
   旧图，要刷新才看得到新的。 */
export const withVersion = (url: string, version: string | null | undefined): string =>
  version ? `${url}&v=${encodeURIComponent(version)}` : url;

/** 封面地址。`thumb` 是派生档，`upgradeCover` 把它摘掉换原件，版本号留在后面。 */
export const coverUrl = (item: { code?: string | null; cover_key?: string; cover_version?: string }, thumb = false): string =>
  withVersion(`/cover?code=${encodeURIComponent(item.cover_key || item.code || '')}${thumb ? '&thumb=1' : ''}`, item.cover_version);

/** 厂牌标识地址，`variant` 跟着位置走：大位要字标、小位要方形图标。 */
export const logoUrl = (studio: string, variant: string, version?: string | null): string =>
  withVersion(`/logo?studio=${encodeURIComponent(studio)}&variant=${variant}`, version);

const focusOf = (value: unknown): FaceFocus | null =>
  value && typeof value === 'object' ? value as FaceFocus : null;

/** 「女优」只用于番号发行物。素人、创作者自制和网红内容里的出镜者是艺人，套上 JAV 的行业
 *  称谓既不准确也会和创作者身份混淆。判据由后端 `is_jav` 给。 */
export function performerLabel(item: { is_jav?: boolean } | null | undefined): string {
  return item && item.is_jav ? '女优' : '艺人';
}

/* 实体那张脸：规范实体图优先，取不到退到代表作头像，两样都取不到就一个 `<img>`
   都不出。四个位置（顶栏圆头像、卡片署名、共演者、资料页大位）共用这一份。

   无条件出图、等 404 再把图摘掉的代价是：一个作品详情页 9 个这样的 404（1 个厂牌
   实体图、4 个人物实体图、4 个头像），首页手机视口 2 个；`/entity-image` 与
   `/avatar` 的 404 都不带缓存头，每次重绘再打一整轮。`hasImage` 由 `/api/tops`、
   `/api/items`、`/api/item`、`/api/entity` 随资料下发，判据和取图同一个函数。

   `rep` 这一侧不带标志：调用方传进来的就该是「取得到的代表作」（顶栏在入代表作表时
   已经筛过，见 `./representatives.ts`）。`/avatar` 是按需生成的，还没裁过但印相还在也算
   取得到——那条点一下就有的路不能一起关掉。

   兜底链最后一环必须真的把 `<img>` 拿掉（`data-drop="self"`）：留着取不到图的
   `<img>`，`:has(img)` 仍然匹配，首字母垫底回不来，浏览器还会把 alt 画出来。

   `thumb` 要的是实体图缩到长边 640 的那一份。资料页仍取原件——那里就是要看清。

   `version` 是服务端随 `has_image` 下发的 `image_version`，`logoVersion` 是 `logo_version`。

   `standIn` 是服务端的 `avatar_stand_in`：账号没有本人身份依据，圆框里那张代表作画面只是
   替它占位。显示代表作头像时图上带 `title` 与 `aria-label` 说明这一点；实体图不带，
   取图失败退到代表作时再补上。 */
export function entityFaceImg({
  kind = 'performer', id = null, hasImage = false, version = '', rep = null, mark = null, logo = '',
  logoVersion = '', logoVariant = 'logo', alt = '', lazy = true, style = '', dropStyle = false, focus = null, thumb = false,
  standIn = false,
}: {
  kind?: string; id?: number | null | undefined; hasImage?: boolean | undefined; version?: string | null | undefined;
  rep?: number | null | undefined; mark?: number | null | undefined; logo?: string;
  logoVersion?: string | null | undefined; logoVariant?: string; alt?: string;
  lazy?: boolean; style?: string; dropStyle?: boolean; focus?: unknown; thumb?: boolean;
  standIn?: boolean | undefined;
} = {}): string {
  const useEntity = !!(id && hasImage);
  const entitySrc = useEntity ? withVersion(`/entity-image?kind=${kind}&id=${id}${thumb ? '&thumb=1' : ''}`, version) : '';
  // `rep` 由服务端的 has_avatar 决定有没有值，没有就不出这一环。
  const avatarSrc = rep ? `/avatar?id=${rep}` : '';
  /* 公司的门面是它自己的标识，不是作品截图——那是某部片的画面，说的是别人的事。
     厂牌走 `/logo`：`logo` 只在调用方问过 `has_logo` 时才有值。变体跟着位置走，
     大位要字标、小位要方形图标。事务所没有标识文件，走官网那条链接的站点圆标 `mark`。 */
  const useLogo = !!logo;
  const src = useLogo ? logoUrl(logo, logoVariant, logoVersion)
    : (entitySrc || avatarSrc || (mark ? `/link-mark?id=${mark}` : ''));
  if (!src) return '';
  const fallbacks = useLogo ? [entitySrc, avatarSrc].filter(Boolean)
    : (useEntity && avatarSrc ? [avatarSrc] : []);
  // 人脸取景是按实体图算出来的，回落图是另一张照片，脸不在同一位置：只贴给第一环。
  const framed = useEntity && !useLogo;
  const faceBox = framed ? faceBoxAttrs(focus) : '';
  /* 挪和放大是同一份 sidecar 的两半，这里替调用点把挪那一半补上：给了 `focus` 却
     没给 `style` 的，按同一个换算自己算。分开传时漏掉 `style` 不会报错也看不出来
     ——图照样出，只是几何居中，脸落在画面顶上的那些正好被裁掉脑袋。 */
  const framedStyle = style || facePos(focus);
  /* 贴了脸框就一定要能撤 style：放大是 avatarFrame 写进 img 内联 style 的，回落时
     不撤，那几个百分比会按上一张图的尺寸套在这一张上。调用点不必记得开这个开关——
     忘了开的代价是页面上一张明显错位的图，而它只在回落发生时才现形。 */
  /* `decoding="async"` 让解码离开主线程：一屏几十张图同时落地时，同步解码把滚动
     和点击一起压住，而这些图一张都不参与首屏的排版——框的尺寸由 CSS 定死。 */
  const note = standIn && src === avatarSrc ? ` title="${STAND_IN_TITLE}" aria-label="${STAND_IN_TITLE}"` : '';
  const fallbackNote = standIn && fallbacks.includes(avatarSrc)
    ? ` data-fallback-note-src="${esc(avatarSrc)}" data-fallback-note="${STAND_IN_TITLE}"` : '';
  return `<img src="${src}" width="128" height="128" alt="${alt}"${note}${fallbackNote}${lazy ? ' loading="lazy"' : ''} decoding="async"${framed ? framedStyle : ''} `
    + `${faceBox}${imageFallbackAttrs({
      dropStyle: (dropStyle || !!faceBox || !!framedStyle) && framed,
      fallbacks,
    })}>`;
}

/* 头像内层：先垫首字母，再叠真实图。

   `has_image` 缺席按「没图」处理，和 entityFaceImg 的默认值一致：每一个调用点的
   ref 都由服务端带着标志下发（卡片署名、索引页、口味榜、复核卡片、沉浸模式），
   宽容缺席只会让下一个忘了挂标志的端点悄悄退回「无条件出图、等 404 再摘」。

   取景反过来：不传就从 ref 上取。它和 `has_image` 出自同一份下发，分开传的代价是
   七个调用点要各记一次，而漏掉不报错也不掉图，只是几何居中——这种错只有对着页面
   一个个看才发现得了。公司那一格要的是「明确不取景」，传 `null` 覆盖掉。 */
export function avatarInner(name: string, ref: FaceRef | null | undefined, repId: number | null | undefined,
  kind = 'performer', markId: number | null = null, logoName = '', logoVariant = 'icon',
  focus: unknown = undefined, thumb = false): string {
  // 这一层大多是小圆框和窄格子，厂牌标识在那里要方形图标而不是横着的字标；索引页的
  // 厂牌大格是同一个模板里的例外，由调用方点名要 `large`。
  const hint = focus === undefined ? (ref && ref.avatar_focus) || null : focus;
  const sourceFace = !ref?.has_image && !logoName && ref?.source_avatar_url
    ? `<img src="${esc(ref.source_avatar_url)}" width="128" height="128" alt="" loading="lazy" decoding="async" ${
      imageFallbackAttrs({ fallbacks: ref.source_avatar_fallbacks || [] })}>` : '';
  return `<span class="ini">${esc(firstGrapheme(name))}</span>`
    + (sourceFace || entityFaceImg({
      kind, id: ref && ref.id, hasImage: !!(ref && ref.has_image), version: ref && ref.image_version,
      rep: ref?.has_avatar && ref.rep ? ref.rep : repId, mark: markId,
      logo: logoName, logoVersion: ref && ref.logo_version, logoVariant, focus: hint, thumb,
      standIn: !!(ref && ref.avatar_stand_in),
    }));
}

/** 卡片署名、Mix 叠放头像、沉浸作者那一格：`avatarInner` 读取引用或顶栏代表作表。`kind` 为空是
 *  「没有署名人」，不退代表作，按女优那一档出首字母。 */
export function entityAvatar(name: string, ref: FaceRef | null | undefined, kind: string): string {
  return avatarInner(name, ref, kind ? representativeOf(name) : null, kind || 'performer');
}

/* 人脸取景：资料页圆框按检出的人脸中心取景（/api/entity 的 avatar_focus）。
   没检出或没算过返回空串维持几何居中；换回落图时必须撤掉——那是另一张照片，
   脸不在同一位置，见资料卡大位那张图的 `data-drop-style`。

   换算只有这一份。资料页把它写进 img 的 style；索引页大图版式要把它交给圆框上的
   CSS 变量——那里的 img 由共用的 avatarInner 拼，版式能改的容器只有圆框。 */
export function faceOrigin(value: unknown): string {
  const f = focusOf(value);
  return f && f.axis === 'x' ? `${f.pct}% 50%`
    : f && f.axis === 'y' ? `50% ${f.pct}%`
      : '';
}

export function facePos(value: unknown): string {
  const origin = faceOrigin(value);
  return origin ? ` style="object-position:${origin}"` : '';
}

/* 人脸放大：把脸框的像素尺寸交给页面，倍数在图加载后按框的真实尺寸算。

   只挪解决不了「脸太小」——cover 的缩放由框和图的比例定死，脸在图里占多少，在框里
   就占多少。539 张里有 29 张是全身站姿照，脸落在画面上半截的一小块里，挪到正中依旧
   是一颗认不出是谁的头。放大倍数由 `./face-frame.ts` 夹在「够看清」「不上采样」
   「不切头」三条之间，服务端算不了：它不知道这个框有多大、这块屏幕几倍像素。

   属性而不是 style：倍数得等图和框都落地才算得出来，和封面的 `data-cx`／`coverAnchor`
   同一条路。缺 `box` 的 sidecar（补字段之前算的）不贴属性，那些图照旧只挪不放大。

   五个数挤在一个属性里，是为了让回落只需要摘一样东西：脸框只描述第一环那张实体图，
   换到 `/avatar` 那张就整个作废，见 `./image-fallback.ts` 的 advanceImageFallback。 */
export function faceBoxAttrs(value: unknown): string {
  const b = focusOf(value)?.box;
  if (!b) return '';
  return ` data-facebox="${[b.cx, b.cy, b.faceW, b.imgW, b.imgH].map(Number).join(' ')}"`;
}

/** 封面里那张图要的字段。 */
export interface CoverItem {
  id?: number;
  code?: string;
  cover_key?: string;
  is_jav?: boolean;
  has_cover?: boolean;
  cover_version?: string;
  has_thumb?: boolean;
  has_local_poster?: boolean;
  cover_frame?: { cx?: number | null; cy?: number | null; layout?: string } | null;
  poster_box?: { x0?: number; y0?: number; x1?: number; y1?: number; px?: number[]; method?: string } | null;
  [field: string]: unknown;
}

/* 官方封面那张 `<img>`。人脸位置原样交给页面，锚点由 `coverAnchor` 在加载后算：哪个轴被裁、
   要推多远，只有同时拿到图片和容器的比例才知道。人物在画面里的位置差别很大，写死的锚点会
   把一部分作品裁掉下巴或整个切出画外；取不到人脸就退回固定取景。 */
export function coverImage(item: CoverItem, layout: 'big' | 'small', eager = false): string {
  const src = coverUrl(item, true);
  const wide = /ブルーレイ|blu[\s-]?ray/i.test(String(item.title || item.display_title || item.name || ''));
  const f = item.cover_frame || {};
  const still = item.poster_box?.method !== 'manual' && (f.layout === 'still'
    || /^(IENEE|IENFE|SDHS)-/i.test(String(item.code || ''))
    || /(?:【|\[|\(|（)\s*(?:[48]K\s*)?VR\s*(?:】|\]|\)|）)/i.test(String(item.title || item.display_title || item.name || '')));
  const faceY = f.cy == null ? null : still ? Math.min(1, Math.max(0, f.cy)) : Math.min(0.6, Math.max(0.05, f.cy));
  const face = [f.cx != null ? ` data-cx="${f.cx}"` : '',
    faceY != null ? ` data-cy="${faceY}"` : ''].join('');
  /* 正封那一块的取景框，源图像素坐标加源图尺寸，由 `posterPanel` 在加载后换算成
     百分比。`map(Number)` 既是校验也是转义：进到属性里的一定是数字。 */
  const pb = item.poster_box;
  const box = pb ? ` data-posterbox="${[pb.x0, (pb.px || [])[0], (pb.px || [])[1], pb.y0, pb.x1, pb.y1].map(Number).join(' ')}"` : '';
  // 小图看整张（含剧照拼贴），大图只取右侧正封。
  return `<img class="poster cover ${layout === 'small' ? 'whole' : 'front'}" src="${src}"
    width="640" height="360" alt="" loading="${eager ? 'eager' : 'lazy'}" data-cover-code="${esc(item.code || '')}"${wide ? ' data-wide-sleeve="1"' : ''}${still ? ' data-cover-layout="still"' : ''}${face}${box} data-drop="self">`;
}

/** 封面格的两种图：官方封套（取景链接管）或预览图。空串是这一条没有可用的图。 */
export type ArtworkKind = 'cover' | 'thumb' | '';

export interface Artwork {
  kind: ArtworkKind;
  html: string;
}

const NO_ARTWORK: Artwork = { kind: '', html: '' };

/** 番号作品的封面：按「JAV 默认封面」取官方封套或预览图，两种来源都挂在元素上，
 *  设置一换 `syncJavImages` 原地换图。取景数据跟着元素走，从预览图切回封面时还找得到框。 */
export function javArtwork(item: CoverItem, layout: 'big' | 'small', eager: boolean, javImage: unknown): Artwork {
  const kind = javImageKind(item, javImage);
  if (!kind) return NO_ARTWORK;
  const cover = item.has_cover && item.code ? coverUrl(item, true) : '';
  const thumb = item.has_thumb || item.has_local_poster ? `/poster?id=${item.id}&c=4` : '';
  const coverMarkup = coverImage(item, layout, eager);
  const frame = (coverMarkup.match(/ data-(?:c[xy]|posterbox)="[^"]*"/g) || []).join('');
  const image = kind === 'cover'
    ? coverMarkup
    : `<img class="poster" src="${thumb}" width="640" height="360" alt="" loading="${eager ? 'eager' : 'lazy'}"${frame}>`;
  return {
    kind: kind === 'cover' ? 'cover' : 'thumb',
    html: image.replace('<img ', `<img data-jav-image="${item.id}" data-jav-cover="${esc(cover)}" data-jav-thumb="${esc(thumb)}" data-jav-image-layout="${layout}" `),
  };
}

/** 一张作品卡的封面。番号作品走上面那条；关注来源的条目用来源自己的缩略图；其余取本地
 *  预览格。`size` 是这一屏的大图／小图，只施加给番号作品。 */
export function cardArtwork(item: CoverItem & { follow_thumb_url?: string | null }, size: 'big' | 'small',
  eager: boolean, javImage: unknown): Artwork {
  if (item.is_jav) return javArtwork(item, size, eager, javImage);
  if (item.follow_thumb_url) {
    return {
      kind: 'thumb',
      html: `<img class="poster" src="${esc(item.follow_thumb_url)}" width="640" height="360" alt="" loading="${eager ? 'eager' : 'lazy'}" referrerpolicy="no-referrer">`,
    };
  }
  return mixFace(item, size, eager, javImage);
}

/** Mix 的一张画面，播放队列每一行的小图也是它：番号作品走封套链，其余取本地预览格，同一条
 *  在两处长得一样。`size` 只施加给番号作品。 */
export function mixFace(item: CoverItem, size: 'big' | 'small', eager: boolean, javImage: unknown): Artwork {
  if (item.is_jav) return javArtwork(item, size, eager, javImage);
  if (item.has_cover && item.cover_key) return { kind: 'cover', html: coverImage(item, size, eager) };
  if (!item.has_thumb && !item.has_local_poster) return NO_ARTWORK;
  return { kind: 'thumb', html: `<img class="poster" src="/poster?id=${item.id}&c=4" width="640" height="360" alt="" loading="${eager ? 'eager' : 'lazy'}">` };
}

/** 播放队列一行的小图：Mix 画面按小图取，没有可用的图就写「无预览」占住那一格。 */
export function queueThumbHtml(item: CoverItem, javImage: unknown): string {
  return mixFace(item, 'small', false, javImage).html || '<span class="nopic">无预览</span>';
}

/* 详情开场给播放器的海报位：video.js 的脚本还在下载、流源还没接上时，画面先给本地
   封面，不留一块黑。选哪张与卡片同一份判据，番号作品跟随「JAV 默认封面」设置——
   官方封套或预览图；其它媒体退到本地预览格。返回空串表示这条没有可用的本地图，
   播放器照旧从黑场开始。 */
export function detailPosterUrl(item: CoverItem, javImage: unknown): string {
  const thumb = item.has_thumb || item.has_local_poster ? `/poster?id=${item.id}&c=4` : '';
  if (!item.is_jav) return item.has_cover && item.cover_key ? coverUrl(item) : thumb;
  return javImageKind(item, javImage) === 'cover'
    ? coverUrl(item) : thumb;
}

/** 卡片署名要的字段。 */
export interface IdentityItem {
  is_jav?: boolean;
  creator?: string;
  performers?: string[];
  performer_entities?: (FaceRef | null)[];
  performer_total?: number;
  creator_entity?: FaceRef | null;
  entity_refs?: Partial<Record<'performer' | 'creator' | 'studio' | 'series', FaceRef[]>>;
  studio?: string;
  code?: string;
  tags?: string[];
}

/* 卡片署名落到哪一个身份上。作品卡、版次队列和「接着看」必须用同一份推导——各算各的
   迟早会在同名 creator/performer 那 35 组上分叉，同一条作品在两处指向两个实体。

   番号旧投影常把女优罗马字同时塞进 `asset.creator`。规范 performer 实体已经本地化时，
   不能再让旧扁平字段抢走卡片署名和链接；非番号创作者作品仍优先 creator。

   共演作品用头像提示多人，但文字只保留第一位，再给总人数。两个长名字加元数据
   会在普通卡片里折成三行；「第一位 + 等 N 人」仍能说明身份与规模。 */
export function cardIdentity(item: IdentityItem): {
  kind: '' | 'creator' | 'performer'; name: string; coStarred: boolean; performers: string[];
  refs: (FaceRef | null)[]; total: number;
} {
  const performers = item.performers || [];
  const refs = item.performer_entities ?? item.entity_refs?.performer ?? [];
  const performer = performers[0] || '';
  const primaryCreator = item.is_jav && performer ? '' : item.creator || '';
  const kind = primaryCreator ? 'creator' : performer ? 'performer' : '';
  return {
    kind, name: primaryCreator || performer || '未归属',
    coStarred: performers.length > 1 && !primaryCreator, performers, refs,
    total: item.performer_total || performers.length,
  };
}

/* 播放队列一行的头像。整行本身就是一个 <button>，里面再嵌 <button> 会被浏览器就地拆散，
   头像和标题会被甩到行外面去，所以这里一律出 `<span>`、不可点。

   头像和名字必须落到同一个身份。各自挑 kind（头像先看 performer、名字先看
   creator）时，同名的 creator/performer 重复实体（数据库里有 35 组）会一个跳
   `/performers/x`、另一个跳 `/creators/x`，同一张卡上两个入口去两个地方。 */
export function queueAvatarHtml(item: IdentityItem): string {
  const { kind, name, coStarred, performers, refs } = cardIdentity(item);
  if (coStarred) {
    return `<div class="mavstack">${performers.slice(0, 5)
      .map((nm, i) => `<span class="mav">${avatarInner(nm, refs[i], representativeOf(nm))}</span>`)
      .join('')}</div>`;
  }
  const ref = kind === 'performer' ? refs[0] : item.creator_entity ?? item.entity_refs?.creator?.[0];
  return `<span class="mav">${avatarInner(name, ref, kind ? representativeOf(name) : null, kind || 'performer')}</span>`;
}

/** Mix 的署名：番号作品先认女优，其余先认创作者。 */
export function mixLabel(item: IdentityItem, tagLabel: (tag: string) => string): string {
  const performer = (item.performers || [])[0];
  return (item.is_jav && performer ? performer : item.creator) || performer || item.studio || item.code
    || tagLabel((item.tags || [])[0] || '') || '为你推荐';
}
