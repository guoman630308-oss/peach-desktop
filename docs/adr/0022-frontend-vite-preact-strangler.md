# ADR-0022：前端改用 Vite + TypeScript + Preact，按页面绞杀式迁移

- 状态：Superseded by ADR-0031（绞杀式迁移、产物入库、`/dist/` 路由与单一测试入口仍有效）
- 日期：2026-09-02
- 关系：细化 ADR-0001 的「独立 web 表面」；替代 AGENTS.md 中「不做 React 重写」的字面约束（结论不变：不整体重写）；ADR-0014 的 Video.js 保留。

## 背景

`web/app.js` 是一份 6.7k 行、无构建、无类型的单文件，`app.css` 2.4k 行；页面切换靠 25 分支的 `if` 链，四份卡片模板各写一遍，`let` 状态被前置函数引用只是因为运行时机恰好没踩到 TDZ。行为契约由 `tests/test_web_ui.py` 里约 1600 条源码字符串断言锁住，改一处实现要改一批测试。

这些代价在个人项目里可以忍。项目即将开源为通用的自托管单人媒体应用，之后维护者不止一个，每个页面还会长出更多状态（追更、复核、播放列表、批处理面板）。没有类型和组件模型的话，新贡献者的每次改动都要读整份文件才能确认没有把别的页面弄坏。

曾评估但不采用的方案：

- 继续 vanilla 只拆 ES modules：解决文件大小，不解决状态管理和类型；测试仍只能对源码字符串断言。
- Preact + `htm` 无构建：拿到组件模型，但没有类型检查，没有 tree-shaking，模板字符串没有编辑器支持。
- React / Vue 全量重写：违反「只有替代实现测试通过后才删旧代码」，且一次性迁 6.7k 行代码没有可验收的中间态。
- StyleX（Linear 的方案）：面向多团队、多主题、多平台的样式治理，Peach 只有一套 CSS 变量和两套主题，收益不抵构建链复杂度。

## 决策

- 前端新代码一律用 **Vite + TypeScript（strict）+ Preact + `@preact/signals`**，源码放在 `frontend/`，单测用 vitest。
- **绞杀式迁移（strangler）**：旧的 `web/app.js` 继续拥有壳、导航与全部未迁移页面。每个页面被重写为一个 Preact「岛」，通过 `frontend/src/islands.ts` 导出的 `mountIsland(name, el, props)` / `unmountIsland(el)` 契约由旧路由挂载；旧页面的渲染函数在岛通过页面源测试与 vitest 后立即删除，不保留双实现。
- 迁移顺序：先小而自包含的页面（单个 GET、无共享 DOM），再实体、追更、播放器；最后把壳与路由本体迁进去并删除 `web/app.js`。每次迁移一到两个页面，作为独立分支集成。
- **构建产物入库**：`npm run build` 输出到 `web/dist/`，文件名固定不带哈希，提交到 Git。Python 服务、PyInstaller 打包和 macOS 只读端运行时不需要 Node；CI 里 `git diff --exit-code web/dist` 阻止源码与产物漂移。
- **单一测试入口不变**：`scripts/test.ps1` / `scripts/test.sh` 新增 `web` 域，Node 存在时跑 vitest，缺失时显式跳过并说明；`full` 包含它。
- 服务端新增 `/dist/{path}` 静态路由，与 `/js/{name}` 同一套路径遍历、扩展名和缓存头检查；不引入 CDN。
- 现有手工 vendored 的 video.js / swiper / lucide 暂不改；等对应页面迁移时再决定是否改为 npm 依赖经 Vite 打包。
- 直接依赖数量要最小，每个依赖在 [docs/FRONTEND.md](../FRONTEND.md) 登记一句用途；版本精确锁定并提交 lockfile。
- 测试契约从「源码字符串」转向「用户可见文案、`data-*`、`aria-*`、路由与 API 路径」；纯函数（路由匹配、时长格式、标签显示名、回退链解析）先抽成 `web/js/*.js` 模块，供 vitest 直接测试。

## 后果

- 贡献者需要 Node 24+ 才能改前端；只改后端仍然不需要。
- `web/dist/` 产物 diff 会出现在前端 PR 里，审阅时看 `frontend/src/` 即可。
- 迁移期内存在两套渲染方式，边界由岛契约固定；每个页面只能属于其中一套。
- `tests/test_web_ui.py` 在页面迁走时相应删减；不允许为了保留断言而保留旧渲染函数。

## 验收门槛

- 每个迁移分支：目标页面在桌面与 390×844 下功能等价、`-Scope web` 与 `full` 全绿、`web/dist/` 与源码一致。
- 迁移完成的定义：`web/app.js` 删除，`index.html` 只挂载一个入口，AGENTS.md 与 README 的前端章节只描述 `frontend/`。
