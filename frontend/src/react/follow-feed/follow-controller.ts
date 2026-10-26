import { followPageUrl, FOLLOW_RANDOM_SORT, type FollowFeedActions, type FollowFeedHelpers, type FollowFeedProps, type FollowView } from './follow-feed';
import { followViewPath, readFollowView } from './follow-view';

export interface FollowSession {
  discoverySeed: number;
  revision: number;
  scrollY: number;
}
export type FollowPreferences = Pick<FollowFeedProps, 'selectMode' | 'selected' | 'photoSize' | 'photoLayout' | 'imagesOnly'>;

/** 地址认领、会话存储与宿主操作分别由唯一拥有者提供。 */
export interface FollowControllerPorts {
  location(): { pathname: string; search: string };
  view(): FollowView;
  adoptView(view: FollowView): void;
  session(): FollowSession;
  writeSession(patch: Partial<FollowSession>): void;
  preferences(): FollowPreferences;
  live(): boolean;
  push(patch: Partial<FollowFeedProps>): void;
  route(path: string): void;
  openPage(path: string): void;
  rollSeed(): number;
  enterSeed(): number;
  revealFeed(): void;
  syncPhotoWalls(): void;
  scrollTop(): void;
}

export function createFollowController(ports: FollowControllerPorts, helpers: FollowFeedHelpers,
  delegated: Omit<FollowFeedActions, 'route' | 'shuffle'>) {
  const read = () => {
    const view = readFollowView(ports.location().search, ports.view().seed, ports.rollSeed);
    ports.adoptView(view);
    return view;
  };
  const push = (patch: Partial<FollowFeedProps>) => { if (ports.live()) ports.push(patch) };
  const route = (view: FollowView, patch: Partial<FollowFeedProps> = {}) => {
    const before = followPageUrl(ports.view(), 0);
    ports.adoptView(view);
    ports.route(followViewPath(view));
    push({ ...patch, view: ports.view() });
    ports.syncPhotoWalls();
    if (followPageUrl(ports.view(), 0) !== before) ports.scrollTop();
  };
  const shuffle = () => {
    const seed = ports.rollSeed();
    ports.writeSession({ discoverySeed: seed });
    route({ ...ports.view(), seed, sort: FOLLOW_RANDOM_SORT }, { seed: ports.session().discoverySeed });
  };
  // 动作对象跨 props 推送保持身份，卡片的 memo 不因页内筛选失效。
  const actions: FollowFeedActions = { ...delegated, route, shuffle };
  const props = (): FollowFeedProps => {
    const session = ports.session(), preferences = ports.preferences();
    return { ...preferences, selected: new Set(preferences.selected), view: ports.view(),
      seed: session.discoverySeed, revision: session.revision, helpers, actions };
  };
  return {
    actions, props, read, route, shuffle,
    refresh(): boolean {
      const view = read();
      if (!ports.live()) return false;
      ports.revealFeed();
      ports.writeSession({ revision: ports.session().revision + 1 });
      push({ view, seed: ports.session().discoverySeed, revision: ports.session().revision });
      ports.syncPhotoWalls();
      ports.scrollTop();
      return true;
    },
    enter(fresh = false): void {
      if (fresh || ports.location().pathname === '/follow') {
        ports.writeSession({ discoverySeed: ports.enterSeed(), scrollY: 0,
          ...(ports.live() ? {} : { revision: ports.session().revision + 1 }) });
        ports.openPage('/follow');
      } else ports.openPage(followViewPath(ports.view()));
    },
  };
}

/** 资料页在线卡复用详情与偏好动作，页级筛选、抽屉与本地多选各由资料页持有。 */
export function entityFollowActions(actions: FollowFeedActions): FollowFeedActions {
  return { ...actions, route: () => {}, shuffle: () => {}, loaded: () => {}, toggleSelection: () => {} };
}
