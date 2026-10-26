/* JAV 用户脚本的默认下载渠道与专用令牌。 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { RiCheckboxBlankCircleFill } from '@remixicon/react';
import { Button } from '@/components/base/buttons/button';
import { Input } from '@/components/base/input/input';
import { Select, SelectItem } from '@/components/base/select/select';
import { apiGet, apiSend, errorMessage } from '../../api';
import { LoadingDots } from '../components/loading-dots';
import { SourceLabel } from '../components/source-label';
import { ErrorText, ExternalLink, FieldLabel, Footer, Help, Section, Stack } from './section';
import { busyProps, useAction } from './use-action';

const URL = '/api/configuration/jav-browser';
interface State {
  enabled: boolean; provider: string; available: boolean; token?: string;
  providers: { key: string; label: string; configured: boolean; target: string }[];
}

export function JavBrowserSettings({ receipt, refreshKey }: { receipt(message: string): void; refreshKey: string }) {
  const query = useQuery({ queryKey: ['jav-browser-settings', refreshKey],
    queryFn: ({ signal }) => apiGet<State>(URL, signal), refetchOnMount: 'always' });
  if (query.error) return <ErrorText>{errorMessage(query.error)}</ErrorText>;
  if (query.isFetching) return <LoadingDots label="正在读取浏览器添加设置" />;
  return query.data ? <BrowserForm initial={query.data} receipt={receipt} /> : null;
}

function BrowserForm({ initial, receipt }: { initial: State; receipt(message: string): void }) {
  const [state, setState] = useState(initial);
  const [provider, setProvider] = useState(initial.provider);
  const [token, setToken] = useState('');
  const action = useAction();
  const save = (kind: 'enable' | 'rotate' | 'disable') => {
    void action.run(kind, signal => apiSend<State>(URL, { action: kind, provider }, 'POST', signal), result => {
      const { token: issued = '', ...settings } = result;
      setState(settings);
      setToken(issued);
      receipt(kind === 'disable' ? '已关闭浏览器添加' : '已生成专用令牌');
    });
  };
  const ready = state.providers.some(item => item.key === provider && item.configured && item.target);
  return <Section title="浏览器添加 JAV">
    <Stack>
      <div><ExternalLink href="https://sleazyfork.org/zh-CN/scripts/600821">安装 Peach 用户脚本</ExternalLink></div>
      <div className="flex flex-col gap-1"><FieldLabel>浏览器添加的下载渠道</FieldLabel>
      <Select aria-label="浏览器添加的下载渠道" selectedKey={provider}
        onSelectionChange={key => setProvider(String(key))} isDisabled={Boolean(action.busy)}>
        {state.providers.map(item => <SelectItem key={item.key} id={item.key} textValue={item.label}>
          <SourceLabel source={item.key} label={item.label} />
        </SelectItem>)}
      </Select></div>
      {!ready ? <Help>请先保存所选网盘的账号和目标目录。</Help> : null}
      <Input label="Peach 地址" value={window.location.origin} isReadOnly autoComplete="off" />
      {token ? <>
        <Input label="浏览器添加专用令牌" type="password" value={token} isReadOnly autoComplete="new-password" />
        <div><Button variant="secondary" type="button" onClick={() => {
          void action.run('copy', async () => navigator.clipboard.writeText(token), () => receipt('已复制专用令牌'));
        }} {...busyProps(action.busy === 'copy')}>复制专用令牌</Button></div>
      </> : null}
      {action.error ? <ErrorText>{action.error}</ErrorText> : null}
    </Stack>
    <Footer status={<span role="status" className="inline-flex items-center gap-1.5">
      <span aria-hidden="true" className="inline-flex size-3 items-center justify-center">
        <RiCheckboxBlankCircleFill className={state.enabled ? 'size-2 text-notification-success-foreground' : 'size-2 text-text-tertiary'} />
      </span>{state.enabled ? '已启用' : '未启用'}
    </span>}>
      {state.enabled ? <Button variant="secondary" type="button" disabled={Boolean(action.busy)}
        onClick={() => save('disable')}>关闭浏览器添加</Button> : null}
      <Button type="button" disabled={!state.available || !ready || Boolean(action.busy)}
        onClick={() => save(state.enabled ? 'rotate' : 'enable')}
        {...busyProps(action.busy === 'enable' || action.busy === 'rotate')}>
        {state.enabled ? '更新渠道与令牌' : '启用并生成令牌'}
      </Button>
    </Footer>
  </Section>;
}
