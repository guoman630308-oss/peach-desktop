# r18dev 英文 genre 与 DMM 日文 genre 的对照证据

- 来源：r18.dev 作品 JSON `https://r18.dev/videos/vod/movies/detail/-/combined=<content_id>/json`
  的 `categories` 字段，每项同时给出 DMM 分类编号 `id`、`name_en`、`name_ja` 与
  `name_en_is_machine_translation`；下表各项该字段都是 `false`。
- 取得日期：2026-09-28
- 取证方式：Aside 浏览器（Claude in Chrome）打开上面的地址后同源 `fetch`，
  每次请求间隔 1.2 秒，共 39 部作品。Python `urllib` 直连返回 HTTP 403，
  内置浏览器面板导航被拒；本机 javinizer-go 快照（`sources/metadata/javinizer-go/<番号>/r18dev.json`）
  只存 `genres` 的英文名，不带编号与日文名。
- 本文不登记进 `docs/reference-sources.json`：这是一次性的只读取证，没有需要跟踪漂移的上游原文。
- 用法：英文名跟着同一编号的日文名走，`genre_taxonomy` 里两个名字登记同一去向。

## 已取得

| name_en | id | name_ja | 取自 content_id |
| --- | --- | --- | --- |
| Asian Actress | 2011 | アジア女優 | asia00091 |
| Big Asses | 2024 | 巨尻 | 1nhdtb00455 |
| Breast Milk | 5060 | 母乳 | mism00236 |
| Business Suit | 6939 | ビジネススーツ | 1nhdtb00530 |
| Caucasian Actress | 2012 | 白人女優 | 1stars00457 |
| Cherry Boy | 4014 | 童貞 | cawd00241 |
| Club Hostess & Sex Worker | 1036 | キャバ嬢・風俗嬢 | dpmx00016 |
| Club Manager | 6961 | 部活・マネージャー | 1stars00419 |
| Confessional | 6086 | 体験告白 | 433gun00851 |
| Confinement | 5010 | 監禁 | ipz00522 |
| Cross Dresser | 3036 | 女装・男の娘 | wanz00972 |
| Daydreamers | 6995 | 妄想族 | focs00272 |
| Drinking Party/Mixer | 6957 | 飲み会・合コン | cemd00765 |
| Embarrassment | 27 | 辱め | focs00272 |
| Employee/Coworker | 6946 | 部下・同僚 | 1fset00847 |
| Enema | 5014 | 浣腸 | gvh00424 |
| Female Boss | 6945 | 女上司 | 1fset00847 |
| Female Detective | 2022 | 女捜査官 | mide00890 |
| Flexible body | 6935 | 軟体 | focs00272 |
| Foot Fetish | 4008 | 脚フェチ | 39 部之一，未记番号；本地 DPMI-008 的 r18dev 与 javbus 快照同时给出这两个名字 |
| G-Spot | 6108 | ポルチオ | ipx00663 |
| Genital Close-Up | 4017 | 局部アップ | arm00935 |
| Hard Sex | 567 | 鬼畜 | 1nhdtb00455 |
| Hospital/Clinic | 6963 | 病院・クリニック | 1fsdss00259 |
| Hostess | 1041 | コンパニオン | h_1074fnk00029 |
| Huge Dick - Large Dick | 5073 | デカチン・巨根 | dvdms00623 |
| Kiss Kiss | 4059 | キス・接吻 | 1fset00847 |
| Light Skin | 8513 | 色白 | mfc0135 |
| Lookalike | 1030 | そっくりさん | mukc00016 |
| Love | 555 | 恋愛 | sqte00362 |
| M-jo | 6967 | M女 | huntb00317 |
| Masochist Man | 5074 | M男 | 1kire00038 |
| Masturbation Support | 6938 | オナサポ | midv00751 |
| Miniskirt | 3007 | ミニスカ | dpmi00046 |
| Muscular | 97 | 筋肉 | 118har00006 |
| Non-nude Erotica | 4116 | 着エロ | dpmx00016 |
| Object Insertion | 72 | 異物挿入 | gvh00424 |
| Old Playmates | 1083 | 幼なじみ | 1stars00439 |
| Pranks | 4122 | イタズラ | gvh276 |
| Premature Ejaculation | 5072 | 早漏 | waaa00067 |
| Queen | 6944 | 女王様 | gvh00304 |
| Quickie | 3029 | 即ハメ | 1nhdtb00455 |
| Race Queen | 1011 | レースクィーン | dpmi00067 |
| S******n | 4058 | ショタ | gvh276 |
| Shame | 28 | 羞恥 | 1nhdtb00530 |
| Shemale | 4015 | ニューハーフ | wanz00972 |
| Sister | 4057 | 姉・妹 | 1fsdss00260 |
| Soapland Girl | 6937 | ヘルス・ソープ | huntb00317 |
| Stepfamily | 4002 | 近親相姦 | gvh00424 |
| Stepmom | 524 | 義母 | dass00468 |
| Substance Use | 5015 | ドラッグ | vrtm390 |
| Sweating | 5075 | 汗だく | arm00935 |
| Tight Dress | 3013 | ボディコン | dpmi00046 |
| Urination | 5011 | 放尿・お漏らし | cjod00158 |
| Various Worker | 1026 | 職業色々 | vrtm390 |
| Youthful | 2008 | ミニ系 | dasd00321 |

## 未取得

- `D***k Girl`（id 4121，取自 miad00812）：r18.dev 的 `name_ja` 是 `null`，对不上 DMM 日文名。
  词表按打码字形读作 `Drunk Girl` 归醉酒，这是推断，不是对照证据。
