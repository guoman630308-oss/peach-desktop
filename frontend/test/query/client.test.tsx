/* 全站唯一 QueryClient（src/query/）：Application 与 Providers 直接引用共享源码，读写同一缓存。 */
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it } from 'vitest';

import { queryClient } from '@peach/query';

import { queryClient as sharedQueryClient } from '../../src/query/client';
import { Providers } from '../../src/react/providers';
import { queryClient as reactQueryClient } from '../../src/react/query';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => { document.body.innerHTML = '' });

it('React 岛里 useQueryClient() 拿到的就是壳那一个', async () => {
  let seen: QueryClient | null = null;
  function Probe() { seen = useQueryClient(); return null }
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => { root.render(<Providers><Probe /></Providers>) });
  expect(seen).toBe(queryClient);
  expect(reactQueryClient).toBe(queryClient);
  expect(sharedQueryClient).toBe(queryClient);
  await act(async () => { root.unmount() });
});

it('壳写进去的那一份，岛里按同一个键直接读到', () => {
  queryClient.setQueryData(['probe'], { n: 1 });
  expect(reactQueryClient.getQueryData(['probe'])).toEqual({ n: 1 });
  queryClient.removeQueries({ queryKey: ['probe'] });
});

it('默认选项：不重试、聚焦与挂载都不重取，离线也照发', () => {
  const queries = queryClient.getDefaultOptions().queries!;
  expect(queries).toMatchObject({
    networkMode: 'always', retry: 0, refetchOnWindowFocus: false, refetchOnMount: false, retryOnMount: false,
  });
});
