--
-- 一轮任务结束时派生出来的后继，与它的父任务记在同一张表里。
--
-- 为什么要这么改：刮削链每跑一轮都会登记新的女优和厂牌实体，它们刚建出来时没有头像，
-- 补头像的能力全在手工脚本里。ADR-0040 把这件事定成「任务在结束时声明后继，调度端统一
-- 派发」，后继要能被看见、被去重、被串行，还要在重启后接着跑——这四件事都需要它自己
-- 在库里有一行，而不是进程里的一个回调。
--
-- 不另开一张边表：Peach 是单进程单文件 SQLite，父子关系就是 `task_run` 上的一列自引用，
-- 一次查询取得完。amane 那边的 `task_links` 是为「同一个子任务可以有多个父」准备的，
-- Peach 的后继按全局 key 去重，同一件事只会有一条在排队，多父这个形状不存在。
--
-- `followup_key` 是这条后继要做的那件事的全名（`entity-avatar:performer:8022`），不是
-- 任务类型。它同时是互斥键：入队时写进 `mutex_key`，撞上 0028 那条活跃唯一索引就是
-- 「同一件事已经在排了」，一次 INSERT 判完，不先查一遍。
--
-- `followup_depth` 是链深度，根任务 0，它派出的后继 1，以此类推。ADR-0040 定的上限在
-- 代码里判，这一列是判它的依据，也是事后回答「这条是第几层派出来的」的唯一地方。
--
-- 三列都允许 NULL：不是后继的普通任务照常一行什么都不带，旧行不必回填。

ALTER TABLE task_run ADD COLUMN parent_run_id INTEGER REFERENCES task_run(id) ON DELETE SET NULL;
ALTER TABLE task_run ADD COLUMN root_run_id INTEGER REFERENCES task_run(id) ON DELETE SET NULL;
ALTER TABLE task_run ADD COLUMN followup_key TEXT;
ALTER TABLE task_run ADD COLUMN followup_depth INTEGER NOT NULL DEFAULT 0;

-- 「这一轮派出了哪些后继」：详情端点按父 id 取子行，活动页按同一条路数状态。
CREATE INDEX idx_task_run_parent ON task_run(parent_run_id, id);

-- 「还有哪些后继等着跑」：调度端启动与每跑完一条都从这里领下一条，
-- 部分索引只覆盖待跑的行，终态历史再多也不进这个索引。
CREATE INDEX idx_task_run_followup_queue ON task_run(id)
  WHERE followup_key IS NOT NULL AND status='pending';
