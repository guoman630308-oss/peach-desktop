/* 本机访问密码，由 BoardUI 原版组件组合；表单值只留在提交期间的组件内存中。
 *
 * 字段是 BoardUI `Input`（React Aria TextField），开关是 BoardUI `Checkbox`，提交键是 BoardUI `Button`；
 * 新密码与确认两框是与首启页共用的 `./password-pair`，分区外框与提示用配置页共用的 `./section`。 */
import { useRef, useState, type FormEvent } from 'react';

import { Button } from '@/components/base/buttons/button';
import { Checkbox } from '@/components/base/checkbox/checkbox';
import { Input } from '@/components/base/input/input';

import { ApiError, apiSend, errorMessage } from '../../api';
import type { AccessSettingsProps, AccessState } from '../bundle';
import { Note } from '../components/note';
import { PasswordPair } from './password-pair';
import { ErrorText, Footer, Help, Section, Stack } from './section';
import { busyProps, useAction } from './use-action';

type FieldErrors = Partial<Record<'current_password' | 'password' | 'confirmation', string>>;

const HELP: Partial<Record<AccessState['mode'], string>> = {
  legacy: '当前使用系统生成的访问口令。改设自己的密码，或关闭登录要求。',
  locked: '访问设置无法读取，请在本机检查配置文件。',
  password: '已设置密码。新设备需要登录，登录时勾选「保持登录」可保持 30 天。',
};

export function AccessSettings({ initial, receipt }: AccessSettingsProps) {
  const [state, setState] = useState(initial);
  const [current, setCurrent] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [disable, setDisable] = useState(false);
  const [fields, setFields] = useState<FieldErrors>({});
  const body = useRef<HTMLDivElement>(null);
  const action = useAction();

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const issues: FieldErrors = {};
    if (state.mode === 'password' && !current) issues.current_password = '请输入当前访问密码';
    if (!disable) {
      if (password.length < 8 || password.length > 256) issues.password = '访问密码需为 8–256 个字符';
      if (password !== confirmation) issues.confirmation = '两次输入的密码不一致';
    }
    setFields(issues);
    if (Object.keys(issues).length) {
      requestAnimationFrame(() => body.current?.querySelector<HTMLInputElement>('input[aria-invalid="true"]')?.focus());
      return;
    }
    void action.run('save', (signal) => apiSend<AccessState>('/api/configuration/access', {
      revision: state.revision, action: disable ? 'disable' : 'set', confirm_disable: disable,
      current_password: current, password: disable ? '' : password, confirmation: disable ? '' : confirmation,
    }, 'POST', signal), (next) => {
      setState(next); setCurrent(''); setPassword(''); setConfirmation(''); setDisable(false); setFields({});
      receipt(next.mode === 'open' ? '已关闭访问密码' : '已保存配置');
    }, (cause) => {
      const payload = cause instanceof ApiError ? cause.body as { errors?: FieldErrors; detail?: { errors?: FieldErrors } } : null;
      const errors = payload?.errors || payload?.detail?.errors;
      if (errors) setFields(errors); else action.setError(errorMessage(cause));
    });
  };

  const help = HELP[state.mode];
  const editable = state.mode !== 'locked';
  return (
    <Section title="访问密码" onSubmit={submit}>
      <div ref={body}>
        <Stack>
          {help ? <Help>{help}</Help> : null}
          {state.mode === 'open'
            ? <Note tone="warning" title="未设置访问密码">能连接到 Peach 的设备打开地址就能看馆藏，不需要登录。</Note>
            : null}
          {state.mode === 'password' || state.mode === 'legacy'
            ? <Checkbox isSelected={disable} onChange={setDisable}>关闭访问密码</Checkbox>
            : null}
          {state.mode === 'password'
            ? <Input id="access-current" type="password" label="当前访问密码" autoComplete="current-password" maxLength={256}
                value={current} onChange={setCurrent} isRequired validationBehavior="aria"
                isInvalid={Boolean(fields.current_password)} hint={fields.current_password} />
            : null}
          {editable
            ? <PasswordPair label={state.mode === 'password' ? '新访问密码' : '设置访问密码'}
                password={password} confirmation={confirmation} onPassword={setPassword} onConfirmation={setConfirmation}
                disabled={disable} passwordError={fields.password} confirmationError={fields.confirmation}
                passwordHint={disable ? '关闭访问密码时无需填写。' : '至少 8 个字符。保存后其他设备需要重新登录。'} />
            : null}
          {disable ? <Note tone="warning" title="访问范围">保存后，能连接到 Peach 的设备将直接访问馆藏。</Note> : null}
          {action.error ? <ErrorText>{action.error}</ErrorText> : null}
        </Stack>
      </div>
      {editable
        ? <Footer><Button type="submit" {...busyProps(action.busy === 'save')}>保存配置</Button></Footer>
        : null}
    </Section>
  );
}
