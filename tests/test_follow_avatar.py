import json
import unittest

from peach.follow import FollowSourceError
from peach.follow_avatar import (
    profile_avatar_tiers, profile_identities, resolve_official_avatar, resolve_official_profile,
)
from peach.http import HttpResponse


class _Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, _timeout, _max_bytes):
        self.requests.append(request)
        return self.responses.pop(0)


class OfficialAvatarTests(unittest.TestCase):
    def _responses(self, *, user_id="30917150",
                   icon="https://pixiv.pximg.net/c/160x160/icon.jpeg"):
        metadata = {"urlContext": {"host": {"creatorId": "lazyprocrast"}}}
        page = (
            "<html><meta name='metadata' content='"
            + json.dumps(metadata).replace("'", "&#39;")
            + "'></html>"
        ).encode()
        api = json.dumps({"body": {"user": {
            "userId": user_id, "name": "LazyProcrastinator", "iconUrl": icon,
        }}}).encode()
        return [HttpResponse(200, {"content-type": "text/html"}, page),
                HttpResponse(200, {"content-type": "application/json"}, api)]

    def test_fanbox_avatar_comes_from_the_official_creator_profile(self):
        transport = _Transport(self._responses())
        avatar = resolve_official_avatar("fanbox", "30917150", transport=transport)
        self.assertEqual(avatar, "https://pixiv.pximg.net/c/160x160/icon.jpeg")
        self.assertEqual(transport.requests[0].url,
                         "https://www.pixiv.net/fanbox/creator/30917150")
        self.assertIn("creatorId=lazyprocrast", transport.requests[1].url)
        self.assertEqual(transport.requests[1].headers["Origin"],
                         "https://lazyprocrast.fanbox.cc")

    def test_the_same_verified_lookup_returns_the_official_profile(self):
        profile = resolve_official_profile(
            "fanbox", "30917150", transport=_Transport(self._responses()))
        self.assertEqual(profile.creator_id, "lazyprocrast")
        self.assertEqual(profile.name, "LazyProcrastinator")
        self.assertEqual(profile.url, "https://lazyprocrast.fanbox.cc/")

    def test_the_official_api_must_return_the_requested_user(self):
        with self.assertRaises(FollowSourceError):
            resolve_official_avatar(
                "fanbox", "30917150", transport=_Transport(
                    self._responses(user_id="999")))

    def test_the_avatar_cannot_redirect_to_an_untrusted_host(self):
        with self.assertRaises(FollowSourceError):
            resolve_official_avatar(
                "fanbox", "30917150", transport=_Transport(
                    self._responses(icon="https://example.test/avatar.jpg")))

    def test_only_verified_fanbox_identities_are_accepted(self):
        for service, user in (("patreon", "30917150"), ("fanbox", "../secret"),
                              ("fanbox", "")):
            with self.assertRaises(FollowSourceError):
                resolve_official_avatar(service, user, transport=_Transport([]))

    def test_a_creator_id_skips_the_lookup_page(self):
        """论坛名片上只有 `jul3dnsfw.fanbox.cc` 这一个身份，没有 pixiv 的数字 id。

        `creator.get` 本来就收创作者 id，那一步换算可以省掉。
        """
        transport = _Transport(self._responses()[1:])
        avatar = resolve_official_avatar("fanbox", "lazyprocrast",
                                         transport=transport)
        self.assertEqual(avatar, "https://pixiv.pximg.net/c/160x160/icon.jpeg")
        self.assertEqual(len(transport.requests), 1)
        self.assertIn("creatorId=lazyprocrast", transport.requests[0].url)

    def test_a_pixiv_profile_link_resolves_through_the_same_fanbox_lookup(self):
        transport = _Transport(self._responses())
        self.assertEqual(profile_avatar_tiers("pixiv", "30917150", transport=transport),
                         ["https://pixiv.pximg.net/c/160x160/icon.jpeg"])
        self.assertEqual(transport.requests[0].url,
                         "https://www.pixiv.net/fanbox/creator/30917150")

    def test_a_creator_id_still_needs_a_real_user_behind_it(self):
        # 没有可核对的数字 id 时，身份以官方资料回的为准；回不出就是没有头像。
        with self.assertRaises(FollowSourceError):
            resolve_official_avatar("fanbox", "lazyprocrast", transport=_Transport(
                self._responses(user_id="")[1:]))


class ProfileAvatarTests(unittest.TestCase):
    """论坛名片上的 X、Patreon 与 pixiv：各给一串从原图到小图的地址，主机写死。"""

    @staticmethod
    def _page(image):
        body = f'<html><meta property="og:image" content="{image}"></html>'.encode()
        return HttpResponse(200, {"content-type": "text/html"}, body)

    @staticmethod
    def _campaigns(vanity="sharkarts",
                   host="c10.patreonusercontent.com"):
        return HttpResponse(200, {"content-type": "application/json"}, json.dumps({"data": [
            {"attributes": {"vanity": vanity, "avatar_photo_image_urls": {
                "original": f"https://{host}/p/campaign/1/original.png",
                "default": f"https://{host}/p/campaign/1/default.png",
            }}}]}).encode())

    def test_x_avatar_tiers_come_from_the_logged_out_profile_page(self):
        transport = _Transport([self._page(
            "https://pbs.twimg.com/profile_images/1334790870760042496/QzmzhT0y_200x200.jpg")])
        tiers = profile_avatar_tiers("twitter", "Rekin3D", transport=transport)
        self.assertEqual(transport.requests[0].url, "https://x.com/Rekin3D")
        self.assertEqual(tiers[0],
                         "https://pbs.twimg.com/profile_images/1334790870760042496/QzmzhT0y.jpg")
        self.assertEqual(len(tiers), 3)

    def test_an_x_page_without_its_own_avatar_has_none(self):
        with self.assertRaises(FollowSourceError):
            profile_avatar_tiers("twitter", "Rekin3D", transport=_Transport([
                self._page("https://abs.twimg.com/rich_link_card.png")]))

    def test_patreon_avatar_starts_from_the_uploaded_original(self):
        transport = _Transport([self._campaigns()])
        tiers = profile_avatar_tiers("patreon", "sharkarts", transport=transport)
        self.assertIn("filter%5Bvanity%5D=sharkarts", transport.requests[0].url)
        self.assertEqual(tiers, [
            "https://c10.patreonusercontent.com/p/campaign/1/original.png",
            "https://c10.patreonusercontent.com/p/campaign/1/default.png",
        ])

    def test_patreon_must_answer_for_the_same_creator_on_its_own_image_host(self):
        for response in (self._campaigns(vanity="someoneelse"),
                         self._campaigns(host="example.test")):
            with self.subTest(response=response.body[:60]), \
                    self.assertRaises(FollowSourceError):
                profile_avatar_tiers("patreon", "sharkarts", transport=_Transport([response]))

    def test_profile_identities_accept_only_known_services_and_handle_shapes(self):
        self.assertEqual(profile_identities("twitter:Rekin3D,patreon:sharkarts,pixiv:14934767"),
                         (("twitter", "Rekin3D"), ("patreon", "sharkarts"),
                          ("pixiv", "14934767")))
        for value in ("", "twitter:../x", "fanbox:jul3dnsfw", "pixiv:flim13",
                      "twitter:Rekin3D,patreon:a/b",
                      ",".join(f"patreon:user{index}" for index in range(5))):
            with self.subTest(value=value):
                self.assertEqual(profile_identities(value), ())
        for service, handle in (("twitter", "../x"), ("fanbox", "jul3dnsfw")):
            with self.subTest(service=service), self.assertRaises(FollowSourceError):
                profile_avatar_tiers(service, handle, transport=_Transport([]))


if __name__ == "__main__":
    unittest.main()
