/* 作品详情（ADR-0031）：舞台里那一整块——播放区、右侧队列、侧栏与下方的「接着看」。
 *
 * 舞台（`<dialog id="stage">`）、它的进出场、小窗与 Video.js 都归舞台岛（`../stage/`），关注详情
 * 也在用；这里只画舞台里的内容，播放区的媒体框画好之后经 `actions.mountPlayer` 交给舞台挂播放器。
 * 点身份、标签、地区回列表或资料页，队列里换一条，都经壳：地址与顶栏上下文要换。队列里换条时舞台
 * 原地换内容，浮窗不重开。
 *
 * 写操作（评分、标签、反馈、稍后看、高清目标、偏好、播放列表排序与移出）都在岛里发，成功后
 * 把服务端回的那几个字段换进 `['item', id]`，目录网格缓存里的同一张卡一起换，不重读列表。 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { fmtDur, fmtSize, icon, requestErrorMessage } from '@peach/legacy/core';
import {
  coverUrl, mixLabel, performerLabel, queueAvatarHtml, queueThumbHtml,
} from '@peach/card-art';
import { confirmModal, dismissMenu, presentMenu, spinnerHtml } from '@peach/legacy/ui';

import { apiGet, apiSend } from '../../api';
import { prefersExternalPlayer } from '../../local-player';
import { Button } from '@/components/base/buttons/button';
import { RiArrowDownSLine } from '@remixicon/react';
import { CatalogGridPage } from '../catalog-grid/catalog-grid-page';
import { patchCatalogItems, replaceCatalogItem } from '../catalog-grid/catalog-grid';
import type { MediaItem } from '../catalog-grid/types';
import { RetryNote } from '../components/grid-reveal';
import { MixQueue, MixQueueRow } from '../components/mix-queue';
import { RelatedSection } from '../components/related-section';
import { DetailContinuation } from '../components/detail-continuation';
import { IdentityCell, IdentitySeries } from '../components/identity-cell';
import { useFoldedTitle } from '../components/folded-title';
import { MediaCarousel } from '../components/media-carousel';
import { CatalogPicture } from './catalog-picture';
import { fetchJavDetail, javDetailKey } from '../jav-detail/jav-detail';
import { busyProps } from '../settings/use-action';
import { ExternalPlayback } from './external-playback';
import { CoverCrop } from '../cover-crop/cover-crop-page';
import { queryClient } from '../query';
import { LibraryCompletionPrompt, missingPreviewIds } from '../library-processing/library-completion';
import {
  CAST_SHOWN, EDITION_TONE, FEEDBACK_URL, ITEM_TAG_URL, PLAYLIST_URL, PREFERENCE_URL, QUALITY_GOAL_URL,
  RELATED_URL, WATCH_LATER_URL, chooseItem, clampRating, detailTags, feedbackReceipt, fetchItem, fetchQueue,
  hasTag, identityGroups, isVanished, itemKey, mediaGate, movedOrder, nextPart, nextRating, partLabel, partLabelHtml, pickerSections, playlistQueue,
  queueCopy, queueKey, ratingStars, ratingText, realWatched, recentTags, relatedKey, rememberTag, sameOrder,
  withPartLabel, withTag, withoutTag,
  type DetailEntityRef, type DetailItem, type DetailQueue, type DetailTag, type ItemDetailActions,
  type ItemDetailHelpers, type ItemDetailProps, type MediaGate, type PlaylistPayload, type TagCount,
} from './item-detail';

/** 字形原样取壳那一份 `icon()`：骨架与遗留详情画的就是它，换成别的字形接管那一拍会跳。 */
const Glyph = ({ name }: { name: string }) => (
  <span className="contents" dangerouslySetInnerHTML={{ __html: icon(name) }} />
);
const Html = ({ html }: { html: string }) => <span className="contents" dangerouslySetInnerHTML={{ __html: html }} />;

/** 文件大小：没记下或记成 0 的写「大小未知」，同卡片上那一格。 */
const sizeText = (size: unknown) => (Number(size) > 0 ? fmtSize(Number(size)) : '大小未知');

export function ItemDetailPage(props: ItemDetailProps) {
  const { queue: ref, actions } = props;
  const queueResult = useQuery({
    queryKey: ref ? queueKey(ref) : ['item-queue', 'none'],
    queryFn: ({ signal }) => fetchQueue(ref!, props.helpers, signal),
    enabled: !!ref,
  });
  const queue = ref ? queueResult.data || null : null;
  const id = queue ? chooseItem(queue, props.id) : ref ? null : props.id;
  const result = useQuery({
    queryKey: itemKey(id ?? 0),
    queryFn: ({ signal }) => fetchItem(id!, signal),
    enabled: id != null,
  });
  if (!result.data || (ref && !queue)) {
    const error = result.error || queueResult.error;
    return (
      <div data-stage-grid="" data-item-detail="">
        <div data-stage-media="" data-item-media="none">
          <CloseStage onClose={actions.close} />
          <div data-item-placeholder="">
            {error ? <RetryNote message={requestErrorMessage(error)}
              onRetry={() => { if (queueResult.error) void queueResult.refetch(); if (result.error) void result.refetch() }} /> : null}
          </div>
        </div>
      </div>
    );
  }
  return <Detail key={result.data.id} {...props} item={withPartLabel(result.data, queue)} queue={queue} />;
}

/** 关闭键。`id` 留着：Escape、点浮窗外面与壳的兜底都按 `#closeStage` 找它再点一下。 */
const CloseStage = ({ onClose }: { onClose(): void }) => (
  <button type="button" data-stage-close="" id="closeStage" title="关闭" aria-label="关闭" onClick={onClose}
    dangerouslySetInnerHTML={{ __html: icon('x') }} />
);

function Detail(props: ItemDetailProps & { item: DetailItem; queue: DetailQueue | null }) {
  const { item, queue, actions, helpers } = props;
  const write = useDetailWrite(item, actions, helpers.tagLabel);

  /* 画出来的是哪一条只报一次：写完换进来的是同一条的新对象，不能因此重推地址、重画顶栏。 */
  const presented = useRef(item);
  useLayoutEffect(() => { actions.present(presented.current, queue) }, [actions]);
  /* 播放列表记下续播位置：每打开一条报一次，失败不打扰（下次打开照旧从记下的那一条开始）。 */
  useEffect(() => {
    if (queue?.kind !== 'playlist') return;
    apiSend(PLAYLIST_URL, { action: 'progress', id: queue.playlistId, asset_id: item.id }).catch(() => {});
  }, []);

  const gate = mediaGate(item, helpers.sourceOffline(item.location || ''));
  const next = nextPart(queue, item.id);
  const continuePart = () => {
    if (next && queue) actions.openQueueItem({ kind: queue.kind, seedId: queue.seedId, autoplay: true }, next.id);
  };
  return (
    <>
      <div data-stage-grid="" data-with-queue={queue ? '' : undefined} data-item-detail="">
        <MediaFrame item={item} gate={gate} helpers={helpers} actions={actions}
          autoplay={props.queue?.autoplay} onEnded={continuePart} />
        <Side item={item} queue={queue} write={write} helpers={helpers} actions={actions} />
      </div>
      <DetailContinuation editionCards={queue?.kind === 'editions' ? queue.items.length : undefined}
        queue={queue ? <Queue queue={queue} itemId={item.id} helpers={helpers} actions={actions} /> : null}>
        {props.relatedLimit > 0 && (!queue || queue.kind === 'parts' || queue.kind === 'editions')
          ? <Related {...props} item={queue ? { ...item, id: queue.seedId ?? item.id } : item} /> : null}
      </DetailContinuation>
    </>
  );
}

/* ── 播放区 ── */

function MediaFrame({ item, gate, helpers, actions, autoplay, onEnded }: {
  item: DetailItem; gate: MediaGate; helpers: ItemDetailHelpers; actions: ItemDetailActions;
  autoplay?: boolean; onEnded(): void;
}) {
  /* 计费来源先挡一层说明，点了才拉流；没挂载与在线拦截的那两种不挂播放器。 */
  const [started, setStarted] = useState<'auto' | 'clicked' | ''>(gate && !(gate === 'disc' && prefersExternalPlayer()) ? '' : 'auto');
  /* 播放器挂在这块框里，`<video>` 由舞台建：Video.js 会把它包进自己的 div，从小窗展开回来时
     整个实例还要搬进来，那一截 DOM 不归 React。 */
  const frameRef = useRef<HTMLDivElement>(null);
  const [image, setImage] = useState(-2);
  const refreshSamples = useRef(false);
  const source = useQuery({ queryKey: javDetailKey(item.code || ''),
    queryFn: ({ signal }) => {
      const refresh = refreshSamples.current;
      refreshSamples.current = false;
      return fetchJavDetail(item.code!, signal, refresh);
    }, staleTime: 6 * 3600_000,
    enabled: !!item.is_jav && !!item.code && !item.sample_images?.length });
  const samples = item.sample_images?.length ? item.sample_images.map(sample => ({ index: sample.n, source: false }))
    : Array.from({ length: Math.min(30, Math.max(0, source.data?.preview_count || 0)) }, (_, index) => ({ index: index + 1, source: true }));
  const media = item.is_jav && item.code ? [...(item.has_cover ? [{ index: -1, label: '封面' }] : []),
    ...samples.map((sample, index) => ({ ...sample, label: `预览 ${index + 1}` })),
    { index: -2, label: '正片', video: true }] : [];
  const hasPictures = media.length > 1;
  const selectImage = (index: number) => {
    if (index !== -2) frameRef.current?.querySelector('video')?.pause();
    setImage(index);
  };
  const mounting = useRef(item);
  const ended = useRef(onEnded);
  ended.current = onEnded;
  useLayoutEffect(() => {
    const node = frameRef.current;
    if (!node || !started) return;
    const complete = () => ended.current();
    node.addEventListener('ended', complete, true);
    const unmount = actions.mountPlayer(node, mounting.current, null,
      started === 'clicked' || autoplay ? { autoplay: true } : undefined);
    return () => { node.removeEventListener('ended', complete, true); unmount() };
  }, [actions, started]);
  const badge = helpers.badgeHtml(item.location || '', item.cost || '', 'srcbig');
  return (
    <div ref={frameRef} data-stage-media="" data-item-media={gate || 'video'} data-item-gallery={hasPictures ? '' : undefined}
      data-item-picture={image !== -2 ? '' : undefined}>
      <CloseStage onClose={actions.close} />
      {hasPictures ? <>
        <button type="button" data-jav-preview-toggle="" aria-pressed={image !== -2}
          onClick={() => selectImage(image === -2 ? media[0]!.index : -2)}><Glyph name={image === -2 ? 'image' : 'play'} />{image === -2 ? '查看图片' : '播放正片'}</button>
        {image !== -2 ? <CatalogPicture key={image} item={item} image={image} source={samples.find(sample => sample.index === image)?.source} /> : null}
        <MediaCarousel items={media} position={Math.max(0, media.findIndex(entry => entry.index === image))} onSelect={selectImage}
          below renderThumbnail={entry => <CatalogPicture item={item} image={entry.index === -2 ? media[0]!.index : entry.index}
            source={samples.find(sample => sample.index === (entry.index === -2 ? media[0]!.index : entry.index))?.source} thumbnail />} />
      </> : null}
      {item.is_jav && item.code && !item.sample_images?.length && (source.isPending || source.error || source.data?.state === 'partial') ?
        <div data-item-samples-state="">{source.isPending ? <span data-skeleton="samples" role="status" aria-label="正在读取样图" className="relative block h-12 w-20 skeleton-sheen" />
          : <Button variant="secondary" {...busyProps(source.isFetching)} title={source.data?.message || requestErrorMessage(source.error)}
            onClick={() => { refreshSamples.current = true; void source.refetch() }}>重试样图</Button>}</div> : null}
      {gate === 'offline' ? <OfflineGate item={item} badge={badge} helpers={helpers} actions={actions} />
        : gate === 'disc' ? (
          <div role="status" data-item-gate="disc">
            <b>ISO 原盘</b>
            <span>请使用支持原盘的外部播放器打开。</span>
            <ExternalPlayback id={item.id} />
          </div>
        ) : gate === 'online' ? (
          <div id="onlineGate" role="status" data-item-gate="online">
            <Html html={badge} />
            <b>在线作品</b>
            <span>这条没有对应的关注条目，媒体地址无从解析。</span>
            <button className="chip" id="openSavedFollow" type="button" onClick={actions.openSavedFollow}>打开已保存关注</button>
          </div>
        ) : gate === 'metered' && !started ? (
          <div id="gate" data-item-gate="metered" onClick={() => setStarted('clicked')}>
            <Html html={badge} />
            <span>点此开始拉流 · {sizeText(item.size)}</span>
          </div>
        ) : null}
    </div>
  );
}

function OfflineGate({ item, badge, helpers, actions }: {
  item: DetailItem; badge: string; helpers: ItemDetailHelpers; actions: ItemDetailActions;
}) {
  const [checking, setChecking] = useState(false);
  const [still, setStill] = useState(false);
  const retry = async () => {
    setChecking(true);
    const online = await actions.checkSource(item.location || '').catch(() => false);
    setChecking(false);
    if (online) actions.reopen();
    else setStill(true);
  };
  return (
    <div id="offlineGate" role="status" data-item-gate="offline">
      <Html html={badge} />
      <b>媒体目录无法访问</b>
      <span>{helpers.offlineReason(item.location || '')}</span>
      <button className="chip" id="offlineRetry" type="button" disabled={checking} onClick={() => void retry()}>
        {still ? '仍无法读取 · 刷新' : '刷新状态'}
      </button>
    </div>
  );
}

/* ── 队列 ── */

function Queue({ queue, itemId, helpers, actions }: {
  queue: DetailQueue; itemId: number; helpers: ItemDetailHelpers; actions: ItemDetailActions;
}) {
  const copy = queueCopy(queue);
  const list = useRef<HTMLDivElement | null>(null);
  const reorder = usePlaylistReorder(queue, itemId, actions);
  const reorderRef = useRef(reorder);
  reorderRef.current = reorder;
  const listRef = useCallback((node: HTMLDivElement | null) => {
    list.current = node;
    if (!node) return;
    helpers.wireDrag(node);
    /* 顺序直接拖：一列十几条，靠上移下移把最后一条挪到第二位要按十几次，而每一次都是一趟写库
       加一次重绘。拖动中的行态由 `wireDragReorder` 挂在行上，行是按 id 复用的，重排之后监听还在。 */
    if (queue.kind === 'playlist') {
      helpers.wireDragReorder(node, {
        selector: '[data-queue-row]', attribute: 'data-queue-row',
        onMove: (from, target, after) => reorderRef.current.move(+from, +target, after),
      });
    }
  }, [helpers, queue.kind]);
  const saveMix = useSaveMix(queue, actions);
  const head = queue.kind === 'mix'
    ? <button type="button" data-save-mix="" title="保存为播放列表" aria-label="保存为播放列表" onClick={saveMix}
      dangerouslySetInnerHTML={{ __html: icon('playlist') }} />
    : queue.kind === 'playlist'
      ? <button type="button" data-edit-playlist="" title="编辑播放列表" aria-label="编辑播放列表" onClick={actions.editPlaylist}
        dangerouslySetInnerHTML={{ __html: icon('playlist') }} />
      : null;
  const ref = { kind: queue.kind, seedId: queue.seedId, playlistId: queue.playlistId };
  return (
    <MixQueue kind={queue.kind} title={copy.title} summary={copy.summary} onClose={actions.close} actions={head} listRef={listRef}>
      {queue.items.map((row) => {
        const edition = queue.kind === 'editions' && row.edition_label
          ? <i className={`javedition ${EDITION_TONE[row.edition_label] || 'censored'}`} data-queue-edition="">{row.edition_label}</i> : null;
        // 已消失的条目照旧列在播放列表里、能移出，但文件不在盘上，这一行不能点开去播放。
        const gone = queue.kind === 'playlist' && isVanished(row);
        return (
          <MixQueueRow key={row.id} current={row.id === itemId} data-queue-item={row.id} row={{ 'data-queue-row': row.id }}
            disabled={gone} title={gone ? '文件已不在盘上，不能播放' : undefined}
            {...(gone ? { 'data-queue-vanished': '' } : {})}
            onClick={() => { if (!gone) actions.openQueueItem(ref, row.id) }}
            pic={<><Html html={queueThumbHtml(row, helpers.javImage())} /><i className="mono" data-mix-item-duration="">{fmtDur(row.duration)}</i></>}
            lead={<Html html={queueAvatarHtml(row)} />}
            after={queue.kind === 'playlist' ? (
              <span data-queue-edit="">
                <i data-queue-grip="" aria-hidden="true"><Glyph name="grip-vertical" /></i>
                <button type="button" data-queue-remove={row.id} title="移出播放列表" aria-label="移出播放列表"
                  onClick={() => reorder.remove(row.id)}><Glyph name="x" /></button>
              </span>
            ) : null}>
            <span data-queue-head="">{edition}<b data-middle-truncate="">{helpers.displayName(row)}</b></span>
            <span data-truncate-end="">{gone ? '已消失 · 文件已不在盘上'
              : queue.kind === 'parts' || (queue.kind === 'editions' && row.part_label)
                ? partLabel(row.part_label) : mixLabel(row, helpers.tagLabel)}</span>
          </MixQueueRow>
        );
      })}
    </MixQueue>
  );
}

/** 保存 Mix：壳弹表单（舞台是原生模态，弹层要进同一层），确认时由这里写库。 */
function useSaveMix(queue: DetailQueue, actions: ItemDetailActions) {
  const save = useMutation({
    mutationFn: (name: string) => apiSend<{ playlist: PlaylistPayload }>(PLAYLIST_URL, {
      action: 'create', name, asset_ids: queue.items.map((row) => row.id), source_kind: 'mix', source_seed_asset_id: queue.seedId,
    }).then((result) => result.playlist),
  });
  return () => actions.saveMix({ title: queue.title, count: queue.items.length, save: (name) => save.mutateAsync(name) });
}

/** 播放列表的排序与移出。两者写完都把服务端回的整份列表换进队列缓存，行原地重排，不重挂舞台；
 *  移出的正是这一条时才经壳换到下一条，列表被移空就回列表页。撤销拿的是动作之前那一份完整顺序，
 *  所以一次拖动无论跨多少行都只需按一次撤销。 */
function usePlaylistReorder(queue: DetailQueue, itemId: number, actions: ItemDetailActions) {
  const key = queueKey({ kind: 'playlist', playlistId: queue.playlistId });
  const apply = (playlist: PlaylistPayload) => {
    queryClient.setQueryData(key, playlistQueue(playlist));
    return playlist;
  };
  const write = (body: Record<string, unknown>) =>
    apiSend<{ playlist: PlaylistPayload }>(PLAYLIST_URL, { id: queue.playlistId, ...body }).then((result) => result.playlist);
  const reorder = useMutation({
    mutationFn: (ids: number[]) => write({ action: 'reorder', asset_ids: ids }),
    onSuccess: (playlist, _ids) => { apply(playlist) },
    onError: (error) => actions.failure('调整播放顺序', error),
  });
  const ids = queue.items.map((row) => row.id);
  return {
    move: async (from: number, target: number, after: boolean) => {
      if (queue.kind !== 'playlist') return;
      const next = movedOrder(ids, from, target, after);
      if (sameOrder(next, ids)) return;
      const before = ids;
      await reorder.mutateAsync(next);
      actions.toast('已调整播放顺序', { undo: async () => { apply(await write({ action: 'reorder', asset_ids: before })) } });
    },
    remove: (assetId: number) => {
      const before = ids;
      void confirmModal({
        title: '移出播放列表', body: '这个视频将从播放列表移除，视频文件保留。', confirmLabel: '移出播放列表',
        onConfirm: async () => {
          const playlist = await write({ action: 'remove', asset_id: assetId });
          /* 撤销要把它放回它那一位：`add` 只会补在末尾，所以补完再按移出之前那份顺序排一次。
             列表被这一下清空时它已经不在页面上，撤销后带回列表页而不是空队列。 */
          actions.toast('已移出播放列表', {
            undo: async () => {
              await write({ action: 'add', asset_ids: [assetId] });
              const restored = apply(await write({ action: 'reorder', asset_ids: before }));
              if (!playlist.items.length || assetId === itemId) {
                actions.openQueueItem({ kind: 'playlist', playlistId: restored.id }, itemId, false);
              }
            },
          });
          if (!playlist.items.length) { actions.openPlaylists(); return }
          apply(playlist);
          if (assetId === itemId) {
            actions.openQueueItem({ kind: 'playlist', playlistId: playlist.id }, playlist.current_asset_id || playlist.items[0]!.id, false);
          }
        },
      });
    },
  };
}

/* ── 侧栏 ── */

function Side({ item, queue, write, helpers, actions }: {
  item: DetailItem; queue: DetailQueue | null; write: DetailWrite; helpers: ItemDetailHelpers; actions: ItemDetailActions;
}) {
  const online = item.location === 'online';
  const busy = write.busyAttrs;
  const [reasonOpen, setReasonOpen] = useState(false);
  return (
    <div data-stage-side="" data-item-side="">
      <div data-stage-side-content="">
        <DetailTitle item={item} queue={queue} helpers={helpers} actions={actions} onStatus={write.setSourceState} />
        {online ? null : <span data-title-state="" aria-live="polite">{write.sourceState}</span>}
        <Rating item={item} write={write} />
        <div className="mono" data-stage-meta="" data-reveal-line="">
          {item.width && item.height
            ? <span data-spec-item=""><Glyph name="monitor" /><span>{item.width}×{item.height}</span></span>
            : null}
          <span data-spec-item=""><Glyph name="hard-drive" /><span>{sizeText(item.size)}</span></span>
          {item.release_date ? <span data-spec-item=""><Glyph name="calendar" /><span>{item.release_date}</span></span> : null}
          {item.region_label ? (
            <button type="button" data-spec-item="" data-open-region={item.region || ''}
              title={item.region_settled ? '已判定的产地；打开同产地的作品' : '按番号或厂牌推断的产地，批量判定后不再变；打开同产地的作品'}
              onClick={() => actions.openRegion(item.region || '')}>
              <Glyph name="globe" /><span>{item.region_label}{item.region_settled ? '' : '（推断）'}</span>
            </button>
          ) : null}
        </div>
        <Identity item={item} helpers={helpers} actions={actions} />
        <Tags item={item} write={write} helpers={helpers} actions={actions} />
        <LibraryCompletionPrompt assetIds={missingPreviewIds([item], helpers.javImage())}
          onComplete={() => { void queryClient.invalidateQueries({ queryKey: itemKey(item.id) }) }} />
        <Trace item={item} />
        {item.medium === 'video' && item.location !== 'online' ? <ExternalPlayback key={item.id} id={item.id} /> : null}
        <div data-stage-actions="" data-item-feedback="">
          <button type="button" id="likeBtn" data-fb="like" aria-label={item.liked ? '取消喜欢' : '喜欢'} title="喜欢 · 记录口味偏好"
            aria-pressed={!!item.liked} {...busy('like')} onClick={() => write.preference({ liked: !item.liked })}><Glyph name="thumbs-up" /></button>
          <button type="button" id="preferenceToggle" data-fb="reason" aria-label="喜爱理由" title="喜爱理由" aria-expanded={reasonOpen}
            aria-controls="preferencePanel" data-has-reason={String(!!item.like_reason)}
            onClick={() => setReasonOpen((open) => !open)}><Glyph name="notebook-pen" /></button>
          <button type="button" data-stage-action="dislike" data-kind="dislike" aria-label="不合口味" title="不合口味 · 降低推荐权重"
            aria-pressed={item.feedback === 'dislike'} {...busy('dislike')} onClick={() => write.feedback('dislike')}><Glyph name="thumbs-down" /></button>
          <button type="button" data-stage-action="seen" data-kind="seen" aria-label="看过了" title="看过了 · 只降低近期推荐"
            aria-pressed={item.feedback === 'seen'} {...busy('seen')} onClick={() => write.feedback('seen')}><Glyph name="eye" /></button>
          <button type="button" data-stage-action="later" id="stageLater" aria-label="稍后看" title="稍后看 · 加入或移出队列"
            aria-pressed={!!item.watch_later} {...busy('later')} onClick={write.later}><Glyph name={item.watch_later ? 'check' : 'bookmark-plus'} /></button>
          <button type="button" id="addPlaylist" data-fb="playlist" aria-label="加入播放列表" title="加入播放列表"
            onClick={() => actions.addToPlaylist(item)}><Glyph name="playlist" /></button>
          <button type="button" id="betterVersion" data-fb="quality" aria-label="寻找更好版本"
            title={item.better_version ? (item.better_version_reason || '已标记寻找更好版本') : '寻找高清、无水印或完整版'}
            aria-pressed={!!item.better_version} {...busy('quality')} onClick={write.quality}><Glyph name="sparkles" /></button>
          <button type="button" data-kind="dispose" data-fb="dispose" aria-label="移入回收站" title="移入回收站 · 文件仍保留，可从回收站永久清除"
            aria-pressed={item.disposal === 'trash'} {...busy('dispose')} onClick={() => write.feedback('dispose')}><Glyph name="trash" /></button>
        </div>
        <Preference item={item} open={reasonOpen} write={write} />
        <button type="button" data-kind="o" {...busy('o')} onClick={() => write.feedback('o')}>
          <Glyph name="sperm" /><span>记一次高潮</span><b className="mono" id="oCount">{item.o_count || 0}</b>
        </button>
      </div>
    </div>
  );
}

/* 标题默认折成两行，真溢出才给展开键：折叠态下 scrollHeight 比 clientHeight 高，就是有行被裁掉了。
   展开与收起换字形——往下多看一截是 chevron-down，收回去是 chevron-up。展开键排在那排键最前面，
   紧挨着它管的标题。标题文字本身也接这一下：人想看全文时手本来就落在字上。选中文字那一下不算，
   那是在复制标题。溢出不只在打开那一刻判：影院模式和普通视图之间切换时标题栏宽度差一大截。 */
function DetailTitle({ item, queue, helpers, actions, onStatus }: {
  item: DetailItem; queue: DetailQueue | null; helpers: ItemDetailHelpers; actions: ItemDetailActions;
  onStatus(text: string): void;
}) {
  const { text, expanded, foldable, toggle } = useFoldedTitle(String(item.title || item.name || ''));
  const html = helpers.badgeHtml(item.location || '', item.cost || '', 'srcbig') + helpers.titleHtml(item) + partLabelHtml(item, queue);
  return (
    <div data-item-title="">
      <div data-stage-title="" data-title-line="" data-reveal-line="">
        <span ref={text} data-detail-title="" data-expanded={expanded ? '' : undefined}
          data-foldable={foldable ? '' : undefined}
          onClick={() => { if (foldable && !String(getSelection() || '')) toggle() }}
          dangerouslySetInnerHTML={{ __html: html }} />
        <span data-title-tools="">
          <button type="button" data-title-fold="" hidden={!foldable} aria-expanded={expanded}
            aria-label={expanded ? '收起标题' : '展开标题'} title={expanded ? '收起标题' : '展开完整标题'} onClick={toggle}>
            <Glyph name={expanded ? 'chevron-up' : 'chevron-down'} />
          </button>
          {/* 取景框存的是坐标不是图片：存完重取这一条，封面地址没变，变的是接口给的 `poster_box`。
              背后网格里同一番号的卡不重取，就地换上新框，关掉详情看到的就是框过的样子。 */}
          {item.has_cover && (item.cover_key || item.code) ? (
            <span data-cover-crop-host="">
              <CoverCrop code={item.cover_key || item.code || ''} coverUrl={coverUrl(item)} box={item.poster_box || null}
                onSaved={(box) => {
                  void queryClient.invalidateQueries({ queryKey: itemKey(item.id), exact: true });
                  patchCatalogItems((one) => (one.cover_key || one.code) === (item.cover_key || item.code), { poster_box: box });
                }} />
            </span>
          ) : null}
          {item.location === 'online' ? null : <SourceTools id={item.id} actions={actions} onStatus={onStatus} />}
        </span>
      </div>
    </div>
  );
}

/** 定位源文件与核对目录。对账删掉的可能就是这一条，删了就没什么可停留的，直接退回列表。 */
function SourceTools({ id, actions, onStatus }: { id: number; actions: ItemDetailActions; onStatus(text: string): void }) {
  const [busy, setBusy] = useState('');
  const reveal = async () => {
    if (busy) return;
    setBusy('reveal');
    onStatus('');
    onStatus(await actions.reveal(id));
    setBusy('');
  };
  const sync = async () => {
    if (busy) return;
    setBusy('sync');
    onStatus('正在核对目录…');
    const result = await actions.sync(id);
    onStatus(result.text);
    setBusy('');
    if (result.removed.includes(id)) actions.close();
  };
  return (
    <>
      <button type="button" data-reveal={id} title="在文件管理器里打开源文件所在目录" aria-label="定位源文件"
        {...(busy === 'reveal' ? { 'aria-busy': true, 'aria-disabled': true } : {})} onClick={() => void reveal()}
        dangerouslySetInnerHTML={{ __html: busy === 'reveal' ? spinnerHtml('正在定位') : icon('folder-open') }} />
      <button type="button" data-sync={id} title="核对该目录：磁盘上已删除的移入 Peach 回收站，带个人记录的标为已消失" aria-label="同步删除"
        {...(busy === 'sync' ? { 'aria-busy': true, 'aria-disabled': true } : {})} onClick={() => void sync()}>
        <Glyph name="folder-sync" />
      </button>
    </>
  );
}

/* 五颗星站在标题和那行规格之间：评分是对这一条作品的判断，属于身份的一部分。 */
function Rating({ item, write }: { item: DetailItem; write: DetailWrite }) {
  const on = ratingStars(item.rating);
  return (
    <div id="detailRating" role="group" data-item-rating="" aria-label="评分" data-value={clampRating(item.rating)}>
      <div data-rating-stars="">
        {[1, 2, 3, 4, 5].map((star) => (
          <button key={star} type="button" data-rate={star} data-on={String(star <= on)}
            title={star === on ? '再点一次取消评分' : `${star} 星`}
            aria-label={star === on ? `取消评分（当前 ${star} 星）` : `评为 ${star} 星`}
            {...write.busyAttrs(`rate:${star}`)} onClick={() => write.rate(star)}><Glyph name="star" /></button>
        ))}
      </div>
      <span data-rating-value="" aria-live="polite">{ratingText(item.rating)}</span>
    </div>
  );
}

/* 身份按类别分组：标签作为组标题写在上方，同类横向排开。逐行一个名字在共演作品上会把整个侧栏
   撑满。一个都没有时，「未归属」就是这条作品所属的那一类，和女优、厂牌并列：点进去看得到全部。 */
function Identity({ item, actions }: { item: DetailItem; helpers: ItemDetailHelpers; actions: ItemDetailActions }) {
  const groups = useMemo(() => identityGroups(item), [item]);
  const [castOpen, setCastOpen] = useState(false);
  const overflow = Math.max(0, groups.cast.length - CAST_SHOWN);
  const cell = (kind: string, ref: DetailEntityRef, index: number) => {
    const hide = kind === 'performer' && index >= CAST_SHOWN && !castOpen;
    return <IdentityCell key={`${kind}:${ref.name}`} kind={kind} person={ref} onOpen={actions.openEntity}
      hidden={hide} overflow={kind === 'performer' && index >= CAST_SHOWN} />;
  };
  const group = (label: string, kind: string, list: DetailEntityRef[], extra: ReactNode = null) => (list.length ? (
    <section key={`${kind}:${label}`} data-id-group={kind}>
      <h5 data-id-label="">{label}</h5>
      <div data-id-row="">{list.map((ref, index) => cell(kind, ref, index))}{extra}</div>
    </section>
  ) : null);
  const primary = [
    groups.unowned ? (
      <section key="unowned" data-id-group="unowned">
        <h5 data-id-label="">归属</h5>
        <div data-id-row="">
          <button type="button" data-id-cell="unowned" data-open-unowned="" title="打开未归属：馆藏里没有署名人的作品"
            onClick={actions.openUnowned}>
            <span data-id-face=""><Glyph name="user-round" /></span><span data-id-name="">未归属</span>
          </button>
        </div>
      </section>
    ) : group(performerLabel(item), 'performer', groups.cast, overflow && !castOpen
      ? <Button variant="secondary" size="small" id="castMore" data-cast-more="" trailingIcon={RiArrowDownSLine}
        aria-expanded={false} onClick={() => setCastOpen(true)}>还有 {overflow} 位</Button> : null),
    group('厂牌', 'studio', groups.studios),
    group('片商', 'studio', groups.makers),
    group('卖家', 'creator', groups.creators.filter(ref => ref.identity_categories?.includes('seller'))),
    group('创作者', 'creator', groups.creators.filter(ref => !ref.identity_categories?.includes('seller'))),
  ].filter(Boolean);
  return (
    <div data-item-identity="">
      {primary.length ? <div data-identity-primary="">{primary}</div> : null}
      <IdentitySeries people={groups.series} onOpen={actions.openEntity} />
    </div>
  );
}

/* ── 标签 ── */

function Tags({ item, write, helpers, actions }: {
  item: DetailItem; write: DetailWrite; helpers: ItemDetailHelpers; actions: ItemDetailActions;
}) {
  const tags = detailTags(item, helpers);
  return (
    <div data-stage-tags="" id="detailTags" data-item-tags="">
      {tags.map((tag) => (
        <span key={tag.k} data-detail-tag="">
          <button type="button" data-tag={tag.k} title={helpers.tagLabel(tag.k)} onClick={() => actions.openTag(tag.k)}>{helpers.tagLabel(tag.k)}</button>
          <button type="button" data-remove-tag={tag.k} title="从此视频隐藏该标签"
            aria-label={`删除标签 ${helpers.tagLabel(tag.k)}`} {...write.busyAttrs(`tag:${tag.k}`)}
            onClick={() => write.removeTag(tag)}><Glyph name="x" /></button>
        </span>
      ))}
      <TagPicker item={item} write={write} helpers={helpers} />
    </div>
  );
}

/* 标签选择器：最近使用 + 全部（或搜索结果），上下键挑、回车加，输入的词没有完全命中就多一格
   「新建」。开合走全站菜单那一对口（`presentMenu`／`dismissMenu`），`hidden` 由它们管。
   选字过程中框里是半截拼音，筛选按 `term` 走、组完字才跟上，不拿「zhon」去筛。 */
function TagPicker({ item, write, helpers }: { item: DetailItem; write: DetailWrite; helpers: ItemDetailHelpers }) {
  const plus = useRef<HTMLButtonElement>(null);
  const picker = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const composing = useRef(false);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [term, setTerm] = useState('');
  const [active, setActive] = useState(-1);
  const close = useCallback(() => {
    if (picker.current) dismissMenu(picker.current);
    setOpen(false);
  }, []);
  /* 点外面就收起。延一拍再挂：打开它的这一次 pointerdown 还在冒泡，立刻挂上会自己把自己关掉。 */
  useEffect(() => {
    if (!open) return undefined;
    const handler = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!picker.current?.contains(target) && target !== plus.current) close();
    };
    const timer = setTimeout(() => document.addEventListener('pointerdown', handler, true), 0);
    return () => { clearTimeout(timer); document.removeEventListener('pointerdown', handler, true) };
  }, [open, close]);
  const sections = open ? pickerSections(term, helpers.tagCandidates(), recentTags()) : { recent: [], results: [], create: '' };
  const options = [...sections.recent, ...sections.results, ...(sections.create ? [{ k: sections.create, n: -1 }] : [])];
  const pick = (tag: TagCount, selected: boolean) => {
    close();
    if (!selected) write.addTag(tag.k);
  };
  const keydown = (event: KeyboardEvent<HTMLInputElement>) => {
    /* 选字那一下的回车是定字，不是「新建这个标签」——半截拼音会真的建成标签。 */
    if (event.nativeEvent.isComposing) return;
    if (event.key === 'Escape') { event.preventDefault(); close(); plus.current?.focus(); return }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!options.length) return;
      const next = (active + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
      setActive(next);
      picker.current?.querySelectorAll('[data-pick]')[next]?.scrollIntoView({ block: 'nearest' });
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      const chosen = active >= 0 ? options[active] : null;
      if (chosen) pick(chosen, chosen.n !== -1 && hasTag(item, chosen.k));
      else if (query.trim()) { close(); write.addTag(query.trim()) }
    }
  };
  let index = -1;
  const button = (tag: TagCount) => {
    index += 1;
    const at = index;
    const selected = hasTag(item, tag.k);
    return (
      <button key={`${at}:${tag.k}`} type="button" data-pick={tag.k} data-active={at === active ? '' : undefined}
        aria-pressed={selected} onClick={() => pick(tag, selected)}>
        <Glyph name={selected ? 'check' : 'tags'} /><span data-pick-name="">{helpers.tagLabel(tag.k)}</span>
        <span data-pick-count="">{(tag.n || 0).toLocaleString()}</span>
      </button>
    );
  };
  return (
    <>
      <button ref={plus} type="button" id="tagPlus" data-tag-plus="" title="添加标签" aria-label="添加标签" aria-expanded={open}
        onClick={() => {
          if (!picker.current) return;
          presentMenu(picker.current);
          setOpen(true); setQuery(''); setTerm(''); setActive(-1);
          requestAnimationFrame(() => search.current?.focus());
        }}><Glyph name="plus" /></button>
      <div ref={picker} id="tagPicker" data-tag-picker="" role="dialog" aria-label="添加标签" hidden>
        <label data-tag-search="">
          <Glyph name="search" />
          <input ref={search} id="tagPickSearch" maxLength={80} placeholder="搜索或输入新标签" autoComplete="off" value={query}
            onChange={(event) => { setQuery(event.target.value); setActive(-1); if (!composing.current) setTerm(event.target.value) }}
            onCompositionStart={() => { composing.current = true }}
            onCompositionEnd={(event) => { composing.current = false; setTerm(event.currentTarget.value) }} onKeyDown={keydown} />
        </label>
        <div id="tagPickBody" data-tag-body="">
          {open && sections.recent.length ? (
            <section data-tag-section=""><h4>最近使用</h4><div data-tag-grid="">{sections.recent.map(button)}</div></section>
          ) : null}
          {open ? (
            <section data-tag-section="">
              <h4>{term.trim() ? '搜索结果' : '全部标签'}</h4>
              <div data-tag-grid="">
                {sections.results.map(button)}
                {sections.create ? (() => {
                  index += 1;
                  const at = index;
                  return (
                    <button key="create" type="button" data-pick={sections.create} data-active={at === active ? '' : undefined}
                      onClick={() => { close(); write.addTag(sections.create) }}>
                      <Glyph name="plus" /><span data-pick-name="">新建“{sections.create}”</span>
                    </button>
                  );
                })() : null}
              </div>
            </section>
          ) : null}
        </div>
      </div>
    </>
  );
}

/* ── 观看轨迹 ──
   两条读数的 id 留给壳：Video.js 挂上之后，离开位置由 `wireTelemetry` 随播放写，真实观看在每次
   上报之后回填。这里只画初值，之后不再改它们。 */
function Trace({ item }: { item: DetailItem }) {
  const real = realWatched(item);
  return (
    <div data-item-trace="">
      <div data-trace-metric="">
        <div data-trace-label=""><span>离开位置</span><span id="ratioTxt">0%</span></div>
        <div data-trace-bar=""><u id="watched" /><b id="mark" /></div>
      </div>
      <div data-trace-metric="">
        <div data-trace-label=""><span>真实观看</span><span id="realTxt">{real == null ? '0%' : `${real.toFixed(0)}%`}</span></div>
        {/* oxlint-disable-next-line shadcn/no-inline-styles -- 宽度是这一条看过的比例，之后由壳的遥测随上报改写 */}
        <div data-trace-bar=""><u id="realBar" data-trace-real="" style={real == null ? undefined : { width: `${real.toFixed(1)}%` }} /></div>
      </div>
    </div>
  );
}

function Preference({ item, open, write }: { item: DetailItem; open: boolean; write: DetailWrite }) {
  const [reason, setReason] = useState(item.like_reason || '');
  const field = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (open) field.current?.focus() }, [open]);
  /* 服务端回来的理由（保存、撤销）换进输入框；正在打字时本地这一份不被覆盖，因为它只在写完才变。 */
  useEffect(() => { setReason(item.like_reason || '') }, [item.like_reason]);
  const saving = write.busy.has('preference');
  return (
    <div id="preferencePanel" data-item-preference="" hidden={!open}>
      <textarea ref={field} id="likeReason" maxLength={2000} placeholder="为什么喜欢？" value={reason}
        onChange={(event) => setReason(event.target.value)} />
      <div data-preference-foot="">
        <span id="preferenceState" aria-live="polite">{write.preferenceState}</span>
        <button type="button" className="geist-button primary" id="savePreference" data-save-preference="" title="提交喜爱理由" aria-label="提交喜爱理由"
          {...write.busyAttrs('preference')} onClick={() => write.preference({ reason })}
          dangerouslySetInnerHTML={{ __html: saving ? `${spinnerHtml('正在提交喜爱理由')}<span>提交中…</span>` : '<span>提交</span>' }} />
      </div>
    </div>
  );
}

/* ── 接着看 ──
   这一排的卡是目录网格的 items 模式：取数在这里，卡只画。空结果与失败留在推荐区，
   拖动与滚轮都靠壳的 `wireDrag`。 */
function Related(props: ItemDetailProps & { item: DetailItem }) {
  const { item, relatedLimit, helpers, grid } = props;
  const result = useQuery({
    queryKey: relatedKey(item.id, relatedLimit),
    queryFn: async ({ signal }) => {
      const page = await apiGet<{ items?: MediaItem[] }>(RELATED_URL(item.id, relatedLimit), signal);
      return page.items || [];
    },
  });
  const [skeleton] = useState(() => helpers.relatedSkeletonHtml());
  return (
    <RelatedSection wireDrag={helpers.wireDrag} busy={result.isPending} empty={result.data?.length === 0}
      error={result.isError ? `读取接着看失败：${requestErrorMessage(result.error)}` : undefined}
      onRetry={() => { void result.refetch() }}>
        {result.data ? (
          <CatalogGridPage mode="items" variant="next" items={result.data} helpers={grid.helpers} actions={grid.actions}
            layout={props.layout} selectMode={props.selectMode} selected={props.selected} seekSeconds={props.seekSeconds}
            revision={0} wireDrag={helpers.wireDrag} skeletonHtml={() => ''} />
        ) : <Html html={skeleton} />}
    </RelatedSection>
  );
}

/* ── 写操作 ──
 * 每一枚键各有自己的忙态（`aria-busy` + `aria-disabled`，控件仍可聚焦，重复触发由壳的
 * `wireBusyActions` 拦住）。成功后把服务端回的那几个字段换进 `['item', id]` 与目录缓存里同一张卡；
 * 撤销也是一次真实写回，写完同样换进去。 */
type DetailWrite = ReturnType<typeof useDetailWrite>;
type Patch = Partial<DetailItem>;

function useDetailWrite(item: DetailItem, actions: ItemDetailActions, label: (tag: string) => string) {
  const id = item.id;
  const key = itemKey(id);
  const [busy, setBusy] = useState<ReadonlySet<string>>(() => new Set());
  const [sourceState, setSourceState] = useState('');
  const [preferenceState, setPreferenceState] = useState('');
  const mark = (name: string, on: boolean) => setBusy((now) => {
    const next = new Set(now);
    if (on) next.add(name); else next.delete(name);
    return next;
  });
  const current = () => queryClient.getQueryData<DetailItem>(key) || item;
  const apply = (patch: Patch | ((item: DetailItem) => DetailItem)) => {
    const next = typeof patch === 'function' ? patch(current()) : { ...current(), ...patch };
    queryClient.setQueryData(key, next);
    const card = { feedback: next.feedback, disposal: next.disposal, watch_later: next.watch_later, rating: next.rating, o_count: next.o_count };
    replaceCatalogItem(id, card);
    return next;
  };
  const post = <T,>(url: string, body: Record<string, unknown>) => apiSend<T>(url, { id, ...body });

  type Run = { name: string; action: string; run(): Promise<void> };
  const task = useMutation({
    mutationFn: ({ run }: Run) => run(),
    onError: (error, { action }) => actions.failure(action, error),
    onSettled: (_result, _error, { name }) => mark(name, false),
  });
  const start = (name: string, action: string, run: () => Promise<void>) => {
    if (busy.has(name)) return;
    mark(name, true);
    task.mutate({ name, action, run });
  };

  type FeedbackResult = { feedback?: string | null; disposal?: string | null; o_count?: number; rating?: number | null };
  const sendFeedback = async (kind: string, value?: number) => {
    const result = await post<FeedbackResult>(FEEDBACK_URL, value === undefined ? { kind } : { kind, value });
    if (kind === 'rate') apply({ rating: result.rating ?? null });
    else apply({ feedback: result.feedback ?? undefined, disposal: result.disposal ?? undefined, o_count: result.o_count });
    return result;
  };

  return {
    busy, sourceState, setSourceState, preferenceState,
    busyAttrs: (name: string) => (busy.has(name) ? { 'aria-busy': true, 'aria-disabled': true } as const : {}),

    rate: (star: number) => start(`rate:${star}`, '评分', async () => {
      const before = current().rating || 0;
      const value = nextRating(before, star);
      await sendFeedback('rate', value);
      actions.toast(value ? `已评 ${ratingStars(value)} 星` : '已取消评分', { undo: async () => { await sendFeedback('rate', before) } });
    }),

    feedback: (kind: string) => start(kind, '操作', async () => {
      const before = { feedback: current().feedback || null };
      const result = await sendFeedback(kind);
      actions.toast(feedbackReceipt(kind, result), {
        undo: async () => {
          if (kind === 'o') await sendFeedback('o-undo');
          else if (kind === 'dispose') await sendFeedback('dispose');
          else {
            if (result.feedback) await sendFeedback(result.feedback);
            if (before.feedback) await sendFeedback(before.feedback);
          }
          await actions.trashChanged(current().disposal || null, true);
        },
      });
      if (kind === 'dispose') await actions.trashChanged(result.disposal || null, false);
    }),

    later: () => start('later', '更新稍后看', async () => {
      const result = await post<{ watch_later: boolean }>(WATCH_LATER_URL, {});
      apply({ watch_later: result.watch_later });
      actions.toast(result.watch_later ? '已加入稍后看' : '已移出稍后看', {
        undo: async () => { apply({ watch_later: (await post<{ watch_later: boolean }>(WATCH_LATER_URL, {})).watch_later }) },
      });
    }),

    quality: () => start('quality', '更新版本需求', async () => {
      const before = { wanted: !!current().better_version, reason: current().better_version_reason || '' };
      type Goal = { better_version: boolean; better_version_reason?: string };
      const paint = (goal: Goal) => apply({ better_version: goal.better_version, better_version_reason: goal.better_version_reason || '' });
      const result = await post<Goal>(QUALITY_GOAL_URL, { wanted: !before.wanted });
      paint(result);
      actions.toast(result.better_version ? '已标记寻找更好版本' : '已取消寻找更好版本', {
        undo: async () => { paint(await post<Goal>(QUALITY_GOAL_URL, before)) },
      });
    }),

    /* 喜欢与理由是同一条偏好：点喜欢键只改 `liked`，提交理由时有字就算喜欢。 */
    preference: ({ liked, reason }: { liked?: boolean; reason?: string }) => {
      const name = liked === undefined ? 'preference' : 'like';
      start(name, '保存喜欢偏好', async () => {
        const before = { liked: !!current().liked, reason: current().like_reason || '' };
        const text = reason ?? before.reason;
        const wanted = liked ?? (before.liked || text.trim().length > 0);
        type Preference = { liked: boolean; like_reason?: string };
        const paint = (result: Preference) => apply({ liked: result.liked, like_reason: result.like_reason || '' });
        setPreferenceState('保存中…');
        try {
          paint(await post<Preference>(PREFERENCE_URL, { liked: wanted, reason: text }));
        } catch (error) {
          setPreferenceState('保存失败 · 请重试');
          throw error;
        }
        setPreferenceState('已保存');
        setTimeout(() => setPreferenceState((now) => (now === '已保存' ? '' : now)), 1400);
        const saved = current();
        actions.toast(saved.liked ? '已保存喜欢偏好' : '已取消喜欢', {
          undo: async () => { paint(await post<Preference>(PREFERENCE_URL, before)) },
        });
      });
    },

    removeTag: (tag: DetailTag) => start(`tag:${tag.k}`, '删除标签', async () => {
      const result = await post<{ ok?: boolean }>(ITEM_TAG_URL, { operation: 'remove', tag: tag.k });
      if (!result.ok) throw new Error('标签未删除');
      apply((now) => withoutTag(now, tag.k));
      actions.toast(`已删除标签「${label(tag.k)}」`, {
        undo: async () => {
          await post(ITEM_TAG_URL, { operation: 'add', tag: tag.k });
          apply((now) => withTag(now, tag));
        },
      });
    }),

    addTag: (raw: string) => {
      const tag = raw.trim();
      if (!tag) return;
      start(`add:${tag}`, '添加标签', async () => {
        const result = await post<{ ok?: boolean; tag?: string }>(ITEM_TAG_URL, { operation: 'add', tag });
        if (!result.ok) return;
        const canonical = result.tag || tag;
        if (hasTag(current(), canonical)) {
          actions.toast(`标签「${label(canonical)}」已存在`);
          return;
        }
        apply((now) => withTag(now, { k: canonical, cat: 'general' }));
        rememberTag(canonical);
        actions.toast(`已添加标签「${label(canonical)}」`, {
          undo: async () => {
            await post(ITEM_TAG_URL, { operation: 'remove', tag: canonical });
            apply((now) => withoutTag(now, canonical));
          },
        });
      });
    },
  };
}
