/** 页面持续读取已提交结果；仅使发生变化的资料缓存失效。 */
import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { fetchTasks, pollInterval, TASKS_KEY } from './activity/tasks';
import { queryClient } from './query';

const CATALOG = ['catalog-grid', 'catalog-folders', 'item', 'item-related', 'item-queue',
  'entity', 'entity-items', 'entity-photos', 'entity-route', 'index', 'stats',
  'quality-goals', 'duplicates', 'orphan-records', 'trash-summary', 'junk-summary', 'immerse-list'];
const FOLLOW = ['follow-feed', 'follow-item', 'follow-manage', 'follow', 'entity', 'index'];
const DISCOVERIES = ['feed-new', 'jav-discoveries', 'jav-detail', 'wants', 'index'];
const READINGS: Record<string, string[]> = {
  catalog: [...CATALOG, ...FOLLOW, 'review', 'taste'],
  follow: FOLLOW,
  discoveries: DISCOVERIES,
  review: ['review'],
  artwork: [...CATALOG, ...FOLLOW, ...DISCOVERIES, 'avatar-choices'],
};

export function createResultRefresher() {
  const pending = new Set<string>();
  let running: Promise<void> | null = null;
  return (before: Record<string, string>, next: Record<string, string>): Promise<void> => {
    for (const key of Object.keys(next)) {
      if (next[key] !== before[key]) for (const head of READINGS[key] || []) pending.add(head);
    }
    if (!running && pending.size) {
      running = (async () => {
        while (pending.size) {
          const heads = new Set(pending);
          pending.clear();
          const filters = { predicate: (query: { queryKey: readonly unknown[] }) => heads.has(String(query.queryKey[0]))
            && (query.queryKey[0] !== 'follow-manage' || query.queryKey.length === 1 || query.queryKey[1] === 'feeds') };
          const inFlight = queryClient.getQueryCache().findAll(filters)
            .some(query => query.isActive() && query.state.fetchStatus === 'fetching');
          await queryClient.invalidateQueries(filters, { cancelRefetch: false });
          if (inFlight) for (const head of heads) pending.add(head);
        }
      })().finally(() => { running = null });
    }
    return running || Promise.resolve();
  };
}

export function ResultUpdates() {
  const query = useQuery({ queryKey: TASKS_KEY, queryFn: ({ signal }) => fetchTasks(signal),
    refetchInterval: current => pollInterval(current.state.data) });
  const previous = useRef<Record<string, string> | undefined>(undefined);
  const [refresh] = useState(createResultRefresher);
  const versions = query.data?.result_versions;
  useEffect(() => {
    if (!versions) return;
    if (previous.current) void refresh(previous.current, versions);
    previous.current = versions;
  }, [versions, refresh]);
  return null;
}
