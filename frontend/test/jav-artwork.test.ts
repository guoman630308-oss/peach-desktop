import { describe, expect, it } from 'vitest';
import { COVER_FRONT_RATIO } from '../src/appearance/layout';
import { javImageKind, normalizeJavImage, normalizeJavPreferences, panelFrame, relayoutJavImages, syncJavImages } from '../src/jav-artwork';

describe('JAV 默认封面', () => {
  const jav = { is_jav: true, code: 'TEST-001', has_cover: true, has_thumb: true };
  it('默认官方封面，两档都按作品身份选择图片', () => {
    expect(normalizeJavImage(undefined)).toBe('cover');
    expect(normalizeJavImage('invalid')).toBe('cover');
    expect(javImageKind(jav, 'cover')).toBe('cover');
    expect(javImageKind(jav, 'thumbnail')).toBe('thumbnail');
    expect(javImageKind({ ...jav, is_jav: false }, 'cover')).toBe('thumbnail');
  });
  it('缺官方封面或预览图时使用可用图片，两者缺失时留空', () => {
    expect(javImageKind({ ...jav, has_cover: false }, 'cover')).toBe('thumbnail');
    expect(javImageKind({ ...jav, code: '' }, 'cover')).toBe('thumbnail');
    expect(javImageKind({ ...jav, has_thumb: false }, 'thumbnail')).toBe('cover');
    expect(javImageKind({ ...jav, has_cover: false, has_thumb: false }, 'cover')).toBe('');
  });
  it('原地更新多个表面的图片并可切回，保留播放器和节点', () => {
    const root = document.createElement('div');
    root.innerHTML = '<video></video>' + ['big', 'small'].map(layout =>
      `<img data-jav-image="1" data-jav-cover="/cover?code=TEST-001" data-jav-thumb="/poster?id=1&c=4" data-jav-image-layout="${layout}" class="poster" src="/poster?id=1&c=4">`).join('');
    const images = [...root.querySelectorAll('img')];
    const video = root.querySelector('video');
    syncJavImages(root, 'cover');
    expect(images.every(img => img.classList.contains('cover'))).toBe(true);
    expect(images[0]?.classList.contains('front')).toBe(true);
    expect(images[1]?.classList.contains('whole')).toBe(true);
    syncJavImages(root, 'thumbnail');
    expect(images.every(img => img.getAttribute('src') === '/poster?id=1&c=4' && !img.classList.contains('cover'))).toBe(true);
    expect(root.querySelector('video')).toBe(video);
    expect([...root.querySelectorAll('img')]).toEqual(images);
  });
  it('换图时撤掉上一张算出的取景，模糊背景跟着一起撤', () => {
    // 取景是按上一张图的折痕和尺寸算的，留着会把正封摆在一块错位的区域上。
    // 模糊背景挂在卡片上，`removeAttribute('style')` 够不着它，必须单独清。
    const root = document.createElement('div');
    root.innerHTML = '<div class="pic" style="--cover-blur:url(&quot;/cover?code=TEST-001&quot;)">'
      + '<img data-jav-image="1" data-jav-cover="/cover?code=TEST-001" data-jav-thumb="/poster?id=1&c=4"'
      + ' data-jav-image-layout="big" class="poster cover front panel"'
      + ' style="--panel-left:-101.3%;--panel-clip:52.63%" src="/cover?code=TEST-001"></div>';
    const pic = root.querySelector('.pic') as HTMLElement;
    const img = root.querySelector('img') as HTMLImageElement;
    syncJavImages(root, 'thumbnail');
    expect(img.classList.contains('panel')).toBe(false);
    expect(img.getAttribute('style')).toBeNull();
    expect(pic.style.getPropertyValue('--cover-blur')).toBe('');
  });
  it('换版式在同一张图上换取景类名，来源不动，只交回已加载完、要立刻重算的那几张', () => {
    const root = document.createElement('div');
    root.innerHTML = '<div data-media-pic="" style="--cover-blur:url(&quot;/cover?code=TEST-001&quot;)">'
      + '<img data-jav-image="1" data-jav-image-layout="big" class="poster cover front panel"'
      + ' style="--panel-left:-101.3%" src="/cover?code=TEST-001"></div>'
      + '<img data-jav-image="2" data-jav-image-layout="big" class="poster cover front" src="/cover?code=TEST-002">'
      + '<img data-jav-image="3" data-jav-image-layout="big" class="poster" src="/poster?id=3&c=4">';
    const [loaded, pending, thumb] = [...root.querySelectorAll('img')] as [HTMLImageElement, HTMLImageElement, HTMLImageElement];
    Object.defineProperty(loaded, 'naturalWidth', { value: 800 });
    Object.defineProperty(loaded, 'complete', { value: true });
    Object.defineProperty(pending, 'complete', { value: false });
    expect(relayoutJavImages(root, 'small')).toEqual([loaded]);
    expect([loaded.className, pending.className, thumb.className])
      .toEqual(['poster cover whole', 'poster cover whole', 'poster']);
    expect(loaded.getAttribute('style')).toBeNull();
    expect((root.firstElementChild as HTMLElement).style.getPropertyValue('--cover-blur')).toBe('');
    expect([loaded, pending, thumb].map(img => img.dataset.javImageLayout)).toEqual(['small', 'small', 'small']);
    expect(loaded.getAttribute('src')).toBe('/cover?code=TEST-001');
    // 同一版式再来一次什么都不做：卡片每次重画都会问一遍。
    expect(relayoutJavImages(root, 'small')).toEqual([]);
  });
  it.each(['big', 'small'])('%s 保留两种封面来源，保存后恢复同一组合', javLayout => {
    for (const javImage of ['cover', 'thumbnail']) {
      const settings = normalizeJavPreferences({ javLayout, javImage });
      expect(settings).toEqual({ javLayout, javImage });
      expect(normalizeJavPreferences(JSON.parse(JSON.stringify(settings)))).toEqual(settings);
      expect(javImageKind(jav, settings.javImage)).toBe(javImage);
    }
  });
  it('沿用已存版式与预览图选择，未知值使用默认值', () => {
    expect(normalizeJavPreferences({ javLayout: 'preview', javImage: 'cover' })).toEqual({ javLayout: 'small', javImage: 'thumbnail' });
    expect(normalizeJavPreferences({ javLayout: 'sleeve' })).toEqual({ javLayout: 'small', javImage: 'cover' });
    expect(normalizeJavPreferences({ javLayout: 'cover' })).toEqual({ javLayout: 'big', javImage: 'cover' });
    expect(normalizeJavPreferences({ javLayout: 'invalid', javImage: 'invalid' })).toEqual({ javLayout: 'big', javImage: 'cover' });
  });
});

/* 大图版式的容器比例，取的就是版式那一层（`src/appearance/layout.ts`）真会用的那个值；
   下面算式里写的 0.75 就是它。 */
const BIG_LAYOUT_RATIO = COVER_FRONT_RATIO;

/** 算出来的那两档框永远满高贴右缘，纵向两个轴因此恒为 0 与 100。 */
const fullHeight = (left: number) => ({ top: 0, right: 0, bottom: 0, left });

describe('正封取景框换算成卡片里的位置', () => {
  it('正封装得下就居中，两侧留白相等', () => {
    // 1600×1000 的封套，折痕右侧那块正封从 887 开始。图片按卡片高度铺满后宽是
    // 卡片的 1.6/0.75≈2.1333 倍，正封占其中 (1−0.554375)×2.1333≈0.9507 倍卡片宽，
    // 装得下：左右各留 (1−0.9507)/2≈0.0247，图片左缘因此推到 −1.158。
    const fold = { x0: 887, y0: 0, x1: 1600, y1: 1000, method: 'fold', px: [1600, 1000] };
    expect(panelFrame(fold, BIG_LAYOUT_RATIO))
      .toEqual({ clip: fullHeight(55.44), left: -115.8, top: 0, height: 100 });
    // 容器换一个比例，图片的渲染宽度跟着变：同一个框算出的位置必须跟着动。
    expect(panelFrame(fold, 0.5))
      .toEqual({ clip: fullHeight(55.44), left: -220, top: 0, height: 100 });
  });

  it('右半居中的框走同一套算术，方法只是它的来路', () => {
    // 800×538 是本机封套最常见的尺寸；没找到折痕时正面从右半 421 开始，框宽 379。
    const half = { x0: 421, y0: 0, x1: 800, y1: 538, method: 'ratio', px: [800, 538] };
    expect(panelFrame(half, BIG_LAYOUT_RATIO))
      .toEqual({ clip: fullHeight(52.63), left: -101.3, top: 0, height: 100 });
  });

  it('正封比卡片宽时贴右缘，从左边切', () => {
    // 800×500 的正封宽高比 0.800，是本机最宽的一档，比 0.75 的容器还宽 6.7%。
    // 居中会同时切掉两边，而标题、女优名和角标都压在右侧，只能让右缘对齐。
    expect(panelFrame({ x0: 400, px: [800, 500] }, BIG_LAYOUT_RATIO))
      .toEqual({ clip: fullHeight(50), left: -113.33, top: 0, height: 100 });
  });

  it('人手工框的四条边都作数，横着切一刀就要放大再上移', () => {
    // 800×538 里框出 (200,50)–(600,450)：框比容器宽，贴右缘从左边切；框高只有源图的
    // 400/538，图片要放到卡片高的 134.5% 才能让框铺满，多出来的顶上那截靠 top 拉上去。
    const manual = { x0: 200, y0: 50, x1: 600, y1: 450, method: 'manual', px: [800, 538] };
    expect(panelFrame(manual, BIG_LAYOUT_RATIO)).toEqual({
      clip: { top: 9.29, right: 25, bottom: 16.36, left: 25 },
      left: -100, top: -12.5, height: 134.5,
    });
  });

  it('没有框时返回 null，调用方退回贴右缘的取景', () => {
    // 本机 1014 张封面里 331 张判定为不裁，接口发的就是 null，这条路不能算出数来。
    expect(panelFrame(null, BIG_LAYOUT_RATIO)).toBeNull();
    expect(panelFrame(undefined, BIG_LAYOUT_RATIO)).toBeNull();
    expect(panelFrame({ x0: 421 }, BIG_LAYOUT_RATIO)).toBeNull();
    expect(panelFrame({ x0: Number.NaN, px: [800, 538] }, BIG_LAYOUT_RATIO)).toBeNull();
    expect(panelFrame({ x0: 421, px: [0, 0] }, BIG_LAYOUT_RATIO)).toBeNull();
    expect(panelFrame({ x0: 421, px: [800, 538] }, 0)).toBeNull();
  });

  it('折痕夹回图片之内，切掉整幅的框没有正封可摆', () => {
    // 坏数据按整幅可见处理，比拿负宽度算下去安全。
    expect(panelFrame({ x0: -40, px: [1600, 1000] }, BIG_LAYOUT_RATIO))
      .toEqual({ clip: fullHeight(0), left: -113.33, top: 0, height: 100 });
    // 折痕落在图片右缘上：折痕右边一个像素都不剩，这时没有正封可摆。
    expect(panelFrame({ x0: 1600, px: [1600, 1000] }, BIG_LAYOUT_RATIO)).toBeNull();
  });
});
