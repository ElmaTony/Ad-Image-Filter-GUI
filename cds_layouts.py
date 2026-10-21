# -*- coding: utf-8 -*-
"""三种骨架的布局管理器。

设计要点：**共用同一批控件实例**，只做重排 / 显隐，不重建。
好处是切换布局时 model、勾选、滚动位置、扫描状态全部保留（功能零丢失），
也让 105 项 gui-test 引用的 app.grid / app.folder_bar 等引用始终有效。

  studio    D1：256px 常驻导航 + 图片墙 + 底部 62px 处置条
  workbench D2：288px 可调侧栏 + 图片墙 + 右侧 360px 可调详情
  gallery   D3：全宽图片墙（侧栏收进抽屉）+ 悬浮操作胶囊（Composer 20px 双环）
"""
from PySide6.QtCore import Qt, QEvent, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout,
                               QWidget)

import cds_tokens as T
from cds_tokens import T as TT          # 类属性（surface / text / border …）
from cds_widgets import CDSIconButton, _font
from qfluentwidgets import FluentIcon as FIF


def tint_icon(base, color: QColor, size: int = 16):
    """字体图标染色。qfluentwidgets 的 icon(color) 只对 SVG 生效——FIF 全是字体
    图标，color 会被静默忽略画成黑（深色侧栏上 = 隐形）。手动 SourceIn 填色。"""
    pm = base.icon().pixmap(size, size)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.drawPixmap(0, 0, pm)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(0, 0, size, size, color)
    p.end()
    return out

# 导航图标全部用 FluentIcon（Segoe Fluent Icons 字体，Windows 一定存在）；
# 别再自造 ◍▦⛒ 这类 Unicode 字形——在部分字体里是方块，看着不像成品。
NAV_ITEMS = [
    (FIF.PLAY, "扫描", "start_scan"),
    (FIF.PHOTO, "结果", "focus_grid"),
    (FIF.SETTING, "规则", "open_rule_editor"),
    (FIF.HISTORY, "历史", "open_history"),
    (FIF.BACK_TO_WINDOW, "隔离区", "open_quarantine"),
    (FIF.EDIT, "设置", "open_config"),
]


class CDSNavItem(QFrame):
    """侧栏导航项：中性实底选中态（CDS 选中态 A），32px 高。"""

    clicked = Signal(str)

    def __init__(self, icon, text, key, parent=None):
        super().__init__(parent)
        self.key = key
        self._icon = icon
        self._sel = False
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(T.H_SIDEBAR_ITEM)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(8)
        self.ico = QLabel(self)
        self.ico.setFixedSize(16, 16)
        lay.addWidget(self.ico)
        self.txt = QLabel(text, self)
        self.txt.setFont(_font(13))
        lay.addWidget(self.txt)
        lay.addStretch(1)
        self.badge = QLabel("", self)
        self.badge.setFont(_font(10, bold=True))
        self.badge.setFixedHeight(18)          # 否则被布局拉伸成全行高的橙块
        self.badge.hide()                       # 空文本 + padding 会撑出橙色小块，必须先藏
        lay.addWidget(self.badge)
        self.dark = True
        self.refresh_theme(True)

    def mouseReleaseEvent(self, e):
        self.clicked.emit(self.key)
        super().mouseReleaseEvent(e)

    def set_selected(self, on):
        self._sel = on
        self.refresh_theme(self.dark)

    def set_badge(self, n):
        self.badge.setText("" if not n else str(n))
        self.badge.setVisible(bool(n))

    def refresh_theme(self, dark: bool):
        self.dark = dark
        if self._sel:
            bg, fg = T.c(dark, TT.sel_neutral_bg), T.c(dark, TT.sel_neutral_text)
        else:
            bg, fg = "transparent", T.c(dark, TT.text_2)
        self.setStyleSheet(f"CDSNavItem {{ background: {bg}; border-radius: {T.R_LG}px; }}")
        self.ico.setPixmap(tint_icon(self._icon, QColor(fg), 16))
        self.txt.setStyleSheet(
            f"color: {fg}; background: transparent; font-weight: {'600' if self._sel else '400'};")
        self.badge.setStyleSheet(
            f"color: #fff; background: {T.CLAY};"
            f" border-radius: {T.R_MD}px; padding: 1px 6px;")


class CDSSidebar(QFrame):
    """侧栏：品牌头 + 导航 + 底部设置。宽度由布局模式决定。"""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.items = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 12, 10, 12)
        lay.setSpacing(2)
        head = QHBoxLayout()
        self.logo = QLabel("广", self)
        self.logo.setFixedSize(24, 24)
        self.logo.setAlignment(Qt.AlignCenter)
        self.logo.setFont(_font(12, bold=True))
        head.addWidget(self.logo)
        self.brand = QLabel("广告图过滤", self)
        self.brand.setFont(_font(13, bold=True))
        head.addWidget(self.brand)
        head.addStretch(1)
        lay.addLayout(head)
        lay.addSpacing(10)
        for glyph, text, key in NAV_ITEMS:
            it = CDSNavItem(glyph, text, key, self)
            it.clicked.connect(self._on_nav)
            self.items[key] = it
            lay.addWidget(it)
        lay.addStretch(1)
        self.dark = True
        self.refresh_theme(True)

    def _on_nav(self, key):
        app = self.app
        fn = {
            "start_scan": lambda: app.start_scan(),
            "focus_grid": lambda: app.grid.setFocus(),
            "open_rule_editor": lambda: app.build_rule_editor().exec(),
            "open_history": lambda: app.open_history(),
            "open_quarantine": lambda: app.restore_ui(),
            "open_config": lambda: app.open_config(),
        }.get(key)
        if fn:
            fn()
        for k, it in self.items.items():
            it.set_selected(k == key)

    def set_badges(self, **kw):
        for k, v in kw.items():
            if k in self.items:
                self.items[k].set_badge(v)

    def refresh_theme(self, dark: bool):
        self.dark = dark
        self.setStyleSheet(
            f"CDSSidebar {{ background: {T.c(dark, TT.sidebar)};"
            f" border-right: 1px solid {T.c(dark, TT.border)}; }}")
        self.logo.setStyleSheet(
            f"background: {T.CLAY}; color: #fff; border-radius: {T.R_MD}px;")
        self.brand.setStyleSheet(f"color: {T.c(dark, TT.text)};")
        for it in self.items.values():
            it.refresh_theme(dark)


class _Grip(QFrame):
    """可调分隔条（B：hover 主色、拖拽改宽、双击复位）。"""

    def __init__(self, target, min_w, max_w, step, on_resize, parent=None):
        super().__init__(parent)
        self.target, self.min_w, self.max_w, self.step = target, min_w, max_w, step
        self.on_resize = on_resize
        self.setCursor(Qt.SizeHorCursor)
        self.setFixedWidth(6)
        self._x0 = None
        self._w0 = None
        self.dark = True

    def mousePressEvent(self, e):
        self._x0, self._w0 = e.globalX(), self.target.width()

    def mouseMoveEvent(self, e):
        if self._x0 is None:
            return
        d = e.globalX() - self._x0
        w = max(self.min_w, min(self.max_w, self._w0 + d))
        w = round(w / self.step) * self.step
        self.target.setFixedWidth(int(w))
        self.on_resize(int(w))

    def mouseReleaseEvent(self, e):
        self._x0 = None

    def mouseDoubleClickEvent(self, e):
        self.target.setFixedWidth(self.min_w)
        self.on_resize(self.min_w)

    def enterEvent(self, e):
        self.setStyleSheet(f"background: {T.CLAY};")

    def leaveEvent(self, e):
        self.setStyleSheet("background: transparent;")


class LayoutManager:
    """在既有控件上重排三种骨架。"""

    MODES = ("studio", "workbench", "gallery")
    LABELS = {"studio": "D1 Studio", "workbench": "D2 工作台", "gallery": "D3 画廊"}

    def __init__(self, app):
        self.app = app
        self.mode = "studio"
        self.sidebar = None
        self.grip = None
        self._float_bar = None

    # ---------------- 主入口 ----------------
    def apply(self, mode: str):
        if mode not in self.MODES:
            return
        app = self.app
        self.mode = mode
        self._teardown()
        mid = getattr(app, "mid", None)
        if mid is None:
            return

        if mode == "studio":
            self.sidebar = CDSSidebar(app, app)
            self.sidebar.setFixedWidth(T.SIDEBAR_W)
            mid.insertWidget(0, self.sidebar)
            app.detail.setVisible(False)
            self._dock_bottom(True)

        elif mode == "workbench":
            self.sidebar = CDSSidebar(app, app)
            self.sidebar.setFixedWidth(288)
            mid.insertWidget(0, self.sidebar)
            self.grip = _Grip(self.sidebar, T.SIDEBAR_MIN, T.SIDEBAR_MAX, T.SIDEBAR_STEP,
                              lambda w: None, app)
            mid.insertWidget(1, self.grip)
            app.detail.setVisible(True)
            app.detail.setFixedWidth(T.DETAIL_W)
            self._dock_bottom(True)

        else:  # gallery
            app.detail.setVisible(False)
            self._dock_bottom(False)   # 底部改悬浮胶囊
        self.refresh_theme(getattr(app, "theme", "dark") == "dark")

    def _teardown(self):
        app = self.app
        mid = getattr(app, "mid", None)
        for w in (self.sidebar, self.grip):
            if w is not None:
                try:
                    if mid is not None:
                        mid.removeWidget(w)
                    w.setParent(None)      # 历史坑：只 deleteLater 会留在原位重叠绘制
                    w.deleteLater()
                except RuntimeError:
                    pass
        self.sidebar = self.grip = None
        if self._float_bar is not None:
            try:
                self._float_bar.setParent(None)
                self._float_bar.deleteLater()
            except RuntimeError:
                pass
            self._float_bar = None

    # ---------------- 底部条：停靠 ↔ 悬浮 ----------------
    def _dock_bottom(self, docked: bool):
        app = self.app
        bot = getattr(app, "bot", None)
        if bot is None:
            return
        root = getattr(app, "_root_layout", None)
        if docked:
            try:
                bot.setParent(app)
                if root is not None and root.indexOf(bot) < 0:
                    root.insertWidget(root.count() - 1, bot)
            except Exception:
                pass
            bot.setStyleSheet(getattr(app, "_bot_css", ""))
            bot.show()
        else:
            # 悬浮胶囊：Composer 同款 20px 圆角 + 双环（Qt 用 border 1px + 外描边模拟）
            if root is not None:
                try:
                    root.removeWidget(bot)
                except Exception:
                    pass
            bot.setParent(app)
            dark = getattr(app, "theme", "dark") == "dark"
            s3 = T.c(dark, TT.surface3)
            bot.setStyleSheet(
                f"#bottomBar {{ background: {s3}; border: 1px solid {T.c(dark, TT.border)};"
                f" border-radius: {T.R_COMPOSER}px; }}")
            bot.setFixedHeight(56)
            bot.show()
            bot.raise_()
            self._float_bar = bot
            self._place_float()

    def _place_float(self):
        app = self.app
        bot = self._float_bar
        if bot is None:
            return
        # 宽度按内容 sizeHint 自适应（硬编码 560 曾把按钮挤到重叠裁切），
        # 放不下时等比收紧内边距与间距，内容永不裁切
        bot.setFixedWidth(16777215)          # 先解锁再量
        hint = bot.sizeHint().width()
        avail = app.width() - 48
        w = max(360, min(hint, avail))
        if w < hint:
            bl = bot.layout()
            bl.setSpacing(max(2, bl.spacing() - 4))
            m = bl.contentsMargins()
            bl.setContentsMargins(max(6, m.left() - 4), m.top(),
                                  max(6, m.right() - 4), m.bottom())
        bot.setFixedWidth(w)
        bot.move((app.width() - w) // 2, app.height() - 72)

    def on_resize(self):
        if self.mode == "gallery":
            self._place_float()

    def refresh_theme(self, dark: bool):
        if self.sidebar is not None:
            self.sidebar.refresh_theme(dark)
        if self.grip is not None:
            self.grip.setStyleSheet("background: transparent;")
        if self.mode == "gallery":
            self._dock_bottom(False)

    def set_badges(self, **kw):
        if self.sidebar is not None:
            self.sidebar.set_badges(**kw)
