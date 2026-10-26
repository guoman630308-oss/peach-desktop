--
-- 一张表回答「现在在跑什么、刚才跑了什么、为什么没跑」。
--
-- 为什么要这么改：任务状态此前有三套互不相通的存法——`jobs.BackgroundJob` 的进程内
-- 状态字典（进程一停就失忆）、`PidFileLock` 的锁文件（只说得出有没有人在跑）、
-- `library_processing` 的状态 JSON（只覆盖一种任务）；追更轮询、批量操作和命令行
-- 脚本干脆什么都不留。对外是六个快照端点、六个页面各说一段，没有一处能同时答出
-- 上面三个问题，而「刚才那轮定时检查为什么没跑」根本没有地方可答：轮询撞上手动
-- 检查时静默跳过，跳过这件事只存在于调度器的内存里。
--
-- 各域自己的状态机保留，那是执行细节（正在处理哪一项、第几次重试、预算还剩多少）。
-- 这张表只记对外的那一层，并且是唯一的对外视图。
--
-- 互斥靠部分唯一索引，不引队列组件：Peach 是单进程 SQLite，`INSERT` 撞上索引就是
-- 冲突，比「先查一遍再插」少一个时间窗。索引带 `status` 条件，所以终态行不占位，
-- 历史想留多少留多少；终态也不清空 `mutex_key`，「上次是谁挡的」因此还查得到。
--
-- `trigger` 说的是这一轮由谁发起，它决定冲突怎么处理：`scheduled` 撞上在跑的就记一条
-- `cancelled` 静默跳过，`manual` 撞上则让接口回 409 并带上挡路那条的 id。
--
-- `heartbeat_at` 是租约。进程被杀、行还停在 `running` 的，由 `task_runs.recover_interrupted`
-- 在服务启动时改成 `interrupted` 而不是 `failed`：任务没有失败，是没跑完，两者在
-- 「要不要去查为什么」上是相反的结论。
--
-- 时间列一律 ISO-8601 UTC 文本，与 `entity`、`genre_decision` 同一种写法。
-- 终态与 `finished_at` 用 CHECK 绑死：终态行必有终止时刻，在跑的行必没有，
-- 「这条是不是还活着」因此只有一个判据。

CREATE TABLE task_run(
  id INTEGER PRIMARY KEY,
  task_key TEXT NOT NULL,
  trigger TEXT NOT NULL CHECK(trigger IN ('manual','scheduled','startup','cli')),
  status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','failed','cancelled','interrupted')),
  mutex_key TEXT,
  pid INTEGER,
  host TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  finished_at TEXT,
  heartbeat_at TEXT,
  progress_current INTEGER,
  progress_total INTEGER,
  progress_label TEXT,
  result_summary TEXT NOT NULL DEFAULT '{}',
  error TEXT,
  CHECK((status IN ('pending','running')) = (finished_at IS NULL))
);

-- 同一把互斥锁在「未结束」的行里只能有一条。SQLite 的唯一索引允许多个 NULL，
-- 所以不声明互斥的任务照常并行。
CREATE UNIQUE INDEX idx_task_run_active_mutex ON task_run(mutex_key)
  WHERE mutex_key IS NOT NULL AND status IN ('pending','running');

-- 「这个任务最近跑了几轮」：活动页按 task_key 取最近 N 条，`prune` 按同一条路找阈值。
CREATE INDEX idx_task_run_key_recent ON task_run(task_key, id DESC);

-- 「现在有什么在跑」：活动页首屏和 `recover_interrupted` 都从 status 进。
CREATE INDEX idx_task_run_status_recent ON task_run(status, id DESC);
