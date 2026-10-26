---
name: peach-jav-cover-workflow
description: 在用户说 JAV 封面、高清封面、缺封面、封面刮削、重探、来源比较或继续抓取时使用。
---

最后复核：2026-10-18
证据来源：`scripts/fetch_jav_covers.py`、`scripts/audit_cover_artwork.py`、`tests/test_jav_covers.py`、`tests/test_jav_poster_crop.py` 与 ADR-0107。

# JAV 封面获取流程

目标是取得可证明属于当前番号的最大官方封套。封面是可重建产物，不写 ledger；
`cover-fetch-log.csv` 是来源与尺寸证据，必须保留。

## 起跑前

1. 读 [运行与配置](../../../docs/OPERATIONS.md) 与 `docs/REUSE.md` 的 JAV 封面条目。
2. 检查是否已有 `fetch_jav_covers.py` 进程；不得重复起跑。
3. 运行只读盘点，记录番号、已解码、缺失、损坏和宽度分布：

   ```powershell
   & .\.venv\Scripts\python.exe .\scripts\fetch_jav_covers.py --audit
   ```

## 候选来源

所有候选都量尺寸，不设「某主机永远优先」的固定链：

1. 先离线复用 `sources/metadata/javinizer-go/<番号>/*.json` 的 `cover_url` 与
   `content_id`，不要为已有成功快照重复请求元数据站。
2. 从 DMM URL 生成并实测 modern `awsimgsrc.dmm.com/dig/...`、legacy
   `awsimgsrc.dmm.co.jp/pics_dig/...` 和原始 URL；覆盖 `digital/video`、
   `digital/amateur`、`mono/movie`。
3. 有 Prestige 厂牌证据时，再直连 MGS `EnlargeImage` 与 Prestige
   `api/search` → `api/product` → `packageImage`。
4. 把上轮成功日志的精确 URL 加回候选，保住已发现的 DUGA 等图片。
5. AVBase 被 Cloudflare 拦截时放弃，不绕过。DUGA 批量搜索没有代理店应用 ID 时不调用。
6. Amazon 日本只有完成真实 POC、匹配无歧义并登记来源后才进候选；4K/8K 水印或放大图不算原始高清封面。

坏例：按主机名直接覆盖 1000×674 的 DUGA 图，换成 800×539 的 DMM 图。
好例：Range 读取图片头，校验可解码和宽度门槛，按像素面积选最大者。

## 下载与替换门槛

- 宽度低于 700、无法解码、缩略图、剧照、关联作品图都拒绝。
- Range 最多先取 64 KiB 量尺寸，胜出后才下载完整图片。
- 正式封面按实际像素面积择优，同尺寸优先商品图；JavDB 图片只作核验，不安装或显示兜底。
- 单条网络异常写失败并换连接池，任务继续；确认无候选与瞬时失败分开记录。
- `DiskGuard` 运行期守住系统盘；每条完成后重写可续跑日志。

## 取景 sidecar

新封面写入后补算人脸，否则页面只能用写死的锚点：

```powershell
& .\.venv\Scripts\python.exe .\scripts\detect_cover_faces.py
```

- 算法版本与尺寸匹配的记录默认跳过，过期记录自动重算；`--redo` 全量重算，未检出时页面居中。
- sidecar 的 `cx` 和 `cy` 都要留着。用哪个轴由容器比例决定：`object-fit:cover`
  一次只裁一个轴，16:9 官方剧照在大图版式里裁的是横向，纵向锚点在那里不生效。
- 版式判据（1.2 / 1.65 两个分界）的真相在 `peach.jav_poster_crop`，`frontend/src/card-art/framing.ts` 的 `coverAnchor`
  另有一份，改一处必须改两处，否则脚本按封套丢掉左半边的脸、页面却按剧照用那张脸取景。

大图卡片要的是另一样东西：书脊折痕在哪，同目录写 `<番号>.poster.json`：

```powershell
& .\.venv\Scripts\python.exe .\scripts\poster_crop_boxes.py
& .\.venv\Scripts\python.exe .\scripts\poster_crop_boxes.py --apply
```

- 判据在 `peach.jav_poster_crop`：Sobel 列梯度找书脊折痕，HEYZO／FC2／六位日期／韩国 MIB
  不裁；16:9 只认满高拼接缝（覆盖 ≥75% 行）夹出的居中正封（PASN 等），原图不动。
  DVD 比例的居中正封还须双侧接缝满高，且两侧纹理均低于正封四分之一；无书脊的 4:3、√2:1 画布保留整幅。
- **折痕按「切出来的正封形状对不对」认，不按它落在全宽的百分之几**。正封宽高比是印刷面的物理常数
  （DVD 135×190mm ＝ 0.711）；折痕在全宽里的位置随背面留白与书脊厚度飘。
  只在 `PANEL_ASPECT_MIN`～`PANEL_ASPECT_MAX` 窗口里
  找峭壁，窗外再强的边是画面内容，按它切会削掉一截大标题：KBI-036 的背面分栏线与正封内一道边恰好关于中线对称。
- **窗里够强的边不止一条时按形状挑，不按谁更强**（`FOLD_RIVAL_RATIO`，到窗内最强边七成的
  都算候选，相邻列归并成一条边）。厚书脊两条边都在窗里，左边那条挨着封底留白常更强，按它切
  整条书脊留在框里。ABP-968 的厚书脊是对应回归样例。
- **峰是斜坡最陡的那一列，不是斜坡尽头**。书脊最后一两列还压在峰的右边，按峰切正封左缘留一条
  竖线。选定后往右走到梯度落回窗内中位数（`FOLD_SETTLE_LIMIT`，最多源图宽 1%），基线每张各算。
- **切点不满高、右边窗内还有满高缝时正封从缝的下一列起**（`_seam_beyond`，只向右、不再走斜坡）。厚书脊里
  竖排文字的边和折痕一样陡，ABW-147 按形状仍选中文字边；文字边不满高，折痕从上贯到下。切点自身满高不动
  （正封边框也满高），书脊左缘也满高所以不往左看；TKM-005 正封左侧黑边跟着切掉是已知代价。
- **缝也没有时看孤立尖峰**（`_sharp_beyond`）：书脊与正封同底色的封套，折痕只在书脊内容的行里存在，是周围中位数
  3 倍以上、左比右忙的窄峰（IPZ-602）；正封内容的起笔正相反（PPT-018）。ABP-627 的低对比折痕是已知边界。
- 框就是正封本身：`x0` 是折痕列，右下角就是源图右下角。框里不另取子区域，因为卡片的
  容器比例只有页面知道，在这一侧先按某个形状切一刀，只会把正封两侧各削掉一圈。
- 默认只算边车缺失、版本落后或尺寸对不上的封面，重探换大图后再跑即重算；`--redo` 才全算。
  改了判据要进 `ALGORITHM_VERSION`，重算完再重启服务，否则旧服务整批拒收新边车、全站框消失。
- 页面只用框里的 `x0`：`frontend/src/card-art/framing.ts` 的 `posterPanel` 把它换算成 `--panel-clip` 与
  `--panel-left`，图片按卡片高度铺满、`clip-path` 切掉折痕以左的封底，正封居中摆。
- **正封容器比例定在 0.75**：一行卡片等高，正封完整居中，比例差异处保留卡片纯色底。
  整幅大封在竖版按主脸填满、横版完整显示，均不使用模糊补边（ADR-0107）。
- 找不到折痕的按 `PANEL_ASPECT` 从右缘量回去，记 `ratio`；形状仍是正封的物理比例。
- **宽封套由厂牌开门、比例决定走不走**（`WIDE_SLEEVE_LABELS`：CWPBD、SMBD）。Blu-ray 模板
  750×419 与 mgstage 16:9 剧照只差 1%，比例与满高缝无法单独区分两者；
  厂牌在名单且比例在 1.75～1.85 才按正封 0.85 先验在 0.83～0.87 窄窗找折痕，DVD 比例照旧走上一档。

## 正式批次

先重探所有缺失，包括旧的确认落空：

```powershell
& .\.venv\Scripts\python.exe .\scripts\fetch_jav_covers.py --retry-misses
```

再重探宽度低于 1200 的已有封面；只会升级，不会降级：

```powershell
& .\.venv\Scripts\python.exe .\scripts\fetch_jav_covers.py --upgrade-existing --upgrade-max-width 1199
```

## 收尾

1. 再跑 `--audit`，报告前后缺失、损坏、尺寸分布、取得、保留与失败。
2. 抽查新增来源 URL 与本地图片尺寸，确认番号精确匹配。
3. 代码变更跑 `& .\scripts\test.ps1 -Scope metadata`；跨域时跑 full。
4. 分开报告代码/测试、真实封面产物、生产服务和 ledger；本流程正常不重启、不写 ledger。
