import { useEffect, useState, type FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/base/buttons/button';
import { Input } from '@/components/base/input/input';
import { apiSend, errorMessage } from '../../api';
import { LOCAL_PLAYER_URL, loadLocalPlayerPolicy, rememberLocalPlayerPolicy, type LocalPlayerPolicy } from '../../local-player';
import { SelectField } from '../settings-panel/legacy-controls';
import { ErrorText, Footer, Section, Stack } from './section';
import { busyProps, useAction } from './use-action';

export function LocalPlayerSettings({ receipt }: { receipt(message: string): void }) {
  const policy = useQuery({ queryKey: ['local-player'], queryFn: () => loadLocalPlayerPolicy() });
  const [mode, setMode] = useState('external');
  const [executable, setExecutable] = useState('');
  const action = useAction();
  useEffect(() => {
    if (policy.data) { setMode(policy.data.mode); setExecutable(policy.data.executable) }
  }, [policy.data]);
  if (!policy.data?.available) return policy.error ? <ErrorText>{errorMessage(policy.error)}</ErrorText> : null;
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void action.run('save', signal => apiSend<LocalPlayerPolicy>(LOCAL_PLAYER_URL, { mode, executable }, 'POST', signal), result => {
      rememberLocalPlayerPolicy(result); receipt('已保存播放器设置');
    });
  };
  return <Section title="播放器" onSubmit={submit}><Stack>
    <SelectField id="default-video-player" label="默认播放方式" epoch={0} value={mode}
      options={[["external", "外部播放器"], ["internal", "内置播放器"]]} onPick={setMode} />
    <Input label="外部播放器程序" value={executable} onChange={setExecutable}
      placeholder="例如 C:\\Program Files\\PotPlayer\\PotPlayerMini64.exe…" autoComplete="off" spellCheck="false" />
    {action.error ? <ErrorText>{action.error}</ErrorText> : null}
  </Stack><Footer><Button type="submit" {...busyProps(action.busy === 'save')}>保存配置</Button></Footer></Section>;
}
