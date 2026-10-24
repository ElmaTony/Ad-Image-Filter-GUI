# -*- coding: utf-8 -*-
"""主区布局管理器（单一固定形态）。

历史沿革：
- v7 曾支持 studio / workbench / gallery 三骨架；用户决定只保留一种后，
  侧栏也被判定为冗余（导航项与顶栏/底栏入口完全重复）而删除。
- 当前形态：**顶栏（全部功能入口）+ 图片墙 + 右侧详情面板 + 底部处置条**。
- set_layout() 保留为幂等 no-op 以兼容旧测试/调用。
"""
from PySide6.QtCore import Qt

import cds_tokens as T
from cds_tokens import T as TT          # 类属性（surface / text / border …）


class LayoutManager:
    """主区排布：无侧栏，mid = 图片墙 + 详情面板。"""

    MODES = ("workbench",)
    LABELS = {"workbench": "工作台"}

    def __init__(self, app):
        self.app = app
        self.mode = "workbench"
        self.sidebar = None       # 兼容旧断言/审计代码：始终 None（侧栏已删）
        self.grip = None
        self._float_bar = None

    # ---------------- 主入口 ----------------
    def apply(self, mode: str = "workbench"):
        app = self.app
        self.mode = "workbench"
        self._teardown()
        mid = getattr(app, "mid", None)
        if mid is None:
            return
        app.detail.setVisible(True)
        app.detail.setFixedWidth(T.DETAIL_W)
        self._dock_bottom(True)
        self.refresh_theme(getattr(app, "theme", "dark") == "dark")

    def _teardown(self):
        # 历史骨架的悬浮胶囊清理（保留幂等，旧调用不会炸）
        if self._float_bar is not None:
            try:
                self._float_bar.setParent(None)
                self._float_bar.deleteLater()
            except RuntimeError:
                pass
            self._float_bar = None

    # ---------------- 底部条：停靠 ----------------
    def _dock_bottom(self, docked: bool = True):
        app = self.app
        bot = getattr(app, "bot", None)
        if bot is None:
            return
        root = getattr(app, "_root_layout", None)
        try:
            bot.setParent(app)
            if root is not None and root.indexOf(bot) < 0:
                root.insertWidget(root.count() - 1, bot)
        except Exception:
            pass
        bot.setStyleSheet(getattr(app, "_bot_css", ""))
        # 悬浮模式锁定过 56 高 / 胶囊宽——停靠必须还原，否则宽高被锁死
        bot.setFixedHeight(60)
        bot.setMinimumWidth(0)
        bot.setMaximumWidth(16777215)
        bot.show()

    def on_resize(self):
        pass

    def refresh_theme(self, dark: bool):
        # 侧栏已删：主题重绘由 App._apply_theme 统一驱动各面板
        pass

    def set_badges(self, **kw):
        pass
