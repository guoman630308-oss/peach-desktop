import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from peach.follow_faces import (
    FACE_DISTANCE,
    Face,
    FollowFaceIndex,
    RETRY_SECONDS,
    Signature,
    annotate_group,
    face_clusters,
    same_face,
)
from peach.follow_stream import FollowMediaResolver
from peach.http import HttpResponse


def _png(draw) -> bytes:
    image = Image.new("RGB", (64, 48), (40, 40, 40))
    draw(image)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _left_bright(image):
    image.paste((230, 230, 230), (0, 0, 32, 48))


def _stripes(image):
    for x in range(0, 64, 8):
        image.paste((200, 60, 60), (x, 0, x + 4, 48))


def _left_bright_recoloured(image):
    # 明暗走向与 `_left_bright` 一致（dHash 相同），只把一块换了颜色：同一个姿势的另一套衣服。
    image.paste((230, 230, 230), (0, 0, 32, 48))
    image.paste((255, 230, 150), (8, 12, 24, 36))


def _signature(draw) -> Signature:
    with Image.open(io.BytesIO(_png(draw))) as image:
        return Signature.of(image)


class SameFaceTests(unittest.TestCase):
    def setUp(self):
        self.pose = _signature(_left_bright)
        self.other_outfit = _signature(_left_bright_recoloured)
        self.other_scene = _signature(_stripes)

    def test_the_same_address_is_the_same_face_without_a_signature(self):
        self.assertTrue(same_face(Face("a", "https://x/1.jpg", None, None),
                                  Face("b", "https://x/1.jpg", None, None)))
        self.assertFalse(same_face(Face("a", "https://x/1.jpg", None, None),
                                   Face("b", "https://x/2.jpg", None, None)))

    def test_near_identical_frames_with_matching_durations_are_one_face(self):
        self.assertTrue(same_face(Face("a", "1", 30.0, self.pose), Face("b", "2", 30.8, self.pose)))
        # 时长都已知却差得多，是两段不同的内容。
        self.assertFalse(same_face(Face("a", "1", 30.0, self.pose), Face("b", "2", 45.0, self.pose)))
        self.assertFalse(same_face(Face("a", "1", 30.0, self.pose),
                                   Face("b", "2", 30.0, self.other_scene)))

    def test_a_recoloured_alt_with_the_same_outline_is_a_different_face(self):
        # dHash 只看明暗走向，单靠它会把穿衣版与 nude 版判成同一张；色块把换了颜色的那一块抓出来。
        distance, color = self.pose.distances(self.other_outfit)
        self.assertLessEqual(distance, FACE_DISTANCE)
        self.assertGreater(color, 20)
        self.assertFalse(same_face(Face("a", "1", 30.0, self.pose),
                                   Face("a", "2", 30.0, self.other_outfit)))
        self.assertFalse(same_face(Face("a", "1", None, self.pose),
                                   Face("a", "2", None, self.other_outfit)))

    def test_signatures_survive_the_cache_encoding(self):
        self.assertEqual(Signature.decode(self.pose.encode()), self.pose)
        self.assertIsNone(Signature.decode("zz"))
        self.assertIsNone(Signature.decode(self.pose.encode()[:40]))

    def test_clusters_follow_the_first_member_of_each_face(self):
        faces = [Face("a", "1", None, self.pose), Face("a", "2", None, self.other_scene),
                 Face("b", "3", None, self.pose)]
        self.assertEqual(face_clusters(faces), [0, 1, 0])


class AnnotateGroupTests(unittest.TestCase):
    """`/api/follow` 每一组上的 `stack`：翻哪几张、封面上写几个什么。"""

    def setUp(self):
        self.pose = _signature(_left_bright)
        self.other = _signature(_stripes)
        self.recoloured = _signature(_left_bright_recoloured)

    @staticmethod
    def member(ident, provider, thumb, duration=30.0, kind="video", media=(), variant="main"):
        return {"id": ident, "provider": provider, "thumb_url": thumb, "duration": duration,
                "media_kind": kind, "media_items": list(media), "variant_kind": variant}

    def annotate(self, members, signatures, hashes=None):
        index = mock.Mock()
        index.lookup.side_effect = lambda urls, providers=None: {
            url: signatures[url] for url in urls if url in signatures}
        group = {"primary": members[0], "variants": members[1:], "duplicates": []}
        return annotate_group(group, index, hashes)["stack"]

    def test_the_same_file_on_one_site_is_one_medium(self):
        # 3989 那种：同一个作者在 pawchive 的三个帖子（720p／1080p／4K）附的是同一个 gif。
        members = [self.member(ident, "pawchive", "https://a/sunset.gif", None, "image",
                               variant="alt") for ident in (1, 2, 3)]
        members.append(self.member(4, "rule34video", "https://v/4.jpg", 47.0, variant="alt"))
        stack = self.annotate(members, {"https://a/sunset.gif": self.pose,
                                        "https://v/4.jpg": self.other},
                              {(1, None): "sha256:aa", (2, None): "sha256:aa",
                               (3, None): "sha256:aa"})
        self.assertEqual((stack["media"], stack["copies"], stack["kind"]), (2, 4, "mixed"))

    def test_one_upload_collected_by_two_sources_counts_as_one_medium(self):
        members = [self.member(ident, "rule34video", f"/follow-cover?id={ident}")
                   for ident in (1, 2, 3)]
        for member, external_id in zip(members, ("first", "first", "second")):
            member["external_id"] = external_id
        stack = self.annotate(members, {})
        self.assertEqual((stack["media"], stack["copies"]), (2, 3))
        self.assertEqual(len(stack["faces"]), 2)

    def test_a_post_identity_preserves_its_separate_media_slots(self):
        members = [self.member(ident, "fanbox", None, None, "image", media=[
            {"index": index, "media_kind": "video", "thumb_url": f"/follow-cover?id={ident}&media={index}"}
            for index in (0, 2)]) for ident in (1, 2)]
        for member in members:
            member["external_id"] = "post-1"
        stack = self.annotate(members, {})
        self.assertEqual((stack["media"], stack["copies"]), (2, 4))
        self.assertEqual(len(stack["faces"]), 2)

    def test_site_identity_is_namespaced_by_provider(self):
        members = [self.member(1, "rule34video", "https://a/1.jpg"),
                   self.member(2, "rule34xxx", "https://b/1.jpg")]
        for member in members:
            member["external_id"] = "123"
        self.assertEqual(self.annotate(members, {})["media"], 2)

    def test_archive_post_numbers_are_scoped_by_service_and_creator(self):
        members = [self.member(ident, "kemono", f"https://img/{ident}.jpg") for ident in (1, 2, 3)]
        for member, page in zip(members, ("https://kemono.cr/patreon/user/1/post/123",
                                         "https://kemono.cr/fanbox/user/2/post/123",
                                         "https://kemono.cr/patreon/user/1/post/123")):
            member.update(external_id="123", url=page)
        self.assertEqual(self.annotate(members, {})["media"], 2)

    def test_known_different_files_preserve_media_despite_matching_post_identity(self):
        members = [self.member(ident, "kemono", f"https://img/{ident}.jpg") for ident in (1, 2)]
        for member in members:
            member.update(external_id="123", url="https://kemono.cr/patreon/user/1/post/123")
        stack = self.annotate(members, {}, {(1, None): "sha256:old", (2, None): "sha256:new"})
        self.assertEqual(stack["media"], 2)

    def test_archive_videos_without_thumbnails_merge_only_by_file_hash(self):
        # 归档站的视频没有缩略图：哈希相同就是同一个，跨站同站都一样；不同就各算一个，
        # 也不拿别的画面去猜。
        post = lambda ident, provider: self.member(ident, provider, None, None)  # noqa: E731
        stack = self.annotate([post(1, "kemono"), post(2, "pawchive"), post(3, "pawchive"),
                               post(4, "pawchive")], {},
                              {(1, None): "sha256:aa", (2, None): "sha256:aa",
                               (3, None): "sha256:aa", (4, None): "sha256:bb"})
        self.assertEqual((stack["media"], stack["copies"]), (2, 4))
        self.assertEqual(stack["faces"], [])

    def test_media_inside_posts_merge_by_file_hash(self):
        def post(ident, provider):
            return self.member(ident, provider, None, None, "image", media=[
                {"index": 0, "media_kind": "video", "thumb_url": None},
                {"index": 1, "media_kind": "image", "thumb_url": f"https://{provider}/c.png"}])
        stack = self.annotate([post(1, "kemono"), post(2, "pawchive")], {},
                              {(1, 0): "sha256:video", (1, 1): "sha256:cover",
                               (2, 0): "sha256:video", (2, 1): "sha256:cover"})
        self.assertEqual((stack["media"], stack["copies"]), (2, 4))
        # 翻卡也认同一个文件：两个站的封面图是同一张，只翻一次。
        self.assertEqual(len(stack["faces"]), 1)

    def test_a_post_cover_on_another_site_flips_once(self):
        # 6993 那种：帖子自身的哈希取视频，缩略图却是帖里那张图；另一个站上同一张图只有
        # 帖里那一份能凭哈希认出来，也要归进同一个画面。
        def post(ident, provider):
            cover = f"https://{provider}/c.png"
            return self.member(ident, provider, cover, None, media=[
                {"index": 0, "media_kind": "video", "thumb_url": None},
                {"index": 1, "media_kind": "image", "thumb_url": cover}])
        hashes = {}
        for ident in (1, 2):
            hashes.update({(ident, None): "sha256:video", (ident, 0): "sha256:video",
                           (ident, 1): "sha256:cover"})
        stack = self.annotate([post(1, "kemono"), post(2, "pawchive")], {}, hashes)
        self.assertEqual((stack["media"], stack["copies"]), (2, 4))
        self.assertEqual(len(stack["faces"]), 1)

    def test_a_different_hash_on_one_site_is_never_merged_by_its_picture(self):
        # 同站的差分与另一个分辨率的文件，8×8 签名与原图几乎一致（实测色块差 0.3–2.0），
        # 计数不按画面合并；翻卡照样只翻一次。
        stack = self.annotate([self.member(1, "kemono", "https://a/1.png", None, "image"),
                               self.member(2, "kemono", "https://a/2.png", None, "image")],
                              {"https://a/1.png": self.pose, "https://a/2.png": self.pose},
                              {(1, None): "sha256:aa", (2, None): "sha256:bb"})
        self.assertEqual((stack["media"], stack["copies"]), (2, 2))
        self.assertEqual(len(stack["faces"]), 1)

    def test_the_same_file_joins_regardless_of_version_labels(self):
        # 版本标签是各站按标题判的：remake 帖子（alt）附了原图，同站跨站都还是那一个文件。
        same_site = [self.member(1, "pawchive", None, None, "image"),
                     self.member(2, "pawchive", None, None, "image", variant="alt"),
                     self.member(3, "pawchive", None, None, "image", variant="wip")]
        hashes = {(ident, None): "sha256:aa" for ident in (1, 2, 3)}
        stack = self.annotate(same_site, {}, hashes)
        self.assertEqual((stack["media"], stack["copies"]), (1, 3))
        cross_site = self.annotate([self.member(1, "kemono", None, None, "image"),
                                    self.member(2, "pawchive", None, None, "image", variant="alt")],
                                   {}, {(1, None): "sha256:aa", (2, None): "sha256:aa"})
        self.assertEqual((cross_site["media"], cross_site["copies"]), (1, 2))

    def test_an_alt_with_its_own_file_stays_a_separate_medium(self):
        # 标签不促成合并：alt 自己的文件与 main 哈希不同，就是另一个媒体。
        stack = self.annotate([self.member(1, "pawchive", None, None, "image"),
                               self.member(2, "pawchive", None, None, "image", variant="alt")],
                              {}, {(1, None): "sha256:aa", (2, None): "sha256:bb"})
        self.assertEqual((stack["media"], stack["copies"]), (2, 2))

    def test_one_video_on_two_sites_is_one_medium_from_two_sources(self):
        stack = self.annotate([self.member(1, "rule34video", "https://a/1.jpg"),
                               self.member(2, "rule34xxx", "https://b/2.jpg", 30.4)],
                              {"https://a/1.jpg": self.pose, "https://b/2.jpg": self.pose})
        self.assertEqual((stack["media"], stack["copies"], stack["kind"]), (1, 2, "video"))
        self.assertEqual([face["thumb_url"] for face in stack["faces"]], ["https://a/1.jpg"])

    def test_the_same_frame_on_one_site_still_counts_twice(self):
        # 同一个站里画面相近的是那个站自己的两个版本（4K 与 1080p 两个帖子），各算一个；
        # 翻卡照样只翻一次。
        stack = self.annotate([self.member(1, "kemono", "https://a/1.jpg"),
                               self.member(2, "kemono", "https://a/2.jpg")],
                              {"https://a/1.jpg": self.pose, "https://a/2.jpg": self.pose})
        self.assertEqual((stack["media"], stack["copies"]), (2, 2))
        self.assertEqual(len(stack["faces"]), 1)

    def test_distinct_videos_are_counted_once_each_even_when_one_is_mirrored(self):
        stack = self.annotate([self.member(1, "rule34video", "https://a/1.jpg"),
                               self.member(2, "rule34xxx", "https://b/2.jpg"),
                               self.member(3, "rule34video", "https://a/3.jpg", 12.0),
                               self.member(4, "rule34video", "https://a/4.jpg", 30.0)],
                              {"https://a/1.jpg": self.pose, "https://b/2.jpg": self.pose,
                               "https://a/3.jpg": self.other, "https://a/4.jpg": self.recoloured})
        self.assertEqual((stack["media"], stack["copies"]), (3, 4))
        self.assertEqual(len(stack["faces"]), 3)

    def test_a_mirror_never_absorbs_two_versions_from_the_same_site(self):
        # 跨站那一份若把同站两条串成一簇，计数就把同站的两个版本吞掉了。
        stack = self.annotate([self.member(1, "kemono", "https://a/1.jpg"),
                               self.member(2, "kemono", "https://a/2.jpg"),
                               self.member(3, "pawchive", "https://b/3.jpg")],
                              {url: self.pose for url in
                               ("https://a/1.jpg", "https://a/2.jpg", "https://b/3.jpg")})
        self.assertEqual(stack["media"], 2)

    def test_post_media_are_the_merged_pieces_and_images_mix_with_videos(self):
        post = [{"index": i, "media_kind": "image", "thumb_url": f"https://a/p{i}.jpg"}
                for i in range(3)]
        stack = self.annotate([self.member(1, "kemono", "https://a/p0.jpg", None, "image", post),
                               self.member(2, "kemono", "https://a/v.jpg")], {})
        # 签名还没取得：只按地址认，帖子封面与首图是同一张。
        self.assertEqual((stack["media"], stack["copies"], stack["kind"]), (4, 4, "mixed"))
        self.assertEqual([face["media_kind"] for face in stack["faces"]],
                         ["image", "image", "image", "video"])

    def test_a_single_piece_is_not_a_stack(self):
        group = {"primary": self.member(1, "kemono", "https://a/1.jpg"),
                 "variants": [], "duplicates": []}
        self.assertIsNone(annotate_group(group)["stack"])


class FollowFaceIndexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = 1_000_000.0
        self.requests = []
        self.bodies = {"https://img.example.com/a.png": _png(_left_bright)}
        public = mock.patch("peach.follow_faces.resolves_publicly", return_value=True)
        public.start()
        self.addCleanup(public.stop)

    def transport(self, request, timeout, limit):
        self.requests.append(request.url)
        body = self.bodies.get(request.url)
        return HttpResponse(200 if body else 404, {}, body or b"")

    def index(self, **kwargs):
        return FollowFaceIndex(self.root / "faces", self.transport, clock=lambda: self.now,
                               sleeper=lambda seconds: None, background=False, **kwargs)

    def test_unknown_thumbnails_are_queued_and_filled_later(self):
        index = self.index()
        url = "https://img.example.com/a.png"
        self.assertEqual(index.lookup([url]), {})
        self.assertEqual(index.drain(), 1)
        self.assertEqual(index.lookup([url]), {url: _signature(_left_bright)})
        # 落了盘：换一个实例照样查得到，不再取第二次。
        self.assertEqual(self.index().lookup([url]), {url: _signature(_left_bright)})
        self.assertEqual(self.requests, [url])

    def test_failures_wait_a_day_before_the_next_try(self):
        index = self.index()
        url = "https://img.example.com/missing.png"
        index.lookup([url])
        index.drain()
        index.lookup([url])
        self.assertEqual(index.drain(), 0)
        self.now += RETRY_SECONDS + 1
        index.lookup([url])
        self.assertEqual(index.drain(), 1)
        self.assertEqual(self.requests, [url, url])

    def test_a_refused_thumbnail_pauses_its_source_without_a_day_long_miss(self):
        """缩略图被 429 挡回来：整站冷却，冷却期间同站的不再取；冷却过了照常补上。

        被拒不是「取不到」，记成失败就要等一天才再算这一批。
        """
        refused = {"https://img.example.com/a.png"}
        clock = [0.0]
        gate = FollowMediaResolver(transport=None, clock=lambda: clock[0])

        def transport(request, timeout, limit):
            self.requests.append(request.url)
            if request.url in refused:
                return HttpResponse(429, {"Retry-After": "60"}, b"")
            return HttpResponse(200, {}, self.bodies[request.url])

        gate.transport = transport
        self.bodies["https://img.example.com/b.png"] = _png(_stripes)
        index = self.index(gate=gate)
        urls = ["https://img.example.com/a.png", "https://img.example.com/b.png"]
        providers = dict.fromkeys(urls, "kemono")
        index.lookup(urls, providers)
        index.drain()
        self.assertEqual(self.requests, ["https://img.example.com/a.png"],
                         "第一张被拒后，同站第二张不再请求")
        self.assertGreater(gate.cooling("kemono"), 0)

        refused.clear()
        clock[0] += 61
        index.lookup(urls, providers)
        index.drain()
        self.assertEqual(index.lookup(urls), {"https://img.example.com/a.png": _signature(_left_bright),
                                              "https://img.example.com/b.png": _signature(_stripes)})

    def test_only_public_https_and_cached_covers_are_fetched(self):
        index = self.index()
        index.lookup(["http://img.example.com/a.png", "https://192.0.2.10/a.png",
                      "/follow-cover?id=7"])
        self.assertEqual(index.drain(), 0)
        self.assertEqual(self.requests, [])

    def test_video_covers_are_read_from_the_cover_cache(self):
        covers = self.root / "covers"
        covers.mkdir()
        (covers / "7-abc.jpg").write_bytes(_png(_stripes))
        (covers / "7-abc.123.456.tmp.jpg").write_bytes(b"partial")
        index = self.index(cover_root=covers)
        index.lookup(["/follow-cover?id=7", "/follow-cover?id=8"])
        index.drain()
        self.assertEqual(index.lookup(["/follow-cover?id=7", "/follow-cover?id=8"]),
                         {"/follow-cover?id=7": _signature(_stripes)})
        self.assertEqual(self.requests, [])
        stored = json.loads((self.root / "faces" / "face-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(len(stored["entries"]), 2)

    def test_each_video_in_a_post_reads_its_own_cover(self):
        # 帖子里第二个视频的首帧是 `<id>-m<序号>-<指纹>.jpg`；第一个视频不能读到它，
        # 它也不能读到第一个视频的，否则两段不同的视频签名一模一样。
        covers = self.root / "covers"
        covers.mkdir()
        (covers / "7-abc.jpg").write_bytes(_png(_stripes))
        (covers / "7-m2-def.jpg").write_bytes(_png(_left_bright))
        index = self.index(cover_root=covers)
        urls = ["/follow-cover?id=7", "/follow-cover?id=7&media=2", "/follow-cover?id=7&media=3"]
        index.lookup(urls)
        index.drain()
        self.assertEqual(index.lookup(urls), {"/follow-cover?id=7": _signature(_stripes),
                                              "/follow-cover?id=7&media=2": _signature(_left_bright)})


if __name__ == "__main__":
    unittest.main()
