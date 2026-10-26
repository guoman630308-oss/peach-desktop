/* EvilCharts 源码里的 `@/lib/utils`：条件类名先经 `clsx` 摊平，再交给 BoardUI 的 `cx`。
 *
 * 上游是 shadcn 项目，`cn` 由 `shadcn init` 生成，不在注册表条目里。这里接的是 BoardUI 扩展过
 * 的 `tailwind-merge`：它认得 `text-caption-1-regular` 这类字阶类，和颜色类同处一串时不会被
 * 当成颜色丢掉。路径别名写在 `vite.config.ts`、`vitest.config.ts` 与 `src/react/tsconfig.json`。 */
import { clsx, type ClassValue } from 'clsx';

import { cx } from '@/utils/cx';

export function cn(...inputs: ClassValue[]): string {
  return cx(clsx(inputs));
}
