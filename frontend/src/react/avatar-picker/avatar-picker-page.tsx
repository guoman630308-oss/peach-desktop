/* 换头像：资料页圆框角上那个加号。
 *
 * 自动挑的那张按来源优先级来，而那个顺序回答的是「先试哪一张」，不是「哪一张适合当
 * 头像」——图库里排第一的常常是压着书名的写真封面，同一个人往下翻几张就有片商的正脸
 * 原图。所以这里不做更聪明的自动挑选，只把候选摊开让人看一眼就能换。
 *
 * 这一屏要回答的是「换成哪一张」，所以候选网格占掉中间全部高度，头部和底下那排操作
 * 固定不动，只有网格滚。候选到点开弹层才取：资料页每进一次就预取一遍，多数时候没人点。
 * 弹层外壳是 `../components/modal-frame.tsx`。 */
import { useEffect, useRef, useState } from 'react';
import type { ChangeEvent, CSSProperties } from 'react';
import { RiAddLine, RiCloseLine, RiUserLine } from '@remixicon/react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Heading } from 'react-aria-components';

import { Chip } from '@/components/base/badges/chip';
import { Button } from '@/components/base/buttons/button';
import { IconButton } from '@/components/base/buttons/icon-button';
import { Input } from '@/components/base/input/input';

import { errorMessage } from '../../api';
import {
  centeredBox, isUsableSize, previewStyle, windowStyle, type CropBox, type CropSize,
} from '../../crop-geometry';
import type { AvatarPickerProps } from '../bundle';
import { ModalFrame, ModalGlyph } from '../components/modal-frame';
import { Note } from '../components/note';
import { useOverlayScrollbar } from '../components/overlay-scrollbar';
import { CropFrame } from '../crop/crop-frame';
import { queryClient } from '../query';
import { busyProps } from '../settings/use-action';
import {
  avatarChoicesKey, baseLabel, baseVersion, choiceDetail, choiceFrame, choiceImageUrl, cropNote,
  fetchAvatarChoices, fetchCodeCover, frameAddress, frameChoice, frameFile, framesItself, indexNote,
  pickerNote, sendAvatarPick, sharedCast,
  type AvatarChoice, type AvatarSubmission,
} from './avatar-picker';

/** 候选还没回来时摆几格占位：一排四格摆两排，和最常见的一屏候选差不多高。 */
const SKELETON_TILES = 8;
/** 格子的比例，与 `--aspect-avatar-choice` 同一个数。 */
const TILE_ASPECT = 3 / 4;

/** 框选那一步框的是哪张图：服务端那一格，或者本机选的文件（只在浏览器里预览）。 */
interface Cropping { choice: AvatarChoice; file?: File }

/** 进框选的四条路。都要先问服务端一次：图有多大、脸在哪。 */
type Opening =
  | { via: 'choice'; choice: AvatarChoice }
  | { via: 'code'; code: string }
  | { via: 'url'; url: string }
  | { via: 'file'; file: File };

/** 框选交回来的那一块；null 是整张装。 */
type CropPick = { ref: string; crop: CropBox; version: string } | null;

async function openCrop(kind: string, id: number, wanted: Opening): Promise<Cropping> {
  switch (wanted.via) {
    case 'choice': return { choice: await frameChoice(kind, id, wanted.choice) };
    case 'code': return { choice: await fetchCodeCover(wanted.code) };
    case 'url': return { choice: await frameAddress(kind, id, wanted.url) };
    case 'file': return { choice: await frameFile(kind, id, wanted.file), file: wanted.file };
  }
}

export function AvatarPicker({ kind, entityId, name, onPicked }: AvatarPickerProps) {
  const [open, setOpen] = useState(false);
  return (
    <>
      {/* 加号压在圆框右下角那一圈上——它属于这张头像，离得远了就成了页面上一个不知道
          管什么的按钮。定位归外面这一层；头像那张图自己带 `z-index:1`，这一层要抬到它上面，
          不然按钮有一半藏在图底下。按钮收成 28px 的圆——它坐在一个圆的边上，方钮压在弧线
          上会遮掉一大块脸；一圈页面底色的描边把它和图分开。BoardUI 的 IconButton 只有方形，
          形状归组件自己管，所以这里是一枚原生按钮，底色、描边、悬停与焦点环取它同一套 token。
          悬停时底色透出身后的脸：暗色那档的悬停色本来就带 60% 透明，浅色那档是实色，
          这里给它补同样的 60%，两档读起来是同一个动作。 */}
      <span className="absolute right-1 bottom-1 z-10">
        <button type="button" aria-haspopup="dialog" aria-label={`更换${name}的头像`}
          onClick={() => setOpen(true)}
          className="flex size-7 cursor-pointer items-center justify-center rounded-full border border-border-button-default bg-background-primary-default text-foreground-icon-primary shadow-xs outline-none ring-2 ring-background-full transition-colors hover:border-border-button-hover hover:bg-background-primary-hover/60 dark:hover:bg-background-primary-hover focus-visible:ring-border-focus-ring active:bg-background-primary-active">
          <RiAddLine aria-hidden className="size-4" />
        </button>
      </span>
      <ModalFrame isOpen={open} onOpenChange={setOpen} width="avatar-picker" label="更换头像">
        <PickerBody kind={kind} entityId={entityId} name={name} onPicked={onPicked}
          close={() => setOpen(false)} />
      </ModalFrame>
    </>
  );
}

/** 弹层内容。只在弹层开着时挂载，候选也就只在这时候取。 */
function PickerBody({ kind, entityId, name, onPicked, close }: AvatarPickerProps & { close(): void }) {
  const file = useRef<HTMLInputElement>(null);
  const grid = useOverlayScrollbar<HTMLDivElement>();
  const [url, setUrl] = useState('');
  const [code, setCode] = useState('');
  /* 每一张图点开的都是框选，不是当场换图：图库人像多是全身竖图，整张装进圆框脸只占
     一小块。整屏换掉而不是再叠一层弹层：这一步要的是尽可能大的底图，而弹层套弹层只会
     让底图更小。 */
  const [cropping, setCropping] = useState<Cropping | null>(null);
  const key = avatarChoicesKey(kind, entityId);
  const picked = useRef(false);

  const listing = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchAvatarChoices(kind, entityId, signal),
    // 上一次点开没取到就重来。取到过的那一份留着，反复开合不必每次再问一遍。
    retryOnMount: true,
  });
  /* 换成功之后这一份候选里「在用」是哪一格已经过期，扔掉它，下次点开重新取。要等到弹层
     真的收起来再扔：这时候还有人在读这个键，当场清掉它会立刻再取一遍，白问一趟。 */
  useEffect(() => () => {
    if (picked.current) queryClient.removeQueries({ queryKey: avatarChoicesKey(kind, entityId) });
  }, [kind, entityId]);
  /* 三种来源共用一个出口，成功后就地关掉并让宿主重画。失败留在原地并说出原因——
     这一步会改掉盘上的图，静默失败等于让人以为换好了。 */
  const submit = useMutation({
    mutationFn: (submission: AvatarSubmission) => sendAvatarPick(kind, entityId, submission),
    onSuccess: () => {
      picked.current = true;
      close();
      onPicked();
    },
  });
  /* 进框选之前等服务端量完再切屏：先摆个居中框、脸回来再挪，人可能已经在拖了。
     作品画面那一组不必问，列表里就带着尺寸和取景区。 */
  const opening = useMutation({
    mutationFn: (wanted: Opening) => openCrop(kind, entityId, wanted),
    onSuccess: setCropping,
  });
  const opens = (via: Opening['via']) => opening.isPending && opening.variables?.via === via;

  function pickFile(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const chosen = input.files?.[0];
    input.value = '';
    if (chosen && !busy) opening.mutate({ via: 'file', file: chosen });
  }

  /* 框选交回来的那一块按它的来路交：本机文件再发一次原字节，其余按 ref。整张装的那条
     不带框，也不带版本——整张图不存在「框画在哪一版上」的问题。 */
  function pickCrop(picked: CropPick) {
    if (submit.isPending || !cropping) return;
    const { choice, file } = cropping;
    if (file) submit.mutate(picked ? { file, crop: picked.crop } : { file });
    else submit.mutate(picked ?? { ref: choice.ref });
  }

  const data = listing.data;
  const choices = data?.choices ?? [];
  const problem = (submit.error ? errorMessage(submit.error) : '')
    || (!cropping && opening.error ? errorMessage(opening.error) : '')
    || (listing.error ? errorMessage(listing.error) : '');
  const busy = submit.isPending || opening.isPending;
  const index = cropping ? '' : indexNote(data);
  // 只有候选那一份没取到时提示旁边给「重试」：提交和取封面失败，重来的是那颗键自己。
  const listingFailed = !!problem && !!listing.error && problem === errorMessage(listing.error);
  // 候选取完是空的（或没取到）时不留网格那一带：两条分隔线之间空着一块，读起来像还在等。
  const showGrid = listing.isPending || choices.length > 0;
  return (
    <>
      {/* 头部：左边一个方图标槽，右边标题加一句说明，右上角是关闭键。 */}
      <div className="flex shrink-0 items-start gap-4 p-5">
        <ModalGlyph><RiUserLine className="size-6" /></ModalGlyph>
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <Heading slot="title" className="text-title-3-semibold text-text-primary">
              {cropping ? '框出头像那一块' : '更换头像'}
            </Heading>
            {!cropping && choices.length ? <Chip color="soft">{choices.length.toLocaleString()} 张可选</Chip> : null}
          </div>
          <p className="text-body-2-regular text-text-secondary">
            {cropping ? cropNote(cropping.choice) : pickerNote(name, data)}
          </p>
          {index ? <Note tone="neutral">{index}</Note> : null}
          {problem ? (
            <Note tone="error" action={listingFailed ? (
              <Button variant="secondary" size="small" {...busyProps(listing.isFetching)}
                onClick={() => { if (!listing.isFetching) void listing.refetch() }}>重试</Button>
            ) : undefined}>{problem}</Note>
          ) : null}
        </div>
        <IconButton icon={RiCloseLine} size="small" aria-label="关闭" onClick={close} />
      </div>
      {cropping ? (
        <CropStep kind={kind} entityId={entityId} choice={cropping.choice} file={cropping.file}
          busy={submit.isPending} back={() => setCropping(null)} confirm={pickCrop} />
      ) : (
      <>
      {/* 候选网格是这一屏唯一会滚的层：头部和底下那排操作再长也不动。上下各留 16px：
          只留上边的话，最后一排图贴着底下那条线。外面这一层只为放那条覆盖式滚动条的
          轨道，它按 `absolute` 铺，得有一个只裹着滚动块本身的定位祖先。 */}
      {showGrid ? (
      <div className="relative flex min-h-0 flex-1 flex-col">
        <div ref={grid} className="flex min-h-0 flex-1 flex-col overflow-y-auto border-t border-separator-border px-5 py-4">
          {/* 候选回来之前摆同一种格子的占位，格子外框、比例与标签那一行都和真格子一样，
              换上真格子时网格不跳。 */}
          <div role="listbox" aria-label="备选头像" aria-busy={listing.isPending || undefined}
            data-skeleton={listing.isPending ? 'avatar-choices' : undefined}
            className="inline-grid grid-cols-3 content-start gap-2 sm:grid-cols-4">
            {listing.isPending ? Array.from({ length: SKELETON_TILES }, (_, index) => (
              <span key={index} aria-hidden data-avatar-skeleton
                className="flex flex-col gap-1 overflow-hidden rounded-2lg border border-separator-border bg-background-secondary-default pb-1 text-caption-1-regular">
                <span className="relative block w-full aspect-avatar-choice skeleton-sheen" />
                <span className="relative mx-auto w-2/3 rounded-sm skeleton-sheen">&nbsp;</span>
              </span>
            )) : choices.map((choice) => (
              <button type="button" key={choice.ref} role="option" data-avatar-choice
                aria-selected={choice.current} title={choiceDetail(choice)} {...busyProps(busy)}
                onClick={() => {
                  if (busy) return;
                  if (choice.crop) setCropping({ choice });
                  else opening.mutate({ via: 'choice', choice });
                }}
                className="relative flex cursor-pointer flex-col gap-1 overflow-hidden rounded-2lg border border-separator-border bg-background-secondary-default pb-1 text-center text-caption-1-regular text-text-secondary outline-none hover:border-border-button-hover hover:text-text-primary focus-visible:ring-2 focus-visible:ring-border-focus-ring aria-selected:border-border-focus-ring aria-selected:bg-background-tertiary-default aria-selected:text-text-primary aria-disabled:cursor-progress aria-disabled:opacity-60">
                <ChoiceImage src={choiceImageUrl(kind, entityId, choice.ref, baseVersion(choice, choice.ref))}
                  choice={choice} />
                <span className="truncate px-1">{choice.label}</span>
                {choice.current
                  ? <span className="absolute top-1 left-1"><Chip variant="caption" color="gray">在用</Chip></span>
                  : null}
                {/* 合演封面上最大那张脸多半是领衔的另一位，格子不围着它取景，这里标出人数。 */}
                {sharedCast(choice)
                  ? <span className="absolute top-1 right-1"><Chip variant="caption" color="gray">{choice.cast.toLocaleString()} 人</Chip></span>
                  : null}
              </button>
            ))}
          </div>
        </div>
      </div>
      ) : null}
      {/* 番号、手填地址和本机文件跟候选是并列的几条路，不是候选看完之后的补充，所以摆在
          固定的那一块里：网格再长也不会把它们推到看不见的地方。 */}
      <div className="flex shrink-0 flex-col gap-3 border-t border-separator-border p-5">
        {/* 番号那一条会出网，是有副作用的提交，所以配按钮；回车照样能交。 */}
        <form className="flex min-w-0 items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (code.trim() && !busy) opening.mutate({ via: 'code', code: code.trim() });
          }}>
          <div className="min-w-0 flex-1">
            <Input aria-label="番号" placeholder="番号，如 ABW-232" value={code} onChange={setCode} />
          </div>
          <Button type="submit" variant="secondary" disabled={!code.trim()}
            {...busyProps(opens('code'))}>取封面来框</Button>
        </form>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" onClick={() => file.current?.click()}
            {...busyProps(opens('file'))}>从本机选图片</Button>
          {/* 原生文件选择器长相不可控，按钮归 BoardUI，输入框只留着接文件。 */}
          <input ref={file} type="file" accept="image/png,image/jpeg" tabIndex={-1} aria-hidden
            className="hidden" onChange={pickFile} />
          {/* 地址一行至少 224px：窄屏下和「从本机选图片」挤不进一行就整行换下去，输入框不被压成一条缝。 */}
          <div className="flex min-w-0 flex-1 basis-56 items-center gap-2">
            <div className="min-w-0 flex-1">
              <Input type="url" aria-label="图片地址" placeholder="https://…" value={url} onChange={setUrl} />
            </div>
            <Button disabled={!url.trim()} {...busyProps(opens('url'))}
              onClick={() => { if (!busy) opening.mutate({ via: 'url', url: url.trim() }) }}>用这个地址</Button>
          </div>
        </div>
      </div>
      </>
      )}
    </>
  );
}

/** 一格候选的图。图到之前那一格是 Skeleton 微光，到了骨架淡出、图从模糊里清晰起来。
 *
 *  要框的横图（作品画面、番号封面）不整张铺满居中：封面正中常常是书脊或背景，格子
 *  在后端给的取景区里取一块 3:4，看到的就是框选那一步默认框住的那张脸。竖的图库
 *  人像照旧 `object-cover`。 */
function ChoiceImage({ src, choice }: { src: string; choice: AvatarChoice }) {
  const image = useRef<HTMLImageElement>(null);
  const [revealed, setRevealed] = useState(false);
  const [lost, setLost] = useState(false);
  /* 缓存里的图可能在挂上 `onLoad` 之前就已经解码完，那一次 `load` 不会再来。 */
  useEffect(() => {
    if (image.current?.complete && image.current.naturalWidth) setRevealed(true);
  }, []);
  const size = { width: choice.width, height: choice.height };
  const place = framesItself(choice) ? windowStyle(choiceFrame(choice, size, TILE_ASPECT), size) : null;
  const shown = revealed || lost ? true : undefined;
  return (
    <span data-choice-lost={lost || undefined}
      className="relative block w-full aspect-avatar-choice overflow-hidden">
      {/* 取不到图时揭开骨架、撤掉图：骨架停在那儿读成还在等，留着图就是一枚破图标。
          格子剩一块空底，名字和来源照旧在下面那行与 title 里。 */}
      {lost ? null : <img ref={image} loading="lazy" alt="" src={src} data-revealed={shown}
        onLoad={() => setRevealed(true)} onError={() => setLost(true)}
        className={place
          ? 'reveal-content absolute max-w-none top-(--tile-top) left-(--tile-left) h-(--tile-height) w-(--tile-width)'
          : 'reveal-content absolute inset-0 size-full object-cover'}
        style={place ? {
          '--tile-top': place.top, '--tile-left': place.left,
          '--tile-height': place.height, '--tile-width': place.width,
        } as CSSProperties : undefined} />}
      <span aria-hidden data-revealed={shown}
        className="reveal-skeleton pointer-events-none absolute inset-0 skeleton-sheen" />
    </span>
  );
}

/** 本机文件在浏览器里的预览地址。离开框选就收回，不留着占内存。 */
function useFileUrl(file: File | undefined): string {
  const [url, setUrl] = useState('');
  useEffect(() => {
    if (!file) return undefined;
    const made = URL.createObjectURL(file);
    setUrl(made);
    return () => URL.revokeObjectURL(made);
  }, [file]);
  return url;
}

/** 框选那一步：一张底图、一个方框、一个圆预览。
 *
 *  比例锁死 1:1 且不给解锁：头像框是圆的，非方图装进去只会被再裁一次，而第二次
 *  裁在哪由 CSS 说了算，人在这里框的那一块就不作数了。
 *
 *  不是非框不可的图（人像那几路）多一个「整张使用」：有的图本来就是一张合用的大头照。 */
function CropStep({ kind, entityId, choice, file, busy, back, confirm }: {
  kind: string; entityId: number; choice: AvatarChoice; file?: File; busy: boolean;
  back(): void; confirm(picked: CropPick): void;
}) {
  const bases = choice.bases.length ? choice.bases : [choice.ref];
  const [base, setBase] = useState(bases[0]);
  const [size, setSize] = useState<CropSize | null>(null);
  const [box, setBox] = useState<CropBox | null>(null);
  const version = baseVersion(choice, base);
  const fileUrl = useFileUrl(file);
  const src = file ? fileUrl : choiceImageUrl(kind, entityId, base, version);
  const frame = useOverlayScrollbar<HTMLDivElement>();
  /* 换底图就是换一张图，上一张的框一个数都不留：同一组坐标落在另一张图上是一块
     错位的区域，而错位在屏幕上和「本来就框在这儿」看不出区别。 */
  function pickBase(next: string) {
    setBase(next);
    setSize(null);
    setBox(null);
  }
  const preview = box && size ? previewStyle(box, size) : null;
  return (
    <>
      <div ref={frame} className="flex min-h-0 flex-1 flex-col items-center gap-4 overflow-y-auto border-t border-separator-border px-5 py-4">
        {src ? (
          <CropFrame src={src} aspect={1} box={box} size={size}
            label={`${choice.label} 的头像取景框`}
            onSize={(next) => {
              setSize(next);
              /* 这一格自己那张图从取景区里落默认框：检出脸就正好是批处理会截的那一块。
                 换成九宫格的某一格就居中，取景区只对它自己那张作数。 */
              if (isUsableSize(next)) {
                setBox(base === choice.ref ? choiceFrame(choice, next, 1) : centeredBox(next, 1));
              }
            }}
            onBox={setBox} />
        ) : null}
        {/* 底图那一排：封面加九宫格九格。一部作品里哪一格有正脸，只能看着换。 */}
        {bases.length > 1 ? (
          <div role="radiogroup" aria-label="底图" className="flex flex-wrap justify-center gap-2">
            {bases.map((ref) => (
              <button type="button" key={ref} role="radio" aria-checked={ref === base}
                data-crop-base onClick={() => pickBase(ref)}
                className="cursor-pointer rounded-lg px-2 py-1 text-caption-1-regular text-text-secondary outline-none hover:text-text-primary focus-visible:ring-2 focus-visible:ring-border-focus-ring aria-checked:bg-background-tertiary-default aria-checked:text-text-primary">
                {baseLabel(ref)}
              </button>
            ))}
          </div>
        ) : null}
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-3 border-t border-separator-border p-5">
        {/* 圆预览就是它上线之后的样子。背景图和上面那张是同一个地址，不多取一次。 */}
        <span aria-hidden data-crop-preview
          className="size-crop-preview shrink-0 rounded-full border border-separator-border bg-background-secondary-default bg-(image:--crop-preview) bg-(position:--crop-preview-at) bg-(length:--crop-preview-size) bg-no-repeat"
          style={preview ? {
            '--crop-preview': `url("${src}")`,
            '--crop-preview-size': preview.size,
            '--crop-preview-at': preview.position,
          } as CSSProperties : undefined} />
        <div className="flex flex-1 flex-wrap items-center justify-end gap-3">
          <Button variant="secondary" onClick={back} {...busyProps(busy)}>返回备选图片</Button>
          {choice.crop ? null : (
            <Button variant="secondary" {...busyProps(busy)}
              onClick={() => { if (!busy) confirm(null) }}>整张使用</Button>
          )}
          <Button disabled={!box || !size} {...busyProps(busy)}
            onClick={() => { if (box && !busy) confirm({ ref: base, crop: box, version }) }}>用这一块</Button>
        </div>
      </div>
    </>
  );
}
