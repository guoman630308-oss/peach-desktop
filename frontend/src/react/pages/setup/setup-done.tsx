/* 设置完成：扫描是否已排队、进入 Peach 的入口，运行信息默认折叠。口令不出现在页面上。
 *
 * 独立包的服务端在 `redirect` 里给入口地址，过 `RESTART_REDIRECT_MS` 自己跳过去，与配置页保存后
 * 等托盘换进程的时长是同一个数。 */
import { useEffect, useRef } from 'react';

import { ButtonLink } from '@/components/base/buttons/button';

import { RESTART_REDIRECT_MS } from '../../restart-redirect';
import { Disclosure, ExternalLink, Fact, FactList } from '../../settings/section';
import { AuthCard } from '../auth-card';
import type { SetupDone } from './setup-api';

export function SetupDoneView({ done }: { done: SetupDone }) {
  const entry = useRef<HTMLAnchorElement>(null);

  useEffect(() => {
    document.title = 'Peach · 设置完成';
    entry.current?.focus();
  }, []);

  useEffect(() => {
    const target = done.redirect;
    if (!target) return undefined;
    const timer = setTimeout(() => location.assign(target), RESTART_REDIRECT_MS);
    return () => clearTimeout(timer);
  }, [done.redirect]);

  return (
    <AuthCard title="设置完成" lede="正在启动馆藏。">
      <p className="text-body-regular text-text-primary">
        {done.restored ? '已连接已有数据目录。' : done.scan_requested
          ? '首次扫描已排队，在后台整理媒体库，期间可以照常使用 Peach。'
          : '稍后在配置页开始扫描媒体库。'}
      </p>
      <ButtonLink ref={entry} href={done.url} className="self-start">
        {done.history_guide ? '导入浏览器历史记录' : '进入 Peach'}
      </ButtonLink>
      <Disclosure summary="运行信息">
        <FactList>
          {done.facts.map((fact) => (
            <Fact key={fact.term} term={fact.term}>
              {fact.value}
              {fact.download_url
                ? <ExternalLink href={fact.download_url}>{fact.download_label ?? fact.download_url}</ExternalLink>
                : null}
            </Fact>
          ))}
        </FactList>
      </Disclosure>
    </AuthCard>
  );
}
