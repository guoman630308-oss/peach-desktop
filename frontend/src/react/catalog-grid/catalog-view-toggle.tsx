import { Radio, RadioGroup } from 'react-aria-components';
import { RiLayoutGridLine, RiListUnordered } from '@remixicon/react';
import { SEGMENTED_GLASS_TRACK, SEGMENT_GLASS } from '../components/segmented';
import { setCatalogView, useCatalogView } from './catalog-view';

export function CatalogViewToggle() {
  const view = useCatalogView();
  return <RadioGroup aria-label="馆藏显示方式" orientation="horizontal" value={view} onChange={setCatalogView}
    data-entity-layout="" className={SEGMENTED_GLASS_TRACK}>
    <Radio value="cards" aria-label="卡片" className={SEGMENT_GLASS} data-glass-segment="">
      <span title="卡片" className="pointer-events-none contents"><RiLayoutGridLine aria-hidden="true" className="size-4" /></span>
    </Radio>
    <Radio value="list" aria-label="列表" className={SEGMENT_GLASS} data-glass-segment="">
      <span title="列表" className="pointer-events-none contents"><RiListUnordered aria-hidden="true" className="size-4" /></span>
    </Radio>
  </RadioGroup>;
}
