/* 后台任务：点一下起一趟，服务端在后台跑，页面按状态轮询，跟到终态报一次回执。
 *
 * 两条时序在这里定死，页面不各写一份：
 * - **首屏读到的旧终态不冒充新结果**。任务关掉页面照样在跑，状态里常年躺着上一趟的回执；
 *   只有本次启动过、或者本次见过它在跑，终态才交给 `onFinish`、落进 `outcome`。
 * - **启动成功时先把启动请求回的 `running` 快照换进缓存，再重读**。只重读的话，重读回来
 *   之前缓存里的上一趟终态会被当成这一趟的回执；这一趟在重读前就跑完时，结果也会被它顶掉。 */
import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, type QueryKey } from '@tanstack/react-query';

import { queryClient } from './query';

export interface JobState { status?: string }

/** 在跑时两秒一次，和活动页同一个节律。 */
export const JOB_RUNNING_POLL_MS = 2000;

export interface BackgroundJobOptions<Job extends JobState, Args, Data> {
  queryKey: QueryKey;
  queryFn: (context: { signal: AbortSignal }) => Promise<Data>;
  start: (args: Args) => Promise<Job>;
  /** 缓存里存的是整张卡、任务只占一格时：任务在哪一格，新快照怎么换进去。 */
  jobOf?: (data: Data) => Job | undefined;
  withJob?: (data: Data | undefined, job: Job) => Data | undefined;
  /** 闲着时多久问一次。任务可能从别的页面或上一次会话里起来的页面才需要；默认不问。 */
  idlePollMs?: number;
  onStarted?: (started: Job) => void;
  onError?: (cause: unknown) => void;
  /** 本次跟完的那一趟到了终态，只调一次。 */
  onFinish?: (job: Job) => void;
}

const itself = <Job, Data>(data: Data) => data as unknown as Job;
const replaced = <Job, Data>(_data: Data | undefined, job: Job) => job as unknown as Data;

export function useBackgroundJob<Job extends JobState, Args = void, Data = Job>(
  options: BackgroundJobOptions<Job, Args, Data>,
) {
  const { queryKey, jobOf = itself<Job, Data>, withJob = replaced<Job, Data>, idlePollMs } = options;
  /** 本次是不是在跟一趟：启动过，或者本次见过它在跑。 */
  const [tracking, setTracking] = useState(false);
  /** 本次跟完的那一趟。首屏读到的旧终态不算，它不会走到这里。 */
  const [outcome, setOutcome] = useState<Job | null>(null);
  /* 回调由页面每次渲染新建，放进 ref 读最新的一份：进 effect 依赖的话，每画一遍都要重跑。 */
  const hooks = useRef(options);
  useEffect(() => { hooks.current = options });

  const query = useQuery({
    queryKey,
    queryFn: ({ signal }) => hooks.current.queryFn({ signal }),
    refetchInterval: (current) => {
      const data = current.state.data;
      return data !== undefined && jobOf(data)?.status === 'running' ? JOB_RUNNING_POLL_MS : (idlePollMs ?? false);
    },
  });
  const start = useMutation({
    mutationFn: (args: Args) => hooks.current.start(args),
    onSuccess: (started) => {
      setTracking(true);
      setOutcome(null);
      hooks.current.onStarted?.(started);
      queryClient.setQueryData<Data>(queryKey, (data) => withJob(data, started));
      /* `exact`：别的键可能以同一段开头，按前缀失效会把它们一起推倒重来。 */
      void queryClient.invalidateQueries({ queryKey, exact: true });
    },
    onError: (cause) => hooks.current.onError?.(cause),
  });

  const job = query.data === undefined ? undefined : jobOf(query.data);
  const running = job?.status === 'running';
  useEffect(() => {
    if (!job) return;
    if (job.status === 'running') {
      if (!tracking) setTracking(true);
      return;
    }
    if (!tracking) return;
    setTracking(false);
    setOutcome(job);
    hooks.current.onFinish?.(job);
  }, [job, tracking]);

  /** 收起本次的结果，比如用户关掉结果卡，或者结果已经登记完。 */
  const dismiss = () => setOutcome(null);

  return { query, job, running, outcome, start, dismiss };
}
