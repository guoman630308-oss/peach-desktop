import { useEffect, useRef } from 'react';
import { toast } from 'sonner';
import type { LibraryProcessingProps } from '../bundle';
import { errorMessage } from '../../api';
import { DISCONNECTED_TEXT, freshProcessingResult, processingActive } from './library-processing';
import { ProcessingNoticePanel } from './library-processing-notice';
import { useLibraryProcessingJob } from './use-library-processing';

const TASK_TOAST = 'peach-library-processing';

/** 路由根持续观察，任务浮层不随换页卸载；普通回执使用有独立目标的栈。 */
export function LibraryProcessingDock({ toast: receipt }: Pick<LibraryProcessingProps, 'toast'>) {
  const { job, settled } = useLibraryProcessingJob({ toast: receipt, mode: 'notice' });
  const dismissed = useRef('');
  const state = job.data;
  const problem = job.isError ? state ? DISCONNECTED_TEXT : errorMessage(job.error) : '';
  useEffect(() => {
    const active = processingActive(state);
    const key = state?.job_id || 'unavailable';
    const currentFailure = state?.status === 'failed'
      && (settled?.job_id === state.job_id || freshProcessingResult(state));
    if (!active && ((!problem && !currentFailure) || dismissed.current === key)) {
      toast.dismiss(TASK_TOAST);
      return;
    }
    toast.custom(() => <div className="peach-react"><ProcessingNoticePanel state={state} problem={problem} measure /></div>, {
      id: TASK_TOAST, toasterId: 'processing', duration: Infinity, dismissible: !active,
      closeButton: !active, className: 'processing-task-toast',
      onDismiss: () => { dismissed.current = key },
    });
  }, [state, problem, settled]);
  useEffect(() => () => { toast.dismiss(TASK_TOAST) }, []);
  return null;
}
