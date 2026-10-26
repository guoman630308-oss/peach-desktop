import { Dialog, type DialogProps } from 'react-aria-components';
import { useMovingSurface } from './use-moving-surface';

/** 短菜单的悬停面；空隙与禁用项不映射到其他动作。 */
export function MenuDialog(props: DialogProps) {
  const ref = useMovingSurface('hover');
  return <Dialog {...props} ref={ref} />;
}
