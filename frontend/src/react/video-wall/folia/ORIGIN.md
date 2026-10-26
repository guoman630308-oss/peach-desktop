# Folia 墙面排布

来源：[chthollyphile/folia-major](https://github.com/chthollyphile/folia-major/tree/9a01e788ed52d25ea01b1f0d37934e5e371ae82b/src/components/wall)。许可证 AGPL-3.0，revision `9a01e788ed52d25ea01b1f0d37934e5e371ae82b`。

`blockTemplates.ts` 与 `blockReflows.ts` 保留上游原文。四套 12×8 模板及其镜像共 16 种排列；让位表将任一磁贴展开为 6×6，其余十一张留在同一块内。Peach 在 `wall-layout.ts` 对接分页、实例键及视口裁剪。

| 文件 | SHA-256 |
| --- | --- |
| `blockTemplates.ts` | `8630b2ba33f819a689a867f78d4d996fb2e7edd59caad7b3d798876a442029db` |
| `blockReflows.ts` | `21f28aac0061fd7e23a68d8f965e569b6865779e6712c09cdb281676b8e95af6` |

展开使用上游 `wallReflowMotion.ts` 的 `stiffness: 300`、`damping: 34`；控件延后 140 ms、240 ms 内由下方 12 px 淡入。相机居中使用 `useWallCameraPan.ts` 的 420 ms、`[0.22,1,0.36,1]` 曲线。Peach 使用已有 `motion` 依赖的布局投影，位移和缩放可被拖动或滚轮打断；键盘操作及 reduced motion 直接定位。

首次入场使用 `PosterWall.tsx` 的对角波次：每格 30 ms，延后最多 340 ms；`LatticePoster.tsx` 的 `stiffness:360`、`damping:24`、90 世界单位抬升和 0.88 起始缩放。桌面尺度下抬升为 68.4 px。后续平移露出的磁贴在原位由 0.94 缩放淡入。精细指针悬停时磁贴每边扩大一个间隙，展开项不放大；详情返回保留原位。
