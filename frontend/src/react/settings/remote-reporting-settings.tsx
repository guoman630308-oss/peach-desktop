import { useState } from 'react';

import { SettingsRow } from '@/components/application/settings/settings-rows';
import { Switch } from '@/components/base/switch/switch';

import { apiSend } from '../../api';
import { ErrorText, Help, Rows, Stack } from './section';
import { busyProps, useAction } from './use-action';

export interface RemoteReportingState {
  usage: boolean; errors: boolean; available: boolean;
  fields: { usage: string; errors: string }; notice: string;
}

export function ReportingChoices({ value, onChange, busy = false }: {
  value: RemoteReportingState; onChange(value: RemoteReportingState): void; busy?: boolean;
}) {
  return <>
    <Rows>
      <SettingsRow label="发送版本统计与错误诊断（推荐）" description="上传版本、系统信息、故障类型、代码位置与发生次数。">
        <Switch aria-label="发送版本统计与错误诊断（推荐）" isSelected={value.usage && value.errors}
          onChange={(enabled) => onChange({ ...value, usage: enabled, errors: enabled })} {...busyProps(busy)} />
      </SettingsRow>
    </Rows>
    {!value.available ? <Stack divided><Help>上传服务未配置。</Help></Stack> : null}
  </>;
}

export function RemoteReportingSettings({ initial, receipt }: {
  initial: RemoteReportingState; receipt(message: string): void;
}) {
  const [value, setValue] = useState(initial);
  const action = useAction();
  return <>
    <ReportingChoices value={value} busy={action.busy === 'save'} onChange={(next) => {
      void action.run('save', (signal) => apiSend<RemoteReportingState>('/api/configuration/remote-reporting',
      { usage: next.usage, errors: next.errors }, 'POST', signal), (saved) => {
      setValue(saved); receipt('已保存诊断选项');
    });
    }} />
    {action.error ? <Stack divided><ErrorText>{action.error}</ErrorText></Stack> : null}
  </>;
}
