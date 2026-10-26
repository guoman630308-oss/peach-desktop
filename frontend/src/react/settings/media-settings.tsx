/* 「媒体库与入库」分组：媒体目录与自动入库，每个文件夹标着挂载状态。
 *
 * 服务端检查管理权限后用 `editable` 控制表单；没有权限时只说明原因。
 * 保存成功后显示持久提示和访问地址，等待服务就绪后跳转。
 *
 * 校验原因由服务端按字段给（400 的 `errors`），页面写回原位，不在前端复制一份路径与端口的判定。 */
import { useEffect, useRef, useState, type FormEvent } from 'react';

import { Button } from '@/components/base/buttons/button';
import { LinkButton } from '@/components/base/buttons/link-button';
import { Checkbox } from '@/components/base/checkbox/checkbox';
import { Input } from '@/components/base/input/input';
import { ConnectionStatus } from '../components/connection-status';

import { ApiError, apiSend, errorMessage } from '../../api';
import { CONFIGURATION_URL, PICK_FOLDER_URL } from '../../configuration-endpoints';
import type { ConfigurationData, ConfigurationGroupProps } from '../bundle';
import { Note } from '../components/note';
import { queryClient } from '../query';
import { RESTART_REDIRECT_MS } from '../restart-redirect';
import { CloudDriveGuide } from './clouddrive-guide';
import { CONFIGURATION_KEY, fetchConfiguration } from './configuration';
import { groupRoot } from './configuration-panel';
import { FolderRow, isCloudSource, MEDIA_SOURCES, MediaReadPolicy, MediaReadPolicyHelp, SourceSelect, sourceMark, useFolderRows } from './folder-rows';
import { sourceDefaultCost, type SourceCost } from '../../core/media-sources';
import { LibraryIconPicker } from './library-icon-picker';
import { PushDiscoveryForm } from './push-discovery-settings';
import {
  ErrorText, ExternalLink, Fact, FactList, FieldLabel, Footer, Help, Section, SourceMark, Stack,
} from './section';
import { busyProps, useAction } from './use-action';

const sourceName = (location: string) => MEDIA_SOURCES.find(([kind]) => kind === location)?.[1] ?? location;
const pickFolder = async (initial: string) =>
  (await apiSend<{ path: string | null }>(PICK_FOLDER_URL, { initial })).path;

interface MediaRow { path: string; location: string; root: string; library: string; library_icon: string; cost: SourceCost | '' }
interface FieldErrors { media_dirs?: string[]; port?: string }
interface SaveResult { saved: boolean; url: string; revision: string }

const blankRow = (path = ''): MediaRow => ({ path, location: 'local', root: '', library: '', library_icon: '', cost: 'free' });

function initialRows(data: ConfigurationData): MediaRow[] {
  const known = data.media_sources?.filter((row) => MEDIA_SOURCES.some(([kind]) => kind === row.location));
  if (known?.length) {
    return known.map((row) => ({
      path: row.path, location: row.location, root: row.root, library: row.library || '', library_icon: row.library_icon || '',
      cost: row.cost ?? sourceDefaultCost(row.location),
    }));
  }
  return (data.media_dirs.length ? data.media_dirs : ['']).map((path) => blankRow(path));
}

const fieldErrorsOf = (cause: unknown): FieldErrors | null => {
  if (!(cause instanceof ApiError) || cause.status !== 400) return null;
  return (cause.body as { errors?: FieldErrors } | null)?.errors ?? null;
};

/* 能编辑时挂载状态标在每个文件夹行上；只读时没有那张表，单列一块。 */
export function MediaSettings({ data, receipt, panel }: ConfigurationGroupProps) {
  return (
    <div {...groupRoot(panel)}>
      {data.editable ? <MediaForm data={data} receipt={receipt} /> : <Note tone="neutral" title="只读">{data.notice}</Note>}
      {data.editable ? null : <MountStatus data={data} />}
      {data.push_discovery
        ? <PushDiscoveryForm initial={data.push_discovery} receipt={receipt} />
        : null}
    </div>
  );
}

function MediaForm({ data, receipt }: ConfigurationGroupProps) {
  const { rows, edit, add, remove, errors: rowErrors, setErrors: setRowErrors, picking, pick, inputRef } = useFolderRows({
    initial: () => initialRows(data), blank: () => blankRow(), pickFolder, describe: errorMessage,
  });
  const [scanNow, setScanNow] = useState(false);
  const [requireReadPolicy, setRequireReadPolicy] = useState(false);
  const [failure, setFailure] = useState('');
  const [saved, setSaved] = useState<SaveResult | null>(null);
  const revision = useRef(data.revision);
  const action = useAction();

  useEffect(() => {
    if (!saved) return undefined;
    const timer = setTimeout(() => location.assign(saved.url), RESTART_REDIRECT_MS);
    return () => clearTimeout(timer);
  }, [saved]);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const missing = rows.findIndex((row) => !row.cost);
    if (missing >= 0) {
      setRequireReadPolicy(true);
      const form = event.currentTarget;
      requestAnimationFrame(() => form.querySelector<HTMLButtonElement>(`[data-media-read-policy="${missing}"] button`)?.focus());
      return;
    }
    setFailure('');
    void action.run('save', (signal) => apiSend<SaveResult>(CONFIGURATION_URL, {
      revision: revision.current,
      media_dirs: rows.map((row) => row.path),
      ...(data.media_sources ? { media_sources: rows } : {}),
      port: String(data.port),
      scan_now: scanNow,
    }, 'POST', signal), (result) => {
      revision.current = result.revision;
      setRowErrors([]);
      receipt('已保存配置');
      setSaved(result);
    }, (cause) => {
      const fields = fieldErrorsOf(cause);
      setRowErrors(fields?.media_dirs ?? []);
      if (!fields || fields.port) setFailure(fields?.port || errorMessage(cause));
    });
  };

  if (saved) {
    return (
      <Section title="媒体目录">
        <Stack>
          <Note tone="success" title="配置已保存">
            Peach 正在重新启动。稍后自动跳转，或点击<LinkButton href={saved.url} size="small">进入馆藏</LinkButton>。
          </Note>
        </Stack>
      </Section>
    );
  }

  // 挂载状态是已保存的那份配置的读数，只标在路径没改过的行上。
  const mountOf = (path: string) => (path ? data.media_sources?.find((source) => source.path === path) : undefined);
  const cloud = rows.some((row) => isCloudSource(row.location));
  const missing = cloud ? (data.mount_dependencies ?? []).filter((dependency) => !dependency.available) : [];
  return (
    <Section title="媒体目录" onSubmit={submit}>
      <Stack>
        <div className="flex flex-col gap-3">
          <FieldLabel>媒体目录</FieldLabel>
          <div role="group" aria-label="媒体目录" className="flex flex-col gap-3">
            {rows.map((row, index) => (
              <FolderRow key={index} label={`媒体目录 ${index + 1}`} path={row.path}
                onPath={(path) => edit(index, { path })} error={rowErrors[index]} inputRef={inputRef(index)}
                picking={picking === index} onPick={() => void pick(index)}
                onRemove={rows.length > 1 ? () => remove(index) : undefined}
                status={mountOf(row.path) ? <MountBadge online={mountOf(row.path)?.online} /> : null}>
                <SourceSelect index={index} value={row.location}
                  onChange={(location) => edit(index, { location, cost: location === 'local' ? 'free' : '' })} />
                <MediaReadPolicy index={index} cost={row.cost} onChange={(cost) => edit(index, { cost })}
                  error={requireReadPolicy && !row.cost ? '请选择媒体读取策略' : undefined} />
                <Input className="min-w-40 flex-1" aria-label={`媒体库名称 ${index + 1}`} maxLength={80}
                  placeholder="媒体库名称"
                  value={row.library} onChange={(library) => edit(index, { library })} />
                <LibraryIconPicker label={`媒体库图标 ${index + 1}`} value={row.library_icon} kind={row.location}
                  onChange={(library_icon) => edit(index, { library_icon })} />
              </FolderRow>
            ))}
          </div>
          <Button variant="secondary" className="self-start" onClick={add}>添加文件夹</Button>
        </div>
        {cloud ? <><MediaReadPolicyHelp /><CloudDriveGuide /></> : null}
        {missing.map((dependency) => (
          <Help key={dependency.name}>
            未检测到 {dependency.name}。<ExternalLink href={dependency.download_url}>下载 {dependency.name}</ExternalLink>
          </Help>
        ))}
        <Checkbox isSelected={scanNow} onChange={setScanNow}>保存后扫描并补全资料</Checkbox>
        {failure ? <Note tone="error" title="没有保存">{failure}</Note> : null}
      </Stack>
      <Footer status="保存后 Peach 会重新启动。">
        <Button type="submit" {...busyProps(action.busy === 'save')}>保存配置</Button>
      </Footer>
    </Section>
  );
}

function MountBadge({ online }: { online: boolean | undefined }) {
  return <ConnectionStatus online={online} />;
}

/* 重取回来的是整份配置，换进整页那一个键：屏幕上只有一份真相，页面上别处读到的
   也是这一次的结果。另存一份的话，挂载点在这里是新的、在「这台电脑」那张表单里还是旧的。 */
function MountStatus({ data }: { data: ConfigurationData }) {
  const sources = data.media_sources;
  const action = useAction();
  if (!sources) return null;
  const refresh = () => void action.run('refresh', (signal) => fetchConfiguration(signal),
    (result) => queryClient.setQueryData(CONFIGURATION_KEY, result));
  return (
    <Section title="挂载状态">
      <FactList>
        {sources.map((row, index) => (
          <Fact key={index} term={<><SourceMark mark={sourceMark(row.location)} />{sourceName(row.location)}</>}>
            {row.path || '未配置挂载点'}<MountBadge online={row.online} />
          </Fact>
        ))}
      </FactList>
      {action.error ? <Stack divided><ErrorText>{action.error}</ErrorText></Stack> : null}
      <Footer>
        <Button variant="secondary" onClick={refresh} {...busyProps(action.busy === 'refresh')}>刷新挂载状态</Button>
      </Footer>
    </Section>
  );
}
