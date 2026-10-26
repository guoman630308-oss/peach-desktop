/* 卡片、头像与封面那几段 HTML：取哪张图、回落到哪张、取景数据贴在哪一环。 */
import { describe, expect, it } from 'vitest';

import {
  avatarInner, cardArtwork, cardIdentity, coverImage, coverUrl, detailPosterUrl, entityAvatar, entityFaceImg,
  faceBoxAttrs, faceOrigin, facePos, javArtwork, logoUrl, mixFace, mixLabel, performerLabel, queueAvatarHtml,
  queueThumbHtml,
} from '../../src/card-art/markup';
import { rememberRepresentatives, representativeOf } from '../../src/card-art/representatives';
import { advanceImageFallback } from '../../src/card-art/image-fallback';

const parse = (html: string): HTMLImageElement | null => {
  const box = document.createElement('div');
  box.innerHTML = html;
  return box.querySelector('img');
};

const FOCUS = { axis: 'y', pct: 12, box: { cx: 0.44, cy: 0.22, faceW: 67, imgW: 640, imgH: 960 } };

describe('西方官方封面', () => {
  it('无番号作品在卡片、队列和详情使用资产封面键', () => {
    const item = { id: 12, has_cover: true, cover_key: 'ASSET-ID-12', cover_version: 'abc' };
    const source = '/cover?code=ASSET-ID-12&thumb=1&v=abc';
    expect(parse(cardArtwork(item, 'big', false, 'cover').html)?.getAttribute('src')).toBe(source);
    expect(parse(queueThumbHtml(item, 'cover'))?.getAttribute('src')).toBe(source);
    expect(detailPosterUrl(item, 'cover')).toBe('/cover?code=ASSET-ID-12&v=abc');
  });
});

it('创作者名片官方头像使用本机代理和归档回退，实体图优先', () => {
  const ref = { id: 7, has_image: false, source_avatar_url: '/follow-avatar?service=fanbox&id=123',
    source_avatar_fallbacks: ['/follow-avatar?provider=kemono&ref=fanbox%2F123'] };
  const image = parse(avatarInner('创作者', ref, null, 'creator'))!;
  expect(image.getAttribute('src')).toBe(ref.source_avatar_url);
  expect(image.dataset.fallbacks).toBe(ref.source_avatar_fallbacks[0]);
  expect(image.dataset.drop).toBe('self');
  expect(parse(avatarInner('创作者', { ...ref, has_image: true }, null, 'creator'))?.getAttribute('src'))
    .toBe('/entity-image?kind=creator&id=7');
});

describe('entityFaceImg：实体图优先，取不到退代表作头像', () => {
  it('两样都取不到就一个 <img> 都不出', () => {
    expect(entityFaceImg({ id: 3, hasImage: false })).toBe('');
    expect(entityFaceImg({})).toBe('');
  });

  it('服务端没说有实体图就不拼 /entity-image，只出代表作头像', () => {
    const img = parse(entityFaceImg({ id: 3, hasImage: false, rep: 41 }))!;
    expect(img.getAttribute('src')).toBe('/avatar?id=41');
    expect(img.dataset.fallbacks).toBeUndefined();
    expect(img.dataset.drop).toBe('self');
  });

  it('实体图在前，代表作头像排进兜底链；最后一环把图摘掉', () => {
    const img = parse(entityFaceImg({ kind: 'creator', id: 3, hasImage: true, rep: 41 }))!;
    expect(img.getAttribute('src')).toBe('/entity-image?kind=creator&id=3');
    expect(img.dataset.fallbacks).toBe('/avatar?id=41');
    expect(img.dataset.drop).toBe('self');
  });

  it('一屏几十格的位置取派生件', () => {
    expect(parse(entityFaceImg({ id: 3, hasImage: true, thumb: true }))!.getAttribute('src'))
      .toBe('/entity-image?kind=performer&id=3&thumb=1');
  });

  it('换过图的实体地址带上版本，没给版本的地址不变', () => {
    const img = parse(entityFaceImg({ id: 3, hasImage: true, version: 'a b', rep: 41, thumb: true }))!;
    expect(img.getAttribute('src')).toBe('/entity-image?kind=performer&id=3&thumb=1&v=a%20b');
    // 代表作头像是另一张图，不跟实体图的版本走。
    expect(img.dataset.fallbacks).toBe('/avatar?id=41');
    expect(parse(entityFaceImg({ id: 3, hasImage: true, version: '' }))!.getAttribute('src'))
      .toBe('/entity-image?kind=performer&id=3');
    // 没有实体图时版本也不出现在任何一环。
    expect(parse(entityFaceImg({ id: 3, hasImage: false, version: '7', rep: 41 }))!.getAttribute('src'))
      .toBe('/avatar?id=41');
  });

  it('懒加载、异步解码；大位可以要求立刻取', () => {
    const lazy = parse(entityFaceImg({ id: 3, hasImage: true }))!;
    expect(lazy.getAttribute('loading')).toBe('lazy');
    expect(lazy.getAttribute('decoding')).toBe('async');
    expect(lazy.width).toBeGreaterThan(0);
    expect(lazy.height).toBeGreaterThan(0);
    expect(parse(entityFaceImg({ id: 3, hasImage: true, lazy: false }))!.hasAttribute('loading')).toBe(false);
  });

  it('厂牌走自己的标识，实体图和代表作排在它后面，都不取景', () => {
    const img = parse(entityFaceImg({ kind: 'studio', id: 3, hasImage: true, rep: 41, logo: 'S1 NO.1', logoVariant: 'icon', focus: FOCUS }))!;
    expect(img.getAttribute('src')).toBe('/logo?studio=S1%20NO.1&variant=icon');
    expect(img.dataset.fallbacks).toBe('/entity-image?kind=studio&id=3|/avatar?id=41');
    expect(img.hasAttribute('style')).toBe(false);
    expect(img.dataset.facebox).toBeUndefined();
    expect('dropStyle' in img.dataset).toBe(false);
  });

  it('换过的标识地址带上版本：厂牌引用上的 logo_version 一路拼进去', () => {
    expect(parse(entityFaceImg({ kind: 'studio', logo: 'S1', logoVersion: '18f3a' }))!.getAttribute('src'))
      .toBe('/logo?studio=S1&variant=logo&v=18f3a');
    expect(parse(avatarInner('S1', { logo_version: '18f3b' }, null, 'studio', null, 'S1'))!.getAttribute('src'))
      .toBe('/logo?studio=S1&variant=icon&v=18f3b');
    expect(logoUrl('S1', 'icon')).toBe('/logo?studio=S1&variant=icon');
  });

  it('不指定变体就是资料页大位要的字标', () => {
    expect(parse(entityFaceImg({ kind: 'studio', logo: 'S1' }))!.getAttribute('src')).toBe('/logo?studio=S1&variant=logo');
  });

  it('事务所没有标识文件，退到官网的站点圆标', () => {
    expect(parse(entityFaceImg({ kind: 'agency', mark: 9 }))!.getAttribute('src')).toBe('/link-mark?id=9');
  });

  it('只给 focus 也会挪：挪和放大一起贴在实体图那一环，回落时整组撤掉', () => {
    const img = parse(entityFaceImg({ id: 3, hasImage: true, rep: 41, focus: FOCUS }))!;
    expect(img.style.objectPosition).toBe('50% 12%');
    expect(img.dataset.facebox).toBe('0.44 0.22 67 640 960');
    expect('dropStyle' in img.dataset).toBe(true);
  });

  it('只有代表作头像时不贴取景：那是另一张照片', () => {
    const img = parse(entityFaceImg({ id: 3, hasImage: false, rep: 41, focus: FOCUS }))!;
    expect(img.hasAttribute('style')).toBe(false);
    expect(img.dataset.facebox).toBeUndefined();
  });

  it('调用方给的 style 优先于按 focus 算的那一份', () => {
    const img = parse(entityFaceImg({ id: 3, hasImage: true, focus: FOCUS, style: ' style="object-position:10% 20%"' }))!;
    expect(img.style.objectPosition).toBe('10% 20%');
  });
});

describe('avatarInner：首字母垫底，再叠真实图', () => {
  it('引用缺 has_image 按没图处理', () => {
    const html = avatarInner('三上悠亜', { id: 3 }, null);
    expect(html).toBe('<span class="ini">三</span>');
  });

  it('名字转义，空名字垫问号', () => {
    expect(avatarInner('<b>', null, null)).toBe('<span class="ini">&lt;</span>');
    expect(avatarInner('', null, null)).toBe('<span class="ini">?</span>');
  });

  it('取景不传就从引用上取；传 null 明确不取景', () => {
    const ref = { id: 3, has_image: true, avatar_focus: FOCUS };
    expect(parse(avatarInner('A', ref, null))!.dataset.facebox).toBe('0.44 0.22 67 640 960');
    const company = parse(avatarInner('A', ref, null, 'studio', null, '', 'icon', null))!;
    expect(company.dataset.facebox).toBeUndefined();
    expect(company.hasAttribute('style')).toBe(false);
  });

  it('kind 跟着身份走：创作者的图不写成 performer', () => {
    expect(parse(avatarInner('A', { id: 3, has_image: true }, null, 'creator'))!.getAttribute('src'))
      .toBe('/entity-image?kind=creator&id=3');
  });

  it('卖家引用自带代表作头像，实体图优先，未取得的代表作不出图', () => {
    const ref = { id: 80, rep: 18, has_avatar: true, avatar_stand_in: true };
    const avatar = parse(entityAvatar('卖家账号', ref, 'creator'))!;
    expect(avatar.getAttribute('src')).toBe('/avatar?id=18');
    expect(avatar.getAttribute('title')).toBe('代表作画面，非本人');
    const installed = parse(entityAvatar('卖家账号', { ...ref, has_image: true }, 'creator'))!;
    expect(installed.getAttribute('src')).toBe('/entity-image?kind=creator&id=80');
    expect(installed.hasAttribute('title')).toBe(false);
    expect(parse(entityAvatar('卖家账号', { ...ref, has_avatar: false }, 'creator'))).toBeNull();
  });

  it('引用上的 image_version 跟着拼进实体图地址', () => {
    expect(parse(avatarInner('A', { id: 3, has_image: true, image_version: '1727800000' }, null))!.getAttribute('src'))
      .toBe('/entity-image?kind=performer&id=3&v=1727800000');
  });

  it('代表作画面替账号占位时注明不是本人；装了实体图的不注', () => {
    const standIn = parse(avatarInner('A', { id: 3, avatar_stand_in: true }, 41, 'creator'))!;
    expect(standIn.getAttribute('src')).toBe('/avatar?id=41');
    expect(standIn.getAttribute('title')).toBe('代表作画面，非本人');
    expect(standIn.getAttribute('aria-label')).toBe('代表作画面，非本人');
    const installed = parse(avatarInner('A', { id: 3, has_image: true, avatar_stand_in: true }, 41, 'creator'))!;
    expect(installed.hasAttribute('title')).toBe(false);
    expect(parse(avatarInner('A', { id: 3 }, 41, 'creator'))!.hasAttribute('title')).toBe(false);
  });

  it('实体图失败退到代表作时说明非本人，真人代表作不加该说明', () => {
    const standIn = parse(avatarInner('A', { id: 3, has_image: true, avatar_stand_in: true }, 41, 'creator'))!;
    expect(standIn.hasAttribute('title')).toBe(false);
    expect(advanceImageFallback(standIn)).toBe('retry');
    expect(standIn.getAttribute('src')).toBe('/avatar?id=41');
    expect(standIn.title).toBe('代表作画面，非本人');
    expect(standIn.getAttribute('aria-label')).toBe('代表作画面，非本人');
    const person = parse(avatarInner('A', { id: 3, has_image: true }, 41, 'creator'))!;
    advanceImageFallback(person);
    expect(person.hasAttribute('title')).toBe(false);
    expect(person.hasAttribute('aria-label')).toBe(false);
  });
});

describe('人脸取景的换算', () => {
  it('按检出的轴挪；没检出返回空串维持几何居中', () => {
    expect(faceOrigin({ axis: 'x', pct: 30 })).toBe('30% 50%');
    expect(faceOrigin({ axis: 'y', pct: 0 })).toBe('50% 0%');
    expect(faceOrigin(null)).toBe('');
    expect(facePos({ axis: 'x', pct: 30 })).toBe(' style="object-position:30% 50%"');
    expect(facePos(undefined)).toBe('');
  });

  it('脸框五个数挤一个属性，非数字进不了属性', () => {
    expect(faceBoxAttrs(FOCUS)).toBe(' data-facebox="0.44 0.22 67 640 960"');
    expect(faceBoxAttrs({ box: { cx: '"x', cy: 0, faceW: 1, imgW: 1, imgH: 1 } })).toBe(' data-facebox="NaN 0 1 1 1"');
    expect(faceBoxAttrs({ axis: 'x', pct: 3 })).toBe('');
  });
});

describe('coverImage：官方封面', () => {
  it('VR 画面标记来自标题或取景记录，手工框保持其取景', () => {
    expect(parse(coverImage({ code: 'MDVR-276', title: '【VR】作品' }, 'big'))!.dataset.coverLayout).toBe('still');
    expect(parse(coverImage({ code: 'MDVR-276', cover_frame: { layout: 'still' } }, 'big'))!.dataset.coverLayout).toBe('still');
    const manual = parse(coverImage({ code: 'MDVR-276', title: '【VR】作品',
      poster_box: { x0: 0, y0: 0, x1: 300, y1: 600, px: [800, 600], method: 'manual' } }, 'big'))!;
    expect(manual.dataset.coverLayout).toBeUndefined();
    expect(manual.dataset.posterbox).toBeDefined();
    expect(parse(coverImage({ code: 'IENFE-5301' }, 'big'))!.dataset.coverLayout).toBe('still');
    expect(parse(coverImage({ code: 'IENFE-5301', cover_frame: { cy: .8 } }, 'big'))!.dataset.cy).toBe('0.8');
  });
  it('来源封面携带作品身份和明确的 Blu-ray 版次', () => {
    const img = parse(coverImage({ code: 'MIDA-122', title: '作品（ブルーレイディスク）' }, 'big'))!;
    expect(img.dataset.coverCode).toBe('MIDA-122');
    expect(img.dataset.wideSleeve).toBe('1');
    expect(parse(coverImage({ code: 'MIDA-159', title: '作品' }, 'big'))!.dataset.wideSleeve).toBeUndefined();
  });
  it('人脸纵向夹在 5%–60%，正封框六个数按顺序送到元素上', () => {
    const img = parse(coverImage({
      code: 'SSIS-001', cover_frame: { cx: 0.81, cy: 0.9 },
      poster_box: { x0: 421, y0: 0, x1: 800, y1: 538, px: [800, 538] },
    }, 'big'))!;
    expect(img.getAttribute('src')).toBe('/cover?code=SSIS-001&thumb=1');
    expect(img.className).toBe('poster cover front');
    expect(img.dataset.cx).toBe('0.81');
    expect(img.dataset.cy).toBe('0.6');
    expect(img.dataset.posterbox).toBe('421 800 538 0 800 538');
    expect(img.getAttribute('loading')).toBe('lazy');
    expect(img.width).toBeGreaterThan(0);
    expect(img.height).toBeGreaterThan(0);
    expect(img.dataset.drop).toBe('self');
  });

  it('补过高清、重探过的封面地址带上版本，派生档摘掉 thumb 后版本还在', () => {
    expect(parse(coverImage({ code: 'SSIS-001', cover_version: '18f3a' }, 'big'))!.getAttribute('src'))
      .toBe('/cover?code=SSIS-001&thumb=1&v=18f3a');
    expect(coverUrl({ code: 'SSIS-001', cover_version: '18f3a' })).toBe('/cover?code=SSIS-001&v=18f3a');
    expect(detailPosterUrl({ ...JAV, cover_version: '18f3a' }, 'cover')).toBe('/cover?code=SSIS-001&v=18f3a');
    expect(parse(javArtwork({ ...JAV, cover_version: '18f3a' }, 'big', false, 'thumbnail').html)!.dataset.javCover)
      .toBe('/cover?code=SSIS-001&thumb=1&v=18f3a');
  });

  it('小图看整张；没检出人脸就不带锚点', () => {
    const img = parse(coverImage({ code: 'A B' }, 'small', true))!;
    expect(img.className).toBe('poster cover whole');
    expect(img.getAttribute('src')).toBe('/cover?code=A%20B&thumb=1');
    expect(img.getAttribute('loading')).toBe('eager');
    expect(img.dataset.cx).toBeUndefined();
    expect(img.dataset.cy).toBe(undefined);
  });
});

const JAV = {
  id: 5, code: 'SSIS-001', is_jav: true, has_cover: true, has_thumb: true,
  cover_frame: { cx: 0.7, cy: 0.3 }, poster_box: { x0: 421, y0: 0, x1: 800, y1: 538, px: [800, 538] },
};

describe('javArtwork：番号作品两种来源都挂在元素上', () => {
  it('偏好封套：官方封面加换图属性', () => {
    const art = javArtwork(JAV, 'big', false, 'cover');
    expect(art.kind).toBe('cover');
    const img = parse(art.html)!;
    expect(img.dataset.javImage).toBe('5');
    expect(img.dataset.javCover).toBe('/cover?code=SSIS-001&thumb=1');
    expect(img.dataset.javThumb).toBe('/poster?id=5&c=4');
    expect(img.dataset.javImageLayout).toBe('big');
    expect(img.classList.contains('cover')).toBe(true);
  });

  it('偏好预览图：取景数据照样跟着元素走，切回封面时找得到框', () => {
    const art = javArtwork(JAV, 'small', true, 'thumbnail');
    expect(art.kind).toBe('thumb');
    const img = parse(art.html)!;
    expect(img.getAttribute('src')).toBe('/poster?id=5&c=4');
    expect(img.className).toBe('poster');
    expect(img.dataset.cx).toBe('0.7');
    expect(img.dataset.posterbox).toBe('421 800 538 0 800 538');
    expect(img.getAttribute('loading')).toBe('eager');
  });

  it('两种都没有就是空的', () => {
    expect(javArtwork({ ...JAV, has_cover: false, has_thumb: false }, 'big', false, 'cover')).toEqual({ kind: '', html: '' });
  });
});

describe('cardArtwork 与队列小图', () => {
  it('关注来源用来源自己的缩略图，地址转义、不带来源页', () => {
    const img = parse(cardArtwork({ id: 1, follow_thumb_url: 'https://x/a?b=1&c="2"' }, 'big', false, 'cover').html)!;
    expect(img.getAttribute('src')).toBe('https://x/a?b=1&c="2"');
    expect(img.getAttribute('referrerpolicy')).toBe('no-referrer');
  });

  it('其余取本地预览格；没有就是空的', () => {
    const img = parse(cardArtwork({ id: 1, has_local_poster: true }, 'big', false, 'cover').html)!;
    expect(img.getAttribute('src')).toBe('/poster?id=1&c=4');
    expect(img.getAttribute('loading')).toBe('lazy');
    expect(img.width).toBeGreaterThan(0);
    expect(img.height).toBeGreaterThan(0);
    expect(cardArtwork({ id: 1 }, 'big', false, 'cover').kind).toBe('');
  });

  it('Mix 叠卡最上面那张立刻取，其余懒加载', () => {
    expect(parse(mixFace({ id: 1, has_thumb: true }, 'big', true, 'cover').html)!.getAttribute('loading')).toBe('eager');
    expect(parse(mixFace({ id: 1, has_thumb: true }, 'big', false, 'cover').html)!.getAttribute('loading')).toBe('lazy');
    expect(mixFace({ id: 1 }, 'big', true, 'cover').kind).toBe('');
  });

  it('队列一行没有图时写「无预览」占住那一格；番号作品按小图取', () => {
    expect(queueThumbHtml({ id: 1 }, 'cover')).toBe('<span class="nopic">无预览</span>');
    expect(parse(queueThumbHtml(JAV, 'cover'))!.className).toBe('poster cover whole');
  });
});

describe('detailPosterUrl：详情开场的海报位', () => {
  it('其它媒体退到本地预览格，没有就空串', () => {
    expect(detailPosterUrl({ id: 2, has_thumb: true }, 'cover')).toBe('/poster?id=2&c=4');
    expect(detailPosterUrl({ id: 2 }, 'cover')).toBe('');
  });

  it('番号作品跟随「JAV 默认封面」：封套取原件，预览图取预览格', () => {
    expect(detailPosterUrl(JAV, 'cover')).toBe('/cover?code=SSIS-001');
    expect(detailPosterUrl(JAV, 'thumbnail')).toBe('/poster?id=5&c=4');
  });
});

describe('卡片署名的身份推导', () => {
  it('番号作品的规范女优压过旧投影里的 creator 文本', () => {
    const identity = cardIdentity({ is_jav: true, creator: 'Yua Mikami', performers: ['三上悠亜'] });
    expect(identity).toMatchObject({ kind: 'performer', name: '三上悠亜', coStarred: false });
  });

  it('非番号作品先认创作者；共演不成立', () => {
    expect(cardIdentity({ creator: 'cc', performers: ['a', 'b'] })).toMatchObject({ kind: 'creator', name: 'cc', coStarred: false });
  });

  it('没有署名人是「未归属」', () => {
    expect(cardIdentity({})).toMatchObject({ kind: '', name: '未归属' });
  });

  it('共演只写第一位再给总人数', () => {
    expect(cardIdentity({ performers: ['a', 'b'], performer_total: 7 })).toMatchObject({ coStarred: true, total: 7 });
    expect(cardIdentity({ performers: ['a', 'b'] }).total).toBe(2);
  });

  it('队列行的头像不可点：整行就是按钮，里面只出 <span>；共演最多叠五张', () => {
    const single = queueAvatarHtml({ performers: ['a'], performer_entities: [{ id: 3, has_image: true }] });
    expect(single).toBe(`<span class="mav">${avatarInner('a', { id: 3, has_image: true }, undefined)}</span>`);
    const stack = document.createElement('div');
    stack.innerHTML = queueAvatarHtml({ performers: ['a', 'b', 'c', 'd', 'e', 'f'] });
    expect(stack.querySelectorAll('.mavstack>span.mav')).toHaveLength(5);
    expect(stack.querySelector('button')).toBeNull();
  });

  it('创作者那一格取 creator_entity 的图', () => {
    const html = queueAvatarHtml({ creator: 'cc', creator_entity: { id: 8, has_image: true } });
    expect(parse(html)!.getAttribute('src')).toBe('/entity-image?kind=creator&id=8');
  });

  it('详情队列从 entity_refs 取得共演头像、版本和取景', () => {
    const host = document.createElement('div');
    host.innerHTML = queueAvatarHtml({ is_jav: true, performers: ['JULIA', '共演者'], entity_refs: {
      performer: [{ id: 7846, has_image: true, image_version: 'portrait-v2', avatar_focus: { axis: 'y', pct: 15 } },
        { id: 9, has_image: false }],
    } });
    expect(host.querySelectorAll('.mav')).toHaveLength(2);
    expect(host.querySelector('img')?.getAttribute('src')).toBe('/entity-image?kind=performer&id=7846&v=portrait-v2');
    expect(host.querySelector('img')?.style.objectPosition).toBe('50% 15%');
    expect(host.querySelectorAll('.mav')[1]?.querySelector('img')).toBeNull();
  });

  it('详情队列的创作者头像使用 creator 实体引用', () => {
    const html = queueAvatarHtml({ creator: '创作者', entity_refs: { creator: [{ id: 8, has_image: true }] } });
    expect(parse(html)!.getAttribute('src')).toBe('/entity-image?kind=creator&id=8');
  });
});

describe('称谓与 Mix 署名', () => {
  it('「女优」只用于番号发行物', () => {
    expect(performerLabel({ is_jav: true })).toBe('女优');
    expect(performerLabel({ is_jav: false })).toBe('艺人');
    expect(performerLabel(null)).toBe('艺人');
  });

  it('番号作品先认女优，其余先认创作者，再依次退到厂牌、番号、首个标签', () => {
    const label = (tag: string) => `#${tag}`;
    expect(mixLabel({ is_jav: true, creator: 'x', performers: ['p'] }, label)).toBe('p');
    expect(mixLabel({ creator: 'x', performers: ['p'] }, label)).toBe('x');
    expect(mixLabel({ studio: 's', code: 'c' }, label)).toBe('s');
    expect(mixLabel({ code: 'c' }, label)).toBe('c');
    expect(mixLabel({ tags: ['t'] }, label)).toBe('#t');
    expect(mixLabel({}, () => '')).toBe('为你推荐');
  });
});

describe('代表作表', () => {
  it('只收真能取到头像的代表作', () => {
    rememberRepresentatives([{ k: 'has', rep: 11, has_avatar: true }, { k: 'none', rep: 12, has_avatar: false }, { k: 'zero', rep: 0, has_avatar: true }]);
    expect(representativeOf('has')).toBe(11);
    expect(representativeOf('none')).toBeUndefined();
    expect(representativeOf('zero')).toBeUndefined();
    expect(representativeOf('constructor')).toBeUndefined();
  });

  it('卡片署名有身份时才退代表作；没有署名人按女优那一档出首字母', () => {
    rememberRepresentatives([{ k: 'rep', rep: 21, has_avatar: true }]);
    expect(parse(entityAvatar('rep', null, 'performer'))!.getAttribute('src')).toBe('/avatar?id=21');
    expect(entityAvatar('rep', null, '')).toBe('<span class="ini">r</span>');
  });
});
