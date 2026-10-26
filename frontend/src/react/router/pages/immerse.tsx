/* 沉浸模式的页面元素（ADR-0031）：`/immerse` 在页面组里，不算覆盖、不带背景。
 *
 * 启动与没人认领的历史变化落在 `/immerse` 上时，从地址上的 `?id=` 那一条打开沉浸（`ShellActions.openImmerse`）；
 * 顶栏按钮认领写地址后也由元素打开。沉浸里每换一条由壳认领 replace 写 `?id=`，元素按页面打开序号挂 key，
 * 不重挂、不重开，条目数不变。
 *
 * 卸下不收沉浸：后退离开 `/immerse` 时沉浸照旧开着，由 Escape 或关闭键收起，收起时壳回首页并写一条地址。 */
import { useContext, type ReactNode } from 'react';

import { peachHistory } from '@peach/history';

import { ShellActionsContext, useOpenEpoch } from '../router';
import { useRouteOpen } from './overlay';

/* 地址上的那一条：`?id=` 是数字才算，不然从头开始。 */
function startId(): number | undefined {
  const id = new URLSearchParams(peachHistory.navigation.location.search).get('id');
  return /^\d+$/.test(id || '') ? Number(id) : undefined;
}

function ImmerseOpen() {
  const actions = useContext(ShellActionsContext);
  useRouteOpen(() => { actions?.openImmerse(startId()) }, true);
  return null;
}

/** `/immerse` 的元素。 */
export function ImmerseMatch(): ReactNode {
  return <ImmerseOpen key={useOpenEpoch()} />;
}
