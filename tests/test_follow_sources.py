"""站点追更连接器的隔离测试。

夹具的字段名与 DOM 结构取自 2026-08-25 对真实站点的实测响应（kemono.cr
`fanbox/30917150`、rule34video.com `/models/lazyprocrastinator/`、f95zone
thread 50685），不是凭记忆构造的形状。测试本身不联网：transport 全部注入。
"""
import json
import os
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from peach import follow_sources
from peach.follow import FollowHistoryEnd, FollowSourceError, FollowSourceRateLimited
from peach.follow_secrets import Credential, CredentialError, CredentialStore
from peach.follow_sources import (
    USER_AGENT, F95ZoneConnector, FanboxConnector, KemonoConnector,
    PatreonConnector, Rule34PahealConnector, Rule34VideoConnector, Rule34XxxConnector,
    SimpCityConnector, SubscribeStarConnector, build_connector,
    _iso_from_relative, origin_group_key, parse_source_url,
)
from peach.http import HttpResponse
from support.mp4 import minimal_mp4


def _transport(status=200, headers=None, body=b"", record=None):
    def call(request, timeout, max_bytes):
        if record is not None:
            record.append(request)
        return HttpResponse(status, headers or {}, body)
    return call


KEMONO_POSTS = json.dumps([
    {"id": "11406814", "user": "30917150", "service": "fanbox",
     "title": "Villainous Valentine's Day 7", "substring": "",
     "published": "2026-02-14T21:51:10", "edited": None,
     "file": {"name": "a.png", "path": "/1c/fa/1cfae7.png"},
     "attachments": [{"name": "a.zip", "path": "/1c/fa/1cfae7.zip"}]},
    # 第二条只有附件、没有正文文件，`media_url` 要能从附件里取到。连附件也没有的帖子
    # 会被判为「不是 release」丢掉，拿那种形状当固定件会让别的断言都少一条。
    {"id": "11400490", "user": "30917150", "service": "fanbox",
     "title": "Villainous Valentine's Day 6", "published": "2026-02-13T21:30:28",
     "file": {}, "attachments": [{"name": "b.png", "path": "/2f/46/2f468d.png"}]},
]).encode()

RULE34VIDEO_HTML = b"""<html><body>
<a class="th js-open-popup" href="https://rule34video.com/video/4542721/fiona-paizuri-nude/"
   title="Fiona - Paizuri (Nude)">
  <div class="img wrap_image" data-preview="https://rule34video.com/get_file/51/aa/4542721_preview.mp4/">
    <img class="thumb lazy-load" src="data:image/gif;base64,R0lGOD"
         data-original="https://rule34video.com/contents/videos_screenshots/4542000/4542721/320x180/3.jpg"/>
    <div class="time">0:20</div>
  </div>
  <div class="thumb_title">Fiona - Paizuri (Nude)</div>
  <div class="thumb_info"><div class="added" title="Submitted: 1 week ago">1 week ago</div></div>
</a>
<a class="th" href="https://rule34video.com/video/4542713/fiona-paizuri/" title="Fiona - Paizuri">
  <div class="img"><div class="time">7:13</div></div>
  <div class="added">4 weeks ago</div>
</a>
<a class="th" href="https://rule34video.com/video/4542713/fiona-paizuri/" title="dup ignored"></a>
</body></html>"""

RULE34VIDEO_DETAIL_HTML = b"""<html><body>
<script type="application/ld+json">{
  "@context":"https://schema.org","@type":"VideoObject",
  "name":"Fiona - Paizuri (Nude)",
  "thumbnailUrl":"https://rule34video.com/contents/videos_screenshots/4542000/4542721/preview.jpg",
  "uploadDate":"2026-08-18","duration":"PT0H7M13S",
  "contentUrl":"https://rule34video.com/get_file/51/aa/4542721/4542721_360.mp4/"
}</script>
<a class="item btn_link video_meta_pill" href="https://rule34video.com/categories/3d/">3D</a>
<a class="item btn_link video_meta_pill" href="https://rule34video.com/categories/final-fantasy/">Final Fantasy</a>
<a class="item btn_link video_meta_pill" href="https://rule34video.com/models/lazyprocrastinator/">LazyProcrastinator</a>
<a class="tag_item" href="https://rule34video.com/tags/1/">deep throat</a>
<a class="tag_item" href="https://rule34video.com/tags/2/">breast squeeze</a>
<script>video_url: 'https://rule34video.com/get_file/51/aa/4542721.mp4/?v-acctoken=test'</script>
</body></html>"""

RULE34XXX_DETAIL_HTML = b"""<html><body><ul id="tag-sidebar">
<li class="tag-type-metadata tag"><a href="index.php?page=wiki&amp;s=list&amp;search=3d">?</a><a href="index.php?page=post&amp;s=list&amp;tags=3d">3d</a></li>
<li class="tag-type-metadata tag"><a href="index.php?page=wiki&amp;s=list&amp;search=blender">?</a><a href="index.php?page=post&amp;s=list&amp;tags=blender">blender</a></li>
<li class="tag-type-metadata tag"><a href="index.php?page=wiki&amp;s=list&amp;search=3d_model">?</a><a href="index.php?page=post&amp;s=list&amp;tags=3d_model">3d model</a></li>
<li class="tag-type-artist tag"><a href="index.php?page=post&amp;s=list&amp;tags=lazyprocrastinator">lazyprocrastinator</a></li>
<li class="tag-type-general tag"><a href="index.php?page=post&amp;s=list&amp;tags=reverse_cowgirl_position">reverse cowgirl position</a></li>
</ul></body></html>"""

# 搜索表单与结果页的结构取自 2026-09-01 对 f95zone.to 的实测：关键词 `Ria_neearts`
# 在 latest_data.php 的五个分类里全为空，站内搜索命中
# `/threads/ria-collection-2026-08-03-ria_neearts.146348/`。结果按帖子返回，
# 所以同一个线程会出现多条。
F95_SEARCH_FORM = b"""<html><body><form action="/search/search" method="post">
<input type="hidden" name="_xfToken" value="1756700000,abc" />
</form></body></html>"""

F95_SEARCH_RESULTS = b"""<html><body><div class="block"><div class="block-container">
<ol class="block-body">
<li class="block-row block-row--separated js-inlineModContainer" data-author="LordLegolas">
<div class="contentRow"><div class="contentRow-main">
<h3 class="contentRow-title"><a href="/threads/ria-collection-2026-08-03-ria_neearts.146348/"><span class="label label--gray" dir="auto">Collection</span><span class="label-append">&nbsp;</span><span class="label label--blue" dir="auto">Pinup</span><span class="label-append">&nbsp;</span>Ria Collection [2026-08-03] [<em class="textHighlight">Ria_neearts</em>]</a></h3>
<div class="contentRow-snippet">Overview: Ria_neearts is a 3D artist from Italy.</div>
</div></div></li>
<li class="block-row block-row--separated js-inlineModContainer" data-author="LordLegolas">
<div class="contentRow"><div class="contentRow-main">
<h3 class="contentRow-title"><a href="/threads/ria-collection-2026-08-03-ria_neearts.146348/post-11223344">Ria Collection [2026-08-03] [Ria_neearts]</a></h3>
</div></div></li>
</ol></div></div></body></html>"""

# 2026-09-12 实测 `63802` 的首楼：发帖人是搬运工，作者本人的地址写在正文的链接区
# 里，同一个人在 X 上两个账号并列。
F95_OPENING_POST = b"""<html><head><title>Strauzek | F95zone</title></head><body>
<h1 class="p-title-value"><span class="label">Collection</span><span class="label">Pinup</span>
Strauzek Collection [2026-09-04] [Mr_Strauz]</h1>
<article class="message" data-content="post-4085963" data-author="equalizzoR">
  <div class="message-userContent"><div class="bbWrapper">
    <a href="https://attachments.f95zone.to/2023/11/3109313_1.gif">banner</a>
    <a href="https://www.patreon.com/strauzek">Patreon</a>
    <a href="https://twitter.com/strauzek">Twitter</a>
    <a href="https://twitter.com/Mr_Strauz">Twitter</a>
    <a href="https://f95zone.to/members/strauzek.1881751/">F95</a>
    <a href="https://f95zone.to/threads/strauzek-models-collection.234481/">Models</a>
  </div></div>
</article></body></html>"""

F95_HTML = b"""<html><head><title>Collection - Video - Lazy | F95zone</title></head><body>
<h1 class="p-title-value"><span class="label">Collection</span><span class="label">Video</span>
Lazy Procrastinator Collection [2026-06-28] [LazyProcrastinator/LazyProcrast]</h1>
<article data-content="post-21383374" data-author="Jkhomie1198">
  <time datetime="2026-08-21T05:14:09+0100">Aug 21, 2026</time>
  <div class="bbWrapper">New batch up <a href="/goto/post?id=1">quoted</a>
  <a href="https://f95zone.to/masked/gofile.io/50685/abc">Gofile</a></div>
</article>
<article data-content="post-21394555" data-author="kim2311">
  <time datetime="2026-08-22T19:09:23+0100">Aug 22, 2026</time>
  <div class="bbWrapper"><blockquote class="bbCodeBlock">Jkhomie1198 said:
    <a href="https://f95zone.to/masked/gofile.io/50685/abc">Gofile</a>
    Click to expand...</blockquote>
  Genre spoilers first page have futa spoiler.</div>
</article>
<article data-content="post-21400001" data-author="LazyProcrastinator">
  <time datetime="2026-08-23T09:00:00+0100">Aug 23, 2026</time>
  <div class="bbWrapper">Preview attached
    <a href="https://pixeldrain.com/u/preview-pack">Download</a>
    <div data-lb-id="attachment6372325"
         data-src="https://attachments.f95zone.to/2026/08/6372325_preview.png">
      <img data-src="https://attachments.f95zone.to/2026/08/6372325_preview.png">
    </div>
    <a href="https://f95zone.to/attachments/6372325/">View attachment</a>
  </div>
</article>
</body></html>"""

FANBOX_JSON = json.dumps({"body": {"posts": [
    {"id": "12489354", "title": "MobiusFF Sarah Animations", "feeRequired": 0,
     "publishedDatetime": "2026-08-26T23:34:51+09:00", "isRestricted": False,
     "user": {"name": "InitialA"}, "cover": {"url": "https://img.test/cover.jpg"},
     "excerpt": "Public release"},
    {"id": "12400000", "title": "Paid preview", "feeRequired": 500,
     "publishedDatetime": "2026-08-20T00:00:00+09:00", "isRestricted": True},
]}}).encode()

#: 视频帖的正文只有一个 file 块，`fileMap` 里没有 `thumbnailUrl`——FANBOX 不给
#: 视频出图。列表接口那张 cover 是这篇唯一能显示的缩略图。
FANBOX_VIDEO_DETAIL_JSON = json.dumps({"body": {"post": {"type": "file", "body": {
    "blocks": [{"type": "file", "fileId": "clip"}],
    "fileMap": {"clip": {"id": "clip", "name": "scene", "extension": "mp4",
                         "url": "https://downloads.fanbox.cc/scene.mp4", "size": 9991}},
}}}}).encode()

FANBOX_DETAIL_JSON = json.dumps({"body": {"post": {"type": "article", "body": {
    "blocks": [
        {"type": "p", "text": "gofile - https://gofile.io/d/OS2Qz9"},
        {"type": "image", "imageId": "one"},
        {"type": "image", "imageId": "two"},
    ],
    "imageMap": {
        "one": {"originalUrl": "https://downloads.fanbox.cc/one.jpg",
                "thumbnailUrl": "https://downloads.fanbox.cc/one-thumb.jpg",
                "width": 1920, "height": 1080},
        "two": {"originalUrl": "https://downloads.fanbox.cc/two.jpg",
                "thumbnailUrl": "https://downloads.fanbox.cc/two-thumb.jpg"},
    },
}}}}).encode()

GOFILE_JSON = json.dumps({"status": "ok", "data": {"type": "folder", "children": {
    "v1": {"id": "v1", "type": "file", "name": "one.mp4", "mimetype": "video/mp4",
           "link": "https://store1.gofile.io/download/one.mp4",
           "thumbnail": "https://store1.gofile.io/one.jpg", "size": 123},
    "v2": {"id": "v2", "type": "file", "name": "two.mp4", "mimetype": "video/mp4",
           "link": "https://store1.gofile.io/download/two.mp4"},
    "txt": {"id": "txt", "type": "file", "name": "readme.txt", "mimetype": "text/plain",
            "link": "https://store1.gofile.io/download/readme.txt"},
}}}).encode()

SUBSCRIBESTAR_HTML = b"""<html><body>
<div class="post is-shown" data-id="2650844">
  <a class="post-user" href="/initiala">InitialA</a>
  <div class="post-date"><a href="/posts/2650844">Aug 26, 2026 02:34 pm</a></div>
  <div class="post-title"><h2>MobiusFF Sarah Animations</h2></div>
  <div class="post-uploads"><img src="https://img.test/one.jpg"></div>
  <div class="post-content">Posted for FREE tiers</div>
</div></body></html>"""

PATREON_HTML = b"""<html><body><div class="card">
<a href="https://www.patreon.com/sample/posts/new-public-work-167576581"></a>
<h3>New public work</h3><p>2 days ago</p><img src="https://img.test/patreon.jpg">
<a href="https://www.patreon.com/sample/posts/new-public-work-167576581">duplicate</a>
</div></body></html>"""

# 属性名与 DOM 形状取自 2026-09-08 对 simpcity.cr 真实线程页的实测：`img.bbImage` 的
# `data-url` 是原图、`src` 是缩略图，分页在 `.pageNav-page a[href]`，表情是 `img.smilie`。
# 内容本身是编的。
SIMPCITY_FIRST_HTML = b"""<html data-logged-in="true" data-cookie-prefix="yMziCv8BrCZz1o7_">
<head><title>Sample Creator | SimpCity Forums</title></head><body>
<h1 class="p-title-value"><span class="label">OnlyFans</span> Sample Creator</h1>
<nav class="pageNav"><ul class="pageNav-main">
<li class="pageNav-page pageNav-page--current"><a href="/threads/sample-creator.4242/">1</a></li>
<li class="pageNav-page"><a href="/threads/sample-creator.4242/page-2">2</a></li>
<li class="pageNav-page pageNav-page--skipEnd"><a href="/threads/sample-creator.4242/page-7">7</a></li>
</ul></nav>
<article data-content="post-100" data-author="opener">
  <time datetime="2026-01-01T10:00:00+0000">Jan 1, 2026</time>
  <div class="bbWrapper">First page post
    <a href="https://imgpage.test/img/first"><img class="bbImage"
        src="https://cdn.imgpage.test/first.md.jpg" data-url="https://cdn.imgpage.test/first.jpg"></a>
  </div>
</article>
</body></html>"""

SIMPCITY_LAST_HTML = b"""<html data-logged-in="true" data-cookie-prefix="yMziCv8BrCZz1o7_">
<head><title>Sample Creator | SimpCity Forums</title></head><body>
<h1 class="p-title-value"><span class="label">OnlyFans</span> Sample Creator</h1>
<nav class="pageNav"><ul class="pageNav-main">
<li class="pageNav-page"><a href="/threads/sample-creator.4242/">1</a></li>
<li class="pageNav-page pageNav-page--current"><a href="/threads/sample-creator.4242/page-7">7</a></li>
</ul></nav>
<article data-content="post-700" data-author="uploader1">
  <time datetime="2026-09-07T10:00:00+0000">Sep 7, 2026</time>
  <div class="bbWrapper">New set
    <a href="https://imgpage.test/img/abc"><img class="bbImage"
        src="https://cdn.imgpage.test/abc.md.jpg" data-url="https://cdn.imgpage.test/abc.jpg"></a>
    <a href="https://imgpage.test/img/def"><img class="bbImage"
        src="https://cdn.imgpage.test/def.md.jpg" data-url="https://cdn.imgpage.test/def.jpg"></a>
    <img class="smilie smilie--emoji" src="https://cdn.jsdelivr.net/emoji.png">
    <a href="/redirect/?to=aHR0cHM6Ly9nb2ZpbGUuaW8vZC9hYmMxMjM&amp;e=1&amp;m=b64" class="link link--external">Gofile</a>
    <a href="/redirect/?to=aHR0cHM6Ly9waXhlbGRyYWluLmNvbS91L3pGM1BxVEpG&amp;e=1&amp;m=b64" class="link link--external">https://pixeldrain.com/u/zF3PqTJF</a>
    <a href="/redirect/?to=bm90IGEgdXJs&amp;e=1&amp;m=b64">broken</a>
    <div class="bbCodeBlock bbCodeBlock--unfurl js-unfurl fauxBlockLink" data-unfurl="true"
        data-url="https://mega.nz/folder/xyz" data-host="mega.nz">
      <a href="/redirect/?to=aHR0cHM6Ly9tZWdhLm56L2ZvbGRlci94eXo&amp;e=1&amp;m=b64" class="fauxBlockLink-blockLink">MEGA</a>
      <div class="contentRow-snippet">Unfurl snippet text</div></div>
  </div>
</article>
<article data-content="post-701" data-author="replier">
  <time datetime="2026-09-07T11:00:00+0000">Sep 7, 2026</time>
  <div class="bbWrapper"><blockquote class="bbCodeBlock">uploader1 said:
    <a href="https://gofile.io/d/abc123">Gofile</a> Click to expand...</blockquote>
  thanks!</div>
</article>
<article data-content="post-702" data-author="uploader2">
  <time datetime="2026-09-07T12:00:00+0000">Sep 7, 2026</time>
  <div class="bbWrapper">Attachment and player only
    <a href="/attachments/clip-mp4.9001/">clip.mp4</a>
    <div class="bbMediaWrapper"><iframe src="https://player.test/e/xyz"></iframe></div>
  </div>
</article>
</body></html>"""

SIMPCITY_GUEST_HTML = b"""<html data-logged-in="false"><body>
<h1 class="p-title-value">Sample Creator</h1></body></html>"""

SIMPCITY_SEARCH_FORM = b"""<html data-logged-in="true"><body>
<form action="/search/search"><input type="hidden" name="_xfToken" value="1757300000,sc" /></form>
</body></html>"""

# 2026-09-08 实测 `solazola` 按标题命中的两行：资源线程与讨论帖各一条，标题前挂着版块标签。
SIMPCITY_SEARCH_RESULTS = b"""<html data-logged-in="true"><body>
<div class="contentRow"><div class="contentRow-main">
<h3 class="contentRow-title"><a href="/threads/solazola-discussion.392510/"><span class="label label--primary" dir="auto">Simp Chat</span><span class="label-append">&nbsp;</span><em class="textHighlight">solazola</em> discussion</a></h3>
<div class="contentRow-minor">HatEF Thread Sep 11, 2024 Replies: 124 Forum: Model Discussion</div>
</div></div>
<div class="contentRow"><div class="contentRow-main">
<h3 class="contentRow-title"><a href="/threads/solazola-baby_sue.17401/"><span class="label label--accent" dir="auto">OnlyFans</span><span class="label-append">&nbsp;</span><em class="textHighlight">Solazola</em> / baby_sue</a></h3>
</div></div>
<div class="contentRow"><div class="contentRow-main">
<h3 class="contentRow-title"><a href="/threads/solazola-baby_sue.17401/page-15#post-50529217">Solazola / baby_sue</a></h3>
</div></div>
</body></html>"""


class OfficialConnectorTests(unittest.TestCase):
    def test_fanbox_keeps_only_public_free_posts(self):
        seen = []
        def route(request):
            seen.append(request)
            return HttpResponse(200, {}, FANBOX_DETAIL_JSON if "post.info" in request.url
                                else FANBOX_JSON)
        result = FanboxConnector(transport=_routed(route)).fetch("ffxivinitiala")
        self.assertIn("creatorId=ffxivinitiala", seen[0].url)
        self.assertEqual(seen[0].headers["Origin"], "https://www.fanbox.cc")
        self.assertIn("Mozilla/5.0", seen[0].headers["User-Agent"])
        self.assertIn("Mozilla/5.0", seen[1].headers["User-Agent"])
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.candidates[0].group_hint, "fanbox:12489354")
        self.assertEqual(result.candidates[0].published_at, "2026-08-26T14:34:51Z")
        self.assertEqual(len(result.candidates[0].extra["media_items"]), 2)
        self.assertEqual(result.candidates[0].extra["media_items"][0]["width"], 1920)
        self.assertEqual(result.candidates[0].extra["media_items"][1]["width"], None)
        self.assertEqual(result.candidates[0].extra["links"],
                         ["https://gofile.io/d/OS2Qz9"])
        self.assertEqual(result.candidates[0].extra["post_type"], "article")
        self.assertTrue(result.candidates[0].extra["media_dims"])
        self.assertEqual(result.candidates[0].extra["image_count"], 2)
        self.assertEqual(result.candidates[0].extra["video_count"], 0)
        self.assertEqual(result.candidates[0].extra["file_count"], 0)
        self.assertEqual(result.probed, 1)

    def test_fanbox_history_pages_follow_the_site_cursor_list(self):
        seen = []
        def route(request):
            seen.append(request.url)
            if "paginateCreator" in request.url:
                return HttpResponse(200, {}, json.dumps({"body": {"pageUrls": [
                    "https://api.fanbox.cc/post.listCreator?creatorId=x&firstId=11",
                    "https://api.fanbox.cc/post.listCreator?creatorId=x&firstId=21",
                ]}}).encode())
            return HttpResponse(200, {}, FANBOX_JSON)
        result = FanboxConnector(transport=_routed(route)).fetch("ffxivinitiala", page=2)
        self.assertIn("paginateCreator", seen[0])
        self.assertIn("firstId=21", seen[1])
        # 其余请求是第二阶段的详情补全。
        self.assertTrue(all("post.info" in url for url in seen[2:]))
        self.assertEqual(result.candidates[0].external_id, "12489354")

    def test_fanbox_history_walk_ends_when_the_cursor_list_runs_out(self):
        def route(request):
            return HttpResponse(200, {}, json.dumps({"body": {"pageUrls": [
                "https://api.fanbox.cc/post.listCreator?creatorId=x&firstId=11",
            ]}}).encode())
        connector = FanboxConnector(transport=_routed(route))
        with self.assertRaises(FollowHistoryEnd):
            connector.fetch("ffxivinitiala", page=2)

    def test_fanbox_history_walk_ends_on_an_empty_cursor_page(self):
        def route(request):
            if "paginateCreator" in request.url:
                return HttpResponse(200, {}, json.dumps({"body": {"pageUrls": [
                    "https://api.fanbox.cc/post.listCreator?creatorId=x&firstId=11",
                ]}}).encode())
            return HttpResponse(200, {}, json.dumps({"body": {"posts": []}}).encode())
        connector = FanboxConnector(transport=_routed(route))
        with self.assertRaises(FollowHistoryEnd):
            connector.fetch("ffxivinitiala", page=1)

    def test_a_fanbox_video_post_keeps_the_cover_as_its_thumbnail(self):
        """视频帖的缩略图退回列表封面，不拿视频地址当图。

        FANBOX 的 `fileMap` 只给视频本体，没有 `thumbnailUrl`。把 mp4 地址填进
        `thumb_url`，关注页那张 `<img>` 就指着一个视频文件——浏览器拉完几百兆也
        画不出东西，卡片上是一片空白。
        """
        def route(request):
            return HttpResponse(200, {}, FANBOX_VIDEO_DETAIL_JSON if "post.info" in request.url
                                else FANBOX_JSON)
        candidate = FanboxConnector(transport=_routed(route)).fetch("ffxivinitiala").candidates[0]
        self.assertEqual(candidate.extra["media_items"][0]["media_kind"], "video")
        self.assertIsNone(candidate.extra["media_items"][0]["thumb_url"])
        self.assertEqual(candidate.thumb_url, "https://img.test/cover.jpg")

    def test_fanbox_routes_only_post_info_through_the_browser_transport(self):
        list_seen = []
        detail_seen = []

        def list_route(request):
            list_seen.append(request)
            return HttpResponse(200, {}, FANBOX_JSON)

        def detail_route(request):
            detail_seen.append(request)
            return HttpResponse(200, {}, FANBOX_DETAIL_JSON)

        result = FanboxConnector(
            transport=_routed(list_route),
            detail_transport=_routed(detail_route),
        ).fetch("ffxivinitiala")
        self.assertEqual(len(result.candidates), 1)
        self.assertTrue(all("post.listCreator" in request.url for request in list_seen))
        self.assertEqual(len(detail_seen), 1)
        self.assertIn("post.info", detail_seen[0].url)
        self.assertEqual(USER_AGENT, detail_seen[0].headers["User-Agent"])
        self.assertEqual(
            detail_seen[0].headers["Referer"],
            "https://www.fanbox.cc/@ffxivinitiala/posts/12489354",
        )

    def test_fanbox_uses_gofile_api_token_and_keeps_only_playable_files(self):
        seen = []
        def route(request):
            seen.append(request)
            if "api.gofile.io" in request.url:
                return HttpResponse(200, {}, GOFILE_JSON)
            return HttpResponse(200, {}, FANBOX_DETAIL_JSON if "post.info" in request.url
                                else FANBOX_JSON)
        result = FanboxConnector(
            transport=_routed(route),
            gofile_credential=Credential("gofile", {"api_token": "secret"}),
        ).fetch("ffxivinitiala")
        post = result.candidates[0]
        self.assertEqual(post.extra["gofile_video_count"], 2)
        self.assertEqual(len(post.extra["media_items"]), 4)
        gofile_request = next(request for request in seen if "api.gofile.io" in request.url)
        self.assertEqual(gofile_request.headers["Authorization"], "Bearer secret")
        self.assertNotIn("secret", gofile_request.url)

    def test_fanbox_cookie_stays_on_fanbox_requests(self):
        seen = []
        def route(request):
            seen.append(request)
            if "api.gofile.io" in request.url:
                return HttpResponse(200, {}, GOFILE_JSON)
            return HttpResponse(200, {}, FANBOX_DETAIL_JSON if "post.info" in request.url
                                else FANBOX_JSON)
        FanboxConnector(
            transport=_routed(route),
            credential=Credential("fanbox", {"cookie": "FANBOXSESSID=session"}),
            gofile_credential=Credential("gofile", {"api_token": "secret"}),
        ).fetch("ffxivinitiala")
        fanbox_requests = [request for request in seen if "api.fanbox.cc" in request.url]
        self.assertTrue(fanbox_requests)
        self.assertTrue(all(request.headers.get("Cookie") == "FANBOXSESSID=session"
                            for request in fanbox_requests))
        gofile_request = next(request for request in seen if "api.gofile.io" in request.url)
        self.assertNotIn("Cookie", gofile_request.headers)

    def test_gofile_premium_requirement_is_not_reported_as_a_bad_token(self):
        def route(request):
            if "api.gofile.io" in request.url:
                return HttpResponse(401, {"content-type": "application/json"},
                                    b'{"status":"error-notPremium","data":{}}')
            return HttpResponse(200, {}, FANBOX_DETAIL_JSON if "post.info" in request.url
                                else FANBOX_JSON)
        connector = FanboxConnector(
            transport=_routed(route),
            gofile_credential=Credential("gofile", {"api_token": "valid-token"}),
        )
        with self.assertRaises(FollowSourceError) as caught:
            connector.fetch("ffxivinitiala")
        self.assertIn("Premium", str(caught.exception))
        self.assertIn("token 有效", str(caught.exception))

    def test_one_blocked_detail_keeps_the_public_list_item(self):
        def route(request):
            if "post.info" in request.url:
                return HttpResponse(403, {}, b"challenge")
            return HttpResponse(200, {}, FANBOX_JSON)
        result = FanboxConnector(transport=_routed(route)).fetch("ffxivinitiala")
        self.assertEqual(len(result.candidates), 1)
        self.assertIn("HTTP 403", result.candidates[0].extra["media_error"])
        # 不写空清单：`partial` 行落库走 `json_patch`，补丁里的 null 是「删掉这个
        # 键」，一篇临时被挡就会把上一轮取到的媒体清单抹掉。
        self.assertNotIn("media_items", result.candidates[0].extra)
        self.assertTrue(result.candidates[0].partial,
                        "详情没取到就不算补齐，下一轮还要再问一次")

    def test_fanbox_asks_post_info_only_for_posts_it_has_not_read_yet(self):
        """已经补齐过的帖子不再打一次 post.info。

        一页 10 条、每条一次详情，是这条来源里唯一会随条目数增长的请求。列表页的
        304 只在**整页没变**时省下它们；新增一条就会连带把另外九条重新问一遍。
        """
        seen = []

        def route(request):
            seen.append(request.url)
            return HttpResponse(200, {}, FANBOX_DETAIL_JSON
                                if "post.info" in request.url else FANBOX_JSON)

        result = FanboxConnector(transport=_routed(route),
                                 enrich_skip={"12489354"}).fetch("ffxivinitiala")
        self.assertEqual([url for url in seen if "post.info" in url], [])
        self.assertEqual(result.probed, 0)
        item = result.candidates[0]
        self.assertTrue(item.partial, "跳过第二阶段的条目必须标 partial")
        self.assertNotIn("media_items", item.extra,
                         "详情才知道的键不能带着 null 进补丁，那会把库里的媒体删掉")
        # 列表本来就给得出的那几项仍然在：候选不是空壳。
        self.assertEqual(item.url, "https://ffxivinitiala.fanbox.cc/posts/12489354")
        self.assertEqual(item.published_at, "2026-08-26T14:34:51Z")

    def test_fanbox_general_error_is_reported_instead_of_as_bad_json_shape(self):
        def route(request):
            if "post.info" in request.url:
                return HttpResponse(200, {"content-type": "application/json"},
                                    b'{"error":"general_error"}')
            return HttpResponse(200, {}, FANBOX_JSON)

        result = FanboxConnector(transport=_routed(route)).fetch("ffxivinitiala")
        self.assertIn("general_error", result.candidates[0].extra["media_error"])

    def test_subscribestar_reads_public_profile_posts_without_login(self):
        result = SubscribeStarConnector(
            transport=_transport(body=SUBSCRIBESTAR_HTML)).fetch(
                "subscribestar.adult/initiala")
        self.assertEqual(len(result.candidates), 1)
        post = result.candidates[0]
        self.assertEqual(post.external_id, "2650844")
        self.assertEqual(post.title, "MobiusFF Sarah Animations")
        self.assertEqual(post.author, "InitialA")
        self.assertEqual(post.url, "https://subscribestar.adult/posts/2650844")

    def test_patreon_uses_server_rendered_public_cards_and_deduplicates_links(self):
        result = PatreonConnector(transport=_transport(body=PATREON_HTML)).fetch("sample")
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].external_id, "167576581")
        self.assertEqual(result.candidates[0].title, "New public work")
        self.assertEqual(result.candidates[0].group_hint, "patreon:167576581")

# 形状取自 2026-08-25 对 api.rule34.xxx 的实测响应：顶层是裸列表，`image` 是 32 位
# 十六进制哈希（15/15），`parent_id` 全是 0（15/15），`source` 13/15 有值。
RULE34XXX_JSON = json.dumps([
    {"id": 18534395, "image": "3df1cbc67e072d6144588d6c80e490ea.mp4", "parent_id": 0,
     "tags": "lazyprocrastinator fiona blush video sound", "change": 1787445373,
     "file_url": "https://api-cdn-mp4.rule34.xxx/images/1232/3df1.mp4",
     "sample_url": "https://api-cdn.rule34.xxx/images/1232/3df1.jpg",
     "preview_url": "https://api-cdn.rule34.xxx/thumbnails/1232/t_3df1.jpg",
     "width": 1280, "height": 720,
     "score": 72, "source": "https://lazyprocrast.fanbox.cc/posts/12304831"},
    {"id": 18534396, "image": "3bda1572c365b223d8b287c538a38956.mp4", "parent_id": 0,
     "tags": "lazyprocrastinator fiona video", "change": 1787445300,
     "file_url": "https://api-cdn-mp4.rule34.xxx/images/1232/3bda.mp4",
     "score": 70, "source": "https://www.fanbox.cc/@lazyprocrast/posts/12304831"},
    {"id": 18534397, "image": "93eaeadff5effabe078c738aab85b3bc.mp4", "parent_id": 0,
     "tags": "lazyprocrastinator sayuri video", "change": 1787445200,
     "file_url": "https://api-cdn-mp4.rule34.xxx/images/1232/93ea.mp4", "score": 12},
    {"id": 18534398, "image": "fiona_paizuri_nude.mp4", "parent_id": 18534397,
     "tags": "lazyprocrastinator fiona nude", "change": 1787445100,
     "file_url": "https://api-cdn-mp4.rule34.xxx/images/1232/fp.mp4", "score": 9},
]).encode()

PAHEAL_LIST_HTML = b"""<div class='shm-image-list'>
<div class='shm-thumb thumb' data-ext='mp4'
 data-tags='amina animated blender final_fantasy_vii initiala tifa_lockhart'
 data-post-id='7428820'><a class='shm-thumb-link' href='/post/view/7428820'>
 <img src='https://r34t.paheal.net/df/fb/thumb'></a>
 <a href='https://r34i.paheal-cdn.net/df/fb/video'>File Only</a></div></div>"""

PAHEAL_DETAIL_HTML = b"""<video id='main_image' poster='https://r34t.paheal.net/df/fb/thumb'>
<source src='https://r34i.paheal-cdn.net/df/fb/video' type='video/mp4'></video>
<table><tr data-row='Uploader'><td><a class='username'>VHSephi</a>
<time datetime='2026-08-26T15:21:00+00:00'></time></td></tr>
<tr data-row='Tags'><td><a class='tag'>Amina</a><a class='tag'>animated</a>
<a class='tag'>Final_Fantasy_VII</a><a class='tag'>InitialA</a>
<a class='tag'>Tifa_Lockhart</a></td></tr>
<tr data-row='Source Link'><th><a href='/source_history/7428820'>Source</a></th>
<td><a href='https://subscribestar.adult/posts/2639932'>origin</a></td></tr>
<tr data-row='Info'><td>1280x720, 28.4s // 6.3MB // mp4</td></tr></table>"""


def _routed(route):
    """按 URL 分派的测试传输。探测详情页的请求要能和列表请求分开回不同的响应。"""
    def call(request, timeout, max_bytes):
        return route(request)
    return call


class Rule34PahealConnectorTests(unittest.TestCase):
    def _connector(self):
        return Rule34PahealConnector(transport=_routed(
            lambda request: HttpResponse(
                200, {}, PAHEAL_DETAIL_HTML if "/post/view/" in request.url
                else PAHEAL_LIST_HTML)))

    def test_tag_page_uses_detail_source_for_exact_cross_site_grouping(self):
        result = self._connector().fetch("initiala")
        self.assertEqual(len(result.candidates), 1)
        item = result.candidates[0]
        self.assertEqual(item.external_id, "7428820")
        self.assertEqual(item.group_hint, "subscribestar:2639932")
        self.assertEqual(item.duration, 28.4)
        self.assertEqual(item.published_at, "2026-08-26T15:21:00Z")
        self.assertEqual(item.media_url,
                         "https://r34i.paheal-cdn.net/df/fb/video")
        self.assertFalse(item.title_is_name)

    def test_list_thumb_media_kind_reads_extension_or_mime(self):
        """视频帖在图片视图里没有图片尺寸可占位，判成图片就混进瀑布流。"""
        listing = b"""<div class='shm-image-list'>
<div class='shm-thumb thumb' data-ext='mp4' data-tags='initiala' data-post-id='1'></div>
<div class='shm-thumb thumb' data-mime='video/mp4' data-tags='initiala' data-post-id='2'></div>
<div class='shm-thumb thumb' data-mime='image/jpeg' data-tags='initiala' data-post-id='3'></div>
</div>"""
        connector = Rule34PahealConnector(transport=_routed(
            lambda request: HttpResponse(
                200, {}, PAHEAL_DETAIL_HTML if "/post/view/" in request.url else listing)))
        kinds = {item.external_id: item.extra["media_kind"]
                 for item in connector.fetch("initiala").candidates}
        self.assertEqual(kinds, {"1": "video", "2": "video", "3": "image"})

    def test_backfill_404_means_history_is_exhausted(self):
        connector = Rule34PahealConnector(transport=_transport(status=404))
        with self.assertRaises(FollowHistoryEnd):
            connector.fetch("initiala", page=3)

    def test_a_throttled_detail_page_is_retried_without_really_waiting(self):
        """列表一页 24 条、每条一次详情页，被按频率挡回来是常态。

        退避真的 sleep 会让这条测试等满 7 秒，于是它一直没被写；没有测试的结果是
        「挡回来一次就当这条没有上传时间」这种缺陷只能在生产数据里发现。
        """
        attempts, waits = [], []

        def route(request):
            if "/post/view/" not in request.url:
                return HttpResponse(200, {}, PAHEAL_LIST_HTML)
            attempts.append(request.url)
            if len(attempts) < 3:
                return HttpResponse(429, {}, b"slow down")
            return HttpResponse(200, {}, PAHEAL_DETAIL_HTML)

        result = Rule34PahealConnector(transport=_routed(route),
                                       sleeper=waits.append).fetch("initiala")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(waits, [1.0, 2.0], "退避节奏是声明的那三档，按需取用")
        item = result.candidates[0]
        self.assertEqual(item.published_at, "2026-08-26T15:21:00Z")
        self.assertFalse(item.partial)

    def test_a_detail_page_that_stays_blocked_leaves_the_row_partial(self):
        """取不到就保持列表视图。落库时 partial 行不动上一轮取到的时间与时长。"""
        waits = []

        def route(request):
            if "/post/view/" not in request.url:
                return HttpResponse(200, {}, PAHEAL_LIST_HTML)
            return HttpResponse(503, {}, b"nope")

        result = Rule34PahealConnector(transport=_routed(route),
                                       sleeper=waits.append).fetch("initiala")
        item = result.candidates[0]
        self.assertTrue(item.partial)
        self.assertIsNone(item.published_at)
        self.assertIsNone(item.duration)
        # 列表页本来就给得出的那几项仍然在：候选不是空壳。
        self.assertEqual(item.external_id, "7428820")
        self.assertEqual(item.media_url, "https://r34i.paheal-cdn.net/df/fb/video")
        self.assertEqual(item.group_hint, "rule34paheal:post:7428820")
        self.assertEqual(waits, [1.0, 2.0, 4.0, 8.0])

    def test_details_already_taken_cost_no_request_at_all(self):
        """第二阶段只打新条目。这是唯一会让请求数随条目数增长的路径。"""
        seen = []

        def route(request):
            seen.append(request.url)
            return HttpResponse(200, {}, PAHEAL_DETAIL_HTML
                                if "/post/view/" in request.url else PAHEAL_LIST_HTML)

        result = Rule34PahealConnector(transport=_routed(route),
                                       enrich_skip={"7428820"}).fetch("initiala")
        self.assertEqual(len(seen), 1, "库里已经补齐过的条目不该再打一次详情页")
        self.assertEqual(result.probed, 0)
        item = result.candidates[0]
        self.assertTrue(item.partial, "跳过第二阶段的条目必须标 partial")
        self.assertNotIn("source", item.extra,
                         "详情才知道的键不能带着 null 进补丁，那会把库里的出处删掉")


class KemonoConnectorTests(unittest.TestCase):
    def test_fetch_normalizes_posts_and_sends_the_documented_accept_header(self):
        seen = []
        connector = KemonoConnector(transport=_transport(body=KEMONO_POSTS, record=seen,
                                                         headers={"ETag": '"k1"'}))
        result = connector.fetch("fanbox/30917150")
        self.assertEqual(seen[0].url,
                         "https://kemono.cr/api/v1/fanbox/user/30917150/posts")
        # 站点自己在 403 响应体里要求抓取带这个头，不是绕过防护。
        self.assertEqual(seen[0].headers["Accept"], "text/css")
        self.assertEqual(seen[0].headers["User-Agent"], USER_AGENT)
        self.assertEqual(result.etag, '"k1"')
        self.assertEqual(result.semantics, "work")
        self.assertEqual(len(result.candidates), 2)
        first = result.candidates[0]
        self.assertEqual(first.external_id, "11406814")
        self.assertEqual(first.published_at, "2026-02-14T21:51:10Z")
        self.assertEqual(first.url,
                         "https://kemono.cr/fanbox/user/30917150/post/11406814")
        # 交付文件取非图片的那个：夹具里 file 是 a.png、附件是 a.zip，压缩包才是正片。
        # 按 file.path 优先会把整条判成图片——pawchive 上作者用 gif 当预览、mp4 放附件，
        # 正是这条路径让两个 1080p 正片消失（2026-08-30 取证）。
        # /data 前缀是必需的：少了它三站的原始文件都是 404（2026-08-30 取证）。
        self.assertEqual(first.media_url, "https://kemono.cr/data/1c/fa/1cfae7.zip")
        self.assertEqual(first.extra["attachment_count"], 1)
        # post id 就是原平台的 post id，和别的站点从 source 归一出的键同一个命名空间。
        self.assertEqual(first.group_hint, "fanbox:11406814")

    def test_archive_posts_get_a_cover_thumbnail(self):
        """归档站的卡片必须带封面：不设 `thumb_url` 不是取不到，是压根没去取。

        2026-08-30 实测缩略图走 `img.` 子域：主域 kemono.cr 回 302、pawchive.pw 回 404；
        去掉 `thumbnail/` 前缀则是 404，所以这个前缀是必需的。
        """
        result = KemonoConnector(transport=_transport(body=KEMONO_POSTS)).fetch("fanbox/1")
        self.assertEqual(result.candidates[0].thumb_url,
                         "https://img.kemono.cr/thumbnail/data/1c/fa/1cfae7.png")

    def test_archive_video_uses_an_image_attachment_as_its_cover(self):
        """主资源是视频/压缩包时，后附的图片仍然是可用封面。"""
        posts = [{
            "id": "cover-after-video", "title": "Animation pack",
            "published": "2026-08-27T00:00:00Z",
            "file": {"path": "/video/release.mp4"},
            "attachments": [
                {"path": "/video/notes.txt"},
                {"path": "/cover/release.webp"},
            ],
        }]
        result = KemonoConnector(
            transport=_transport(body=json.dumps(posts).encode())).fetch("fanbox/1")
        candidate = result.candidates[0]
        self.assertEqual(candidate.media_url, "https://kemono.cr/data/video/release.mp4")
        self.assertEqual(candidate.thumb_url,
                         "https://img.kemono.cr/thumbnail/data/cover/release.webp")

    def test_archive_posts_with_several_media_list_them_for_the_detail_carousel(self):
        """两张以上图或视频的帖子把它们列进媒体清单，详情里才翻得到第二张。

        交付文件排第一（条目级媒体类型跟清单首项走），其余按帖子顺序；`file` 在附件里
        重复列出的按路径去重；文本、压缩包不进清单。只有一张的帖子不建清单。
        """
        posts = [
            {"id": "multi", "title": "Pack", "published": "2026-08-27T00:00:00Z",
             "file": {"name": "cover.gif", "path": "/a/cover.gif"},
             "attachments": [{"name": "cover.gif", "path": "/a/cover.gif"},
                             {"name": "clip.mp4", "path": "/a/clip.mp4"},
                             {"name": "notes.txt", "path": "/a/notes.txt"},
                             {"name": "page2.jpg", "path": "/a/page2.jpg"}]},
            {"id": "single", "title": "One", "published": "2026-08-26T00:00:00Z",
             "file": {"name": "only.png", "path": "/b/only.png"},
             "attachments": [{"name": "only.zip", "path": "/b/only.zip"}]},
        ]
        result = KemonoConnector("pawchive", transport=_transport(
            body=json.dumps(posts).encode())).fetch("patreon/1")
        multi, single = result.candidates
        self.assertEqual(multi.media_url, "https://file.pawchive.pw/data/a/clip.mp4")
        items = multi.extra["media_items"]
        self.assertEqual([(media["id"], media["media_kind"]) for media in items],
                         [("/a/clip.mp4", "video"), ("/a/cover.gif", "image"),
                          ("/a/page2.jpg", "image")])
        self.assertEqual(items[1], {
            "id": "/a/cover.gif", "name": "cover.gif",
            "url": "https://file.pawchive.pw/data/a/cover.gif",
            "thumb_url": "https://img.pawchive.pw/thumbnail/data/a/cover.gif",
            "media_kind": "image", "resource_provider": "pawchive"})
        self.assertIsNone(items[0]["thumb_url"])
        # 卡面封面与清单里第一张图是同一张，界面据此沿用条目级量过的比例。
        self.assertEqual(multi.thumb_url, items[1]["thumb_url"])
        self.assertNotIn("media_items", single.extra)

    def test_no_thumbnail_is_offered_for_things_that_have_none(self):
        # 视频和压缩包没有缩略图，给了也是 404——那会让卡片显示一张碎图，
        # 比一个干净的占位更糟。
        connector = KemonoConnector()
        self.assertIsNone(connector._thumb_url("/a/b/clip.mp4"))
        self.assertIsNone(connector._thumb_url("/a/b/pack.zip"))
        self.assertIsNone(connector._thumb_url(None))

    def test_a_post_that_only_links_a_file_host_is_a_release(self):
        """用户定的判据：资源贴要么贴附件，要么附上网盘链接。

        只做图的作者也算——一张图片附件就够，不要求压缩包或视频。
        """
        posts = json.loads(KEMONO_POSTS)
        posts.append({"id": "777", "title": "Monthly pack",
                      "published": "2026-02-01T00:00:00", "file": {}, "attachments": [],
                      "substring": "gofile - https://gofile.io/d/xyz"})
        result = KemonoConnector(
            transport=_transport(body=json.dumps(posts).encode())).fetch("fanbox/1")
        self.assertIn("777", [c.external_id for c in result.candidates])
        self.assertEqual(result.skipped, 0)
        self.assertEqual(result.probed, 0, "摘要里就能判出来，不该多打一次站点")

    def test_a_post_it_cannot_judge_is_probed_not_guessed(self):
        """列表判不出来时去抓详情，而不是猜。

        列表接口只给 `substring`（正文摘要），网盘链接常常在摘要之外。判不出来直接
        当「不是 release」会删掉真东西，当「是」又等于没过滤——所以判据是三态，
        只有第三种答案才允许下一步联网。
        """
        posts = json.loads(KEMONO_POSTS)
        posts.append({"id": "888", "title": "March pack", "published": "2026-03-01T00:00:00",
                      "file": {}, "attachments": [], "substring": "Here you go"})
        detail = json.dumps({"post": {"id": "888",
                                      "content": "<p>https://mega.nz/folder/abc</p>"}}).encode()
        bodies = {"posts": json.dumps(posts).encode(), "post/888": detail}

        def route(request):
            for key, body in bodies.items():
                if request.url.endswith(key):
                    return HttpResponse(200, {}, body)
            raise AssertionError(f"未预期的请求：{request.url}")

        result = KemonoConnector(transport=_routed(route)).fetch("fanbox/1")
        self.assertIn("888", [c.external_id for c in result.candidates])
        self.assertEqual(result.probed, 1)

    def test_a_post_already_in_the_ledger_is_not_probed_again(self):
        """库里有的帖子当初判过保留；每次检查都再探一遍，请求数就跟着库存涨。"""
        posts = json.loads(KEMONO_POSTS)
        posts.append({"id": "888", "title": "March pack", "published": "2026-03-01T00:00:00",
                      "file": {}, "attachments": [], "substring": "Here you go"})

        def route(request):
            if "/post/" in request.url:
                raise AssertionError(f"未预期的详情请求：{request.url}")
            return HttpResponse(200, {}, json.dumps(posts).encode())

        result = KemonoConnector(transport=_routed(route), enrich_skip={"888"}).fetch("fanbox/1")
        self.assertIn("888", [c.external_id for c in result.candidates])
        self.assertEqual(result.probed, 0)

    def test_a_rate_limited_probe_stops_the_check(self):
        """探测被 429 挡回来时不能当成「保留」接着探下一帖，要让这次检查停下。"""
        posts = json.loads(KEMONO_POSTS)
        posts.extend({"id": str(ident), "title": f"pack {ident}",
                      "published": "2026-03-01T00:00:00", "file": {}, "attachments": [],
                      "substring": "Here"} for ident in (801, 802, 803))
        probes = []

        def route(request):
            if "/post/" in request.url:
                probes.append(request.url)
                return HttpResponse(429, {"Retry-After": "90"}, b"")
            return HttpResponse(200, {}, json.dumps(posts).encode())

        with self.assertRaises(FollowSourceRateLimited) as raised:
            KemonoConnector(transport=_routed(route)).fetch("fanbox/1")
        self.assertEqual(raised.exception.retry_after, 90.0)
        self.assertEqual(len(probes), 1)

    def test_a_post_with_no_resource_at_all_is_dropped(self):
        """公告、感谢这类帖子既没有附件也没有网盘链接，抓了详情之后确认丢掉。"""
        posts = json.loads(KEMONO_POSTS)
        posts.append({"id": "999", "title": "Thanks for 10k followers!",
                      "published": "2026-02-01T00:00:00", "file": {}, "attachments": [],
                      "substring": "You are all wonderful"})
        detail = json.dumps({"post": {"id": "999",
                                      "content": "<p>See you next month</p>"}}).encode()

        def route(request):
            if request.url.endswith("post/999"):
                return HttpResponse(200, {}, detail)
            return HttpResponse(200, {}, json.dumps(posts).encode())

        result = KemonoConnector(transport=_routed(route)).fetch("fanbox/1")
        self.assertEqual(result.skipped, 1)
        self.assertNotIn("999", [c.external_id for c in result.candidates])

    def test_an_unreachable_probe_keeps_the_post(self):
        """抓不到详情就保留。

        网络抖一下就删掉用户的一份更新，是拿一次失败的请求换一次不可见的数据丢失。
        探测额度用完时同理——额度是内部限额，不该变成删数据的理由。
        """
        posts = json.loads(KEMONO_POSTS)
        posts.append({"id": "555", "title": "April pack", "published": "2026-04-01T00:00:00",
                      "file": {}, "attachments": [], "substring": "Here"})

        def route(request):
            if "post/555" in request.url:
                return HttpResponse(503, {}, b"")
            return HttpResponse(200, {}, json.dumps(posts).encode())

        waits = []
        result = KemonoConnector(transport=_routed(route), sleeper=waits.append).fetch("fanbox/1")
        self.assertIn("555", [c.external_id for c in result.candidates])
        self.assertEqual(waits, [1, 2, 4, 8])

        # 额度为 0 时不联网，也不删。
        offline = KemonoConnector(max_probes=0,
                                  transport=_transport(body=json.dumps(posts).encode()))
        kept = offline.fetch("fanbox/1")
        self.assertIn("555", [c.external_id for c in kept.candidates])
        self.assertEqual(kept.probed, 0)

    def test_a_release_named_after_a_poll_is_still_a_release(self):
        """**不要按标题关键词丢投票贴。**

        2026-08-27 拿 LazyProcrastinator 的真实 50 条跑过一版
        `poll|vote|survey|…` 正则，丢掉 18 条，其中包括
        `Public Poll Release + Littlest Ramble`、`October Poll Animations Released`
        ——这位作者的正片就是按投票结果命名的。列表接口不给帖子类型字段，
        标题不足以区分「投票贴」和「投票选出的成品」，误删一份 release
        比多出一张卡片糟糕得多。这条测试就是为了挡住那个正则再被加回来。
        """
        posts = json.loads(KEMONO_POSTS)
        posts[0] = {**posts[0], "id": "777",
                    "title": "Public Poll Release + Littlest Ramble"}
        result = KemonoConnector(
            transport=_transport(body=json.dumps(posts).encode())).fetch("fanbox/1")
        self.assertIn("777", [c.external_id for c in result.candidates])
        self.assertEqual(result.skipped, 0)

    def test_paging_back_uses_an_offset_and_drops_the_conditional_headers(self):
        """往回翻要换页，而且**不能带条件请求头**。

        `If-None-Match` 里存的是第一页的 etag。拿它去问第二页，站点很可能回 304，
        用户点了「抓更早的」却什么都没发生——而且看起来和「确实没有更早的了」
        一模一样。

        2026-08-27 实测：kemono 的列表接口一页 50 条，`?o=50` 拿到的是第 51 条起，
        与第一页零重叠。
        """
        seen = []
        connector = KemonoConnector(
            transport=_transport(body=KEMONO_POSTS, record=seen))
        connector.fetch("fanbox/30917150", etag='"k1"', last_modified="then", page=2)
        self.assertEqual(seen[0].url,
                         "https://kemono.cr/api/v1/fanbox/user/30917150/posts?o=100")
        self.assertNotIn("If-None-Match", seen[0].headers)
        self.assertNotIn("If-Modified-Since", seen[0].headers)

    def test_the_first_page_still_sends_conditional_headers(self):
        # 常规检查必须保留条件请求：追更每天都在跑，没变就不该把整页再传一遍。
        seen = []
        KemonoConnector(transport=_transport(body=KEMONO_POSTS, record=seen)).fetch(
            "fanbox/30917150", etag='"k1"')
        self.assertEqual(seen[0].headers.get("If-None-Match"), '"k1"')
        self.assertNotIn("?o=", seen[0].url)

    def test_pawchive_returns_a_bare_list_and_uses_its_own_host(self):
        seen = []
        connector = KemonoConnector(provider="pawchive",
                                    transport=_transport(body=KEMONO_POSTS, record=seen))
        result = connector.fetch("fanbox/30917150")
        self.assertTrue(seen[0].url.startswith("https://pawchive.pw/"))
        self.assertEqual(result.provider, "pawchive")
        self.assertEqual(len(result.candidates), 2)

    def test_dict_shaped_payload_is_accepted(self):
        body = json.dumps({"posts": json.loads(KEMONO_POSTS)}).encode()
        result = KemonoConnector(transport=_transport(body=body)).fetch("fanbox/1")
        self.assertEqual(len(result.candidates), 2)

    def test_max_items_bounds_the_result(self):
        connector = KemonoConnector(max_items=1, transport=_transport(body=KEMONO_POSTS))
        self.assertEqual(len(connector.fetch("fanbox/1").candidates), 1)

    def test_not_modified_returns_no_candidates(self):
        connector = KemonoConnector(transport=_transport(status=304))
        result = connector.fetch("fanbox/1", etag='"k1"')
        self.assertTrue(result.not_modified)
        self.assertEqual(result.candidates, ())

    def test_backfill_400_means_history_is_exhausted(self):
        connector = KemonoConnector(transport=_transport(status=400))
        with self.assertRaises(FollowHistoryEnd):
            connector.fetch("fanbox/1", page=2)

    def test_malformed_ref_is_rejected_before_any_request(self):
        seen = []
        connector = KemonoConnector(transport=_transport(record=seen))
        for bad in ("30917150", "fanbox/", "../etc/passwd", "fanbox/a b"):
            with self.assertRaises(FollowSourceError):
                connector.fetch(bad)
        self.assertEqual(seen, [])

    def test_forbidden_status_names_credentials_or_bot_check(self):
        connector = KemonoConnector(transport=_transport(status=403))
        with self.assertRaises(FollowSourceError) as caught:
            connector.fetch("fanbox/1")
        self.assertIn("403", str(caught.exception))

    def test_unknown_kemono_host_is_rejected(self):
        with self.assertRaises(FollowSourceError):
            KemonoConnector(provider="nope")


class Rule34VideoConnectorTests(unittest.TestCase):
    def _fetch(self, body=RULE34VIDEO_HTML, **kwargs):
        return Rule34VideoConnector(transport=_transport(body=body), **kwargs).fetch(
            "lazyprocrastinator")

    def test_creator_page_yields_deduplicated_videos_with_duration(self):
        result = self._fetch()
        self.assertEqual([c.external_id for c in result.candidates],
                         ["4542721", "4542713"])
        self.assertEqual(result.candidates[0].duration, 20.0)
        self.assertEqual(result.candidates[1].duration, 433.0)

    def test_empty_backfill_page_means_history_is_exhausted(self):
        connector = Rule34VideoConnector(transport=_transport(body=b"<html></html>"))
        with self.assertRaises(FollowHistoryEnd):
            connector.fetch("lazyprocrastinator", page=4)

    def test_data_uri_placeholder_is_not_used_as_a_thumbnail(self):
        first = self._fetch().candidates[0]
        self.assertTrue(first.thumb_url.endswith("/3.jpg"))
        self.assertTrue(first.media_url.endswith("_preview.mp4/"))

    def test_relative_dates_are_marked_approximate(self):
        first = self._fetch().candidates[0]
        self.assertEqual(first.extra["published_precision"], "approximate")
        self.assertEqual(first.extra["added_text"], "1 week ago")
        self.assertIsNotNone(first.published_at)

    def test_missing_dates_are_reported_as_unknown_not_guessed(self):
        body = b'<a class="th" href="https://rule34video.com/video/1/x/" title="X"></a>'
        candidate = self._fetch(body=body).candidates[0]
        self.assertIsNone(candidate.published_at)
        self.assertEqual(candidate.extra["published_precision"], "unknown")

    def test_empty_parse_is_an_error_not_an_empty_success(self):
        # 结构变了却报「本次没有更新」，会把站点改版静默吞掉。
        with self.assertRaises(FollowSourceError):
            self._fetch(body=b"<html><body>no videos here</body></html>")

    def test_slug_is_validated(self):
        connector = Rule34VideoConnector(transport=_transport(body=RULE34VIDEO_HTML))
        with self.assertRaises(FollowSourceError):
            connector.fetch("../models")

    def test_detail_enrichment_adds_full_cover_video_date_and_content_tags(self):
        seen = []

        def transport(request, timeout, max_bytes):
            seen.append(request.url)
            body = (RULE34VIDEO_HTML if "/models/" in request.url
                    else RULE34VIDEO_DETAIL_HTML)
            return HttpResponse(200, {}, body)

        result = Rule34VideoConnector(transport=transport).fetch("lazyprocrastinator")
        first = result.candidates[0]
        self.assertEqual(result.probed, 2)
        self.assertEqual(first.published_at, "2026-08-18T00:00:00Z")
        self.assertEqual(first.extra["published_precision"], "exact")
        self.assertEqual(first.duration, 433.0)
        self.assertEqual(first.extra["tags"], ["deep throat", "breast squeeze"])
        self.assertEqual(first.extra["categories"], ["3D", "Final Fantasy"])
        self.assertEqual(first.extra["tag_types"]["3D"], "metadata")
        self.assertEqual(first.extra["tag_types"]["Final Fantasy"], "copyright")
        self.assertTrue(first.thumb_url.endswith("/preview.jpg"))
        self.assertIn("4542721_360.mp4", first.media_url)

    def _detail_with_credits(self, names, *, ready=None):
        credits = "".join(
            f'<a class="item btn_link video_meta_pill"'
            f' href="https://rule34video.com/models/m{n}/">{name}</a>'
            for n, name in enumerate(names)
        ).encode()
        detail = RULE34VIDEO_DETAIL_HTML.replace(b"</body>", credits + b"</body>")

        def transport(request, timeout, max_bytes):
            return HttpResponse(200, {},
                                RULE34VIDEO_HTML if "/models/" in request.url else detail)

        connector = Rule34VideoConnector(transport=transport)
        connector.candidate_ready = ready
        return connector.fetch("lazyprocrastinator")

    def test_detail_with_many_credited_models_is_not_collected(self):
        # 样本自带 LazyProcrastinator 一位，这里再添三位就是四位画面作者。
        ready = []
        result = self._detail_with_credits(["M1", "M2", "M3"], ready=ready.append)
        self.assertEqual(result.candidates, ())
        self.assertEqual(ready, [])
        self.assertEqual(result.skipped, 2)
        self.assertEqual(result.skipped_compilations, 2)

    def test_voice_and_audio_credits_do_not_make_a_work_a_collection(self):
        """配音和音效不算画面作者。

        站点把它们记在同一份 Artist 名单里，角色写在名字末尾的括号中。三位配音
        加一位音效的单人作品在名单上是五位，画面作者只有一位；数满名单会把
        `Yunara Showing Ahri Some Discipline`、`The Rite of Loss` 这类作品
        误判成合辑。
        """
        result = self._detail_with_credits(
            ["Adaline (VA)", "GeminiStarsign1 (VA)", "Cinderdryadva (va)",
             "Huntress___ (Audio/SFX)", "HentAudio (Audio)"])
        self.assertEqual(result.skipped_compilations, 0)
        first = result.candidates[0]
        self.assertEqual(first.extra["model_count"], 6)
        self.assertEqual(first.extra["visual_model_count"], 1)

    def test_a_video_already_detailed_in_the_ledger_is_not_probed_again(self):
        """库里补齐过的条目不再打详情页，额度全留给新条目。

        跳过的候选是 `partial`：落库时详情给的正片、封面、日期不被列表值盖掉；
        列表那份「预览片」「相对时间」也不带进 metadata。
        """
        seen = []

        def transport(request, timeout, max_bytes):
            seen.append(request.url)
            body = (RULE34VIDEO_HTML if "/models/" in request.url
                    else RULE34VIDEO_DETAIL_HTML)
            return HttpResponse(200, {}, body)

        result = Rule34VideoConnector(transport=transport, enrich_skip={"4542721"}).fetch(
            "lazyprocrastinator")
        known, fresh = result.candidates
        details = [url for url in seen if "/video/" in url]
        self.assertEqual(result.probed, 1)
        self.assertEqual(len(details), 1)
        self.assertNotIn("/video/4542721/", details[0])
        self.assertTrue(known.partial)
        self.assertNotIn("media_kind", known.extra)
        self.assertNotIn("published_precision", known.extra)
        self.assertFalse(fresh.partial)

    def test_a_failed_probe_does_not_overwrite_the_details_already_recorded(self):
        def transport(request, timeout, max_bytes):
            if "/models/" in request.url:
                return HttpResponse(200, {}, RULE34VIDEO_HTML)
            return HttpResponse(404, {}, b"")

        result = Rule34VideoConnector(transport=transport).fetch("lazyprocrastinator")
        self.assertTrue(all(candidate.partial for candidate in result.candidates))

    def test_a_rate_limited_probe_stops_the_check(self):
        """详情页被 429 挡回来，接着探剩下的只会让整站被封得更久。"""
        probes = []

        def transport(request, timeout, max_bytes):
            if "/models/" in request.url:
                return HttpResponse(200, {}, RULE34VIDEO_HTML)
            probes.append(request.url)
            return HttpResponse(429, {"Retry-After": "120"}, b"")

        with self.assertRaises(FollowSourceRateLimited) as raised:
            Rule34VideoConnector(transport=transport).fetch("lazyprocrastinator")
        self.assertEqual(raised.exception.retry_after, 120.0)
        self.assertEqual(len(probes), 1)


class PagingBackTests(unittest.TestCase):
    """各站往回翻的地址形状。都是 2026-08-27 实测过的端点。"""

    def test_rule34video_uses_the_async_block_endpoint(self):
        # KVS 的分页是异步块请求，`from` 是 1 起的两位页码。实测第 2 页 24 条、
        # 与第 1 页零重叠。
        seen = []
        Rule34VideoConnector(
            transport=_transport(body=RULE34VIDEO_HTML, record=seen)).fetch(
                "lazyprocrastinator", page=1)
        self.assertIn("mode=async", seen[0].url)
        self.assertIn("block_id=custom_list_videos_common_videos", seen[0].url)
        self.assertIn("from=02", seen[0].url)

    def test_rule34xxx_uses_pid_and_still_keeps_the_key_out_of_the_evidence_url(self):
        credential = Credential("rule34xxx", {"user_id": "1", "api_key": "secret"})
        seen = []
        result = Rule34XxxConnector(
            credential=credential,
            transport=_transport(body=RULE34XXX_JSON, record=seen)).fetch(
                "lazyprocrastinator", page=3)
        self.assertIn("pid=3", seen[0].url)
        # 脱敏后的 URL 进证据档案，凭据永远不能出现在那里。
        self.assertIn("pid=3", result.request_url)
        self.assertNotIn("secret", result.request_url)
        self.assertNotIn("api_key", result.request_url)

    def test_f95zone_reports_the_end_of_history_instead_of_refetching_page_one(self):
        """`/latest` 只有一页。`page` 被静默丢掉的话，往回翻会重新抓同一页并
        报成一次成功检查——用户看到的就是「点了没反应」。"""
        seen = []
        with self.assertRaises(FollowHistoryEnd):
            F95ZoneConnector(
                transport=_transport(body=F95_HTML, record=seen)).fetch(
                    "50685", page=1)
        self.assertEqual(seen, [], "报到底不该再打一次上游")


class F95ZoneConnectorTests(unittest.TestCase):
    @staticmethod
    def _masked_transport(record):
        def call(request, timeout, max_bytes):
            record.append(request)
            if request.method == "GET":
                return HttpResponse(200, {}, F95_HTML)
            return HttpResponse(200, {}, json.dumps({
                "status": "ok", "msg": "https://gofile.io/d/oOdYTK",
            }).encode())
        return call

    def test_latest_page_yields_replies_not_just_the_opening_post(self):
        seen = []
        result = F95ZoneConnector(
            transport=_transport(body=F95_HTML, record=seen)).fetch("50685")
        self.assertEqual(seen[0].url, "https://f95zone.to/threads/50685/latest")
        self.assertEqual(result.semantics, "release")
        self.assertEqual([c.external_id for c in result.candidates],
                         ["21383374", "21400001"])
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.candidates[0].author, "Jkhomie1198")
        self.assertEqual(result.candidates[0].published_at, "2026-08-21T04:14:09Z")

    def test_thread_title_drops_prefix_labels_and_keeps_the_version(self):
        result = F95ZoneConnector(transport=_transport(body=F95_HTML)).fetch("50685")
        title = result.candidates[0].title
        self.assertTrue(title.startswith("Lazy Procrastinator Collection"))
        self.assertNotIn("F95zone", title)
        self.assertIn("[2026-06-28]", title)

    def test_quoted_links_and_discussion_only_replies_are_skipped(self):
        # 引用块里的链接是被引用那层发的。不剥掉会把追更信号指向错误的楼层。
        result = F95ZoneConnector(transport=_transport(body=F95_HTML)).fetch("50685")
        self.assertNotIn("21394555", [row.external_id for row in result.candidates])
        self.assertEqual(result.skipped, 1)

    def test_reply_with_an_attachment_is_kept_and_gets_a_thumbnail(self):
        result = F95ZoneConnector(transport=_transport(body=F95_HTML)).fetch("50685")
        candidate = result.candidates[1]
        self.assertEqual(candidate.external_id, "21400001")
        self.assertEqual(candidate.thumb_url,
                         "https://attachments.f95zone.to/2026/08/6372325_preview.png")
        self.assertEqual(candidate.extra["attachment_count"], 1)
        self.assertEqual(candidate.extra["attachments"], [
            "https://attachments.f95zone.to/2026/08/6372325_preview.png",
        ])
        self.assertEqual([item["media_kind"] for item in candidate.extra["media_items"]],
                         ["image"])
        self.assertEqual(candidate.extra["media_items"][0]["resource_provider"],
                         "f95zone")

    def test_inline_meme_without_a_file_resource_is_skipped(self):
        body = F95_HTML.replace(
            b'<a href="https://pixeldrain.com/u/preview-pack">Download</a>', b'')
        result = F95ZoneConnector(transport=_transport(body=body)).fetch("50685")
        self.assertEqual([row.external_id for row in result.candidates], ["21383374"])
        self.assertEqual(result.skipped, 2)

    def test_confirmed_discussion_images_keep_the_file_resource(self):
        for name in ("6453006_IMG_2124.jpeg", "6456143_attachment-3.gif"):
            body = F95_HTML.replace(b"6372325_preview.png", name.encode())
            candidate = F95ZoneConnector(transport=_transport(body=body)).fetch("50685").candidates[1]
            self.assertEqual(candidate.media_url, "https://pixeldrain.com/u/preview-pack")
            self.assertIsNone(candidate.thumb_url)
            self.assertEqual(candidate.extra["media_items"], ())

    def test_media_is_flagged_as_needing_a_login_session(self):
        # 发现不需要 cookie，取附件需要。下载动作必须先看这个标志。
        result = F95ZoneConnector(transport=_transport(body=F95_HTML)).fetch("50685")
        self.assertTrue(result.candidates[0].extra["media_needs_credential"])

    def test_cookie_resolves_masked_media_without_leaking_to_the_file_host(self):
        seen = []
        result = F95ZoneConnector(
            transport=self._masked_transport(seen),
            credential=Credential("f95zone", {"cookie": "xf=1"}),
        ).fetch("50685")
        candidate = result.candidates[0]
        self.assertEqual(candidate.media_url, "https://gofile.io/d/oOdYTK")
        self.assertEqual(candidate.extra["links"], ["https://gofile.io/d/oOdYTK"])
        self.assertFalse(candidate.extra["media_needs_credential"])
        self.assertEqual([request.method for request in seen], ["GET", "POST"])
        self.assertEqual(seen[1].url,
                         "https://f95zone.to/masked/gofile.io/50685/abc")
        self.assertEqual(seen[1].headers["Cookie"], "xf=1")
        self.assertEqual(seen[1].body, b"xhr=1&download=1")
        self.assertFalse(any("gofile.io/d/" in request.url for request in seen))

    def test_non_file_links_do_not_turn_a_reply_into_media(self):
        body = F95_HTML.replace(
            b"https://f95zone.to/masked/gofile.io/50685/abc",
            b"https://example.com/creator",
        )
        result = F95ZoneConnector(transport=_transport(body=body)).fetch("50685")
        self.assertEqual([row.external_id for row in result.candidates], ["21400001"])
        self.assertEqual(result.skipped, 2)

    def test_a_valid_page_with_only_discussion_returns_an_empty_fetch(self):
        body = F95_HTML.replace(
            b"https://f95zone.to/masked/gofile.io/50685/abc",
            b"https://example.com/creator",
        ).replace(
            b'<article data-content="post-21400001"',
            b'<article data-content="comment-21400001"',
        )
        result = F95ZoneConnector(transport=_transport(body=body)).fetch("50685")
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.skipped, 2)

    def test_only_absolute_links_count_as_media(self):
        result = F95ZoneConnector(transport=_transport(body=F95_HTML)).fetch("50685")
        self.assertEqual(result.candidates[0].media_url,
                         "https://f95zone.to/masked/gofile.io/50685/abc")
        self.assertEqual(result.candidates[1].media_url,
                         "https://pixeldrain.com/u/preview-pack")

    def test_cookie_is_sent_only_when_supplied(self):
        seen = []
        F95ZoneConnector(transport=_transport(body=F95_HTML, record=seen)).fetch("50685")
        self.assertNotIn("Cookie", seen[0].headers)
        seen.clear()
        F95ZoneConnector(transport=_transport(body=F95_HTML, record=seen),
                         credential=Credential("f95zone", {"cookie": "xf=1"})).fetch("50685")
        self.assertEqual(seen[0].headers["Cookie"], "xf=1")

    def test_thread_ref_must_be_numeric(self):
        connector = F95ZoneConnector(transport=_transport(body=F95_HTML))
        with self.assertRaises(FollowSourceError):
            connector.fetch("lazy-procrastinator-collection.50685")

    def test_thread_index_rejects_unknown_categories(self):
        connector = F95ZoneConnector(transport=_transport(body=b"{}"))
        with self.assertRaises(FollowSourceError):
            connector.thread_index("movies", "lazy")

    def _search_transport(self, record=None, results=F95_SEARCH_RESULTS):
        def call(request, timeout, max_bytes):
            if record is not None:
                record.append(request)
            body = results if request.method == "POST" else F95_SEARCH_FORM
            return HttpResponse(200, {}, body)
        return call

    def test_forum_search_finds_threads_the_latest_index_never_lists(self):
        seen = []
        rows = F95ZoneConnector(
            transport=self._search_transport(record=seen),
            credential=Credential("f95zone", {"cookie": "xf_user=1"}),
        ).search_threads("Ria_neearts")
        # 同一线程的两条帖子只留一条，标题去掉 `Collection`、`Pinup` 这些前缀标签。
        self.assertEqual([row["thread_id"] for row in rows], ["146348"])
        self.assertEqual(rows[0]["title"], "Ria Collection [2026-08-03] [Ria_neearts]")

    def test_forum_search_sends_the_session_token_and_cookie(self):
        seen = []
        F95ZoneConnector(
            transport=self._search_transport(record=seen),
            credential=Credential("f95zone", {"cookie": "xf_user=1"}),
        ).search_threads("Ria_neearts")
        self.assertEqual([request.method for request in seen], ["GET", "POST"])
        body = seen[1].body.decode()
        self.assertIn("_xfToken=1756700000%2Cabc", body)
        self.assertIn("keywords=Ria_neearts", body)
        self.assertIn("c%5Btitle_only%5D=1", body)
        self.assertEqual(seen[1].headers["Cookie"], "xf_user=1")

    def test_forum_search_without_a_cookie_says_so_before_requesting(self):
        seen = []
        connector = F95ZoneConnector(transport=_transport(record=seen))
        with self.assertRaises(CredentialError):
            connector.search_threads("Ria_neearts")
        self.assertEqual(seen, [])

    def test_a_search_page_without_a_token_is_reported_not_parsed_as_empty(self):
        connector = F95ZoneConnector(
            transport=_transport(body=b"<html><body>login</body></html>"),
            credential=Credential("f95zone", {"cookie": "stale"}))
        with self.assertRaises(FollowSourceError):
            connector.search_threads("Ria_neearts")

    def test_thread_index_returns_rows(self):
        body = json.dumps({"status": "ok", "msg": {"data": [
            {"thread_id": 50685, "title": "Lazy Procrastinator Collection",
             "version": "2026-06-28"}]}}).encode()
        rows = F95ZoneConnector(transport=_transport(body=body)).thread_index(
            "animations", "lazy procrastinator")
        self.assertEqual(rows[0]["thread_id"], 50685)

    def _profile_connector(self, record=None):
        return F95ZoneConnector(
            transport=_transport(body=F95_OPENING_POST, record=record),
            credential=Credential("f95zone", {"cookie": "xf_user=1"}))

    def test_the_opening_post_gives_the_authors_own_pages(self):
        """首楼是作者的名片，`/latest` 里没有它。"""
        seen = []
        profile = self._profile_connector(seen).thread_profile("63802")
        self.assertEqual(seen[0].url, "https://f95zone.to/threads/63802/")
        self.assertEqual(profile["title"],
                         "Strauzek Collection [2026-09-04] [Mr_Strauz]")
        self.assertEqual([(row["service"], row["handle"]) for row in profile["links"]],
                         [("patreon", "strauzek"), ("twitter", "strauzek"),
                          ("twitter", "Mr_Strauz"), ("f95zone", "strauzek")])

    def test_two_handles_on_one_service_are_both_kept(self):
        # 同一个人两个 X 账号正是「这两个名字是同一个人」的证据，不能去重掉一个。
        links = self._profile_connector().thread_profile("63802")["links"]
        self.assertEqual([row["handle"] for row in links if row["service"] == "twitter"],
                         ["strauzek", "Mr_Strauz"])

    def test_attachments_and_other_threads_are_not_identities(self):
        links = self._profile_connector().thread_profile("63802")["links"]
        self.assertNotIn("attachments", [row["service"] for row in links])
        self.assertNotIn("threads", [row["handle"] for row in links])

    def test_reading_the_opening_post_without_a_cookie_says_so(self):
        # 游客态站点把站外链接全换成 `/login/`，空清单会被误读成「他没留主页」。
        connector = F95ZoneConnector(transport=_transport(body=F95_OPENING_POST))
        with self.assertRaises(CredentialError):
            connector.thread_profile("63802")


class ProfileLinkTests(unittest.TestCase):
    """哪些链接算作者身份。主机写死，论坛正文里别人贴的地址不算。"""

    def test_a_fanbox_subdomain_is_the_creator_id(self):
        self.assertEqual(
            follow_sources.profile_link_identity("https://lazyprocrast.fanbox.cc/"),
            ("fanbox", "lazyprocrast"))

    def test_a_pixiv_profile_is_the_numeric_user_id(self):
        self.assertEqual(
            follow_sources.profile_link_identity(
                "https://www.pixiv.net/en/users/30917150"),
            ("pixiv", "30917150"))

    def test_shop_and_social_hosts_name_the_account(self):
        for url, identity in (
                ("https://jul3d.itch.io/", ("itchio", "jul3d")),
                ("https://ko-fi.com/artist", ("kofi", "artist")),
                ("https://bsky.app/profile/artist.bsky.social", ("bsky", "artist.bsky.social")),
                ("https://fantia.jp/fanclubs/12345", ("fantia", "12345"))):
            with self.subTest(url=url):
                self.assertEqual(follow_sources.profile_link_identity(url), identity)

    def test_a_function_page_is_not_a_handle(self):
        self.assertIsNone(
            follow_sources.profile_link_identity("https://www.patreon.com/login"))

    def test_an_unlisted_host_is_not_an_identity(self):
        # 正文里贴的图床、网盘和随便什么站都不说明作者是谁。
        self.assertIsNone(
            follow_sources.profile_link_identity("https://gofile.io/d/oOdYTK"))

    def test_multiple_urls_in_one_source_field_are_not_one_identity(self):
        self.assertIsNone(follow_sources.profile_link_identity(
            "https://x.com/Final_EliteDog/status/1 https://patreon.com/another"))

    def test_the_forum_member_page_counts_only_for_its_own_forum(self):
        self.assertEqual(
            follow_sources.profile_link_identity(
                "https://f95zone.to/members/strauzek.1881751/",
                forum_host="f95zone.to"), ("f95zone", "strauzek"))
        self.assertIsNone(follow_sources.profile_link_identity(
            "https://f95zone.to/members/strauzek.1881751/"))


class Rule34XxxConnectorTests(unittest.TestCase):
    def _connector(self, record=None, body=RULE34XXX_JSON, status=200):
        return Rule34XxxConnector(
            transport=_transport(status=status, body=body, record=record),
            credential=Credential("rule34xxx", {"user_id": "42", "api_key": "sekret"}))

    def test_credentials_are_required_and_never_enter_the_recorded_url(self):
        seen = []
        result = self._connector(record=seen).fetch("lazyprocrastinator")
        self.assertIn("api_key=sekret", seen[0].url)
        self.assertNotIn("api_key", result.request_url)
        self.assertNotIn("sekret", result.request_url)

    def test_the_listing_keeps_the_file_dimensions_the_dapi_reports(self):
        """图片墙要在图落地前占好比例，而 dapi 随帖就给宽高——不必再问文件。"""
        by_id = {candidate.external_id: candidate
                 for candidate in self._connector().fetch("lazyprocrastinator").candidates}
        self.assertEqual(by_id["18534395"].extra["width"], 1280)
        self.assertEqual(by_id["18534395"].extra["height"], 720)
        # 没给的帖子一个键也不写：界面按「没有」处理，加载完再回写。
        self.assertNotIn("width", by_id["18534396"].extra)
        self.assertNotIn("height", by_id["18534396"].extra)

    def test_post_page_taxonomy_is_recorded_without_guessing_from_tag_words(self):
        seen = []
        def transport(request, timeout, max_bytes):
            seen.append(request)
            body = RULE34XXX_JSON if request.url.startswith("https://api.rule34.xxx/") \
                else RULE34XXX_DETAIL_HTML
            return HttpResponse(200, {}, body)

        result = Rule34XxxConnector(
            transport=transport, max_items=1,
            credential=Credential("rule34xxx", {"user_id": "42", "api_key": "sekret"}),
        ).fetch("lazyprocrastinator")
        types = result.candidates[0].extra["tag_types"]
        self.assertEqual(types["3d"], "metadata")
        self.assertEqual(types["blender"], "metadata")
        self.assertEqual(types["3d_model"], "metadata")
        self.assertEqual(types["lazyprocrastinator"], "artist")
        self.assertEqual(types["reverse_cowgirl_position"], "general")
        self.assertNotIn("api_key", seen[1].url)
        # 详情页没有 `#stats` 时，dapi 的 `change` 留作占位。
        self.assertEqual(result.candidates[0].published_at, "2026-08-23T00:36:13Z")

    def test_upload_time_comes_from_the_post_page_not_the_last_edit(self):
        """dapi 的 `change` 是最后修改时间：17361475 五月上传、九月改过标签就显示成九月。"""
        detail = RULE34XXX_DETAIL_HTML.replace(b"</ul>", (
            b'</ul><div id="stats"><ul><li>Id: 18534395</li>'
            b"<li>Posted: 2026-05-01 16:32:21<br> by <a>rekin3d</a></li></ul></div>"))

        def transport(request, timeout, max_bytes):
            body = RULE34XXX_JSON if request.url.startswith("https://api.rule34.xxx/") \
                else detail
            return HttpResponse(200, {}, body)

        result = Rule34XxxConnector(
            transport=transport, max_items=1,
            credential=Credential("rule34xxx", {"user_id": "42", "api_key": "sekret"}),
        ).fetch("lazyprocrastinator")
        self.assertEqual(result.candidates[0].published_at, "2026-05-01T16:32:21Z")
        self.assertFalse(result.candidates[0].partial)

    def test_video_duration_comes_from_the_original_file_head(self):
        """接口和帖子页都不给时长，原文件头的 `mvhd` 有；只取头上一段，不拉整片。"""
        video = minimal_mp4(timescale=1000, sample_delta=40, samples=250, keyframe_every=25)
        seen = []

        def transport(request, timeout, max_bytes):
            seen.append((request, max_bytes))
            if request.url.startswith("https://api.rule34.xxx/"):
                return HttpResponse(200, {}, RULE34XXX_JSON)
            if request.url.startswith("https://api-cdn-mp4.rule34.xxx/"):
                return HttpResponse(206, {}, video)
            return HttpResponse(200, {}, RULE34XXX_DETAIL_HTML)

        result = Rule34XxxConnector(
            transport=transport, max_items=1,
            credential=Credential("rule34xxx", {"user_id": "42", "api_key": "sekret"}),
        ).fetch("lazyprocrastinator")
        self.assertEqual(result.candidates[0].duration, 10.0)
        head, budget = next((request, size) for request, size in seen
                            if request.url.endswith(".mp4"))
        self.assertEqual(head.headers["Range"], f"bytes=0-{budget - 1}")
        self.assertLessEqual(budget, 65536)

    def test_a_throttled_post_page_still_brings_the_file_head_duration(self):
        """文件头在另一个主机上，帖子页被挡回来不牵连它：时长照样带上，候选仍是 partial。"""
        video = minimal_mp4(timescale=1000, sample_delta=40, samples=250, keyframe_every=25)

        def transport(request, timeout, max_bytes):
            if request.url.startswith("https://api.rule34.xxx/"):
                return HttpResponse(200, {}, RULE34XXX_JSON)
            if request.url.startswith("https://api-cdn-mp4.rule34.xxx/"):
                return HttpResponse(206, {}, video)
            return HttpResponse(503, {}, b"slow down")

        result = Rule34XxxConnector(
            transport=transport, max_items=1, sleeper=lambda _: None,
            credential=Credential("rule34xxx", {"user_id": "42", "api_key": "sekret"}),
        ).fetch("lazyprocrastinator")
        self.assertEqual(result.candidates[0].duration, 10.0)
        self.assertTrue(result.candidates[0].partial)
        self.assertNotIn("tag_types", result.candidates[0].extra)

    def test_a_throttled_detail_page_is_retried_not_read_as_no_types(self):
        """站方公布的是每 60 秒 60 次，而列表页一页 24 条、每条都要单独打一次详情页。

        一次挡回来就返回 {}，落进库里就是「这条没有类型」——和「这条确实没有
        类型」长得一模一样。实测：回填首轮 400 条里 372 条判成「没有类型」，
        事后逐条直取，全部 200 且 `#tag-sidebar` 正常。
        """
        attempts = []

        def transport(request, timeout, max_bytes):
            if request.url.startswith("https://api.rule34.xxx/"):
                return HttpResponse(200, {}, RULE34XXX_JSON)
            if "page=post" not in request.url:
                return HttpResponse(404, {}, b"")
            attempts.append(request.url)
            if len(attempts) < 3:
                return HttpResponse(503, {}, b"slow down")
            return HttpResponse(200, {}, RULE34XXX_DETAIL_HTML)

        waits = []
        result = Rule34XxxConnector(
            transport=transport, max_items=1, sleeper=waits.append,
            credential=Credential("rule34xxx", {"user_id": "42", "api_key": "sekret"}),
        ).fetch("lazyprocrastinator")
        self.assertEqual(len(attempts), 3, "被挡回来要重试，不能一次就当没有类型")
        # 退避节奏本身也是行为：注入假 sleeper 才能断言它，而且测试不会真的等 5.5 秒。
        self.assertEqual(waits, [1.0, 2.0])
        self.assertEqual(result.candidates[0].extra["tag_types"]["lazyprocrastinator"],
                         "artist")

    def test_a_detail_page_that_stays_unavailable_records_no_types(self):
        """重试完仍取不到就留空，让下一轮再问；不写空字典冒充已补。"""
        def transport(request, timeout, max_bytes):
            if request.url.startswith("https://api.rule34.xxx/"):
                return HttpResponse(200, {}, RULE34XXX_JSON)
            return HttpResponse(503, {}, b"slow down")

        waits = []
        result = Rule34XxxConnector(
            transport=transport, max_items=1, sleeper=waits.append,
            credential=Credential("rule34xxx", {"user_id": "42", "api_key": "sekret"}),
        ).fetch("lazyprocrastinator")
        self.assertNotIn("tag_types", result.candidates[0].extra)
        self.assertTrue(result.candidates[0].partial,
                        "详情没取到就得留着 partial，否则落库会把上一轮的分类抹掉")
        self.assertEqual(waits, [1.0, 2.0, 4.0, 8.0], "退避有界且不会叠乘")

    def test_autocomplete_reports_the_real_tag_spelling_without_credentials(self):
        # 站上写作 `ria-neearts`，手边的手柄是 `Ria_neearts`；补全是唯一能把两者
        # 对上的官方接口，而且不需要 user_id/api_key。
        body = json.dumps([{"label": "ria-neearts (248)", "value": "ria-neearts"},
                           {"label": "riahri (156)", "value": "riahri"},
                           {"label": "no-count", "value": "no-count"}]).encode()
        seen = []
        rows = Rule34XxxConnector(
            transport=_transport(body=body, record=seen)).autocomplete("Ria-neearts")
        self.assertEqual(rows, (("ria-neearts", 248), ("riahri", 156), ("no-count", 0)))
        self.assertIn("q=Ria-neearts", seen[0].url)
        self.assertNotIn("api_key", seen[0].url)

    def test_autocomplete_reports_a_changed_payload_shape(self):
        connector = Rule34XxxConnector(transport=_transport(body=b'{"tags": []}'))
        with self.assertRaises(FollowSourceError):
            connector.autocomplete("ria")

    def test_missing_credential_fails_before_the_request(self):
        seen = []
        connector = Rule34XxxConnector(transport=_transport(record=seen))
        with self.assertRaises(CredentialError):
            connector.fetch("lazyprocrastinator")
        self.assertEqual(seen, [])

    def test_a_search_asks_dapi_once_and_skips_the_subscription_machinery(self):
        """一段标签表达式换一次请求，整串原样进 `tags`。

        题材圆标在本库那几张里挑不出脸时走这一趟：要的是「这个题材最热的几张」，
        `sort:score` 这类元标签和主标签共用同一个参数。它不落库，也就不需要订阅那
        条路上的条件请求和逐条打详情页——那些在这里全是白花的请求。
        """
        seen = []
        candidates = self._connector(record=seen).search("stellar_blade 3d sort:score",
                                                         limit=3)
        self.assertEqual(len(seen), 1, "一次搜索就是一次请求，不再逐条打详情页")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(seen[0].url).query)
        self.assertEqual(query["tags"], ["stellar_blade 3d sort:score"])
        self.assertEqual(query["limit"], ["3"])
        self.assertNotIn("pid", query, "一次性的搜索只要第一页")
        self.assertEqual(len(candidates), 3)
        self.assertEqual(candidates[0].thumb_url,
                         "https://api-cdn.rule34.xxx/images/1232/3df1.jpg",
                         "圆标要的是 sample 那一层，不是 250px 的 preview")

    def test_a_search_without_credentials_fails_before_the_request(self):
        seen = []
        connector = Rule34XxxConnector(transport=_transport(record=seen))
        with self.assertRaises(CredentialError):
            connector.search("stellar_blade sort:score")
        self.assertEqual(seen, [])

    def test_an_empty_search_expression_never_reaches_the_site(self):
        """标签是空的就不出网：一个没有标签的 dapi 查询回的是整站最新，不是这个题材。"""
        seen = []
        self.assertEqual(self._connector(record=seen).search("   "), ())
        self.assertEqual(seen, [])

    def test_an_empty_success_response_means_the_tag_has_no_posts(self):
        # rule34.xxx 的零命中响应是 HTTP 200 + 空正文，不是 JSON `[]`。
        # 不能把普通的「搜不到」显示成红色 JSON 错误。
        result = self._connector(body=b" \r\n").fetch("not-a-real-tag")
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.raw_body, b" \r\n")

    def test_an_empty_backfill_response_means_history_is_exhausted(self):
        with self.assertRaises(FollowHistoryEnd):
            self._connector(body=b" \r\n").fetch("lazyprocrastinator", page=4)

    def test_the_declared_origin_becomes_a_cross_site_group_key(self):
        # 同一个 fanbox 帖在 source 里有两种写法，必须归一到同一个键——而那串数字
        # 正是 kemono 上同一帖子的 post id，跨站重复因此能精确命中。
        candidates = self._connector().fetch("lazyprocrastinator").candidates
        self.assertEqual(candidates[0].group_hint, "fanbox:12304831")
        self.assertEqual(candidates[1].group_hint, "fanbox:12304831")

    def test_posts_without_an_origin_fall_back_to_the_booru_parent_chain(self):
        candidates = self._connector().fetch("lazyprocrastinator").candidates
        # 父帖用自己的 id，子帖用 parent_id，拼出来是同一个键。
        self.assertEqual(candidates[2].group_hint, "rule34xxx:post:18534397")
        self.assertEqual(candidates[3].group_hint, "rule34xxx:post:18534397")

    def test_a_hash_filename_is_not_used_as_a_title(self):
        # 实测 15/15 的 image 都是哈希。拿它当标题既不可读，又会让每条帖子各自成组。
        first = self._connector().fetch("lazyprocrastinator").candidates[0]
        self.assertNotIn("3df1cbc6", first.title)
        self.assertEqual(first.extra["title_from"], "tags")
        self.assertFalse(first.title_is_name)
        self.assertIn("fiona", first.title)
        self.assertEqual(first.published_at, "2026-08-23T00:36:13Z")

    def test_sample_image_is_preferred_over_the_small_preview(self):
        first = self._connector().fetch("lazyprocrastinator").candidates[0]
        self.assertEqual(
            first.thumb_url, "https://api-cdn.rule34.xxx/images/1232/3df1.jpg")
        self.assertEqual(
            first.extra["preview_url"],
            "https://api-cdn.rule34.xxx/thumbnails/1232/t_3df1.jpg")

    def test_video_card_keeps_the_source_thumbnail_and_image_keeps_its_extension(self):
        from types import SimpleNamespace
        thumb = 'https://api-cdn.rule34.xxx/thumbnails/1232/thumbnail_' + 'a' * 32 + '.jpg'
        video = SimpleNamespace(thumb_url=thumb, media_url='https://api-cdn.rule34.xxx/images/1232/hash.mp4', metadata={'media_kind': 'video'})
        self.assertEqual(Rule34XxxConnector.display_thumb_url(video), thumb)
        image = SimpleNamespace(thumb_url=thumb, media_url='https://api-cdn.rule34.xxx/images/1232/hash.png', metadata={'media_kind': 'image'})
        self.assertEqual(Rule34XxxConnector.display_thumb_url(image), image.media_url)

    def test_a_readable_filename_is_still_preferred_and_counts_as_a_name(self):
        last = self._connector().fetch("lazyprocrastinator").candidates[3]
        self.assertEqual(last.title, "fiona paizuri nude")
        self.assertEqual(last.extra["title_from"], "image")
        self.assertTrue(last.title_is_name)

    def test_the_tag_label_drops_the_subject_and_media_words(self):
        first = self._connector().fetch("lazyprocrastinator").candidates[0]
        for noise in ("lazyprocrastinator", "video", "sound"):
            self.assertNotIn(noise, first.title)

    def test_dapi_tags_and_source_urls_are_unescaped(self):
        """dapi 返回的标签和出处 URL 是 HTML 转义形态，入库前必须反转义。

        线上实测（2026-08-29）：rule34.xxx 的 JSON 里标签写作 `miqo&#039;te`、
        出处 URL 里 `&amp;` 代替 `&`。实体原样进 metadata，页面转义后用户看到
        的就是 `&#039;` 字面量（生产账本 131 行中招），同一个标签还会和反转义
        后的写法分裂成两个身份。归组键只取 path，query 里的实体不影响分组。
        """
        body = json.dumps([{
            "id": 18534501, "image": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6.jpg",
            "parent_id": 0,
            "tags": "miqo&#039;te y&#039;shtola final_fantasy",
            "change": 1787445400,
            "file_url": "https://api-cdn.rule34.xxx/images/1232/miqo.jpg",
            "source": "https://porn3dx.com/post/42957/aranea?list_type=key&amp;sort=new",
        }]).encode()
        candidate = self._connector(body=body).fetch("final_fantasy").candidates[0]
        self.assertEqual(candidate.extra["tags"], "miqo'te y'shtola final_fantasy")
        self.assertNotIn("&#039;", candidate.title)
        self.assertEqual(
            candidate.extra["source"],
            "https://porn3dx.com/post/42957/aranea?list_type=key&sort=new")

    def test_rate_limit_body_is_reported_as_rate_limit_not_bad_json(self):
        """限流要报成限流：用户需要知道「等一会儿再试」而不是「响应坏了」。

        rule34.xxx 限流时回 HTTP 200 + 文本正文（实测句式 "You currently have
        a limit of 60 requests every 60 second(s)"）。按「不是合法 JSON」上报，
        检查失败的红色明细里就只剩一句没头没尾的原因。
        """
        body = (b"<!doctype html><html><body>You currently have a limit of "
                b"60 requests every 60 second(s). Please try again later."
                b"</body></html>")
        with self.assertRaises(FollowSourceError) as caught:
            self._connector(body=body).fetch("lazyprocrastinator")
        message = str(caught.exception)
        self.assertIn("频率限制", message)
        self.assertIn("60", message)
        self.assertNotIn("JSON", message)
        # 检查循环据此让整站冷却，等的就是正文里那个窗口。
        self.assertIsInstance(caught.exception, FollowSourceRateLimited)
        self.assertEqual(caught.exception.retry_after, 60.0)

    def test_http_429_reports_throttling(self):
        connector = self._connector(body=b"{}", status=429)
        with self.assertRaises(FollowSourceRateLimited) as caught:
            connector.fetch("lazyprocrastinator")
        self.assertIn("429", str(caught.exception))
        self.assertIn("频繁", str(caught.exception))


    def test_authentication_rejection_is_reported_as_a_credential_error(self):
        connector = self._connector(
            body=b'"Missing authentication. Go to api.rule34.xxx for more information"')
        with self.assertRaises(CredentialError):
            connector.fetch("lazyprocrastinator")


class SimpCityConnectorTests(unittest.TestCase):
    COOKIE = Credential("simpcity", {"cookie": "yMziCv8BrCZz1o7_user=u; yMziCv8BrCZz1o7_session=s"})

    @staticmethod
    def _paged_transport(record, first=SIMPCITY_FIRST_HTML, last=SIMPCITY_LAST_HTML):
        def call(request, timeout, max_bytes):
            record.append(request)
            body = last if "/page-" in request.url else first
            return HttpResponse(200, {"ETag": '"p7"'}, body)
        return call

    def test_thread_links_resolve_to_the_thread_id(self):
        parsed = parse_source_url("https://simpcity.cr/threads/sample-creator.4242/page-3")
        self.assertEqual((parsed.provider, parsed.ref), ("simpcity", "4242"))
        self.assertEqual(parsed.url, "https://simpcity.cr/threads/4242/")
        self.assertEqual(parsed.label, "sample creator")
        self.assertEqual(parsed.semantics, "release")
        with self.assertRaises(FollowSourceError):
            parse_source_url("https://simpcity.cr/forums/onlyfans.12/")

    def test_without_a_cookie_nothing_is_requested(self):
        # 站点不让游客读帖：缺 cookie 就是「未授权」，不该先打一次 403 再说。
        seen = []
        with self.assertRaises(CredentialError) as caught:
            SimpCityConnector(transport=_transport(record=seen)).fetch("4242")
        self.assertIn("cookie", str(caught.exception))
        self.assertEqual(seen, [])

    def test_the_last_page_is_read_after_the_first_page_reveals_it(self):
        seen = []
        result = SimpCityConnector(
            credential=self.COOKIE, transport=self._paged_transport(seen),
        ).fetch("4242", etag='"old"')
        self.assertEqual([r.url for r in seen], [
            "https://simpcity.cr/threads/4242/",
            "https://simpcity.cr/threads/4242/page-7",
        ])
        # 两个请求都带 cookie；条件请求头只在末页上——第一页没变不代表线程没更新。
        self.assertTrue(all(r.headers["Cookie"] == self.COOKIE.values["cookie"] for r in seen))
        self.assertNotIn("If-None-Match", seen[0].headers)
        self.assertEqual(seen[1].headers["If-None-Match"], '"old"')
        self.assertEqual(result.request_url, "https://simpcity.cr/threads/4242/page-7")
        self.assertEqual(result.semantics, "release")
        self.assertEqual(result.etag, '"p7"')
        self.assertEqual([c.external_id for c in result.candidates], ["700", "702"])
        self.assertEqual(result.skipped, 1)

    def test_a_single_page_thread_costs_one_request(self):
        seen = []
        head, rest = SIMPCITY_LAST_HTML.split(b"<nav", 1)
        single = head + rest.split(b"</nav>", 1)[1]
        result = SimpCityConnector(
            credential=self.COOKIE, transport=_transport(body=single, record=seen),
        ).fetch("4242")
        self.assertEqual([r.url for r in seen], ["https://simpcity.cr/threads/4242/"])
        self.assertEqual(len(result.candidates), 2)

    def test_images_links_and_attachments_are_read_from_the_post_body(self):
        result = SimpCityConnector(
            credential=self.COOKIE, transport=self._paged_transport([]),
        ).fetch("4242")
        first, second = result.candidates
        self.assertEqual(first.title, "Sample Creator")
        self.assertEqual(first.url, "https://simpcity.cr/threads/4242/post-700")
        self.assertEqual(first.author, "uploader1")
        self.assertEqual(first.published_at, "2026-09-07T10:00:00Z")
        # 有图的楼层以图为媒体，网盘链接只做资源按钮。
        self.assertEqual(first.media_url, "https://cdn.imgpage.test/abc.jpg")
        # 站点把外链套在 `/redirect/?to=<base64url>` 里，网盘链接要还原之后才认得出。
        # 预览卡（unfurl）里的链接是楼主贴的，要留下；卡片上站点生成的摘要文字不是。
        self.assertEqual(first.extra["links"],
                         ["https://gofile.io/d/abc123", "https://pixeldrain.com/u/zF3PqTJF",
                          "https://mega.nz/folder/xyz"])
        self.assertNotIn("Unfurl snippet text", first.summary)
        self.assertEqual(first.thumb_url, "https://cdn.imgpage.test/abc.md.jpg")
        self.assertEqual(first.extra["images"],
                         ["https://cdn.imgpage.test/abc.jpg", "https://cdn.imgpage.test/def.jpg"])
        self.assertEqual((first.extra["image_count"], first.extra["link_count"]), (2, 3))
        self.assertEqual(first.extra["page"], 7)
        # 表情图不是内容；被引用的网盘链接属于被引用的楼层，不算 replier 发的。
        self.assertNotIn("cdn.jsdelivr.net", str(first.extra))
        self.assertEqual(second.external_id, "702")
        self.assertIsNone(second.thumb_url)
        self.assertEqual(second.media_url, None)
        self.assertEqual(second.extra["attachments"],
                         ["https://simpcity.cr/attachments/clip-mp4.9001/"])
        self.assertEqual(second.extra["embed_count"], 1)

    def test_redirect_links_are_decoded_only_when_they_hold_a_real_url(self):
        decode = SimpCityConnector._external_href
        self.assertEqual(decode("/redirect/?to=aHR0cHM6Ly9waXhlbGRyYWluLmNvbS91L3pGM1BxVEpG&e=1&m=b64"),
                         "https://pixeldrain.com/u/zF3PqTJF")
        self.assertEqual(decode("https://simpcity.cr/redirect/?to=aHR0cHM6Ly9nb2ZpbGUuaW8vZC9hYmMxMjM"),
                         "https://gofile.io/d/abc123")
        # 解出来不是地址、缺 to、别的站的 /redirect/、普通链接：一律原样返回。
        for href in ("/redirect/?to=bm90IGEgdXJs&m=b64", "/redirect/?e=1",
                     "https://other.test/redirect/?to=aHR0cHM6Ly9nb2ZpbGUuaW8vZC9hYmMxMjM",
                     "https://gofile.io/d/abc123", "/redirect/?to=%%%"):
            self.assertEqual(decode(href), href)

    def test_history_pages_count_back_from_the_last_page(self):
        seen = []
        connector = SimpCityConnector(credential=self.COOKIE,
                                      transport=self._paged_transport(seen))
        connector.fetch("4242", page=1, etag='"old"')
        self.assertEqual(seen[1].url, "https://simpcity.cr/threads/4242/page-6")
        self.assertNotIn("If-None-Match", seen[1].headers)
        seen.clear()
        # 翻回到第 1 页时复用刚读过的第一页，不再多打一次。
        result = connector.fetch("4242", page=6)
        self.assertEqual(len(seen), 1)
        self.assertEqual([c.external_id for c in result.candidates], ["100"])
        with self.assertRaises(FollowHistoryEnd):
            connector.fetch("4242", page=7)

    def test_a_forbidden_page_blames_the_cookie_not_a_bot_check(self):
        with self.assertRaises(FollowSourceError) as caught:
            SimpCityConnector(credential=self.COOKIE,
                              transport=_transport(status=403, body=b"Forbidden")).fetch("4242")
        self.assertIn("cookie", str(caught.exception))
        self.assertNotIn("机器人", str(caught.exception))

    def test_a_guest_page_means_the_cookie_was_not_recognised(self):
        with self.assertRaises(FollowSourceError) as caught:
            SimpCityConnector(credential=self.COOKIE,
                              transport=_transport(body=SIMPCITY_GUEST_HTML)).fetch("4242")
        self.assertIn("游客态", str(caught.exception))

    @staticmethod
    def _search_transport(record):
        def call(request, timeout, max_bytes):
            record.append(request)
            body = SIMPCITY_SEARCH_RESULTS if request.method == "POST" else SIMPCITY_SEARCH_FORM
            return HttpResponse(200, {}, body)
        return call

    def test_forum_search_returns_each_thread_once_with_its_forum_label(self):
        rows = SimpCityConnector(credential=self.COOKIE,
                                 transport=self._search_transport([])).search_threads("solazola")
        # 同一线程的两条帖子只留一条；标题去掉版块标签，标签另给一列让人分辨资源帖和讨论帖。
        self.assertEqual([row["thread_id"] for row in rows], ["392510", "17401"])
        self.assertEqual([row["title"] for row in rows],
                         ["solazola discussion", "Solazola / baby_sue"])
        self.assertEqual([row["labels"] for row in rows], [["Simp Chat"], ["OnlyFans"]])

    def test_forum_search_sends_the_session_token_and_cookie_to_simpcity_only(self):
        seen = []
        SimpCityConnector(credential=self.COOKIE,
                          transport=self._search_transport(seen)).search_threads("solazola")
        self.assertEqual([(request.method, request.url) for request in seen],
                         [("GET", "https://simpcity.cr/search/"),
                          ("POST", "https://simpcity.cr/search/search")])
        body = seen[1].body.decode()
        self.assertIn("_xfToken=1757300000%2Csc", body)
        self.assertIn("keywords=solazola", body)
        self.assertIn("c%5Btitle_only%5D=1", body)
        self.assertTrue(all(request.headers["Cookie"] == self.COOKIE.values["cookie"]
                            for request in seen))

    def test_forum_search_without_a_cookie_asks_for_it_before_requesting(self):
        seen = []
        with self.assertRaises(CredentialError) as caught:
            SimpCityConnector(transport=_transport(record=seen)).search_threads("solazola")
        self.assertIn("cookie", str(caught.exception))
        self.assertEqual(seen, [])

    def test_a_forbidden_search_form_blames_the_cookie(self):
        with self.assertRaises(FollowSourceError) as caught:
            SimpCityConnector(credential=self.COOKIE,
                              transport=_transport(status=403, body=b"Forbidden")
                              ).search_threads("solazola")
        self.assertIn("cookie", str(caught.exception))


class OriginGroupKeyTests(unittest.TestCase):
    def test_the_two_fanbox_url_shapes_normalize_together(self):
        self.assertEqual(origin_group_key("https://lazyprocrast.fanbox.cc/posts/12304831"),
                         origin_group_key("https://www.fanbox.cc/@lazyprocrast/posts/12304831"))

    def test_a_source_without_a_scheme_still_normalizes(self):
        # rule34.xxx 实测：同一条推文，一帖写 `https://x.com/…`，另一帖只写 `x.com/…`。
        self.assertEqual(origin_group_key("x.com/vileclipse/status/2101310844021928089"),
                         "x:2101310844021928089")
        self.assertEqual(origin_group_key("lazyprocrast.fanbox.cc/posts/12304831"),
                         origin_group_key("https://www.fanbox.cc/@lazyprocrast/posts/12304831"))

    def test_known_platforms_get_their_own_prefix(self):
        self.assertEqual(origin_group_key("https://x.com/a/status/20861277667730761"),
                         "x:20861277667730761")
        self.assertEqual(origin_group_key("https://twitter.com/a/status/12345678"),
                         "x:12345678")
        self.assertEqual(origin_group_key("https://www.patreon.com/posts/98765432"),
                         "patreon:98765432")

    def test_unknown_hosts_and_junk_yield_nothing(self):
        for value in ("https://rule34video.com/video/4542721/", "https://fanbox.cc/",
                      "not a url", "", None, 12345):
            self.assertIsNone(origin_group_key(value))


class ParseSourceUrlTests(unittest.TestCase):
    """粘进来的链接怎么认。纯解析，不联网。"""

    def test_each_supported_host_maps_to_its_provider_and_ref(self):
        cases = {
            "https://kemono.cr/fanbox/user/30917150": ("kemono", "fanbox/30917150"),
            "https://coomer.st/onlyfans/user/abc": ("coomer", "onlyfans/abc"),
            "https://pawchive.pw/patreon/user/123": ("pawchive", "patreon/123"),
            "https://rule34video.com/models/lazyprocrastinator/":
                ("rule34video", "lazyprocrastinator"),
            "https://rule34.xxx/index.php?page=post&s=list&tags=lazyprocrastinator":
                ("rule34xxx", "lazyprocrastinator"),
            "https://rule34.paheal.net/post/view/7428820#search=InitialA":
                ("rule34paheal", "initiala"),
            "https://f95zone.to/threads/lazy-collection.50685/": ("f95zone", "50685"),
            "https://ffxivinitiala.fanbox.cc/posts/12489354":
                ("fanbox", "ffxivinitiala"),
            "https://www.fanbox.cc/@ffxivinitiala/posts/12489354":
                ("fanbox", "ffxivinitiala"),
            "https://subscribestar.adult/initiala":
                ("subscribestar", "subscribestar.adult/initiala"),
            "https://www.patreon.com/cw/somnivagrious":
                ("patreon", "somnivagrious"),
            "https://www.patreon.com/user?u=12345": ("patreon", "user/12345"),
        }
        for url, expected in cases.items():
            parsed = parse_source_url(url)
            self.assertEqual((parsed.provider, parsed.ref), expected, url)

    def test_a_deep_link_is_narrowed_to_the_creator(self):
        parsed = parse_source_url("https://kemono.cr/fanbox/user/30917150/post/11406814")
        self.assertEqual(parsed.ref, "fanbox/30917150")
        self.assertEqual(parsed.url, "https://kemono.cr/fanbox/user/30917150")

    def test_rule34_tags_use_one_case_insensitive_identity(self):
        upper = parse_source_url(
            "https://rule34.xxx/index.php?page=post&s=list&tags=LazyProcrastinator")
        lower = parse_source_url(
            "https://rule34.xxx/index.php?page=post&s=list&tags=lazyprocrastinator")
        self.assertEqual(upper.ref, "lazyprocrastinator")
        self.assertEqual(upper, lower)

    def test_threads_get_release_semantics_and_others_get_work(self):
        self.assertEqual(parse_source_url("https://f95zone.to/threads/x.1/").semantics,
                         "release")
        self.assertEqual(
            parse_source_url("https://rule34video.com/models/abc/").semantics, "work")
        self.assertEqual(parse_source_url("https://creator.fanbox.cc/").semantics,
                         "work")

    def test_a_bare_host_is_accepted_and_www_is_ignored(self):
        self.assertEqual(parse_source_url("rule34video.com/models/abc/").provider,
                         "rule34video")
        self.assertEqual(parse_source_url("https://www.rule34video.com/models/abc/").ref,
                         "abc")

    def test_the_thread_slug_becomes_a_readable_label(self):
        parsed = parse_source_url(
            "https://f95zone.to/threads/lazy-procrastinator-collection.50685/")
        self.assertEqual(parsed.label, "lazy procrastinator collection")

    def test_the_label_stops_at_the_release_date_not_at_the_end_of_the_slug(self):
        # f95 的 slug 惯例是 `<作品名>-<发布日期>-<作者手柄>`，全取会得到一长串元数据。
        parsed = parse_source_url(
            "https://f95zone.to/threads/"
            "lazy-procrastinator-collection-2026-06-28-lazyprocrastinator-lazyprocrast.50685/")
        self.assertEqual(parsed.label, "lazy procrastinator collection")

    def test_the_right_host_with_the_wrong_path_says_what_shape_is_expected(self):
        for url, hint in (
            ("https://kemono.cr/posts", "fanbox/user"),
            ("https://rule34video.com/latest-updates/", "models"),
            ("https://rule34.xxx/index.php?page=post&s=view&id=1", "tags="),
            ("https://rule34.paheal.net/post/view/7428820", "搜索标签"),
            ("https://f95zone.to/latest", "threads"),
            ("https://www.fanbox.cc/", "创作者主页"),
            ("https://subscribestar.adult/posts/1", "创作者主页"),
            ("https://www.patreon.com/posts/123", "创作者主页"),
        ):
            with self.assertRaises(FollowSourceError) as caught:
                parse_source_url(url)
            self.assertIn(hint, str(caught.exception), url)

    def test_credentials_and_non_http_urls_are_refused(self):
        for url in ("https://user:pw@kemono.cr/fanbox/user/1",
                    "ftp://kemono.cr/fanbox/user/1", ""):
            with self.assertRaises(FollowSourceError):
                parse_source_url(url)


class BuildConnectorTests(unittest.TestCase):
    def test_registry_maps_the_three_kemono_hosts_to_distinct_providers(self):
        for provider in ("kemono", "coomer", "pawchive"):
            self.assertEqual(build_connector(provider).provider, provider)

    def test_the_registry_is_derived_from_what_each_class_declares(self):
        """`CONNECTORS` 由各类自己的声明推导，不手写。手写的话 kemono 系三站要在
        映射里把同一个类写三遍，和 `KemonoConnector.HOSTS` 成了同一件事的两份清单。"""
        self.assertEqual(
            follow_sources.CONNECTORS,
            {key: factory for factory in follow_sources._CONNECTOR_CLASSES
             for key in factory.provider_keys()})
        self.assertEqual(set(KemonoConnector.provider_keys()),
                         set(KemonoConnector.HOSTS))
        self.assertEqual(FanboxConnector.provider_keys(), ("fanbox",))

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(FollowSourceError):
            build_connector("nyaa")

    def test_official_channel_connectors_are_registered(self):
        self.assertIsInstance(build_connector("fanbox"), FanboxConnector)
        self.assertIsInstance(build_connector("patreon"), PatreonConnector)
        self.assertIsInstance(build_connector("subscribestar"), SubscribeStarConnector)
        self.assertIsInstance(build_connector("rule34paheal"), Rule34PahealConnector)


class ParseUrlDelegationTests(unittest.TestCase):
    """链接解析的分派边界：主机归 `follow_providers`，形状归各自的连接器。"""

    def test_the_dispatcher_hands_off_to_the_connector_that_owns_the_host(self):
        with patch.object(Rule34VideoConnector, "parse_url") as parse:
            parse.return_value = "sentinel"
            self.assertEqual(
                parse_source_url("https://www.rule34video.com/models/abc/"),
                "sentinel")
        provider, parsed, host = parse.call_args.args
        self.assertEqual((provider, host), ("rule34video", "rule34video.com"))
        self.assertEqual(parsed.path, "/models/abc/")

    def test_every_registered_connector_can_parse_its_own_links(self):
        """漏写 `parse_url` 只会让那个站的链接报「暂不支持」，界面上看不出是漏写。"""
        for provider, factory in follow_sources.CONNECTORS.items():
            with self.subTest(provider=provider):
                self.assertIsNot(factory.parse_url.__func__,
                                 follow_sources._BaseConnector.parse_url.__func__,
                                 f"{provider} 没有自己的 parse_url")

    def test_an_unrecognised_host_lists_the_hosts_that_are_registered(self):
        """提示词从登记表来；新增站点不必再回来改这句文案。"""
        with self.assertRaises(FollowSourceError) as caught:
            parse_source_url("https://nyaa.si/view/1")
        message = str(caught.exception)
        for host in follow_sources._URL_HOSTS:
            self.assertIn(host, message)


class RelativeDateTests(unittest.TestCase):
    def test_units_are_converted_against_an_injected_reference(self):
        now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(_iso_from_relative("1 week ago", now=now), "2026-08-18T12:00:00Z")
        self.assertEqual(_iso_from_relative("2 days", now=now), "2026-08-23T12:00:00Z")
        self.assertIsNone(_iso_from_relative("just now", now=now))
        self.assertIsNone(_iso_from_relative(None, now=now))


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = CredentialStore(self.root)
        (self.root / "follow").mkdir(parents=True)
        self.addCleanup(self.temporary.cleanup)

    def _write(self, provider, payload, mode=0o600):
        path = self.root / "follow" / f"{provider}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(path, mode)
        return path

    def test_missing_credential_is_none_not_an_error(self):
        self.assertIsNone(self.store.load("rule34xxx"))
        described = self.store.describe("rule34xxx")
        self.assertFalse(described["present"])

    def test_describe_reports_field_names_but_never_values(self):
        self._write("rule34xxx", {"user_id": "42", "api_key": "sekret"})
        described = self.store.describe("rule34xxx")
        self.assertEqual(described["fields"], ["api_key", "user_id"])
        self.assertNotIn("sekret", json.dumps(described))
        self.assertIn(described["world_readable"], (False, None))

    @unittest.skipIf(os.name == "nt", "NTFS 走 ACL，st_mode 的组/其他读位恒为真")
    def test_group_or_world_readable_permissions_are_reported(self):
        self._write("f95zone", {"cookie": "xf=1"}, mode=0o644)
        self.assertTrue(self.store.describe("f95zone")["world_readable"])

    @unittest.skipUnless(os.name == "nt", "只在 Windows 上成立")
    def test_windows_reports_unknown_rather_than_a_meaningless_permission(self):
        self._write("f95zone", {"cookie": "xf=1"})
        self.assertIsNone(self.store.describe("f95zone")["world_readable"])

    def test_require_lists_every_missing_field(self):
        self._write("rule34xxx", {"user_id": "42"})
        credential = self.store.load("rule34xxx")
        with self.assertRaises(CredentialError) as caught:
            credential.require("user_id", "api_key")
        self.assertIn("api_key", str(caught.exception))

    def test_unparsable_file_is_an_error(self):
        (self.root / "follow" / "kemono.json").write_text("{oops", encoding="utf-8")
        with self.assertRaises(CredentialError):
            self.store.load("kemono")

    def test_provider_name_cannot_escape_the_secrets_directory(self):
        for bad in ("../ledger", "a/b", ".hidden"):
            with self.assertRaises(CredentialError):
                self.store.path_for(bad)

class EnrichPhaseTests(unittest.TestCase):
    """第二阶段的共用机制：额度、跳过集合与「补齐判据」的登记。

    详情页是每条一次请求，列表页是一整页一次。所以第二阶段必须有额度，而且
    补齐过的条目不再问——否则一个作者的来源每次检查都要按条目数付请求。
    """

    def _rule34xxx(self, seen, **kwargs):
        def transport(request, timeout, max_bytes):
            seen.append(request.url)
            if request.url.startswith("https://api.rule34.xxx/index.php"):
                return HttpResponse(200, {}, RULE34XXX_JSON)
            return HttpResponse(200, {}, RULE34XXX_DETAIL_HTML)

        return Rule34XxxConnector(
            transport=transport, max_items=4,
            credential=Credential("rule34xxx", {"user_id": "42", "api_key": "sekret"}),
            **kwargs)

    def test_the_budget_bounds_how_many_details_one_check_can_cost(self):
        seen = []
        result = self._rule34xxx(seen, enrich_budget=2).fetch("lazyprocrastinator")
        self.assertEqual(len(result.candidates), 4)
        self.assertEqual(result.probed, 2)
        details = [url for url in seen if "s=view" in url]
        self.assertEqual(len(details), 2, "额度是请求数的上限，不是建议值")
        self.assertEqual([item.partial for item in result.candidates],
                         [False, False, True, True],
                         "额度用完之后的条目仍然入库，只是标着 partial")

    def test_a_zero_budget_means_the_list_phase_alone(self):
        seen = []
        result = self._rule34xxx(seen, enrich_budget=0).fetch("lazyprocrastinator")
        self.assertEqual(seen, [url for url in seen if "s=view" not in url])
        self.assertEqual(result.probed, 0)
        self.assertTrue(all(item.partial for item in result.candidates))

    def test_each_enriched_item_is_published_before_the_next_detail_request(self):
        seen, ready = [], []
        connector = self._rule34xxx(seen, enrich_budget=2)

        def publish(candidate):
            ready.append(candidate)
            enriched = sum(not item.partial for item in ready)
            self.assertEqual(sum('s=view' in url for url in seen), enriched)

        connector.candidate_ready = publish
        result = connector.fetch('lazyprocrastinator')
        self.assertEqual([item.external_id for item in ready[:4]],
                         [item.external_id for item in result.candidates])
        self.assertEqual([item.partial for item in ready], [True, True, True, True, False, False])

    def test_the_skip_set_is_spent_on_the_condition_not_on_the_budget(self):
        """跳过的条目不占额度：额度是为「还没补齐的那几条」留的。"""
        seen = []
        result = self._rule34xxx(
            seen, enrich_budget=2, enrich_skip={"18534395", "18534396"},
        ).fetch("lazyprocrastinator")
        details = sorted(url.rsplit("id=", 1)[1] for url in seen if "s=view" in url)
        self.assertEqual(details, ["18534397", "18534398"])
        self.assertEqual([item.partial for item in result.candidates],
                         [True, True, False, False])

    def test_every_declared_mark_has_a_ledger_predicate(self):
        """连接器声明「补齐之后哪一处会有值」，判据的 SQL 在 store 里。

        两处必须对得上：声明一个 store 不认识的键，第二阶段会在 `plan_check` 里
        直接抛错，而那是每次检查都会走的路径。
        """
        from peach.follow_store import _ENRICHED_PREDICATES
        for provider, factory in sorted(follow_sources.CONNECTORS.items()):
            mark = factory.ENRICHED_MARK
            probes = factory.DEFAULT_ENRICH_BUDGET or getattr(factory, "DEFAULT_MAX_PROBES", 0)
            with self.subTest(provider=provider):
                self.assertEqual(bool(mark), bool(probes),
                                 "打详情页就得有判据，有判据就得打详情页")
                if mark:
                    self.assertIn(mark, _ENRICHED_PREDICATES)

    def test_the_mark_of_an_unknown_provider_is_empty_not_an_error(self):
        self.assertEqual(follow_sources.enrichment_mark("nope"), "")
        self.assertEqual(follow_sources.enrichment_mark(""), "")
        self.assertEqual(follow_sources.enrichment_mark("kemono"), "kept",
                         "kemono 的探测是收录判定，库里有的帖子就是判过保留的")
        self.assertEqual(follow_sources.enrichment_mark("rule34xxx"), "tag_types_duration")

    def test_rule34xxx_is_not_marked_by_a_time_the_list_already_gave(self):
        """rule34xxx 的上传时间来自列表的 `change`，第一次落库就有值。

        拿 `published_at` 当它的补齐判据，等于宣布「所有条目都已补齐」，上一轮被
        限流挡掉的分类就永远补不回来了。
        """
        self.assertNotEqual(Rule34XxxConnector.ENRICHED_MARK, "published_at")
        listed = Rule34XxxConnector(
            transport=_transport(body=RULE34XXX_JSON), enrich_budget=0,
            credential=Credential("rule34xxx", {"user_id": "42", "api_key": "sekret"}),
        ).fetch("lazyprocrastinator").candidates
        self.assertTrue(all(item.published_at for item in listed))
        self.assertTrue(all("tag_types" not in item.extra for item in listed))


class MediaContentHashTests(unittest.TestCase):
    """哪些站的文件名是内容哈希：地址形状取自 2026-09-24 本机 ledger 的真实行。"""

    SHA = "58739e4717810cf6b2d4b4a0c1b5f79a0e2f1e3d4c5b6a79881726354a5b6c7d"
    MD5 = "424faa6b8bbe4f4df1d0858f8880b56f"

    def hash_of(self, provider, url):
        return follow_sources.media_content_hash(provider, url)

    def test_archive_sites_name_files_by_their_sha256(self):
        for provider, url in (
                ("kemono", f"https://kemono.cr/data/58/73/{self.SHA}.mp4"),
                ("pawchive", f"https://file.pawchive.pw/data/58/73/{self.SHA}.mp4"),
                ("pawchive", f"https://pawchive.pw/58/73/{self.SHA}"),
                ("coomer", f"https://coomer.st/data/58/73/{self.SHA}.jpg"),
                ("kemono", f"https://img.kemono.cr/thumbnail/data/58/73/{self.SHA}.png")):
            with self.subTest(url=url):
                self.assertEqual(self.hash_of(provider, url), f"sha256:{self.SHA}")

    def test_an_archive_path_whose_folders_disagree_with_the_name_is_not_a_hash(self):
        self.assertIsNone(self.hash_of("kemono", f"https://kemono.cr/data/aa/bb/{self.SHA}.mp4"))
        self.assertIsNone(self.hash_of("kemono", f"https://elsewhere.example/data/58/73/{self.SHA}.mp4"))

    def test_boorus_name_originals_by_their_md5(self):
        self.assertEqual(
            self.hash_of("rule34xxx", f"https://api-cdn-mp4.rule34.xxx/images/7456/{self.MD5}.mp4"),
            f"md5:{self.MD5}")
        self.assertEqual(
            self.hash_of("rule34paheal", f"https://r34i.paheal-cdn.net/42/4f/{self.MD5}"),
            f"md5:{self.MD5}")
        # 同一个文件在两个 booru 上是同一个键。
        self.assertEqual(
            self.hash_of("rule34xxx", f"https://api-cdn.rule34.xxx/images/1/{self.MD5}.jpeg"),
            self.hash_of("rule34paheal", f"https://r34i.paheal-cdn.net/42/4f/{self.MD5}"))

    def test_names_that_are_not_content_hashes_do_not_take_part(self):
        for provider, url in (
                # 早年帖子的 40 位文件名无从与站点 hash 字段核对。
                ("rule34xxx", "https://api-cdn.rule34.xxx/images/1223/"
                              "759219d0499d863a0de889e13ee4d47a8853a98b.jpg"),
                ("rule34paheal", f"https://r34i.paheal-cdn.net/aa/bb/{self.MD5}"),
                # rule34video 路径里那串是签名令牌，fanbox、f95zone 是站内随机 id。
                ("rule34video", f"https://rule34video.com/get_file/1/{self.MD5}/4583000/4583801/4583801_4k.mp4/"),
                ("fanbox", "https://downloads.fanbox.cc/images/post/12489354/TjsymhYHYYm8uIQ8QSLYW1LG.png"),
                ("f95zone", "https://attachments.f95zone.to/2026/09/6501839_1_1.png"),
                ("unknown", f"https://kemono.cr/data/58/73/{self.SHA}.mp4"),
                ("kemono", None)):
            with self.subTest(provider=provider, url=url):
                self.assertIsNone(self.hash_of(provider, url))


if __name__ == "__main__":
    unittest.main()
