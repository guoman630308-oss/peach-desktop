/* 后台任务仍在推进、但没有总量可言时的 Loading Dots（`peach-web-ui`）。
 *
 * 不用 Spinner：Spinner 只反馈用户刚刚点下的那一下。也不画没有分母的进度条。
 * 三颗点的错相由 `../styles.css` 的 `dot-wave-*` 给，减少动效时全局规则关掉动画。
 *
 * `inline` 那一档住在按钮里（「载入更多」按下去之后）：按钮里不能放段落，换成行内元素，
 * 字号跟着按钮走。 */

export function LoadingDots({ label, inline = false }: { label: string; inline?: boolean }) {
  const dots = (
    <span aria-hidden className="inline-flex shrink-0 items-center gap-1">
      <i className="dot-wave-0 size-1 rounded-full bg-current" />
      <i className="dot-wave-1 size-1 rounded-full bg-current" />
      <i className="dot-wave-2 size-1 rounded-full bg-current" />
    </span>
  );
  if (inline) return <span role="status" className="inline-flex items-center gap-2">{dots}{label}</span>;
  return (
    <p role="status" className="flex min-w-0 items-center gap-2 text-caption-1-regular text-text-secondary">
      {dots}
      <span className="min-w-0 wrap-anywhere">{label}</span>
    </p>
  );
}
