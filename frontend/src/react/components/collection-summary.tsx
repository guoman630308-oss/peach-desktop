/* 列表页顶上那块汇总（旧 `.collection-summary`）：左边一个名目配一个大数，右边一句分项。
 *
 * 浅色是页面底上的一块填充面，深色反过来比页面亮一档（旧 `html:not(.dark)` 那条），14px 圆角、
 * 不压阴影。600px 以下改成上下两行。
 *
 * 数字回来之前，大数那一格是一条 96×32 的微光占位（旧 `.collection-summary strong .countskeleton`），
 * 行高照旧，数字到货时整块不改高度。`flush` 去掉块下方的间距：落在一行自己排间距的容器里时
 * （垃圾文件的计数行、`Page` 自己排间距的高清版），间距归那一层。壳铺的首屏骨架
 * （`src/island-skeleton.ts` 的 `islandSummary`）抄的是这里等待态的类名，改这里要一起改那边。 */
export function CollectionSummary({ label, figure, detail = '', pending = false, flush = false }: {
  label: string; figure: string; detail?: string; pending?: boolean; flush?: boolean;
}) {
  return (
    <div data-collection-summary=""
      className={`${flush ? '' : 'mb-5 '}flex items-end justify-between gap-4 rounded-surface bg-background-secondary-default px-6 py-5
      max-compact:flex-col max-compact:items-start max-compact:gap-3 max-compact:p-4
      dark:bg-background-primary-default`}>
      <div className="flex min-w-0 flex-col gap-2">
        <span className="text-body-regular text-text-secondary">{label}</span>
        <strong className="text-display-4-medium tabular-nums text-text-primary">
          {pending
            ? <span data-skeleton="count" aria-hidden className="relative inline-block h-8 w-24 rounded-2lg align-middle skeleton-sheen" />
            : figure}
        </strong>
      </div>
      {detail ? <p className="text-body-regular text-text-secondary">{detail}</p> : null}
    </div>
  );
}
