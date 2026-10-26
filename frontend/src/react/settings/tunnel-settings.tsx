/* Cloudflare Tunnel：临时链接显示服务实际返回的随机入口，命名隧道显示配置好的公开主机名。
 *
 * 临时链接没有稳定域名和独立身份层，服务端会在 `open` 模式下拒绝启动。命名隧道多一份
 * 隧道令牌：它只往服务端去，回来的只有「存过没有」，这里任何时候都不显示令牌本身。
 * 模式选择只在源码开发环境出现，独立包收到的 `named_available` 是 false。 */
import { useEffect, useState, type FormEvent } from 'react';
import { Radio } from 'react-aria-components';

import { Button } from '@/components/base/buttons/button';
import { Input } from '@/components/base/input/input';

import { ApiError, apiSend, errorMessage } from '../../api';
import type { ConfigurationData, TunnelState } from '../bundle';
import { Note } from '../components/note';
import { queryClient } from '../query';
import { SEGMENT, SEGMENTED_TRACK, SegmentedRadioGroup as RadioGroup } from '../components/segmented';
import { CONFIGURATION_KEY, fetchConfiguration } from './configuration';
import { ExternalLink, ErrorText, Footer, Help, Section, Stack } from './section';
import { busyProps, useAction } from './use-action';

/** 连接握手在服务端进行，页面按固定间隔重问，直到拿到入口或失败原因。 */
const POLL_INTERVAL = 2000;

const MODES: [TunnelState['mode'], string][] = [['quick', '临时链接'], ['named', '命名隧道']];

const HELP: Record<TunnelState['mode'], string> = {
  quick: '临时预览用，地址随机，重启后会变。启动前要先设置访问密码。',
  named: '使用你在 Cloudflare 绑定的固定域名。启动前要先设置访问密码。',
};

type FieldErrors = Partial<Record<'hostname' | 'mode', string>>;

export function TunnelSettings({ revision: initialRevision, initial, receipt }: {
  revision: string;
  initial: TunnelState;
  receipt(message: string): void;
}) {
  const [state, setState] = useState(initial);
  const [revision, setRevision] = useState(initialRevision);
  const [mode, setMode] = useState<TunnelState['mode']>(initial.mode);
  const [hostname, setHostname] = useState(initial.hostname);
  const [token, setToken] = useState('');
  const [fields, setFields] = useState<FieldErrors>({});
  const action = useAction();
  const active = state.state === 'starting' || state.state === 'running';
  const named = state.named_available && mode === 'named';

  useEffect(() => {
    if (state.state !== 'starting') return undefined;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      void fetchConfiguration(controller.signal)
        .then((next: ConfigurationData) => {
          if (controller.signal.aborted || !next.tunnel) return;
          // 取回来的是整份配置，换进整页那一个键：屏幕上只有一份真相。
          queryClient.setQueryData(CONFIGURATION_KEY, next);
          setState(next.tunnel);
          setRevision(next.revision);
        })
        .catch(() => undefined);
    }, POLL_INTERVAL);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [state.state]);

  /** 开关和保存走同一个端点：形态与开关状态一次提交，服务端据此构造启动计划。 */
  const send = (key: 'save' | 'toggle', enabled: boolean, done: (next: TunnelState) => void) => {
    setFields({});
    void action.run(key, (signal) => apiSend<TunnelState & { revision: string }>(
      '/api/configuration/tunnel',
      { revision, enabled, mode, hostname, token }, 'POST', signal,
    ), (next) => {
      setState(next);
      setRevision(next.revision);
      setMode(next.mode);
      setHostname(next.hostname);
      // 令牌交上去之后草稿就清掉：页面上不留一份明文，回来的也只有「存过没有」。
      setToken('');
      done(next);
    }, (cause) => {
      const payload = cause instanceof ApiError
        ? cause.body as { errors?: FieldErrors; detail?: { errors?: FieldErrors } } : null;
      const errors = payload?.errors || payload?.detail?.errors;
      if (errors) setFields(errors); else action.setError(errorMessage(cause));
    });
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    send('save', active, () => receipt('已保存配置'));
  };

  const toggle = () => send('toggle', !active, (next) => receipt(
    next.enabled ? '公网入口已启动' : '公网入口已停止',
  ));

  return (
    <Section title="Cloudflare 公网入口" onSubmit={submit}>
      <Stack>
        {state.named_available
          ? <RadioGroup aria-label="公网入口的形态（二选一）" value={mode} isDisabled={active}
              onChange={(next) => setMode(next as TunnelState['mode'])} className={SEGMENTED_TRACK}>
              {MODES.map(([value, label]) => (
                <Radio key={value} value={value} className={SEGMENT}>{label}</Radio>
              ))}
            </RadioGroup>
          : null}
        <Help>{HELP[named ? 'named' : 'quick']}</Help>
        {named ? <>
          <Input id="tunnel-hostname" label="公开主机名" autoComplete="off" maxLength={253}
            value={hostname} onChange={setHostname} isDisabled={active} validationBehavior="aria"
            isInvalid={Boolean(fields.hostname)}
            hint={fields.hostname || 'Cloudflare Zero Trust 里指向这台电脑的主机名。'} />
          <Input id="tunnel-token" type="password" label="隧道令牌" autoComplete="off" maxLength={2048}
            value={token} onChange={setToken} isDisabled={active} validationBehavior="aria"
            hint={state.token_set
              ? '已保存，留空不改。'
              : '在 Cloudflare Zero Trust 的隧道详情页复制。'} />
        </> : null}
        {!state.available
          ? <Note tone="warning" title="找不到 cloudflared">安装官方 cloudflared，或用设置文件的 tunnel.binary 与 PEACH_CLOUDFLARED 指定路径。</Note>
          : null}
        {state.state === 'running' && state.url
          ? <Note tone="info" title="公网地址"><ExternalLink href={state.url}>{state.url}</ExternalLink></Note>
          : null}
        {state.state === 'starting' ? <Note tone="info" title="正在连接">正在等待 cloudflared 连上 Cloudflare 边缘。</Note> : null}
        {state.error ? <ErrorText>{state.error}</ErrorText> : null}
        {action.error ? <ErrorText>{action.error}</ErrorText> : null}
      </Stack>
      <Footer status={state.enabled ? 'Peach 重启后自动恢复。' : undefined}>
        <Button variant={state.named_available ? 'secondary' : 'primary'} onClick={toggle} {...busyProps(action.busy === 'toggle')}>
          {active ? '停止公网入口' : '启动公网入口'}
        </Button>
        {state.named_available
          ? <Button type="submit" {...busyProps(action.busy === 'save')}>保存配置</Button>
          : null}
      </Footer>
    </Section>
  );
}
