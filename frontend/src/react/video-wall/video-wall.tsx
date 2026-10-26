import { memo, useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { Radio } from 'react-aria-components';
import { useQuery } from '@tanstack/react-query';
import { motion, useReducedMotion } from 'motion/react';
import { cardArtwork, coverAnchor } from '@peach/card-art';
import { fmtDur, requestErrorMessage } from '@peach/legacy/core';
import { spinnerHtml } from '@peach/legacy/ui';
import { isOverlayPath, peachHistory } from '@peach/history';
import { Button } from '@/components/base/buttons/button';
import type { MediaCardActions, MediaCardHelpers, MediaItem } from '../catalog-grid/types';
import { Note } from '../components/note';
import { ArtSlot } from '../components/art-slot';
import { SEGMENT, SEGMENTED_TRACK, SegmentedRadioGroup } from '../components/segmented';
import { busyProps } from '../settings/busy-props';
import { useWallCamera } from './use-wall-camera';
import { expandedWallTiles, finiteWallTile, visibleWallTiles, wallTileAt, WallExploration, type WallCamera, type WallTile } from './wall-layout';
import { WALL_REFLOW_SPRING } from './wall-motion';
import { chooseItem, fetchQueue, nextPart, partLabel, queueKey, type QueueItem, type QueueRef } from '../item-detail/item-detail';
import { WallControls } from './wall-controls';
import { WallQueue } from './wall-queue';
import { Shuffle } from '../entity-filter/entity-filter-page';
import { exitWall, unfilteredWallAddress } from './wall-route';
import './video-wall.css';

interface VideoWallProps {
  sessionKey: string;
  items: readonly MediaItem[];
  finite: boolean;
  hasMore: boolean;
  busy: boolean;
  loading?: boolean;
  error?: string;
  retry?(): void;
  emptyHtml?: string;
  loadMore(): Promise<void>;
  actions: MediaCardActions;
  helpers: MediaCardHelpers;
  javImage: string;
}

interface WallSession { camera: WallCamera; selectedId: number | null; selectedKey?: string; sourceId?: number | null; image: string; pool: string; exploration: WallExploration }
// 页面刷新代次不属于集合身份。只保存界面位置，不保存 Query 中的作品副本。
const wallSessions = new Map<string, WallSession>();

const WallArtwork = memo(function WallArtwork({ item, image, big = false, ratio = 1 }: { item: MediaItem; image: string; big?: boolean; ratio?: number }) {
  const source = !item.is_jav && image === 'thumbnail' && item.has_thumb ? { ...item, has_cover: false } : item;
  const artwork = cardArtwork(source, big || ratio >= 1 ? 'small' : 'big', true, image);
  const pic = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    pic.current?.querySelectorAll<HTMLImageElement>('img.cover').forEach(img => {
      if (img.complete && img.naturalWidth) coverAnchor(img);
    });
  }, [ratio, artwork.html]);
  return <span ref={pic} data-media-pic="" style={{ '--card-ratio':String(ratio) } as CSSProperties}>
    <ArtSlot artwork={artwork} relayout={node => node.querySelectorAll<HTMLImageElement>('img.cover').forEach(img => {
      if (img.complete && img.naturalWidth) coverAnchor(img);
    })} />
  </span>;
});

/** 单一播放宿主常驻在墙内，视口裁剪只影响海报磁贴。 */
export function VideoWall({ items, finite, hasMore, busy, loading = false, error: queryError = '', retry, emptyHtml = '', loadMore, actions, helpers, javImage, sessionKey }: VideoWallProps) {
  const pool = String(items[0]?.id);
  const [session] = useState(() => {
    const saved = wallSessions.get(sessionKey);
    return saved && (loading || finite || saved.pool === pool) ? saved : undefined;
  });
  const [exploration] = useState(() => session?.exploration || new WallExploration());
  const moreFlight = useRef(false);
  const [freshFrom, setFreshFrom] = useState<number | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const playerFrame = useRef<HTMLDivElement>(null);
  const codeTitle = useRef<HTMLElement>(null);
  const releasePlayer = useRef<((continuePlaying?: boolean) => void) | null>(null);
  const controller = useRef<AbortController | null>(null);
  const [selected, setSelected] = useState<MediaItem | null>(() => items.find(item => item.id === session?.selectedId) || null);
  const [selectedKey, setSelectedKey] = useState(session?.selectedKey || '');
  const [sourceId, setSourceId] = useState<number | null>(session?.sourceId || null);
  const [queueOpen, setQueueOpen] = useState(false);
  const groupRef: QueueRef | null = selected?.edition_group
    ? { kind:'editions', seedId:selected.edition_group.seed_id }
    : selected?.part_group ? { kind:'parts', seedId:selected.part_group.seed_id } : null;
  const groupQuery = useQuery({ queryKey:queueKey(groupRef || { kind:'parts', seedId:0 }), enabled:!!groupRef,
    queryFn:({ signal }) => fetchQueue(groupRef!, { tagLabel:helpers.tagLabel,
      mixRelated:async () => [] }, signal),
  });
  const group = groupRef ? groupQuery.data : undefined;
  const activeItem = group?.items.find(item => item.id === chooseItem(group, sourceId || selected?.id || null)) || selected;
  const camera = useWallCamera(items.length, finite && !!items.length, session?.camera || { x: 0, y: 0, scale: innerWidth < 640 ? .68 : 1 }, !!selected);
  const reducedMotion = useReducedMotion();
  const [entranceDone, setEntranceDone] = useState(!!session);
  useEffect(() => {
    if (!items.length || entranceDone || reducedMotion) return;
    const timer = setTimeout(() => setEntranceDone(true), 1100);
    return () => clearTimeout(timer);
  }, [pool, entranceDone, reducedMotion]);
  const hoverable = matchMedia('(hover:hover) and (pointer:fine)').matches;
  const [playing, setPlaying] = useState<number | null>(null);
  const [pendingPlayback, setPendingPlayback] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const [moreError, setMoreError] = useState('');
  const [laterBusy, setLaterBusy] = useState(false);
  const [later, setLater] = useState<{ id: number; on: boolean } | null>(null);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [portal] = useState(() => {
    const node = document.createElement('div'); node.className = 'peach-react'; return node;
  });
  useLayoutEffect(() => {
    document.body.append(portal);
    camera.viewport.current?.focus({ preventScroll: true });
  }, [portal]);
  useEffect(() => () => portal.remove(), [portal]);
  const [covered, setCovered] = useState(() => isOverlayPath(peachHistory.navigation.location.pathname));
  const [image, setImage] = useState(session?.image || javImage);
  const tileFocus = useRef<{ key: string; id: number } | null>(null);
  const previousPool = useRef(pool);
  const hasItems = useRef(!!items.length);
  hasItems.current = !!items.length;
  const savedView = useRef({ selectedId: selected?.id || null, selectedKey, sourceId, image, pool });
  savedView.current = { selectedId: selected?.id || null, selectedKey, sourceId, image, pool };
  const saveView = () => {
    if (!hasItems.current) return;
    wallSessions.delete(sessionKey);
    wallSessions.set(sessionKey, { ...savedView.current, camera: camera.snapshot(), exploration });
    if (wallSessions.size > 8) wallSessions.delete(wallSessions.keys().next().value!);
  };
  useLayoutEffect(saveView);
  const visible = visibleWallTiles(loading ? 12 : finite ? items.length : items.length || hasMore ? 12 : 0,
    finite && !loading, camera.view.camera, camera.view.size);
  const tiles = loading ? visible.map(tile => ({ ...tile, index:-1 })) : finite ? visible : exploration.assign(visible, items.length, hasMore);
  const activeTile = selected && (finite ? wallTileAt(items.length, true, selectedKey) : exploration.tile(selectedKey, items.length));
  const expanded = activeTile ? expandedWallTiles(items.length, finite, activeTile) : new Map();
  if (activeTile && !tiles.some(tile => tile.key === activeTile.key)) tiles.push(activeTile);
  const stopPlayer = (continuePlaying = false) => {
    controller.current?.abort(); controller.current = null;
    releasePlayer.current?.(continuePlaying); releasePlayer.current = null;
    setPlaying(null); setStarting(false);
  };
  useLayoutEffect(() => {
    if (loading) return;
    if (!items.length) {
      if (previousPool.current === pool) return;
      previousPool.current = pool;
      controller.current?.abort(); releasePlayer.current?.(true); releasePlayer.current = null;
      setPlaying(null); setStarting(false); setSelected(null); setSelectedKey(''); setError('');
      return;
    }
    if (previousPool.current === 'undefined') {
      previousPool.current = pool;
      if (session && (finite || session.pool === pool)) setSelected(items.find(item => item.id === session.selectedId) || null);
      else if (session) camera.reset();
      return;
    }
    if (finite || previousPool.current === pool) return;
    previousPool.current = pool;
    controller.current?.abort(); releasePlayer.current?.(true); releasePlayer.current = null;
    setPlaying(null); setStarting(false); setSelected(null); setSelectedKey(''); setError(''); camera.reset();
  }, [finite, pool, loading]);
  useEffect(() => peachHistory.listen(navigation => {
    const overlay = isOverlayPath(navigation.location.pathname);
    if (overlay) {
      controller.current?.abort(); releasePlayer.current?.(true); releasePlayer.current = null;
      setPlaying(null); setStarting(false);
    }
    setCovered(overlay);
  }), []);
  useEffect(() => () => {
    controller.current?.abort(); releasePlayer.current?.(true);
    saveView();
  }, []);
  useEffect(() => {
    if (covered) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = previous };
  }, [covered]);
  const close = (continuePlaying = true) => {
    stopPlayer(continuePlaying); setPendingPlayback(null); setSelected(null); setSelectedKey(''); setSourceId(null); setQueueOpen(false); setError('');
    const focus = tileFocus.current;
    if (focus) requestAnimationFrame(() => {
      const tile = root.current?.querySelector<HTMLButtonElement>(`[data-wall-tile="${focus.key}"]`);
      tile?.focus({ preventScroll: true });
    });
  };
  const choose = (item: MediaItem, tile: WallTile, animate = true) => {
    if (selected?.id !== item.id) stopPlayer(false);
    tileFocus.current = { key: tile.key, id: item.id };
    setError(''); setPendingPlayback(null); setSelected(item); setSelectedKey(tile.key); setSourceId(null); setQueueOpen(false); setToolsOpen(false);
    camera.reveal(expandedWallTiles(items.length, finite, tile).get(tile.key) || tile, animate, true);
    if (!animate) requestAnimationFrame(() => root.current?.querySelector<HTMLButtonElement>('[data-wall-play]')?.focus({ preventScroll: true }));
  };
  const details = () => {
    if (!activeItem || !root.current) return;
    saveView();
    stopPlayer(true);
    actions.wallDetails?.(activeItem, root.current.querySelector<HTMLElement>('[data-wall-focus]') || root.current);
  };
  const play = async (target: MediaItem | null = activeItem) => {
    if (!target || !playerFrame.current || !actions.wallPlayer) return;
    stopPlayer(false); setError(''); setStarting(true);
    const run = new AbortController(); controller.current = run;
    const id = target.id;
    try {
      const release = await actions.wallPlayer(target, playerFrame.current, run.signal);
      if (run.signal.aborted || controller.current !== run) { release(false); return }
      releasePlayer.current = release; setPlaying(id);
    } catch (failure) {
      if (!run.signal.aborted) setError(requestErrorMessage(failure));
    } finally {
      if (controller.current === run) setStarting(false);
    }
  };
  const onEnded = () => {
    const next = nextPart(group || null, playing || 0);
    if (next) { setSourceId(next.id); void play(next) }
  };
  useEffect(() => {
    if (pendingPlayback == null || selected?.id !== pendingPlayback || covered) return;
    setPendingPlayback(null); void play(selected);
  }, [pendingPlayback, selected?.id, covered]);
  const tracks = group?.items || items;
  const trackIndex = tracks.findIndex(item => item.id === activeItem?.id);
  const switchTrack = (item: QueueItem) => {
    setQueueOpen(false);
    if (item.id === activeItem?.id) {
      if (!playing) void play(item);
      else void playerFrame.current?.querySelector('video')?.play().catch(failure => setError(requestErrorMessage(failure)));
      return;
    }
    if (group) { setSourceId(item.id); void play(item); return }
    const view = camera.snapshot();
    const index = items.findIndex(row => row.id === item.id);
    const point = {
      x:(camera.view.size.width / 2 - view.x) / view.scale, y:(camera.view.size.height / 2 - view.y) / view.scale,
    };
    const tile = finite ? finiteWallTile(items.length, index) : exploration.nearest(items.length, index, point);
    if (tile) { choose(item, tile); setPendingPlayback(item.id) }
  };
  const more = async () => {
    if (busy || moreFlight.current) return;
    moreFlight.current = true;
    setMoreError('');
    try { await loadMore() } catch (failure) { setMoreError(requestErrorMessage(failure)); setFreshFrom(null) }
    finally { moreFlight.current = false }
  };
  const pendingTiles = tiles.some(tile => tile.index < 0);
  useEffect(() => {
    if (loading || finite || covered || selected || busy || moreError || freshFrom !== null || !hasMore) return;
    if (pendingTiles || items.length - exploration.size < 24) void more();
  }, [loading, finite, covered, selected, busy, moreError, hasMore, pendingTiles, items.length, exploration.size, freshFrom]);
  useEffect(() => {
    if (freshFrom === null || busy || moreError) return;
    if (items.length <= freshFrom && hasMore) { void more(); return }
    exploration.prefer(freshFrom);
    const next = exploration.fresh(), view = camera.snapshot();
    close(); camera.pan(-next.x * view.scale - view.x, -next.y * view.scale - view.y);
    setFreshFrom(null);
  }, [freshFrom, items.length, busy, hasMore, moreError]);
  const fresh = async () => {
    if (busy || moreFlight.current) return;
    setFreshFrom(items.length);
    if (hasMore) await more();
  };
  const addLater = async () => {
    if (!activeItem || laterBusy) return;
    setLaterBusy(true);
    try { await actions.watchLater(activeItem, on => setLater({ id: activeItem.id, on })) }
    finally { setLaterBusy(false) }
  };
  const label = (item: MediaItem) => helpers.displayName(item, String(item.name || item.code || '未命名视频'));
  const current = activeItem || selected;
  useLayoutEffect(() => {
    const node = codeTitle.current;
    if (!node || !current?.code) return;
    let live = true, lastWidth = node.clientWidth;
    const fit = () => {
      if (!live) return;
      node.style.removeProperty('--wall-code-fit');
      const width = node.getBoundingClientRect().width;
      if (!width) return;
      const range = document.createRange(); range.selectNodeContents(node);
      const needed = range.getBoundingClientRect().width;
      if (needed > width) {
        const base = parseFloat(getComputedStyle(node).fontSize);
        node.style.setProperty('--wall-code-fit', `${Math.floor(base * width / needed * 100) / 100}px`);
      }
      lastWidth = node.clientWidth;
    };
    const observer = new ResizeObserver(entries => {
      if (Math.round(entries[0]!.contentRect.width) !== lastWidth) fit();
    });
    observer.observe(node); fit();
    void document.fonts.ready.then(fit);
    document.fonts.addEventListener('loadingdone', fit);
    return () => { live = false; observer.disconnect(); document.fonts.removeEventListener('loadingdone', fit) };
  }, [current?.code, selectedKey, playing, starting, covered]);
  const focusContent = selected && current ? <>
    <div data-wall-focus-backdrop="" aria-hidden="true"><WallArtwork item={selected} image={image} big /></div>
    <div data-wall-scene=""><WallArtwork item={selected} image={image} big /></div>
    <div ref={playerFrame} data-wall-player="" hidden={!playing && !starting} />
    <div data-wall-expanded-shade="" aria-hidden="true" />
    <span data-wall-number="">{playing ? '正在播放 · ' : ''}{String((activeTile?.index || 0) + 1).padStart(2, '0')}</span>
    <div data-wall-focus-head=""><b ref={codeTitle} data-wall-code={current.code ? '' : undefined}>{current.code || label(current)}</b>
      {current.code ? <strong>{label(current).startsWith(current.code) ? label(current).slice(current.code.length).trim() : label(current)}</strong> : null}
      <span>{current.performers?.join(' · ') || current.creator || '未归属'}</span>
      {group ? <small>{[(current as QueueItem).edition_label, (current as QueueItem).part_label ? partLabel((current as QueueItem).part_label) : ''].filter(Boolean).join(' · ')}</small> : null}
    </div>
    <button type="button" data-wall-focus-close="" aria-label="收起作品" title="收起" onClick={() => close()}><svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-x" /></svg></button>
    {error || (groupRef && groupQuery.error && !group) ? <div data-wall-play-error=""><Note tone="error">{error || requestErrorMessage(groupQuery.error)}</Note>
      {groupQuery.error && !group ? <Button size="small" variant="ghost" onClick={() => void groupQuery.refetch()}>重试</Button> : null}</div> : null}
    <WallControls item={current} playing={playing} frame={playerFrame} starting={starting} pendingGroup={!!groupRef && groupQuery.isPending}
      grouped={!!groupRef} queueOpen={queueOpen} queueReady={!!tracks.length && (!groupRef || !!group)}
      hasPrevious={trackIndex > 0} hasNext={trackIndex >= 0 && trackIndex < tracks.length - 1}
      later={later?.id === current.id ? later.on : !!current.watch_later} laterBusy={laterBusy}
      play={() => play()} previous={() => switchTrack(tracks[trackIndex - 1]!)} next={() => switchTrack(tracks[trackIndex + 1]!)}
      queue={() => setQueueOpen(!queueOpen)} watchLater={() => void addLater()} details={details} ended={onEnded} error={setError} />
    {queueOpen ? <WallQueue items={tracks} activeId={current.id} kind={group?.kind || 'wall'} label={label} choose={switchTrack} close={() => setQueueOpen(false)} /> : null}
  </> : null;
  const wall = <div ref={root} data-video-wall="" data-fullscreen="" data-focused={!!selected || undefined}
    data-refreshing={freshFrom !== null || undefined}
    hidden={covered} onKeyDown={event => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.preventDefault(); event.stopPropagation();
      if (queueOpen) setQueueOpen(false); else if (selected) close(); else if (toolsOpen) setToolsOpen(false); else exitWall();
    }}>
    <button type="button" data-wall-back="" aria-label="退出播放墙" title="返回" onClick={exitWall}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-chevron-left" /></svg>
    </button>
    {finite ? <aside data-wall-filter-notice="" className="ui-project-banner" role="status">
      <div><svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-info" /></svg><p>当前仅显示筛选结果</p></div>
      <Button size="small" variant="ghost" onClick={() => peachHistory.push(unfilteredWallAddress())}>清除筛选</Button>
    </aside> : null}
    {toolsOpen ? <div data-wall-toolbar="" aria-label="播放墙工具">
      <form onSubmit={event => {
        event.preventDefault();
        const params = new URLSearchParams(location.search);
        const text = String(new FormData(event.currentTarget).get('q') || '').trim();
        if (text) params.set('q', text); else params.delete('q');
        saveView(); peachHistory.push(`/wall?${params}`); setToolsOpen(false);
      }}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-search" /></svg>
        <input type="search" name="q" aria-label="搜索播放墙" placeholder="搜索作品" defaultValue={new URLSearchParams(location.search).get('q') || ''} />
      </form>
      <SegmentedRadioGroup aria-label="播放墙图片" value={image} onChange={setImage} className={SEGMENTED_TRACK}>
        <Radio value="cover" className={SEGMENT}>封面</Radio><Radio value="thumbnail" className={SEGMENT}>缩略图</Radio>
      </SegmentedRadioGroup>
      <div data-wall-tools="">
        <Button size="small" variant="ghost" aria-label="缩小播放墙" onClick={() => camera.zoom(1 / 1.2)}>缩小</Button>
        <Button size="small" variant="ghost" aria-label="放大播放墙" onClick={() => camera.zoom(1.2)}>放大</Button>
        <Button size="small" variant="ghost" onClick={camera.reset}>回到起点</Button>
      </div>
      <span data-wall-hint="">拖动浏览 · Shift + 滚轮横移</span>
    </div> : null}
    <div ref={camera.viewport} data-wall-viewport="" tabIndex={0} role="region" aria-label="播放墙，拖动或方向键浏览"
      aria-busy={loading || freshFrom !== null || (busy && pendingTiles) || undefined} data-skeleton={loading ? 'wall' : undefined}
      {...(loading || freshFrom !== null ? {} : camera.handlers)} onClick={event => {
        if (selected && event.target instanceof Element && !event.target.closest('[data-wall-tile]')) {
          if (queueOpen) setQueueOpen(false); else close();
        }
      }} onKeyDown={event => {
        if (freshFrom !== null) return;
        if (event.target instanceof Element && event.target.closest('button,input,[role="slider"],[data-wall-player],[data-wall-queue]')) return;
        const directions: Record<string, [number, number]> = { ArrowLeft: [200,0], ArrowRight: [-200,0], ArrowUp: [0,200], ArrowDown: [0,-200] };
        const delta = directions[event.key];
        if (!delta || event.ctrlKey || event.metaKey || event.altKey) return;
        event.preventDefault(); camera.pan(...delta);
      }}>
      <div ref={camera.world} data-wall-world="">
        {tiles.map(tile => {
          if (tile.index < 0) return <span key={tile.key} data-wall-skeleton="" aria-hidden="true"
            style={{ '--wall-left':`${tile.x}px`, '--wall-top':`${tile.y}px`, '--wall-width':`${tile.width}px`, '--wall-height':`${tile.height}px` } as CSSProperties} />;
          const item = items[tile.index]!;
          const focused = !!selected && tile.key === selectedKey;
          const rect = expanded.get(tile.key) || tile;
          const landing = !entranceDone && !focused;
          const delay = landing ? Math.min(.34, (Math.max(0, tile.x + camera.view.camera.x / camera.view.camera.scale)
            + Math.max(0, tile.y + camera.view.camera.y / camera.view.camera.scale)) / 103.36 * .03) : 0;
          return <motion.article key={tile.key} layout data-wall-tile={tile.key} data-wall-id={item.id}
            data-wall-focus={focused ? '' : undefined} data-playing={focused && (playing || starting) ? '' : undefined}
            role={focused ? 'group' : 'button'} tabIndex={focused ? -1 : 0}
            aria-label={focused ? label(item) : `展开 ${label(item)}`} aria-expanded={focused ? undefined : false}
            initial={reducedMotion || session ? false : { opacity:0, transform:landing ? 'translateY(-68.4px) scale(.88)' : 'scale(.94)' }}
            animate={{ opacity:1, transform:'translateY(0px) scale(1)' }}
            whileHover={hoverable && !reducedMotion && !focused && entranceDone
              ? { transform:`translateY(0px) scale(${(rect.width + 12.16) / rect.width},${(rect.height + 12.16) / rect.height})` } : undefined}
            transition={reducedMotion ? { duration:0 } : {
              layout:WALL_REFLOW_SPRING,
              transform:landing ? { type:'spring', stiffness:360, damping:24, delay } : { duration:.3, ease:'easeOut' },
              opacity:{ duration:landing ? .24 : .26, delay },
            }}
            style={{ '--wall-left': `${rect.x}px`, '--wall-top': `${rect.y}px`, '--wall-width': `${rect.width}px`, '--wall-height': `${rect.height}px` } as CSSProperties}
            onFocus={event => {
              if (focused || event.target !== event.currentTarget) return;
              const rect = event.currentTarget.getBoundingClientRect(), box = camera.viewport.current?.getBoundingClientRect();
              if (box && (rect.left < box.left || rect.right > box.right || rect.top < box.top || rect.bottom > box.bottom)) camera.reveal(tile);
            }} onClick={event => { if (!focused) choose(item, tile, event.detail !== 0) }}
            onKeyDown={event => {
              if (focused || !['Enter',' '].includes(event.key)) return;
              event.preventDefault(); choose(item, tile, false);
            }}>
            {focused ? focusContent : <>
              <WallArtwork item={item} image={image} ratio={rect.width / rect.height} />
              <span data-wall-tile-shade="" aria-hidden="true" />
              <span data-wall-number="">{String(tile.index + 1).padStart(2, '0')}</span>
              <span data-wall-copy=""><b>{item.code || label(item)}</b><span>{item.performers?.join(' · ') || item.creator || item.studio || '未归属'}</span></span>
              {item.duration ? <span data-wall-duration="">{fmtDur(item.duration)}</span> : null}
              {Number(item.leave_ratio) > 0 ? <span data-wall-progress="" style={{ '--wall-progress': `${Math.min(100, Number(item.leave_ratio) * 100)}%` } as CSSProperties} aria-label="观看进度" /> : null}
              <motion.span data-wall-dim="" animate={{ opacity:selected ? .65 : 0 }} transition={{ duration:reducedMotion ? 0 : .24 }} />
            </>}
          </motion.article>;
        })}
      </div>
    </div>
    {!loading && queryError ? <div data-wall-error=""><Note tone="error">{queryError}</Note><Button size="small" variant="ghost" onClick={retry}>重试</Button></div> : null}
    {!loading && !queryError && !items.length ? <div data-wall-empty="" dangerouslySetInnerHTML={{ __html:emptyHtml }} /> : null}
    {!finite && (hasMore || items.length > 0) ? <div data-wall-refresh=""><Shuffle busy={busy} onPress={() => void fresh()} /></div> : null}
    {hasMore && finite ? <div data-wall-footer=""><Button size="small" variant="secondary" {...busyProps(busy)} onClick={() => void more()}>
      {busy ? <span dangerouslySetInnerHTML={{ __html:spinnerHtml() }} /> : null}载入更多</Button></div> : null}
    {moreError ? <div data-wall-error=""><Note tone="error">{moreError}</Note><Button size="small" variant="ghost" {...busyProps(busy)} onClick={() => void more()}>重试</Button></div> : null}
    <button type="button" data-wall-tools-button="" aria-label="播放墙工具" title="播放墙工具" aria-expanded={toolsOpen} onClick={() => setToolsOpen(!toolsOpen)}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-settings" /></svg>
    </button>
  </div>;
  return createPortal(wall, portal);
}
