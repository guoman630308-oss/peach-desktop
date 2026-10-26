/* 视口外的卡片跳过渲染：封面和元信息区交给浏览器按视口取舍，卡片盒照常排版。
 *
 * 这几条守的是跳过渲染的副作用：屏外卡确实被跳过、没渲染过的卡按估计高度排进来之后
 * 整页高度和卡片位置不跳、Ctrl+F 仍找得到屏外卡里的字。演示库只有十来条，一屏就放完，
 * 这里把目录和女优资料页的作品响应放大成一长列，编号改成互不相同，封面请求再改写回原来那条。 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';

import type { Browser, Page } from 'playwright-core';

import { launch, layout, settle, visit, VIEWPORTS, type Visit } from './harness.ts';

const DESKTOP = VIEWPORTS.find((viewport) => !viewport.mobile)!;
const CLONES = 150;
const CLONE_BASE = 900_000;
const PROBE_TAG = '屏外探针';

/** 卡片里各块的选择器。作品卡归目录网格，关注卡归关注页，钩子都是
 * `data-media-*`；关注卡的封面格多包一层 `[data-follow-visual]`，头像和标签是 `<span>`。 */
interface CardShape {
  card: string; pic: string; meta: string; avatar: string; text: string; tags: string;
  avatarButton: string; tagButton: string;
}
const MEDIA: CardShape = {
  card: '[data-media-card]', pic: '[data-media-pic]', meta: '[data-media-meta]', avatar: '[data-media-avatar]',
  text: '[data-media-text]', tags: '[data-media-tags]',
  avatarButton: '[data-media-meta]>button[data-media-avatar]', tagButton: '[data-media-tags]>button[data-media-tag]',
};
const FOLLOW = MEDIA;
const CATALOG_CARDS = '#grid [data-media-grid]>[data-media-card]';
const ENTITY_CARDS = '#index [data-entity-grid] [data-media-grid]>[data-media-card]';
const FOLLOW_CARDS = '[data-follow-list]>[data-follow-item]';

interface CatalogPayload {
  total: number;
  items: Array<Record<string, unknown>>;
}

/** `/api/items` 的首页换成 `CLONES` 条：每三条有一条带标签，最后一条带一枚独有的标签给查找用。
 * 带标签的那几条同时归给一位女优：演示库的作品都未归属，头像是不可聚焦的 `<span>`，归属之后
 * 才是按钮，焦点环那条用例要聚焦它。
 * 克隆源是去掉 `performer` 筛选之后的目录首页：演示库里没有人物实体，资料页按名字筛出来是空的。 */
async function serveLongItems(page: Page): Promise<void> {
  const original = new Map<number, number>();
  let baseline: CatalogPayload | undefined;
  await page.route(/\/api\/items\?/, async (route) => {
    const url = new URL(route.request().url());
    /* 首页那条竖屏带是第一页落地之后另取、再插进网格中间的附加内容，何时到只看请求快慢。它落在
       首次测量之后，整页就凭空多出一条带的高度，量的却不是估计误差。这里给它空列表，带子不插。 */
    if (url.searchParams.has('orient')) {
      await route.fulfill({ json: { items: [], total: 0, has_more: false } });
      return;
    }
    if (url.searchParams.get('offset') !== '0') {
      await route.continue();
      return;
    }
    url.searchParams.delete('performer');
    baseline ??= await (await route.fetch({ url: url.toString() })).json() as CatalogPayload;
    const source = baseline.items.filter((item) => !item.part_group && !item.edition_group);
    const items = Array.from({ length: CLONES }, (_, index) => {
      const item = structuredClone(source[index % source.length]);
      original.set(CLONE_BASE + index, Number(item.id));
      item.id = CLONE_BASE + index;
      item.tags = index === CLONES - 1 ? [PROBE_TAG] : index % 3 === 0 ? ['演示'] : [];
      if (index % 3 === 0) {
        Object.assign(item, { creator: '', performers: ['演示演员'], performer_total: 1,
          performer_entities: [{ id: 90_100, name: '演示演员', has_image: false }] });
      }
      return item;
    });
    await route.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ ...baseline, total: CLONES, has_more: false, items }) });
  });
  await page.route((url) => original.has(Number(url.searchParams.get('id'))), async (route) => {
    const url = new URL(route.request().url());
    url.searchParams.set('id', String(original.get(Number(url.searchParams.get('id')))));
    await route.continue({ url: url.toString() });
  });
}

async function openLongCatalog(browser: Browser, density = 'big'): Promise<Visit> {
  const opened = await visit(browser, '/', DESKTOP);
  const { page } = opened;
  await serveLongItems(page);
  await page.evaluate((value) => localStorage.setItem('density', value), density);
  await page.reload({ waitUntil: 'load' });
  await page.waitForFunction(([selector, count]) => document.querySelectorAll(selector).length >= count,
    [CATALOG_CARDS, CLONES] as const, { timeout: 15_000 });
  await settle(page);
  return opened;
}

/** 女优资料页的作品区放大成 `CLONES` 部：资料由这里给，字段照 `/api/entity` 的形状写。 */
async function openLongEntity(browser: Browser): Promise<Visit> {
  const name = '七沢みあ';
  const opened = await visit(browser, '/', DESKTOP);
  const { page } = opened;
  await page.route(/\/api\/entity\?/, (route) => route.fulfill({ json: {
    id: 90_001, kind: 'performer', canonical_name: name, aliases: [], display_aliases: [],
    user_aliases: [], asset_count: CLONES, tags: [], related_performers: [], links: [],
    metadata: {}, has_image: false, has_avatar: false, avatar_focus: null,
    representative_asset_id: null, entry_links: [], feed: { following: false },
  } }));
  await serveLongItems(page);
  await page.goto(new URL(`/performers/${encodeURIComponent(name)}`, page.url()).href, { waitUntil: 'load' });
  await page.waitForFunction(([selector, count]) => document.querySelectorAll(selector).length >= count,
    [ENTITY_CARDS, CLONES] as const, { timeout: 15_000 });
  await settle(page);
  return opened;
}

/** 关注页换成 `CLONES` 条合成的更新：演示库没有关注来源。字段照 `/api/follow` 的形状，
 * 不带缩略图，不发外站请求；每三条有一条带标签，屏外卡因此有高矮两种元信息区。 */
async function openLongFollow(browser: Browser): Promise<Visit> {
  const opened = await visit(browser, '/', DESKTOP);
  const { page } = opened;
  const source = { id: 1, provider: 'rule34xxx', provider_label: 'Rule34.xxx', ref: 'demo', label: '演示来源',
    author_name: '演示作者', author_key: 'name:演示作者', official_avatar_url: null, avatar_url: null,
    url: 'https://example.invalid/', semantics: 'work', enabled: true, entity_id: null, entity_name: null,
    can_backfill: false, backfill_page: 0, created_at: '2026-09-01T00:00:00Z', last_checked_at: null,
    last_status: 'ok', last_error: null, history_exhausted: true };
  const groups = Array.from({ length: CLONES }, (_, index) => ({
    release_key: `demo-${index}`, variants: [], duplicates: [], providers: ['rule34xxx'], has_wip: false,
    is_release: false, newest_at: '2026-09-01T00:00:00Z',
    primary: {
      id: CLONE_BASE + index, provider: 'rule34xxx', provider_label: 'Rule34.xxx', resource_provider: 'rule34xxx',
      source_id: 1, source_label: '演示来源', external_id: String(index), title: `演示更新 ${index}`,
      author: '演示作者', summary: null, url: `https://example.invalid/${index}`, thumb_url: null,
      published_at: '2026-09-01T00:00:00Z', published_precision: 'exact', version: null, duration: 60,
      variant_kind: 'main', variant_label: null, status: 'new', asset_id: null, media_needs_credential: false,
      media_error: null, has_media: false, media_kind: 'video', width: 1280, height: 720, media_type: null,
      playable: false, media_items: [], hidden_media: [], resource_urls: [],
      tags: index % 3 === 0 ? ['演示'] : [], detail_tags: [], tag_types: {},
    },
  }));
  const body = { ok: true, sources: [source], author_aliases: [], alias_suggestions: [], suggestions: [], groups,
    counts: { new: CLONES, seen: 0, saved: 0, ignored: 0 }, sort: 'new', dir: 'desc', seed: 1,
    facets: { authors: [], providers: [], tags: [], works: [] }, offset: 0, limit: CLONES, has_more: false,
    providers: ['rule34xxx'] };
  await page.route(/\/api\/follow(\?|$)/, (route) => route.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify(body) }));
  await page.goto(new URL('/follow', page.url()).toString(), { waitUntil: 'load' });
  await page.waitForFunction(([selector, count]) => document.querySelectorAll(selector).length >= count,
    [FOLLOW_CARDS, CLONES] as const, { timeout: 15_000 });
  await settle(page);
  return opened;
}

/** 一屏一屏滚到底再滚回顶，每步等两帧，让每张卡都至少渲染过一次。 */
function roundTrip(page: Page): Promise<void> {
  return page.evaluate(async () => {
    const frame = () => new Promise(requestAnimationFrame);
    for (const direction of [1, -1]) {
      for (let step = 0; step < 500; step++) {
        const y = scrollY;
        scrollBy(0, direction * innerHeight * .9);
        await frame();
        await frame();
        if (scrollY === y) break;
      }
    }
  });
}

describe('视口外的卡片', () => {
  let browser: Browser;

  before(async () => {
    browser = await launch();
  });

  after(async () => {
    await browser.close();
  });

  it('手机关注卡片按文字和标签定高，空标签区不占行', { timeout: 60_000 }, async () => {
    const opened = await openLongFollow(browser);
    try {
      await opened.page.setViewportSize({ width: 390, height: 844 });
      for (const density of ['roomy', 'dense']) {
        await opened.page.evaluate(value => { document.body.dataset.density = value }, density);
        const cards = await opened.page.locator(FOLLOW_CARDS).evaluateAll(nodes => nodes.slice(0, 3).map(node => {
          const meta = node.querySelector('[data-media-meta]')!;
          const children = [...meta.children].filter(child => getComputedStyle(child).display !== 'none')
            .map(child => child.getBoundingClientRect());
          const content = Math.max(...children.map(child => child.bottom)) - Math.min(...children.map(child => child.top));
          return { height: meta.getBoundingClientRect().height, content };
        }));
        for (const card of cards) assert.ok(card.height <= card.content + 17, JSON.stringify({ density, ...card }));
      }
      assert.deepEqual((await layout(opened.page)).offenders, []);
    } finally { await opened.close() }
  });

  for (const [where, open, selector] of [
    ['馆藏', openLongCatalog, CATALOG_CARDS],
    ['女优资料页', openLongEntity, ENTITY_CARDS],
  ] as const) {
    it(`${where}：跳过封面与元信息区的渲染，Ctrl+F 仍找得到屏外卡里的字`, { timeout: 60_000 }, async () => {
      const opened = await open(browser);
      try {
        const state = await opened.page.evaluate(([tag, cardSelector, shape]) => {
          const last = [...document.querySelectorAll<HTMLElement>(cardSelector)].at(-1)!;
          const parts = [last.querySelector(shape.pic)!, last.querySelector(shape.meta)!];
          // 跳过的是元素的内容，元素自己仍在排版里：查它的第一个子元素。
          const skipped = parts.map((part) => !part.firstElementChild!.checkVisibility({ contentVisibilityAuto: true }));
          const box = last.getBoundingClientRect();
          /* 侧栏的标签列表里也有这枚标签，排在文档前面；往下接着找，直到落进那张卡。 */
          const find = (window as unknown as { find(text: string): boolean }).find.bind(window);
          let hits = 0;
          while (hits < 5 && find(tag)) {
            hits++;
            const node = getSelection()?.anchorNode;
            if (node && last.contains(node)) break;
          }
          const hit = getSelection()?.anchorNode;
          return {
            values: parts.map((part) => getComputedStyle(part).contentVisibility),
            skipped, below: box.top - innerHeight, height: box.height,
            hits, inLast: !!hit && last.contains(hit),
          };
        }, [PROBE_TAG, selector, MEDIA] as const);
        assert.deepEqual(state.values, ['auto', 'auto'], '屏外卡的封面和元信息区没有交给浏览器按视口取舍');
        assert.ok(state.below > 1000, `最后一张卡离视口只有 ${state.below}px，放大的列表不够长`);
        assert.deepEqual(state.skipped, [true, true], '屏外卡的封面或元信息区仍在渲染');
        assert.ok(state.height > 0, '屏外卡的 getBoundingClientRect 量出了零高');
        assert.ok(state.inLast, `页内查找没有落到屏外那张卡的标签上（命中 ${state.hits} 次）`);
        assert.deepEqual(opened.problems, []);
      } finally {
        await opened.close();
      }
    });
  }

  for (const [where, open, selector, shape] of [
    ['馆藏', openLongCatalog, CATALOG_CARDS, MEDIA],
    ['馆藏密集一档', (b: Browser) => openLongCatalog(b, 'dense'), CATALOG_CARDS, MEDIA],
    ['女优资料页', openLongEntity, ENTITY_CARDS, MEDIA],
    ['关注', openLongFollow, FOLLOW_CARDS, FOLLOW],
  ] as const) {
    it(`${where}：长距离往返之后整页高度和深处那张卡的位置不跳，屏外量到的几何与渲染后一致`, { timeout: 90_000 }, async () => {
      const opened = await open(browser);
      try {
        const { page } = opened;
        const measure = () => page.evaluate(([cardSelector, metaSelector]) => {
          const cards = [...document.querySelectorAll(cardSelector)];
          const deep = cards[Math.floor(cards.length * .8)];
          return { height: document.documentElement.scrollHeight,
            deep: deep.getBoundingClientRect().top + scrollY, y: scrollY,
            view: deep.getBoundingClientRect().top,
            skipping: getComputedStyle(deep.querySelector(metaSelector)!).contentVisibility,
            meta: deep.querySelector(metaSelector)!.getBoundingClientRect().height };
        }, [selector, shape.meta] as const);
        /* 首屏之外的卡此刻都没渲染过，高度全按估计值；往返一趟之后每张都记住了自己的实际
           尺寸。两次量到的差就是估计值的误差，一行差一点，累积到深处就是滚动条在跳。 */
        const fresh = await measure();
        assert.equal(fresh.skipping, 'auto', `${where}的屏外卡不跳过渲染，量不到估计值的误差`);
        await roundTrip(page);
        const rendered = await measure();
        assert.ok(Math.abs(rendered.height - fresh.height) <= 1,
          `整页高度从 ${fresh.height} 变成 ${rendered.height}：元信息区的估计高度与实际不符`);
        assert.ok(Math.abs(rendered.deep - fresh.deep) <= 1,
          `深处那张卡从 ${fresh.deep} 挪到 ${rendered.deep}`);

        /* 停在深处，滚到底再回来：同一个 scrollY 下那张卡应该还在原处。 */
        await page.evaluate((y) => scrollTo(0, y), rendered.deep - 300);
        await page.waitForTimeout(300);
        const parked = await measure();
        /* 跳过渲染时量到的几何要和渲染后一致：卡里有别的子元素抢空间时，内容不参与最小尺寸的
           元信息区会被压扁，整张卡的高度不变，从外面看不出来，只有量它自己才露馅。 */
        assert.ok(Math.abs(fresh.meta - parked.meta) <= 1,
          `屏外卡的元信息区量出 ${fresh.meta}px，渲染后是 ${parked.meta}px`);
        await roundTrip(page);
        await page.evaluate((y) => scrollTo(0, y), parked.y);
        await page.waitForTimeout(300);
        const back = await measure();
        assert.equal(back.y, parked.y, '回不到原来的滚动位置');
        assert.ok(Math.abs(back.view - parked.view) <= 1,
          `回到同一滚动位置后那张卡在视口里从 ${parked.view} 挪到 ${back.view}`);
        assert.deepEqual(opened.problems, []);
      } finally {
        await opened.close();
      }
    });
  }

  /* 关注卡的头像和标签是 `<span>`，不可聚焦，焦点环那一半只在作品卡上查。 */
  for (const [where, open, selector, shape, focusable] of [
    ['馆藏', openLongCatalog, CATALOG_CARDS, MEDIA, true],
    ['馆藏密集一档', (b: Browser) => openLongCatalog(b, 'dense'), CATALOG_CARDS, MEDIA, true],
    ['女优资料页', openLongEntity, ENTITY_CARDS, MEDIA, true],
    ['关注', openLongFollow, FOLLOW_CARDS, FOLLOW, false],
  ] as const) {
    it(`${where}：元信息区垫出的裁切余量不改排版、不接指针，头像和标签的焦点环整圈可见`, { timeout: 60_000 }, async () => {
      const opened = await open(browser);
      try {
        const { page } = opened;
        await page.locator(selector).first().evaluate(async (card) => {
          card.scrollIntoView({ block: 'center' });
          await new Promise(requestAnimationFrame);
          await new Promise(requestAnimationFrame);
        });
        /* 元信息区四周垫内边距再用负外边距抵消，裁切边（padding box）往外扩，内容盒原地不动。
           内容盒要贴着卡片左右缘、封面下方隔一道卡片行距，外边距盒的下沿就是内容盒下沿；
           同一行里最高的那张卡，内容盒下面到卡片下缘（关注卡是到 `[data-follow-state]`）不留空，卡高
           于是仍是封面、行距与内容三项之和。头像和文字列贴着内容盒。垫出去的那一圈伸出
           卡片盒，指针落在那里不能算进这张卡。 */
        const frames = await page.evaluate(([cardSelector, parts]) => {
          const inFlow = (element: Element | null, step: 'previousElementSibling' | 'nextElementSibling') => {
            let node = element?.[step] ?? null;
            while (node && getComputedStyle(node).position === 'absolute') node = node[step];
            return node;
          };
          return [...document.querySelectorAll(cardSelector)].filter((card) => {
            const box = card.getBoundingClientRect();
            return card.querySelector(`:scope>${parts.meta}`) && box.top >= 0 && box.bottom <= innerHeight;
          }).map((card) => {
            const meta = card.querySelector(`:scope>${parts.meta}`)!;
            const style = getComputedStyle(meta);
            const border = meta.getBoundingClientRect();
            const content = {
              left: border.left + meta.clientLeft + parseFloat(style.paddingLeft),
              top: border.top + meta.clientTop + parseFloat(style.paddingTop),
              right: border.left + meta.clientLeft + meta.clientWidth - parseFloat(style.paddingRight),
              bottom: border.top + meta.clientTop + meta.clientHeight - parseFloat(style.paddingBottom),
            };
            const cardBox = card.getBoundingClientRect();
            const cardStyle = getComputedStyle(card);
            const gap = parseFloat(cardStyle.rowGap);
            const above = inFlow(meta, 'previousElementSibling')!.getBoundingClientRect();
            const below = inFlow(meta, 'nextElementSibling');
            const floor = below ? below.getBoundingClientRect().top - gap
              : cardBox.bottom - parseFloat(cardStyle.paddingBottom);
            const mav = meta.querySelector(`:scope>${parts.avatar}`)?.getBoundingClientRect();
            const mtext = meta.querySelector(`:scope>${parts.text}`)!;
            const textStyle = getComputedStyle(mtext);
            const textBox = mtext.getBoundingClientRect();
            /* 探的是垫出来的那几圈（元信息区 8px、标签行与密集一档的文字列 4px）里面的点。 */
            const tags = meta.querySelector(parts.tags)?.getBoundingClientRect();
            const probes = [...[content.top + 10, (content.top + content.bottom) / 2, content.bottom - 4]
              .flatMap((y) => [[cardBox.left - 2, y], [cardBox.right + 2, y]]),
            [(content.left + content.right) / 2, cardBox.bottom + 2],
            ...(tags ? [[tags.left + 10, cardBox.bottom + 2], [cardBox.right + 2, (tags.top + tags.bottom) / 2]] : [])];
            const outside = probes.map(([x, y]) => document.elementFromPoint(x, y));
            return {
              row: Math.round(cardBox.top), slack: floor - content.bottom,
              padded: [parseFloat(style.paddingLeft), parseFloat(style.paddingTop)],
              offsets: [content.left - cardBox.left, cardBox.right - content.right,
                content.top - (above.bottom + gap), border.bottom + parseFloat(style.marginBottom) - content.bottom,
                mav ? mav.left - content.left : 0,
                textBox.top - parseFloat(textStyle.marginTop) - content.top,
                content.right - (textBox.right + parseFloat(textStyle.marginRight))].map((n) => Math.round(n * 100) / 100),
              background: style.backgroundColor,
              claimed: outside.filter((hit) => hit && card.contains(hit)).length,
            };
          });
        }, [selector, shape] as const);
        assert.ok(frames.length > 0, `${where}没有整张落在视口里的卡`);
        for (const frame of frames) {
          assert.ok(frame.padded.every((value) => value > 0), `${where}的元信息区没有垫裁切余量`);
          assert.ok(frame.offsets.every((value) => Math.abs(value) <= .5),
            `${where}的元信息区内容盒偏离了卡片与封面：${frame.offsets.join(', ')}`);
          assert.equal(frame.background, 'rgba(0, 0, 0, 0)', `${where}的元信息区有背景，垫出来的一圈会画在卡片外面`);
          assert.equal(frame.claimed, 0, `${where}卡片盒外 2px 处的指针落进了这张卡`);
          assert.ok(frame.slack >= -.5, `${where}的元信息区内容盒越过了卡片下缘 ${-frame.slack}px`);
        }
        for (const row of new Set(frames.map((frame) => frame.row))) {
          const least = Math.min(...frames.filter((frame) => frame.row === row).map((frame) => frame.slack));
          assert.ok(least <= .5, `${where}同一行最高的卡在元信息区下面还空着 ${least}px，卡片被撑高了`);
        }

        /* 焦点环画在元素外面，逐层往上找会裁切的祖先（`overflow` 不是 visible，或跳过渲染带来的
           paint containment），环的外沿必须落在每一层的 padding box 以内。 */
        const room = (target: string) => page.locator(`${selector} ${target}`).first().evaluate((element, cardSelector) => {
          const style = getComputedStyle(element);
          const ring = style.outlineStyle === 'none' ? 0 : parseFloat(style.outlineWidth) + parseFloat(style.outlineOffset);
          const box = element.getBoundingClientRect();
          let least = Infinity, clips = 0;
          for (let clip = element.parentElement; clip && !clip.matches(cardSelector); clip = clip.parentElement) {
            const clipStyle = getComputedStyle(clip);
            if (clipStyle.overflow === 'visible' && clipStyle.contentVisibility !== 'auto') continue;
            clips++;
            const edge = clip.getBoundingClientRect();
            const left = edge.left + clip.clientLeft, top = edge.top + clip.clientTop;
            least = Math.min(least, box.left - ring - left, box.top - ring - top,
              left + clip.clientWidth - (box.right + ring), top + clip.clientHeight - (box.bottom + ring));
          }
          return { focused: element.matches(':focus-visible'), ring, least, clips };
        }, shape.card);
        for (const target of focusable ? [shape.avatarButton, shape.tagButton] : []) {
          await page.keyboard.press('Tab');
          await page.locator(`${selector} ${target}`).first().focus();
          const edge = await room(target);
          assert.ok(edge.focused && edge.ring > 0, `${where}键盘聚焦的 ${target} 没有焦点环`);
          assert.ok(edge.clips > 0, `${where}的 ${target} 上面没有会裁切的祖先：这条用例守的前提变了，改用例`);
          assert.ok(edge.least >= -.5, `${where}的 ${target} 焦点环越过裁切边 ${-edge.least}px，那一截会被裁掉`);
        }
        assert.deepEqual(opened.problems, []);
      } finally {
        await opened.close();
      }
    });
  }
});
