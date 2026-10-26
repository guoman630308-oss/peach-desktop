# ADR-0001：FastAPI 化的模块化单体

- 状态：Accepted
- 日期：2026-08-14

## 背景

Peach 已有可运行的旧 Web 服务，数据查询和写入函数大体独立于 HTTP Handler，但媒体 Range、缩略图、Cookie 与错误响应仍耦合在 `BaseHTTPRequestHandler`。直接重写会让现有播放流程失去回退路径。

## 决策

- 后端渐进迁移到 FastAPI；前端与后端在代码职责上拆分，但继续单进程、单体部署。
- 第一阶段先提供 `/healthz` 与现有 `/api/*` JSON 入口；第二阶段已补齐首页、标准媒体响应、
  缩略图和 preview/logo 路由。生产切换完成后，查询/写入 contract 已抽到
  `src/peach/web_contract.py`，旧 `BaseHTTPRequestHandler` 服务与动态加载器均已删除。
- 不引入微服务、PostgreSQL、React 重写或完整账号系统。
- Uvicorn 只允许单 worker，直到进程内缓存、写锁和后台任务状态移出全局变量。
- schema 升级必须由显式迁移器完成；应用导入和健康检查不得自动改真实数据库。

## 被否决的方案

- 一次性把首页、JSON API、媒体串流和生成任务全部重写。
- 为了「前后端分离」拆成独立部署服务。
- 先换前端框架，再恢复现有功能。

## 后果

FastAPI 现在是唯一 Web 入口；JSON contract、媒体 Range、页面和双视口保持兼容。进程内缓存与
写锁仍要求单 worker，后续可继续把 contract 拆为显式 repository/application 对象。
