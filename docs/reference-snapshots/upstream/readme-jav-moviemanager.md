# Potplayer下载和使用教程
**截至8/29/2025，potplayer已继承在软件包中，故不再需要安装potplayer，如果使用上仍有问题，请加issue**
- 初次使用无法添加播放列表解决方法：https://github.com/4evergaeul/JAV_MovieManager/blob/main/Installation/Tutorial.mp4
  
~~- Potplayer下载：https://github.com/4evergaeul/JAV_MovieManager/blob/main/Installation/PotPlayerSetup64.exe~~

# 软件更新方法
1. 在[发布页面](https://github.com/4evergaeul/JAV_MovieManager/releases/tag/v1.4.3)找到最新版本
2. 下载含有**ReturningUser**后缀的zip文件
3. 解压到本地
4. 复制你的MovieDb.db到新版本文件夹

# 交流群
https://t.me/javmgr/20

# 安装说明
影片需要预先通过刮削器取得元数据和影片封面图片。如果已有元数据，请直接从第二步开始阅读。
## 第一步：影片刮削
从8/23/2025开始，推荐刮削器换为社区更新的[MDCX]([https://github.com/Yuukiy/JavSP](https://github.com/sqzw-x/mdcx))。大家可以去原Repo下载。下载链接如下。
[下载地址](https://github.com/sqzw-x/mdcx/releases)

***此外如果有刮削器导致的影片库加载问题，请使用我在群里置顶的MDCx以及我的配置（均已包含在压缩包），这样应该能最大程度减少软件使用的问题***

1.  清理影片名，让影片名的格式为：{番号名}-{序号} 或 {番号名}-{序号}-C。
例如xxxx.com@ADN-566.mp4需要重命名为ADN-566.mp4，否则影片刮削器无法识别。

2. 重命名完毕后，MDCX.exe.

4. 运行[男优刮削器](https://github.com/4evergaeul/AVDanyuScrapper)（AvdanyuScraper.exe，可选），选择<b>#整理完成</b>文件夹，等待刮削结束。

此时数据整理完毕，之后可以把处理过后的文件夹全部移动到你的影片库位置。

## 第二步：安装软件
1. 在一切安装完毕后，即可以解压MovieManager。双击"MovieManager.TrayApp.exe"运行。此时会弹窗“程序正在初始化”，请等待大约5-10秒。

2. 程序初始化完毕后，弹窗会自动关闭，并且程序会用默认浏览器打开。并且程序也会出现在<b>屏幕右下角的托盘图标中</b>，如下图。
![](Readme/Picture9.png)

3. 右键图标，点击"打开"。这个时候默认浏览器会打开影片管理器。点击<b>设置</b>。
![](Readme/Picture10.png)

4.  下面是设置表格的介绍：
<b>（必须）</b> “文件夹”： 设置为你的影片目录（需要手动输入，或把地址复制粘贴过去）。e.g.``` J:\MyFile\New\有码\演员```。<br>
如果需要测试，可以在文件夹下找到TestingMovieLib，把该文件夹设置为影片目录即可。<br>
演员头像库请从该库下载：https://github.com/gfriends/gfriends<br>
<b>（可选）</b> “演员头像（DMM）”：将用于演员页面的演员大头照。放文件夹地址。<br>
<b>（可选）</b>“演员头像（全体）”：用于演员详细页面里的写真照片。放文件夹地址。<br>
“扫描所有文件”： 默认为ON，如果OFF的话则可以设定扫描多少天内加的电影。<br>
“扫描多少天内加的电影”：如果这里的数值设为<b>-1</b>，则搜索所有电影。<br>
![](Readme/Picture11.png)

5. 在一切配置完成后，点击“保存设置”->“添加新电影”。 程序就会开始构建影片库，这个时长会根据你影片数量而定。影片库构建完成后页面会刷新并返回电影页面。（如果点击保存设置后，程序未能立刻保存，说明后端程序没有正常运行，右键点击托盘栏图标并点击退出，重新运行程序即可）

配置完成后类似下图。
![](Readme/Picture12.png)

# 软件介绍（目前暂不支持步兵和欧美片）
所谓工欲善其事必先利其器，如果说拥有一个元数据完备的电影库则是优雅看片的第一步，那么我认为有一个功能功能完备的电影管理器则是优雅看片的第二步。

自从我19，20之际参考@Pockies大佬写的《[利用AV Data Capture+Jellyfin+Kodi打造更优雅的本地AV（毛片）+普通影片媒体库](https://pockies.github.io/2020/01/09/av-data-capture-jellyfin-kodi/)》一文，建立了我的电影库后，我便一直苦于没有一个符合我心目中全部功能的PC端影片管理软件，当时正逢疫情之际，我也得以长期在家办公，于是便心生写一个满足自己想要功能的影音管理器。

从21年动笔，到24年今日的第一版release，作为一款轻量级的软件来说开发周期算是很长的了，其实在软件立项1，2个月后基本功能上就已经成型，但归咎于我前端的不熟练，我便一直懒于完成这个软件的使用周期。在23年chatgpt横空出世后，我便利用它完成了整个软件的loop。

作为一个传统行业出身的码农，我选的后端技术栈是 .NET Core 3.1，前端用的是React.js; UI框架采用的是Ant Design; 数据库采用的是SQL Lite。

软件主要的页面有“影片”， “演员”，其次有类型，标签，导演。我在前两个页面花了比较大的功夫，功能也更加完备。

电影页面如下，基本就是以Ant Design里Card来显示各个影片的缩略图，屏幕导航栏可以根据演员，类型等信息快速filter查找影片。

右侧是影片搜索，加入Potplayer播放列表功能。并且支持自定义SQL搜索。
![](Readme/1.jpg)

点击影片卡后即可进入影片详情界面，界面会自动获取演员，类型，标签信息并以Button形式显示在详情界面上，点击button即可查看相关影片。
![](Readme/2.png)

演员界面如下，目前的演员列表是基于我的电影收藏刮削获得（本人库收藏6000余部，基本囊括热门女优），在本程序里也内置了一个女演员刮削器。演员界面的左侧支持通过年龄，身高，罩杯进行filter，右上方可以将屏幕内所有演员的电影加入Potplayer播放列表，以及人名搜索功能。
![](Readme/3.png)

点击演员头像卡即可进入演员详情页面，页面里将有演员的各项信息以及评分（评分来源为minnano-av，同时这也是我用于刮削数据的网站）
![](Readme/4.png)

# Repository文档
### 工程文件树
```
MovieManager
│
├───MovieManager.BusinessLogic： 后端逻辑
│
├───MovieManager.ClassLibrary： ViewClass
│
├───MovieManager.Data：数据库交互层
│
├───MovieManager.DB：数据库文件
│
├───MovieManager.Deployment：用于自动部署，build的executable可在./MovieManager.TrayApp/bin/Any CPU找到
│
├───MovieManager.Endpoint： API文件
│
├───MovieManager.Testing： Unit Testing文件
│
├───MovieManager.TrayApp： 程序Startup逻辑
│
└───MovieManager.Web： 前端逻辑
```
### Build文件树
```
MovieManager_{MMddyyyy}_{hhmmss}
│
├───build： 前端文件
│
├───logs： 日志文件
│
├───TestingMovieLib：测试用的影片库
│
└────runtimes

```

### 参数
参数位于appsettings.json文件内。下面是各个参数的解释：
- EndpointHost：后端API的端口，如果要更改为其他则需要更改并重新build前端项目。

### Loggings
项目使用的时Serilog的loggings库，log文件build的文件夹下。

# 法律信息及免责声明
本项目仅用于技术研究与学习交流目的，不得用于任何违反法律法规的用途。
在使用本软件前，请务必确认并遵守您所在国家或地区的相关法律法规。
- 项目仅用于技术、学术交流，严禁用于商业和其他盈利目的。
- 在法律禁止传播或使用本软件的地区，严禁下载、传播或使用本软件。
- 开发者不对因使用或传播本软件而产生的任何直接或间接后果承担责任。
- 演员和影片图片等信息版权归相应网站及演员所属经纪公司所有。
- 使用本软件即代表您已阅读并同意以上声明，并自行承担相关风险与责任。
