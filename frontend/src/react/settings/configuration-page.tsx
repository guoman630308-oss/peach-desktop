/* 配置页（`/settings/library`）：这台运行 Peach 的电脑上那些要「保存配置」的多字段表单。
 * 每个分区各占一格页签，一次只摆一组；本机配置权限与数据库写入权限由服务端分别判定。
 * 设置弹层里的「Peach 配置」入口指到这里。
 *
 * `.ui-configpage` 的第一层：最前面是页签条（`.ui-board-local-nav`），后面每组一个 `h2.ui-configgroup` 小标题
 * 紧跟这一组的根节点，根节点就是那一格页签面板。小标题在样式里不显示，它的字已经写在页签上；
 * 没有内容的组连标题与页签一起省略。页签条第一帧就在，和整页同一次提交画出来。 */
import { useQuery } from '@tanstack/react-query';
import { Fragment, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactElement } from 'react';
import { peachHistory, shellNavigate, settingsPath } from '@peach/history';

import { errorMessage } from '../../api';
import type { ConfigurationData, ConfigurationPanel, ConfigurationProps } from '../bundle';
import { Note } from '../components/note';
import { CONFIGURATION_KEY, fetchConfiguration } from './configuration';
import { DownloadGroup } from './download-settings';
import { GeneralSettings } from './general-settings';
import { MaintenanceSettings } from './maintenance-settings';
import { MediaSettings } from './media-settings';
import { MediaProcessingSettings } from './media-processing-settings';
import { LocalPlayerSettings } from './local-player-settings';
import { NetworkSettings } from './network-settings';
import { SourceSettings } from './source-settings';
import { groupRoot } from './configuration-panel';

export function ConfigurationPage({ receipt, reopenTutorial, section = '', diagnostics }: ConfigurationProps & { diagnostics?: ReactElement }) {
  const config = useQuery({
    queryKey: CONFIGURATION_KEY, queryFn: ({ signal }) => fetchConfiguration(signal),
  });
  const data = config.data;
  if (!data) {
    return (
      <div className="ui-configpage">
        <Note tone="error" title="配置读取失败">
          {config.error ? errorMessage(config.error) : '未取得配置，请刷新页面重试。'}
        </Note>
      </div>
    );
  }
  return <ConfigurationGroups data={data} receipt={receipt} reopenTutorial={reopenTutorial} section={section} diagnostics={diagnostics} />;
}

interface Group { title: string; body(panel: ConfigurationPanel): ReactElement }

/* 页签与面板的 id 前缀按页签条计数：每画出一条新的页签条领一个号。 */
let tabSequence = 0;

/* 页签条是一个 tablist，方向键在整排里走、首尾相接，Home／End 到两头，挪到哪一格就选中哪一格、焦点跟过去。
   分区地址与选中项同步，表单保持挂载。 */
function ConfigurationGroups(
  { data, receipt, reopenTutorial, section, diagnostics }: Required<ConfigurationProps> & { data: ConfigurationData; diagnostics?: ReactElement },
) {
  const [prefix] = useState(() => `board-tabs-${++tabSequence}`);
  const [chosen, setChosen] = useState(section);
  const workspace = useRef<HTMLDivElement>(null);
  const previous = useRef(chosen);
  useEffect(() => { setChosen(section) }, [section]);
  useLayoutEffect(() => {
    if (previous.current === chosen) return;
    previous.current = chosen;
    const nav = workspace.current?.querySelector<HTMLElement>('[role="tablist"]');
    const panel = workspace.current?.querySelector<HTMLElement>('.ui-board-group-active');
    if (!nav || !panel) return;
    const boundary = parseFloat(getComputedStyle(nav).top);
    if (!Number.isFinite(boundary) || nav.getBoundingClientRect().top > boundary + 1) return;
    const top = matchMedia('(max-width:900px)').matches ? nav.getBoundingClientRect().bottom + 8 : boundary;
    window.scrollTo(0, Math.max(0, window.scrollY + panel.getBoundingClientRect().top - top));
  }, [chosen]);
  const choose = (title: string) => {
    setChosen(title);
    shellNavigate(settingsPath(title), { replace: true, state: peachHistory.navigation.location.state });
  };
  const [narrow, setNarrow] = useState(() => matchMedia('(max-width:900px)').matches);
  useEffect(() => {
    const media = matchMedia('(max-width:900px)');
    const changed = () => setNarrow(media.matches);
    media.addEventListener('change', changed);
    return () => media.removeEventListener('change', changed);
  }, []);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const group = { data, receipt };
  const network = Boolean(data.peach_proxy || data.access || data.tunnel || (data.editable && data.port_editable !== false));
  const downloads = data.downloads;
  const groups: Group[] = [
    { title: '媒体库与入库', body: (panel) => <div {...groupRoot(panel)}>
      <MediaSettings {...group} /><MediaProcessingSettings receipt={receipt} />
      <LocalPlayerSettings receipt={receipt} />
    </div> },
    { title: '来源与订阅', body: (panel) => <SourceSettings {...group} panel={panel} /> },
    ...(downloads ? [{
      title: '下载渠道',
      body: (panel: ConfigurationPanel) => <DownloadGroup initial={downloads} receipt={receipt} panel={panel} />,
    }] : []),
    ...(network ? [{ title: '网络与访问', body: (panel: ConfigurationPanel) => <NetworkSettings {...group} panel={panel} /> }] : []),
    {
      title: '应用与维护',
      body: (panel) => <div {...groupRoot(panel)}>
        <GeneralSettings {...group} />
        <MaintenanceSettings {...group} reopenTutorial={reopenTutorial} />
      </div>,
    },
    { title: '系统诊断', body: panel => <div {...groupRoot(panel)}>{panel.active ? diagnostics : null}</div> },
  ];
  const at = Math.max(0, groups.findIndex((item) => item.title === chosen));
  const move = (event: KeyboardEvent<HTMLButtonElement>, from: number) => {
    const last = groups.length - 1;
    let next: number;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = from === last ? 0 : from + 1;
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = from === 0 ? last : from - 1;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = last;
    else return;
    event.preventDefault();
    choose(groups[next].title);
    buttons.current[next]?.focus();
  };
  return (
    <div ref={workspace} className="ui-configpage">
      <div className="ui-board-local-nav" role="tablist" aria-label="配置分区" data-section-nav="" data-section-items=""
        aria-orientation={narrow ? 'horizontal' : 'vertical'}>
        {groups.map(({ title }, i) => (
          <button key={title} ref={(node) => { buttons.current[i] = node }} type="button" role="tab"
            id={`${prefix}-tab-${i}`} aria-controls={`${prefix}-panel-${i}-0`} aria-selected={i === at}
            tabIndex={i === at ? 0 : -1} onClick={() => choose(title)} onKeyDown={(event) => move(event, i)}>
            {title}
          </button>
        ))}
      </div>
      {groups.map(({ title, body }, i) => (
        <Fragment key={title}>
          <h2 className="ui-configgroup">{title}</h2>
          {body({ index: i, id: `${prefix}-panel-${i}-0`, labelledBy: `${prefix}-tab-${i}`, active: i === at })}
        </Fragment>
      ))}
    </div>
  );
}
