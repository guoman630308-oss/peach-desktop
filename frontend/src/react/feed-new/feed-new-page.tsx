/* 首页那一行新作（`feed-new` 页面内组件，路由树画进 `#feedNew`）。只在目录路径上画：离开目录页由壳收起，人还在目录页里换
 * 筛选时这一行不重挂、不重取——它不随筛选变。取回来的卡 portal 回宿主 `#feedNew` 本身。 */
import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

import { queryClient } from '../query';
import { feedNewKey, feedNewOptions, postFeedAction, type FeedNewProps } from './feed-new';
import { FeedNewRow } from './feed-new-row';

export function FeedNewPage({ host, revision, helpers, actions }: FeedNewProps) {
  const feed = useQuery(feedNewOptions(null, helpers.feedRowHtml));
  const seen = useRef(revision);
  useEffect(() => {
    if (seen.current === revision) return;
    seen.current = revision;
    void queryClient.invalidateQueries({ queryKey: feedNewKey(null) });
  }, [revision]);
  return createPortal(<FeedNewRow feedNew={feed.data ?? null} host={host} wire={helpers.wireFeedRow}
    act={postFeedAction} settled={actions.settled} />, host);
}
