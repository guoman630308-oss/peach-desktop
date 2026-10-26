/* 活动页：任务中心那张表的唯一界面。
 *
 * 这一屏要回答三个问题，顺序就是它们的紧迫程度：现在有什么在跑、有哪一轮被挡下了、
 * 刚跑完的那些怎么样。三段共用 `/api/tasks` 一次请求的结果。末尾的「云下载」段是网盘离线
 * 任务，取数与轮询都在 `downloads-panel.tsx` 里，和这三段互不牵连。
 *
 * 轮询交给 Query 的 `refetchInterval`：间隔按上一次拿到的内容算，有东西在跑两秒一次，
 * 全是终态十秒一次。它是后台刷新，不写 `aria-busy`——页面上的内容一直是完整的，写了
 * 等于告诉辅助技术和冒烟用例「这一屏还没好」。取数失败只在页内报一条，上一份数据留着：
 * 服务重启的那几秒里，把整页换成一句错误比留着十秒前的进度更难用。
 *
 * 「最近完成」末尾的「加载更早」往前接一页，接上的行留在组件里，与轮询那一份按 id 合并：
 * 轮询只管第一页，翻过的那些不会被下一次刷新冲掉。 */
import { useId, useState } from 'react';
import type { ReactNode } from 'react';
import { RiArrowDownSLine, RiHistoryLine } from '@remixicon/react';
import { useMutation, useQuery } from '@tanstack/react-query';

import { Chip } from '@/components/base/badges/chip';
import { Button } from '@/components/base/buttons/button';

import { errorMessage } from '../../api';
import { cardClass } from '../components/card';
import { EmptyState } from '../components/empty-state';
import { LoadingDots } from '../components/loading-dots';
import { Note } from '../components/note';
import { Page } from '../components/page';
import { Progress } from '../components/progress';
import { busyProps } from '../settings/use-action';
import { Disclosure } from '../settings/section';
import { DownloadsPanel } from './downloads-panel';
import { ErrorExcerpt } from './error-excerpt';
import {
  elapsedText, fetchEarlier, fetchTasks, foldRoutine, followupDetail, groupFollowups, isActive,
  isRoutine, mergeFinished, momentText, pollInterval, progressText, statusLabel, summaryText, TASKS_KEY, TRIGGER_LABELS, type TaskRunPayload,
} from './tasks';

/* 状态徽章只有三档颜色：成功是绿、失败是红、被叫停与被打断是黄，其余留中性底。
   BoardUI 的 Chip 把这三档写成 lime／rose／yellow，第四种颜色不存在。 */
const BADGE_COLORS: Record<string, 'lime' | 'rose' | 'yellow'> = {
  succeeded: 'lime', failed: 'rose', cancelled: 'yellow', interrupted: 'yellow',
};

function StatusBadge({ status }: { status: string }) {
  return <Chip color={BADGE_COLORS[status] ?? 'neutral'}>{statusLabel(status)}</Chip>;
}

/** 一轮任务派出的后继（ADR-0040）。挂在父任务卡里，一条一行。
 *
 * 后继是独立的一轮，有自己的状态和结果，但单独摆出来就读不出「它是谁派的」——
 * 而那恰恰是这一屏上唯一需要解释的东西：用户没点过补头像，它却在跑。
 * 条间线写在每个 li 自己身上，第一条那根同时充当与卡片正文的分隔。 */
function FollowupList({ rows }: { rows: TaskRunPayload[] }) {
  return (
    <ul className="flex min-w-0 flex-col">
      {rows.map((row) => {
        const detail = followupDetail(row);
        return (
          <li key={row.id} data-status={row.status} data-followup-key={row.followup_key}
            className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 border-t border-separator-border px-5 py-2.5">
            <span className="min-w-0 break-words text-caption-1-regular text-text-primary">
              {row.task_label}
            </span>
            <StatusBadge status={row.status} />
            {detail
              ? <span className="min-w-0 break-words text-caption-1-regular text-text-secondary">{detail}</span>
              : null}
          </li>
        );
      })}
    </ul>
  );
}

/** 一轮任务一张卡。失败时整张卡的框线换成 danger 色，不给结束原因那行字上色——
 *  一屏十几行里逐行读红字，比看一眼哪张卡的框是红的慢得多。 */
function RunCard(
  { run, meta, children, footer, followups }:
  { run: TaskRunPayload; meta: string; children?: ReactNode; footer?: string;
    followups?: TaskRunPayload[] },
) {
  return (
    <li data-status={run.status} data-task-key={run.task_key} data-run-id={run.id}
      className={cardClass({
        padding: 'none',
        bordered: 'line',
        className: run.status === 'failed' ? 'flex flex-col border-border-error-default' : 'flex flex-col',
      })}>
      <div className="flex flex-col gap-1.5 px-5 pt-5 pb-4">
        <div className="flex flex-wrap items-center gap-2">
          <strong className="min-w-0 break-words text-title-1-medium text-text-primary">{run.task_label}</strong>
          <StatusBadge status={run.status} />
        </div>
        <p className="text-caption-1-regular text-text-secondary">{meta}</p>
        {children}
      </div>
      {followups?.length ? <div className="border-t border-separator-border px-5 py-2.5">
        <Disclosure summary={`任务明细 · ${followups.length} 项`}
          defaultOpen={followups.some((row) => isActive(row) || row.status === 'failed')}>
          <FollowupList rows={followups} />
        </Disclosure>
      </div> : null}
      {footer
        ? <div className="flex min-h-14 flex-col justify-center rounded-b-2xl border-t border-separator-border bg-card-footer px-5 py-3">
            <ErrorExcerpt text={footer} className="text-caption-1-regular text-text-secondary" />
          </div>
        : null}
    </li>
  );
}

function RunningRun({ run, followups }: { run: TaskRunPayload; followups?: TaskRunPayload[] }) {
  const total = run.progress_total || 0;
  const current = run.progress_current || 0;
  const label = run.progress_label || '正在进行';
  const elapsed = elapsedText(run.elapsed_seconds);
  const meta = [TRIGGER_LABELS[run.trigger] || run.trigger, elapsed && `已跑 ${elapsed}`]
    .filter(Boolean).join(' · ');
  return (
    <RunCard run={run} meta={meta} followups={followups}>
      {total > 0
        ? <div className="flex flex-col gap-1.5">
            <Progress label={label} value={Math.min(current, total)} max={total} />
            <p className="text-caption-1-regular text-text-secondary wrap-anywhere">{label} · {progressText(current, total)} 项</p>
          </div>
        : <LoadingDots label={label} />}
    </RunCard>
  );
}

/** 跑完的一轮。`runs` 多于一条时是折叠起来的几轮例行任务：时间写成从最早到最新那一段，
 *  摘要取最新那一轮——例行轮次的摘要本来就一样，逐条列出来只是同一行抄 N 遍。 */
function SettledRun(
  { run, followups, runs }: { run: TaskRunPayload; followups?: TaskRunPayload[]; runs?: TaskRunPayload[] },
) {
  const summary = summaryText(run.result_summary);
  const elapsed = elapsedText(run.elapsed_seconds);
  const oldest = runs && runs.length > 1 ? runs[runs.length - 1] : undefined;
  const meta = (oldest
    ? [TRIGGER_LABELS[run.trigger] || run.trigger, `近 ${runs!.length} 轮`,
       `${momentText(oldest.finished_at)} – ${momentText(run.finished_at)}`]
    : [TRIGGER_LABELS[run.trigger] || run.trigger, momentText(run.finished_at),
       elapsed && `用时 ${elapsed}`]).filter(Boolean).join(' · ');
  return (
    <RunCard run={run} meta={meta} footer={run.error} followups={followups}>
      {summary ? <p className="text-caption-1-regular text-text-secondary wrap-anywhere">{summary}</p> : null}
    </RunCard>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex min-w-0 flex-col gap-3">
      <h3 id={id} className="text-title-2-medium text-text-primary">{title}</h3>
      {children}
    </section>
  );
}

function RunList({ live = false, children }: { live?: boolean; children: ReactNode }) {
  return (
    <ul aria-live={live ? 'polite' : undefined} className="flex min-w-0 flex-col gap-3">{children}</ul>
  );
}

export function ActivityPage() {
  const tasks = useQuery({
    queryKey: TASKS_KEY,
    queryFn: ({ signal }) => fetchTasks(signal),
    refetchInterval: (query) => pollInterval(query.state.data),
  });
  const data = tasks.data;
  const top = data?.finished;
  // 往前翻接上的行。没翻过时一直是空的，页面和只有第一页时完全一样。
  const [earlier, setEarlier] = useState<TaskRunPayload[]>([]);
  const [earlierMore, setEarlierMore] = useState(false);
  const [seenTop, setSeenTop] = useState(top);
  if (top !== seenTop) {
    setSeenTop(top);
    // 翻过页之后，新完成的几轮会把第一页末尾的挤出去；把上一份第一页并进已翻过的部分，
    // 不然被挤出去的那几轮夹在两段之间，哪一段都不再有它们。
    if (earlier.length && seenTop) setEarlier(mergeFinished(earlier, seenTop));
  }
  const more = useMutation({
    mutationFn: ({ oldest }: { oldest: TaskRunPayload; shown: TaskRunPayload[] }) => fetchEarlier(oldest),
    onSuccess: (page, { shown }) => {
      setEarlier((current) => mergeFinished(current, shown, page.finished || []));
      setEarlierMore(Boolean(page.finished_has_more));
    },
  });
  const problem = tasks.error ? errorMessage(tasks.error) : '';
  // 首屏就没拿到数据：只剩这一条，不画空的三段。
  if (!data) return <Page><Note tone="error">{problem || '读取任务中心失败'}</Note></Page>;

  const allFinished = mergeFinished(data.finished || [], earlier);
  const allRuns = [...(data.running || []), ...(data.skipped || []), ...allFinished];
  // 后继挂到派出它的那张卡下面。父任务不在这一屏上（已经被 prune 掉）时照常单独摆出来
  // ——挂不上去就不显示，等于让一条在跑的任务凭空消失。
  const byParent = groupFollowups(allRuns);
  const visible = new Set(allRuns.map((run) => run.id));
  const topLevel = (rows: TaskRunPayload[]) => rows.filter(
    (run) => !(run.followup_key && run.parent_run_id != null && visible.has(run.parent_run_id)));
  const settled = topLevel(allFinished);
  // 游标取最旧的顶层那一轮：服务端一页数的是顶层行，后继跟着父任务走。
  const oldest = settled[settled.length - 1];
  const hasEarlier = Boolean(oldest) && (earlier.length ? earlierMore : Boolean(data.finished_has_more));
  const loadEarlier = () => {
    if (!more.isPending && oldest) more.mutate({ oldest, shown: allFinished });
  };
  // 自己跑完、派出的后继还在跑的那一轮算「正在进行」：整件事还没完，放进「最近完成」
  // 就是一张卡上一个已完成、一个进行中。
  const working = settled.filter((run) => byParent.get(run.id)?.some(isActive));
  const running = topLevel(data.running || []);
  const skipped = topLevel(data.skipped || []);
  // 同一轮不在「最近完成」里再出现一次：一屏两行说的是同一件事，读起来像跑了两轮。
  const finished = settled.filter((run) => !working.includes(run)
    && !skipped.some((row) => row.id === run.id));
  // 每小时一轮的定时检查什么也没发生时一条条摆出来，一页就被同一句「已检查 83」占满。
  const folds = foldRoutine(finished, (run) => isRoutine(run) && !byParent.get(run.id)?.length);
  const quiet = !running.length && !working.length && !skipped.length && !finished.length;
  return (
    <Page>
      {problem ? <Note tone="error">{problem}</Note> : null}
      {data.available === false
        ? <Note tone="warning">{data.message || '数据库尚未准备好任务记录表'}</Note>
        : null}
      {quiet
        ? <EmptyState shell="plain" icon={RiHistoryLine} title="还没有任务记录">
            扫描、追更检查、批量操作和命令行批处理跑起来之后，这里会显示它们的进度与结果。
          </EmptyState>
        : <>
            <Section title="正在进行">
              {running.length || working.length
                ? <RunList live>
                    {running.map((run) => (
                      <RunningRun key={run.id} run={run} followups={byParent.get(run.id)} />))}
                    {working.map((run) => (
                      <SettledRun key={run.id} run={run} followups={byParent.get(run.id)} />))}
                  </RunList>
                : <Note tone="neutral">没有任务在跑。</Note>}
            </Section>
            {/* 「刚才那一轮为什么没跑」只有这一段答得出：定时触发撞上在跑的那一轮会
                安静跳过，不留记录的话它在界面上和从没触发过一模一样。 */}
            {skipped.length
              ? <Section title="被挡下的">
                  <RunList>{skipped.map((run) => (
                    <SettledRun key={run.id} run={run} followups={byParent.get(run.id)} />))}</RunList>
                </Section>
              : null}
            {finished.length || hasEarlier
              ? <Section title="最近完成">
                  {finished.length
                    ? <RunList>{folds.map(({ run, runs }) => (
                        <SettledRun key={run.id} run={run} runs={runs}
                          followups={byParent.get(run.id)} />))}</RunList>
                    : null}
                  {more.error ? <Note tone="error">{errorMessage(more.error)}</Note> : null}
                  {hasEarlier
                    ? <Button variant="secondary" size="small" leadingIcon={RiArrowDownSLine}
                        className="self-start" {...busyProps(more.isPending)} onClick={loadEarlier}>
                        加载更早
                      </Button>
                    : null}
                </Section>
              : null}
          </>}
      {/* 离线下载跑在网盘那头，自己一张表、自己轮询；任务中心为空时它照样在。 */}
      <DownloadsPanel />
    </Page>
  );
}
