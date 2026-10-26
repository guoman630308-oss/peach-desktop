/* 实体资料页的正文（`entity-body` island）：名册、作品网格与照片墙三个视图里的一个。
 *
 * 数据全由壳取好推进来，这里只挑出当前视图画。作品网格按 `revision` 换键：壳每发起一次新的
 * 作品请求，这一格先铺骨架（`items` 为 `null`），列表回来后骨架淡出、卡片从模糊里清晰起来，
 * 同目录那一格。骨架还没到显示门槛就回来的，直接落内容。 */
import { Fragment, useCallback, useMemo, useState } from 'react';

import { CatalogGridPage, LoadMore } from '../catalog-grid/catalog-grid-page';
import { useSkeletonReveal } from '../components/grid-reveal';
import { PAGE_SIZE } from '../index/index-data';
import { PeopleGrid } from '../index/index-people';
import { openPhotoLightbox } from '../photo-lightbox/photo-lightbox-dialog';
import type { LightboxSlide } from '../photo-lightbox/photo-lightbox';
import type {
  EntityBodyProps, EntityCodeSet, EntityPhotos, EntityRoster, EntitySamplePhoto, EntityWallPhoto,
} from './entity-body';

export function EntityBodyPage(props: EntityBodyProps) {
  if (props.view === 'people' && props.roster) return <Roster roster={props.roster} props={props} />;
  if (props.view === 'videos') return <VideoSection key={props.revision} {...props} />;
  if (props.view === 'photos' && props.photos) {
    return <PhotoSection key={props.photos.revision} photos={props.photos} props={props} />;
  }
  return null;
}

/** 名册一页摆几格，同索引页名册一页的格数。 */
const ROSTER_PAGE = PAGE_SIZE.people;

/** 名册一格点开进这个人（或这个厂牌）的资料页，取图同索引页那条回落链。
 *
 *  名单随资料一次下来，大事务所有上千人；一次全画出来就是上千个头像格和上千次取图。先摆一页，
 *  余下的走作品区同一枚「载入更多」，滚到附近自己接下一页。 */
function Roster({ roster, props }: { roster: EntityRoster; props: EntityBodyProps }) {
  const { helpers, actions } = props;
  const cell = useMemo(() => ({ personAvatar: helpers.personAvatar, openEntity: actions.openEntity }),
    [helpers, actions]);
  const [shown, setShown] = useState<number>(ROSTER_PAGE);
  const people = useMemo(() => roster.people.slice(0, shown), [roster.people, shown]);
  const loadMore = useCallback(async () => { setShown((count) => count + ROSTER_PAGE) }, []);
  return (
    <>
      <PeopleGrid kind={roster.kind} items={people} layout={roster.layout} props={cell} />
      {shown < roster.people.length
        ? <LoadMore key={shown} entity load={loadMore} enabled={() => true} />
        : null}
    </>
  );
}

/** 作品区：外面这一层只管「壳还在取」时的骨架，列表到了就是卡片网格的 entity 模式，
 *  第一页由壳给、续页经 `fetchPage` 取。`[data-entity-grid]` 是壳按 Shift 连选排卡片时认的那一格。 */
function VideoSection(props: EntityBodyProps) {
  const reveal = useSkeletonReveal(!props.items, props.skeletonHtml);
  return (
    <div data-entity-grid="" data-grid-reveal={reveal.fading ? '' : undefined}>
      {reveal.layer}
      {props.items ? (
        <CatalogGridPage mode="entity" entityKey={`${props.kind}:${props.name}`} revision={props.revision}
          initial={props.items} fetchPage={props.fetchPage} listScope={props.listScope} helpers={props.helpers} actions={props.actions}
          layout={props.layout} selectMode={props.selectMode} selected={props.selected}
          seekSeconds={props.seekSeconds} wireDrag={props.wireDrag}
          skeletonHtml={props.skeletonHtml} groupCollapse={props.groupCollapse} canLoadMore={props.canLoadMore} />
      ) : null}
    </div>
  );
}

/** 一部作品的样张铺成几格。张数、站名和标题已随 `/api/photos` 下来，不再请求一次。 */
const sampleItems = (set: EntityCodeSet): EntitySamplePhoto[] => Array.from({ length: set.n }, (_, i) => ({
  sample: true, code: set.code, position: i + 1, total: set.n, name: `${set.code} 样张 ${i + 1}`,
  source: set.site_label || '官方样张',
}));

const isSample = (item: EntityWallPhoto): item is EntitySamplePhoto => (item as EntitySamplePhoto).sample === true;

/** 样张的地址按番号与序号由服务端查，缩略图与灯箱里的大图共用。 */
const sampleQuery = (item: EntitySamplePhoto) => `code=${encodeURIComponent(item.code)}&n=${item.position}`;

/* 缩略图一律走 `/photo-thumb`（服务端缓存），只有灯箱里的大图读原图：PikPak 是计费来源，一屏
 * 直接铺原图等于付几十兆流量。样张走 `/sample-thumb`。 */
const thumbSrc = (item: EntityWallPhoto) => isSample(item)
  ? `/sample-thumb?${sampleQuery(item)}`
  : `/photo-thumb?id=${item.id}`;

/** 墙上一格换成灯箱里的一张。本地图片整条当 asset 递进去，详情面板读来源与大小、定位读 id。 */
const wallSlide = (item: EntityWallPhoto): LightboxSlide => isSample(item)
  ? { src: `/sample-image?${sampleQuery(item)}`, thumb: thumbSrc(item), name: item.name, asset: null,
    source: item.source }
  : { src: `/photo?id=${item.id}`, thumb: thumbSrc(item), name: item.name || '', asset: item };

/** 照片视图：名下每部作品的官方样张按发行日从新到旧一段一段铺在前面，每段一行段头；本地图片
 *  那一面墙在后面，只有本地图片时不出段头（ADR-0068）。
 *
 *  一面通铺的瀑布流按列往下填，一部的图会从上一列底部接到下一列顶部，逐张标番号又太吵；分段后
 *  一部只标一次。`data-photo-index` 是一格在整面墙里的序号，跨段连续，点下去把整列交给灯箱，
 *  灯箱照旧跨段连续翻。翻页只数本地图片：样张一次铺完，不走分页。 */
function PhotoSection({ photos, props }: { photos: EntityPhotos; props: EntityBodyProps }) {
  const { photoSize: size, photoLayout: layout, actions, canLoadMore } = props;
  const wall = useMemo(() => [...photos.codeSets.flatMap(sampleItems), ...photos.items], [photos]);
  /* 取不到图的格子。本地图片取不到说明文件没了，整格该走；样张取不到多半是来源这会儿不通，
   * 格子留着空底，序号不断，灯箱里照样翻得到。两种都不改别的格子的序号。 */
  const [lost, setLost] = useState<ReadonlySet<number>>(() => new Set());
  const lose = useCallback((index: number) => setLost((prev) => {
    if (prev.has(index)) return prev;
    const next = new Set(prev);
    next.add(index);
    return next;
  }), []);
  const open = useCallback((index: number) => {
    void openPhotoLightbox(index, wall.map(wallSlide), { revealSource: actions.revealSource });
  }, [actions, wall]);
  const cell = (item: EntityWallPhoto, index: number) => (
    <PhotoCell key={index} item={item} index={index} lost={lost.has(index)} onLose={lose} onOpen={open} />
  );
  const wallAttrs = { 'data-photo-wall': '', 'data-size': size, 'data-layout': layout };
  let start = 0;
  const groups = photos.codeSets.map((set) => {
    const from = start;
    start += set.n;
    return { set, from };
  });
  const localStart = start;
  return (
    <>
      {groups.map(({ set, from }) => (
        <Fragment key={set.code}>
          <GroupHead label={set.code} title={set.name}
            meta={[`${set.site_label || '官方'} 样张`, set.release_date, `${set.n.toLocaleString()} 张`].filter(Boolean).join(' · ')} />
          <div {...wallAttrs}>{wall.slice(from, from + set.n).map((item, i) => cell(item, from + i))}</div>
        </Fragment>
      ))}
      {groups.length && photos.total ? <GroupHead label="本地图片" meta={`${photos.total.toLocaleString()} 张`} /> : null}
      <div {...wallAttrs} data-local-wall="">
        {photos.items.map((item, i) => (lost.has(localStart + i) ? null : cell(item, localStart + i)))}
      </div>
      {photos.hasMore ? (
        <LoadMore key={photos.items.length} entity load={actions.loadMorePhotos}
          enabled={() => canLoadMore?.() !== false} />
      ) : null}
    </>
  );
}

/** 段头一行：番号、标题和右端「来源 样张 · 发行日 · 张数」。标题过长尾部省略，全名在 title 里。 */
function GroupHead({ label, title, meta }: { label: string; title?: string; meta: string }) {
  return (
    <div data-photo-group="">
      <b>{label}</b>
      {title ? <span data-photo-group-title="" title={title}>{title}</span> : null}
      <span data-photo-group-meta="">{meta}</span>
    </div>
  );
}

/** 一格。图还没到时是一块 `--sunk` 空底，不是一行文件名：`alt` 留给读屏，字色在样式里调成透明。 */
function PhotoCell({ item, index, lost, onLose, onOpen }: {
  item: EntityWallPhoto; index: number; lost: boolean; onLose(index: number): void; onOpen(index: number): void;
}) {
  const name = item.name ?? '';
  return (
    <button type="button" data-photo-cell="" data-photo-index={index} title={name} onClick={() => onOpen(index)}>
      {lost ? null : (
        <img src={thumbSrc(item)} alt={name} loading="lazy" decoding="async" fetchPriority="low"
          onError={() => onLose(index)} />
      )}
    </button>
  );
}
