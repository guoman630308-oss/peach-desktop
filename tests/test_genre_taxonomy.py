import unittest

from peach import catalog_rules
from peach.genre_taxonomy import (
    CONTENT_GENRES,
    NON_CONTENT_GENRES,
    NON_CONTENT_PATTERNS,
    PROFILE_GENRES,
    UNMAPPED,
    genres_in_warning,
    is_non_content_genre,
    map_genres,
    normalise_genre,
    resolve_genre,
    resolve_profile_tag,
    unmapped_genre_warning,
)
from peach.taste_history import TASTE_CATEGORY_TAGS
from opencc import OpenCC


class SimplifiedSourceGenreTests(unittest.TestCase):
    def test_every_registered_chinese_spelling_has_the_same_simplified_projection(self):
        import re
        converter = OpenCC('t2s')
        for raw in CONTENT_GENRES:
            if re.search(r'[ぁ-ヿ]', raw):
                continue
            with self.subTest(raw=raw):
                self.assertEqual(resolve_genre(converter.convert(raw)), resolve_genre(raw))
        for raw in NON_CONTENT_GENRES:
            if re.search(r'[ぁ-ヿ]', raw):
                continue
            with self.subTest(excluded=raw):
                self.assertIsNone(resolve_genre(converter.convert(raw)))

    def test_simplified_sources_keep_user_overrides_and_unknown_words(self):
        self.assertEqual(map_genres(['护士', '業餘', '业余', '无码破解', '4小时以上作品', '未知类别']),
                         (['护士', '素人'], ['未知类别']))
        self.assertEqual(resolve_genre('护士', {'护士': '职业角色'}), '职业角色')


def _catalog_vocabulary() -> set[str]:
    names = (
        "LENGTH_TAGS", "TECH_TAGS", "ATTRIBUTE_TAGS", "RELATIONSHIP_TAGS",
        "ROLE_TAGS", "APPEARANCE_TAGS", "SCENE_TAGS", "STORY_TAGS", "POSITION_TAGS",
    )
    vocabulary: set[str] = set()
    for name in names:
        vocabulary |= set(getattr(catalog_rules, name))
    return vocabulary


class GenreTaxonomyTests(unittest.TestCase):
    def test_office_role_and_uniform_keep_separate_dimensions(self):
        tags, unknown = map_genres(["OL", "Secretary", "Office Lady", "スーツ", "Business Suit"])
        self.assertEqual(tags, ["秘书OL", "OL制服"])
        self.assertEqual(unknown, [])
        self.assertEqual(catalog_rules.current_tags(["OL", "秘书OL", "OL制服"]),
                         ["秘书OL", "OL制服"])
        self.assertEqual(catalog_rules.tag_cat("秘书OL"), "role")
        self.assertEqual(catalog_rules.tag_cat("OL制服"), "appearance")
        self.assertEqual(catalog_rules.tag_cat("JK制服"), "appearance")

    def test_cat_ears_share_one_target_across_source_spellings(self):
        self.assertEqual(map_genres(["猫耳", "貓耳女"]), (["猫耳"], []))

    def test_public_javdb_simplified_categories_share_verified_targets(self):
        self.assertEqual(map_genres(['蕩婦', '荡妇', '戀腿癖', '恋腿癖']), (['痴女', '恋腿'], []))

    def test_anal_region_is_distinct_from_anal_intercourse(self):
        for raw in ("Anal", "Anal Play", "アナル", "アナル責め", "アナルシリーズ", "肛門・肛交"):
            with self.subTest(raw=raw):
                self.assertEqual(resolve_genre(raw), "屁眼")
        for raw in ("Anal Sex", "アナルセックス", "アナルファック", "アナル中出し"):
            with self.subTest(raw=raw):
                self.assertEqual(resolve_genre(raw), "肛交")

    def test_javdb_categories_have_explicit_existing_vocabulary_targets(self):
        tags, unknown = map_genres(["高跟鞋", "絲襪、過膝襪", "白天出軌", "女大學生",
                                    "第一人稱攝影", "4小時以上作品", "單體作品", "尚未收錄的分類"])
        self.assertEqual(tags, ["高跟", "丝袜", "出轨", "学生", "主观视角"])
        self.assertEqual(unknown, ["尚未收錄的分類"])
        self.assertEqual(resolve_genre("靴子"), "靴子")

    def test_javdb_aliases_share_the_existing_semantic_targets(self):
        aliases = (
            ("溫泉", "Hot Spring", "温泉"),
            ("女同性戀", "Lesbian", "百合"),
            ("女同接吻", "レズキス", "百合"),
            ("強姦", "レイプ", "强制剧情"),
            ("偷窺", "盗撮", "偷拍偷窥"),
            ("處女", "Virgin", "处女设定"),
            ("爛醉如泥的", "泥酔", "醉酒"),
            ("黑人演員", "黒人男優", "黑人"),
            ("家教", "Private Tutor", "家庭教师"),
            ("童年朋友", "幼なじみ", "青梅竹马"),
            ("變性者", "Transsexual", "跨性别"),
            ("肉肉女", "Chubby", "丰满"),
            ("水手服", "Sailor Uniform", "制服"),
            ("學校泳裝", "School Swimsuits", "泳装"),
            ("手指插入", "Fingering", "手交"),
            ("手淫", "Masturbation", "自慰"),
            ("輪姦", "Gangbang", "多人"),
            ("兩男兩女", "Foursome", "3P多人"),
            ("打屁股", "Spanking", "调教"),
            ("灌腸", "Enema", "排泄"),
            ("假陽具", "Dildo", "性玩具"),
            ("肛交", "Anal Sex", "肛交"),
            ("薄馬賽克", "ギリモザ", "薄码"),
            ("數位馬賽克", "Digital Mosaic", "有码"),
        )
        for chinese, existing, target in aliases:
            with self.subTest(chinese=chinese, existing=existing):
                tags, unknown = map_genres([chinese, existing, chinese])
                self.assertEqual(tags, [target])
                self.assertEqual(unknown, [])

    def test_javdb_aliases_keep_distinct_or_unsupported_meanings_unmapped(self):
        raw = ["口爆/吞精", "車站性交", "天賦", "觸摸打字", "養尊處優",
               "婆婆", "女主播", "女檢察官"]
        tags, unknown = map_genres(raw)
        self.assertEqual(tags, [])
        self.assertEqual(unknown, raw)

    def test_confirmed_javdb_translations_preserve_semantic_dimensions(self):
        raw = ["靴子", "高跟鞋", "濕身", "濕透", "流汗", "飲尿", "尿失禁",
               "妓女", "風俗嬢", "風俗", "雙性人", "變性者", "母親", "亂倫",
               "女醫生", "護士", "Female Investigator", "戀腿癖", "美腳", "戀乳癖", "美乳",
               "子宮頸", "正太控", "瘦小身型"]
        tags, unknown = map_genres(raw)
        self.assertEqual(tags, ["靴子", "高跟", "湿身", "汗湿", "饮尿", "放尿",
                                "风俗女郎", "风俗店", "扶她", "跨性别", "母亲角色", "近亲",
                                "女医生", "护士", "女搜查官", "恋腿", "美腿", "恋乳", "美乳",
                                "宫颈", "正太控", "娇小"])
        self.assertEqual(unknown, [])

    def test_javdb_category_link_identities_share_chinese_and_english_targets(self):
        for chinese, english, target in (
            ("雙性人", "Futanari", "扶她"), ("妓女", "Prostitutes", "风俗女郎"),
            ("戀腿癖", "Leg Fetish", "恋腿"), ("戀乳癖", "Busty Fetish", "恋乳"),
            ("蠻橫嬌羞", "Tsundere", "傲娇"), ("子宮頸", "Cervix", "宫颈"),
        ):
            with self.subTest(chinese=chinese):
                self.assertEqual(map_genres([chinese, english, chinese]), ([target], []))

    def test_javdb_source_version_labels_do_not_assert_local_file_attributes(self):
        self.assertEqual(map_genres(["無碼流出", "無碼破解", "HDTV", "共演", "店長推薦"]), ([], []))
        self.assertEqual(map_genres(["無修正", "肛交", "女醫生"]), (["无码", "肛交", "女医生"], []))

    def test_confirmed_tags_have_filter_groups(self):
        for group, names in (
            (catalog_rules.ROLE_TAGS, ["风俗女郎", "扶她", "女医生", "母亲角色"]),
            (catalog_rules.STORY_TAGS, ["恋腿", "恋乳", "正太控"]),
            (catalog_rules.APPEARANCE_TAGS, ["靴子", "湿身"]),
            (catalog_rules.POSITION_TAGS, ["饮尿", "宫颈"]),
        ):
            with self.subTest(names=names):
                self.assertTrue(set(names) <= group)

    def test_javdb_aliases_preserve_scene_behavior_and_anal_boundaries(self):
        tags, unknown = map_genres(["戶外", "Exhibitionism", "洗澡", "溫泉",
                                    "手淫", "手指插入", "Anal", "肛交", "肛門・肛交"])
        self.assertEqual(tags, ["户外", "户外露出", "浴室", "温泉", "自慰", "手交", "屁眼", "肛交"])
        self.assertEqual(unknown, [])

    def test_every_projection_lands_in_the_existing_catalog_vocabulary(self):
        """投影只能落在既有词表上。

        映射表凭翻译造一个新标签不会报错，只会让这个标签在筛选面板、同义词
        取代和统计里全部缺席——写进 ledger 后才发现，就已经是一次真实写入。
        """
        vocabulary = _catalog_vocabulary()
        unknown = sorted({tag for tag in CONTENT_GENRES.values() if tag not in vocabulary})
        self.assertEqual(unknown, [], "这些标签不在 catalog_rules 词表里，先决定收录还是改投影")

    def test_japanese_and_english_keys_share_one_table_and_one_value_space(self):
        """javbus/javdb 给日文类型词，r18dev 给英文；两套键共用一张表、一套值。

        身体尺寸与发行／营销／规格分类一概不进 `CONTENT_GENRES`，和既有的
        「`Featured Actress` 不入库」是同一条线。
        """
        self.assertEqual(CONTENT_GENRES["中出し"], "中出内射")
        self.assertEqual(CONTENT_GENRES["パイパン"], "白虎")
        self.assertEqual(CONTENT_GENRES["女子校生"], "学生")
        self.assertEqual(CONTENT_GENRES["寝取り・寝取られ"], "绿帽NTR")
        self.assertEqual(CONTENT_GENRES["巨乳"], "巨乳")
        self.assertEqual(CONTENT_GENRES["美乳"], "美乳")
        for measurement in ("Dカップ", "Gカップ", "Jカップ"):
            self.assertNotIn(measurement, CONTENT_GENRES,
                             f"{measurement} 是身体尺寸，词表里没有对应分类")
        for marketing in ("独占配信", "配信専用", "単体作品", "企画", "店長推薦作品",
                          "ハイビジョン", "フルハイビジョン(FHD)", "1080p", "60fps",
                          "4時間以上作品", "AV女優"):
            self.assertNotIn(marketing, CONTENT_GENRES,
                             f"{marketing} 是发行/营销/规格分类，不是内容标签")
        self.assertTrue({key for key in CONTENT_GENRES if not key.isascii()})

    def test_no_genre_maps_into_a_dropped_tag(self):
        """`乳系`、`足系` 这种粗桶已经撤掉，来源说到哪一级就取哪一级。

        映进撤掉的标签，等于写入一个账本清理时会整条删掉的值。`おっぱい` 只说到
        「胸」这一级，没有对应的具体标签，所以它归非内容，不投进哪个乳型。
        """
        offenders = sorted(
            f"{source} -> {mapped}"
            for table in (CONTENT_GENRES, PROFILE_GENRES)
            for source, mapped in table.items()
            if set(mapped if isinstance(mapped, tuple) else (mapped,)) & catalog_rules.DROPPED_TAGS)
        self.assertEqual(offenders, [], "这些映射落进了撤掉的粗桶")
        self.assertEqual(map_genres(["おっぱい"]), ([], []))

    def test_no_genre_maps_into_a_retired_name(self):
        """退役名在账本里会被改成规范名，投影表再产出它就是每轮抓取都留一次旧写法。"""
        offenders = sorted(
            f"{source} -> {mapped}"
            for table in (CONTENT_GENRES, PROFILE_GENRES)
            for source, mapped in table.items()
            if set(mapped if isinstance(mapped, tuple) else (mapped,)) & set(catalog_rules.RETIRED_TAGS))
        self.assertEqual(offenders, [], "这些映射落进了退役名")

    def test_content_and_non_content_tables_do_not_overlap(self):
        both = {normalise_genre(key) for key in CONTENT_GENRES} & {
            normalise_genre(value) for value in NON_CONTENT_GENRES
        }
        self.assertEqual(both, set(), "同一个 genre 不能既是内容又被排除")

    def test_case_and_width_variants_hit_the_same_entry(self):
        self.assertEqual(map_genres(["Deep Throat"])[0], ["深喉"])
        self.assertEqual(map_genres(["deep throat"])[0], ["深喉"])
        self.assertEqual(map_genres(["ＤＥＥＰ　ＴＨＲＯＡＴ"])[0], ["深喉"])
        self.assertEqual(map_genres(["３Ｐ"])[0], ["3P多人"])

    def test_japanese_and_english_sources_project_to_one_tag(self):
        """dmm/mgstage 只给日文，r18dev 只给英文；两边必须落到同一个标签。

        这正是官方 tag 长期缺口的成因：抓取脚本各带一份英文表，日文官方来源
        排在 policy 第一位却一个标签也产不出来。
        """
        self.assertEqual(map_genres(["Creampie"])[0], map_genres(["中出し"])[0])
        self.assertEqual(map_genres(["Blowjob"])[0], map_genres(["フェラチオ"])[0])
        self.assertEqual(map_genres(["Big Tits"])[0], map_genres(["巨乳"])[0])
        # 胸的大小只有一档：来源之间 `Big Tits` 与 `Huge Tits` 的边界各说各的。
        self.assertEqual(map_genres(["Huge Tits"])[0], map_genres(["巨乳"])[0])
        self.assertEqual(map_genres(["Lunch Box Fuck"])[0], map_genres(["駅弁"])[0])

    def test_marketing_and_format_categories_are_excluded_not_missing(self):
        tags, unmapped = map_genres(["AV女優", "単体作品", "サンプル動画", "ハイビジョン"])
        self.assertEqual(tags, [])
        self.assertEqual(unmapped, [], "已判定的非内容分类不该再当成待补条目")
        self.assertTrue(is_non_content_genre("Featured Actress"))

    def test_unrecognised_genres_come_back_for_registration(self):
        tags, unmapped = map_genres(["Creampie", "まだ知らない分類", "Creampie"])
        self.assertEqual(tags, ["中出内射"])
        self.assertEqual(unmapped, ["まだ知らない分類"], "未收录不等于非内容，必须回传登记")

    def test_order_is_first_appearance_and_duplicates_collapse(self):
        tags, _ = map_genres(["巨乳", "Creampie", "Big Tits", "中出し"])
        self.assertEqual(tags, ["巨乳", "中出内射"])

    def test_campaign_names_are_excluded_by_shape_not_by_enumeration(self):
        """促销企划名逐片起，穷举一轮就过期一轮。

        实测样本：プレステージ20周年特別企画、春のBIGセール、
        プレステージグループ秋の企画祭り、プレステージ40％オフセール。
        """
        for value in ("プレステージ20周年特別企画", "春のBIGセール",
                      "プレステージグループ秋の企画祭り", "プレステージ40％オフセール",
                      "BIG Sale Part 2", "Summer BIG Sale",
                      "Adult Summer Campaign", "MOODYZ Fan Campaign"):
            self.assertTrue(is_non_content_genre(value), value)
        self.assertEqual(map_genres(["中出し", "春のBIGセール"]), (["中出内射"], []))
        # 形状判据不能误伤内容分类。
        for value in ("中出し", "巨乳", "コスプレ", "痴女"):
            self.assertFalse(is_non_content_genre(value), value)

    def test_abw_220_reproduces_the_mgs_product_page(self):
        """本轮的起点样本：r18dev 只给三个泛化类别，MGS 商品页给的是这些。"""
        tags, unmapped = map_genres([
            "MGS限定特典映像", "性教育", "中出し", "巨乳", "スレンダー",
            "単体作品", "フルハイビジョン(FHD)", "プレステージ20周年特別企画",
        ])
        # `性教育` 是题材类，用户复核后收进词表，不再算待决。
        self.assertEqual(tags, ["性教育", "中出内射", "巨乳", "苗条"])
        self.assertEqual(unmapped, [])

    def test_traditional_nfo_spellings_share_the_same_taxonomy(self):
        tags, unmapped = map_genres([
            "主觀視角", "苗條", "單體作品", "MGSだけのおまけ映像付き",
            "フルハイビジョン(FHD)",
        ])
        self.assertEqual(tags, ["主观视角", "苗条"])
        self.assertEqual(unmapped, [])

    def test_non_content_patterns_stay_narrow(self):
        # 每条形状判据都要说得出它为什么必然是卖法而不是内容。
        self.assertEqual(len(NON_CONTENT_PATTERNS), 6)

    def test_no_pattern_carries_a_control_character(self):
        """`\\b` 被 shell 吃掉一层就成了退格符，正则照样编译、照样静默不匹配。

        源码看上去完全正常，代价是「Summer BIG Sale」这类词一路走到复核页上等人判。
        """
        for pattern in NON_CONTENT_PATTERNS:
            with self.subTest(pattern=pattern.pattern):
                self.assertEqual(
                    [char for char in pattern.pattern if ord(char) < 32], [])

    def test_uncensored_source_vocabularies_project_too(self):
        """caribbeancom 与 javbus 的词表和 dmm/mgstage 不一样，同样要投影。"""
        carib, carib_left = map_genres([
            "オリジナル動画", "美乳", "中出し", "パイパン", "オナニー",
            "手コキ", "69", "クンニ", "初裏", "スレンダー"])
        self.assertEqual(carib, ["美乳", "中出内射", "白虎", "自慰", "手交", "69",
                                 "舔阴", "初次无码", "苗条"])
        self.assertEqual(carib_left, [])
        heyzo, heyzo_left = map_genres([
            "中出し", "潮吹き", "淫語", "騎乗位", "口内発射", "看護婦", "指マン"])
        self.assertEqual(heyzo, ["中出内射", "潮吹", "淫语", "骑乘", "口爆", "护士", "手交"])
        self.assertEqual(heyzo_left, [])
        # javbus 会混进画质与演员编成，同样按非内容排除。
        self.assertTrue(all(is_non_content_genre(v)
                            for v in ("1080p", "60fps", "AV女優", "超VIP", "オリジナル動画")))


class VocabularyHygieneTests(unittest.TestCase):
    """一件事只留一个标签名，译名取通行写法。"""

    def test_no_retired_name_survives_anywhere(self):
        """退役名留在词表或投影表里，等于这次改名只改了一半。

        口味维度那张表也要查：它按标签名取资产，名字改过之后那一条只是不再命中，
        没有任何报错——`足系` 维度会安静地少掉一整类。
        """
        vocabulary = _catalog_vocabulary()
        self.assertEqual(sorted(set(catalog_rules.RETIRED_TAGS) & vocabulary), [])
        self.assertEqual(
            sorted({tag for tag in CONTENT_GENRES.values()
                    if tag in catalog_rules.RETIRED_TAGS}), [])
        taste = {tag for tags in TASTE_CATEGORY_TAGS.values() for tag in tags}
        self.assertEqual(sorted(taste & set(catalog_rules.RETIRED_TAGS)), [])
        self.assertEqual(sorted(taste - vocabulary), [], "口味维度只能按词表里真有的标签取资产")

    def test_every_retired_name_points_at_a_live_tag(self):
        """改名的目标必须是词表里真有的标签，否则改完那批资产就剩一个没人认得的名字。"""
        vocabulary = _catalog_vocabulary()
        self.assertEqual(
            sorted(set(catalog_rules.RETIRED_TAGS.values()) - vocabulary), [])
        self.assertEqual(
            sorted(set(catalog_rules.RETIRED_TAGS) & set(catalog_rules.RETIRED_TAGS.values())),
            [], "改名不能接力：一步到位，脚本才能重复执行")

    def test_an_english_word_is_judged_the_same_as_its_japanese_original(self):
        """同一个来源分类的两种写法必须同去向，否则英文那份就一路走到复核页上。

        候选文件停在抓取那一刻：日文原词是这之后才开始取的，旧队列里冻着的仍是英文。
        """
        for japanese, english in (("企画", "Variety"), ("盗撮・のぞき", "Peeping"),
                                  ("美脚", "Legs"), ("足フェチ", "Foot Fetish"),
                                  ("家庭教師", "Private Tutor")):
            with self.subTest(genre=english):
                self.assertEqual(map_genres([english]), map_genres([japanese]))

    def test_a_foot_fetish_genre_is_not_a_pair_of_nice_legs(self):
        """`Foot Fetish` 说的是恋足，不是腿好看。

        两者曾同投 `美腿`，于是「找恋足题材」和「找美腿出镜」在检索上分不开。
        """
        self.assertEqual(map_genres(["Foot Fetish"])[0], ["恋足"])
        self.assertEqual(map_genres(["足フェチ"])[0], ["恋足"])
        self.assertEqual(map_genres(["Legs"])[0], ["美腿"])
        self.assertEqual(map_genres(["美脚"])[0], ["美腿"])

    def test_the_name_the_library_actually_uses_wins(self):
        """规范名取馆藏里通行的写法，不取词表里先写下的那个。

        `合集` 3699 条、全部来自文件名；`混合集` 313 条、全部来自已关停的 Stash 导入。
        """
        self.assertEqual(map_genres(["総集編"])[0], ["合集"])
        self.assertEqual(map_genres(["ベスト・総集編"])[0], ["合集"])
        self.assertEqual(map_genres(["Compilation"])[0], ["合集"])
        self.assertEqual(map_genres(["淫語"])[0], ["淫语"])
        self.assertEqual(map_genres(["Dirty Talk"])[0], ["淫语"])

    def test_a_source_that_only_publishes_english_still_reaches_one_tag(self):
        """k-mib、aventertainments、javdb 的类目在来源页上就是英文，没有日文原词可换。

        重抓换不回日文，所以这几个词只能靠表里直接收录。同一件事的两种写法要落在
        一个标签上，否则筛选面板会摆出两行同义标签。
        """
        for english, japanese, tag in (
            ("Kiss", "キス", "接吻"),
            ("Ahegao", "アヘ顔", "阿黑颜"),
            ("Femdom", "女王様", "女性主导"),
            ("Tiny Girl", "小柄", "娇小"),
            ("Oil", "ローション・オイル", "油压"),
            ("Sitting on Face", "顔面騎乗", "颜面骑乘"),
            ("Beautiful Skin", "美肌", "美肌"),
        ):
            self.assertEqual(map_genres([english])[0], [tag])
            self.assertEqual(map_genres([japanese])[0], [tag])

    def test_every_toy_name_collapses_into_one_tag(self):
        """`バイブ` 是震动棒、`ローター` 是跳蛋、`電マ` 是电动按摩棒，各压一到五条。

        分成三个标签得到的是三个筛不动的稀标签，筛选面板上多三行、检索上分不开。
        """
        for word in ("Sex Toy", "Sex Toys", "大人のおもちゃ", "おもちゃ",
                     "Vibrator", "Big Vibrator", "Egg Vibrator",
                     "バイブ", "ローター", "電マ"):
            self.assertEqual(map_genres([word])[0], ["性玩具"], word)

    def test_a_catch_all_bucket_is_excluded_not_registered(self):
        """来源的兜底格说的是「归不进上面任何一格」，不是某种内容。"""
        for word in ("Fetish", "フェチ", "その他フェチ", "Other Fetishes",
                     "Various Professions", "職業色々"):
            self.assertTrue(is_non_content_genre(word), word)
        # 服装与职业细到这一档时，单独立标签只能压住一两部片。
        for word in ("ジーンズ", "短パン", "店員", "買い物"):
            self.assertTrue(is_non_content_genre(word), word)

    def test_two_source_words_for_one_thing_land_on_one_tag(self):
        """同义的来源词各投一个标签，就是页面上那两行重复的来处。"""
        self.assertEqual(map_genres(["Peeping"])[0], map_genres(["Voyeur"])[0])
        self.assertEqual(map_genres(["High Heels"])[0], ["高跟"])
        # 家教和校内老师是两件事，中文里「老师」把它们盖在一起。
        self.assertEqual(map_genres(["家庭教師"])[0], ["家庭教师"])
        self.assertEqual(map_genres(["女教師"])[0], ["教师"])

    def test_every_spelling_a_source_uses_reaches_the_same_tag(self):
        """同一个含义在各来源写法不同：英日、连写分写、片假名平假名。

        `normalise_genre` 只折叠大小写、全半角和空白，`Doggystyle` 与 `Doggy Style`、
        `おもちゃ` 与 `オモチャ` 在它眼里仍是两个键，词表里得各登记一次。
        """
        for spellings, tag in (
            (("Doggystyle", "Doggy Style", "Doggy", "バック", "後背位"), "后入"),
            (("Deep Throat", "Deepthroat", "Irrumatio", "イラマチオ"), "深喉"),
            (("Handjob", "Hand Job", "Fingering", "指マン", "手マン"), "手交"),
            (("Titty Fuck", "Tit Fuck", "Paizuri", "パイズリ"), "乳交"),
            (("Squirting", "Squirt", "潮吹き"), "潮吹"),
            (("Threesome", "Foursome", "3P・4P", "3P/4P", "4P"), "3P多人"),
            (("Orgy", "Gangbang", "Gang Bang", "Group Sex", "乱交"), "多人"),
            (("Sex Toy", "Toys", "Dildo", "おもちゃ", "オモチャ", "Pinkrotor", "ピンクローター"), "性玩具"),
            (("Facesitting", "Face Sitting", "顔面騎乗"), "颜面骑乘"),
            (("Big Tits", "Big Boobs", "Busty", "巨乳"), "巨乳"),
            (("Small Tits", "Tiny Tits", "Flat Chest", "貧乳"), "贫乳"),
            (("Slender", "Slim", "Skinny", "細身", "スレンダー"), "苗条"),
            (("Tiny Girl", "Petite", "小柄", "ミニマム"), "娇小"),
            (("Tall Girl", "Tall", "長身", "高身長", "高身長グラマラス"), "高个"),
            (("Pantyhose", "パンスト", "タイツ", "ストッキング", "網タイツ"), "丝袜"),
            (("Kimono", "Yukata", "着物", "浴衣", "和服・浴衣"), "和服浴衣"),
            (("Twintails", "Pigtails", "ツインテール"), "双马尾"),
            (("Married Woman", "Housewife", "Wife", "主婦", "人妻"), "人妻"),
            (("School Girls", "Schoolgirl", "Student", "女学生", "女子校生"), "学生"),
            (("Nurse", "看護婦", "看護師", "ナース"), "护士"),
            (("Stewardess", "Flight Attendant", "キャビンアテンダント"), "空姐"),
            (("Bath", "Shower", "Bathroom", "お風呂", "シャワー", "入浴"), "浴室"),
            (("School", "Classroom", "学校", "教室", "学園もの"), "教室学校"),
            (("Hotel", "ホテル"), "酒店"),
            (("Office", "オフィス"), "办公室"),
            (("Bondage", "Shibari", "Tied Up", "緊縛", "縛り"), "捆绑"),
            (("Cuckold", "Netorare", "寝取り", "寝取られ"), "绿帽NTR"),
            (("Cheating Wife", "Cheating", "不倫", "浮気"), "出轨"),
            (("Voyeur", "盗撮", "盗撮・のぞき"), "偷拍偷窥"),
            (("Uncensored", "無修正"), "无码"),
            (("Virtual Reality", "VR", "VR専用"), "VR"),
        ):
            for spelling in spellings:
                with self.subTest(spelling=spelling):
                    self.assertEqual(map_genres([spelling])[0], [tag])

    def test_a_debut_is_filed_with_the_first_shoot(self):
        """`デビュー作品`／`Debut` 是厂牌说的「第一次上镜」，和素人系列的 `初撮り` 同一格。"""
        for word in ("初撮り", "デビュー作品", "Debut"):
            self.assertEqual(map_genres([word])[0], ["初拍"], word)

    def test_fame_and_mood_words_are_excluded_not_registered(self):
        """`Famous Name` 说的是演员名气，`Hardcore Fuck`、`Scream for Joy` 是氛围词。

        `Choker`、`Kawaii Fashion` 和 `ジーンズ` 同档：比词表细一级的穿搭。
        """
        for word in ("Famous Name", "Hardcore Fuck", "Scream for Joy",
                     "Choker", "Kawaii Fashion"):
            self.assertTrue(is_non_content_genre(word), word)
        self.assertEqual(map_genres(["Famous Name", "中出し"]), (["中出内射"], []))

    def test_the_newly_localised_words_reach_their_tag(self):
        """本机候选里出现过、之前没有中文标签的那批，现在各有去处。

        这些原文此前逐轮回到复核页问同一个问题，而复核页给的答案只落在
        `genre_decision` 里——换一台机器、换一份候选又要再答一遍。
        """
        for spellings, tag in (
            (("風俗", "ソープ", "Soapland", "デリヘル"), "风俗店"),
            (("温泉", "Hot Spring"), "温泉"),
            (("金髪・ブロンド", "Blonde", "金髪", "ブロンド"), "金发"),
            (("Short Hair", "ショートヘア", "短髪"), "短发"),
            (("スポーツ", "Sports", "アスリート"), "运动"),
            (("童貞", "Virgin Boy"), "处男"),
            (("処女", "Virgin"), "处女设定"),
            (("汗だく", "Sweaty", "汗"), "汗湿"),
            (("泥酔", "Drunk", "酔っ払い"), "醉酒"),
            (("ドラッグ", "媚薬", "Aphrodisiac"), "药物"),
            (("局部アップ", "Close Up", "Close-Up"), "局部特写"),
            (("Nice Pussy", "Beautiful Pussy"), "美穴"),
            (("Glamorous Body", "グラマー"), "丰满"),
            (("ドキュメンタリー", "Documentary"), "纪录片"),
            (("イメージビデオ", "Image Video", "グラビア"), "写真映像"),
            (("痴漢", "Molester", "Groping"), "痴汉"),
            (("妊婦", "妊娠", "Pregnant"), "孕妇"),
            (("巨根", "Big Cock", "デカチン"), "巨根"),
            (("放尿", "Peeing", "おしっこ"), "放尿"),
            (("黒人", "Black Guy"), "黑人"),
        ):
            for spelling in spellings:
                with self.subTest(spelling=spelling):
                    self.assertEqual(map_genres([spelling])[0], [tag])

    def test_the_first_time_is_told_apart_by_whose_first_time_it_is(self):
        """`童貞` 说男方、`処女` 说女方；英文 `Virgin` 单说时讲的是女方。

        两边合成一个「第一次」标签，馆藏里就再也问不出想找的是哪一种。
        """
        self.assertEqual(map_genres(["童貞", "処女"])[0], ["处男", "处女设定"])
        self.assertEqual(map_genres(["Virgin"])[0], ["处女设定"])
        self.assertEqual(map_genres(["Virgin Boy"])[0], ["处男"])

    def test_words_whose_meaning_is_not_settled_stay_on_the_review_page(self):
        """含义还没查清的原文留给人判，不按字面猜一个标签。

        `Inter` 只在 SMBD-110 上出现，是站方截断的半个词，既可能是
        `Interracial` 也可能是 `Interview`。猜哪一个都会写出一条错标签。
        """
        for word in ("Inter",):
            with self.subTest(word=word):
                self.assertEqual(resolve_genre(word), UNMAPPED)


class Fc2SellerTagTests(unittest.TestCase):
    """FC2 卖家自填的标签：花样拆得细、从标题截词，还混着发行说法和夸法。"""

    def test_the_variants_of_one_act_land_on_its_tag(self):
        """口交的站姿、骑姿、不用手各写一格，说的仍是口交；事后舔干净的另归一格。"""
        for spellings, tag in (
            (("フェラ抜き", "仁王立ちフェラ", "ノーハンドフェラ",
              "馬乗りフェラ", "Ｗフェラ", "玉舐め", "裏筋舐め", "口淫"), "口交"),
            (("お掃除フェラ", "お掃除", "掃除"), "打扫口交"),
            (("舌上発射", "舌上射精"), "口爆"),
            (("精飲",), "吞精"),
            (("挟射", "馬乗りパイズリ", "着衣パイズリ"), "乳交"),
            (("2回射精", "大量射精", "大量発射", "ザーメン発射"), "射精"),
            (("精子を搾り取", "搾り取", "ザーメン搾り取", "精液搾り"), "榨精"),
            (("濃厚中出し", "膣内射精", "生セックス", "生SEX", "ザーメン注入"), "中出内射"),
            (("アナルファック", "AF", "アナル中出し"), "肛交"),
            (("アナル舐め",), "毒龙"),
            (("亀頭責め",), "龟头责"),
            (("正常位",), "传教士"),
            (("ピストン", "ガン突き"), "打桩"),
            (("腋コキ", "わき"), "腋"),
            (("オナホ", "ローター責め", "コックリング", "アナルプラグ"), "性玩具"),
        ):
            for spelling in spellings:
                with self.subTest(spelling=spelling):
                    self.assertEqual(map_genres([spelling]), ([tag], []))

    def test_spelling_variants_and_costumes_reach_the_existing_tags(self):
        """假名、省写与扮装各有既有的一格，不另开标签。"""
        for spellings, tag in (
            (("ぱいぱん",), "白虎"),
            (("スジマン",), "美穴"),
            (("猫耳",), "猫耳"),
            (("セーラー",), "制服"),
            (("体操着", "体操服"), "体操服"),
            (("バニー",), "兔女郎"),
            (("競泳水着",), "泳装"),
            (("ボンテージ", "エナメル"), "皮衣皮裙"),
            (("コス", "コスチューム", "サキュバス"), "角色扮演"),
            (("メンズエステ", "メンエス"), "按摩"),
            (("隠し撮り",), "偷拍偷窥"),
            (("他人棒",), "绿帽NTR"),
            (("奥様",), "人妻"),
            (("無", "モ無し"), "无码"),
        ):
            for spelling in spellings:
                with self.subTest(spelling=spelling):
                    self.assertEqual(map_genres([spelling]), ([tag], []))

    def test_a_gravure_idol_is_an_idol_on_a_work_and_a_trade_on_a_profile(self):
        """同一个 `グラドル`：作品上说出镜的是谁，资料页上说她做过的行业。"""
        self.assertEqual(map_genres(["グラドル"])[0], ["偶像艺人"])
        self.assertEqual(resolve_profile_tag("グラドル"), ("写真偶像",))

    def test_release_wording_and_praise_are_excluded_not_registered(self):
        """谁拍的、公开过没有、有多好，都不说拍了什么。"""
        for word in ("個人撮影", "個撮", "オリジナル", "未公開", "限定", "長編", "VIP",
                     "セックス", "SEX", "エッチ", "セクシー", "極上", "イキまくり",
                     "Wピース", "ゴム", "着衣", "しょうなちゃん", "ＦＧ○"):
            with self.subTest(word=word):
                self.assertTrue(is_non_content_genre(word), word)
        self.assertEqual(map_genres(["個人撮影", "オリジナル", "お掃除フェラ"]), (["打扫口交"], []))

    def test_words_the_catalog_has_no_slot_for_stay_on_the_review_page(self):
        """词表里没有对应一格、或者含义还没查清的词，留给人判。

        `神乳` 剥掉夸法只剩「乳」这一级，和 `おっぱい` 同理；`孕ませ`、`排卵`、`種付け` 说的是
        受孕相关题材，不等同于 `孕妇` 表达的怀孕状态；`Iカップ` 是尺寸；`ハイレグ` 既可能是
        泳装也可能是体操服；`モザ` 在一部已判无码的片子上出现，是半个词。
        """
        for word in ("神乳", "孕ませ", "排卵", "種付け", "Iカップ", "ハイレグ", "モザ"):
            with self.subTest(word=word):
                self.assertEqual(resolve_genre(word), UNMAPPED)


class ResolveGenreTests(unittest.TestCase):
    """抓取与复核折叠候选走同一个查表函数。"""

    def test_the_three_outcomes_are_told_apart(self):
        self.assertEqual(resolve_genre("中出し"), "中出内射")
        self.assertIsNone(resolve_genre("単体作品"), "非内容是结论，不是未收录")
        self.assertEqual(resolve_genre("まだ知らない分類"), UNMAPPED)
        self.assertIsNone(resolve_genre(""), "空值没有可判的东西")

    def test_a_decision_outranks_both_static_tables(self):
        decisions = {normalise_genre("中出し"): "内射体験", normalise_genre("巨乳"): None}
        self.assertEqual(resolve_genre("中出し", decisions), "内射体験")
        self.assertIsNone(resolve_genre("巨乳", decisions))


class UserDecisionTests(unittest.TestCase):
    """复核页收录下来的那批，和静态表一起参与查表。"""

    def test_a_recorded_genre_stops_coming_back_as_unmapped(self):
        decisions = {normalise_genre("Famous Name"): "有名女优"}
        tags, unmapped = map_genres(["中出し", "Famous Name", "Inter"], decisions)
        self.assertEqual(tags, ["中出内射", "有名女优"])
        self.assertEqual(unmapped, ["Inter"], "还没决定的那个仍要回来问")

    def test_a_genre_judged_non_content_is_excluded_not_asked_again(self):
        tags, unmapped = map_genres(["Inter", "巨乳"],
                                    {normalise_genre("Inter"): None})
        self.assertEqual(tags, ["巨乳"])
        self.assertEqual(unmapped, [], "排除也是结论；再问一遍等于没记下来")

    def test_the_written_form_does_not_split_one_decision_into_several(self):
        # 键已规范化，所以全角、半角和大小写写法命中同一条决定。
        decisions = {normalise_genre("Kiss"): "接吻"}
        self.assertEqual(map_genres(["ＫＩＳＳ"], decisions)[0], ["接吻"])
        self.assertEqual(map_genres([" kiss "], decisions)[0], ["接吻"])

    def test_a_decision_outranks_both_static_tables(self):
        """用户刚说过的话不该被发版时写下的默认盖掉。"""
        self.assertEqual(map_genres(["中出し"], {normalise_genre("中出し"): "内射"})[0], ["内射"])
        # `単体作品` 静态表判为非内容；用户改主意就该按他说的收录。
        self.assertEqual(map_genres(["単体作品"], {normalise_genre("単体作品"): "单体作品"})[0],
                         ["单体作品"])

    def test_the_warning_line_can_be_read_back_into_the_words(self):
        """2026-09-11 之前的候选文件只有那句话，复核页要能在它们身上继续收录。"""
        line = unmapped_genre_warning(["69", "初裏"])
        self.assertEqual(line, "来源还有 2 个未收录 genre：69、初裏")
        self.assertEqual(genres_in_warning(line), ["69", "初裏"])
        self.assertEqual(genres_in_warning("来源值含重复片段，已规范化：A → B"), [])
        self.assertEqual(genres_in_warning(""), [])


class LooksTests(unittest.TestCase):
    """颜值只在说的确实是两回事时才分开。"""

    def test_beauty_words_that_mean_the_same_share_one_tag(self):
        for word in ("美人", "美少女", "美女", "美顔", "Beautiful Girl", "Pretty Face"):
            with self.subTest(word=word):
                self.assertEqual(map_genres([word])[0], ["高颜值"])

    def test_seiso_and_cute_are_their_own_tags(self):
        """`清楚` 说的是气质干净、不张扬，不是长得好看；`可愛い` 是另一种好看。"""
        for word in ("清楚", "清楚系", "清純", "Neat and Clean"):
            with self.subTest(word=word):
                self.assertEqual(map_genres([word])[0], ["清纯"])
        for word in ("可愛い", "かわいい", "Cute"):
            with self.subTest(word=word):
                self.assertEqual(map_genres([word])[0], ["可爱"])


class ProfileTagTests(unittest.TestCase):
    """资料页的标签先查资料专用表，再落回作品的表。"""

    def test_each_trade_keeps_its_own_name(self):
        self.assertEqual(resolve_profile_tag("現役デリヘル嬢"), ("上门服务女郎",))
        self.assertEqual(resolve_profile_tag("ソープ嬢"), ("泡泡浴女郎",))
        self.assertEqual(resolve_profile_tag("セクシーパブ嬢"), ("性感酒吧女郎",))
        self.assertEqual(resolve_profile_tag("風俗嬢"), ("风俗从业",))
        # 作品上的 `看護師` 是角色扮演，资料上是她的本职。
        self.assertEqual(resolve_profile_tag("現役看護師"), ("现役护士",))

    def test_a_style_change_keeps_both_ends_of_the_arrow(self):
        self.assertEqual(resolve_profile_tag("ロリ→ギャル"), ("萝莉→辣妹",))
        self.assertEqual(resolve_profile_tag("微乳→巨乳"), ("贫乳→巨乳",))

    def test_a_cell_holding_several_tags_is_split(self):
        self.assertEqual(resolve_profile_tag("ショートカット、美乳、レズ"), ("短发", "美乳", "百合"))
        self.assertEqual(resolve_profile_tag("清楚お嬢様系"), ("清纯", "千金小姐"))

    def test_labels_and_career_notes_are_not_tags(self):
        for word in ("カリビアン", "改名・移籍", "引退", "セクシー", "女優", ""):
            with self.subTest(word=word):
                self.assertEqual(resolve_profile_tag(word), ())

    def test_everything_else_falls_back_to_the_work_table(self):
        self.assertEqual(resolve_profile_tag("巨尻"), ("巨臀",))
        self.assertEqual(resolve_profile_tag("単体作品"), ())
        self.assertIsNone(resolve_profile_tag("まだ知らない分類"), "未收录要让调用方照原文列")

    def test_a_decision_outranks_the_profile_table(self):
        decisions = {normalise_genre("風俗嬢"): "风俗", normalise_genre("カリビアン"): None}
        self.assertEqual(resolve_profile_tag("風俗嬢", decisions), ("风俗",))
        self.assertEqual(resolve_profile_tag("カリビアン", decisions), ())

    def test_the_profile_table_never_overlaps_the_exclusions(self):
        excluded = {normalise_genre(value) for value in NON_CONTENT_GENRES}
        self.assertEqual(sorted(key for key in PROFILE_GENRES
                                if normalise_genre(key) in excluded), [])

    def test_an_r18dev_name_goes_where_its_dmm_category_goes(self):
        """r18dev 的英文名是 DMM 分类的译名，去向跟同一分类的日文名一致：收录、排除还是未收录。

        对照取自 r18.dev 作品 JSON 的 `categories`（`docs/reference-snapshots/r18dev-genre-categories.md`）。
        收录了日文名却漏了英文名，r18dev 快照里的这个分类就一个标签都投不出来。
        """
        same_category = {
            "Asian Actress": "アジア女優", "Big Asses": "巨尻", "Breast Milk": "母乳",
            "Business Suit": "ビジネススーツ", "Caucasian Actress": "白人女優", "Cherry Boy": "童貞",
            "Club Hostess & Sex Worker": "キャバ嬢・風俗嬢", "Club Manager": "部活・マネージャー",
            "Confessional": "体験告白", "Confinement": "監禁", "Cross Dresser": "女装・男の娘",
            "Daydreamers": "妄想族", "Drinking Party/Mixer": "飲み会・合コン", "Embarrassment": "辱め",
            "Employee/Coworker": "部下・同僚", "Enema": "浣腸", "Female Boss": "女上司",
            "Female Detective": "女捜査官", "Flexible body": "軟体", "Foot Fetish": "脚フェチ",
            "G-Spot": "ポルチオ", "Genital Close-Up": "局部アップ", "Hard Sex": "鬼畜",
            "Hospital/Clinic": "病院・クリニック", "Hostess": "コンパニオン",
            "Huge Dick - Large Dick": "デカチン・巨根", "Kiss Kiss": "キス・接吻", "Light Skin": "色白",
            "Lookalike": "そっくりさん", "Love": "恋愛", "M-jo": "M女", "Masochist Man": "M男",
            "Masturbation Support": "オナサポ", "Miniskirt": "ミニスカ", "Muscular": "筋肉",
            "Non-nude Erotica": "着エロ", "Object Insertion": "異物挿入", "Old Playmates": "幼なじみ",
            "Pranks": "イタズラ", "Premature Ejaculation": "早漏", "Queen": "女王様", "Quickie": "即ハメ",
            "Race Queen": "レースクィーン", "S******n": "ショタ", "Shame": "羞恥",
            "Shemale": "ニューハーフ", "Sister": "姉・妹", "Soapland Girl": "ヘルス・ソープ",
            "Stepfamily": "近親相姦", "Stepmom": "義母", "Substance Use": "ドラッグ", "Sweating": "汗だく",
            "Tight Dress": "ボディコン", "Urination": "放尿・お漏らし", "Various Worker": "職業色々",
            "Youthful": "ミニ系",
        }
        apart = {english: (resolve_genre(english), resolve_genre(japanese))
                 for english, japanese in same_category.items()
                 if resolve_genre(english) != resolve_genre(japanese)}
        self.assertEqual(apart, {})
        self.assertEqual(sorted(english for english in same_category
                                if resolve_genre(english) == UNMAPPED), [])

    def test_a_race_queen_is_one_tag_in_either_kana_spelling(self):
        # DMM 写小字 `ィ`，FC2 卖家写 `イ`；NFKC 不折叠，两种写法各登记一次。
        self.assertEqual(resolve_genre("レースクィーン"), "赛车女郎")
        self.assertEqual(resolve_genre("レースクイーン"), "赛车女郎")

    def test_dressing_as_a_girl_is_told_apart_from_being_transgender(self):
        self.assertEqual(resolve_genre("女装・男の娘"), "伪娘")
        self.assertEqual(resolve_genre("ニューハーフ"), "跨性别")


if __name__ == "__main__":
    unittest.main()
