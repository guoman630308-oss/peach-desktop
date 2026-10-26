import { useState, type FormEvent } from 'react';
import { Heading } from 'react-aria-components';
import { Button } from '@/components/base/buttons/button';
import { Input } from '@/components/base/input/input';
import { apiSend } from '../../api';
import { ModalFrame } from '../components/modal-frame';
import { Note } from '../components/note';
import { SettingsDialogClose } from '../components/settings-dialog-close';
import { busyProps, useAction } from '../settings/use-action';
import { LIBRARY_PROCESSING_IMPORT_URL, PICK_MEDIA_FILE_URL, type LibraryProcessingData } from './library-processing';

export function SingleFileImportDialog({ close, onStarted }: {
  close(): void; onStarted(state: LibraryProcessingData): void;
}) {
  const [path, setPath] = useState('');
  const action = useAction();
  const busy = !!action.busy;
  const pick = () => {
    void action.run('pick', signal => apiSend<{ path: string | null }>(PICK_MEDIA_FILE_URL, {}, 'POST', signal),
      result => { if (result.path) setPath(result.path) });
  };
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!path.trim() || busy) return;
    void action.run('start', signal => apiSend<LibraryProcessingData>(LIBRARY_PROCESSING_IMPORT_URL,
      { path: path.trim() }, 'POST', signal), result => { onStarted(result); close() });
  };
  return <ModalFrame isOpen width="form" label="添加单个视频" onOpenChange={open => { if (!open && !busy) close() }}>
    <form className="peach-react flex min-w-0 flex-col" onSubmit={submit}>
      <div className="flex items-start justify-between gap-4 border-b border-separator-border p-5">
        <Heading slot="title" className="text-title-2-medium">添加单个视频</Heading>
        <SettingsDialogClose label="关闭添加单个视频" onPress={() => { if (!busy) close() }} />
      </div>
      <div className="flex min-w-0 flex-col gap-4 p-5">
        <p className="text-body-2-regular text-text-secondary">选择已配置媒体目录中的视频，读取媒体属性，并按设置生成预览和采集资料。</p>
        <div className="flex min-w-0 items-end gap-2">
          <Input label="视频文件路径" value={path} onChange={setPath} isRequired isDisabled={busy}
            placeholder="粘贴视频文件的完整路径" className="min-w-0 flex-1" />
          <Button variant="secondary" onClick={pick} {...busyProps(action.busy === 'pick')} disabled={action.busy === 'start'}>浏览</Button>
        </div>
        {action.error ? <Note tone="error">{action.error}</Note> : null}
      </div>
      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-separator-border p-4">
        <Button variant="secondary" onClick={() => { if (!busy) close() }}>取消</Button>
        <Button variant="primary" type="submit" disabled={!path.trim() || action.busy === 'pick'}
          {...busyProps(action.busy === 'start')}>添加并采集</Button>
      </div>
    </form>
  </ModalFrame>;
}
