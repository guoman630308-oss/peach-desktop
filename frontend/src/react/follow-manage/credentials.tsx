/* 来源和凭证：哪些站要登录、这台电脑上填没填、去哪儿取。
 *
 * 值不回显。服务端只报存在性，保存成功之后这一栏清空，看得到的只有字段名——凭据回到
 * 浏览器里就等于又多一处会被翻出来的副本。 */
import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { confirmModal } from '@peach/legacy/ui';

import { Button } from '@/components/base/buttons/button';
import { Chip } from '@/components/base/badges/chip';
import { Input } from '@/components/base/input/input';

import { errorMessage } from '../../api';
import { cardClass } from '../components/card';
import { EmptyState } from '../components/empty-state';
import { Note } from '../components/note';
import { PathLine } from '../components/path-line';
import { queryClient } from '../query';
import { busyProps } from '../settings/use-action';
import { Disclosure, ErrorText, ExternalLink, Help, Section, Stack } from '../settings/section';
import {
  CREDENTIAL_STATE, credentialDone, FOLLOW_CREDENTIALS_KEY, saveCredential,
  type CredentialData, type CredentialRow,
} from './follow-manage';
import { SourceIcon } from './source-view';
import { RiKey2Line } from '@remixicon/react';

const CLEAR_BODY = '将清除这个来源在本机和共享副本中的登录凭据。';
const WORLD_READABLE = '文件权限过宽。在运行 Peach 的 POSIX 主机上收紧为 0600。';

/** 一个字段此刻的样子：共享回填的、本机存过的，还是从来没填过。 */
function fieldHint(row: CredentialRow, name: string): string {
  if ((row.shared_fields || []).includes(name)) return '来自共享，留空表示不改';
  return row.fields.includes(name) ? '已保存，留空表示不改' : '未填写';
}

function StateChip({ row }: { row: CredentialRow }) {
  const done = credentialDone(row);
  if (done) return <Chip variant="caption" color="lime">已配置</Chip>;
  const color = row.requirement === 'required' ? 'yellow'
    : row.requirement === 'blocked' ? 'rose' : 'neutral';
  return <Chip variant="caption" color={color}>{CREDENTIAL_STATE[row.requirement] || ''}</Chip>;
}

function CredentialForm({ row, readOnly, toast }:
{ row: CredentialRow; readOnly: boolean; toast(message: string): void }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState('');
  const done = credentialDone(row);

  const write = useMutation({
    mutationFn: (next: Record<string, string>) => saveCredential(row.provider, next),
    onSuccess: (result, next) => {
      /* 共享盘不在时后端只撤掉了本机那一份，必须让用户看见：他以为撤干净了，等盘回来
         key 又被同步回来。 */
      if (result.note) { setProblem(result.note); return }
      setProblem('');
      setValues({});
      void queryClient.invalidateQueries({ queryKey: FOLLOW_CREDENTIALS_KEY, exact: true });
      toast(Object.keys(next).length ? '已保存来源凭据' : '已清除来源凭据');
    },
    onError: (cause) => setProblem(errorMessage(cause)),
  });

  const save = () => {
    const filled = Object.fromEntries(
      Object.entries(values).map(([name, value]) => [name, value.trim()]).filter(([, value]) => value));
    if (!Object.keys(filled).length) { setProblem('没有填写内容'); return }
    write.mutate(filled);
  };

  return (
    <div className="flex flex-col gap-3">
      <Help>
        {row.why}
        {/* 「去取」跳到那个站自己的页面，会离开 Peach，所以带外链标。 */}
        {row.where ? <> <ExternalLink href={row.where}>获取凭据</ExternalLink></> : null}
      </Help>
      {row.howto ? <Help>{row.howto}</Help> : null}
      <div className="flex w-full flex-col gap-3">
        {(row.needs || []).map((name) => (
          <Input key={name} type="password" label={name} placeholder={fieldHint(row, name)}
            autoComplete="off" spellCheck="false" isDisabled={readOnly}
            value={values[name] || ''} onChange={(value) => setValues({ ...values, [name]: value })}
            className="w-full min-w-0" />
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" size="small" disabled={readOnly} {...busyProps(write.isPending)}
          onClick={save}>保存配置</Button>
        {done ? (
          <Button variant="danger" size="small" disabled={readOnly} {...busyProps(write.isPending)}
            onClick={() => void confirmModal({
              title: '清除来源凭据', body: CLEAR_BODY, confirmLabel: '清除来源凭据', danger: true,
              onConfirm: () => write.mutateAsync({}),
            })}>清除</Button>
        ) : null}
      </div>
      {problem ? <ErrorText>{problem}</ErrorText> : null}
      {(row.shared_fields || []).length
        ? <Help>{`${row.shared_fields!.join('、')} 是从共享副本回填的，本机没有单独存。清除会把两边一起删。`}</Help>
        : null}
      {row.path
        ? <Disclosure summary="凭据文件"><PathLine path={row.path} className="font-mono text-caption-1-regular text-text-tertiary" onRevealed={toast} /></Disclosure>
        : null}
    </div>
  );
}

function CredentialSection({ row, readOnly, toast }:
{ row: CredentialRow; readOnly: boolean; toast(message: string): void }) {
  const head = (
    <span className="flex min-w-0 flex-wrap items-center gap-2">
      <span className="inline-flex size-3.5 shrink-0 items-center justify-center">
        <SourceIcon provider={row.provider} />
      </span>
      <b className="min-w-0 text-body-medium wrap-anywhere text-text-primary">{row.provider_label}</b>
      <StateChip row={row} />
      {row.missing.length
        ? <Chip variant="caption" color="rose">{`缺 ${row.missing.join('、')}`}</Chip>
        : null}
    </span>
  );
  if (row.requirement === 'none' || row.requirement === 'blocked') {
    return (
      <div className="flex flex-wrap items-center gap-3 border-b border-separator-border px-6 py-5 last:border-b-0 max-sm:px-4">
        {head}
        {row.requirement === 'blocked' ? <Help>{row.why}</Help> : null}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2 border-b border-separator-border px-6 py-5 last:border-b-0 max-sm:px-4">
      {head}
      <Disclosure summary={credentialDone(row) ? '修改凭据' : '填写凭据'}
        defaultOpen={row.requirement === 'required' && !credentialDone(row)}>
        <CredentialForm row={row} readOnly={readOnly} toast={toast} />
      </Disclosure>
      {row.world_readable ? <Note tone="error" title="凭据文件权限过宽">{WORLD_READABLE}</Note> : null}
    </div>
  );
}

export function Credentials(
  { data, readOnly, toast, embedded = false }:
  { data: CredentialData; readOnly: boolean; toast(message: string): void; embedded?: boolean },
) {
  const rows = data.providers || [];
  const pending = rows.filter((row) => row.requirement === 'required' && !credentialDone(row));
  if (!rows.length) {
    return <EmptyState shell="plain" icon={RiKey2Line} title="没有可配置的来源">已接入的站点及其凭据状态会显示在这里。</EmptyState>;
  }
  const pendingLabel = pending.length ? <span className="text-body-2-regular text-text-error-primary">{`${pending.length} 个待配置`}</span> : null;
  const groups = <>{[
        { title: '可配置凭据', rows: rows.filter((row) => row.requirement !== 'none' && row.requirement !== 'blocked') },
        { title: '不需要凭据', rows: rows.filter((row) => row.requirement === 'none') },
        { title: '暂不支持', rows: rows.filter((row) => row.requirement === 'blocked') },
      ].filter((group) => group.rows.length).map((group) => (
        <section key={group.title} aria-label={group.title} className="flex flex-col gap-2">
          <h4 className="text-body-medium text-text-secondary">{group.title}</h4>
          <div className={cardClass({ variant: 'outlined', padding: 'none', className: 'flex flex-col overflow-hidden' })}>
            {group.rows.map((row) => (
              <CredentialSection key={row.provider} row={row} readOnly={readOnly} toast={toast} />
            ))}
          </div>
        </section>
      ))}</>;
  return embedded ? <Section title="来源凭据" aside={pendingLabel || undefined}><Stack>{groups}</Stack></Section> :
    <div className={cardClass({ padding: 'none', className: 'flex flex-col gap-4 px-6 py-5 max-sm:px-4' })}>
      <div className="flex flex-wrap items-center gap-2"><h3 className="mr-auto text-title-2-medium text-text-primary">来源凭据</h3>{pendingLabel}</div>
      {groups}
    </div>;
}
