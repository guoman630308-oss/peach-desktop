/* 关注卡片与详情共用的署名、头像、来源图标与几处字样（`follow-marks.ts`）。
 *
 * 拼出来的是 HTML 串，这里按串比：卡片和详情直接把它摆进去，类名与 `data-drop` 一族属性就是
 * 遗留样式与图片回落接得上的那个契约。 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { FollowContext, FollowGroup, FollowItem, FollowSource } from '../../src/react/follow-feed/follow-feed';
import {
  FOLLOW_UNTITLED, authorAvatarHtml, followAuthorEntity, followAuthorName, followBadges, followCompactWhen, followIdentity, followMediaIssue,
  followTitle, followTitleMarks, followWhen, learnFollowDims, sourceIcon, sourceMark,
} from '../../src/react/follow-feed/follow-marks';

const item = (id: number, extra: Partial<FollowItem> = {}): FollowItem =>
  ({ id, title: `更新 ${id}`, status: 'new', provider: 'kemono', provider_label: 'Kemono', ...extra });
const group = (primary: FollowItem, extra: Partial<FollowGroup> = {}): FollowGroup =>
  ({ primary, variants: [], duplicates: [], ...extra });
const source = (id: number, extra: Partial<FollowSource> = {}): FollowSource =>
  ({ id, provider: 'kemono', provider_label: 'Kemono', ...extra });
const context = (extra: Partial<FollowContext> = {}): FollowContext =>
  ({ sources: [], aliases: [], credentials: new Set(), ...extra });

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() });

describe('来源图标', () => {
  it('登记过的站出本机那枚图，取不到就撤掉；没登记的站是空串', () => {
    expect(sourceIcon('kemono')).toBe(
      '<img data-follow-site-icon="" src="/source-icon?provider=kemono" alt="" loading="lazy" data-drop="self">');
    expect(sourceIcon('f95zone', 'F95 <zone>')).toBe('<img data-follow-site-icon="" src="/source-icon?provider=f95zone" '
      + 'alt="F95 &lt;zone&gt;" title="F95 &lt;zone&gt;" loading="lazy" data-drop="self">');
    expect(sourceIcon('nowhere', '某站')).toBe('');
  });

  it('只有图标一格的站标：图取不到时由回落脚本换成站名首字，没登记的站直接写首字', () => {
    expect(sourceMark('gofile', 'Gofile')).toBe('<img data-follow-site-icon="" src="/source-icon?provider=gofile" alt="" '
      + 'loading="lazy" data-drop="initial" data-initial="G" data-drop-class="follow-site-initial">');
    expect(sourceMark('itchio', 'itch.io')).toBe('<span class="follow-site-initial" aria-hidden="true">I</span>');
    expect(sourceMark('itchio')).toBe('<span class="follow-site-initial" aria-hidden="true">I</span>');
  });
});

describe('创作者', () => {
  it('作者导航只认唯一实体，并跟随实际发布者', () => {
    const bound = source(1, { entity_id: 90, entity_name: 'Kou', author_key: 'entity:90', author_name: 'Kou' });
    expect(followAuthorEntity([bound, source(2)])).toBe('Kou');
    expect(followAuthorEntity([source(2)])).toBe('');
    expect(followAuthorEntity([bound, source(3, { entity_id: 91, entity_name: 'Mira' })])).toBe('');
    expect(followIdentity(item(1), [bound], context()).entityName).toBe('Kou');
    const poster = source(4, { entity_id: 92, entity_name: 'Poster', ref: 'poster', author_key: 'entity:92' });
    expect(followIdentity(item(2, { credit: { poster: 'poster' } }), [bound],
      context({ sources: [bound, poster] })).entityName).toBe('Poster');
    expect(followIdentity(item(3, { credit: { poster: 'stranger' } }), [bound],
      context({ sources: [bound] })).entityName).toBe('');
  });
  it('名字按别名合并后的统称、官方来源的写法依次取', () => {
    const sources = [source(1, { author_key: 'name:lazy', label: 'lazyprocrastinator · fanbox' }),
      source(2, { author_key: 'name:lazy', label: 'LazyProcrastinator Collection', official_avatar_url: '/a.png' })];
    expect(followAuthorName(sources, context())).toBe('LazyProcrastinator');
    expect(followAuthorName(sources, context({
      aliases: [{ canonical_key: 'lazy', canonical_name: 'Lazy P', aliases: [] }] }))).toBe('Lazy P');
    /* F95 的标签是整个线程标题：哪段是人名由服务端判（`author_name`），页面不自己解析。 */
    expect(followAuthorName([source(3, { label: 'Strauzek Collection [2026-09-04] [Mr_Strauz]',
      author_name: 'Mr_Strauz' })], context())).toBe('Mr_Strauz');
  });

  it('头像官方优先、归档站回退，回落链与首字母写在属性上；都没有就是首字母那一格', () => {
    const both = [source(1, { avatar_url: '/mirror.png' }), source(2, { official_avatar_url: '/official.png' })];
    expect(authorAvatarHtml(both, 'kou')).toBe('<img class="favatar" src="/official.png" alt="" loading="lazy" '
      + 'referrerpolicy="no-referrer" data-drop="initial" data-fallbacks="/mirror.png" data-initial="K" '
      + 'data-drop-class="favatar none">');
    expect(authorAvatarHtml([source(1, { avatar_url: '/mirror.png' })], '初音')).not.toContain('data-fallbacks');
    expect(authorAvatarHtml([], '初音')).toBe('<span class="favatar none" title="没有可用头像">初</span>');
  });

  it('booru 帖子认出发布者时署发布者，被关注者退成「署名含」；发布者没关注过就只出首字母', () => {
    const followed = [source(1, { author_key: 'name:kou', author_name: 'kou', avatar_url: '/kou.png' })];
    const poster = source(2, { ref: 'Poster_One', author_key: 'name:poster', author_name: 'Poster One' });
    const credited = item(3, { credit: { poster: 'poster-one', credited: 'kou' } });
    const known = followIdentity(credited, followed, context({ sources: [...followed, poster] }));
    expect(known).toEqual({ author: 'Poster One', avatar: '<span class="favatar none" title="没有可用头像">P</span>',
        credited: 'kou', entityName: '' });
    const stranger = followIdentity(item(4, { credit: { poster: 'someone' } }), followed, context({ sources: followed }));
    expect(stranger.author).toBe('someone');
    expect(stranger.avatar).toBe('<span class="favatar none" title="没有可用头像">S</span>');
    const plain = followIdentity(item(5), followed, context());
    expect(plain.author).toBe('kou');
    expect(plain.avatar).toContain('src="/kou.png"');
    expect(followIdentity(item(6, { author: '帖子作者' }), [], context()).author).toBe('帖子作者');
    expect(followIdentity(item(7), [], context()).author).toBe('创作者未取得');
  });
});

describe('标题前后的字样', () => {
  it('WIP 说这一条，同组有 WIP 说「含」；版本与声音版本各一枚', () => {
    expect(followTitleMarks(group(item(1, { variant_kind: 'wip' })))).toBe(
      '<small class="javedition" data-follow-edition="wip">WIP</small>');
    const shown = item(2, { audio: 'voiced' });
    expect(followTitleMarks(group(item(1, { version: 'v<2>' }), { has_wip: true }), shown)).toBe(
      '<small class="javedition" data-follow-edition="has-wip">含 WIP</small>'
      + '<small class="javedition" data-follow-edition="version">v&lt;2&gt;</small>'
      + '<small class="javedition" data-follow-edition="voiced">配音版</small>');
    expect(followTitleMarks(group(item(1)), item(3, { audio: 'silent' })))
      .toContain('data-follow-edition="silent">无声版');
    expect(followTitleMarks(group(item(1)))).toBe('');
  });

  it('另见相对卡面这一条：列别的站，没登记图标的写站名', () => {
    const primary = item(1, { provider: 'patreon', provider_label: 'Patreon' });
    const mirror = item(2);
    const forum = item(3, { provider: 'somewhere', provider_label: '某论坛' });
    const badges = followBadges(group(primary, { duplicates: [mirror, forum] }), primary);
    expect(badges).toBe('<span data-follow-seealso="" title="另见 Kemono、某论坛"><span data-follow-seealso-label="">另见</span> '
      + '<img data-follow-site-icon="" src="/source-icon?provider=kemono" alt="Kemono" title="Kemono" loading="lazy" data-drop="self">'
      + '<span>某论坛</span></span>');
    expect(followBadges(group(primary, { duplicates: [mirror] }), mirror)).toContain('另见 Patreon');
    expect(followBadges(group(primary))).toBe('');
  });

  it('媒体未取得说清原因；缺 F95 会话时分「部分」与「全部」', () => {
    expect(followMediaIssue(item(1, { media_error: '404' }), context())).toBe('媒体未取得：404');
    const needs = item(2, { provider: 'f95zone', media_needs_credential: true });
    expect(followMediaIssue(needs, context())).toBe('媒体未取得：需要 F95 登录会话解析');
    expect(followMediaIssue({ ...needs, playable: true }, context())).toBe('部分媒体未取得：需要 F95 登录会话解析');
    expect(followMediaIssue(needs, context({ credentials: new Set(['f95zone']) }))).toBe('');
  });

  it('时间按本机时区写，没给就明说，不加「约」', () => {
    expect(followWhen(item(1))).toBe('时间未取得');
    const when = followWhen(item(2, { published_at: '2026-09-04T00:00:00Z' }));
    expect(when).toMatch(/^\d{4}-\d\d-\d\d \d\d:\d\d$/);
    expect(when).not.toContain('约');
    /* 数据库存 UTC：没带时区标记的串也按 UTC 读，不按本机时区读，否则 UTC+8 的人每个时间都早 8 小时。 */
    expect(followWhen(item(3, { published_at: '2026-09-04T00:00:00' }))).toBe(when);
  });

  it('来源只给了相对时间（approximate）时只写到日并加「约」，不写换算出来的时分', () => {
    const now = Date.parse('2026-10-08T12:00:00Z');
    const old = item(1, { published_at: '2009-03-01T12:00:00Z', published_precision: 'approximate' });
    expect(followWhen(old, now)).toBe('约 2009-03-01');
    expect(followCompactWhen(old, now)).toBe('约 2009-03-01');
    expect(followWhen({ ...old, published_precision: 'exact' }, now)).toMatch(/^2009-03-01 \d\d:\d\d$/);
  });

  it('晚于此刻一天以上的时间照写，后面标「晚于现在」；一天以内的时钟误差不标', () => {
    const now = Date.parse('2026-10-08T12:00:00Z');
    const future = item(1, { published_at: '2031-01-01T12:00:00Z' });
    expect(followWhen(future, now)).toMatch(/^2031-01-01 \d\d:\d\d · 晚于现在$/);
    expect(followCompactWhen(future, now)).toBe('2031-01-01 · 晚于现在');
    expect(followWhen(item(2, { published_at: '2026-10-08T20:00:00Z' }), now)).not.toContain('晚于现在');
  });

  it('列表里的短写法：今年的去掉年份，往年的留着', () => {
    const now = Date.parse('2026-10-08T12:00:00Z');
    expect(followCompactWhen(item(1, { published_at: '2026-09-04T12:00:00Z' }), now)).toBe('09-04');
    expect(followCompactWhen(item(2, { published_at: '2024-09-04T12:00:00Z' }), now)).toBe('2024-09-04');
    expect(followCompactWhen(item(3), now)).toBe('时间未取得');
  });

  it('标题为空或只有空白时写同一句「未命名内容」', () => {
    expect(followTitle(item(1, { title: '' }))).toBe(FOLLOW_UNTITLED);
    expect(followTitle(item(2, { title: '   ' }))).toBe('未命名内容');
    expect(followTitle({})).toBe('未命名内容');
    expect(followTitle(item(3, { title: ' 正题 ' }))).toBe('正题');
  });
});

describe('回写图片尺寸', () => {
  it('同一张图一次会话只报一次，攒 800ms 一批发出去，失败不出声', async () => {
    vi.useFakeTimers();
    const fetch = vi.fn(async () => new Response('{}', { status: 409 }));
    vi.stubGlobal('fetch', fetch);
    learnFollowDims(9001, null, 300, 500);
    learnFollowDims(9001, null, 300, 500);
    learnFollowDims(9002, 1, 640, 480);
    expect(fetch).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(800);
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/follow/image-dims');
    expect(JSON.parse(String(init.body))).toEqual({ entries: [
      { item: 9001, width: 300, height: 500 }, { item: 9002, width: 640, height: 480, media: 1 }] });
    learnFollowDims(9001, null, 300, 500);
    await vi.advanceTimersByTimeAsync(800);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
