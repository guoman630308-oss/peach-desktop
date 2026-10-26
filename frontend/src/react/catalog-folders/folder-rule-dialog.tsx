import { useContext, useState } from 'react';
import { flushSync } from 'react-dom';
import { Heading } from 'react-aria-components';
import { Button } from '@/components/base/buttons/button';
import { Checkbox } from '@/components/base/checkbox/checkbox';
import { Input } from '@/components/base/input/input';
import { Label } from '@/components/base/input/label';
import { Select, SelectItem } from '@/components/base/select/select';
import { confirmModal, spinnerHtml } from '@peach/legacy/ui';
import { pageOpens, writeShell } from '@peach/shell';
import { apiSend } from '../../api';
import { ModalFrame } from '../components/modal-frame';
import { Note } from '../components/note';
import { SettingsDialogClose } from '../components/settings-dialog-close';
import { queryClient } from '../query';
import { busyProps, useAction } from '../settings/use-action';
import { ShellActionsContext } from '../router/router';
import { PURPOSES, type FolderPurpose, type FolderRule, type MediaFolder } from './catalog-folders';

interface Preview {
  plan_version: string; total: number; applicable: number; withdrawn: number;
  conflicts: { id: number; name: string; reason: string }[];
  sample: { id: number; name: string }[];
}

export function FolderRuleDialog({ folder, rule, revision, close }: {
  folder: MediaFolder; rule: FolderRule | null; revision: number; close(): void;
}) {
  const actions = useContext(ShellActionsContext);
  const action = useAction();
  const [purpose, setPurpose] = useState<FolderPurpose>(rule?.purpose || 'category');
  const [name, setName] = useState(rule?.name || '');
  const [recursive, setRecursive] = useState(rule?.recursive ?? true);
  const [future, setFuture] = useState(rule?.future ?? false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [confirming, setConfirming] = useState(false);
  const body = { folder: folder.id, purpose, name, recursive, future };
  const dirty = () => { setPreview(null); action.setError(''); };
  const finished = (message: string) => {
    void queryClient.invalidateQueries({ queryKey: ['catalog-folders'] });
    actions?.receipt(message);
    close();
    writeShell({ pageOpens: pageOpens + 1 });
  };
  const showPreview = () => action.run('preview', signal => apiSend<Preview>('/api/folders/preview', body, 'POST', signal), setPreview);
  const save = () => {
    if (!preview) return;
    void action.run('save', signal => apiSend<{ applied: number; conflicts: number }>('/api/folders/rule', {
      ...body, action: 'save', plan_version: preview.plan_version, expected_revision: revision,
    }, 'POST', signal), result => finished(`已保存目录用途${result.applied ? `，关联 ${result.applied} 项` : ''}`));
  };
  const remove = async () => {
    flushSync(() => setConfirming(true));
    const result = await confirmModal({
      title: '移除目录规则', body: `「${folder.name}」的目录规则将被移除，并撤回这条规则建立的关联。`, confirmLabel: '移除目录规则',
      onConfirm: () => apiSend('/api/folders/rule', { folder: folder.id, action: 'remove', expected_revision: revision }),
    });
    if (result.confirmed) finished('已移除目录规则'); else setConfirming(false);
  };
  const busy = !!action.busy;
  const waiting = (key: string, label: string) => action.busy === key
    ? <><span dangerouslySetInnerHTML={{ __html: spinnerHtml(label) }} />{label}</> : null;
  if (confirming) return null;
  return <ModalFrame isOpen onOpenChange={open => { if (!open && !busy) close() }} width="form" label="设置目录用途">
    <div className="peach-react">
      <div className="flex items-start justify-between gap-4 border-b border-separator-border p-5">
        <div className="min-w-0"><Heading slot="title" className="text-title-2-medium">设置目录用途</Heading>
          <p className="mt-1 text-body-2-regular text-text-secondary wrap-anywhere">{folder.path}</p></div>
        <SettingsDialogClose label="关闭目录用途" onPress={() => { if (!busy) close() }} />
      </div>
      <div className="flex flex-col gap-5 p-5">
        <div className="flex flex-col gap-1"><Label>这个文件夹用于</Label>
          <Select aria-label="这个文件夹用于" selectedKey={purpose} isDisabled={busy}
            onSelectionChange={key => { setPurpose(key as FolderPurpose); dirty() }}>
            {Object.entries(PURPOSES).map(([key, label]) => <SelectItem key={key} id={key}>{label}</SelectItem>)}
          </Select>
        </div>
        {purpose !== 'category' ? <Input label="关联名称" value={name} onChange={value => { setName(value); dirty() }}
          placeholder="输入人物、账号或厂牌名称" maxLength={200} isDisabled={busy} isRequired /> : null}
        <Checkbox isSelected={recursive} isDisabled={busy} onChange={value => { setRecursive(value); dirty() }}>应用到子文件夹</Checkbox>
        <Checkbox isSelected={future} isDisabled={busy} onChange={value => { setFuture(value); dirty() }}>新文件沿用此规则</Checkbox>
        {preview ? <section aria-label="目录用途影响范围" className="flex flex-col gap-3">
          <p className="text-body-medium">{preview.total} 项 · 可应用 {preview.applicable} 项{preview.conflicts.length ? ` · 保留现有归属 ${preview.conflicts.length} 项` : ''}</p>
          {preview.withdrawn ? <p className="text-body-2-regular text-text-secondary">撤回此规则已有的 {preview.withdrawn} 项关联，再按当前范围应用。</p> : null}
          {preview.sample.length ? <ul className="flex flex-col gap-1 text-body-2-regular text-text-secondary">
            {preview.sample.map(item => <li className="wrap-anywhere" key={item.id}>{item.name}</li>)}
          </ul> : null}
          {preview.conflicts.length ? <Note tone="warning" extra={<details className="mt-2 text-body-2-regular"><summary>查看归属冲突</summary>
            <ul className="mt-2 flex flex-col gap-2">{preview.conflicts.slice(0, 30).map(item => <li className="wrap-anywhere" key={item.id}>{item.name}：{item.reason}</li>)}</ul>
          </details>}>已有归属或逐件确认的内容保留，目录规则只应用到其余内容。</Note> : null}
        </section> : null}
        {action.error ? <Note tone="error">{action.error}</Note> : null}
      </div>
      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-separator-border p-4">
        {rule ? <Button variant="ghost" className="mr-auto" onClick={() => { if (!busy) void remove() }} {...busyProps(action.busy === 'remove')}>移除规则</Button> : null}
        <Button variant="secondary" onClick={() => { if (!busy) close() }}>取消</Button>
        {preview ? <Button variant="primary" {...busyProps(action.busy === 'save')} onClick={save}>{waiting('save', '正在保存') || '保存目录用途'}</Button>
          : <Button variant="primary" disabled={purpose !== 'category' && !name.trim()} {...busyProps(action.busy === 'preview')}
            onClick={() => { void showPreview() }}>{waiting('preview', '正在预览') || '预览影响'}</Button>}
      </div>
    </div>
  </ModalFrame>;
}
