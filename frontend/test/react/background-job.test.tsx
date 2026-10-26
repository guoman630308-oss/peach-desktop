/* 后台任务的时序：旧终态什么时候算、什么时候不算。
 *
 * 失败模式先列清楚，每条一个用例：
 * - 首屏读到上一趟的终态，把它当成这一趟的回执报出去；
 * - 点下启动、重读回来之前，缓存里上一趟的终态冒充这一趟；
 * - 这一趟在第一次重读之前就跑完，结果被丢掉；
 * - 同一个终态报两次；
 * - 缓存的是整张卡时，换进快照把卡上别的字段冲掉；
 * - 启动失败时，旧终态被当成这一趟的结果。
 *
 * 重读用手动放行的 `pending()` 拖住：假读取当场回话的话，中间那段空档不存在，前两条测不出来。 */
import { act, type ReactElement } from 'react';
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

import { useBackgroundJob } from '../../src/react/background-job';
import { queryClient } from '../../src/react/query';
import { buttonNamed, click, mount, pending, settle } from './render';

afterEach(() => queryClient.clear());

/* Query 默认用 `setTimeout(0)` 派发更新，`settle()` 只等微任务，等不到它；改成当场派发。 */
notifyManager.setScheduler((notify) => notify());

interface Job { status: string; result?: string }
interface Card { name: string; job: Job }
type Answer<T> = T | Promise<T>;

const KEY = ['background-job-test'] as const;
const RUNNING: Job = { status: 'running' };
const STALE: Job = { status: 'complete', result: '上一趟' };

interface Hooks {
  start?: () => Promise<Job>;
  onFinish?: (job: Job) => void;
  onError?: (cause: unknown) => void;
}

const snapshot = (job: Job | undefined, outcome: Job | null, running: boolean) =>
  `job=${job ? `${job.status}:${job.result ?? ''}` : ''};`
  + `outcome=${outcome ? `${outcome.status}:${outcome.result ?? ''}` : ''};running=${running}`;

function Flat({ read, start = async () => RUNNING, onFinish, onError }: { read: () => Answer<Job> } & Hooks) {
  const task = useBackgroundJob<Job>({
    queryKey: KEY, queryFn: async () => read(), start, onFinish, onError,
  });
  return (
    <>
      <p>{snapshot(task.job, task.outcome, task.running)}</p>
      <button type="button" onClick={() => task.start.mutate()}>启动</button>
    </>
  );
}

function Nested({ read, onFinish }: { read: () => Answer<Card> } & Hooks) {
  const task = useBackgroundJob<Job, void, Card>({
    queryKey: KEY, queryFn: async () => read(), start: async () => RUNNING, onFinish,
    jobOf: (card) => card.job,
    withJob: (card, job) => card && { ...card, job },
  });
  return (
    <>
      <p>{`name=${task.query.data?.name ?? ''};${snapshot(task.job, task.outcome, task.running)}`}</p>
      <button type="button" onClick={() => task.start.mutate()}>启动</button>
    </>
  );
}

const open = async (element: ReactElement) => {
  const host = await mount(<QueryClientProvider client={queryClient}>{element}</QueryClientProvider>);
  await settle();
  return host;
};

const reread = () => act(async () => { await queryClient.refetchQueries({ queryKey: KEY }) });

it('首屏读到的上一趟终态不报回执，也不落成这一趟的结果', async () => {
  const onFinish = vi.fn();
  const host = await open(<Flat read={() => STALE} onFinish={onFinish} />);
  expect(host.textContent).toContain('job=complete:上一趟;outcome=;running=false');
  await reread();
  expect(onFinish).not.toHaveBeenCalled();
});

it('首屏就在跑的那一趟算本次在跟：跑完报一次回执', async () => {
  let answer: Job = RUNNING;
  const onFinish = vi.fn();
  const host = await open(<Flat read={() => answer} onFinish={onFinish} />);
  expect(host.textContent).toContain('running=true');

  answer = { status: 'complete', result: '别处起的' };
  await reread();
  await reread();
  expect(onFinish.mock.calls).toEqual([[{ status: 'complete', result: '别处起的' }]]);
  expect(host.textContent).toContain('outcome=complete:别处起的');
});

it('点下启动到重读回来之间，上一趟的终态不冒充这一趟；这一趟在重读前跑完也接得住', async () => {
  let answer: Answer<Job> = STALE;
  const onFinish = vi.fn();
  const host = await open(<Flat read={() => answer} onFinish={onFinish} />);
  const slow = pending<Job>();
  answer = slow.answer;
  await click(buttonNamed('启动', host));
  await settle();
  expect(onFinish).not.toHaveBeenCalled();
  expect(host.textContent).toContain('job=running:;outcome=;running=true');

  // 重读回来时它已经跑完了：缓存里从头到尾没出现过第二个 running。
  await slow.release({ status: 'complete', result: '这一趟' });
  await settle();
  expect(onFinish.mock.calls).toEqual([[{ status: 'complete', result: '这一趟' }]]);
  expect(host.textContent).toContain('outcome=complete:这一趟;running=false');

  answer = { status: 'complete', result: '这一趟' };
  await reread();
  expect(onFinish, '同一个终态只报一次').toHaveBeenCalledTimes(1);
});

it('缓存整张卡时只换任务那一格，卡上别的字段在重读回来前还在', async () => {
  let answer: Answer<Card> = { name: '钉住的版本', job: STALE };
  const onFinish = vi.fn();
  const host = await open(<Nested read={() => answer} onFinish={onFinish} />);
  const slow = pending<Card>();
  answer = slow.answer;
  await click(buttonNamed('启动', host));
  await settle();
  expect(host.textContent).toContain('name=钉住的版本;job=running:;outcome=;running=true');
  expect(onFinish).not.toHaveBeenCalled();

  await slow.release({ name: '钉住的版本', job: { status: 'failed', result: '这一趟' } });
  await settle();
  expect(onFinish.mock.calls).toEqual([[{ status: 'failed', result: '这一趟' }]]);
});

it('启动被拒时交给 onError，旧终态不当成这一趟的结果', async () => {
  const onFinish = vi.fn();
  const onError = vi.fn();
  const refused = new Error('已有一趟在跑');
  const host = await open(<Flat read={() => STALE} onFinish={onFinish} onError={onError}
    start={async () => { throw refused }} />);
  await click(buttonNamed('启动', host));
  await settle();
  expect(onError.mock.calls).toEqual([[refused]]);
  expect(onFinish).not.toHaveBeenCalled();
  expect(host.textContent).toContain('outcome=;running=false');
});
