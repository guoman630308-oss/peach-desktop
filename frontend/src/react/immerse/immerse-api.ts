/* 沉浸模式（常驻组件 `immerse`，`immerse-island.tsx`）对壳的契约：壳只拿命令式入口，片单、每一格的播放器、
 * 手势、动作键与作者标题都在 `peach-app.js` 里。地址栏、首页筛选、头像拼法与「关掉之后去哪儿」归壳。 */

export interface ImmerseHost {
  /** 首页当前的筛选（壳的 `state`）：片单按它随机抽样，画幅不算。 */
  filters(): Record<string, string | number | null | undefined>;
  /** 双击左右半区快退快进的秒数（`appSettings.seekSeconds`）。 */
  seekSeconds(): number;
  /** 来源脱盘：这样的片子拉不到流，进了片单就是一条黑屏加载中。 */
  sourceOffline(location: string): boolean;
  /** 标题那一行的纯文本（遗留层 `javDisplayName`）。 */
  displayName(item: Record<string, unknown>): string;
  /** 每换一条：壳用 replace 把 `/immerse?id=` 写进地址栏，刷新落回同一条。 */
  route(id: number): void;
  /** 沉浸模式关掉之后：壳回首页。 */
  closed(): void;
  openItem(id: number): void;
  openEntity(kind: 'performer' | 'creator', name: string): void;
  openUnowned(): void;
  /** 操作回执；给了 `undo` 就带一颗撤销键。 */
  toast(message: string, options?: { undo?: () => Promise<void> }): void;
  /** 带提示音的警告（片单为空）。 */
  warn(message: string): void;
  failure(action: string, error: unknown): void;
}

export interface ImmerseApi {
  /** 打开沉浸模式；`startId` 不在这一批随机抽样里时取它的详情插到最前。 */
  open(startId: number | null): Promise<void>;
  /** 关闭键与 Escape 走的那一条：拆掉所有格、按会话取消读取，然后交给壳的 `closed`。 */
  close(): void;
  isOpen(): boolean;
  /** 该响应播放快捷键的 video：当前出画的那一格。 */
  activeVideo(): HTMLVideoElement | null;
}
