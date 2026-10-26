# amane 任务后继（TaskLink / TaskResult.followups）设计证据

- 来源：<https://github.com/sqzw-x/amane>，GPL-3.0
- 固定 revision：`79ecfa763cc786318e1964a3d7f4e244a7d5c96d`
- 取得日期：2026-09-22
- 取证方式：仓库外独立克隆，
  只读源码与其单元测试，不引入代码。GPL 与 Peach 的许可证不兼容，本文只记录设计判据，
  Peach 侧按自己的表结构与并发模型重写。
- 本文不登记进 `docs/reference-sources.json`：与 `amane-crawlers-poc.md` 同理，这是对上游
  源码的一次性只读实证，没有对应的可变 Markdown 原文需要跟踪漂移。

## 已取得

### 一、后继只能由任务结果声明

`handlers/protocol.py` 定义 `FollowupTask(key, task_type, payload, priority)` 与
`TaskResult(success, result, error, followups)`。类型注释写明两条约束：后继只经
`TaskResult.followups` 进入完成事务，handler 不能直接写队列；`payload` 由 handler 自己
组装成已绑定的类型化对象，不透传父任务字段。

handler 内部没有任何起线程、投递队列的入口——`TaskHandler.handle` 只返回 `TaskResult`。
所以「任务中途随手派生」在这套结构里不可表达，不是靠约定守住的。

### 二、父完成与子创建是同一个事务

`scheduler/worker.py` 在 `result.success` 时调用
`repo.complete_task_with_followups(task_id, result.as_dict(), followups)`；该方法（`db/repos/tasks.py`）
在一把插入锁加一个 session 里做完四件事：把父行改 `DONE`、写 `result`、按 key 逐条建子任务、
写 `task_links` 边，最后一次 commit。

这一步失败时 worker 把父任务改判 `FAILED`。也就是说「父成功但后继没落库」这个中间态不存在：
要么父与全部后继一起落地，要么这一轮整体算失败。

父任务失败（`result.success` 为假或 handler 抛异常）走 `fail_task`，只写父行的状态与错误，
一条后继都不创建，也不回滚父任务已经做过的写入。

### 三、去重分两层，各管各的

- **同父同 key**：`task_links` 上 `UniqueConstraint("parent_task_id", "key")`，加上
  `complete_task_with_followups` 里的 `seen_keys` 集合先滤一遍。key 的语义是「父节点内的
  后继名」，扇出时必须带实体 id（上游注释举的例子是 `scrape:{media_file_id}`）。
- **跨父的同一件事**：`_EXCLUSIVE_FIELDS` 声明哪些任务类型按 payload 的哪个字段互斥
  （固定 revision 上只有 `ACTOR_SCRAPE: "actor_id"` 一条）。`_insert_or_reuse` 先查
  `status IN (QUEUED, RUNNING)` 里同类型同字段值的行，有就复用那一行、只加一条边，不另建任务。
  终态行不参与，所以同一个演员可以被重刮，只是不会同时排两条。

`ORGANIZE` / `TRASH` 刻意不进互斥表（源码注释写明），它们每次都新建行。

### 四、写库类串行靠 handler 内的库级锁，不靠队列

`handlers/_common.py` 的 `LibraryTaskLocks` 是 `{library_id: asyncio.Lock}` 加一把建锁用的
guard 锁。`handlers/file.py`（ORGANIZE）与 `handlers/trash.py`（TRASH）都在 `handle` 里
先 `async with await locks.get(library.id)`，再调各自的 `_handle_unlocked`。

判据是「同库串行、不同库并行」：锁的粒度是库 id，不是全局，也不是任务类型。调度器本身
不认识这件事——它照常并发认领任务，串行发生在 handler 第一行。

### 五、链根与父子关系用两列一张表表达

`Task.root_task_id`：根任务指向自己，裸任务为 `NULL`；`complete_task_with_followups` 在父任务
落地时把 `NULL` 补成自己的 id，子任务继承父的 root。按这一列一次取出整条链。
`TaskLink(parent_task_id, child_task_id, key)`：出边表，删父任务只清出边，不删子节点。

API 侧（`api/models/tasks.py`）的 `TaskResponse` 因此带 `root_task_id`、`child_count`
与 `child_status`（折叠时按状态计数，不必展开整层），子任务响应 `TaskChildResponse` 多一个
`link_key`。子任务由 `/{task_id}/children` 按需加载，列表默认只返回链根。

### 六、重启后停在 running 的行一律判失败

`fail_all_running_tasks` 在 worker 启动时把全部 `RUNNING` 改成 `FAILED`，错误写「应用重启」。
`QUEUED` 的行不动，重启后照常被 `claim_next_task` 领走——队列本身就在库里，续跑是自然发生的。

## 未取得

**后继的数量上限与链深度上限**：固定 revision 的源码里搜不到任何 `depth`、`max_followups`
一类的闸门。扇出规模由各 handler 自己的取数上限间接决定（`rescrape` 用 `payload.limit`、
`refresh` 用本库文件数），链深度没有任何限制，只有「哪些 handler 会声明后继」这一条隐式约束。
所以「防链式爆炸」这件事上游没有可借的实现，Peach 侧要自己定判据。

## Peach 采用

- 后继由任务结果声明，调度端统一取出；handler 不自己起线程。
- 同父同 key 只留一条；跨父的同一件事按全局 key 复用已在排队或在跑的那一条。
- 父完成与后继入队在同一个写事务里。
- 写账本的后继串行，不写账本的可并行。
- 父子关系落在 `task_run` 自己的列上（Peach 是单进程 SQLite 一张表，不另开边表）。

## 有意差异

- **上限是 Peach 自己加的**：单轮后继条数与链深度都有硬上限，超出的记一条摘要而不是静默丢弃。
  上游没有这道闸（见「未取得」）。
- **串行的粒度不同**：amane 按库 id 上锁，Peach 按「写不写账本」分道——Peach 的账本是单文件
  SQLite，库级并行在这里没有对应物。
- **重启后的处置相反**：amane 把 `RUNNING` 一律判失败，Peach 的后继要能续跑（`peach-batch-jobs`
  的判据是只把成功当作已完成），所以未跑完的后继重新排队，代价是后继必须幂等。
