/* 模态弹层的外壳：遮罩、浮层那一档面与圆角、里面一个 React Aria `Dialog`。
 *
 * 注册表里没有模态弹层，用 React Aria 的 `Modal` 组合，差异登记在 `../boardui/ORIGIN.md`。
 * 层级取遗留壳的 `--layer-dialog`（`z-dialog`）：顶栏和侧栏各有自己的 z-index，比 Tailwind
 * 那档 `z-50` 都高，遮罩压不住它们，弹层开着时那两块还亮着。
 *
 * 宽度按用途取一档：换头像一排四张候选、裁剪封面放得下整张封套、填一份表同
 * `ui-kit/modal.css` 里 `.ui-geist-modal` 的 540px。
 *
 * 页头左边那枚 56px 图标方块也在这里（`ModalGlyph`）：它是弹层页头的一部分，不是卡。 */
import type { ReactNode } from 'react';
import { useFocusVisible } from 'react-aria';
import { Dialog, Modal, ModalOverlay } from 'react-aria-components';

const WIDTH = {
  'avatar-picker': 'max-w-avatar-picker',
  'cover-crop': 'max-w-cover-crop',
  form: 'max-w-form-modal',
  collections: 'max-w-4xl',
} as const;

export function ModalFrame({ isOpen, onOpenChange, width, label, children }: {
  isOpen: boolean;
  onOpenChange(open: boolean): void;
  width: keyof typeof WIDTH;
  /** 弹层没有 `Heading slot="title"` 时的无障碍名称。 */
  label?: string;
  children: ReactNode;
}) {
  const { isFocusVisible } = useFocusVisible();
  return (
    <ModalOverlay isOpen={isOpen} onOpenChange={onOpenChange} isDismissable
      data-modal-motion="" data-modal-kind={width} data-motion-instant={isFocusVisible || undefined}
      className="fixed inset-0 z-dialog flex items-center justify-center bg-scrim p-4">
      <Modal data-modal-surface="" data-modal-width={width} className={`flex max-h-full w-full ${WIDTH[width]} flex-col overflow-hidden rounded-2-5xl border border-separator-border bg-background-full shadow-dropdown`}>
        {/* 调用方自己的正文滚动区收得下时这一层不滚；矮视口里连页头加按钮都放不下，就整块滚，
            关闭键总够得着。 */}
        <Dialog aria-label={label} className="flex min-h-0 flex-col overflow-y-auto overscroll-contain outline-none">{children}</Dialog>
      </Modal>
    </ModalOverlay>
  );
}

/** 弹层页头左边的方图标槽，字形由调用方给（`size-6`）。 */
export function ModalGlyph({ children }: { children: ReactNode }) {
  return (
    <span aria-hidden className="flex size-14 shrink-0 items-center justify-center rounded-2xl border border-separator-border bg-background-secondary-default text-foreground-icon-secondary">
      {children}
    </span>
  );
}
