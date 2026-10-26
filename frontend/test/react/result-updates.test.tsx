import { act } from 'react';
import { QueryClientProvider, notifyManager, useQuery } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

import { createResultRefresher, ResultUpdates } from '../../src/react/result-updates';
import { TASKS_KEY } from '../../src/react/activity/tasks';
import { queryClient } from '../../src/react/query';
import { mount, pending, settle } from './render';

notifyManager.setScheduler(notify => notify());
afterEach(() => queryClient.clear());

it('未显示页面的后台预取继续读取，不阻塞当前页面的版本处理', async () => {
  const old = pending<string>();
  const prefetched = queryClient.fetchQuery({ queryKey: ['item', 3], queryFn: () => old.answer });
  await createResultRefresher()({ catalog: '1' }, { catalog: '2' });
  expect(queryClient.getQueryState(['item', 3])?.fetchStatus).toBe('fetching');
  await old.release('预取内容');
  expect(await prefetched).toBe('预取内容');
});

it('运行中的任务发布确认结果时更新列表，状态与凭据查询保持原值', async () => {
  const data = { available: true, running: [{ id: 1, status: 'running' }], finished: [], skipped: [],
    result_versions: { follow: '1' } };
  queryClient.setQueryData(TASKS_KEY, data);
  queryClient.setQueryData(['follow-feed'], '首项');
  queryClient.setQueryData(['follow-manage', 'check'], 'running');
  queryClient.setQueryData(['follow-manage', 'credentials'], '凭据');
  queryClient.setQueryData(['catalog-grid'], '馆藏');
  queryClient.setQueryData(['index', 'performers'], '旧名册');
  let current = '首项';
  const read = vi.fn(async () => current);
  function Feed() {
    const feed = useQuery({ queryKey: ['follow-feed'], queryFn: read });
    return <p>{feed.data}</p>;
  }
  const host = await mount(<QueryClientProvider client={queryClient}><ResultUpdates /><Feed /></QueryClientProvider>);
  await settle();
  expect(read).not.toHaveBeenCalled();
  await act(async () => {
    current = '首项、次项';
    queryClient.setQueryData(TASKS_KEY, { ...data, result_versions: { follow: '2' } });
  });
  await settle();
  expect(host.textContent).toBe('首项、次项');
  expect(read).toHaveBeenCalledTimes(1);
  expect(queryClient.getQueryState(['follow-manage', 'check'])?.isInvalidated).toBe(false);
  expect(queryClient.getQueryState(['follow-manage', 'credentials'])?.isInvalidated).toBe(false);
  expect(queryClient.getQueryState(['catalog-grid'])?.isInvalidated).toBe(false);
  expect(queryClient.getQueryState(['index', 'performers'])?.isInvalidated).toBe(true);
});

it('版本到达前已发出的读取结束后再核查一次，旧响应不能盖住确认结果', async () => {
  const old = pending<string>();
  let reads = 0;
  function Item() {
    const item = useQuery({ queryKey: ['item', 2], queryFn: () => ++reads === 1 ? old.answer : Promise.resolve('确认结果') });
    return <p>{item.data}</p>;
  }
  const host = await mount(<QueryClientProvider client={queryClient}><Item /></QueryClientProvider>);
  const refresh = createResultRefresher();
  const completed = refresh({ catalog: '1' }, { catalog: '2' });
  await old.release('旧响应');
  await act(async () => { await completed; });
  expect(reads).toBe(2);
  expect(host.textContent).toBe('确认结果');
});

it('缓慢读取期间的下一项结果排队刷新，不取消读取且不遗漏新值', async () => {
  const first = pending<string>();
  let reads = 0;
  queryClient.setQueryData(['item', 1], '旧标题');
  function Item() {
    const item = useQuery({ queryKey: ['item', 1], queryFn: () => ++reads === 1 ? first.answer : Promise.resolve('最终标题') });
    return <p>{item.data}</p>;
  }
  const host = await mount(<QueryClientProvider client={queryClient}><Item /></QueryClientProvider>);
  const refresh = createResultRefresher();
  let completed: Promise<void>;
  await act(async () => {
    completed = refresh({ catalog: '1' }, { catalog: '2' });
    void refresh({ catalog: '2' }, { catalog: '3' });
  });
  expect(host.textContent).toBe('旧标题');
  await first.release('中间标题');
  await act(async () => { await completed!; });
  expect(reads).toBe(2);
  expect(host.textContent).toBe('最终标题');
});
