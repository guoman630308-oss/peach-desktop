/* React 子树取 TanStack Query 客户端的入口（ADR-0031「前端基础库随页面引入」）。
 *
 * 实例建在 `src/query/client.ts`，按 `@peach/query` 引用，别名直接解析到同一份源码：
 * 壳启动时取的筛选栏数据、页面级 `prefetch` 与组件里的 `useQuery` 读的是同一份缓存。 */
export { queryClient } from '@peach/query';
