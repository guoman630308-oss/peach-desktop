/* 代表作表：女优、厂牌名对应一部能取得头像的作品 id。
 * 卡片署名、Mix 叠放头像与沉浸作者缺实体图时读取这张表，回落到 /avatar?id=。
 * catalog-bars.ts 经共享 QueryClient 读取 /api/tops 后更新该表，读者共用同一源码实例。
 * 只收能取得头像的作品；has_avatar 表示裁图或印相可用，/avatar 可按需生成。 */

/** `/api/tops` 里一枚圆头像：`k` 是名字，`rep` 是代表作 id。 */
export interface RepresentativeRow { k: string; rep?: number | null; has_avatar?: boolean }

const representatives = new Map<string, number>();

/** 记下一批代表作。只增不减：换一批之后，屏幕上旧卡片的回落照样找得到。 */
export function rememberRepresentatives(rows: readonly RepresentativeRow[] | null | undefined): void {
  (rows || []).forEach(row => { if (row.rep && row.has_avatar) representatives.set(row.k, row.rep); });
}

/** 这个名字的代表作 id，表里没有就是 `undefined`。 */
export function representativeOf(name: string): number | undefined {
  return representatives.get(name);
}
