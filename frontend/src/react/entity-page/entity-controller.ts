import { entityPath } from '@peach/legacy/core';
import { nextSortState } from '../../sort-preferences';
import type { EntityFilters, EntityMedia, EntityPageActions, EntityPageHelpers, EntityPageProps } from './entity-page';
import { emptyEntityFilters, entityViewPath } from './entity-route';

export type EntityOpeningProps = Omit<EntityPageProps, 'hosts' | 'card'>;
export type EntityPreferences = Omit<EntityOpeningProps,
  'kind' | 'name' | 'filters' | 'media' | 'helpers' | 'actions' | 'seed' | 'jav'>;

export interface EntityContext { kind: string; name: string; filters: EntityFilters }
export interface EntityControllerPorts {
  context(): EntityContext | null;
  live(kind: string, name: string): boolean;
  current(): boolean;
  route(path: string, replace?: boolean): void;
  suffix(): string;
  restoreHomeContext(): void;
  releaseBodyPreviews(): void;
  push(patch: Partial<EntityOpeningProps>): void;
  state(): { seed: string; jav: boolean };
  preferences(): EntityPreferences;
  changeFilter(change: (filters: Record<string, string>) => void): void;
  rollSeed(): string;
  writeSeed(seed: string): void;
  setJavLayout(value: string): void;
  javLayout(): string;
  setPhotoLayout(value: string): void;
  syncPhotoWalls(): void;
  openEntity(kind: string, name: string): void;
  photoViewActive(): boolean;
  recordPainted(view: Parameters<EntityPageActions['painted']>[0], wall: boolean): void;
  syncDensity(): void;
  scheduleSticky(): void;
  releasePage(): void;
  showMissing(kind: string): void;
  dropBars(): void;
}

/** 页内换筛选复用当前宿主，地址提交由历史的唯一拥有者执行。 */
export function createEntityController(ports: EntityControllerPorts, helpers: EntityPageHelpers,
  delegated: Pick<EntityPageActions, 'toggleTag' | 'openFollowAuthor' | 'javContext'>) {
  const route = (kind: string, name: string, filters: EntityFilters,
    media: EntityMedia = { media: 'videos', set: 0 }, { push = true }: { push?: boolean } = {}) => {
    if (push) ports.route(entityViewPath(kind, name, filters, media));
    ports.restoreHomeContext();
    if (!ports.live(kind, name)) return false;
    ports.releaseBodyPreviews();
    const state = ports.state();
    ports.push({ filters: { ...filters }, media: { ...media }, seed: state.seed, jav: state.jav });
    return true;
  };
  const actionCache = new Map<string, EntityPageActions>();
  const actions = (kind: string, name: string): EntityPageActions => {
    const key = entityPath(kind, name);
    const cached = actionCache.get(key);
    if (cached) return cached;
    const live = () => {
      const context = ports.context();
      return context?.kind === kind && context.name === name ? context.filters : emptyEntityFilters();
    };
    const value: EntityPageActions = {
      ...delegated,
      route: (filters, media) => { route(kind, name, filters, media) },
      clearFilter: key => ports.changeFilter(filters => { filters[key] = '' }),
      clearAll: () => ports.changeFilter(filters => {
        filters.tag = ''; filters.creator = ''; filters.studio = ''; filters.owner = '';
      }),
      setSort: key => {
        const filters = live(), next = nextSortState(key, filters.sort || 'new', filters.dir || '');
        if (next) route(kind, name, { ...filters, ...next });
      },
      reshuffleVideos: () => {
        const seed = ports.rollSeed();
        ports.writeSeed(seed);
        route(kind, name, { ...live(), sort: 'seed' });
        return ports.state().seed;
      },
      setJavLayout: value => { ports.setJavLayout(value); ports.push({ javLayout: ports.javLayout() }) },
      setPhotoLayout: value => { ports.setPhotoLayout(value); ports.syncPhotoWalls() },
      openEntity: (target, to, replace = false) => {
        if (replace) ports.route(entityPath(target, to) + ports.suffix(), true);
        else ports.openEntity(target, to);
      },
      painted: (view, wall) => {
        const wasPhotos = ports.photoViewActive();
        ports.recordPainted(view, !!wall);
        if (ports.photoViewActive() !== wasPhotos) ports.syncDensity();
        ports.scheduleSticky();
      },
      missing: () => queueMicrotask(() => {
        if (!ports.current()) return;
        ports.releasePage();
        ports.showMissing(kind);
      }),
      avatarChanged: () => ports.dropBars(),
    };
    // 只缓存当前一页，重开该页的 props 仍读实时偏好与筛选。
    actionCache.clear();
    actionCache.set(key, value);
    return value;
  };
  return {
    route, actions,
    props(kind: string, name: string, filters: EntityFilters, media: EntityMedia): EntityOpeningProps {
      return { ...ports.preferences(), ...ports.state(), kind, name, filters, media, helpers, actions: actions(kind, name) };
    },
  };
}
