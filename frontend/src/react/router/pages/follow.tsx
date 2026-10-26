/* 关注页与播放列表页的路由元素（ADR-0031）：页面组匹配到 `BROWSE_ROUTES` 的这两条时挂上，按地址打开那一页，
 * 画进 `#stats`。元素自己什么都不画：页面经 `openManagedRoute` 登记进 `#stats`，由 `ManagedSurface` 画。
 *
 * 什么时候打开同索引页（`usePageOpen`）：挂上、领了页面打开序号（后退前进与不认领的跳转）、壳要求按当前地址重开
 * （`@peach/shell` 的 `pageOpens` 加一）各一次；`pageOpens` 还是 0 时壳没开始路由，元素只挂着；那一刻被详情压着
 * （页面组按背景匹配）就不动，关掉详情时这一页没画着才打开。
 *
 * 关注页不按代次挂 key，「回到」与「重新进入」都是同一个元素再开一次，交壳按地址读一遍筛选（`refresh`）：
 * - 关注页还画在 `#stats` 里（资料页压过来时壳只藏起它）就铺开它，就地推筛选、取样种子与一个新的刷新代次：列表
 *   重取、回到顶上，页头、两排与那块玻璃不撤。
 * - 没画着才整页打开：铺骨架、取齐首屏后照离开时记下的位置滚回去（`followScrollY`），没记就回到顶上。刷新代次
 *   没变时首屏命中缓存，列表不重取。
 * 重新进入（已在关注页再点侧栏、检查更新之后去看）由壳决定：重掷取样种子、滚动清零、回到干净的 `/follow`，再
 * 要求重开。关掉关注详情回列表由壳认领写地址、只推筛选，元素不动。深链直接进关注详情时页面组没有背景，不挂这个
 * 元素，列表等关掉详情才打开。后退前进落到压在列表上的关注详情、列表却没画着（中间去过别的页）时，交壳让出
 * `#stats`（`ShellActions.follow.ground`），不画列表。
 *
 * 播放列表页每次打开都整页重开、取最新的那一份。停在这一页时壳要求重读（顶栏「换一批」）就写一个新的刷新代次
 * （`playlistsRevision`），元素推给画着的那一页，页面重取、不重挂。
 *
 * 两个元素卸下时都不收页面：换到管理区、索引页时由它们认领表面收掉；资料页压过来时壳只藏起 `#stats`，回来还要
 * 接着用。 */
import { useContext, useEffect, useRef, useSyncExternalStore } from 'react';

import { managedEntry, openManagedRoute, peachHistory, updateManagedRoute } from '@peach/history';
import { revealSkeleton } from '@peach/legacy/ui';
import { followScrollY, playlistsRevision, subscribeShell } from '@peach/shell';

import { managementSkeletonHtml, paintManagementPlaceholder } from '../../../management-placeholder';
import { ShellActionsContext, useOpenEpoch } from '../router';
import type { ShellActions } from '../shell-actions';
import { usePageOpen } from './index-entity';

const statsHost = () => document.getElementById('stats');
const readPlaylistsRevision = () => playlistsRevision;

/* 取齐首屏时骨架不一次清空：骨架抬成一层淡出，新宿主同时从模糊里清晰起来，同目录网格。宿主放进去之后页面
   当场画进去，仍在同一个任务里，骨架与整页之间没有空帧。 */
function reveal(container: Element): HTMLElement {
  const host = document.createElement('div');
  host.className = 'peach-react';
  revealSkeleton(container, () => { container.textContent = ''; container.append(host) });
  return host;
}

/* ── 关注 ── */

async function openFollow(actions: ShellActions, live: () => boolean) {
  const stats = statsHost();
  const shell = actions.follow;
  if (!stats || !shell) return;
  if (shell.refresh()) return;
  const restoreY = followScrollY;
  actions.surfaceChanged('follow', '/follow');
  paintManagementPlaceholder(stats, shell.skeleton());
  await openManagedRoute('/follow', shell.props(), { container: stats, isCurrent: live, place: reveal });
  if (live()) window.scrollTo(restoreY ? { top: restoreY, behavior: 'instant' } : { top: 0, behavior: 'smooth' });
}

/* `#stats` 里画着、露着的是关注页。 */
function followShown(): boolean {
  const stats = statsHost();
  const entry = stats ? managedEntry(stats) : null;
  return !!stats && !stats.hidden && !!entry && entry.path === '/follow' && entry.host.isConnected;
}

/** 关注页的元素。 */
export function FollowMatch() {
  const actions = useContext(ShellActionsContext);
  usePageOpen(useOpenEpoch(), (_fresh, live) => {
    if (actions) void openFollow(actions, live);
  }, {
    shown: followShown,
    onCovered: () => {
      if (peachHistory.navigation.location.pathname.startsWith('/posts/')) actions?.follow?.ground();
    },
  });
  return null;
}

/* ── 播放列表 ── */

/* `#stats` 里画着的是播放列表页。 */
function playlistsShown(stats: HTMLElement | null): stats is HTMLElement {
  const entry = stats ? managedEntry(stats) : null;
  return !!entry && entry.path === '/playlists' && entry.host.isConnected;
}

async function openPlaylists(actions: ShellActions, live: () => boolean) {
  const stats = statsHost();
  if (!stats) return;
  actions.surfaceChanged('playlists', '/playlists');
  paintManagementPlaceholder(stats, managementSkeletonHtml('/playlists', { followLayout: '', followSort: '', followDir: '' }));
  await openManagedRoute('/playlists', { revision: playlistsRevision }, { container: stats, isCurrent: live });
  if (live()) window.scrollTo({ top: 0, behavior: 'smooth' });
}

/** 播放列表页的元素。 */
export function PlaylistsMatch() {
  const actions = useContext(ShellActionsContext);
  const revision = useSyncExternalStore(subscribeShell, readPlaylistsRevision);
  const last = useRef(revision);
  useEffect(() => {
    if (last.current === revision) return;
    last.current = revision;
    const stats = statsHost();
    if (playlistsShown(stats)) updateManagedRoute(stats, { revision });
  }, [revision]);
  usePageOpen(useOpenEpoch(), (_fresh, live) => {
    if (actions) void openPlaylists(actions, live);
  }, { shown: () => { const stats = statsHost(); return playlistsShown(stats) && !stats.hidden } });
  return null;
}
