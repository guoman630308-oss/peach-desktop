/* 管理区页头（常驻组件 `manage-header`，`manage-header-island.tsx`）对壳的契约。
 *
 * 它画的是管理区正文上面那一块：管理条（一排下划线页签）、数据管理子页的面包屑、页面标题，以及
 * 回收站的说明行（读数与「清空回收站」）。当前在哪个管理区、菜单列哪几项、回收站读数由壳推进来
 * （`ManageHeaderProps`，定义与判据在 `src/manage-header.ts`，壳的启动骨架也用它）。
 *
 * 换页、确认弹层与写接口仍归壳：点下去回到 `openManage`、`openDataCleanup` 与清空回收站那条流程。
 * 页面标题的逐行揭示也由壳在每次推送之后做（换了页才放一遍），骨架阶段与路由树接手之后是同一处。 */
import type { ManageHeaderProps } from '../../manage-header';

export type { ManageEntry, ManageHeaderProps, TrashCount } from '../../manage-header';

export interface ManageHeaderHost {
  /** 岛画进去的那一层（`[data-manage-header]`，本身 `display: contents`）。 */
  root: HTMLElement;
  /** 管理条上的一项。 */
  openManage(section: string): void;
  /** 面包屑的上一级。带修饰键或中键的点击不经这里，照链接自己的 href 走（新标签页、右键菜单）。 */
  openDataCleanup(): void;
  /** 回收站说明行右端那颗键：确认弹层、写接口与回执都在壳里，确认之后整页读数重取。 */
  emptyTrash(): void;
}

export interface ManageHeaderApi {
  render(props: ManageHeaderProps): void;
}
