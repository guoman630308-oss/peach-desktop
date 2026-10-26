import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/base/buttons/button';
import { Checkbox } from '@/components/base/checkbox/checkbox';
import { Input } from '@/components/base/input/input';
import { confirmModal, spinnerHtml } from '@peach/legacy/ui';
import { esc } from '@peach/legacy/core';
import { apiSend, errorMessage } from '../../api';
import { queryClient } from '../query';
import { CodeBlock } from '../components/code-block';
import { ErrorText, FieldLabel, Footer, Help, Section, Stack } from '../settings/section';
import { busyProps, useAction } from '../settings/use-action';
import { SelectField } from '../settings-panel/legacy-controls';
import { CUSTOM_SOURCES_URL as URL, CUSTOM_SOURCES_KEY as KEY, fetchCustomSources,
  type CustomSource as Source } from './custom-source-data';

const FIELDS = [['code', '番号'], ['title', '标题'], ['studio', '厂牌'], ['series', '系列'],
  ['release_date', '发行日期'], ['performers', '演员（文本数组）'], ['tags', '标签（文本数组）']] as const;
const TYPES = [['censored', '有码'], ['amateur', '素人'], ['uncensored', '无码'], ['fc2', 'FC2'],
  ['kmib', '韩国 MIB'], ['other', '其他'], ['unsure', '待核番号'], ['unknown', '未知']] as const;
const blank = (): Source => ({ name: '', endpoint: '', enabled: false, fields: { code: 'id', title: 'title' }, content_types: ['censored'] });

export function CustomSources({ receipt }: { receipt(message: string): void }) {
  const [editing, setEditing] = useState<Source | null>(null);
  const query = useQuery({ queryKey: KEY, queryFn: ({ signal }) => fetchCustomSources(signal) });
  const action = useAction();
  const changed = (result: { items: Source[] }) => queryClient.setQueryData(KEY, result);
  const hasContent = Boolean(query.isPending || query.error || query.data?.items.length || action.error);
  const add = <Button variant="secondary" onClick={() => setEditing(blank())}>添加来源</Button>;
  return <Section title="自定义元数据来源">
    {hasContent ? <Stack>
      {query.isPending ? <div aria-busy="true" data-skeleton="" aria-label="读取自定义来源" /> : null}
      {query.error ? <ErrorText>{errorMessage(query.error)}</ErrorText> : null}
      {query.data?.items.map((source) => <div key={source.id} className="flex flex-wrap items-center gap-3">
        <span className="min-w-0 flex-1">{source.name} · {source.enabled ? '已启用' : '已停用'}</span>
        <Button variant="secondary" onClick={() => setEditing(source)}>编辑</Button>
        <Button variant="secondary" {...busyProps(action.busy === source.id)} onClick={() => void action.run(source.id!,
          (signal) => apiSend<{ items: Source[] }>(URL, { ...source, enabled: !source.enabled }, 'POST', signal),
          (result) => { changed(result); receipt(source.enabled ? '已停用来源' : '已启用来源') })}>
          {source.enabled ? '停用' : '启用'}</Button>
        <Button variant="danger" onClick={() => confirmModal({ title: '删除自定义来源',
          body: `删除「${esc(source.name)}」后，采集将不再查询这个来源。`, confirmLabel: '删除来源',
          onConfirm: async () => { changed(await apiSend<{ items: Source[] }>(URL, { action: 'delete', id: source.id }));
            setEditing(null); receipt('已删除来源') } })}>删除</Button>
      </div>)}
      {action.error ? <ErrorText>{action.error}</ErrorText> : null}
    </Stack> : null}
    {!editing ? hasContent ? <Footer>{add}</Footer> : <Stack><div className="flex justify-end">{add}</div></Stack> : null}
    {editing ? <SourceForm key={editing.id || 'new'} initial={editing} cancel={() => setEditing(null)}
      saved={(result) => { changed(result); setEditing(null); receipt('已保存来源') }} /> : null}
  </Section>;
}

function SourceForm({ initial, cancel, saved }: {
  initial: Source; cancel(): void; saved(result: { items: Source[] }): void;
}) {
  const [source, setSource] = useState(initial);
  const [code, setCode] = useState('');
  const [preview, setPreview] = useState<string>('');
  const revision = useRef(0);
  const action = useAction();
  const change = (patch: Partial<Source>) => { revision.current++; setSource((current) => ({ ...current, ...patch })); setPreview('') };
  return <form aria-label="元数据来源配置" aria-busy={action.busy === 'preview'} onSubmit={(event) => {
    event.preventDefault();
    void action.run('save', (signal) => apiSend<{ items: Source[] }>(URL, source, 'POST', signal), saved);
  }}>
    <Stack>
      <Input label="来源名称" value={source.name} maxLength={80} onChange={(name) => change({ name })} />
      <Input label="JSON 接口地址" value={source.endpoint} maxLength={2048}
        placeholder="https://example.com/api/movies/{code}" onChange={(endpoint) => change({ endpoint })} />
      <div className="flex flex-col gap-2">
        <FieldLabel>来源连接方式</FieldLabel>
        <SelectField id="custom-source-network" label="来源连接方式" epoch={0} value={source.network || 'direct'}
          options={[["direct", "直连"], ["peach_proxy", "Peach 代理"]]} onPick={(network) => change({ network })} />
      </div>
      <div className="flex flex-col gap-2">
        <FieldLabel>接口番号格式</FieldLabel>
        <SelectField id="custom-source-code-format" label="接口番号格式" epoch={0} value={source.code_format || 'original'}
          options={[["original", "原样"], ["lowercase", "小写"], ["compact", "去掉连字符"], ["underscore", "连字符转下划线"]]}
          onPick={(code_format) => change({ code_format })} />
      </div>
      <Help>填写公开 JSON 接口和响应字段路径，例如 data.title。番号字段用于核对作品身份。</Help>
      {FIELDS.map(([field, label]) => <Input key={field} label={`${label}字段路径`} value={source.fields[field] || ''}
        onChange={(path) => { const fields = { ...source.fields }; if (path) fields[field] = path; else delete fields[field]; change({ fields }) }} />)}
      <div className="flex flex-wrap gap-3" role="group" aria-label="适用内容类型">
        {TYPES.map(([type, label]) => <Checkbox key={type} isSelected={source.content_types.includes(type)}
          onChange={(checked) => change({ content_types: checked ? [...source.content_types, type] : source.content_types.filter((kind) => kind !== type) })}>{label}</Checkbox>)}
      </div>
      <Checkbox isSelected={source.enabled} onChange={(enabled) => change({ enabled })}>启用来源</Checkbox>
      <Input label="试查番号（回车查询）" value={code} onChange={(value) => {
        revision.current++; setCode(value); setPreview('');
      }} onKeyDown={(event) => {
        if (event.key !== 'Enter' || event.nativeEvent.isComposing) return;
        event.preventDefault();
        const current = revision.current;
        void action.run('preview', (signal) => apiSend<{ record: unknown }>(URL,
          { ...source, code, action: 'preview' }, 'POST', signal), (result) => {
            if (current === revision.current) setPreview(JSON.stringify(result.record, null, 2));
          }, (cause) => { if (current === revision.current) action.setError(errorMessage(cause)) });
      }} />
      {action.busy === 'preview' ? <Help role="status"><span dangerouslySetInnerHTML={{ __html: spinnerHtml('正在试查来源') }} />正在试查来源…</Help> : null}
      {preview ? <CodeBlock code={preview} language="json" label="元数据接口返回" /> : null}
      {action.error ? <ErrorText>{action.error}</ErrorText> : null}
    </Stack>
    <Footer><Button variant="secondary" type="button" onClick={cancel}>取消</Button>
      <Button type="submit" {...busyProps(action.busy === 'save')}>保存来源</Button></Footer>
  </form>;
}
