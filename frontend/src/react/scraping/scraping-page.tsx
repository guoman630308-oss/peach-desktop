/* 来源和凭证页：每个采集来源怎么连、拿什么身份连，外加一趟按番号补高清封面。
 *
 * 这是第一个带写操作的 React 档，两件事在这里定型：
 * - **写操作是 `useMutation`**，不进 Query 的缓存节律。保存成功后用 `setQueryData` 把服务端
 *   回的那一条换进列表，而不是把整页重取一遍：用户可能正在填另一张卡，重取会把它一起重画。
 *   失败只在卡内留一句原因，缓存里的上一份数据不动。
 * - **后台任务走 `useBackgroundJob`**：跑起来两秒一次，停了就不问，首屏读到的旧终态不冒充
 *   新结果。轮询跟着组件走，遗留壳换页时卸根，它自己就停了。 */
import { useRef, useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';
import { RiArrowRightLine } from '@remixicon/react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Radio } from 'react-aria-components';

import { SettingsRow } from '@/components/application/settings/settings-rows';
import { Button } from '@/components/base/buttons/button';
import { LinkButton } from '@/components/base/buttons/link-button';
import { Input } from '@/components/base/input/input';
import { Select, SelectItem } from '@/components/base/select/select';

import { apiSend, errorMessage } from '../../api';
import { useBackgroundJob } from '../background-job';
import type { ScrapingProps } from '../bundle';
import { LoadingDots } from '../components/loading-dots';
import { Note } from '../components/note';
import { Page } from '../components/page';
import { SiteMark } from '../components/site-mark';
import { SEGMENT, SEGMENTED_TRACK, SegmentedRadioGroup as RadioGroup } from '../components/segmented';
import { queryClient } from '../query';
import {
  Disclosure, ErrorText, ExternalLink, Fact, FactList, FieldLabel, Footer, Help, Rows, Section, Stack,
} from '../settings/section';
import { busyProps } from '../settings/use-action';
import { CustomSources } from './custom-sources';
import {
  AMANE_BRIDGE_CHECK_URL, AMANE_BRIDGE_KEY, AMANE_BRIDGE_REBUILD_URL,
  checkText, COOKIE_TEXT_LIMIT, COVER_JOB_KEY, fetchAmaneBridge, fetchCoverJob,
  fetchSources, SCRAPING_CHECK_URL, SCRAPING_COVER_URL, SCRAPING_KEY, SCRAPING_SETTINGS_URL,
  type AmaneBridge, type Check, type CoverJob, type ScrapingData, type Source,
} from './scraping';

const NETWORKS = [['peach', 'Peach 代理'], ['direct', '直接连接']] as const;

/** 连接方式的选项。数据库里存着这两种以外的值（旧版写下的、手改的）时把它原样列成一项，
 *  下拉框照实显示现在是什么，不落到空占位上。 */
export function networkOptions(current: string): (readonly [string, string])[] {
  return NETWORKS.some(([key]) => key === current) || !current ? [...NETWORKS] : [...NETWORKS, [current, current]];
}

/** 登录页外链只写主机名：整条地址在窄屏上会把分区标题挤成一字一行，路径也说明不了是哪个站。 */
export function linkHost(url: string): string {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}
const COOKIE_METHODS = [['paste', '粘贴 Cookie'], ['file', '导入文件']] as const;

function GitHubMark() {
  return <SiteMark source="github" />;
}

/** 一个来源一张卡：怎么连、拿什么身份连，底下三个动作。 */
function SourceCard({ source, toast }: { source: Source } & ScrapingProps) {
  const [network, setNetwork] = useState(source.network);
  const [cookie, setCookie] = useState('');
  const [cookieText, setCookieText] = useState('');
  const [method, setMethod] = useState<string>(COOKIE_METHODS[0][0]);
  const [fileName, setFileName] = useState('');
  const [fileProblem, setFileProblem] = useState('');
  const file = useRef<HTMLInputElement>(null);

  /* 秘密只在提交那一刻存在于页面上：保存回来之后输入框清空，页面上不再留着刚交上去的
     那一份。文件选择器自己也要清，否则同名文件再选一次不会触发 change。 */
  function forgetSecrets() {
    setCookie('');
    setCookieText('');
    setFileName('');
    setFileProblem('');
    if (file.current) file.current.value = '';
  }

  const save = useMutation({
    mutationFn: (revoke: boolean) => apiSend<{ saved: Source }>(SCRAPING_SETTINGS_URL, {
      source: source.source, network, cookie, cookies_text: cookieText, revoke,
    }),
    onSuccess: (result, revoke) => {
      // 服务端回的就是这一条的新样子，换进列表即可，不为一次保存把整页重取一遍。
      queryClient.setQueryData<ScrapingData>(SCRAPING_KEY, (current) => current && {
        ...current,
        sources: current.sources.map(
          (row) => (row.source === result.saved.source ? result.saved : row)),
      });
      forgetSecrets();
      toast(revoke ? 'Cookie 已撤销' : '来源设置已保存');
    },
  });
  const check = useMutation({
    mutationFn: () => apiSend<{ results: Check[] }>(SCRAPING_CHECK_URL, { source: source.source }),
  });
  // 一张卡上三个动作互斥：它们改的是同一份凭据，谁先落地都会让另一次的结果说不清楚。
  const busy = save.isPending || check.isPending;

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    check.reset();
    save.mutate(false);
  };
  const revoke = () => {
    if (busy) return;
    check.reset();
    save.mutate(true);
  };
  const connect = () => {
    if (busy) return;
    save.reset();
    check.mutate();
  };

  async function pickFile(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const selected = input.files?.[0];
    setCookieText('');
    setFileProblem('');
    if (!selected) {
      setFileName('');
      return;
    }
    if (selected.size > COOKIE_TEXT_LIMIT) {
      // 超限的多半根本不是 Cookie 文件。在浏览器里就拦住，不拿它去占一次请求。
      input.value = '';
      setFileName('');
      setFileProblem('Cookie 文本超过 256 KiB');
      return;
    }
    try {
      const text = await selected.text();
      setCookieText(text);
      setFileName(selected.name);
    } catch {
      setFileProblem('Cookie 文件未读取，请重新选择');
    }
  }

  const problem = fileProblem
    || (save.error ? errorMessage(save.error) : '')
    || (check.error ? errorMessage(check.error) : '');
  const results = check.data?.results ?? [];
  // 这一块空着时连同它那道分隔线一起不画：直连、又不收 Cookie 的来源只有一行连接方式。
  const detailed = network === 'peach' || source.accepts_cookie || source.browser || !!problem || results.length > 0;

  return (
    <Section title={source.label} onSubmit={submit} aside={
      <span className="flex min-w-0 items-center gap-1">
        <SiteMark source={source.source} />
        {source.login ? <ExternalLink href={source.login}>{linkHost(source.login)}</ExternalLink> : null}
      </span>
    }>
      <Rows>
        <SettingsRow label="连接方式">
          {/* 定宽：数据库里的长取值按内容撑宽会把左边的「连接方式」挤成竖排，定宽后取值在框里截断。 */}
          <Select aria-label="连接方式" className="w-44 min-w-0" selectedKey={network}
            onSelectionChange={(key) => { if (key !== null) setNetwork(String(key)) }}>
            {networkOptions(network).map(([key, name]) => <SelectItem key={key} id={key}>{name}</SelectItem>)}
          </Select>
        </SettingsRow>
      </Rows>
      {source.cooldown_until && source.cooldown_until > Date.now() / 1000 ? (
        <Note tone="warning">来源正在冷却，截止 {new Date(source.cooldown_until * 1000).toLocaleString('zh-CN')}</Note>
      ) : null}
      {detailed ? <Stack divided>
        {network === 'peach'
          ? <span className="self-start">
              <LinkButton href="/settings/library#peachProxy" size="small" trailingIcon={RiArrowRightLine}>
                配置 Peach 代理
              </LinkButton>
            </span>
          : null}
        {/* 由本机浏览器过验证的来源不收 Cookie：浏览器自己带着会话。留着「撤销 Cookie」让人清掉旧的。 */}
        {source.browser
          ? <p className="text-body-2-regular text-text-secondary">
              人机验证由本机浏览器自动完成；需要点击时窗口会弹出。这台机器上不需要 Cookie。
            </p>
          : null}
        {source.accepts_cookie && !source.browser ? <>
          <p className="text-body-2-regular text-text-secondary">
            {source.cookie_saved
              ? 'Cookie 已保存；登录是否有效要到抓取时才知道。'
              : '需要登录时，任选一种方式提供 Cookie。'}
          </p>
          {/* 两种方式互斥，交上去的只能是其中一种：切换时把另一种的输入清掉，
              不让看不见的那一份跟着提交。 */}
          <RadioGroup aria-label="提供 Cookie 的方式（二选一）" value={method}
            onChange={(next) => { setMethod(next); forgetSecrets() }}
            className={SEGMENTED_TRACK}>
            {COOKIE_METHODS.map(([value, label]) => (
              <Radio key={value} value={value} className={SEGMENT}>
                {label}
              </Radio>
            ))}
          </RadioGroup>
          {method === 'paste'
            ? <Input type="password" label="Cookie" autoComplete="off" value={cookie} onChange={setCookie} />
            : <div className="flex flex-col gap-2">
                <FieldLabel>Netscape Cookie 文件（.txt）</FieldLabel>
                <div className="flex flex-wrap items-center gap-3">
                  <Button variant="secondary" onClick={() => file.current?.click()}>选择文件</Button>
                  <span className="min-w-0 text-body-2-regular break-all text-text-secondary">
                    {fileName || '未选择文件'}
                  </span>
                </div>
                {/* 原生文件选择器长相不可控，按钮归 BoardUI，输入框只留着接文件。 */}
                <input ref={file} type="file" accept=".txt" tabIndex={-1} aria-hidden
                  className="hidden" onChange={(event) => { void pickFile(event) }} />
              </div>}
        </> : null}
        {problem ? <ErrorText>{problem}</ErrorText> : null}
        {results.map((result) => (
          <Note key={result.label} tone={result.ok ? 'success' : 'error'}>
            {checkText(result, source.label)}
          </Note>
        ))}
      </Stack> : null}
      {/* 主体动作在最右。次级动作先出现，`保存` 是这张卡唯一的写入，放在一行的末尾；
          表单里只有它一个 `type=submit`，所以挪位置不影响回车提交。 */}
      <Footer>
        {source.accepts_cookie && source.cookie_saved
          ? <Button variant="secondary" onClick={revoke} {...busyProps(busy)}>撤销 Cookie</Button>
          : null}
        <Button variant="secondary" onClick={connect} {...busyProps(busy)}>检查连接</Button>
        <Button type="submit" {...busyProps(busy)}>保存</Button>
      </Footer>
    </Section>
  );
}

/** 按番号补一张高清封面。这一趟在后台跑，页面上留状态。 */
function CoverCard({ toast }: ScrapingProps) {
  const [code, setCode] = useState('');
  const { running, outcome, start } = useBackgroundJob<CoverJob>({
    queryKey: COVER_JOB_KEY,
    queryFn: ({ signal }) => fetchCoverJob(signal),
    start: () => apiSend<CoverJob>(SCRAPING_COVER_URL, { code }),
    onFinish: (state) => {
      if (state.status === 'complete') toast(state.result || '封面采集完成');
    },
  });

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (running || start.isPending || !code.trim()) return;
    start.mutate();
  };

  const problem = start.error ? errorMessage(start.error)
    : outcome?.status === 'failed' ? (outcome.error || '采集未取得') : '';
  return (
    <Section title="高清封面" onSubmit={submit}>
      <Stack>
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-0 flex-1">
            <Input aria-label="馆藏番号" isRequired isDisabled={running} value={code} onChange={setCode}
              placeholder="输入馆藏番号，如 ABW-232" />
          </div>
          <Button type="submit" disabled={!code.trim()} {...busyProps(running || start.isPending)}>
            抓取封面
          </Button>
        </div>
        {/* 这一趟要挨个问几家站点、还可能走代理，没有可数的总量，也不能编一个百分比出来。
            关掉页面它照样在跑，回来能接上，所以状态得留在页面上，而不是只让按钮转一下。 */}
        {running ? <LoadingDots label="正在抓取封面" /> : null}
        {problem ? <Note tone="error">{problem}</Note> : null}
        {outcome?.status === 'complete'
          ? <Note tone="success">{outcome.result || '封面采集完成'}</Note>
          : null}
      </Stack>
    </Section>
  );
}

/** amane 桥：钉在哪个 revision、venv 建没建、上游最新到哪。升级是人读 diff 之后改清单，
 *  这张卡只做两件事：问一下上游最新版，和按钉住的版本重建 venv。 */
function AmaneBridgeCard({ toast }: ScrapingProps) {
  const { query: bridge, running, outcome, start: rebuild } = useBackgroundJob<CoverJob, void, AmaneBridge>({
    queryKey: AMANE_BRIDGE_KEY,
    queryFn: ({ signal }) => fetchAmaneBridge(signal),
    start: () => apiSend<CoverJob>(AMANE_BRIDGE_REBUILD_URL, {}),
    // 这个键缓存的是整张卡，这一趟的快照只换进 `job` 那一格。
    jobOf: (card) => card.job,
    withJob: (card, started) => card && { ...card, job: started },
    onFinish: (state) => {
      if (state.status === 'complete') toast(state.result || 'amane 桥已重建');
    },
  });
  const check = useMutation({
    mutationFn: () => apiSend<{ latest: string }>(AMANE_BRIDGE_CHECK_URL, {}),
  });

  const data = bridge.data;

  if (!data) {
    return bridge.error
      ? <Section title="amane"><Stack><Note tone="error">{errorMessage(bridge.error)}</Note></Stack></Section>
      : null;
  }
  const busy = running || rebuild.isPending || check.isPending;
  const problem = rebuild.error ? errorMessage(rebuild.error)
    : check.error ? errorMessage(check.error)
    : outcome?.status === 'failed' ? (outcome.error || '重建未完成') : '';
  return (
    <Section title="amane" aside={<ExternalLink href={data.repository} leadingIcon={GitHubMark}>{data.repository}</ExternalLink>}>
      <FactList>
        <Fact term="已安装版本">{data.installed_version || '未安装'}</Fact>
        <Fact term="上游最新版本">{check.data?.latest ?? '尚未检查'}</Fact>
        <Fact term="运行环境">{data.installed ? '已安装' : '未安装'}</Fact>
        <Fact term="内置站点">{data.sites.length.toLocaleString()} 个</Fact>
      </FactList>
      <Stack divided>
        {data.sites.length
          ? <Disclosure summary="查看内置站点">
              <p className="text-body-2-regular text-text-secondary">{data.sites.map((site) => site.label).join('、')}</p>
            </Disclosure>
          : null}
        <Help>
          已安装版本：{data.installed_version || '未安装'}。amane（{data.license}）提供这些内置站点的解析能力。
          重新安装会使用 Peach 内置的 {data.version} 版本，不随上游自动升级。
        </Help>
        {running ? <LoadingDots label="正在重建运行环境" /> : null}
        {problem ? <Note tone="error">{problem}</Note> : null}
        {outcome?.status === 'complete' ? <Note tone="success">{outcome.result || 'amane 桥已重建'}</Note> : null}
      </Stack>
      <Footer>
        <Button variant="secondary" onClick={() => { if (!busy) check.mutate() }} {...busyProps(check.isPending)}>
          检查上游版本
        </Button>
        <Button onClick={() => { if (!busy) rebuild.mutate() }} {...busyProps(running || rebuild.isPending)}>
          {data.installed ? '重新安装' : '安装'}
        </Button>
      </Footer>
    </Section>
  );
}

export function ScrapingPage({ toast }: ScrapingProps) {
  const sources = useQuery({ queryKey: SCRAPING_KEY, queryFn: ({ signal }) => fetchSources(signal) });
  const data = sources.data;
  if (!data) {
    return (
      <Page>
        <Note tone="error" title="采集来源读取失败">
          {sources.error ? errorMessage(sources.error) : '读取采集来源失败'}
        </Note>
      </Page>
    );
  }
  return (
    <Page>
      <p className="text-body-2-regular text-text-secondary">高清图片可能要经代理才能下载，先检查连接。</p>
      <CoverCard toast={toast} />
      <CustomSources receipt={toast} />
      {(data.sources || []).map(
        (source) => <SourceCard key={source.source} source={source} toast={toast} />)}
      <AmaneBridgeCard toast={toast} />
    </Page>
  );
}
