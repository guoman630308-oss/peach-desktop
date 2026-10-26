"""Gofile 展开器的隔离测试。

重点是那条安全约束：来源站的会话不能跟着资源链接发到第三方主机。展开器还是
`_BaseConnector` 一个方法的时候，它靠调用方记得写 `connector_headers=False` 维持；
搬出来之后展开器自己持有 transport，约束由结构保证。
"""
import json
import unittest

from peach.follow import FollowSourceError
from peach.follow_gofile import GofileExpander, folder_ids, folder_labels
from peach.follow_secrets import Credential, CredentialError
from peach.http import HttpResponse


CONTENTS_OK = json.dumps({
    "status": "ok",
    "data": {"children": {
        "a": {"type": "file", "mimetype": "video/mp4", "id": "a",
              "name": "one.mp4", "link": "https://store1.gofile.io/download/one.mp4",
              "thumbnail": "https://store1.gofile.io/one.jpg", "size": 123},
        "b": {"type": "file", "mimetype": "text/plain", "id": "b",
              "name": "readme.txt", "link": "https://store1.gofile.io/download/readme.txt"},
    }},
}).encode("utf-8")


def _transport(status=200, body=CONTENTS_OK, record=None):
    def call(request, timeout, max_bytes):
        if record is not None:
            record.append(request)
        return HttpResponse(status, {}, body)
    return call


class FolderIdTests(unittest.TestCase):
    def test_only_gofile_folder_links_are_picked_and_deduplicated(self):
        self.assertEqual(
            folder_ids([
                "https://gofile.io/d/OS2Qz9",
                "https://store1.gofile.io/d/OS2Qz9",   # 子域也算
                "https://gofile.io/d/OS2Qz9",          # 重复
                "https://example.test/d/NOPE",         # 别的站
                "https://gofile.io/about",             # 不是文件夹路径
                "not a url at all",
            ]),
            ["OS2Qz9"],
        )

    def test_fanbox_copy_can_name_each_gofile_folder(self):
        self.assertEqual(
            folder_labels(
                "gofile (Mitsuru) - https://gofile.io/d/DCkbZU "
                "gofile (Poll) - https://gofile.io/d/bFRgRI"
            ),
            {"DCkbZU": "Mitsuru", "bFRgRI": "Poll"},
        )


class CredentialIsolationTests(unittest.TestCase):
    """展开器自己发出的请求带什么。

    端到端的隔离已经有测试守着，这里不重造：
    `test_follow_sources.test_fanbox_cookie_stays_on_fanbox_requests` 断言 FANBOX 的
    会话不进 Gofile 请求，`test_cookie_resolves_masked_media_without_leaking_to_the_file_host`
    对 F95 的 masked 链接做同一件事。本文件补的是展开器自身此前没有的直接覆盖。
    """

    def test_the_request_carries_only_the_gofile_token(self):
        seen = []
        GofileExpander(
            _transport(record=seen),
            credential=Credential("gofile", {"api_token": "secret"}),
        ).expand(["https://gofile.io/d/OS2Qz9"])

        self.assertEqual(len(seen), 1)
        headers = {key.casefold(): value for key, value in seen[0].headers.items()}
        self.assertEqual(headers["authorization"], "Bearer secret")
        self.assertNotIn("cookie", headers, "第三方主机不该收到任何 Cookie")
        self.assertNotIn("secret", seen[0].url, "token 永远不进 URL")


class ApiErrorTests(unittest.TestCase):
    def _expand(self, status=200, body=CONTENTS_OK):
        return GofileExpander(
            _transport(status=status, body=body),
            credential=Credential("gofile", {"api_token": "t"}),
        ).expand(["https://gofile.io/d/OS2Qz9"])

    def test_no_credential_means_no_request_instead_of_an_error(self):
        seen = []
        expander = GofileExpander(_transport(record=seen), credential=None)
        self.assertEqual(expander.expand(["https://gofile.io/d/OS2Qz9"]), ())
        self.assertEqual(seen, [], "没有 token 就不该发请求")

    def test_a_rejected_token_is_a_credential_error(self):
        with self.assertRaises(CredentialError):
            self._expand(status=401, body=b"denied")
        with self.assertRaises(CredentialError):
            self._expand(body=json.dumps({"status": "error-token"}).encode("utf-8"))

    def test_a_non_premium_account_says_so_instead_of_failing_vaguely(self):
        with self.assertRaisesRegex(FollowSourceError, "Premium"):
            self._expand(body=json.dumps(
                {"status": "error-notPremium"}).encode("utf-8"))

    def test_only_playable_files_survive(self):
        items = GofileExpander(
            _transport(), credential=Credential("gofile", {"api_token": "t"}),
        ).expand(["https://gofile.io/d/OS2Qz9"], labels={"OS2Qz9": "Poll"})
        self.assertEqual([item["media_kind"] for item in items], ["video"])
        self.assertEqual(items[0]["resource_provider"], "gofile")
        self.assertEqual(items[0]["resource_group"], "gofile:OS2Qz9")
        self.assertEqual(items[0]["resource_group_label"], "Poll")
        self.assertEqual(items[0]["url"], "https://store1.gofile.io/download/one.mp4")


if __name__ == "__main__":
    unittest.main()
