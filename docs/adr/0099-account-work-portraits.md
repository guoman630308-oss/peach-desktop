# ADR-0099：个人博主按两部作品互证自动截脸，其余账号用代表作画面占位

- 状态：Accepted
- 日期：2026-10-09
- 修订：ADR-0095 的作品头像判据（`work_portrait_predicate`）
- 相关：ADR-0040、ADR-0053、ADR-0074

## 背景

发布账号（`creator`）的作品画面是否代表账号本人，由 `entity_classification.work_portrait_predicate`
判定：账号须有可信的 `identity=person` 或出演类职业断言（`adult_performer`、`model`、`actor`），
且没有卖家、厂牌、动画作者或机构类断言。判据成立的是个人博主，其余是只发布他人作品的账号。

补头像后继只派女优，个人博主有作品也装不上头像。不过判据的账号在名册、资料页、口味榜与统计里
一律不显示作品帧，圆框只剩首字母。`TokyoDolls`（745 部作品，分类只有 `identity=account`）
就属于这种情况。

博主作品大多没有番号。`western_artwork.artwork_key` 给它们的键是 `ASSET-ID-<资产 id>`，
封面目录里一般没有这张图；画面来源是接触印相（3×3 九格，与 `/poster` 同一网格）。

## 决策

**一、补头像后继派给个人博主。**`avatar_followup.KINDS` 包含 `creator`。新登记与存量补派都只取
过判据的账号，跑的时候再判一次：派出之后分类被改、不再过判据时结论是「账号没有本人身份依据」，
这一次不写 `Attempts`。

**二、博主不查图库，只从自己的作品画面截脸。**作品按 `role='creator'` 取，同一作品挂着别的发布
账号就不取（`avatar_cover_face.single_works`）。每部作品把接触印相切成九格，有封面的作品封面
也算一格，逐格检脸，留脸最宽、截出来还检得出脸的那一格（`sheet_faces`）。截法沿用
`face_square` 与 `readable_cut`。一条后继最多看 24 部作品。

**三、两部不同作品的脸互相过线才装。**判据复用 ADR-0074 第四条：比截出来的方图，余弦落在
`COSINE_THRESHOLD` 与 `NEAR_DUPLICATE` 之间才算互证（`avatar_offsite_cover_face.agreeing`）。一部作品
只出一张脸，同一部片的两格不算两份证据。情侣号、多人号的两部作品截出不同的人时一张都不装，
截到的脸留进候选缓存，挑图弹层里可以点。不另设分类排除情侣号。

**四、装上的与封面截脸同一档。**来源记录 `provider: cover-face`、`identity_verified: false`，
带 `sheet_cell` 与互证证据（作品键、每一对的余弦）。之后有人挑的图或更宽的脸，照样换掉它。

**五、指纹按印相数作品。**博主的作品数只数铺过接触印相的作品，后补印相会让账号重新入列。女优的
指纹不变，规则版本保持 6。

**六、过不了判据的账号用代表作画面占位，并标明不是本人。**`web_catalog.attach_avatar_availability`
照常按印相判 `has_avatar`；账号不过判据时另下发 `avatar_stand_in: true`。资料页、索引、搜索、
口味榜与统计都从这一处取标志。前端在代表作头像打头的圆图上加 `title` 与 `aria-label`
「代表作画面，非本人」，不加徽章。已装实体图的圆图不加。

**七、挑图弹层照列作品画面。**这类账号的作品格说明以「代表作画面，非本人」开头。人挑哪一张由
用户决定；作品画面装上的来源记录写 `identity_verified: false`。

## 后果

- 有两部以上独占作品、印相里截得出同一张脸的个人博主，会自动装上头像。
- `TokyoDolls` 这类只有账号身份的条目，圆框显示代表作画面，悬停可见「代表作画面，非本人」。
  它们不会派补头像后继。
- 卡片署名、Mix 叠放头像与沉浸作者那一格读的是顶栏代表作表（`representatives.ts`），表里
  只有女优与厂牌，不带 `avatar_stand_in`。
