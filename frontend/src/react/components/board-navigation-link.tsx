import type { ComponentProps } from 'react';

/** BoardUI 的链接槽使用 Peach 的原生地址；页内导航由调用方接管。 */
export default function BoardNavigationLink(props: ComponentProps<'a'>) {
  return <a {...props} />;
}
