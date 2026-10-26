# ADR-0049：label 归属片商，但不当片商处理

- 状态：Accepted；「只有一层」由 [ADR-0051](0051-labels-can-sit-under-a-label.md) 修订
- 日期：2026-09-23

## 背景

账本里的厂牌只有一层 `kind='studio'`。同一家母公司旗下的 label 在账本里有三种记法：

- 各自独立成一条厂牌，彼此没有关联（`BAZOOKA` 与 `K M Produce`）；
- 通过别名并进母公司（`PRESTIGE PREMIUM` 挂在 `Prestige` 名下）；
- 把「label/母公司」写成一个名字（`ABC/妄想族`）。

这样一来，按母公司查看全部作品做不到。合并脚本还会把 label 当成母公司的另一种写法吞掉，而合并不可逆。

用户 2026-09-23 的要求是「加一层 label 在详情里展示，但是不作为厂商处理」。

## 决策

新增表 `label_maker(label_id, maker_id, source, confidence, checked_at)`，迁移为 `0035`。

- **label 仍是厂牌**：作品照旧挂在 label 自己身上，不改名，不合并，也不改写作品上的厂牌字段。这张表只多记一件事：label 归哪家片商。
- **只有一层**：label 不能再挂 label，片商自己也不能是别家的 label。装入脚本遇到这两种情况会拒收。
- **主键在 label 一侧**：一个 label 只有一家现役片商。label 转手时覆盖原来那一行，不新增一行；做法与 `entity_membership` 相同。
- **片商可以没有作品**：`妄想族` 的作品全挂在旗下 label 上，它自己是一条没有作品的 `studio` 实体，由装入脚本加 `--create-makers` 新建。
- **合并时关系跟着走**：`merge_entity` 两个方向都迁移。label 并入自己的片商时，那一行会变成自己指向自己，直接删掉。
- **页面上只是链接**：
  - 作品详情里，厂牌一组旁边多一组「片商」；
  - label 的资料页，在归属那一格链到片商；
  - 片商的资料页，另起一行「旗下」列出 label。

  作品数各算各的，片商页不把 label 的作品算进来。

数据来源是各家母公司的官方名录：KMP 的 `km-produce.com/label`、Prestige 的 `prestige-av.com`、妄想族的 `mousouzoku-av.com`。复核 CSV 的列为 `label,maker,evidence`，由 `scripts/install_label_makers.py` 装入。脚本默认 dry-run，`--apply` 必须同时给 `--backup`。

## 不做的事

- 不按片商聚合筛选作品。需要时另开一条查询，把 label 并起来，不去改动归属本身。
- 不把已经作为别名并进母公司的 label（如 `PRESTIGE PREMIUM`）拆出来。拆分需要逐部作品判断归属，属于另一项任务。
