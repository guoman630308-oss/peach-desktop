/* 矩形块拼贴参考 Folia 的 blockTemplates/layout，AGPL-3.0，固定 revision
 * 9a01e788ed52d25ea01b1f0d37934e5e371ae82b。Peach 按浏览位置接续馆藏。 */
import { getBlockTemplate, getBlockReflow } from './folia/blockTemplates';
import type { MediaItem } from '../catalog-grid/types';
export interface WallRect { x: number; y: number; width: number; height: number }
export interface WallCamera { x: number; y: number; scale: number }
export interface WallTile extends WallRect { key: string; index: number }
export interface WallSize { width: number; height: number }

const PITCH = 103.36;
const GAP = 6.08;
const BLOCK_W = 12 * PITCH;
const BLOCK_H = 8 * PITCH;
const SLOTS = 12;
const mod = (value: number, count: number) => ((value % count) + count) % count;
const intersects = (a: WallRect, b: WallRect) => a.x < b.x + b.width && a.x + a.width > b.x
  && a.y < b.y + b.height && a.y + a.height > b.y;

export function wallShape(count: number, finite: boolean): WallSize & { columns: number } {
  const blocks = Math.max(1, Math.ceil(count / SLOTS));
  const columns = finite ? 3 : Math.min(3, blocks);
  return { columns, width: columns * BLOCK_W, height: Math.ceil(blocks / columns) * BLOCK_H };
}

function blockSlot(column: number, row: number, slot: number): WallRect {
  const { x,y,cols:w,rows:h } = getBlockTemplate(column, row)[slot]!;
  return { x: column * BLOCK_W + x * PITCH, y: row * BLOCK_H + y * PITCH,
    width: w * PITCH - GAP, height: h * PITCH - GAP };
}

/** 只接受有实际封面引用的视频；缩略图设置不改变入墙资格。 */
export function wallHasCover(item: MediaItem): boolean {
  return (!item.medium || item.medium === 'video') && !!item.has_cover && !!(item.cover_key || item.code);
}

/** 按实例键定位，不依赖视口裁剪结果，展开播放器可留在屏外。 */
export function wallTileAt(count: number, finite: boolean, key: string): WallTile | null {
  if (!count) return null;
  const [column,row,slot] = key.split(':').map(Number);
  if (![column,row,slot].every(Number.isInteger) || slot! < 0 || slot! >= SLOTS) return null;
  const shape = wallShape(count, finite), rows = shape.height / BLOCK_H;
  if (finite && (column! < 0 || column! >= shape.columns || row! < 0 || row! >= rows)) return null;
  const localCol = finite ? column! : mod(column!, shape.columns);
  const localRow = finite ? row! : mod(row!, rows);
  const rank = (localRow * shape.columns + localCol) * SLOTS + slot!;
  if (finite && rank >= count) return null;
  const rect = blockSlot(column!, row!, slot!);
  return { ...rect, index:rank % count, key };
}

/** 有限墙队列选择落到作品的唯一位置。 */
export function finiteWallTile(count: number, index: number): WallTile | null {
  if (index < 0 || index >= count) return null;
  const block = Math.floor(index / SLOTS);
  return wallTileAt(count, true, `${block % 3}:${Math.floor(block / 3)}:${index % SLOTS}`);
}

/** Folia 原始让位表：展开成 6×6，所在块的另外十一格同步换位。 */
export function expandedWallTiles(_count: number, _finite: boolean, active: WallTile): Map<string, WallRect> {
  const [column,row,slot] = active.key.split(':').map(Number);
  const reflow = getBlockReflow(column!, row!, slot!);
  return new Map((reflow || []).map((rect, index) => [`${column}:${row}:${index}`, {
    x:column! * BLOCK_W + rect.x * PITCH, y:row! * BLOCK_H + rect.y * PITCH,
    width:rect.cols * PITCH - GAP, height:rect.rows * PITCH - GAP,
  }]));
}

export function wallExtent(count: number): WallSize {
  let width = 0, height = 0;
  for (let index = Math.max(0, count - SLOTS * 3); index < count; index += 1) {
    const block = Math.floor(index / SLOTS);
    const rect = blockSlot(block % 3, Math.floor(block / 3), index % SLOTS);
    width = Math.max(width, rect.x + rect.width);
    height = Math.max(height, rect.y + rect.height);
  }
  // 满行的宽度不会因最后一行只占一块而缩回去。
  return { width: count >= SLOTS * 3 ? 3 * BLOCK_W - GAP : width, height };
}

export function clampWall(camera: WallCamera, viewport: WallSize, extent: WallSize, finite: boolean): WallCamera {
  if (!finite) return camera;
  const clamp = (value: number, content: number, screen: number) =>
    Math.min(16, Math.max(Math.min(16, screen - content * camera.scale - 16), value));
  return { ...camera, x: clamp(camera.x, extent.width, viewport.width), y: clamp(camera.y, extent.height, viewport.height) };
}

export function visibleWallTiles(count: number, finite: boolean, camera: WallCamera, viewport: WallSize): WallTile[] {
  if (!count || !viewport.width || !viewport.height) return [];
  const pad = 180 / camera.scale;
  const view = { x: -camera.x / camera.scale - pad, y: -camera.y / camera.scale - pad,
    width: viewport.width / camera.scale + pad * 2, height: viewport.height / camera.scale + pad * 2 };
  const shape = wallShape(count, finite);
  const rows = shape.height / BLOCK_H;
  const result: WallTile[] = [];
  for (let row = Math.floor(view.y / BLOCK_H); row <= Math.floor((view.y + view.height) / BLOCK_H); row += 1) {
    for (let column = Math.floor(view.x / BLOCK_W); column <= Math.floor((view.x + view.width) / BLOCK_W); column += 1) {
      if (finite && (column < 0 || column >= shape.columns || row < 0 || row >= rows)) continue;
      const localCol = finite ? column : mod(column, shape.columns);
      const localRow = finite ? row : mod(row, rows);
      for (let slot = 0; slot < SLOTS; slot += 1) {
        const rank = (localRow * shape.columns + localCol) * SLOTS + slot;
        if (finite && rank >= count) continue;
        const rect = blockSlot(column, row, slot);
        if (intersects(rect, view)) result.push({ ...rect, index: rank % count, key: `${column}:${row}:${slot}` });
      }
    }
  }
  return result;
}

/** 按浏览顺序分配作品，续页不改变已分配位置；全范围耗尽才循环。 */
export class WallExploration {
  private readonly positions = new Map<string, number>();
  private readonly used = new Set<number>();
  private cursor = 0;
  private repeat = 0;

  get size(): number { return this.used.size }

  prefer(index: number): void { this.cursor = index }

  tile(key: string, count: number): WallTile | null {
    const index = this.positions.get(key);
    const tile = index === undefined ? null : wallTileAt(count, false, key);
    return tile ? { ...tile, index:index! } : null;
  }

  assign(tiles: readonly WallTile[], count: number, hasMore: boolean): WallTile[] {
    return tiles.flatMap(tile => {
      let index = this.positions.get(tile.key);
      if (index === undefined) {
        if (this.used.size < count) {
          while (this.used.has(this.cursor % count)) this.cursor++;
          index = this.cursor++ % count;
          this.used.add(index);
        } else if (hasMore || !count) return [{ ...tile, index:-1 }];
        else index = this.repeat++ % count;
        this.positions.set(tile.key, index);
      }
      return [{ ...tile, index }];
    });
  }

  fresh(): { x:number; y:number } {
    let column = 0;
    for (const key of this.positions.keys()) column = Math.max(column, Number(key.split(':')[0]));
    return { x:(column + 2) * BLOCK_W, y:0 };
  }

  nearest(count: number, index: number, point: { x:number; y:number }): WallTile | null {
    if (index < 0 || index >= count) return null;
    let nearest: WallTile | null = null, distance = Infinity;
    for (const [key, assigned] of this.positions) {
      if (assigned !== index) continue;
      const tile = this.tile(key, count)!;
      const d = (point.x - tile.x - tile.width / 2) ** 2 + (point.y - tile.y - tile.height / 2) ** 2;
      if (d < distance) { nearest = tile; distance = d }
    }
    if (nearest) return nearest;
    const fresh = this.fresh(), key = `${Math.round(fresh.x / BLOCK_W)}:0:0`;
    this.positions.set(key, index); this.used.add(index);
    return this.tile(key, count);
  }
}

export const WALL_FILTER_KEYS = ['q','performer','series','creator','studio','owner','tag','state','orient','len','dur_min','dur_max','region'] as const;

export function finiteWallFilters(filters: Readonly<Record<string, string>> = {}): boolean {
  return WALL_FILTER_KEYS.some(key => !!filters[key]);
}
