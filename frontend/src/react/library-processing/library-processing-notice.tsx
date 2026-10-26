/* 常驻任务面：状态、计数与控件共用服务端快照。位置由全站通知栈管理。 */
import { useEffect, useRef, useState } from 'react';
import { VisuallyHidden } from 'react-aria';
import { RiArrowRightLine, RiArrowDownSLine, RiArrowUpSLine, RiCheckLine, RiCheckboxBlankCircleLine, RiErrorWarningLine, RiInformationLine } from '@remixicon/react';
import { Button } from '@/components/base/buttons/button';
import { LinkButton } from '@/components/base/buttons/link-button';
import { cx } from '@/utils/cx';
import { errorMessage } from '../../api';
import type { LibraryProcessingProps } from '../bundle';
import { DISCONNECTED_TEXT, processingActive, remainingTime, type LibraryProcessingData } from './library-processing';
import { ProcessingControls } from './processing-controls';
import { useLibraryProcessingJob } from './use-library-processing';
import { ProcessingLine } from './processing-line';

const COLLAPSE_KEY = 'peach.library-processing.collapsed';
function expandedFor(jobId?: string): boolean {
  try { return !jobId || sessionStorage.getItem(COLLAPSE_KEY) !== jobId } catch { return true }
}

/** 圆环只表达当前阶段的实际完成率；未知总量使用静态任务图标。 */
function Gauge({ value, max }: { value: number; max: number }) {
  const percent = Math.max(0, Math.min(100, value / max * 100));
  return <svg role="progressbar" aria-label="当前阶段完成率" aria-valuemin={0} aria-valuemax={100}
    aria-valuenow={percent} viewBox="0 0 32 32" fill="none" className="size-8 shrink-0 -rotate-90 text-border-focus-ring">
    <circle cx="16" cy="16" r="13" stroke="currentColor" strokeWidth="3" className="opacity-20" />
    <circle cx="16" cy="16" r="13" stroke="currentColor" strokeWidth="3" pathLength={100}
      strokeDasharray={`${percent} 100`} strokeLinecap="round" />
  </svg>;
}

export function ProcessingNoticePanel({ state, problem = '', measure = false }: {
  state?: LibraryProcessingData; problem?: string; measure?: boolean;
}) {
  const [expanded, setExpanded] = useState(() => expandedFor(state?.job_id));
  const panel = useRef<HTMLElement>(null);
  const failed = state?.status === 'failed';
  const active = processingActive(state);
  useEffect(() => { setExpanded(expandedFor(state?.job_id)) }, [state?.job_id]);
  const toggle = () => {
    try {
      if (expanded && state?.job_id) sessionStorage.setItem(COLLAPSE_KEY, state.job_id);
      else sessionStorage.removeItem(COLLAPSE_KEY);
    } catch { /* 会话存储不可用时当前页面仍可收起 */ }
    setExpanded(!expanded);
  };
  useEffect(() => {
    if (!measure || !panel.current) return;
    const node = panel.current;
    let disposed = false;
    const update = () => {
      if (!disposed && node.isConnected)
        document.documentElement.style.setProperty('--processing-toast-space', `${node.getBoundingClientRect().height + 12}px`);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(node);
    return () => { disposed = true; observer.disconnect(); document.documentElement.style.removeProperty('--processing-toast-space') };
  }, [measure]);
  if (!problem && !active && !failed) return null;
  const warning = !!problem || !!state?.stalled;
  const title = failed ? '扫描与资料采集未完成' : state?.status === 'paused' ? '扫描与采集已暂停' : '扫描与采集';
  return <aside ref={panel} data-processing-dock="" data-processing-tone={failed ? 'error' : warning ? 'warning' : 'neutral'}
    aria-label="扫描任务" role={failed ? 'alert' : 'status'}
    className={cx('flex min-w-0 flex-col gap-3 border bg-background-primary-default p-4',
      failed ? 'border-current/35 text-text-error-primary'
        : warning ? 'border-current/35 text-status-yellow-text'
          : 'border-border-button-default text-text-primary')}>
    <div className="flex min-w-0 items-center gap-3">
      {active && state?.total ? <Gauge value={state.checked || 0} max={state.total} />
        : failed || warning ? <RiErrorWarningLine aria-hidden className="size-5 shrink-0" />
          : <RiInformationLine aria-hidden className="size-5 shrink-0" />}
      <div className="min-w-0 flex-1">
        <p className="text-body-medium">{title}</p>
        {active ? <p data-processing-eta="" className="text-caption-1-regular text-text-secondary tabular-nums wrap-anywhere">{remainingTime(state!)}</p> : null}
      </div>
      <Button size="small" variant="secondary" iconOnly leadingIcon={expanded ? RiArrowDownSLine : RiArrowUpSLine}
        aria-label={expanded ? '收起任务详情' : '展开任务详情'} aria-expanded={expanded} onClick={toggle} />
    </div>
    {expanded ? <div className={cx('flex min-w-0 flex-col gap-2 border-t pt-3',
      failed ? 'border-border-error-default/20' : warning ? 'border-current/20' : 'border-separator-border')}>
      {state?.steps?.length ? <ol aria-label="处理步骤" className="flex min-w-0 flex-col gap-1.5">
        {state.steps.map(step => <li key={step.id} data-processing-step={step.status}
          aria-current={step.status === 'current' ? 'step' : undefined}
          className={cx('flex min-h-8 items-center gap-2 text-body-2-regular',
            step.status === 'current' ? 'text-text-primary' : 'text-text-tertiary')}>
          {step.status === 'complete' ? <RiCheckLine aria-hidden className="size-4 shrink-0" />
            : <RiCheckboxBlankCircleLine aria-hidden className={cx('size-4 shrink-0', step.status === 'current' && 'text-border-focus-ring')} />}
          <VisuallyHidden>{step.status === 'complete' ? '已完成：' : step.status === 'current' ? '当前：' : '待处理：'}</VisuallyHidden>
          {step.status === 'complete' ? <s className="wrap-anywhere">{step.label}</s> : <span className="wrap-anywhere">{step.label}</span>}
        </li>)}
      </ol> : null}
      <p className="text-body-2-regular wrap-anywhere">{problem || (failed ? state?.error || '请查看并处理未完成项' : <ProcessingLine state={state!} />)}</p>
      {active && state?.stage && !state.steps?.length ? <p className="text-caption-1-regular text-text-tertiary">当前阶段 · {state.stage}</p> : null}
      {state?.stalled && !problem ? <p className="text-caption-1-regular">当前步骤耗时较长，正在等待进展</p> : null}
    </div> : null}
    <div className="flex flex-wrap items-center justify-between gap-2">
      {active ? <ProcessingControls state={state!} disabled={!!problem} /> : null}
      <LinkButton href="/data-cleanup#libraryProcessing" variant="secondary" size="small" trailingIcon={RiArrowRightLine}>
        {problem || failed ? '查看并处理' : '任务详情'}
      </LinkButton>
    </div>
  </aside>;
}

/** 独立任务面与数据管理卡片共享缓存和完成通知。 */
export function LibraryProcessingNotice(props: LibraryProcessingProps) {
  const { job } = useLibraryProcessingJob(props);
  return <ProcessingNoticePanel state={job.data} problem={job.isError ? (job.data ? DISCONNECTED_TEXT : errorMessage(job.error)) : ''} />;
}
