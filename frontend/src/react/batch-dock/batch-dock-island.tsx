/* 批量条（ADR-0031 第 11g 步）：目录、回收站、垃圾文件与关注页多选时底部那块浮条。
 *
 * 常驻组件 `batch-dock`（`router/managed-routes.tsx` 的常驻表）：路由树把它画进壳常驻在 `body` 末尾的
 * `[data-batch-dock]`，宿主就是那个节点本身。壳只拿 `configureBatchDock` 给的命令式入口：每次重画选中态
 * 推一份计数与语境，句柄把它写进本模块的 store 再 `flushSync` 通知，返回时浮条已经画好——壳紧接着就
 * 补扫玻璃贴图（`syncGlassOptics`）。浮条外壳是共用的 `SelectionDock`（复核、标签页、关注管理同一块），
 * 它要 `.peach-react` 作用域里的 token 与 Preflight，所以组件外面包一层 `display: contents` 的
 * `.peach-react`，不占盒子。
 *
 * 浮条上的键是透明 `<button>`：尺寸、描边、字号与 17px 字形照 ADR-0031 的迁移前基线，全部写在
 * `batch-dock.css`；BoardUI `Button` 的 medium 档是 20px 字形、8px 内边距，对不上。危险键挂
 * `data-danger`，红色读 `../styles.css` 里全站那一份危险键规则。 */
import { useSyncExternalStore } from 'react';
import { flushSync } from 'react-dom';

import { SelectionDock } from '../components/selection-dock';
import { batchActions, type BatchDockApi, type BatchDockHost, type BatchDockProps } from './batch-dock-api';
import './batch-dock.css';

let host: BatchDockHost | null = null;
let props: BatchDockProps = { count: 0, context: 'catalog', junkDismissed: false };
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener) };
}

function notify(): void {
  for (const listener of [...listeners]) listener();
}

/* 这一面抛错、被错误边界卸掉之后没人订阅，推进来的只写进 store，不画、不抛。 */
const api: BatchDockApi = {
  render(next) { props = next; flushSync(notify) },
};

/** 接上壳给的宿主，拿回批量条的命令式入口。只调一次；壳的 `loadBatchDock` 接着在路由树里打开这一面。 */
export function configureBatchDock(next: BatchDockHost): BatchDockApi {
  host = next;
  return api;
}

/** 常驻表里的那一面：读本模块的 store，宿主还没接上时不画。 */
export function BatchDockSurface() {
  const view = useSyncExternalStore(subscribe, () => props);
  return host ? <BatchDock host={host} props={view} /> : null;
}

function BatchDock({ host: at, props: view }: { host: BatchDockHost; props: BatchDockProps }) {
  return (
    <div className="peach-react" data-batch-scope="" data-context={view.context}>
      <SelectionDock label="所选项目操作" visible={view.count > 0} count={`已选 ${view.count.toLocaleString()} 项`}>
        {batchActions(view.context, view.junkDismissed).map((action) => (
          <button key={`${action.group}:${action.operation}`} type="button" data-batch-group={action.group}
            data-batch-action={action.operation} data-danger={action.danger ? '' : undefined}
            onClick={(event) => at.run(action.group, action.operation, event.currentTarget)}>
            <svg aria-hidden viewBox="0 0 24 24"><use href={`#i-${action.glyph}`} /></svg>
            {action.label}
          </button>
        ))}
      </SelectionDock>
    </div>
  );
}
