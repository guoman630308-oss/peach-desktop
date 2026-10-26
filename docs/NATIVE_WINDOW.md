# Windows 独立窗口测试版

独立窗口使用 WebView2 显示原版 Peach 页面，由原版托盘持有本机服务。保留扫描、番号、人物、资料来源、关注、云下载、播放和配置入口。联网功能由用户在原版设置中选择启用。

## 安装与使用

运行 `PeachDesktop-<版本>-windows-x64-setup.exe`，或解压完整免安装包并运行 `Peach.exe`。系统需要 Microsoft Edge WebView2 Runtime；主界面无需完整 Edge 浏览器，也无需安装 Python 或 Node。外部网站验证、登录和用户脚本仍使用系统浏览器。运行组件缺失时应安装微软官方 WebView2 Runtime；窗口固定使用 WebView2，不使用旧版 MSHTML。

首次运行在窗口内选择媒体目录与访问范围。关闭窗口会暂停当前页面的媒体播放并隐藏到托盘；双击托盘或再次运行程序可重新打开。托盘「配置 Peach」在同一个窗口打开设置；「退出 Peach」结束窗口与本实例拥有的服务。命令行 `Peach.exe --quit` 等待窗口和托盘退出。

窗口测试版默认安装在当前用户的 `Programs/PeachDesktop`，资料目录为 `%LOCALAPPDATA%/PeachDesktop/peach-data`，拥有独立安装标识。`--data-root` 可显式指定另一份资料。首次设置修改资料目录时沿用原版的重定向记录，窗口控制与 WebView2 配置保留在启动落点，供重开和异常恢复使用。卸载保留资料目录。

视频默认由本机外部播放器打开，自动发现 PotPlayer。播放区提供「内置播放」，专用沉浸模式继续
使用内置播放器。「Peach 配置 → 媒体库与入库 → 播放器」可选择默认播放方式及播放器程序。
播放器偏好保存在资料目录的 `state/local-player.json`，外部播放不回传观看记录。详见[外部播放器说明](EXTERNAL_PLAYERS.md)。

amane 采集桥的脚本、清单与锁文件随包提供。其独立运行环境在「来源和凭证」页底部安装，需要本机已有 uv；首次安装会下载锁定的 Python 与依赖。页面显示「未安装」时，依赖 amane 的九类来源尚不能解析资料，网站连接检查通过也不代表运行环境已安装。安装或更新窗口程序不会自动启动这项下载。

## 窗口行为

全新资料目录会优先使用默认本机端口，已占用时由系统分配空闲端口。首次设置保存端口后以保存值为准，启动环境中的首启端口不覆盖用户设置。

系统代理兼容启动环境中单独提供本机绕过规则的情况；显式环境代理和绕过规则仍保留。更换 Windows 系统代理的地址或端口后，请从托盘退出窗口版再重新打开。来源选择「直接连接」时仍按来源设置执行。

首页和配置页在内嵌窗口显示。外部资料网站在系统浏览器打开；浏览器扩展和用户脚本仍安装在系统浏览器中。WebView2 使用独立 Cookie、缓存与站点存储，不自动读取个人浏览器的登录状态。需要 Cookie 的来源使用原版的凭据配置流程。

页面下载允许系统保存对话框，视频全屏同步到整个窗口。界面不暴露文件操作或后端调用的 JavaScript 桥。顶层页面限制在当前本机服务来源，外部 HTTP/HTTPS 导航交给系统浏览器，拒绝文件网址。HTTPS 证书验证保持启用。

本测试包只接受 `PeachDesktop-<版本>-windows-x64.zip` 更新资产。没有配套发行资产时，更新页提示使用窗口版安装包。不要使用普通 `Peach` 更新归档覆盖本包。安装包未代码签名。

## 构建与验证

同步锁定的 `desktop-window` 与 `build` 可选依赖后，通过原版 `scripts/build_windows.ps1 -EmbeddedWindow -Installer` 构建。FFmpeg、untrunc 和 cloudflared 仍按原版清单校验，第三方工具及对应源码随完整包分发。

窗口适配用例位于 `tests/test_native_shell.py`、`tests/test_native_updates.py`，与原版托盘用例共同通过 `scripts/test.ps1` 执行。真实窗口验收应使用临时资料库和合成视频，分别核验首次设置、扫描、播放、全屏、下载、重开与退出，不能将普通浏览器测试等同于 WebView2 验收。

开发验收可显式设置 `PEACH_WEBVIEW_DEBUG_PORT` 为回环调试端口；普通启动不开放调试端口。不要对真实个人资料库开放调试会话。

## 第三方许可

pywebview、Python.NET、clr-loader 和 bottle 的许可保留在包内 `_internal` 的分发元数据中。proxy-tools 0.1.0 的源码头部及[上游许可证](https://github.com/jtushman/proxy_tools/blob/master/LICENSE.txt)声明 BSD；其发布归档不含许可证文件，完整声明随本文保留。

```text
The BSD License (BSD)

Copyright (c) 2013 Armin Ronacher
Copyright (c) 2014 Jonathan Tushman

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

  * Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.
  * Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in the
    documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL <COPYRIGHT HOLDER> BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```
