"""macOS 菜单栏项。直接用 AppKit，不走 pystray。

菜单栏项这件事没有跨平台的轮子可用。pystray 名义上支持 darwin，但它的后端只做了
`statusItemWithLength_` + `setImage_`，漏掉两件在 macOS 上必需的事：

1. `NSApplication.setActivationPolicy_(Accessory)` —— 不声明就不是「只在菜单栏出现」
   的附件应用。
2. `NSImage.setSize_(18, 18)` —— 菜单栏高度是 22pt、图标按 18pt 画。直接把 64px 的
   PNG 塞进按钮，画出来要么被裁掉要么糊成一坨。

它也不调 `setTemplate_`，所以图标不会跟着浅色/深色菜单栏反色。三件事都得自己补，
补完等于把它那层薄封装重写一遍，不如直接用 AppKit——Windows 那边继续用 pystray。

判据不是猜的：`item.button().window().frame()` 能直接读出状态项落在屏幕的哪个位置，
本机实测 `x=858 w=34`（屏宽 1512），确认它确实被放上了菜单栏。
"""
from __future__ import annotations

import io
import logging
import threading
from dataclasses import dataclass
from typing import Callable

import AppKit
import PyObjCTools.AppHelper
import objc
from PIL import Image


LOGGER = logging.getLogger(__name__)

#: 菜单栏图标的绘制尺寸。菜单栏高 22pt，图标按 18pt 画是系统惯例。
ICON_POINTS = 18


def nsimage(image: Image.Image, *, points: int = ICON_POINTS) -> AppKit.NSImage:
    """把 PIL 图转成菜单栏用的 template NSImage。"""
    buffer = io.BytesIO()
    image.convert("RGBA").resize((points * 2, points * 2), Image.Resampling.LANCZOS).save(
        buffer, "PNG")
    payload = buffer.getvalue()
    data = AppKit.NSData.dataWithBytes_length_(payload, len(payload))
    result = AppKit.NSImage.alloc().initWithData_(data)
    # 尺寸按点设，位图给两倍以适配 Retina。
    result.setSize_(AppKit.NSMakeSize(points, points))
    # template image 由系统按菜单栏明暗自动反色；彩色图不会跟着变。
    result.setTemplate_(True)
    return result


class _Target(AppKit.NSObject):
    """菜单项的动作目标。AppKit 只认 ObjC 选择器，所以要有这么一层。"""

    def initWithHandlers_(self, handlers):
        self = objc.super(_Target, self).init()
        if self is None:
            return None
        self._handlers = handlers
        return self

    @objc.python_method
    def _dispatch(self, sender) -> None:
        handler = self._handlers.get(int(sender.tag()))
        if handler is None:
            return
        try:
            handler()
        except Exception:
            LOGGER.exception("菜单项处理失败")

    def invoke_(self, sender) -> None:
        self._dispatch(sender)


@dataclass(frozen=True)
class Submenu:
    """原生子菜单；每次刷新取得当前的服务与提醒行。"""

    items: Callable[[], list[tuple[object, object]]]


class MenuBarApp:
    """一个菜单栏状态项，外加它的下拉菜单。

    `items` 是 (标题, 处理函数、Submenu 或 None) 的序列；None 表示只读信息行。
    动态标题和子菜单每五秒刷新，内容不变时保留原菜单。
    """

    def __init__(
        self,
        image: Image.Image,
        tooltip: str,
        items: list[tuple[object, Callable[[], None] | Submenu | None]],
    ):
        self.app = AppKit.NSApplication.sharedApplication()
        # 附件应用：只在菜单栏出现，不占 Dock、不进 ⌘Tab。bundle 里 LSUIElement 也声明了
        # 一次，但直接从终端跑时只有这句管用。
        self.app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

        self._items = items
        self._handlers: dict[int, Callable[[], None]] = {}
        self._target = _Target.alloc().initWithHandlers_(self._handlers)
        self._rendered = None

        self.item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSVariableStatusItemLength)
        self.item.button().setImage_(nsimage(image))
        self.item.button().setToolTip_(tooltip)
        self.refresh_titles()
        self.item.setVisible_(True)

    def _resolve(self, items):
        return [(title() if callable(title) else title,
                 self._resolve(handler.items()) if isinstance(handler, Submenu) else handler)
                for title, handler in items]

    def _menu(self, items):
        menu = AppKit.NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        for title, handler in items:
            if title is None:
                menu.addItem_(AppKit.NSMenuItem.separatorItem())
                continue
            entry = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(str(title), None, "")
            if isinstance(handler, list):
                entry.setSubmenu_(self._menu(handler))
                entry.setEnabled_(True)
            elif handler is None:
                entry.setEnabled_(False)
            else:
                index = len(self._handlers)
                self._handlers[index] = handler
                entry.setTag_(index)
                entry.setTarget_(self._target)
                entry.setAction_("invoke:")
                entry.setEnabled_(True)
            menu.addItem_(entry)
        return menu

    def refresh_titles(self) -> None:
        """同时更新摘要与子菜单；已消失的提醒和旧动作不留在原生菜单里。"""
        rendered = self._resolve(self._items)
        if rendered == self._rendered:
            return
        self._handlers.clear()
        self.menu = self._menu(rendered)
        self.item.setMenu_(self.menu)
        self._rendered = rendered

    def placement(self) -> tuple[float, float, float, float] | None:
        """状态项在屏幕上的位置；拿不到窗口说明它根本没被放上菜单栏。"""
        window = self.item.button().window()
        if window is None:
            return None
        frame = window.frame()
        return (frame.origin.x, frame.origin.y, frame.size.width, frame.size.height)

    def notify(self, message: str, title: str = "Peach") -> None:
        """用系统通知报状态；菜单栏项没有窗口可以弹。"""
        notification = AppKit.NSUserNotification.alloc().init()
        notification.setTitle_(title)
        notification.setInformativeText_(message)
        AppKit.NSUserNotificationCenter.defaultUserNotificationCenter(
        ).deliverNotification_(notification)

    def log_placement(self) -> None:
        """把状态项的实际坐标写进日志。

        必须等运行循环转起来之后再读：刚构造完时窗口还没布局，读到的是 (0,0,34,0)，
        看着像「没放上去」其实只是早了一步。
        """
        def report() -> None:
            LOGGER.info("菜单栏状态项位置：%s", self.placement())

        AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            1.0, False, lambda _timer: report())

    def run(self) -> None:
        self.log_placement()

        # 标题里有「状态：运行中」这种会变的内容，定时刷新比只在打开时刷简单且够用。
        def tick() -> None:
            while True:
                AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(self.refresh_titles)
                if self._stop.wait(5.0):
                    return

        self._stop = threading.Event()
        threading.Thread(target=tick, name="PeachMenuTitles", daemon=True).start()
        try:
            PyObjCTools.AppHelper.runEventLoop()
        finally:
            self._stop.set()

    def stop(self) -> None:
        AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(self.item)
        self.app.terminate_(None)
