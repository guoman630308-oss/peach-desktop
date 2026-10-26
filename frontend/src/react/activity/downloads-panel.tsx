/* 活动页「云下载」段：提交过的离线下载任务。提交在各页原地的弹层里（`cloud-download-dialog.tsx`）。
 *
 * 任务表是 `/api/downloads` 自己的一份，不并进 `/api/tasks`：离线任务跑在网盘那头，一跑
 * 几小时到几天，是一条带远端编号的提交记录，不是这台电脑上的一轮批处理。所以这一段自己取数、
 * 自己轮询：有任务还在远端跑或等着落盘时五秒一次，全是终态三十秒一次。后台的对账节律由
 * 服务端定（提交后约十秒查一次，之后退避），这里只是把它查到的结果读出来。
 *
 * 一条任务都没有时整段不画。失败的任务给中文原因；失败、取消与停滞的可以重新提交，被判
 * 违规的不给（服务端也会拒）。每张卡都能复制磁力：接口失效时退回手动添加。 */
import { useId, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import { Chip } from '@/components/base/badges/chip';
import { Button } from '@/components/base/buttons/button';

import { apiGet, apiSend, errorMessage } from '../../api';
import type { DownloadsSnapshot, DownloadTask } from '../bundle';
import { cardClass } from '../components/card';
import { Note } from '../components/note';
import { Progress } from '../components/progress';
import { ErrorText } from '../settings/section';
import { busyProps, useAction } from '../settings/use-action';
import { momentText } from './tasks';
import { SourceLabel } from '../components/source-label';

export const DOWNLOADS_URL = '/api/downloads';
export const DOWNLOADS_KEY = ['downloads'] as const;
const CANCEL_URL = '/api/downloads/cancel';
const POSTPROCESS_URL = '/api/downloads/postprocess';

export const downloadPollInterval = (data: DownloadsSnapshot | undefined) =>
  data?.tasks.some((task) => task.cancellable || task.postprocess_active) ? 5_000 : 30_000;

const STATE_COLORS: Record<string, 'lime' | 'rose' | 'yellow'> = {
  ingested: 'lime', failed: 'rose', cancelled: 'yellow', stalled: 'yellow',
};

const taskName = (task: DownloadTask) =>
  task.title || task.display_name || task.remote_name || task.code || `磁力 ${task.info_hash?.slice(0, 8) ?? task.id}`;

export const failureText = (task: DownloadTask) =>
  task.failure_label ? [task.failure_label, task.failure_detail].filter(Boolean).join('：') : '';

export function DownloadsPanel() {
  const id = useId();
  const query = useQuery({
    queryKey: DOWNLOADS_KEY,
    queryFn: ({ signal }) => apiGet<DownloadsSnapshot>(`${DOWNLOADS_URL}?limit=50`, signal),
    refetchInterval: (state) => downloadPollInterval(state.state.data),
  });
  const data = query.data;
  if (!data?.tasks.length && !query.error) return null;
  return (
    <section aria-labelledby={id} className="flex min-w-0 flex-col gap-3">
      <h3 id={id} className="text-title-2-semibold text-text-primary">云下载</h3>
      {query.error ? <Note tone="error">{errorMessage(query.error)}</Note> : null}
      {data?.tasks.length
        ? <ul aria-live="polite" className="flex min-w-0 flex-col gap-3">
            {data.tasks.map((task) => <TaskCard key={task.id} task={task} writable={data.available} />)}
          </ul>
        : null}
    </section>
  );
}

function TaskCard({ task, writable }: { task: DownloadTask; writable: boolean }) {
  const client = useQueryClient();
  const action = useAction();
  const [copied, setCopied] = useState(false);
  const reason = failureText(task);
  const failed = task.state === 'failed' || task.postprocess_state === 'failed';
  const meta = [task.code, task.target, momentText(task.submitted_at)]
    .filter(Boolean).join(' · ');
  const refresh = () => { void client.invalidateQueries({ queryKey: DOWNLOADS_KEY }) };

  const cancel = () => {
    void action.run('cancel', (signal) => apiSend(CANCEL_URL, { id: task.id }, 'POST', signal), refresh);
  };
  const resubmit = () => {
    const body = { magnet: task.source_uri, provider: task.provider, target: task.target,
      code: task.code, title: task.title, origin: task.origin };
    void action.run('resubmit', (signal) => apiSend(DOWNLOADS_URL, body, 'POST', signal), refresh);
  };
  const copy = () => {
    void navigator.clipboard.writeText(task.source_uri).then(() => setCopied(true), () => setCopied(false));
  };
  const retryPostprocess = () => {
    void action.run('postprocess', (signal) => apiSend(
      POSTPROCESS_URL, { id: task.id }, 'POST', signal), refresh);
  };

  return (
    <li data-download-state={task.state} data-download-id={task.id}
      className={cardClass({
        padding: 'none', bordered: 'line',
        className: failed ? 'flex flex-col border-border-error-default' : 'flex flex-col',
      })}>
      <div className="flex flex-col gap-1.5 px-5 pt-5 pb-4">
        <div className="flex flex-wrap items-center gap-2">
          <strong className="min-w-0 break-words text-title-1-medium text-text-primary">{taskName(task)}</strong>
          <Chip color={STATE_COLORS[task.state] ?? 'neutral'}>{task.state_label}</Chip>
          {task.state === 'ingested'
            ? <Chip color={task.postprocess_state === 'complete' ? 'lime'
              : task.postprocess_state === 'failed' ? 'rose' : 'yellow'}>{task.postprocess_label}</Chip>
            : null}
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-2 text-caption-1-regular text-text-secondary"><SourceLabel source={task.provider} label={task.provider_label} /><span className="break-words">{meta}</span></div>
        {task.state === 'remote_running' && task.progress != null
          ? <Progress label="远端下载进度" value={Math.round(task.progress * 100)} max={100} />
          : null}
        {task.ledger_path
          ? <p className="min-w-0 break-all text-caption-1-regular text-text-secondary">{task.ledger_path}</p>
          : null}
        {action.error ? <ErrorText>{action.error}</ErrorText> : null}
        <div className="flex flex-wrap items-center gap-2 pt-1">
          {writable && task.cancellable
            ? <Button variant="secondary" size="small" {...busyProps(action.busy === 'cancel')} onClick={cancel}>取消</Button>
            : null}
          {writable && task.resubmittable
            ? <Button variant="secondary" size="small" {...busyProps(action.busy === 'resubmit')} onClick={resubmit}>重新提交</Button>
            : null}
          {writable && task.postprocess_retryable
            ? <Button variant="secondary" size="small" {...busyProps(action.busy === 'postprocess')}
                onClick={retryPostprocess}>重试后处理</Button>
            : null}
          <Button variant="ghost" size="small" onClick={copy}>{copied ? '已复制磁力' : '复制磁力'}</Button>
        </div>
      </div>
      {reason || task.postprocess_detail
        ? <div className="flex min-h-14 flex-col justify-center rounded-b-2xl border-t border-separator-border bg-card-footer px-5 py-3">
            <p className="text-caption-1-regular text-text-secondary">{reason || task.postprocess_detail}</p>
          </div>
        : null}
    </li>
  );
}
