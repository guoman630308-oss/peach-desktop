/* 「网络与访问」分组：本机端口、Peach 代理、访问密码与公网入口。 */
import { useState, type FormEvent } from 'react';

import { SettingsRow } from '@/components/application/settings/settings-rows';
import { Button } from '@/components/base/buttons/button';
import { Input } from '@/components/base/input/input';
import { Select, SelectItem } from '@/components/base/select/select';

import { apiSend } from '../../api';
import type { ConfigurationGroupProps, PeachProxyState } from '../bundle';
import { Note } from '../components/note';
import { AccessSettings } from './access-settings';
import { groupRoot } from './configuration-panel';
import { PortSettings } from './port-settings';
import { TunnelSettings } from './tunnel-settings';
import { ErrorText, Footer, Rows, Section, Stack } from './section';
import { busyProps, useAction } from './use-action';

const PROXY_MODES = [['environment', '系统代理'], ['direct', '直连'], ['proxy', '自定义']] as const;

export function NetworkSettings({ data, receipt, panel }: ConfigurationGroupProps) {
  return (
    <div {...groupRoot(panel)}>
      {data.editable && data.port_editable !== false ? <PortSettings data={data} receipt={receipt} /> : null}
      {data.peach_proxy ? <PeachProxy initial={data.peach_proxy} receipt={receipt} /> : null}
      {data.access ? <AccessSettings initial={data.access} receipt={receipt} /> : null}
      {data.tunnel ? <TunnelSettings revision={data.revision} initial={data.tunnel} receipt={receipt} /> : null}
    </div>
  );
}

export function PeachProxy({ initial, receipt }: { initial: PeachProxyState; receipt(message: string): void }) {
  const [saved, setSaved] = useState(initial);
  const [mode, setMode] = useState(initial.mode);
  const [address, setAddress] = useState('');
  const action = useAction();

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void action.run('save', (signal) => apiSend<PeachProxyState>('/api/configuration/peach-proxy', { mode, proxy: address }, 'POST', signal),
      (next) => { setSaved(next); setAddress(''); receipt('已保存配置'); });
  };

  return (
    <Section id="peachProxy" title="Peach 代理" onSubmit={submit}>
      <Rows>
        <SettingsRow label="连接方式">
          <Select aria-label="连接方式" selectedKey={mode} onSelectionChange={(key) => { if (key !== null) setMode(String(key)); }}>
            {PROXY_MODES.map(([key, name]) => <SelectItem key={key} id={key}>{name}</SelectItem>)}
          </Select>
        </SettingsRow>
      </Rows>
      {mode === 'proxy' || saved.needs_selection || action.error ? (
        <Stack divided>
          {mode === 'proxy'
            ? <Input id="peachProxyAddress" type="password" label="代理地址" autoComplete="off" value={address} onChange={setAddress}
                placeholder={saved.proxy_saved ? '已保存，留空保留' : 'http://127.0.0.1:7890'} />
            : null}
          {saved.needs_selection ? <Note tone="warning" title="需要选择连接方式">已有来源的代理地址不同，请选择公共连接方式。</Note> : null}
          {action.error ? <ErrorText>{action.error}</ErrorText> : null}
        </Stack>
      ) : null}
      <Footer>
        <Button type="submit" {...busyProps(action.busy === 'save')}>保存配置</Button>
      </Footer>
    </Section>
  );
}
