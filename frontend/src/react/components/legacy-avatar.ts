/* `avatarInner`（`card-art/markup.ts`）画出来的头像放进 React 岛时，圆框里那张图的写法。
 *
 * 图铺满圆框、按 cover 裁。有人脸框时，`avatarFrame`（`card-art/framing.ts`）在图加载时往图上
 * 写内联的 left／top／width／height 百分比，把脸放大到框中，宽度常常超过 100%。岛里的 Tailwind
 * 预检给每张图加了 `max-width:100%`，不放开的话宽被夹回框宽、高照样放大，脸偏到左边，右侧
 * 露出底下的首字母。遗留层自己的 `.searchface img` 没有这条预检。 */
export const LEGACY_AVATAR_IMG =
  '[&_img]:absolute [&_img]:inset-0 [&_img]:size-full [&_img]:max-w-none [&_img]:max-h-none [&_img]:object-cover';
