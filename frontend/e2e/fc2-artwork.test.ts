import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Browser } from 'playwright-core';

import { launch, layout, settle, VIEWPORTS } from './harness.ts';
import { ITEM, openItemPage } from './item-fixture.ts';

describe('FC2 合集与封面取景', () => {
  let browser: Browser;
  before(async () => { browser = await launch(); });
  after(async () => { await browser?.close(); });

  for (const viewport of VIEWPORTS) {
    it(`${viewport.name}: 本篇与特典的标题和队列标签一致`, async () => {
      const opened = await openItemPage(browser, `/assets/22?parts=${ITEM.part}`, viewport);
      try {
        const parts = await opened.page.evaluate(async (id) =>
          (await fetch(`/api/parts?id=${id}`)).json(), ITEM.part);
        const labels = ['本篇', '特典 1', '特典 3'];
        parts.items = parts.items.map((item, index) => ({ ...item, part_label: labels[index] }));
        await opened.page.route('**/api/parts?*', (route) => route.fulfill({ json: parts }));
        await opened.page.reload({ waitUntil: 'load' });
        await opened.page.locator('#stage .partlabel').waitFor();
        assert.equal(await opened.page.locator('#stage .partlabel').innerText(), '特典 1');
        for (const [index, id] of [21, 22, 23].entries()) {
          assert.ok((await opened.page.locator(`#stage [data-queue-item="${id}"]`).innerText()).includes(labels[index]));
        }
        const bounds = await layout(opened.page);
        assert.ok(bounds.scrollWidth <= bounds.viewportWidth + 1, JSON.stringify(bounds));
      } finally { await opened.close(); }
    });

    it(`${viewport.name}: 正封完整居中，卡片边缘使用封面模糊补底`, async () => {
      const opened = await openItemPage(browser, '/', viewport, {
        ready: '[data-media-card]', settings: { javLayout: 'big', homeLayout: 'big', javImage: 'cover' },
      });
      try {
        const base = await opened.page.evaluate(async (id) => (await fetch(`/api/item?id=${id}`)).json(), ITEM.plain);
        const item = { ...base, code: 'IPZZ-477', is_jav: true, has_cover: true, tags: [], part_group: null,
          cover_frame: { cx: .75, cy: .25 },
          poster_box: { x0: 1153, y0: 0, x1: 2184, y1: 1468, px: [2184, 1468], method: 'fold' } };
        await opened.page.route('**/api/items?*', (route) => route.fulfill({ json: { items: [item], total: 1, has_more: false } }));
        await opened.page.route('**/cover?*', (route) => route.fulfill({ contentType: 'image/svg+xml',
          body: '<svg xmlns="http://www.w3.org/2000/svg" width="2184" height="1468"><rect width="1153" height="1468" fill="brown"/><rect x="1153" width="1031" height="1468" fill="black"/></svg>' }));
        await opened.page.reload({ waitUntil: 'load' });
        await opened.page.waitForFunction(() => document.querySelector<HTMLImageElement>('[data-media-card] img.cover')?.naturalWidth === 2184);
        await settle(opened.page);
        const framed = await opened.page.locator('[data-media-card] img.cover').first().evaluate((img: HTMLImageElement) => {
          const pic = img.closest<HTMLElement>('.pic,[data-media-pic]')!;
          pic.style.setProperty('--cover-blur', `url("${img.currentSrc}")`);
          img.dispatchEvent(new Event('load'));
          const card = pic.getBoundingClientRect(), image = img.getBoundingClientRect();
          const left = image.left + image.width * 1153 / 2184 - card.left;
          const right = card.right - image.right;
          return { panel: img.classList.contains('panel'), blur: pic.style.getPropertyValue('--cover-blur'),
            background: getComputedStyle(pic, '::before').backgroundImage, left, right,
            heightGap: Math.abs(card.height - image.height) };
        });
        assert.equal(framed.panel, true);
        assert.match(framed.blur, /^url\(/);
        assert.notEqual(framed.background, 'none');
        assert.ok(framed.left >= -.5 && framed.right >= -.5 && Math.abs(framed.left - framed.right) < 1, JSON.stringify(framed));
        assert.ok(framed.heightGap < 1, JSON.stringify(framed));
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close(); }
    });

    for (const art of [
      { code: 'FC2-PPV-3312576', cx: .519, width: 1548, height: 948, layout: undefined },
      { code: 'FC2-PPV-3312576', cx: .22, width: 1548, height: 948, layout: undefined },
      { code: 'SPONE-001', cx: .555, width: 2184, height: 1638, layout: 'still' },
    ]) {
    it(`${viewport.name}: ${art.code} 缺集归为一张卡，横图人脸 ${art.cx} 尽量居中`, async () => {
      const opened = await openItemPage(browser, '/', viewport, {
        ready: '[data-media-card]', settings: { javLayout: 'big', homeLayout: 'big', javImage: 'cover' },
      });
      try {
        const { code, cx } = art;
        const ids = [8001, 8008, 8010, 8021];
        const base = await opened.page.evaluate(async (id) =>
          (await fetch(`/api/item?id=${id}`)).json(), ITEM.plain);
        const items = ids.map((id) => ({ ...base, id, code, is_jav: true, tags: [],
          name: `${code}-${id - 8000}.mp4`, has_cover: true,
          cover_frame: { cx, cy: .294, layout: art.layout }, poster_box: null,
          part_group: { key: code, title: code, count: ids.length, seed_id: ids[0],
            item_ids: ids, total_duration: 9000, total_size: 9000000000 },
        }));
        await opened.page.route('**/api/items?*', (route) => route.fulfill({ json: {
          items, total: items.length, has_more: false,
        } }));
        await opened.page.route('**/cover?*', (route) => route.fulfill({
          contentType: 'image/svg+xml', body: `<svg xmlns="http://www.w3.org/2000/svg" width="${art.width}" height="${art.height}"><rect width="${art.width}" height="${art.height}" fill="#ddd"/></svg>`,
        }));
        await opened.page.reload({ waitUntil: 'load' });
        const covers = opened.page.locator('[data-media-card] img.cover');
        await covers.first().waitFor();
        await opened.page.waitForFunction((width) =>
          document.querySelector<HTMLImageElement>('[data-media-card] img.cover')?.naturalWidth === width, art.width);
        await settle(opened.page);
        assert.equal(await covers.count(), 1);
        const frame = await covers.first().evaluate((img: HTMLImageElement, faceCenter: number) => {
          const style = getComputedStyle(img);
          const rect = img.getBoundingClientRect();
          const scale = Math.max(rect.width / img.naturalWidth, rect.height / img.naturalHeight);
          const x = parseFloat(style.objectPosition) / 100;
          const sourceFaceX = faceCenter * img.naturalWidth * scale;
          const croppedWidth = img.naturalWidth * scale - rect.width;
          const faceX = sourceFaceX - croppedWidth * x;
          // 无空白的可达范围为 [sourceFaceX - croppedWidth, sourceFaceX]。
          const closestCenter = Math.min(sourceFaceX, Math.max(sourceFaceX - croppedWidth, rect.width / 2));
          return { kind: img.dataset.frame, faceX, closestCenter, croppedWidth, x };
        }, cx);
        assert.equal(frame.kind, 'still');
        assert.ok(frame.croppedWidth > 0, JSON.stringify(frame));
        assert.ok(frame.x >= 0 && frame.x <= 1, JSON.stringify(frame));
        assert.ok(Math.abs(frame.faceX - frame.closestCenter) < 5, JSON.stringify(frame));
        if (art.layout === 'still') {
          for (const shape of ['front', 'whole']) {
            const fill = await covers.first().evaluate((img: HTMLImageElement, mode) => {
              img.classList.toggle('front', mode === 'front');
              img.classList.toggle('whole', mode === 'whole');
              const pic = img.closest<HTMLElement>('[data-media-pic],.pic')!;
              pic.style.setProperty('--card-ratio', mode === 'whole' ? '16/9' : '.75');
              img.dispatchEvent(new Event('load'));
              const box = pic.getBoundingClientRect();
              const image = img.getBoundingClientRect();
              return { fit: getComputedStyle(img).objectFit, blur: pic.style.getPropertyValue('--cover-blur'),
                widthGap: Math.abs(box.width - image.width), heightGap: Math.abs(box.height - image.height) };
            }, shape);
            assert.equal(fill.fit, shape === 'front' ? 'cover' : 'contain', JSON.stringify(fill));
            assert.equal(fill.blur, '', JSON.stringify(fill));
            assert.ok(fill.widthGap < 1 && fill.heightGap < 1, JSON.stringify(fill));
          }
        }
        const bounds = await layout(opened.page);
        assert.ok(bounds.scrollWidth <= bounds.viewportWidth + 1, JSON.stringify(bounds));
        assert.deepEqual(opened.problems, []);
      } finally { await opened.close(); }
    });
    }
  }
});
