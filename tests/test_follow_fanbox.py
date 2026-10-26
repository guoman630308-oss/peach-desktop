"""FANBOX public post body normalization tests."""
import unittest

from peach.fanbox import FanboxContentError, normalize_fanbox_post
from peach.follow_sources import resource_links


class FanboxNormalizerTests(unittest.TestCase):
    def test_article_keeps_block_order_and_collects_all_maps(self):
        post = {"type": "article", "body": {
            "blocks": [
                {"type": "header", "text": "Release"},
                {"type": "p", "text": "Gofile", "links": [
                    {"offset": 0, "length": 6, "url": "https://gofile.io/d/abc"},
                ]},
                {"type": "image", "imageId": "one"},
                {"type": "file", "fileId": "movie"},
                {"type": "embed", "embedId": "youtube"},
                {"type": "url_embed", "urlEmbedId": "card"},
            ],
            "imageMap": {
                "one": {"originalUrl": "https://downloads.fanbox.cc/one.jpg",
                        "thumbnailUrl": "https://downloads.fanbox.cc/one-thumb.jpg"},
                "unused": {"originalUrl": "https://downloads.fanbox.cc/unused.png"},
            },
            "fileMap": {
                "movie": {"name": "movie.mp4", "url":
                          "https://downloads.fanbox.cc/movie.mp4", "mimeType": "video/mp4"},
                "archive": {"name": "source.zip", "url":
                            "https://downloads.fanbox.cc/source.zip",
                            "mimeType": "application/zip"},
            },
            "embedMap": {"youtube": {"serviceProvider": "youtube",
                                        "videoId": "video-id"}},
            "urlEmbedMap": {"card": {"type": "html.card", "html":
                                        '<a href="https://pixeldrain.com/u/card">card</a>'}},
        }}

        content = normalize_fanbox_post(post)

        self.assertEqual(content.post_type, "article")
        self.assertEqual(content.summary, "Release\nGofile")
        self.assertEqual(
            [item["url"] for item in content.media_items],
            ["https://downloads.fanbox.cc/one.jpg",
             "https://downloads.fanbox.cc/movie.mp4",
             "https://downloads.fanbox.cc/unused.png"],
        )
        self.assertEqual((content.image_count, content.video_count, content.file_count),
                         (2, 1, 2))
        self.assertIn("https://www.youtube.com/watch?v=video-id", content.links)
        self.assertEqual(resource_links("\n".join(content.links)), [
            "https://gofile.io/d/abc",
            "https://downloads.fanbox.cc/movie.mp4",
            "https://pixeldrain.com/u/card",
            "https://downloads.fanbox.cc/source.zip",
        ])

    def test_cover_image_joins_media_after_the_body(self):
        cover = ("https://pixiv.pximg.net/c/1200x630_90_a2_g5/fanbox/public/images/"
                 "post/12565427/cover/1iVQFUYcuQvxD6oVZRBcy1Cm.jpeg")
        content = normalize_fanbox_post({
            "type": "image", "coverImageUrl": cover,
            "body": {"images": [
                {"id": "one", "originalUrl": "https://downloads.fanbox.cc/one.jpg",
                 "thumbnailUrl": "https://downloads.fanbox.cc/one-thumb.jpg"},
            ]},
        })

        self.assertEqual([item["id"] for item in content.media_items], ["one", "cover"])
        tail = content.media_items[-1]
        self.assertEqual(tail["name"], "封面")
        self.assertEqual(tail["url"], cover)
        self.assertEqual(tail["thumb_url"], cover)
        self.assertEqual(tail["media_kind"], "image")
        # 裁切尺寸写在封面地址里，解析出来给界面占位。
        self.assertEqual((tail["width"], tail["height"]), (1200, 630))
        self.assertEqual(content.image_count, 2)

    def test_body_images_carry_their_intrinsic_dimensions(self):
        content = normalize_fanbox_post({"type": "image", "body": {"images": [
            {"id": "one", "originalUrl": "https://downloads.fanbox.cc/one.png",
             "thumbnailUrl": "https://downloads.fanbox.cc/w/1200/one.jpeg",
             "width": 1920, "height": 1080},
            {"id": "two", "originalUrl": "https://downloads.fanbox.cc/two.png"},
        ]}})
        self.assertEqual((content.media_items[0]["width"],
                          content.media_items[0]["height"]), (1920, 1080))
        self.assertIsNone(content.media_items[1]["width"])

    def test_cover_image_is_skipped_when_absent_or_already_present(self):
        cover = "https://pixiv.pximg.net/fanbox/public/images/post/1/cover/abc.jpeg"
        again = normalize_fanbox_post({
            "type": "image",
            "body": {"images": [{"id": "one", "originalUrl": cover}]},
        })
        self.assertEqual([item["id"] for item in again.media_items], ["one"])
        none = normalize_fanbox_post({
            "type": "image",
            "coverImageUrl": cover,
            "body": {"images": [{"id": "one", "originalUrl": cover}]},
        })
        self.assertEqual([item["id"] for item in none.media_items], ["one"])

    def test_image_post_exposes_a_switchable_gallery(self):
        content = normalize_fanbox_post({"type": "image", "body": {"images": [
            {"id": "one", "originalUrl": "https://downloads.fanbox.cc/one.jpg",
             "thumbnailUrl": "https://downloads.fanbox.cc/one-thumb.jpg"},
            {"id": "two", "originalUrl": "https://downloads.fanbox.cc/two.png"},
        ]}})

        self.assertEqual(content.image_count, 2)
        self.assertEqual([item["id"] for item in content.media_items], ["one", "two"])
        self.assertTrue(all(item["media_kind"] == "image"
                            for item in content.media_items))

    def test_file_post_keeps_playable_files_and_links_the_archive(self):
        content = normalize_fanbox_post({"type": "file", "body": {"files": [
            {"id": "video", "name": "clip.webm", "url":
             "https://downloads.fanbox.cc/clip.webm", "mimeType": "video/webm"},
            {"id": "image", "name": "cover", "url":
             "https://downloads.fanbox.cc/cover", "mimeType": "image/png"},
            {"id": "zip", "name": "source.zip", "url":
             "https://downloads.fanbox.cc/source.zip", "mimeType": "application/zip"},
        ]}})

        self.assertEqual((content.image_count, content.video_count, content.file_count),
                         (1, 1, 3))
        self.assertEqual([item["media_kind"] for item in content.media_items],
                         ["video", "image"])
        self.assertEqual(resource_links("\n".join(content.links)), [
            "https://downloads.fanbox.cc/clip.webm",
            "https://downloads.fanbox.cc/cover",
            "https://downloads.fanbox.cc/source.zip",
        ])

    def test_legacy_html_collects_text_links_and_images(self):
        content = normalize_fanbox_post({"type": "entry", "body": {"html": """
            <h2>Old post</h2>
            <a href="https://gofile.io/d/old">files</a>
            <img data-src-original="https://downloads.fanbox.cc/old.jpg">
        """}})

        self.assertIn("Old post", content.summary)
        self.assertIn("https://gofile.io/d/old", content.links)
        self.assertEqual(content.image_count, 1)

    def test_unknown_type_fails_loudly(self):
        with self.assertRaisesRegex(FanboxContentError, "类型不受支持"):
            normalize_fanbox_post({"type": "future-shape", "body": {}})


if __name__ == "__main__":
    unittest.main()
