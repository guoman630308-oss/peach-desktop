import { useSyncExternalStore, type ReactNode } from 'react';
import { AnimatePresence, motion, useIsPresent } from 'motion/react';
import { useFocusVisible } from 'react-aria';

const reduceQuery = '(prefers-reduced-motion: reduce)';
const readReduce = () => matchMedia(reduceQuery).matches;
const subscribeReduce = (notify: () => void) => {
  const media = matchMedia(reduceQuery);
  media.addEventListener('change', notify);
  return () => media.removeEventListener('change', notify);
};

interface SelectionDockProps {
  children: ReactNode;
  count: string;
  label: string;
  visible: boolean;
}

function DockSurface({ children, count, label }: Omit<SelectionDockProps, 'visible'>) {
  const present = useIsPresent();
  const reduce = useSyncExternalStore(subscribeReduce, readReduce, () => false);
  const { isFocusVisible } = useFocusVisible();
  const instant = reduce || isFocusVisible;
  const hidden = { opacity: 0, transform: 'translate(-50%, 8px)' };
  return (
    <motion.div role="group" aria-label={label} data-selection-dock data-glass-pane=""
      inert={!present} aria-hidden={!present || undefined}
      initial={instant ? false : hidden}
      animate={{ opacity: 1, transform: 'translate(-50%, 0px)' }}
      exit={{ ...hidden, transition: { duration: instant ? 0 : 0.12 } }}
      transition={{ duration: instant ? 0 : 0.16, ease: [0.23, 1, 0.32, 1] }}>
      <span role="status" className="px-2 text-body-2-regular whitespace-nowrap text-text-primary">
        {count}
      </span>
      {children}
    </motion.div>
  );
}

/** 持续挂载这个外壳，数量变化只更新内容，清空选择由内部完成退出。 */
export function SelectionDock({ visible, ...props }: SelectionDockProps) {
  return <AnimatePresence>{visible ? <DockSurface key="selection" {...props} /> : null}</AnimatePresence>;
}
