/* 访问密码与确认两框：配置页「访问密码」与首启页共用。
 *
 * 两框都是 BoardUI `Input`，`id` 固定为 `access-password`、`access-confirm`，浏览器的密码管理器按
 * `new-password` 认它们。关掉时两框置灰、不必填也不标错；错误写在各自框下，`confirmationInvalid`
 * 让只有一句错误的调用方（首启页的 `access_password`）把确认框一起标红。错误原样放进框下的提示，
 * 调用方可以传一段带 `role="alert"` 的节点让读屏播报。 */
import type { ReactNode } from 'react';

import { Input } from '@/components/base/input/input';

export function PasswordPair({
  label, password, confirmation, onPassword, onConfirmation, disabled = false,
  passwordError, passwordHint, confirmationError, confirmationInvalid = false,
}: {
  label: string;
  password: string;
  confirmation: string;
  onPassword: (value: string) => void;
  onConfirmation: (value: string) => void;
  disabled?: boolean;
  passwordError?: ReactNode;
  passwordHint?: string;
  confirmationError?: ReactNode;
  confirmationInvalid?: boolean;
}) {
  return (
    <>
      <Input id="access-password" type="password" label={label}
        autoComplete="new-password" maxLength={256} value={password} onChange={onPassword}
        isDisabled={disabled} isRequired={!disabled} validationBehavior="aria"
        isInvalid={!disabled && Boolean(passwordError)}
        hint={disabled ? passwordHint : passwordError || passwordHint} />
      <Input id="access-confirm" type="password" label="确认访问密码" autoComplete="new-password" maxLength={256}
        value={confirmation} onChange={onConfirmation} isDisabled={disabled} isRequired={!disabled} validationBehavior="aria"
        isInvalid={!disabled && (Boolean(confirmationError) || confirmationInvalid)}
        hint={disabled ? undefined : confirmationError} />
    </>
  );
}
