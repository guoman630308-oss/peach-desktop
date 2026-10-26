/* 检查更新：当前与最新版本、下载安装进度，准备好之后确认重启。
 *
 * 更新任务进行中每秒问一次状态，空闲时 30 秒一次；连续 120 次问不到就停下并说明原因。 */
import { useEffect, useRef, useState } from 'react';
import { confirmModal } from '@peach/legacy/ui';

import { Button } from '@/components/base/buttons/button';

import { apiGet, apiSend, errorMessage } from '../../api';
import type { ReleaseState, UpdateJob } from '../bundle';
import { Note } from '../components/note';
import { Progress } from '../components/progress';
import { ErrorText, ExternalLink, Fact, FactList, Footer, Help, Section, Stack } from './section';
import { busyProps, useAction } from './use-action';

const ACTIVE = new Set(['downloading', 'verifying', 'extracting', 'preparing', 'restarting', 'installing']);
const STOPS = [65, 75, 90];
const MAX_FAILURES = 120;

export function ReleaseUpdates({ initial, initialJob }: { initial: ReleaseState; initialJob?: UpdateJob | undefined }) {
  const [data, setData] = useState(initial);
  const [job, setJob] = useState<UpdateJob>(initialJob || { state: 'idle', progress: 0 });
  const [pollError, setPollError] = useState('');
  const action = useAction();
  const prompted = useRef(false);
  const mounted = useRef(true);
  const running = ACTIVE.has(job.state);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const restart = async () => {
    const result = await apiSend<UpdateJob>('/api/configuration/update-restart', {});
    if (mounted.current) setJob(result);
  };
  const confirmRestart = () => void confirmModal({
    title: '更新已准备好', body: `Peach ${job.version || ''} 将在重启后安装。`,
    confirmLabel: '立即重启', cancelLabel: '稍后', onConfirm: restart,
  });

  useEffect(() => {
    if (job.state !== 'ready' || prompted.current) return;
    prompted.current = true;
    confirmRestart();
  }, [job.state]);

  useEffect(() => {
    const controller = new AbortController();
    const delay = ACTIVE.has(job.state) ? 1000 : 30000;
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    const poll = async () => {
      try {
        const result = await apiGet<UpdateJob>('/api/configuration/update-status', controller.signal);
        if (controller.signal.aborted) return;
        setJob(result);
        failures = 0;
        if (result.state === 'complete') {
          setData((current) => ({ ...current, current_version: result.version || current.current_version, state: 'current', message: '已是最新测试版。' }));
        }
        const automatic = await apiGet<{ result?: ReleaseState }>('/api/configuration/automatic-updates', controller.signal);
        if (!controller.signal.aborted && automatic.result) setData(automatic.result);
      } catch {
        failures += 1;
        if (failures >= MAX_FAILURES && !controller.signal.aborted) setPollError('尚未连接到 Peach，请检查托盘后刷新页面。');
      }
      if (!controller.signal.aborted && failures < MAX_FAILURES) timer = setTimeout(poll, delay);
    };
    timer = setTimeout(poll, delay);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [job.state]);

  const download = () => {
    if (running) return;
    prompted.current = false;
    setPollError('');
    void action.run('download', (signal) => apiSend<UpdateJob>('/api/configuration/update', {}, 'POST', signal), setJob);
  };
  const check = () => void action.run('check', (signal) => apiGet<ReleaseState>('/api/configuration/updates', signal), setData,
    (cause) => { setData({ ...initial, state: 'error' }); action.setError(errorMessage(cause)); });

  const error = action.error || pollError;
  const reading = job.state === 'downloading' && job.total
    ? `${((job.downloaded || 0) / 1048576).toFixed(1)} / ${(job.total / 1048576).toFixed(1)} MB`
    : `${job.progress}%`;
  return (
    <Section title="检查更新">
      <FactList>
        <Fact term="当前版本">{data.current_version}</Fact>
        <Fact term="安装方式">{data.installation}</Fact>
        <Fact term="更新通道">{data.channel}</Fact>
        <Fact term="最新版本">{data.latest_version || (data.state === 'unchecked' ? '尚未检查' : '未取得')}</Fact>
        {data.package_kind ? <Fact term="下载内容">
          {data.package_kind === 'update' ? '应用更新包' : '应用与工具'}
          {data.asset_size ? ` · ${(data.asset_size / 1048576).toFixed(1)} MB` : ''}
        </Fact> : null}
      </FactList>
      {data.package_kind === 'update'
        ? <Help>应用更新包复用已安装工具；工具缺失、损坏或版本变化时会另行下载所需工具包。</Help>
        : data.package_kind === 'full' ? <Help>本次更新包含媒体工具。</Help> : null}
      {/* 还没检查过时「最新版本」那一行已经写着「尚未检查」，下面不再说一遍。 */}
      {data.state === 'unchecked' && !data.checked_at && !error && job.state === 'idle' ? null : <Stack divided>
        {data.checked_at ? <Help>检查于 {new Date(data.checked_at * 1000).toLocaleString()}</Help> : null}
        {data.state === 'available' && !error
          ? <Note tone="info" title="有可用更新">{data.message}</Note>
          : error || data.state === 'error'
            ? <ErrorText>{error || data.message}</ErrorText>
            : data.state === 'unchecked' ? null : <Help role="status">{data.message}</Help>}
        {job.state === 'error' ? <ErrorText>{job.message}</ErrorText> : null}
        {job.state !== 'idle' && job.state !== 'error' ? (
          <div aria-live="polite" className="flex flex-col gap-2">
            <Progress label="更新准备进度" value={job.progress} stops={STOPS} />
            <Help>下载 → 校验 → 解压 → 准备安装 · {job.message}</Help>
            <Help>{reading}</Help>
          </div>
        ) : null}
      </Stack>}
      <Footer status={<ExternalLink href={data.release_url}>查看发布页</ExternalLink>}>
        {job.state === 'ready' ? <Button onClick={confirmRestart}>重启安装</Button> : null}
        {data.state === 'available' && data.installation === '独立测试包' && job.state !== 'ready'
          ? <Button onClick={download} {...busyProps(running || action.busy === 'download')}>下载并安装</Button>
          : null}
        <Button variant={job.state === 'ready' || (data.state === 'available' && data.installation === '独立测试包') ? 'secondary' : 'primary'}
          disabled={running} onClick={check} {...busyProps(action.busy === 'check')}>检查更新</Button>
      </Footer>
    </Section>
  );
}
