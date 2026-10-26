import { esc } from '@peach/legacy/core';
import { noteHtml, loadingDotsHtml } from '@peach/legacy/ui';

/** 任务那枚环形进度。口味页与统计页归 React 之后只剩这一个读者，所以留在它身边。 */
function jobProgressHtml(label: string, value: number, max: number): string {
  if (!Number.isFinite(max) || max <= 0) return '';
  const done = Math.max(0, Math.min(max, Number.isFinite(value) ? value : 0));
  const percent = done / max * 100;
  return `<div class="ui-board-job-progress" role="progressbar" aria-label="${esc(label)}" aria-valuemin="0" aria-valuemax="${max}" aria-valuenow="${done}"><svg width="36" height="36" viewBox="0 0 36 36" aria-hidden="true"><circle class="ui-board-job-track" cx="18" cy="18" r="15"/><circle class="ui-board-job-fill" cx="18" cy="18" r="15" pathLength="100" stroke-dasharray="${percent} ${100 - percent}" transform="rotate(-90 18 18)"/></svg><span>${esc(label)}<small>${Math.round(percent)}%</small></span></div>`;
}

/** 后台任务查询只重试读状态；启动和写入请求由调用方单次提交。 */
export interface JobState {
  status: string;
  job_id?: string;
  checked?: number;
  total?: number;
  older?: boolean;
  error?: string;
  message?: string;
  current?: { label?: string; provider?: string; attempt?: number;
    max_attempts?: number; retry_in?: number };
}

/** 正在推进的任务用真实计数；总量未取得时显示状态文字。 */
export function jobActivityHtml(label: string, value = 0, total = 0): string {
  return total > 0 ? jobProgressHtml(label, value, total) : loadingDotsHtml(label);
}

export async function watchJob<T extends JobState>(options: {
  read: (signal: AbortSignal) => Promise<T>;
  active: () => boolean;
  render: (state: T) => void;
  disconnected: (error: unknown) => void;
  keepWatching?: boolean;
  once?: boolean;
  pause?: (ms: number) => Promise<void>;
}): Promise<void> {
  const pause = options.pause || (ms => new Promise(resolve => setTimeout(resolve, ms)));
  let failures = 0;
  while (options.active()) {
    let state: T;
    try {
      state = await options.read(AbortSignal.timeout(15000));
    } catch (error) {
      if (!options.active()) return;
      failures++;
      options.disconnected(error);
      await pause(Math.min(2000 * 2 ** Math.min(failures, 4), 30000));
      continue;
    }
    if (!options.active()) return;
    failures = 0;
    options.render(state);
    if (options.once) return;
    if (!options.keepWatching && state.status !== 'running') return;
    await pause(2000);
  }
}

export function followJobProgress(options: {
  host: HTMLElement;
  active: () => boolean;
  read: (signal: AbortSignal) => Promise<JobState>;
  busy: (running: boolean) => void;
  complete: (state: JobState) => void;
  note?: (text: string) => string;
  loading?: (text: string) => string;
  progress?: (value: number, max: number, label?: string) => string;
  container?: (content: string) => string;
  storageKey?: string;
  title?: string;
  watchIdle?: boolean;
}): void {
  const note=options.note || (text=>noteHtml(text,{label:'任务状态',variant:'error'}));
  const loading=options.loading || loadingDotsHtml;
  const progress=options.progress || ((value,max,label)=>jobActivityHtml(label||`已处理 ${value} / ${max}`,value,max));
  const container=options.container || (content=>`<section class="followtask" data-geist-fieldset aria-label="任务进度"><div class="geist-fieldset-content">${content}</div></section>`);
  const panel = document.createElement('div');
  options.host.hidden = true;
  panel.dataset.followJob = '';
  panel.setAttribute('aria-live', 'polite');
  options.host.prepend(panel);
  const storageKey = options.storageKey || 'peach-follow-job';
  let tracked: string | undefined = sessionStorage.getItem(storageKey) || undefined;
  let settled = false;
  void watchJob({
    read: options.read,
    active: () => !settled && options.active() && panel.isConnected,
    keepWatching: options.watchIdle !== false,
    render: state => {
      const running = state.status === 'running';
      options.host.hidden = !running;
      options.busy(running);
      if (running) {
        tracked = state.job_id;
        if (tracked) sessionStorage.setItem(storageKey, tracked);
        const current = state.current;
        const attempt = (current?.attempt || 1) > 1
          ? ` · 第 ${current?.attempt}/${current?.max_attempts} 次尝试${current?.retry_in ? `，${current.retry_in} 秒后重试` : ''}` : '';
        const text = (state.message || (options.title ? options.title + (state.total ? `：已完成 ${state.checked || 0}/${state.total}` : '') : '') || (state.total
          ? `${state.older ? '抓取历史' : '检查更新'}：已完成 ${state.checked || 0}/${state.total} 个来源`
          : '正在准备检查任务…'))
          + (current ? ` · ${current.label || current.provider || ''}${attempt}` : '');
        const content = (state.total || 0) > 0
          ? progress(state.checked || 0, state.total!, text) : loading(text);
        panel.innerHTML = container(content);
      } else if (tracked && tracked === state.job_id) {
        tracked = undefined;
        settled = true;
        options.host.hidden = state.status !== 'failed';
        sessionStorage.removeItem(storageKey);
        panel.innerHTML = state.status === 'failed' ? note(state.error || '检查失败') : '';
        options.complete(state);
      } else {
        if (tracked && state.status === 'idle') {
          options.host.hidden = false;
          panel.innerHTML = note('任务状态已失效，请重新发起任务');
          sessionStorage.removeItem(storageKey);
          settled = true;
          return;
        }
        panel.innerHTML = '';
      }
    },
    disconnected: () => {
      options.host.hidden = false;
      panel.innerHTML = note('暂时无法读取进度，正在重新连接…');
    },
  });
}
