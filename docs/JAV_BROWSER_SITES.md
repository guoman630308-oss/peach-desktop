# JAV 用户脚本站点清单

站点、镜像域名和识别方式在 [peach-mitao/peach-userscript 的完整站点表](https://github.com/peach-mitao/peach-userscript/blob/main/docs/SITES.md)维护。机器可读的唯一源是该仓库的 [`jav-sites.json`](https://github.com/peach-mitao/peach-userscript/blob/main/jav-sites.json)。

Peach 使用 `web/integrations/jav-sites.json` 发布快照校验来源地址；配套脚本和版本、上游提交、SHA256 记录一同发布。同步流程见[用户脚本](JAV_BROWSER.md#制品同步)。

## 接入方式

「自动识别」读取作品详情页主标题和明确番号字段，识别不到或存在冲突时使用脚本菜单。「选中番号」需要先选中一个完整番号，选择脚本菜单「选中番号加入 Peach」，再点击添加按钮。两种方式都由 Peach 校验番号并执行同一条磁力搜索、云下载和入库流程。

## 查漏补缺

独立仓库从 Sleazy 热门 JAV 脚本的显式站点声明收集镜像和缺少的源站，记录脚本版本、SHA256、域名归属、HTTPS 响应与重定向。维护者核对候选后更新清单；超宽通配符、失效或身份无法确认的域名保持待复核。来源及判断见[站点候选复核](https://github.com/peach-mitao/peach-userscript/blob/main/docs/MIRROR_REVIEW.md)。

域名登记不代表站点当前可达，也不代表已取得真实浏览器扩展、源站与网盘下载的联合验收。脚本依赖用户脚本管理器对已登记站点的访问权限。
