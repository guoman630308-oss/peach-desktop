import { sidebarTagCounts } from '../../sidebar';
import type { FollowDrawer } from '../follow-feed/follow-feed';
import type { FollowView } from '../follow-feed/follow-feed';
import { followViewPath } from '../follow-feed/follow-view';
import type { FollowDetailActions } from './follow-detail';

export interface FollowDetailActionPorts {
  view(): FollowView;
  adoptView(view: FollowView): void;
  rememberReturn(path: string): void;
  close(): void;
  open(id: number, mediaIndex: number | null, preserveReturn: boolean): void;
  openAuthor(name: string): void;
  drawer(drawer: Pick<FollowDrawer, 'tags'>): void;
}

/** 舞台的打开、代次与卸载仍由舞台拥有者持有；域只决定筛选与返回地址。 */
export function createFollowDetailActions(ports: FollowDetailActionPorts,
  receipts: Pick<FollowDetailActions, 'toast' | 'failure'>): Omit<FollowDetailActions, 'mountPlayer'> {
  return {
    ...receipts,
    close: () => ports.close(),
    openItem: (id, mediaIndex = null) => ports.open(id, mediaIndex, true),
    openAuthor: name => ports.openAuthor(name),
    openTag: tag => {
      const view = ports.view(), tags = new Set(view.tags);
      if (tags.has(tag)) tags.delete(tag); else tags.add(tag);
      const next = { ...view, tags: [...tags] };
      ports.adoptView(next);
      ports.rememberReturn(followViewPath(next));
      ports.close();
    },
    present: item => ports.drawer({ tags: sidebarTagCounts([{ tags: item.tags || [] }]) }),
  };
}
