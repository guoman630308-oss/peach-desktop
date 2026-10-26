import { describe, expect, it } from 'vitest';
import { clampWall, expandedWallTiles, finiteWallFilters, finiteWallTile, visibleWallTiles, wallExtent, wallHasCover, wallTileAt, WallExploration } from '../../src/react/video-wall/wall-layout';

const view = { width: 1280, height: 720 };
const camera = { x: 16, y: 16, scale: 1 };
describe('二维播放墙', () => {
  it('探索作品按四向浏览顺序分配，返回已有位置和续页均保持身份与几何', () => {
    const pool = new WallExploration(), positions = new Map<string, number>();
    for (const point of [{ x:0,y:0 },{ x:3000,y:0 },{ x:3000,y:3000 },{ x:-3000,y:3000 },{ x:-3000,y:-3000 }]) {
      const visible = visibleWallTiles(12, false, { ...camera,x:point.x,y:point.y }, view);
      for (const tile of pool.assign(visible, 200, true)) {
        if (positions.has(tile.key)) expect(tile.index).toBe(positions.get(tile.key));
        else { expect([...positions.values()]).not.toContain(tile.index); positions.set(tile.key, tile.index) }
        expect(pool.tile(tile.key, 400)).toEqual(tile);
      }
    }
    expect(pool.size).toBeGreaterThan(60);
    const returned = pool.assign(visibleWallTiles(12, false, camera, view), 400, true);
    for (const tile of returned) expect(tile.index).toBe(positions.get(tile.key));
  });
  it('未取得下一页时保留待填磁贴，范围耗尽后才循环', () => {
    const pool = new WallExploration(), visible = visibleWallTiles(12, false, camera, view);
    const first = pool.assign(visible, 3, true);
    expect(first.filter(tile => tile.index >= 0)).toHaveLength(3);
    expect(first.some(tile => tile.index === -1)).toBe(true);
    const next = pool.assign(visible, 40, true);
    expect(new Set(next.map(tile => tile.index)).size).toBe(next.length);
    expect(next.slice(0, 3)).toEqual(first.slice(0, 3));
    const ended = new WallExploration().assign(visible, 3, false);
    expect(ended.every(tile => tile.index >= 0 && tile.index < 3)).toBe(true);
    expect(new WallExploration().assign(visible, 0, false).every(tile => tile.index === -1)).toBe(true);
  });
  it('队列可定位未展示作品，后续分配不会再次使用它；换批优先使用新增页', () => {
    const pool = new WallExploration(), point = { x:-2500,y:1800 };
    const chosen = pool.nearest(100, 77, point)!;
    expect(chosen.index).toBe(77);
    expect(pool.nearest(140, 77, point)).toEqual(chosen);
    pool.prefer(100);
    const tiles = pool.assign(visibleWallTiles(12, false, camera, view), 140, true);
    expect(tiles[0]!.index).toBe(100);
    expect(tiles.map(tile => tile.index)).not.toContain(77);
  });
  it('只有有封面引用的视频进入墙，缩略图不能替代资格', () => {
    expect(wallHasCover({ id:1, is_jav:true, code:'PCH-001', has_cover:true })).toBe(true);
    expect(wallHasCover({ id:2, cover_key:'custom', has_cover:true })).toBe(true);
    expect(wallHasCover({ id:3, has_cover:false, has_thumb:true })).toBe(false);
    expect(wallHasCover({ id:4, has_cover:true })).toBe(false);
    expect(wallHasCover({ id:5, has_cover:true, code:'PCH-005', medium:'photo' })).toBe(false);
  });
  it('展开所在块完整让位，选中磁贴变成方形 6×6，收起位置可重建', () => {
    for (const position of ['0:0:0','1:0:2','2:1:11','-1:-2:5']) {
      const active = wallTileAt(60, false, position)!;
      const expanded = expandedWallTiles(60, false, active);
      const focus = expanded.get(position)!;
      expect(expanded.size).toBe(12);
      expect(focus.width).toBeCloseTo(614.08);
      expect(focus.height).toBe(focus.width);
      const rects = [...expanded.values()];
      for (let a = 0; a < rects.length; a += 1) for (let b = a + 1; b < rects.length; b += 1) {
        const one = rects[a]!, two = rects[b]!;
        expect(one.x < two.x + two.width && one.x + one.width > two.x && one.y < two.y + two.height && one.y + one.height > two.y).toBe(false);
      }
      expect(wallTileAt(60, false, position)).toEqual(active);
    }
  });
  it('空集合不产生磁贴', () => {
    expect(visibleWallTiles(0, false, camera, view)).toEqual([]);
  });
  it('有限墙队列选择保留唯一位置', () => {
    expect(finiteWallTile(0, 0)).toBeNull();
    expect(finiteWallTile(25, 17)).toEqual(wallTileAt(25, true, '1:0:5'));
  });
  it('探索墙在正负两个方向都循环，实例键唯一', () => {
    for (const position of [-100_000, -3000, 16, 3000, 100_000]) {
      const tiles = visibleWallTiles(25, false, { ...camera, x: position, y: -position }, view);
      expect(tiles.length).toBeGreaterThan(0);
      expect(new Set(tiles.map(tile => tile.key)).size).toBe(tiles.length);
      expect(tiles.every(tile => tile.index >= 0 && tile.index < 25)).toBe(true);
    }
  });
  it('有限墙里每个条目只有一个位置，续页不移动已有条目', () => {
    const huge = { width: 3000, height: 3000 };
    const first = visibleWallTiles(17, true, camera, huge);
    const next = visibleWallTiles(53, true, camera, huge);
    expect(first.length).toBe(17);
    expect(new Set(next.map(tile => tile.index)).size).toBe(next.length);
    for (const tile of first) expect(next.find(other => other.index === tile.index)).toEqual(tile);
  });
  it('只有视口附近的磁贴进入渲染，数量不随馆藏增长', () => {
    expect(visibleWallTiles(80_000, true, camera, view).length).toBeLessThan(100);
    expect(visibleWallTiles(80_000, false, camera, view).length).toBeLessThan(100);
  });
  it('有限墙阻止拖向空白，内容小于屏幕时锁定起点', () => {
    expect(clampWall({ ...camera, x: 4000, y: 4000 }, view, wallExtent(12), true)).toEqual(camera);
    const clamped = clampWall({ ...camera, x: -1e8, y: -1e8 }, view, wallExtent(49), true);
    expect(clamped.x).toBe(view.width - wallExtent(49).width - 16);
    expect(clamped.y).toBe(view.height - wallExtent(49).height - 16);
    expect(clampWall({ ...camera, x: -1e8 }, view, wallExtent(49), false).x).toBe(-1e8);
  });
  it('探索磁贴几何不随取数增长改变', () => {
    for (const key of ['-2:3:0','3:-2:4','8:6:11']) {
      const { index:_, ...first } = wallTileAt(12, false, key)!;
      const { index:__, ...next } = wallTileAt(1800, false, key)!;
      expect(next).toEqual(first);
    }
  });
  it('检索、实体条件、状态和时长使用有限墙，默认来源与 JAV 语境仍可探索', () => {
    for (const key of ['q','performer','series','creator','studio','owner','tag','state','orient','len','dur_min','dur_max','region']) {
      expect(finiteWallFilters({ [key]: 'value' })).toBe(true);
    }
    expect(finiteWallFilters({ jav: '1', loc: 'local', sort: 'seed' })).toBe(false);
  });
});
