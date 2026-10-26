import { Button } from '@/components/base/buttons/button';
import { RiPauseLine, RiPlayLine } from '@remixicon/react';

import { apiSend } from '../../api';
import { ProcessingMode } from './processing-effort';
import { queryClient } from '../query';
import { busyProps, useAction } from '../settings/use-action';
import {
  LIBRARY_PROCESSING_KEY, LIBRARY_PROCESSING_URL, processingActive,
  type LibraryProcessingData, type ProcessingControlCommand,
} from './library-processing';

/** 控制只作用于快照里的任务号；状态由服务端确认后交给所有入口。 */
export function ProcessingControls({ state, disabled = false }: {
  state: LibraryProcessingData; disabled?: boolean;
}) {
  const action = useAction();
  const active = processingActive(state);
  const controlLabel = state.status === 'paused' ? '继续' : state.status === 'pausing' ? '正在暂停' : '暂停';
  const change = (command: ProcessingControlCommand) => {
    if (disabled || action.busy) return;
    void action.run(command.action, signal =>
      apiSend<LibraryProcessingData>(LIBRARY_PROCESSING_URL, command, 'POST', signal), next => {
        queryClient.setQueryData(LIBRARY_PROCESSING_KEY, next);
        void queryClient.invalidateQueries({ queryKey: LIBRARY_PROCESSING_KEY });
      });
  };
  return <div className="flex min-w-0 flex-col gap-2" data-processing-controls="">
    <div className="flex flex-wrap items-center gap-2">
      <ProcessingMode value={state.speed_mode || 'standard'} busy={disabled || !!action.busy}
        animate={state.status !== 'paused' && state.status !== 'pausing'}
        onChange={speed_mode => change({ action: 'set_speed', speed_mode, ...(active ? { job_id: state.job_id } : {}) })} />
      {active ? <Button size="small" variant="secondary" iconOnly
        leadingIcon={state.status === 'paused' ? RiPlayLine : RiPauseLine}
        aria-label={controlLabel} title={controlLabel}
        disabled={disabled || !state.job_id || state.status === 'pausing'}
        {...busyProps(action.busy === 'pause' || action.busy === 'resume')}
        onClick={() => {
          if (state.job_id) change({ action: state.status === 'paused' ? 'resume' : 'pause', job_id: state.job_id });
        }} /> : null}
    </div>
    {action.error ? <p role="alert" className="text-caption-1-regular text-text-error-primary">{action.error}</p> : null}
  </div>;
}
