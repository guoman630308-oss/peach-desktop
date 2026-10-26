/* 实体资料页（路由树画进 `#index`，`managed-routes.tsx` 的 `ENTITY_ROUTES`）：资料卡、筛选浮层、新作那一行与正文，一棵根、四块宿主。
 *
 * 资料卡画在壳交出的宿主里（`[data-entity-hero]` 那一格），浮层、新作与正文经 portal 画进壳排好的另外三块：浮层
 * 吸顶要它的父元素就是 `#index`，新作那一行是遗留层的卡片、不进 React 子树的样式范围，所以四块各
 * 守自己的宿主。页内状态都在这里：当前视图、名册还是作品、照片墙的种子与翻页；筛选与媒体视图读
 * 地址栏（壳推进来的 `filters`／`media`），改它们只调 `actions.route`。 */
import { useInfiniteQuery, useMutation, useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from '@peach/legacy/core';
import { tagLabel } from '@peach/legacy/tags';
import { confirmModal } from '@peach/legacy/ui';

import { Button } from '@/components/base/buttons/button';

import { errorMessage } from '../../api';
import type { MediaCardLayout } from '../catalog-grid/types';
import { EmptyState } from '../components/empty-state';
import { RetryNote } from '../components/grid-reveal';
import { spriteGlyph } from '../components/sprite-glyph';
import type { EntityBodyActions, EntityPhotos, EntityRoster } from '../entity-body/entity-body';
import { EntityBodyPage } from '../entity-body/entity-body-page';
import { EntityOnline } from '../entity-body/entity-online';
import type {
  EntityFilterActions, EntityFilterHelpers, EntityFilterTag, EntityPhotoHead, EntityVideoHead, EntityView, EntityViewKeys,
} from '../entity-filter/entity-filter';
import { EntityFilterPage } from '../entity-filter/entity-filter-page';
import type { EntityHeroActions, EntityHeroHelpers } from '../entity-hero/entity-hero';
import { EntityHeroPage } from '../entity-hero/entity-hero-page';
import { feedNewKey, feedNewOptions, postFeedAction } from '../feed-new/feed-new';
import { queryClient } from '../query';
import {
  EMPTY_MEDIA, codeSetsOf, entityKey, entityOptions, entityPhotosKey, fetchItems, itemsOptions,
  photosOptions, tagList, tagPressed, type EntityPageData, type EntityPageProps, type PhotoPage,
} from './entity-page';
import { useEntityOnline } from './use-entity-online';

export function EntityPage(props: EntityPageProps) {
  const { kind, name } = props;
  const entity = useQuery(entityOptions(kind, name));
  /* 名字对不上任何一位时 `/api/entity` 回 `{error}`：整块换成壳里那一屏空态，骨架不留着闪。空态是
     遗留层的标记，不放进 React 子树的样式范围。 */
  const missing = !!entity.data?.error;
  const canonical = entity.data?.canonical_name;
  const redirect = entity.data?.redirect ?? (canonical && canonical !== name ? { kind, name: canonical } : undefined);
  const { actions } = props;
  useLayoutEffect(() => { if (missing) actions.missing() }, [actions, missing]);
  /* 换到规范资料页排到微任务里：壳换页会重排页头与选中态，那一路可能走到批量条的 `flushSync`，
     提交阶段里它画不出来。这一代已卸下就不换。 */
  useLayoutEffect(() => {
    if (!redirect) return;
    let live = true;
    queueMicrotask(() => { if (live) actions.openEntity(redirect.kind, redirect.name, true) });
    return () => { live = false };
  }, [actions, redirect?.kind, redirect?.name]);
  if (entity.isError) return <RetryNote message={errorMessage(entity.error)} onRetry={() => { void entity.refetch() }} />;
  if (!entity.data || missing || redirect) return null;
  return <EntityLoaded {...props} entity={entity.data} />;
}

/** 键一变就加一的代次：作品网格与照片墙按它换键，骨架与续页都从头开始。 */
function useRevision(key: string): number {
  const [seen, setSeen] = useState({ key, n: 1 });
  if (seen.key === key) return seen.n;
  const next = { key, n: seen.n + 1 };
  setSeen(next);
  return next.n;
}

const newSeed = () => String(((Date.now() ^ (Math.random() * 1e9 | 0)) >>> 0) % 99991);

/** 照片一屏的读数：本地图片与样张各数各的；只有样张时不写「0 张」。 */
function photoReadout(page: PhotoPage, codeSets: number) {
  return `照片 · ${[
    page.total || !codeSets ? `${(page.total || 0).toLocaleString()} 张` : '',
    codeSets ? `样张 ${(page.sample_total || 0).toLocaleString()} 张 · ${codeSets.toLocaleString()} 部作品` : '',
  ].filter(Boolean).join(' · ')}`;
}

function EntityLoaded(props: EntityPageProps & { entity: EntityPageData }) {
  const { kind, name, entity, filters, media, helpers, actions, hosts } = props;
  const company = kind === 'agency' || kind === 'studio';
  /* 事务所名下的这批人不摆在资料卡底那排小圆头像里：那是「同台艺人」，一条附注；名册是这一页的
     正文。片商页的名册是旗下 label，同台艺人那排照旧留在卡底。 */
  const roster = kind === 'agency' ? (entity.related_performers || []) : kind === 'studio' ? (entity.labels || []) : [];
  const entityId = Number(entity.id) || 0;

  /* ── 照片：整组那一份数出照片档有几张、出不出；墙读的是当前这一组（整组或一个图集）。 ── */
  const base = useInfiniteQuery(photosOptions(kind, name, 0, ''));
  const basePage = base.data?.pages[0];
  const basePhotos = basePage && !basePage.error ? basePage : null;
  const photoCount = basePhotos ? (basePhotos.total || 0) + (basePhotos.sample_total || 0) : 0;
  const photosAvailable = (basePhotos?.total || 0) > 0 || codeSetsOf(basePhotos).length > 0;
  /* 关注里绑在这位名下的来源（ADR-0096）：有就多一个在线视图。本地一部视频都没有时作品视图是空的，
     视频键不出，进页就落在在线。 */
  const followKey = kind === 'creator' ? entity.follow?.key || '' : '';
  const onlineOnly = !!followKey && !entity.asset_count;
  const mediaNow = media.media === 'photos' && photosAvailable ? media
    : (media.media === 'online' || onlineOnly) && followKey ? { media: 'online' as const, set: 0 } : EMPTY_MEDIA;

  /* 名册与媒体不共用地址栏：地址栏只认 `media`，名册是事务所页与片商页的默认视图，进页就在那里。
     换了筛选就回到作品：标签是作品筛选，留在名册里既不生效，标签条也会自相矛盾。 */
  const [rosterView, setRosterView] = useState<'people' | 'videos'>('people');
  const filterKey = new URLSearchParams(Object.entries(filters).filter(([, value]) => value)).toString();
  const [seenFilters, setSeenFilters] = useState(filterKey);
  if (seenFilters !== filterKey) {
    setSeenFilters(filterKey);
    setRosterView('videos');
  }
  const view: EntityView = company && rosterView === 'people' && roster.length ? 'people' : mediaNow.media;

  /* ── 在线：筛选与取数同关注页，只按这位的作者键筛 ── */
  const online = useEntityOnline(followKey, view === 'online', {
    photoLayout: props.photoLayout, photoLayouts: props.photoLayouts, imagesOnly: props.followImagesOnly,
    setImagesOnly: props.follow.actions.setImagesOnly,
  });
  const photoWall = view === 'photos' || (view === 'online' && online.media === 'images');

  /* 换过几次头像。资料卡与作品网格都把图拼进了自己的状态（网格只在代次变时才按新的第一页
     重建），重取到新数据还不够，得换键重建，新图地址里的版本才落得到页面上。 */
  const [avatarEpoch, setAvatarEpoch] = useState(0);

  /* ── 作品 ── */
  const itemsQuery = itemsOptions(props);
  const items = useQuery(itemsQuery);
  const itemsRevision = useRevision(JSON.stringify([itemsQuery.queryKey, avatarEpoch]));
  /* 直达或刷新资料页时 URL 没有 `jav=1`：以第一页作品的真实 `is_jav` 恢复女优／厂牌语境，版式开关
     不是只在从 JAV 首页点进来时才偶然存在。只看进页那一份，页内换筛选不改语境。 */
  const [javPage] = useState(() => (kind === 'performer' || kind === 'studio')
    && (props.jav || (items.data?.items || []).some((item) => item.is_jav)));
  useLayoutEffect(() => { actions.javContext(javPage) }, [actions, javPage]);
  const layout = useMemo<MediaCardLayout>(() => (props.layout.active === javPage ? props.layout
    : { ...props.layout, active: javPage }), [props.layout, javPage]);
  const opts = { jav: props.jav, seed: props.seed };
  const fetchPage = useCallback(
    (offset: number, signal: AbortSignal, options?: Parameters<typeof fetchItems>[6]) =>
      fetchItems(kind, name, filters, opts, offset, signal, options),
    // 续页的口径就是第一页那一份：代次随键变，键里已经带着筛选、种子与 JAV 开关。
    [itemsRevision]);

  /* ── 照片墙 ── */
  const [seeds, setSeeds] = useState<Record<number, string>>({});
  const wallSet = mediaNow.set;
  const wallSeed = seeds[wallSet] || '';
  const wall = useInfiniteQuery({ ...photosOptions(kind, name, wallSet, wallSeed), enabled: view === 'photos' });
  const wallPage = wall.data?.pages[0];
  const wallRevision = useRevision(JSON.stringify(entityPhotosKey(kind, name, wallSet, wallSeed)));
  const photos = useMemo<EntityPhotos | null>(() => {
    const pages = wall.data?.pages;
    if (!pages?.[0] || pages[0].error) return null;
    const first = pages[0];
    return {
      revision: wallRevision,
      codeSets: first.id ? [] : codeSetsOf(first),
      items: pages.flatMap((page) => page.items || []),
      total: first.total || 0,
      hasMore: !!pages[pages.length - 1]?.has_more,
    };
  }, [wall.data, wallRevision]);
  const wallRefetch = wall.refetch;
  const fetchNextPhotos = wall.fetchNextPage;

  /* ── 新作 ── */
  const feed = useQuery({ ...feedNewOptions(entityId, helpers.feedRowHtml), enabled: !!entityId });
  const feedRevision = useRef(props.feedRevision);
  useEffect(() => {
    if (feedRevision.current === props.feedRevision) return;
    feedRevision.current = props.feedRevision;
    void queryClient.invalidateQueries({ queryKey: feedNewKey(entityId) });
  }, [entityId, props.feedRevision]);

  /* 换了视图、换了一批内容之后吸顶要重算一次。 */
  const hasItems = !!items.data;
  useEffect(() => { actions.painted(view, photoWall) },
    [actions, view, photoWall, itemsRevision, photos?.revision, hasItems, online.key]);

  /* ── 写操作 ── */
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false }, []);
  const refreshEntity = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: ['entity-route'] });
    await queryClient.invalidateQueries({ queryKey: entityKey(kind, name) });
    await queryClient.invalidateQueries({ queryKey: ['entity-items', kind, name] });
  }, [kind, name]);
  const alias = useMutation({
    mutationFn: (payload: { alias: string; remove?: boolean }) => api('/api/entity-alias',
      { method: 'POST', body: JSON.stringify({ kind, name: entity.canonical_name, ...payload }) }) as Promise<{ added?: boolean; alias?: string }>,
    onSuccess: () => refreshEntity(),
  });
  const rename = useMutation({
    mutationFn: ({ from, to }: { from: string; to: string }) => api('/api/entity-name',
      { method: 'POST', body: JSON.stringify({ kind, name: from, canonical: to }) }) as Promise<{
        changed?: boolean; canonical_name?: string; previous_name?: string }>,
    onSuccess: () => refreshEntity(),
  });
  const follow = useMutation({
    mutationFn: (on: boolean) => api('/api/feeds/source',
      { method: 'POST', body: JSON.stringify({ action: 'follow', entity_id: entityId, enabled: on }) }),
    onSuccess: (_data, on) => queryClient.setQueryData<EntityPageData>(entityKey(kind, name),
      (current) => (current ? { ...current, feed: { ...current.feed, following: on } } : current)),
  });

  const bindFollow = useMutation({
    mutationFn: (key: string) => api('/api/follow/creator',
      { method: 'POST', body: JSON.stringify({ entity_id: entityId, key }) }),
  });

  const heroActions = useMemo<EntityHeroActions>(() => ({
    openEntity: actions.openEntity,
    /* 换统称要重写整条实体的扁平投影，先把代价说清再问。确认键、标题和成功回执共用「更改统称」这一个
       动词；写入失败时弹层留在原地，原因写在正文下方等重试。地址换成新名字，由壳重进这一页。 */
    chooseName: (chosen) => {
      const current = entity.canonical_name;
      if (chosen === current) return;
      void confirmModal({
        title: '更改统称',
        body: `「${chosen}」将成为这份档案显示的名称，「${current}」留作别名。作品上的署名、搜索和标签都会跟着改写。`,
        confirmLabel: '更改统称',
        onConfirm: () => rename.mutateAsync({ from: current, to: chosen }),
      }).then(({ confirmed, result }) => {
        const done = result as { changed?: boolean; canonical_name?: string; previous_name?: string } | undefined;
        if (!confirmed || !done?.changed) return;
        helpers.receipt(`已把统称更改为 ${done.canonical_name}`, { undo: async () => {
          await rename.mutateAsync({ from: done.canonical_name || '', to: done.previous_name || '' });
          actions.openEntity(kind, done.previous_name || '');
        } });
        actions.openEntity(kind, done.canonical_name || '');
      });
    },
    addAlias: () => { void helpers.aliasForm(entity.user_aliases || [], (payload) => alias.mutateAsync(payload)) },
    follow: async (on) => {
      try {
        await follow.mutateAsync(on);
      } catch (error) {
        helpers.failure(on ? '订阅新作' : '取消订阅新作', error);
        throw error;
      }
    },
    /* 「订阅新作」打开时服务端当场在后台拉一轮：等那一轮跑完再把新作那一行重取一次。等的上限是
       那一轮自己的时长量级，页面换走了就不再等。 */
    refreshFeedAfterCheck: () => {
      void (async () => {
        for (let tries = 0; tries < 40 && alive.current; tries += 1) {
          await new Promise((resolve) => { setTimeout(resolve, 3000) });
          const job = await (api('/api/feeds/check') as Promise<{ status?: string }>).catch(() => null);
          if (!job || job.status !== 'running') break;
        }
        if (alive.current) await queryClient.invalidateQueries({ queryKey: feedNewKey(entityId) });
      })();
    },
    feedAction: postFeedAction,
    /* 圆框角上那个加号：头像索引在服务端已经换过。资料与作品列表一起重取（作品卡署名里也是
       这张脸），回来后资料卡与作品网格都按新数据重建；壳缓存着的顶部三条也一并作废。 */
    avatarPicked: () => {
      actions.avatarChanged();
      void refreshEntity().then(() => {
        if (alive.current) setAvatarEpoch((epoch) => epoch + 1);
      });
    },
    /* 资料卡那句「N 项更新」：这位名下的就在这一页的在线视图里，不离开；别的作者键才去关注页。 */
    openFollowAuthor: (key) => {
      if (key && key === followKey) actions.route(filters, { media: 'online', set: 0 });
      else actions.openFollowAuthor(key);
    },
    /* 认过之后那组来源的更新都记到这位名下：资料重取，读数与提示跟着换。 */
    confirmFollowAuthor: async (key, author) => {
      try {
        await bindFollow.mutateAsync(key);
      } catch (error) {
        helpers.failure('绑定关注作者', error);
        throw error;
      }
      helpers.receipt(`已把关注里的 ${author} 记到 ${entity.canonical_name} 名下`);
      await refreshEntity();
    },
  }), [actions, alias, bindFollow, entity, entityId, filters, follow, followKey, helpers, kind, name, refreshEntity, rename]);
  const heroHelpers = useMemo<EntityHeroHelpers>(() => ({
    portraitImg: () => helpers.portraitImg(kind, entity),
    wireScroller: helpers.wireDrag,
    wireFeedRow: helpers.wireFeedRow,
    receipt: helpers.receipt,
  }), [entity, helpers, kind]);

  /* ── 浮层 ── */
  const filterActions = useMemo<EntityFilterActions>(() => ({
    /* 按下去那一下视图键就换过去，玻璃跟着滑，不等换视图的活干完。 */
    setView: (next) => {
      if (next === view && !mediaNow.set) return;
      setRosterView(next === 'people' ? 'people' : 'videos');
      actions.route(filters, next === 'photos' || next === 'online' ? { media: next, set: 0 } : EMPTY_MEDIA);
    },
    setState: (value) => actions.route({ ...filters, state: value }, EMPTY_MEDIA),
    toggleTag: actions.toggleTag,
    clearFilter: actions.clearFilter,
    clearAll: actions.clearAll,
    setSort: actions.setSort,
    /* 换一批：照片换一粒种子把这一屏重排一遍，整组或单个图集都是，新墙取回来再换上，等的这一下
       键上转圈；作品那一粒是全站的种子，交给壳。 */
    reshuffle: async () => {
      if (view !== 'photos') {
        const seed = actions.reshuffleVideos();
        const next = itemsOptions({ kind, name, filters: { ...filters, sort: 'seed' }, jav: props.jav, seed,
          revision: props.revision });
        await queryClient.fetchQuery(next).catch(() => null);
        return;
      }
      const seed = newSeed();
      const next = await queryClient.fetchInfiniteQuery(photosOptions(kind, name, wallSet, seed)).catch(() => null);
      if (!alive.current || !next?.pages[0] || next.pages[0].error) return;
      setSeeds((current) => ({ ...current, [wallSet]: seed }));
    },
    setJavLayout: actions.setJavLayout,
    setPhotoLayout: actions.setPhotoLayout,
    photoBack: () => actions.route(filters, { media: 'photos', set: 0 }),
    ...online.actions,
  }), [actions, filters, kind, mediaNow.set, name, online.actions, props.jav, props.revision, view, wallSet]);
  const filterHelpers = useMemo<EntityFilterHelpers>(() => ({
    wireDrag: helpers.wireDrag,
    wireScroller: helpers.wireScroller,
    sourceToolsHtml: helpers.sourceToolsHtml,
    wireSourceTools: (root) => helpers.wireSourceTools(root, () => { void wallRefetch() }),
  }), [helpers, wallRefetch]);

  /* 标签按资料里的顺序摆，选中的不往前挪：刚点的那枚就在指针底下，这一下把它抽走反倒是替人决定
     现在该看哪儿。数的是这个人／厂牌名下带这个标签的视频。 */
  const tags: EntityFilterTag[] = (entity.tags || []).map((tag) => ({
    k: tag.k, label: tagLabel(tag.k), n: tag.n, selected: tagPressed(filters.tag, tag.k) }));
  /* 艺人名册、视频、照片与在线是这一页的互斥视图，共用一组圆键；只有一类东西时不出这一组。 */
  const viewKinds = [roster.length, !onlineOnly, photoCount, followKey].filter(Boolean).length;
  const views: EntityViewKeys | null = viewKinds > 1 ? {
    label: roster.length ? '页面视图' : '媒体类型',
    people: roster.length ? { label: kind === 'studio' ? '厂牌' : '艺人', count: roster.length,
      icon: kind === 'studio' ? 'clapperboard' : 'user-round' } : null,
    videos: onlineOnly ? null : { count: entity.asset_count || 0 },
    photos: photoCount ? { count: photoCount } : null,
    online: followKey ? { count: entity.follow?.n || 0 } : null,
  } : null;
  const video: EntityVideoHead = {
    sorts: helpers.sortKeys(filters.sort || 'new', filters.dir || '', javPage),
    jav: javPage ? { layout: props.javLayout, options: props.javLayouts } : null,
  };
  /* 照片这一排不给排序键：数据库里图片只有文件名、体积和来源三样。在一个图集里多两枚源文件键。 */
  const inSet = !!wallPage?.id && !wallPage.error;
  const photo: EntityPhotoHead | null = wallPage && !wallPage.error ? {
    back: inSet, shuffle: !!wallPage.total, layout: props.photoLayout, layouts: props.photoLayouts,
    setId: inSet ? Number(wallPage.id) : 0,
  } : null;
  /* 名下一部作品都没有（或筛完一部不剩）时正文是空态，下排那排排序键与换一批没有东西可排，不出。 */
  const combo = helpers.comboItems(filters);
  const filtered = combo.length > 0 || !!filters.state;
  const worksEmpty = view === 'videos' && !!items.data && !(items.data.items || []).length;
  const showAll = () => actions.route(
    // 键名与壳的 `clearAll` 同一组，外加观看状态。
    { ...filters, state: '', tag: '', creator: '', studio: '', owner: '' }, EMPTY_MEDIA);
  let readout = '';
  let busy = false;
  if (view === 'people') readout = `${kind === 'studio' ? '厂牌' : '艺人'} · ${roster.length.toLocaleString()}`;
  else if (view === 'online') {
    busy = online.busy;
    readout = `在线 · ${(online.total ?? entity.follow?.n ?? 0).toLocaleString()} 项更新`;
  }
  else if (view === 'videos') {
    busy = !items.data;
    /* 选了哪几枚标签由上面那条交集条一枚一枚列着，读数只报个数：标签名再列一遍，几枚长标签
       就能把这一行撑出页面。 */
    const picked = tagList(filters.tag).length;
    readout = `视频 · ${(items.data?.work_total ?? items.data?.total ?? 0).toLocaleString()}${picked ? ` · 已选 ${picked.toLocaleString()} 个标签` : ''}`;
  } else if (wallPage && !wallPage.error) {
    readout = inSet ? `${wallPage.title} · ${(wallPage.total || 0).toLocaleString()} 张`
      : photoReadout(wallPage, codeSetsOf(wallPage).length);
  } else busy = wall.isPending;

  /* ── 正文 ── */
  const bodyActions = useMemo<EntityBodyActions>(() => ({
    ...props.card.actions,
    /* 本地图片的下一页；取不到就抛错，键下出重试。 */
    loadMorePhotos: async () => {
      const result = await fetchNextPhotos();
      if (result.isError) throw result.error;
    },
  }), [fetchNextPhotos, props.card.actions]);
  const rosterProps = useMemo<EntityRoster | null>(() => (roster.length
    ? { kind: kind === 'studio' ? 'studios' : 'performers', people: roster, layout: props.peopleLayout } : null),
  [kind, props.peopleLayout, roster]);

  return (
    <>
      <EntityHeroPage key={avatarEpoch} kind={kind} name={name} entity={entity} feedNew={feed.data ?? null}
        feedHost={entityId ? hosts.feed : null} jav={javPage} actions={heroActions} helpers={heroHelpers} />
      {createPortal(
        <EntityFilterPage kind={kind} name={name} view={view} views={views} state={filters.state || ''}
          states={props.states} tags={tags} combo={combo} readout={readout} busy={busy}
          video={worksEmpty ? null : video} photo={photo} online={view === 'online' ? online.head : null} actions={filterActions}
          helpers={filterHelpers} />,
        hosts.filter)}
      {createPortal(
        view === 'online'
          ? <EntityOnline key={online.key} view={online.view} media={online.media} result={online.result}
            data={online.data} visible={online.visible} context={online.context} photoSize={props.photoSize}
            photoLayout={props.photoLayout} imagesOnly={props.followImagesOnly} helpers={props.follow.helpers}
            actions={props.follow.actions} canLoadMore={props.canLoadMore} />
          : view === 'videos' && items.isError
          ? <RetryNote message={errorMessage(items.error)} onRetry={() => { void items.refetch() }} />
          : worksEmpty ? <WorksEmpty filtered={filtered} onShowAll={showAll} />
          : (
            <EntityBodyPage kind={kind} name={name} view={view} roster={rosterProps} items={items.data ?? null}
              revision={itemsRevision} fetchPage={fetchPage}
              listScope={JSON.stringify([kind, name, filters, props.jav, filters.sort === 'seed' ? props.seed : ''])}
              photos={photos} photoSize={props.photoSize}
              photoLayout={props.photoLayout} helpers={props.card.helpers} actions={bodyActions} layout={layout}
              selectMode={props.selectMode} selected={props.selected} seekSeconds={props.seekSeconds}
              wireDrag={props.wireDrag} skeletonHtml={props.skeletonHtml} groupCollapse={props.groupCollapse}
              canLoadMore={props.canLoadMore} />
          ),
        hosts.body)}
    </>
  );
}

/** 作品区一部也没有。筛完一部不剩时给一条回到全部视频的路：观看状态与交集条上的筛选一起撤掉。 */
function WorksEmpty({ filtered, onShowAll }: { filtered: boolean; onShowAll(): void }) {
  return filtered ? (
    <EmptyState icon={spriteGlyph('search')} title="没有符合条件的视频"
      actions={<Button variant="secondary" onClick={onShowAll}>查看全部视频</Button>}>
      清除筛选后查看名下全部视频。
    </EmptyState>
  ) : (
    <EmptyState icon={spriteGlyph('film')} title="还没有视频">
      署上这个名字的视频入库后会显示在这里。
    </EmptyState>
  );
}
