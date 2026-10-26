"""换头像：候选从哪来、地址边界在哪、装上去之后盘上是什么。"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

from peach import avatar_online, avatar_picker, gfriends, http as peach_http, jav_poster_crop
from peach.avatar_face import sidecar_path
from peach.avatar_provider import AvatarCandidateCache, inspect_avatar, provenance_now
from peach.http import HttpResponse
from peach.jav_poster_crop import MANUAL

from support.ledger import fresh_ledger

HAS_DEPS = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))

FILETREE = {
    "Content": {
        "0-Hand-Storage": {"葵つかさ.jpg": "葵つかさ.jpg?t=1"},
        "7-S1": {"葵つかさ.jpg": "葵つかさ.jpg?t=2"},
        "y-Minnano": {"葵つかさ.jpg": "AI-Fix-葵つかさ.jpg?t=3"},
        "8-GRAPHIS": {"別人.jpg": "別人.jpg?t=4"},
        # 同一个人按另一种写法另存的一批。图库里这是常态，而它跟上面三张一个键也不共用。
        "3-Prestige": {"葵ツカサ.jpg": "葵ツカサ.jpg?t=5"},
    }
}
LIBRARY_REF = "gfriends:7-S1/葵つかさ.jpg"


def picture(width: int = 40, height: int = 60, colour: str = "red") -> bytes:
    """一张有内容的图：底色之上压一块对比色。

    `images.is_flat` 把整张一个颜色的当成「来源取不到图时给的一块底色」挡在候选之外，
    所以拿来当人像用的固定件必须有起伏。压一块矩形就够——这些用例问的是「哪些候选
    列出来、排第几」，不是像素长什么样；`colour` 仍然决定内容哈希，各用例互不串图。
    """
    buffer = io.BytesIO()
    image = Image.new("RGB", (width, height), colour)
    ImageDraw.Draw(image).rectangle(
        (0, 0, width, height // 2), fill="black" if colour != "black" else "white")
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def flat_picture(width: int = 40, height: int = 60, colour: str = "white") -> bytes:
    """整张一个颜色——`is_flat` 要挡住的正是这种。"""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


def transport_of(body: bytes, status: int = 200, calls: list[str] | None = None):
    """一个只会吐出这张图的 transport。真实取图由 HTTP 域的用例覆盖。"""
    def send(request, timeout=None, max_bytes=None):
        if calls is not None:
            calls.append(request.url)
        return HttpResponse(status, {}, body, request.url)
    return send


def seed_library(providers_root: Path) -> Path:
    """把图库索引放进候选缓存，测试全程不出网。"""
    index_dir = providers_root / avatar_picker.GFRIENDS_CACHE
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / gfriends.INDEX_NAME).write_text(json.dumps(FILETREE),
                                                 encoding="utf-8")
    return index_dir


def seed_person(database: Path) -> None:
    """规范名是简体、图库里只有日文写法的那一类人，别名链的必要性全在这里。"""
    connection = sqlite3.connect(database)
    with connection:
        connection.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,"
            "created_at,updated_at) VALUES(7792,'performer','葵司','葵司','t','t')")
        connection.execute(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,"
            "confidence) VALUES(7792,'葵つかさ','葵つかさ','r18:performer',1.0)")
    connection.close()


class IndexTests(unittest.TestCase):
    """索引的解析与排序。目录前缀是来源优先级，`0-` 排在最前。"""

    def test_the_index_maps_one_name_to_every_source_best_first(self):
        index = gfriends.parse_filetree(json.dumps(FILETREE).encode("utf-8"))
        self.assertEqual(index["葵つかさ"], [
            ("0-Hand-Storage", "葵つかさ.jpg"),
            ("7-S1", "葵つかさ.jpg"),
            ("y-Minnano", "AI-Fix-葵つかさ.jpg"),
        ])

    def test_unknown_prefixes_sort_last_instead_of_first(self):
        """`find` 认不出的前缀返回 -1，直接拿去排序会让它抢到最前面。"""
        self.assertGreater(gfriends.quality_key("《怪目录", "a.jpg"),
                           gfriends.quality_key("z-DMM", "a.jpg"))

    def test_a_missing_index_is_empty_rather_than_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(gfriends.load_index(Path(folder)), {})
            self.assertIsNone(gfriends.index_age(Path(folder)))

    def test_every_name_on_the_chain_brings_its_own_pictures(self):
        """并起来，不是停在第一个命中的名字上。
        """
        index = gfriends.parse_filetree(json.dumps(FILETREE).encode("utf-8"))
        match = gfriends.candidates(index, ["葵司", "葵つかさ", "葵ツカサ"])
        self.assertEqual(match.names, ("葵つかさ", "葵ツカサ"))
        # 并完之后整体重排：挑图看的是哪张该先试，不是它由链上第几个名字找到的。
        self.assertEqual([category for category, _ in match.items],
                         ["0-Hand-Storage", "3-Prestige", "7-S1", "y-Minnano"])
        # 一张图的来路记的是找到它的那个名字，不能记成整条链。
        self.assertEqual(match.finder[("3-Prestige", "葵ツカサ.jpg")], "葵ツカサ")

    def test_a_picture_two_names_both_point_at_is_listed_once(self):
        index = {"甲": [("7-S1", "a.jpg")], "乙": [("7-S1", "a.jpg")]}
        match = gfriends.candidates(index, ["甲", "乙"])
        self.assertEqual(match.items, (("7-S1", "a.jpg"),))
        # 先指到它的那个名字算它的来路。
        self.assertEqual(match.finder[("7-S1", "a.jpg")], "甲")

    def test_a_chain_that_hits_nothing_is_falsy(self):
        index = gfriends.parse_filetree(json.dumps(FILETREE).encode("utf-8"))
        self.assertFalse(gfriends.candidates(index, ["谁都不是"]))


class SourceBoundaryTests(unittest.TestCase):
    """手填地址的边界。Peach 跑在用户机器上，这道判据挡的是「替人去探内网」。"""

    def test_only_public_https_names_are_accepted(self):
        with mock.patch.object(peach_http, "host_addresses",
                               return_value=("93.184.216.34",)):
            self.assertTrue(avatar_picker.allowed_source("https://example.com/a.jpg"))
            for rejected in ("http://example.com/a.jpg", "https://127.0.0.1/a.jpg",
                             "https://169.254.169.254/a.jpg", "https://peach.local/a.jpg",
                             "https://[::1]/a.jpg", "https://user:pw@example.com/a.jpg",
                             "ftp://example.com/a.jpg", "https://localhost/a.jpg",
                             "https://box.internal/a.jpg", ""):
                self.assertFalse(avatar_picker.allowed_source(rejected), rejected)

    def test_a_public_name_that_resolves_inward_is_still_refused(self):
        """名字像公网、解析出来的地址却不可全球路由——这正是绕开字面判据的走法。

        第三组是「一个公网地址加一个链路本地地址」：`169.254.169.254` 是云上取实例
        凭据的那个端点，只要有一个地址不合格就得整条拒掉，不能因为第一个合格就放行。
        """
        for addresses in ((), ("127.0.0.1",), ("93.184.216.34", "169.254.169.254")):
            with mock.patch.object(peach_http, "host_addresses",
                                   return_value=addresses):
                self.assertFalse(
                    avatar_picker.allowed_source("https://example.com/a.jpg"),
                    str(addresses))

    def test_bytes_have_to_decode_as_a_picture(self):
        """格式由解码结果定：响应头和扩展名都是别人说了算的。"""
        with self.assertRaises(avatar_picker.PickerError):
            avatar_picker.accept_image(b"<svg width='9'><rect/></svg>")
        with self.assertRaises(avatar_picker.PickerError):
            avatar_picker.accept_image(b"x" * (avatar_picker.MAX_IMAGE_BYTES + 1))
        self.assertEqual(avatar_picker.accept_image(picture()).width, 40)


class PickerFixture(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.providers = self.folder / "provider-cache" / "performer-avatars"
        self.avatars = self.folder / "avatars"
        self.avatars.mkdir(parents=True)
        seed_library(self.providers)
        self.database = fresh_ledger(self.folder)
        seed_person(self.database)
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        # 人脸模型要下 232 KB ONNX，测试不出网；取景本身由头像域的用例覆盖。
        self.face = mock.patch("peach.avatar_provider.FaceProbe").start()
        self.face.return_value.return_value = None
        self.addCleanup(mock.patch.stopall)

    def remember(self, body: bytes, provider: str = "social-web",
                 external_id: str = "old", url: str = "") -> str:
        """把一张图放进候选缓存并留下证据，等同「这个人取过这张」。

        `url` 给显式值是为了造「同一张图既在图库里、也在取过的图里」那一幕：跨来源
        认同一张图靠的是缓存记录里的地址，不是证据文件自己说了算。
        """
        cache = AvatarCandidateCache(self.providers / provider)
        inspected = inspect_avatar(body)
        url = url or f"https://{provider}.example/{external_id}.jpg"
        cache.store(url, body, inspected)
        cache.store_provenance(provenance_now(
            entity_id=7792, provider=provider, source_kind="official_profile",
            matched_name="葵司", name_source="canonical", external_id=external_id,
            upstream_url=url, width=inspected.width, height=inspected.height,
            mime_type=inspected.mime_type, sha256=inspected.sha256,
            cache_path=f"objects/{inspected.sha256}{inspected.extension}"))
        return inspected.sha256

    def listed(self) -> dict:
        return avatar_picker.choices(self.connection, self.providers,
                                     self.avatars, "performer", 7792)


class ChoiceTests(PickerFixture):
    def test_the_alias_is_what_finds_her_in_the_library(self):
        """规范名是简体，图库里只有日文写法；不查别名一张也列不出来。"""
        out = self.listed()
        self.assertEqual(out["names"], ["葵司", "葵つかさ"])
        self.assertEqual(out["matched_names"], ["葵つかさ"])
        self.assertEqual([one["ref"] for one in out["choices"]], [
            "gfriends:0-Hand-Storage/葵つかさ.jpg",
            LIBRARY_REF,
            "gfriends:y-Minnano/AI-Fix-葵つかさ.jpg",
        ])
        # 只有一个名字找得到时不必说是哪一个：那一屏上写着的就是她。
        self.assertEqual({one["found_by"] for one in out["choices"]}, {""})

    def test_a_second_writing_of_her_name_brings_its_own_pictures(self):
        """两种写法在图库里是两批图，而屏幕上要说清哪一张是按哪个名字找到的。

        找错人是这里唯一会出的大错，名字是唯一的线索：只报「图库里找到的」，一张
        同名不同人的图就没有任何能让人起疑的地方。
        """
        with self.connection:
            self.connection.execute(
                "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,"
                "confidence) VALUES(7792,'葵ツカサ','葵ツカサ','user:alias',1.0)")
        out = self.listed()
        self.assertEqual(out["matched_names"], ["葵つかさ", "葵ツカサ"])
        found = {one["ref"]: one["found_by"] for one in out["choices"]}
        self.assertEqual(found["gfriends:3-Prestige/葵ツカサ.jpg"], "葵ツカサ")
        self.assertEqual(found[LIBRARY_REF], "葵つかさ")

    def test_a_given_name_alone_does_not_search_the_library(self):
        """`茜` 在图库里是另外几个人；图库只命中一张时补头像不比脸就装。"""
        with self.connection:
            self.connection.execute(
                "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,"
                "confidence) VALUES(7792,'茜','茜','javdb-actor-page',0.8)")
        self.assertEqual(self.listed()["names"], ["葵司", "葵つかさ"])

    def test_a_short_alias_that_only_changes_the_glyphs_still_searches(self):
        """规范名本身就短的人，别名只是换了字形：图库里那是同一个人的另一个键。"""
        with self.connection:
            self.connection.execute(
                "UPDATE entity SET canonical_name='美优',normalized_name='美优' "
                "WHERE id=7792")
            self.connection.execute(
                "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,"
                "confidence) VALUES(7792,'美優','美優','r18:performer',1.0)")
        self.assertCountEqual(self.listed()["names"], ["美优", "葵つかさ", "美優"])

    def test_pictures_taken_before_show_up_as_their_own_group(self):
        """换回去不该再下一次：取过的图按内容哈希躺在候选缓存里。"""
        digest = self.remember(picture(colour="blue"))
        history = [one for one in self.listed()["choices"]
                   if one["source"] == "history"]
        self.assertEqual([one["ref"] for one in history], [f"sha256:{digest}"])

    def test_every_label_reads_as_where_the_picture_came_from(self):
        """格子底下写来源，不写取图时用的那串代号。

        `x:aoi_tsukasa8`、`jae:actress.html#joyu117` 回答的是「批处理怎么再找到它」，
        对着屏幕选图的人读不出意思。图库那一侧同理：目录名首字母是图库自己的优先级
        记号（`quality_key` 读的就是它），`S1`、`Minnano` 才说得出这张图出自谁家。
        """
        self.remember(picture(colour="blue"), external_id="x:aoi_tsukasa8")
        out = self.listed()["choices"]
        self.assertEqual([one["label"] for one in out if one["source"] == "gfriends"],
                         ["Hand-Storage", "S1", "Minnano"])
        self.assertEqual([one["label"] for one in out if one["source"] == "history"],
                         ["社交主页"])

    def test_a_picture_with_one_colour_never_reaches_the_grid(self):
        """整张一个颜色的不是人像，是来源取不到图时给的一块底色。

        X 取不到头像时回的那张是 143×143 纯白：尺寸过得了短边下限、格式是正经图片，
        只有看像素才分得出来。摆出来就是一块白格子，而点下去真的会把它装成头像。
        """
        self.remember(flat_picture(), external_id="blank")
        self.assertEqual([one for one in self.listed()["choices"]
                          if one["source"] == "history"], [])

    def test_the_one_on_disk_right_now_is_marked_and_comes_first(self):
        """在用的那张是这一屏唯一的参照物——别的候选好不好，是跟它比出来的。"""
        body = picture(colour="green")
        digest = self.remember(body, external_id="now")
        (self.avatars / "performer-7792.img").write_bytes(body)
        out = self.listed()["choices"]
        self.assertEqual([one["ref"] for one in out if one["current"]],
                         [f"sha256:{digest}"])
        self.assertEqual(out[0]["ref"], f"sha256:{digest}")

    def test_one_picture_is_one_choice_even_when_two_routes_found_it(self):
        """图库里的图取过之后，它同时也是「这个人取过的图」。

        两个格子长得一模一样、点哪个结果也一样，只有标签不同。留图库那一边：`S1`
        说得出这张图是谁家的，「图库」只说得出它是从哪条路子来的。尺寸也一并从缓存
        记录里补上——判一张图多大用不着把它读出来。
        """
        digest = self.remember(picture(colour="blue"), provider="gfriends",
                               external_id="7-S1/葵つかさ.jpg",
                               url=gfriends.image_url("7-S1", "葵つかさ.jpg"))
        out = self.listed()["choices"]
        self.assertEqual([one["ref"] for one in out].count(LIBRARY_REF), 1)
        self.assertNotIn(f"sha256:{digest}", [one["ref"] for one in out])
        listed = next(one for one in out if one["ref"] == LIBRARY_REF)
        self.assertEqual((listed["width"], listed["height"]), (40, 60))

    def test_a_stale_index_is_reported_rather_than_refreshed(self):
        """页面不为一次点击同步拉 6 MB；过期就说出来，补索引是批处理的事。"""
        self.assertFalse(self.listed()["index_stale"])
        old = gfriends.INDEX_MAX_AGE_SECONDS + 3600
        with mock.patch.object(gfriends, "index_age", return_value=old):
            stale = self.listed()
        self.assertTrue(stale["index_stale"])
        self.assertEqual(stale["index_age_hours"], round(old / 3600, 1))

    def test_a_full_frame_library_crop_has_one_choice_and_keeps_current(self):
        body = picture(350, 350, "purple")
        url = gfriends.image_url("7-S1", "葵つかさ.jpg")
        self.remember(body, provider="gfriends", url=url)
        cut, origin = avatar_picker.crop(body, {"x0": 0, "y0": 0, "x1": 350, "y1": 350})
        avatar_picker.install(self.providers, self.avatars, "performer", 7792, cut,
                              {**origin, "provider": "gfriends", "upstream_url": url})
        listed = self.listed()["choices"]
        self.assertEqual([one["ref"] for one in listed if one["current"]], [LIBRARY_REF])
        self.assertEqual(listed[0]["label"], "S1")
        self.assertEqual(len(listed), 3)

    def test_a_partial_library_crop_remains_a_distinct_composition(self):
        body = picture(350, 350, "purple")
        url = gfriends.image_url("7-S1", "葵つかさ.jpg")
        self.remember(body, provider="gfriends", url=url)
        cut, origin = avatar_picker.crop(body, {"x0": 50, "y0": 50, "x1": 300, "y1": 300})
        avatar_picker.install(self.providers, self.avatars, "performer", 7792, cut,
                              {**origin, "provider": "gfriends", "upstream_url": url})
        listed = self.listed()["choices"]
        self.assertEqual(len(listed), 4)
        self.assertEqual(listed[0]["ref"], f"sha256:{inspect_avatar(cut).sha256}")
        self.assertIn(LIBRARY_REF, [one["ref"] for one in listed])


class AssetArtworkTests(PickerFixture):
    """从本人作品的画面里框头像：列哪些作品、谁递得进来、切出来是什么。"""

    def setUp(self):
        super().setUp()
        self.covers = self.folder / "covers"
        self.covers.mkdir()
        self.cells = self.folder / "cells"
        self.cells.mkdir()
        self.cover = self.covers / "ABW-232.jpg"
        self.cover.write_bytes(picture(400, 260, "green"))
        self.cell = self.cells / "abw232-4.jpg"
        self.cell.write_bytes(picture(320, 180, "blue"))
        self.artwork = avatar_picker.ArtworkSource(
            cover_root=self.covers,
            frame=lambda asset_id, cell: self.cell if cell == 4 else None)

    def test_account_artwork_without_an_identified_performer_is_a_stand_in(self):
        from peach.entity_classification import write_claim

        self.add_asset(11, 'ABW-232')
        with self.connection:
            self.connection.execute("UPDATE entity SET kind='creator' WHERE id=7792")

        def details():
            return [choice.detail for choice in
                    avatar_picker.asset_artwork(self.connection, self.covers, 7792)]

        self.assertEqual(details(), ['代表作画面，非本人 · ABW-232 的标题'])
        body, origin = avatar_picker.resolve('asset:11:cover', self.connection, self.providers,
                                             7792, transport_of(b''), artwork=self.artwork)
        self.assertEqual(body, self.cover.read_bytes())
        self.assertFalse(origin['identity_verified'])
        with self.connection:
            write_claim(self.connection, entity_id=7792, facet='identity', value='person',
                        source='user:identity-review', evidence='本人出演确认', status='approved', confidence=1)
        self.assertEqual(details(), ['ABW-232 的标题'])
        for facet, value in [('account_role', 'seller'), ('occupation', 'animator')]:
            with self.connection:
                write_claim(self.connection, entity_id=7792, facet=facet, value=value,
                            source='user:identity-review', evidence='发布他人作品', status='approved', confidence=1)
            self.assertEqual(details(), ['代表作画面，非本人 · ABW-232 的标题'])
            with self.connection:
                self.connection.execute('DELETE FROM entity_classification WHERE entity_id=7792 AND facet=? AND value=?',
                                        (facet, value))

    def add_asset(self, asset_id: int, code: str, snapshot: str = "sheet.jpg",
                  entity_id: int = 7792, size: int = 100, file: str = "") -> None:
        file = file or f"{code}.mp4"
        with self.connection:
            self.connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,code,catalog_title,"
                "size,snapshot_path) VALUES(?,'R:',?,?,'video',?,?,?,?)",
                (asset_id, f"R:\\media\\{file}", file, code,
                 f"{code} 的标题", size, snapshot))
            self.connection.execute(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source) "
                "VALUES(?,?,'performer','r18:performer')",
                (asset_id, entity_id))

    def test_one_work_takes_one_cell_and_carries_every_base_it_has(self):
        self.add_asset(11, "ABW-232")
        listed = avatar_picker.choices(self.connection, self.providers, self.avatars,
                                       "performer", 7792, cover_root=self.covers)
        assets = [one for one in listed["choices"] if one["source"] == "asset"]
        self.assertEqual([one["label"] for one in assets], ["ABW-232"])
        # 封面加九宫格九格，底图在弹层里换，格子只占一个。
        self.assertEqual(assets[0]["bases"],
                         ["asset:11:cover"] + [f"asset:11:cell{n}" for n in range(9)])
        self.assertTrue(assets[0]["crop"], "作品画面要先框一块才能当头像")

    def test_one_code_split_over_several_files_still_takes_one_cell(self):
        # 分段与重复目录各是一条 asset 行，封面同一张；留最大那份，格子只占一个。
        self.add_asset(11, "ABW-232", size=100, file="ABW-232-1.mp4")
        self.add_asset(12, "ABW-232", size=300, file="ABW-232-2.mp4")
        self.add_asset(13, "abw232", size=200, file="dup\\abw232.mp4")
        listed = avatar_picker.choices(self.connection, self.providers, self.avatars,
                                       "performer", 7792, cover_root=self.covers)
        assets = [one for one in listed["choices"] if one["source"] == "asset"]
        self.assertEqual([one["ref"] for one in assets], ["asset:12:cover"])

    def test_a_work_with_neither_a_cover_nor_a_sheet_is_not_listed(self):
        self.add_asset(12, "NOPE-001", snapshot="")
        listed = avatar_picker.choices(self.connection, self.providers, self.avatars,
                                       "performer", 7792, cover_root=self.covers)
        self.assertEqual([one for one in listed["choices"] if one["source"] == "asset"], [])

    def test_the_sharpest_cover_comes_first_and_a_bare_sheet_last(self):
        """框出来的头像清不清楚只看底图的像素，跟片子文件多大无关。"""
        (self.covers / "FC2-PPV-1.jpg").write_bytes(picture(276, 154, "blue"))
        (self.covers / "FC2-PPV-2.jpg").write_bytes(picture(1180, 2100, "red"))
        self.add_asset(21, "FC2-PPV-1", size=900)
        self.add_asset(22, "FC2-PPV-2", size=100)
        self.add_asset(23, "SHEET-001", size=5000)
        listed = avatar_picker.choices(self.connection, self.providers, self.avatars,
                                       "performer", 7792, cover_root=self.covers)
        assets = [one for one in listed["choices"] if one["source"] == "asset"]
        self.assertEqual([one["label"] for one in assets],
                         ["FC2-PPV-2", "FC2-PPV-1", "SHEET-001"])
        self.assertEqual([(one["width"], one["height"]) for one in assets],
                         [(1180, 2100), (276, 154), (0, 0)])

    def test_a_cover_and_a_sheet_cell_both_come_back_as_bytes(self):
        self.add_asset(11, "ABW-232")
        body, origin = avatar_picker.resolve("asset:11:cover", self.connection,
                                             self.providers, 7792, None, self.artwork)
        self.assertEqual(body, self.cover.read_bytes())
        self.assertEqual(origin["provider"], "asset")
        self.assertEqual(origin["asset_code"], "ABW-232")
        frame, _ = avatar_picker.resolve("asset:11:cell4", self.connection,
                                         self.providers, 7792, None, self.artwork)
        self.assertEqual(frame, self.cell.read_bytes())

    def test_a_box_drawn_on_a_cover_that_was_since_replaced_is_refused(self):
        """补高清会原地换掉封面：旧图上框的那组坐标落在新图上是另一块地方。"""
        self.add_asset(11, "ABW-232")
        listed = avatar_picker.choices(self.connection, self.providers, self.avatars,
                                       "performer", 7792, cover_root=self.covers)
        drawn_on = next(one for one in listed["choices"] if one["source"] == "asset")["version"]
        self.assertTrue(drawn_on, "封面那一格要带版本，预览地址靠它换新")
        body, _ = avatar_picker.resolve("asset:11:cover", self.connection, self.providers,
                                        7792, None, self.artwork, version=drawn_on)
        self.assertEqual(body, self.cover.read_bytes())

        self.cover.write_bytes(picture(800, 520, "green"))
        later = self.cover.stat().st_mtime_ns + 1_000_000_000
        os.utime(self.cover, ns=(later, later))
        with self.assertRaisesRegex(avatar_picker.PickerError, "刚换过"):
            avatar_picker.resolve("asset:11:cover", self.connection, self.providers,
                                  7792, None, self.artwork, version=drawn_on)
        # 九宫格那几格从视频抽帧，不随封面变，带着旧版本照样取得到。
        frame, _ = avatar_picker.resolve("asset:11:cell4", self.connection, self.providers,
                                         7792, None, self.artwork, version=drawn_on)
        self.assertEqual(frame, self.cell.read_bytes())

    def test_a_work_that_is_not_hers_is_refused_even_though_the_file_is_there(self):
        """`asset:1:cover` 是个人都拼得出来；凭它读到别人作品的封面就是个枚举口子。"""
        self.add_asset(13, "ABW-232", entity_id=9001)
        with self.connection:
            self.connection.execute(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,"
                "created_at,updated_at) VALUES(9001,'performer','别人','别人','t','t')")
        with self.assertRaises(avatar_picker.PickerError):
            avatar_picker.resolve("asset:13:cover", self.connection, self.providers,
                                  7792, None, self.artwork)

    def test_a_malformed_or_missing_frame_says_so_instead_of_guessing(self):
        self.add_asset(11, "ABW-232")
        for bad in ("asset:11:cell9", "asset:11:cell三", "asset:十一:cover",
                    "asset:11:什么", "asset:11:cell7"):
            with self.subTest(bad=bad), self.assertRaises(avatar_picker.PickerError):
                avatar_picker.resolve(bad, self.connection, self.providers, 7792,
                                      None, self.artwork)
        # 端点没递作品来源时也不能猜一个。
        with self.assertRaises(avatar_picker.PickerError):
            avatar_picker.resolve("asset:11:cover", self.connection, self.providers,
                                  7792, None, None)

    def test_the_crop_makes_new_bytes_and_records_the_box(self):
        body = picture(400, 260, "green")
        cropped, origin = avatar_picker.crop(body, {"x0": 40, "y0": 20,
                                                    "x1": 200, "y1": 180})
        self.assertEqual(avatar_picker.accept_image(cropped).width, 160)
        self.assertEqual(origin["crop_box"], [40, 20, 200, 180])
        self.assertEqual(origin["crop_source_px"], [400, 260])
        self.assertEqual(origin["source_kind"], "user_cropped")

    def test_a_box_that_is_not_a_box_reads_as_a_failure(self):
        for bad in (None, {}, {"x0": 0, "y0": 0, "x1": 0, "y1": 10}):
            with self.subTest(bad=bad), self.assertRaises(avatar_picker.PickerError):
                avatar_picker.crop(picture(400, 260, "green"), bad)
        with self.assertRaises(avatar_picker.PickerError):
            avatar_picker.crop(b"not an image", {"x0": 0, "y0": 0, "x1": 9, "y1": 9})


class PortraitFramingTests(unittest.TestCase):
    """人像进框选：默认框落在哪、框在哪张像素上切。"""

    def test_a_phone_photo_is_cut_where_the_page_showed_it(self):
        # 像素按横的存、标签说「转 90° 显示」：页面上是 20×40 的竖图，上红下蓝。
        body = sideways_photo()
        cropped, origin = avatar_picker.crop(body, {"x0": 0, "y0": 20, "x1": 20, "y1": 40})
        self.assertEqual(origin["crop_source_px"], [20, 40])
        with Image.open(io.BytesIO(cropped)) as image:
            red, _green, blue = image.convert("RGB").getpixel((10, 10))
        self.assertGreater(blue, red, "框的是页面上的下半张，切出来却不是蓝的那一块")

    def test_a_portrait_is_framed_around_the_face_the_probe_found(self):
        from peach.avatar_cover_face import face_square

        body = picture(300, 400, "teal")
        record = face_record(300, 400, cx=0.5, cy=0.2, w=0.1)
        choice = avatar_picker.framed(LIBRARY_REF, body, lambda _: record,
                                      source="", label="")
        self.assertEqual((choice.width, choice.height, choice.crop), (300, 400, False))
        self.assertEqual(choice.bases, (LIBRARY_REF,))
        # 和批处理从封面截头像是同一块：页面上默认框住的就是批处理会截的那块。
        self.assertEqual(choice.focus, face_square(record, 300, 400))
        blind = avatar_picker.framed(LIBRARY_REF, body, lambda _: None, source="", label="")
        self.assertIsNone(blind.focus)

    def test_a_phone_photo_is_measured_the_way_the_page_shows_it(self):
        seen: list[tuple[int, int]] = []

        def probe(body: bytes):
            with Image.open(io.BytesIO(body)) as image:
                seen.append(image.size)
            return None

        choice = avatar_picker.framed("", sideways_photo(), probe, source="upload", label="me.jpg")
        self.assertEqual((choice.width, choice.height), (20, 40))
        self.assertEqual(seen, [(20, 40)], "探针检的不是转正后的那张")


def sideways_photo() -> bytes:
    """手机照片的存法：像素是 40×20 的横图，左红右蓝，EXIF 方向 6（显示时顺时针转 90°）。
    转正后是 20×40 的竖图，上红下蓝。"""
    image = Image.new("RGB", (40, 20), "red")
    ImageDraw.Draw(image).rectangle((20, 0, 40, 20), fill="blue")
    exif = image.getexif()
    exif[0x0112] = 6
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif.tobytes())
    return buffer.getvalue()


def face_record(width: int, height: int, cx: float = 0.8, cy: float = 0.3,
                w: float = 0.05) -> dict:
    """`avatar_face` 边车的形状：`px` 是检测时那张图的尺寸，脸框按比例给。"""
    return {"ratio": width / height, "px": [width, height],
            "face": {"cx": cx, "cy": cy, "w": w, "h": w * 1.2, "score": 0.9}}


class CoverFocusTests(AssetArtworkTests):
    """格子与框选默认框围着哪一块取景。"""

    def focus_of(self) -> dict | None:
        listed = avatar_picker.choices(self.connection, self.providers, self.avatars,
                                       "performer", 7792, cover_root=self.covers)
        return next(one for one in listed["choices"] if one["source"] == "asset")["focus"]

    def test_a_cover_crop_and_its_work_share_a_choice_with_all_bases(self):
        self.add_asset(11, "ABW-232")
        image = Image.open(io.BytesIO(self.cover.read_bytes()))
        ImageDraw.Draw(image).rectangle((315, 60, 330, 90), fill="red")
        image.save(self.cover, format="JPEG")
        record = face_record(400, 260)
        sidecar_path(self.cover).write_text(json.dumps(record), "utf-8")
        box = avatar_picker.cover_focus("ABW-232", self.cover, record, 400, 260)
        from peach.images import crop_to_box
        body = crop_to_box(self.cover.read_bytes(), box)
        avatar_picker.install(self.providers, self.avatars, "performer", 7792, body,
                              {"provider": "cover-face", "source_kind": "cover_face_crop",
                               "upstream_url": "peach:cover-face/ABW-232"})
        listed = avatar_picker.choices(self.connection, self.providers, self.avatars,
                                       "performer", 7792, cover_root=self.covers)["choices"]
        current = [one for one in listed if one["current"]]
        self.assertEqual([one["ref"] for one in current], ["asset:11:cover"])
        self.assertEqual(current[0]["bases"], ["asset:11:cover"] +
                         [f"asset:11:cell{cell}" for cell in range(9)])
        source, _ = avatar_picker.resolve(current[0]["ref"], self.connection, self.providers,
                                          7792, None, self.artwork)
        restored, _ = avatar_picker.crop(source, current[0]["focus"])
        self.assertEqual(restored, body)
        self.assertEqual(len(listed), 4)

    def test_a_detected_face_frames_the_same_square_the_batch_would_cut(self):
        self.add_asset(11, "ABW-232")
        sidecar_path(self.cover).write_text(json.dumps(face_record(400, 260)), "utf-8")
        # 脸宽 20px，方框 48px，脸心在 (320, 78)。
        self.assertEqual(self.focus_of(), {"x0": 296, "y0": 54, "x1": 344, "y1": 102})

    def test_a_face_found_on_another_size_of_the_cover_is_not_trusted(self):
        """封面换成更大的那张之后，旧记录的脸心落在新图上是一块错位的区域。"""
        self.add_asset(11, "ABW-232")
        sidecar_path(self.cover).write_text(json.dumps(face_record(200, 130)), "utf-8")
        # 退回正封：400×260 是封套，正封按比例从右缘量回去 0.704 倍高。
        self.assertEqual(self.focus_of(), {"x0": 217, "y0": 0, "x1": 400, "y1": 260})

    def test_a_sleeve_with_a_framed_front_panel_uses_that_panel(self):
        self.add_asset(11, "ABW-232")
        jav_poster_crop.write_sidecar(
            self.cover, jav_poster_crop.manual_record(400, 260, {"x0": 230, "y0": 0,
                                                                  "x1": 400, "y1": 260}))
        self.assertEqual(self.focus_of(), {"x0": 230, "y0": 0, "x1": 400, "y1": 260})

    def test_a_cover_that_is_not_a_sleeve_has_nothing_to_frame_on(self):
        (self.covers / "FC2-PPV-1.jpg").write_bytes(picture(400, 260, "blue"))
        self.add_asset(21, "FC2-PPV-1")
        self.assertIsNone(self.focus_of())

    def whole_cover_history(self) -> dict:
        self.remember(picture(400, 260, "green"), provider="cover-fallback",
                      external_id="ABW-232")
        listed = avatar_picker.choices(self.connection, self.providers, self.avatars,
                                       "performer", 7792, cover_root=self.covers)
        return next(one for one in listed["choices"] if one["source"] == "history")

    def test_a_whole_cover_kept_by_the_batch_is_framed_like_the_work_itself(self):
        """封面兜底存下的是整张封套；点下去不框就把书脊和封底一起装进圆框。"""
        sidecar_path(self.cover).write_text(json.dumps(face_record(400, 260)), "utf-8")
        choice = self.whole_cover_history()
        self.assertEqual((choice["label"], choice["crop"]), ("作品封面 ABW-232", True))
        self.assertEqual(choice["focus"], {"x0": 296, "y0": 54, "x1": 344, "y1": 102})

    def test_a_whole_cover_without_a_face_record_falls_back_to_the_front_panel(self):
        self.assertEqual(self.whole_cover_history()["focus"],
                         {"x0": 217, "y0": 0, "x1": 400, "y1": 260})

    def co_star(self, asset_id: int) -> None:
        """同一部作品里再登记一位演员。"""
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO entity(id,kind,canonical_name,normalized_name,"
                "created_at,updated_at) VALUES(9001,'performer','共演','共演','t','t')")
            self.connection.execute(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source) "
                "VALUES(?,9001,'performer','r18:performer')", (asset_id,))

    def test_a_shared_cover_says_how_many_are_on_it_and_skips_the_face(self):
        """合演封面上最大那张脸多半是领衔的另一位：只标人数，取景退回正封。"""
        self.add_asset(11, "ABW-232")
        self.co_star(11)
        sidecar_path(self.cover).write_text(json.dumps(face_record(400, 260)), "utf-8")
        listed = avatar_picker.choices(self.connection, self.providers, self.avatars,
                                       "performer", 7792, cover_root=self.covers)
        choice = next(one for one in listed["choices"] if one["source"] == "asset")
        self.assertEqual(choice["cast"], 2)
        self.assertEqual(choice["focus"], {"x0": 217, "y0": 0, "x1": 400, "y1": 260})

    def test_her_own_work_counts_one(self):
        self.add_asset(11, "ABW-232")
        listed = avatar_picker.choices(self.connection, self.providers, self.avatars,
                                       "performer", 7792, cover_root=self.covers)
        self.assertEqual(next(one for one in listed["choices"]
                              if one["source"] == "asset")["cast"], 1)

    def test_a_whole_shared_cover_shares_the_work_and_skips_the_face(self):
        self.add_asset(11, "ABW-232")
        self.co_star(11)
        sidecar_path(self.cover).write_text(json.dumps(face_record(400, 260)), "utf-8")
        self.remember(picture(400, 260, "green"), provider="cover-fallback",
                      external_id="ABW-232")
        listed = avatar_picker.choices(self.connection, self.providers, self.avatars,
                                       "performer", 7792, cover_root=self.covers)
        choice = next(one for one in listed["choices"] if one["source"] == "asset")
        self.assertEqual((choice["cast"], choice["focus"]),
                         (2, {"x0": 217, "y0": 0, "x1": 400, "y1": 260}))
        self.assertFalse(any(one["source"] == "history" for one in listed["choices"]))


class CodeCoverTests(PickerFixture):
    """按番号取一张封面来框：本机有就不出网，取过一次就不再取。"""

    def setUp(self):
        super().setUp()
        self.covers = self.folder / "covers"
        self.covers.mkdir()
        self.fetched: list[str] = []
        self.body = picture(800, 540, "maroon")

    def fetch(self, key: str) -> bytes:
        self.fetched.append(key)
        return self.body

    def take(self, code: str, probe=lambda body: None):
        return avatar_picker.code_cover(code, self.covers, self.providers, self.fetch, probe)

    def test_a_code_outside_the_library_is_fetched_once_and_then_read_back(self):
        first = self.take("abw-999")
        self.assertEqual((first.ref, first.source, first.label), ("cover:ABW-999", "code", "ABW-999"))
        self.assertEqual((first.width, first.height, first.crop), (800, 540, True))
        self.take("ABW-999")
        self.assertEqual(self.fetched, ["ABW-999"])
        body, origin = avatar_picker.resolve("cover:ABW-999", self.connection,
                                             self.providers, 7792, None)
        self.assertEqual(body, self.body)
        self.assertEqual(origin["provider"], "code-cover")
        # `keep` 拿 `upstream_url` 当缓存键；写了它，框出来的那一块会顶掉整张封面。
        self.assertNotIn("upstream_url", origin)

    def test_a_fetched_cover_never_poses_as_something_she_took(self):
        self.take("ABW-999")
        self.assertEqual([one for one in self.listed()["choices"]
                          if one["source"] == "history"], [])

    def test_a_cover_on_this_machine_is_used_without_the_network(self):
        (self.covers / "ABW-232.jpg").write_bytes(self.body)
        sidecar_path(self.covers / "ABW-232.jpg").write_text(
            json.dumps(face_record(800, 540)), "utf-8")
        probed: list[bytes] = []
        chosen = self.take("ABW-232", probe=lambda body: probed.append(body))
        self.assertEqual(self.fetched, [])
        self.assertEqual(probed, [], "边车里已经有这张图的脸")
        self.assertEqual(chosen.focus, (592, 114, 688, 210))

    def test_a_fetched_cover_is_probed_for_a_face(self):
        chosen = self.take("ABW-999", probe=lambda body: face_record(800, 540, cx=0.2))
        self.assertEqual(chosen.focus, (112, 114, 208, 210))

    def test_a_code_never_fetched_is_refused_instead_of_going_out(self):
        """出网只在人输入番号那一步；预览和确认递来的 `cover:` 只读本机。"""
        with self.assertRaises(avatar_picker.PickerError):
            avatar_picker.resolve("cover:ABW-999", self.connection, self.providers, 7792, None)
        with self.assertRaises(avatar_picker.PickerError):
            self.take("  ")
        self.assertEqual(self.fetched, [])


class ResolveTests(PickerFixture):
    def test_a_library_candidate_is_downloaded_once(self):
        body = picture()
        calls: list[str] = []
        transport = transport_of(body, calls=calls)
        got, origin = avatar_picker.resolve(LIBRARY_REF, self.connection,
                                            self.providers, 7792, transport)
        self.assertEqual(got, body)
        self.assertEqual(origin["gfriends_category"], "7-S1")
        self.assertTrue(origin["upstream_url"].startswith(gfriends.GFRIENDS_RAW))
        AvatarCandidateCache(self.providers / avatar_picker.GFRIENDS_CACHE).store(
            origin["upstream_url"], body, inspect_avatar(body))
        avatar_picker.resolve(LIBRARY_REF, self.connection, self.providers, 7792,
                              transport)
        self.assertEqual(len(calls), 1)

    def test_a_reference_outside_what_was_listed_is_refused(self):
        """`ref` 要在服务端刚枚举出来的那一批里，否则就是一个任意地址抓取的口子。"""
        for bad in ("gfriends:8-GRAPHIS/別人.jpg", "gfriends:../../etc/passwd",
                    "https://evil.example/a.jpg", "sha256:" + "0" * 64, ""):
            with self.assertRaises(avatar_picker.PickerError, msg=bad):
                avatar_picker.resolve(bad, self.connection, self.providers, 7792,
                                      transport_of(picture()))

    def test_an_upstream_error_reads_as_a_failure_not_a_picture(self):
        with self.assertRaises(avatar_picker.PickerError):
            avatar_picker.resolve(LIBRARY_REF, self.connection, self.providers,
                                  7792, transport_of(b"", status=404))

    def test_without_a_transport_an_uncached_candidate_says_so(self):
        with self.assertRaises(avatar_picker.PickerError):
            avatar_picker.resolve(LIBRARY_REF, self.connection, self.providers,
                                  7792, None)

    def test_a_remembered_picture_comes_back_without_the_network(self):
        body = picture(colour="blue")
        digest = self.remember(body)
        got, origin = avatar_picker.resolve(f"sha256:{digest}", self.connection,
                                            self.providers, 7792, None)
        self.assertEqual(got, body)
        self.assertEqual(origin["provider"], "history")


class InstallTests(PickerFixture):
    def test_installing_writes_the_four_files_and_keeps_the_evidence(self):
        body = picture(colour="purple")
        result = avatar_picker.install(
            self.providers, self.avatars, "performer", 7792, body,
            {"source": "avatar picker", "provider": "upload", "external_id": "a.jpg"})
        target = self.avatars / "performer-7792.img"
        self.assertEqual(target.read_bytes(), body)
        self.assertEqual(Path(f"{target}.ct").read_text(encoding="utf-8"),
                         "image/jpeg")
        record = json.loads(Path(f"{target}.provenance.json").read_text("utf-8"))
        self.assertEqual(record["provider"], "upload")
        self.assertEqual(record["sha256"], result["sha256"])
        self.assertTrue(record["imported_at"])
        evidence = list(self.providers.glob("upload/evidence/performer-7792-*.json"))
        self.assertEqual(len(evidence), 1)
        self.assertEqual(json.loads(evidence[0].read_text("utf-8"))["source_kind"],
                         "user_selected")

    def test_the_replaced_picture_stays_reachable_as_history(self):
        """被顶下来的那张不删不搬：换回去只是再装一次，不重新取。"""
        first = picture(colour="orange")
        digest = inspect_avatar(first).sha256
        avatar_picker.install(self.providers, self.avatars, "performer", 7792,
                              first, {"provider": "upload"})
        avatar_picker.install(self.providers, self.avatars, "performer", 7792,
                              picture(colour="teal"), {"provider": "upload"})
        refs = {one["ref"] for one in self.listed()["choices"]
                if one["source"] == "history"}
        self.assertIn(f"sha256:{digest}", refs)
        back, _ = avatar_picker.resolve(f"sha256:{digest}", self.connection,
                                        self.providers, 7792, None)
        self.assertEqual(back, first)

    def test_a_stale_face_box_never_survives_a_swap(self):
        """留着上一张图的脸框，页面会按它给新图取景，放大到一个空位置上。"""
        target = self.avatars / "performer-7792.img"
        sidecar = sidecar_path(target)
        sidecar.write_text('{"focus": {}}', encoding="utf-8")
        avatar_picker.install(self.providers, self.avatars, "performer", 7792,
                              picture(), {"provider": "upload"})
        self.assertFalse(sidecar.exists())

    def test_a_face_that_is_found_lands_beside_the_new_picture(self):
        self.face.return_value.return_value = {"focus": {"x": 0.5, "y": 0.4}}
        avatar_picker.install(self.providers, self.avatars, "performer", 7792,
                              picture(), {"provider": "upload"})
        sidecar = sidecar_path(self.avatars / "performer-7792.img")
        self.assertEqual(json.loads(sidecar.read_text("utf-8"))["focus"]["y"], 0.4)

    def test_what_is_not_a_picture_never_reaches_the_avatar_tree(self):
        with self.assertRaises(avatar_picker.PickerError):
            avatar_picker.install(self.providers, self.avatars, "performer", 7792,
                                  b"not an image", {"provider": "upload"})
        self.assertFalse((self.avatars / "performer-7792.img").exists())


@unittest.skipUnless(HAS_DEPS, "FastAPI/httpx 尚未安装")
class AvatarPickerRouteTests(unittest.TestCase):
    """三个端点：列出候选、取候选的预览图、换上去。"""

    def setUp(self):
        from fastapi.testclient import TestClient
        from peach.api import create_app
        from peach.config import PeachSettings

        self.folder = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.candidates = self.folder / "generated"
        self.avatars = self.folder / "avatars"
        self.avatars.mkdir(parents=True)
        self.providers = self.candidates / "provider-cache" / "performer-avatars"
        seed_library(self.providers)
        database = fresh_ledger(self.folder)
        seed_person(database)
        self.face = mock.patch("peach.avatar_provider.FaceProbe").start()
        self.face.return_value.return_value = None
        self.addCleanup(mock.patch.stopall)
        self.app = create_app(PeachSettings(
            db_path=database, configured=True, token="secret",
            avatar_root=self.avatars, candidate_root=self.candidates,
            cover_root=self.folder / "covers"))
        self.picture = picture(colour="navy")
        self.app.state.http_transport = transport_of(self.picture)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def test_the_choices_endpoint_needs_the_token(self):
        self.assertEqual(
            self.client.get("/api/avatar-choices?kind=performer&id=7792").status_code,
            401)

    def test_the_choices_endpoint_lists_the_library_candidates(self):
        out = self.client.get(
            "/api/avatar-choices?kind=performer&id=7792&t=secret").json()
        self.assertEqual(out["matched_names"], ["葵つかさ"])
        self.assertIn(LIBRARY_REF, [one["ref"] for one in out["choices"]])

    def _online_creator(self):
        from peach.follow_sources import FollowCandidate, SourceFetch
        from peach.follow_store import FollowStore
        with self.app.state.database.write_transaction() as connection:
            connection.execute(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES(8892,'creator','Online Author','online author','t','t')")
            connection.execute(
                "INSERT INTO entity_link(entity_id,link_kind,label,url,created_at,updated_at)"
                " VALUES(8892,'social','X','https://x.com/OnlineAuthor','t','t')")
            store = FollowStore(lambda: connection)
            source = store.register(provider="fanbox", ref="online", label="Online Author",
                                    url="https://online.fanbox.cc/", entity_id=8892)
            store.record(source, SourceFetch(provider="fanbox", ref="online", request_url="https://online.fanbox.cc/",
                semantics="work", candidates=(FollowCandidate(provider="fanbox", external_id="post1", title="Online work",
                    thumb_url="https://example.com/cover.jpg", extra={"media_kind": "image", "media_items": [
                        {"id": "one", "media_kind": "image", "url": "https://example.com/full.jpg"},
                        {"id": "two", "media_kind": "image", "url": "https://example.com/hidden.jpg"}]}),)))
            item_id = store.items(source_id=source)[0].id
            connection.execute("UPDATE follow_item SET hidden_media_json='[\"two\"]' WHERE id=?", (item_id,))
        return item_id

    def test_online_choices_include_social_cover_and_visible_content_with_private_refs(self):
        item = self._online_creator()
        listing = self.client.get('/api/avatar-choices?kind=creator&id=8892&t=secret').json()
        online = next(choice for choice in listing['choices'] if choice['source'] == 'online')
        self.assertEqual(online['bases'], [f'follow:{item}:cover', f'follow:{item}:image0'])
        self.assertTrue(any(choice['source'] == 'social' for choice in listing['choices']))
        self.assertNotIn('https://example.com', json.dumps(listing))

    def test_online_domain_uses_the_injected_visible_media_and_cover(self):
        item_id = self._online_creator()
        project = mock.Mock(return_value=avatar_online.MediaChoices(
            cover="https://example.com/policy-cover.jpg", images=(0,)))
        image, avatar, media = mock.Mock(return_value=self.picture), mock.Mock(), mock.Mock()
        with self.app.state.database.read_connection() as connection:
            listed = avatar_online.choices(connection, 8892, project=project)
            online = next(choice for choice in listed if choice.source == "online")
            self.assertEqual(online.bases, (f"follow:{item_id}:cover", f"follow:{item_id}:image0"))
            body, origin = avatar_online.resolve(f"follow:{item_id}:cover", connection, 8892,
                project=project, image=image, avatar=avatar, media=media)
        self.assertEqual(body, self.picture)
        self.assertEqual(origin["external_id"], f"follow:{item_id}:cover")
        self.assertEqual(image.call_args.args[0].id, item_id)
        self.assertEqual(image.call_args.args[1], "https://example.com/policy-cover.jpg")
        self.assertTrue(all(call.args[0].id == item_id for call in project.call_args_list))
        avatar.assert_not_called()
        media.assert_not_called()

    def test_online_domain_rechecks_visibility_before_fetching_a_listed_candidate(self):
        item_id = self._online_creator()
        project = mock.Mock(return_value=avatar_online.MediaChoices(cover=None, images=(0,)))
        fetch = mock.Mock(return_value=self.picture)
        with self.app.state.database.read_connection() as connection:
            online = next(choice for choice in avatar_online.choices(connection, 8892, project=project)
                          if choice.source == "online")
            self.assertEqual(online.ref, f"follow:{item_id}:image0")
            project.return_value = avatar_online.MediaChoices(cover=None, images=())
            with self.assertRaises(avatar_picker.PickerError):
                avatar_online.resolve(online.ref, connection, 8892,
                    project=project, image=fetch, avatar=fetch, media=fetch)
        fetch.assert_not_called()

    def test_single_online_image_lists_its_original_content_alongside_the_cover(self):
        item = self._online_creator()
        with self.app.state.database.write_transaction() as connection:
            connection.execute("UPDATE follow_item SET metadata_json='{\"media_kind\":\"image\"}',"
                               " media_url='https://example.com/full.jpg',hidden_media_json='[]' WHERE id=?", (item,))
        listing = self.client.get('/api/avatar-choices?kind=creator&id=8892&t=secret').json()
        online = next(choice for choice in listing['choices'] if choice['source'] == 'online')
        self.assertEqual(online['bases'], [f'follow:{item}:cover', f'follow:{item}:image'])

    def test_online_cover_is_cached_and_crop_installs_only_on_the_creator(self):
        item = self._online_creator()
        calls = []
        self.app.state.http_transport = transport_of(self.picture, calls=calls)
        ref = f'follow:{item}:cover'
        with mock.patch('peach.avatar_picker.allowed_source', return_value=True):
            preview = self.client.get('/avatar-choice', params={'kind': 'creator', 'id': 8892, 'ref': ref, 't': 'secret'})
            self.assertEqual(preview.status_code, 200)
            framed = self.client.post('/api/avatar-frame?t=secret', json={'kind': 'creator', 'id': 8892, 'ref': ref})
            self.assertEqual(framed.status_code, 200)
            self.assertEqual(framed.json()['bases'], [ref, f'follow:{item}:image0'])
            picked = self.client.post('/api/avatar-pick?t=secret', json={'kind': 'creator', 'id': 8892,
                'ref': ref, 'crop': {'x0': 0, 'y0': 0, 'x1': 40, 'y1': 40}})
        self.assertEqual(picked.status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertTrue((self.avatars / 'creator-8892.img').exists())
        for entity_id, forbidden in ((7792, ref), (8892, f'follow:{item}:image1')):
            response = self.client.get('/avatar-choice', params={'kind': 'creator', 'id': entity_id,
                                                               'ref': forbidden, 't': 'secret'})
            self.assertEqual(response.status_code, 404)

    def test_a_preview_serves_the_picture_and_a_bad_reference_404s(self):
        response = self.client.get(
            "/avatar-choice", params={"kind": "performer", "id": 7792,
                                      "ref": LIBRARY_REF, "t": "secret"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertEqual(response.content, self.picture)
        missing = self.client.get(
            "/avatar-choice", params={"id": 7792, "ref": "gfriends:8-GRAPHIS/別人.jpg",
                                      "t": "secret"})
        self.assertEqual(missing.status_code, 404)

    def test_picking_a_library_candidate_installs_it(self):
        response = self.client.post("/api/avatar-pick?t=secret",
                                    json={"kind": "performer", "id": 7792,
                                          "ref": LIBRARY_REF})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sha256"],
                         inspect_avatar(self.picture).sha256)
        self.assertEqual((self.avatars / "performer-7792.img").read_bytes(),
                         self.picture)

    def test_a_file_from_this_machine_arrives_as_the_request_body(self):
        body = picture(colour="olive")
        response = self.client.post(
            "/api/avatar-pick?id=7792&kind=performer&name=me.jpg&t=secret",
            content=body, headers={"content-type": "image/jpeg"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((self.avatars / "performer-7792.img").read_bytes(), body)
        record = json.loads((self.avatars / "performer-7792.img.provenance.json").read_text("utf-8"))
        self.assertTrue(record["user_selected"])

    def test_a_file_from_this_machine_can_be_cut_before_it_is_installed(self):
        body = picture(300, 400, "olive")
        framed = self.client.post(
            "/api/avatar-frame?id=7792&kind=performer&name=me.jpg&t=secret",
            content=body, headers={"content-type": "image/jpeg"})
        self.assertEqual(framed.status_code, 200)
        self.assertEqual((framed.json()["width"], framed.json()["height"]), (300, 400))
        # 只量不存：本机文件确认时浏览器再发一次原字节。
        self.assertFalse((self.providers / "upload").exists())
        picked = self.client.post(
            "/api/avatar-pick?id=7792&kind=performer&name=me.jpg&crop=0,0,300,300&t=secret",
            content=body, headers={"content-type": "image/jpeg"})
        self.assertEqual(picked.status_code, 200)
        installed = avatar_picker.accept_image((self.avatars / "performer-7792.img").read_bytes())
        self.assertEqual((installed.width, installed.height), (300, 300))

    def test_a_typed_address_is_checked_before_peach_goes_and_fetches_it(self):
        refused = self.client.post("/api/avatar-frame?t=secret",
                                   json={"id": 7792, "url": "http://169.254.169.254/a.jpg"})
        self.assertEqual(refused.status_code, 400)
        self.assertFalse((self.providers / avatar_picker.ADDRESS_CACHE).exists())
        address = "https://example.com/a.jpg"
        calls: list[str] = []
        self.app.state.http_transport = transport_of(self.picture, calls=calls)
        with mock.patch.object(peach_http, "host_addresses",
                               return_value=("93.184.216.34",)):
            accepted = self.client.post("/api/avatar-frame?t=secret",
                                        json={"id": 7792, "url": address})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["ref"], f"url:{address}")
        # 框选时看的预览、确认时切的都是取来的那一份，不再出网。
        preview = self.client.get("/avatar-choice", params={
            "kind": "performer", "id": 7792, "ref": f"url:{address}", "t": "secret"})
        self.assertEqual(preview.content, self.picture)
        picked = self.client.post("/api/avatar-pick?t=secret", json={
            "kind": "performer", "id": 7792, "ref": f"url:{address}"})
        self.assertEqual(picked.status_code, 200)
        self.assertEqual(calls, [address])
        self.assertEqual((self.avatars / "performer-7792.img").read_bytes(), self.picture)

    def test_an_address_never_fetched_is_refused_instead_of_going_out(self):
        calls: list[str] = []
        self.app.state.http_transport = transport_of(self.picture, calls=calls)
        response = self.client.post("/api/avatar-pick?t=secret", json={
            "kind": "performer", "id": 7792, "ref": "url:https://example.com/b.jpg"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(calls, [])

    def test_a_library_portrait_is_framed_around_the_face(self):
        from peach import routes_media
        from peach.avatar_cover_face import face_square

        record = face_record(40, 60, cx=0.5, cy=0.25, w=0.3)
        with mock.patch.object(routes_media._WORK_FACE_PROBE, "on_bytes", return_value=record):
            response = self.client.post("/api/avatar-frame?t=secret", json={
                "kind": "performer", "id": 7792, "ref": LIBRARY_REF})
        self.assertEqual(response.status_code, 200)
        out = response.json()
        self.assertEqual((out["width"], out["height"], out["bases"]), (40, 60, [LIBRARY_REF]))
        self.assertEqual(tuple(out["focus"].values()), face_square(record, 40, 60))
        # 取过一次就留在缓存里，接下来的预览与装上去都不再出网。
        cache = AvatarCandidateCache(self.providers / avatar_picker.GFRIENDS_CACHE)
        self.assertEqual(cache.lookup(gfriends.image_url("7-S1", "葵つかさ.jpg")), self.picture)

    def test_cutting_a_library_portrait_keeps_the_whole_one_under_its_address(self):
        body = picture(300, 400, "purple")
        self.app.state.http_transport = transport_of(body)
        picked = self.client.post("/api/avatar-pick?t=secret", json={
            "kind": "performer", "id": 7792, "ref": LIBRARY_REF,
            "crop": {"x0": 50, "y0": 100, "x1": 250, "y1": 300}})
        self.assertEqual(picked.status_code, 200)
        installed = (self.avatars / "performer-7792.img").read_bytes()
        self.assertEqual(avatar_picker.accept_image(installed).width, 200)
        cache = AvatarCandidateCache(self.providers / avatar_picker.GFRIENDS_CACHE)
        self.assertEqual(cache.lookup(gfriends.image_url("7-S1", "葵つかさ.jpg")), body,
                         "图库那一格取回来的成了框出来的那一块")
        # 框出来的那一块自己进「取过的图」，换回去不必再框一次。
        listed = self.client.get("/api/avatar-choices?kind=performer&id=7792&t=secret").json()
        current = [one for one in listed["choices"] if one["current"]]
        self.assertEqual([one["ref"] for one in current],
                         [f"sha256:{inspect_avatar(installed).sha256}"])
        self.assertEqual(current[0]["label"], "图库")

    def test_a_piece_cut_from_a_taken_picture_reads_as_one_she_took(self):
        self.client.post("/api/avatar-pick?t=secret", json={
            "kind": "performer", "id": 7792, "ref": LIBRARY_REF})
        taken = f"sha256:{inspect_avatar(self.picture).sha256}"
        picked = self.client.post("/api/avatar-pick?t=secret", json={
            "kind": "performer", "id": 7792, "ref": taken,
            "crop": {"x0": 0, "y0": 0, "x1": 40, "y1": 40}})
        self.assertEqual(picked.status_code, 200)
        listed = self.client.get("/api/avatar-choices?kind=performer&id=7792&t=secret").json()
        current = [one for one in listed["choices"] if one["current"]]
        self.assertEqual([one["label"] for one in current], ["取过的图"])

    def test_a_typed_code_brings_back_a_cover_that_then_frames_and_installs(self):
        from peach import routes_media

        cover = picture(800, 540, "maroon")
        with mock.patch.object(routes_media, "_official_cover", return_value=cover) as fetched, \
                mock.patch.object(routes_media._WORK_FACE_PROBE, "on_bytes", return_value=None):
            response = self.client.post("/api/avatar-code-cover?t=secret",
                                        json={"code": "abw-999"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["ref"], "cover:ABW-999")
            self.assertEqual(fetched.call_args.args[1], "ABW-999")
            preview = self.client.get("/avatar-choice", params={
                "kind": "performer", "id": 7792, "ref": "cover:ABW-999", "t": "secret"})
            self.assertEqual(preview.content, cover)
            picked = self.client.post("/api/avatar-pick?t=secret", json={
                "kind": "performer", "id": 7792, "ref": "cover:ABW-999",
                "crop": {"x0": 400, "y0": 0, "x1": 800, "y1": 400}})
        self.assertEqual(picked.status_code, 200)
        self.assertEqual(avatar_picker.accept_image(
            (self.avatars / "performer-7792.img").read_bytes()).width, 400)

    def test_a_code_that_is_not_a_code_never_reaches_the_sources(self):
        from peach import routes_media

        with mock.patch.object(routes_media, "_official_cover") as fetched:
            for bad in ("../etc/passwd", "https://example.com/a.jpg", ""):
                response = self.client.post("/api/avatar-code-cover?t=secret",
                                            json={"code": bad})
                self.assertEqual(response.status_code, 400, bad)
        fetched.assert_not_called()

    def test_a_request_without_a_picture_or_an_id_is_refused(self):
        for payload in ({"id": 7792}, {"ref": LIBRARY_REF}, {"id": 0}):
            response = self.client.post("/api/avatar-pick?t=secret", json=payload)
            self.assertEqual(response.status_code, 400, str(payload))
        self.assertFalse((self.avatars / "performer-7792.img").exists())


@unittest.skipUnless(HAS_DEPS, "FastAPI/httpx 尚未安装")
class CoverCropRouteTests(unittest.TestCase):
    """详情页框正封：写的是边车里的框，封面原图一个字节都不动。"""

    def setUp(self):
        from fastapi.testclient import TestClient
        from peach.api import create_app
        from peach.config import PeachSettings

        self.folder = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.covers = self.folder / "covers"
        self.covers.mkdir(parents=True)
        self.cover = self.covers / "ABW-232.jpg"
        # 1.48 的宽高比才落在封套那一档；正封的框就是从这张里框出来的。
        self.cover.write_bytes(picture(800, 540, "teal"))
        self.before = self.cover.read_bytes()
        self.app = create_app(PeachSettings(
            db_path=fresh_ledger(self.folder), configured=True, token="secret",
            cover_root=self.covers))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def sidecar(self) -> dict:
        return json.loads(
            jav_poster_crop.sidecar_path(self.cover).read_text(encoding="utf-8"))

    def test_a_hand_drawn_box_is_written_as_the_framing_and_read_back(self):
        response = self.client.post("/api/cover-crop?t=secret", json={
            "code": "ABW-232", "box": {"x0": 300, "y0": 20, "x1": 700, "y1": 520}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["poster_box"]["method"], MANUAL)
        self.assertEqual(response.json()["poster_box"]["px"], [800, 540])
        self.assertEqual(self.sidecar()["box"]["x0"], 300)
        self.assertEqual(self.sidecar()["source"], "user:crop")
        self.assertEqual(self.cover.read_bytes(), self.before,
                         "取景是边车元数据，封面原图一个字节都不动")

    def test_restoring_the_default_recomputes_instead_of_leaving_nothing(self):
        self.client.post("/api/cover-crop?t=secret", json={
            "code": "ABW-232", "box": {"x0": 300, "y0": 20, "x1": 700, "y1": 520}})
        response = self.client.post("/api/cover-crop?t=secret",
                                    json={"code": "ABW-232", "box": None})
        self.assertEqual(response.status_code, 200)
        # 边车删掉就没人再算一遍，算出来的那一档取景会跟着消失。
        self.assertNotEqual(self.sidecar()["box"]["method"], MANUAL)
        self.assertNotIn("source", self.sidecar())

    def test_the_endpoint_needs_the_token(self):
        response = self.client.post("/api/cover-crop", json={"code": "ABW-232"})
        self.assertEqual(response.status_code, 401)

    def test_default_crop_uses_current_main_face_and_edition_evidence(self):
        self.cover.with_suffix('.face.json').write_text(json.dumps({
            'px': [800, 540], 'layout': 'still',
            'face': {'cx': .555, 'cy': .331, 'w': .257, 'h': .422, 'score': .918},
        }), encoding='utf-8')
        self.cover.with_suffix('.scraping.json').write_text(json.dumps({'title': 'VR 作品'}), encoding='utf-8')
        response = self.client.post('/api/cover-crop?t=secret', json={'code': 'ABW-232', 'box': None})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['poster_box'])
        self.assertEqual(self.sidecar()['box'], {'x0': 0, 'y0': 0, 'x1': 800, 'y1': 540, 'method': 'none'})
        self.assertEqual(self.sidecar()['title'], 'VR 作品')
        self.assertEqual(self.cover.read_bytes(), self.before)

    def test_a_missing_cover_or_an_impossible_box_says_which(self):
        missing = self.client.post("/api/cover-crop?t=secret",
                                   json={"code": "NOPE-001", "box": None})
        self.assertEqual(missing.status_code, 404)
        bad = self.client.post("/api/cover-crop?t=secret", json={
            "code": "ABW-232", "box": {"x0": 300, "y0": 20, "x1": 300, "y1": 520}})
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(jav_poster_crop.sidecar_path(self.cover).exists())


if __name__ == "__main__":
    unittest.main()
