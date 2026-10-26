/* 横向骨架铺到右缘：铺满，而且整批只让浏览器布局几次。
 *
 * 改过 DOM 之后再读一次宽度，浏览器就得当场把整页布局一遍。冷启动时资料页那几排骨架逐枚
 * 追加、逐枚量，实测一排一百多毫秒。这里用桩出来的几何数「改了又读」发生了几次。 */
import { afterEach, describe, expect, it } from 'vitest';

import { fillSkeletonTier, fitSkeleton } from '../src/ui-kit';

const ROW = 1000;
const SLOT = 100;
let layouts = 0;
let dirty = false;
const rect = (left: number, right: number) => ({ left, right, top: 0, bottom: 20, width: right - left, height: 20 });
const original = HTMLElement.prototype.getBoundingClientRect;

function row(kind: string): HTMLElement {
  const node = document.createElement('div');
  node.dataset.skeletonTier = kind;
  const read = () => { if (dirty) { layouts++; dirty = false; } };
  Object.defineProperty(node, 'clientWidth', { get: () => { read(); return ROW; } });
  Object.defineProperty(node, 'scrollWidth', { get: () => { read(); return Math.max(ROW, node.children.length * SLOT); } });
  const insert = node.insertAdjacentHTML.bind(node);
  node.insertAdjacentHTML = (where: InsertPosition, html: string) => { dirty = true; insert(where, html); };
  return node;
}

HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect(this: HTMLElement) {
  const parent = this.parentElement;
  if (this.dataset.skeletonTier) { if (dirty) { layouts++; dirty = false; } return rect(0, ROW) as DOMRect; }
  if (parent?.dataset.skeletonTier) {
    if (dirty) { layouts++; dirty = false; }
    const index = [...parent.children].indexOf(this);
    return rect(index * SLOT, (index + 1) * SLOT) as DOMRect;
  }
  return original.call(this);
};

afterEach(() => { layouts = 0; dirty = false; document.body.replaceChildren(); });

describe('横向骨架补满', () => {
  it('几排一起补：每排都越过右缘，整批只布局两次', () => {
    const root = document.createElement('div');
    const rows = [row('av'), row('brandpill'), row('pill')];
    root.append(...rows);
    document.body.append(root);
    fitSkeleton(root);
    for (const node of rows) expect(node.children.length * SLOT).toBeGreaterThan(ROW);
    expect(layouts).toBe(2);
  });

  it('已经铺满的那排再补一次不追加', () => {
    const root = document.createElement('div');
    const node = row('pill');
    root.append(node);
    document.body.append(root);
    fitSkeleton(root);
    const filled = node.children.length;
    fitSkeleton(root);
    expect(node.children.length).toBe(filled);
  });

  it('单独补一排也越过右缘，多出来的不超过一轮宽度序列', () => {
    const node = row('av');
    document.body.append(node);
    fillSkeletonTier(node, 'av');
    expect(node.children.length * SLOT).toBeGreaterThan(ROW);
    expect(node.children.length).toBeLessThanOrEqual(ROW / SLOT + 2);
  });
});
