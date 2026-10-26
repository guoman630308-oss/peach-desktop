/* 取数与缓存（@peach/query）：全站唯一 QueryClient，以及 Application 与 React 页面共读的来源查询。
 * 别名直接解析到本目录，实例、查询键与取数函数随主包发出一份。 */
export { queryClient } from './client';
export * from './media-sources';
export * from '@tanstack/query-core';
