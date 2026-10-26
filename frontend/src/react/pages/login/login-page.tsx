/* 登录页：一张 Auth Card，一个访问密码框、「保持登录」与「登录」。
 *
 * 表单是原生提交：`POST /login` 收 `token`、`next`，勾选「保持登录」时多带 `days=30`，会话 cookie 由它的
 * 303 设下，页面不经脚本发请求。口令不对（401）、尝试太多（429）、保持登录时间不合法（400）时服务端
 * 回同一张壳，原因经 `data-invalid` 或 `data-error` 交进来，挂在密码框下面：框标成 `aria-invalid`，
 * 原因由 `role="alert"` 播报，焦点落在框里。改了框里的内容就撤掉这条原因：BoardUI 的 Input 走原生
 * 校验，标着错误时表单交不出去。 */
import { useState } from 'react';

import { Button } from '@/components/base/buttons/button';
import { Checkbox } from '@/components/base/checkbox/checkbox';
import { Input } from '@/components/base/input/input';

import { AuthCard } from '../auth-card';
import type { PageData } from '../page-data';

/** 口令不对时框下那句话。限流与参数错误的原因由服务端给。 */
export const WRONG_PASSWORD = '访问密码不正确';

export function LoginPage({ data }: { data: PageData }) {
  const [reason, setReason] = useState(data.error || (data.invalid === 'true' ? WRONG_PASSWORD : ''));
  return (
    <AuthCard title="Peach">
      <form method="post" action="/login" aria-labelledby="auth-card-title" className="flex flex-col gap-6">
        <Input id="login-token" label="访问密码" name="token" type="password" maxLength={256}
          autoComplete="current-password" autoFocus isRequired
          isInvalid={reason ? true : undefined} onChange={() => setReason('')}
          hint={reason ? <span role="alert">{reason}</span> : undefined} />
        <input type="hidden" name="next" value={data.next || '/'} />
        <Checkbox name="days" value="30" defaultSelected>保持登录</Checkbox>
        <Button type="submit" className="w-full">登录</Button>
      </form>
    </AuthCard>
  );
}
