/* 图片回退链：模板只声明数据，行为归 wireImageFallbacks() 的全站委托监听。
   Application 与 React 页面引用同一模块，候选依次尝试，全部不可用时按 data-drop 处理。

   - `data-drop`：必填，也是这套机制的开关。没有它的 `<img>` 一概不管——页面上
     另有一批靠 CSS 或父节点兜底的图（厂牌 `.mk`），把它们删掉反而是错的。
     取值 `self` 移除 `<img>` 自己；`closest:<选择器>` 移除最近的祖先容器；
     `initial` 换成首字母垫底。
   - `data-fallbacks`：可选，`|` 分隔的候选 src，按序换完才收场。裸 `|` 不是
     合法 URL 字符，所以这个分隔符不会把地址切坏。
   - `data-drop-style`：可选开关，换下一环之前先撤掉内联 style。人脸取景是按
     当前这一张图算出来的，换成另一张照片脸不在同一个位置。
   - `data-initial` / `data-drop-class`：`data-drop="initial"` 用。

   兜底链的最后一环必须真的把 `<img>` 拿掉：留着一个取不到图的 `<img>`，
   `:has(img)` 照样匹配，首字母垫底永远回不来，浏览器还会把 alt 文本画出来。
   `onerror=null` 只是不再重试，不等于这一环走完了。 */
import { esc } from '@peach/legacy/core';

const FALLBACK_SEPARATOR = '|';
const CLOSEST_PREFIX = 'closest:';

/* 回退链解析。字符串进、数组出，不碰 DOM，可以单独测。 */
export function parseFallbacks(value: unknown): string[] {
  return String(value ?? '')
    .split(FALLBACK_SEPARATOR)
    .map(part => part.trim())
    .filter(Boolean);
}

/* 模板里那串 `data-*`。空候选直接丢掉，省得每个调用点自己写一遍三元。 */
export function imageFallbackAttrs({
  drop = 'self', fallbacks = [], initial = '', dropClass = '', dropStyle = false,
}: {
  drop?: string; fallbacks?: string | readonly string[]; initial?: string; dropClass?: string; dropStyle?: boolean;
} = {}): string {
  const chain = (Array.isArray(fallbacks) ? fallbacks : [fallbacks]).filter(Boolean);
  return [
    `data-drop="${esc(drop)}"`,
    chain.length ? `data-fallbacks="${esc(chain.join(FALLBACK_SEPARATOR))}"` : '',
    initial ? `data-initial="${esc(initial)}"` : '',
    dropClass ? `data-drop-class="${esc(dropClass)}"` : '',
    dropStyle ? 'data-drop-style' : '',
  ].filter(Boolean).join(' ');
}

/* 一次失败推进一格：还有候选就换 src，没有就按 `data-drop` 收场。
   返回 `retry`／`drop`／`''`，最后那个是「这个元素没登记，不管」。 */
export function advanceImageFallback(image: HTMLImageElement | null | undefined): '' | 'retry' | 'drop' {
  if (!image || !image.dataset || !image.dataset.drop) return '';
  const chain = parseFallbacks(image.dataset.fallbacks);
  if (chain.length) {
    const [next = '', ...rest] = chain;
    if ('dropStyle' in image.dataset) image.removeAttribute('style');
    /* 脸框只描述第一环那张实体图。回落图是另一张照片，脸不在同一位置，尺寸也不是
       那个尺寸——留着它，下一次 load 就会拿上一张的脸去给这一张算放大倍数。
       这一条不受 `dropStyle` 开关管：脸框本来就只对第一环成立，没有「留着也对」
       的位置，而 style 那边有（`--face` 那类由容器给的取景要留）。 */
    delete image.dataset.facebox;
    if (rest.length) image.dataset.fallbacks = rest.join(FALLBACK_SEPARATOR);
    else delete image.dataset.fallbacks;
    if (next === image.dataset.fallbackNoteSrc && image.dataset.fallbackNote) {
      image.title = image.dataset.fallbackNote;
      image.setAttribute('aria-label', image.dataset.fallbackNote);
    }
    image.src = next;
    return 'retry';
  }
  const drop = image.dataset.drop;
  if (drop.startsWith(CLOSEST_PREFIX)) {
    (image.closest(drop.slice(CLOSEST_PREFIX.length)) || image).remove();
    return 'drop';
  }
  if (drop === 'initial') {
    const span = document.createElement('span');
    span.className = image.dataset.dropClass || '';
    span.textContent = image.dataset.initial || '';
    image.replaceWith(span);
    return 'drop';
  }
  image.remove();
  return 'drop';
}

/* 全站一条监听就够。`error` 不冒泡，但捕获阶段照样会经过祖先，所以挂在根上的
   捕获监听能接住任何后代图片，不必给每个 `<img>` 各挂一个——逐个绑的话，
   资料页外链的站点圆标每次重绘都要重新绑一轮。
   只认 `<img>`：同一个事件名也会从 `<video>`、`<source>`、`<script>` 上发出来。 */
const fallbackRoots = new WeakSet<EventTarget>();
export function wireImageFallbacks(root: EventTarget): void {
  if (fallbackRoots.has(root)) return;
  fallbackRoots.add(root);
  root.addEventListener('error', event => {
    if (event.target instanceof HTMLImageElement) advanceImageFallback(event.target);
  }, true);
}
