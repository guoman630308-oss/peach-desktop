/* 用户自配索引器；地址和 API key 分开保存，读取时只返回凭据是否存在。 */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/base/buttons/button';
import { Checkbox } from '@/components/base/checkbox/checkbox';
import { Input } from '@/components/base/input/input';
import { apiGet, apiSend, errorMessage } from '../../api';
import { LoadingDots } from '../components/loading-dots';
import { Disclosure, ErrorText, ExternalLink, Footer, Help, Section, Stack } from './section';
import { busyProps, useAction } from './use-action';

const URL = '/api/configuration/indexers';
interface Indexer {
  key: string; name: string; url: string; enabled: boolean; api_key_set: boolean;
  api_key?: string; clear_api_key?: boolean;
}
interface State { indexers: Indexer[]; max_indexers: number }

export function IndexerSettings({ receipt }: { receipt(message: string): void }) {
  const query = useQuery({ queryKey: ['indexers'], queryFn: ({ signal }) => apiGet<State>(URL, signal), refetchOnMount: 'always' });
  if (query.error) return <ErrorText>{errorMessage(query.error)}</ErrorText>;
  if (query.isFetching) return <LoadingDots label="正在读取资源搜索设置" />;
  return query.data ? <IndexerForm initial={query.data} receipt={receipt} /> : null;
}

function IndexerForm({ initial, receipt }: { initial: State; receipt(message: string): void }) {
  const [rows, setRows] = useState(initial.indexers);
  const action = useAction();
  const update = (key: string, fields: Partial<Indexer>) =>
    setRows((current) => current.map((row) => row.key === key ? { ...row, ...fields } : row));
  const save = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void action.run('save', (signal) => apiSend<State>(URL, { indexers: rows }, 'POST', signal), (result) => {
      setRows(result.indexers);
      receipt('已保存索引器');
    });
  };
  return (
    <Section title="资源搜索" onSubmit={save}>
      <Stack>
        <Disclosure summary="索引器连接帮助"><Help>填写 Prowlarr 或 Jackett 提供的 Torznab 地址和 API key。</Help>
          <ExternalLink href="https://github.com/peach-mitao/peach/blob/master/docs/RESOURCE_SEARCH.md">详细配置说明</ExternalLink>
        </Disclosure>
        {rows.map((row, index) => (
          <div key={row.key} className="flex min-w-0 flex-col gap-4 border-t border-separator-border pt-4">
            <Input label={`索引器 ${index + 1} 名称`} value={row.name} maxLength={80}
              isDisabled={Boolean(action.busy)} onChange={(name) => update(row.key, { name })} />
            <Input label={`索引器 ${index + 1} Torznab 地址`} value={row.url} type="url" autoComplete="off"
              placeholder="索引器提供的 API 地址" isDisabled={Boolean(action.busy)}
              onChange={(url) => update(row.key, { url })} />
            <Input label={`索引器 ${index + 1} API key`} type="password" autoComplete="new-password"
              value={row.api_key || ''} placeholder={row.api_key_set ? '已保存，留空不改' : undefined}
              isDisabled={Boolean(action.busy)} onChange={(api_key) => update(row.key, { api_key })} />
            <Checkbox isSelected={row.enabled} isDisabled={Boolean(action.busy)}
              onChange={(enabled) => update(row.key, { enabled })}>启用</Checkbox>
            {row.api_key_set ? <Checkbox isSelected={Boolean(row.clear_api_key)}
              isDisabled={Boolean(action.busy)} onChange={(clear_api_key) => update(row.key, { clear_api_key })}>
              清除已保存的 API key
            </Checkbox> : null}
            <div><Button variant="danger" type="button" disabled={Boolean(action.busy)}
              onClick={() => setRows(rows.filter((item) => item.key !== row.key))}>移除索引器 {index + 1}</Button></div>
          </div>
        ))}
        {action.error ? <ErrorText>{action.error}</ErrorText> : null}
      </Stack>
      <Footer>
        <Button variant="secondary" type="button" disabled={Boolean(action.busy) || rows.length >= initial.max_indexers}
          onClick={() => setRows([...rows, { key: crypto.randomUUID(), name: '', url: '', enabled: true, api_key_set: false }])}>
          添加索引器
        </Button>
        <Button type="submit" {...busyProps(action.busy === 'save')}>保存配置</Button>
      </Footer>
    </Section>
  );
}
