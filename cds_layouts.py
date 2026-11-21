# -*- coding: utf-8 -*-
"""主区布局管理器（单一固定形态）。

形态：顶栏（全部功能入口）+ 图片墙 + 右侧详情面板 + 底部处置条。
保留 sidebar / _float_bar / apply() 等兼容属性与入口供既有测试断言。
"""


class LayoutManager:
    """主区排布：无侧栏，mid = 图片墙 + 详情面板。"""

    def __init__(self, app):
        self.app = app
        self.mode = "workbench"
        self.sidebar = None       # 兼容旧断言/审计代码：始终 None（侧栏已删）
        self._float_bar = None

    def apply(self, mode: str = "workbench"):
        app = self.app
        self.mode = "workbench"
        mid = getattr(app, "mid", None)
        if mid is None:
            return
        app.detail.setVisible(True)
        app.detail.setMinimumWidth(320)
        app.detail.setMaximumWidth(520)
        self._dock_bottom()
        self.on_resize()

    def _dock_bottom(self):
        app = self.app
        bot = getattr(app, "bot", None)
        if bot is None:
            return
        root = getattr(app, "_root_layout", None)
        if root is not None and root.indexOf(bot) < 0:
            bot.setParent(app)
            footer = getattr(app, "footer", None)
            insert_at = root.indexOf(footer) if footer is not None else -1
            if insert_at < 0:
                insert_at = max(0, root.count() - 1)
            root.insertWidget(insert_at, bot)
        bot.setMinimumWidth(0)
        bot.setMaximumWidth(16777215)
        bot.show()

    def on_resize(self):
        from workbench_ui import adapt_workbench
        adapt_workbench(self.app)

    def refresh_theme(self, dark: bool):
        # 主题重绘由 App._apply_theme 统一驱动各面板
        pass
