# transitions.dev 动效配方实测记录

- 取证日期：2026-09-16
- URL：<https://transitions.dev/>，逐条配方在 `https://transitions.dev/transitions/<slug>`
- 取证方式：打开每条配方页的 CSS 标签页，读它给出的 `:root` 变量与 `@keyframes` 数值。
  页面上每条都有 CSS 与 React 两个标签页，React 那份只是把同一段 CSS 包一层组件，
  数值一致，所以只记 CSS 那份。
- **不在 `docs/reference-sources.json` 里登记**：那张表的契约是「每个来源都有可哈希的
  上游快照」。这里取的是一页上多条配方的参数，页面由站点自己的组件渲染、随时可改，
  给不出可锁定的单文件哈希。要复核就按上面的方式重开配方页读一遍 CSS 标签页。
- 许可：上游仓库没有 LICENSE 文件，页面只写「copy and paste them」，没有给出明确授权
  条款。因此 Peach 只借形态与参数，类名与变量名一律自拟：不使用它的 `t-*` 类名，
  也不使用它的 `:root` 变量名。落地代码在 `frontend/src/ui-kit/motion.css`、`web/css/25-motion.css` 与 `web/board.css`。
- **未取得**：上游各条配方所依据的设计原始稿、参数取值理由和许可条款均未取得；
  上游没有公布 spring 参数，它的「弹」一律由带超调控制点的 `cubic-bezier` 近似。

## 逐条对照

时长与缓动一律并进 Peach 已有的动效 token：`--board-motion`（.3s cubic-bezier(.4,0,.2,1)）、
`--spring-press`（208ms 采样弹簧，峰值 1.088）、`--motion-swap`（.25s）、
`--motion-count`（.25s cubic-bezier(.34,1.45,.64,1)）、`--motion-reveal`（.4s）、
`--motion-stagger`（70ms），全部由 `board.css` 那一条
`prefers-reduced-motion` 规则统一归零。

| 配方 | slug | 上游取值 | Peach 落成值 | 落点 |
| --- | --- | --- | --- | --- |
| 字形换字形 | `icon-swap` | 250ms；`ease-in-out`；blur 2px；scale .25 | `--motion-swap`（.25s cubic-bezier(.4,0,.2,1)）；blur 2px；scale .25 | `.iconswap`；`iconSwapHtml`／`setIconSwap` |
| 一行字换一行字 | `text-states` | 150ms；`ease-in-out`；blur 2px；位移 4px | `--motion-swap`；blur 2px；位移 4px | `.textswap`；`swapText` |
| 读数按位跳出 | `number-pop-in` | 500ms；`cubic-bezier(0.34,1.45,0.64,1)`；blur 2px；位移 8px；按位延迟 70ms | `--motion-count`（.25s + 同一条超调缓动）；blur 2px；位移 8px；`--motion-stagger` 70ms | `.digits`；`popCount` |
| 占位换真内容 | `skeleton-loader` | 400ms；`ease-in-out`；blur 2px | `--motion-reveal`（.4s cubic-bezier(.4,0,.2,1)）；blur 2px | `.skelreveal`／`.skelfade`；`revealSkeleton` |
| 开关双弹 | `toggle-double-bounce` | 350ms；`cubic-bezier(0.34,1.35,0.64,1)`；行程 14.66px；超出端点 1px | `--spring-press`（208ms，峰值 1.088，行程 18px，超出约 1.6px） | `.ptoggle::after`／`::before` |
| 成功打勾 | `success-check` | 四段各 500ms；rotate 80deg；blur 10px；上荡 40px；`stroke-dasharray` 20；画笔延迟 80ms | `--board-motion` 走转正与清晰、`--spring-press` 走上荡 14px；rotate 80deg；blur 10px；`stroke-dasharray` 24；画笔延迟 `--motion-stagger` | `.checkdraw`；toast 图标 |
| 失败抖动 | `error-shake` | 280ms；`cubic-bezier(0.22,1,0.36,1)`；关键帧 0／28.57％／57.14％／78.57％／100％，位移 0／6px／-6px／4px／0 | `--board-motion`；同样的五个关键帧与位移 | `@keyframes field-shake`；`[aria-invalid="true"]` |

## 与上游取值不同的几处判断

- 开关双弹不写关键帧。上游用一条超调缓动模拟弹簧，Peach 已经有采样自真实弹簧的
  `--spring-press`，峰值 1.088 折到 18px 行程上就是约 1.6px 的超出，手感与上游那条
  1px 超出同档，所以直接让 thumb 的 `transform` 走这条弹簧。
- 成功打勾的上荡距离取 14px 而不是 40px：这枚勾长在 toast 的图标位上，只有 16px 见方，
  40px 会把它荡出 toast 的边界。`stroke-dasharray` 取 24 是 Peach 这枚 Lucide 勾的
  实际折线长度，不是上游那枚字形的 20。
- 读数与文字的换态只在值真的变了时才走，首次落笔不动。上游示例每次渲染都播一遍，
  在 Peach 这是首页每次筛选都要重画的读数，首屏会变成一片抖动。
- 骨架换内容时旧的那一层抬成绝对定位再淡出，容器高度全程由新内容决定；上游示例里
  两块内容尺寸相同，不需要处理这一步。

## 第二批（2026-09-17）

- 取证方式同上，另加一条：`https://transitions.dev/transitions/<slug>` 会 301 到同一
  地址带斜杠的那一份，配方的 CSS 与 React 两份全文都在那一页的 HTML 里，不在 JS 包里。
- 这一批新增一档 token `--motion-pop`（.4s cubic-bezier(.34,1.36,.64,1)），和当时已有五档
  住在 `board.css` 同一个 `:root`、由同一条 `prefers-reduced-motion` 规则归零。
  它单独成档是因为缓动：`animation` 里只能有一条缓动函数，token 已经带着一条，
  再写第二条整条声明就无效，动画会被整个丢掉。
- 2026-09-19 在线复核 `input-clear-with-dissolve`：出场仍为 400ms、向下位移 12px、blur 2px，
  缓动仍为 `cubic-bezier(.22,1,.36,1)`；Peach 保留向上溶解且不做逐词扫光的既有差异。

| 配方 | slug | 上游取值 | Peach 落成值 | 落点 |
| --- | --- | --- | --- | --- |
| 一排头像里抬起一枚 | `avatar-group-hover` | 320ms；进 `cubic-bezier(0.22,1,0.36,1)`、出 `cubic-bezier(0.34,3.85,0.64,1)`；抬 -4px；放大 1.05；衰减 .45 | `--board-motion`（.3s）走抬起、`--spring-press`（208ms 采样弹簧）走落回；抬 -4px；放大 1.05；共演头像最多五枚，常态相邻压 22px，悬停或焦点时后续四枚向右 10／20／30／40px，左缘保留放大与 2px 环空间，均为 Peach 自拟 | `.tier`／`.followauthors`／`.followworks` 的 `.av`、`.brandpill`；卡片上的 `.mavstack .mav` |
| 清空时内容溶解 | `input-clear-with-dissolve` | 出场 400ms；`cubic-bezier(0.22,1,0.36,1)`；位移 12px；blur 2px；另有逐词 radial-gradient 扫光 | `--motion-reveal`（.4s）；位移 12px；blur 2px；不做扫光；复制输入框 padding、字体与横滚位置 | `.cleardissolve`；`dissolveValue`；`#q` 的程序清空、原生清空键、整段删除与第二次 Escape |
| 计数徽标弹出 | `notification-badge` | 弹入 500ms `cubic-bezier(0.34,1.36,0.64,1)`、淡入 400ms；blur 2px；scale 0→1；另有 260ms 的 (-8.2px, 12.4px) 滑入 | `--motion-pop`（.4s + 同一条超调缓动）；blur 2px；scale 0→1；不做滑入 | `.countbadge`；`popBadges`；筛选条与抽屉的 `.n`、垃圾文件分类计数 |
| 标题逐行揭示 | `texts-reveal` | 500ms；`cubic-bezier(0.22,1,0.36,1)`；位移 12px；blur 3px；行距 40ms | `--motion-reveal`（.4s）；位移 12px；blur 3px；行距 `--motion-stagger` 70ms | `.revealline`；`revealTexts`；详情浮窗标题、管理区页面标题、设置面板页签标题 |

### 与上游取值不同的几处判断

- 抬头像的进出两条缓动由整排的 `:has(:hover)` 切换，不写 JS。上游那份要逐个元素写内联
  `transitionTimingFunction` 和 `--shift`，因为它按索引距离算衰减；Peach 这几排最多
  三档就衰减到 .36px（看不出来），选择器直接写到两位即可，省掉一整套监听与重新绑定。
  落回那条不用上游的 `cubic-bezier(0.34,3.85,0.64,1)`，改用站内已有的 `--spring-press`，
  理由与第一批开关双弹同一条：已经有采样自真实弹簧的那一档，不再另起一条近似。
- 横向推开是 Peach 自己加的，上游没有这一项。它只对最多五枚的叠放组成立：常态相邻两枚
  压着 22px，指到或聚焦一枚时只把它右边的头像按 10px 阶梯向标题方向展开，左边的头像和
  整组左缘不动。首枚的真实内缩同时容纳 1.05 倍放大和 2px 环，窄卡只显示前两枚；横滚的
  头像排本来就有间距，不横向推开。悬停与键盘焦点所在头像都升到最高层。
- 清空不做逐词扫光。上游自己在注释里写明那道光的起落曲线无法用静态关键帧表达、必须逐帧写
  `background`；换来的是一道在深浅两个主题下都几乎看不出来的光，搜索框每清一次跑一段
  逐帧 JS 不值当。溶解层是照着输入框当下的位置和字体摆的一份复制品，输入框的 value 在
  动画开始前就已经清空，光标与输入法不等这 400ms。原生搜索清空键、非空到空的整段删除
  与菜单已经关闭后的 Escape 都走同一入口；继续输入会取消残影，输入法开始组字时也先取消，
  不让旧字盖住候选字。
- 徽标不做滑入。上游那枚锚在铃铛图标的右上角，滑入是从图标中心飞到角上；Peach 这几枚
  是行内跟在标签后面的计数，没有「从哪飞来」的起点，硬加一段位移只会让整行字跟着晃。
- 徽标只在值真的变了时弹，判据存在模块级的一张表里，不看节点是不是新建的：这几排每换
  一个筛选都整块重画，按节点判等于每次筛选整列计数一起弹。
- 标题按行揭示，不按词。上游示例本身就是按行（`.t-stagger-line`），逐词是首页那一版的
  做法；Peach 这几处正是最常被复制走的几段字（作品名、页面名），拆成一串 `<span>`
  会让复制出来的文本散架。
- 揭示走完把类名一并摘掉。终点帧留着 `filter:blur(0)` 的话，非 none 的 filter 会另起一个
  backdrop root，标题块里任何 backdrop-filter 从此只采样得到它自己——和第一批骨架那条
  用 `backwards` 不用 `both` 是同一个理由。
- BoardUI 的
  Input（<https://www.boardui.com/components/input>，2026-09-17）没有清空键，Geist 那几份
  快照里也没有，所以这一批不新增清空控件，只做已有清空路径上的内容溶解。
