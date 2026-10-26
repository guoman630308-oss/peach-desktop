-- 云下载入库后的持久状态。下载完成与后处理分开：远端任务已完成不代表主文件已经
-- 识别、资料已经采集、推广附件已经隔离或目录已经整理。
ALTER TABLE download_task ADD COLUMN postprocess_state TEXT NOT NULL DEFAULT 'not_requested';
ALTER TABLE download_task ADD COLUMN postprocess_detail TEXT;
ALTER TABLE download_task ADD COLUMN postprocess_run_id INTEGER;
ALTER TABLE download_task ADD COLUMN postprocessed_at TEXT;

-- 升级前已经入库的任务在服务启动时补派；失败、取消与仍在下载的任务不派。
UPDATE download_task SET postprocess_state='pending' WHERE state='ingested';

