# -*- coding: utf-8 -*-
"""CDS 组件库 —— 按 storybook / claudecodeui 的真实规格在 Qt 里落地。

约定：
  * 每个组件实现 `refresh_theme(dark: bool)`，主窗口主题切换时统一重放
    （历史坑：qfluentwidgets 子组件自带样式表会盖全局 QSS，必须走 setCustomStyleSheet 或逐组件下发）
  * 毛玻璃一律不用；半透明只保留在子控件（遮罩）上，容器拍平为实色
  * 动效时长/曲线只取 cds_tokens 里的常量，禁止临时数值
"""
from PySide6.QtCore import (QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRect, QRectF,
                            Qt, QTimer, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QSizePolicy,
                               QStyledItemDelegate, QToolTip, QVBoxLayout, QWidget)

import cds_tokens as T
from cds_tokens import T as TT          # 主题类属性（surface / text / border / muted …）

FS = "Microsoft YaHei UI"


def _font(size: int, bold: bool = False) -> QFont:
    f = QFont(FS, size)
    f.setBold(bold)
    return f


# ============================================================ 按钮
class CDSButton(QPushButton):
    """四档高度 24/28/32/40；Primary=clay 实心 / Secondary=描边 / Ghost=透明。

    active:scale-95 用按下时轻微缩小模拟（Qt 无 transform，改画内缩 padding）。
    """

    def __init__(self, text="", variant="secondary", size=32, parent=None):
        super().__init__(text, parent)
        self.variant = variant
        self.h = size
        self.setFixedHeight(size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(_font(12, bold=(variant == "primary")))
        self.setObjectName(f"cdsBtn_{variant}")
        self.dark = True
        self.refresh_theme(True)

    def refresh_theme(self, dark: bool):
        self.dark = dark
        t = T.c(dark, TT.text)
        a2 = T.ALPHA["dark" if dark else "light"][1]
        if self.variant == "primary":
            css = (f"QPushButton {{ background: {T.CLAY}; color: #fff; border: none;"
                   f" border-radius: {T.R_LG}px; padding: 0 16px; font-weight: 600; }}"
                   f"QPushButton:hover {{ background: {T.c(dark, T.CLAY_HOVER)}; }}"
                   f"QPushButton:pressed {{ background: {T.CLAY_ACTIVE}; padding: 0 15px; }}"
                   f"QPushButton:disabled {{ background: {T.c(dark, TT.muted)};"
                   f" color: {T.c(dark, TT.text_3)}; }}")
        elif self.variant == "ghost":
            css = (f"QPushButton {{ background: transparent; border: none;"
                   f" border-radius: {T.R_LG}px; padding: 0 10px; color: {T.c(dark, TT.text_2)}; }}"
                   f"QPushButton:hover {{ background: {T.c(dark, TT.hover)}; color: {t}; }}"
                   f"QPushButton:pressed {{ background: {T.c(dark, TT.muted)}; }}")
        else:
            # CDS 硬规则：次级按钮**严禁边框**，用 rgba 填充表达层级
            css = (f"QPushButton {{ background: rgba(0,0,0,{a2 if dark else a2}); color: {t};"
                   f" border: none; border-radius: {T.R_LG}px; padding: 0 14px; }}"
                   f"QPushButton:hover {{ background: {T.c(dark, TT.hover)}; }}"
                   f"QPushButton:pressed {{ background: {T.c(dark, TT.muted)}; padding: 0 13px; }}")
        self.setStyleSheet(css)


class CDSIconButton(QPushButton):
    """32×32 图标按钮（B：桌面 28 / 这里统一 32 便于点按）。"""

    def __init__(self, glyph="", tip="", parent=None, size=32):
        super().__init__(glyph, parent)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(_font(13))
        self.setToolTip(tip)
        self.dark = True
        self.refresh_theme(True)

    def refresh_theme(self, dark: bool):
        self.dark = dark
        self.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; border-radius: {T.R_MD}px;"
            f" color: {T.c(dark, TT.text_2)}; }}"
            f"QPushButton:hover {{ background: {T.c(dark, TT.hover)}; color: {T.c(dark, TT.text)}; }}"
            f"QPushButton:pressed {{ background: {T.c(dark, TT.muted)}; }}")


# ============================================================ 分段控件（A：36px / 2px / 999）
class CDSSegmented(QWidget):
    """高 36、轨道 padding 2、圆角 999；滑块几何等分，150ms ease-snap。"""

    currentChanged = Signal(int)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.options = options          # [(显示名, 值), ...]
        self.btns = []
        self._idx = 0
        self.dark = True
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        self.slider = QLabel(self)
        self.slider.setAttribute(Qt.WA_TransparentForMouseEvents)
        for i, (name, _v) in enumerate(options):
            b = QPushButton(name, self)
            b.setCursor(Qt.PointingHandCursor)
            b.setFont(_font(12))
            b.setObjectName("cdsSegBtn")
            b.clicked.connect(lambda _c, k=i: self.set_index(k))
            self.btns.append(b)
            lay.addWidget(b)
        self.setFixedHeight(36)
        self.refresh_theme(True)
        QTimer.singleShot(0, lambda: self._place(False))

    def refresh_theme(self, dark: bool):
        self.dark = dark
        sel = T.c(dark, TT.sel_neutral_bg)
        txt = T.c(dark, TT.text_2)
        self.setStyleSheet(
            f"#cdsSegmented {{ background: {T.c(dark, TT.muted)}; border-radius: {T.R_FULL}px; }}"
            f"QPushButton#cdsSegBtn {{ background: transparent; border: none; color: {txt};"
            f" border-radius: {T.R_FULL}px; padding: 0 10px; }}"
            f"QPushButton#cdsSegBtn:hover {{ color: {T.c(dark, TT.text)}; }}")
        self.setObjectName("cdsSegmented")
        self.slider.setStyleSheet(
            f"background: {sel}; border-radius: {T.R_FULL}px;"
            f" border: 1px solid {T.c(dark, TT.border_soft)};")
        for b in self.btns:
            b.setStyleSheet(b.styleSheet())

    def index(self):
        return self._idx

    def value(self):
        return self.options[self._idx][1]

    def set_index(self, i, animate=True):
        if i == self._idx and animate:
            return
        self._idx = max(0, min(len(self.btns) - 1, i))
        self._place(animate)
        self.currentChanged.emit(self._idx)

    def _target_rect(self):
        n = len(self.btns)
        if not n:
            return QRect(0, 0, 0, 0)
        w = (self.width() - 4) / n
        return QRect(int(2 + self._idx * w), 2, int(w), self.height() - 4)

    def _place(self, animate=True):
        r = self._target_rect()
        if not animate or not self.isVisible():
            self.slider.setGeometry(r)
            return
        anim = QPropertyAnimation(self.slider, b"geometry", self)
        anim.setDuration(T.ANIM_SEGMENT)
        anim.setEasingCurve(T.EASE_SNAP)
        anim.setStartValue(self.slider.geometry())
        anim.setEndValue(r)
        anim.start(QPropertyAnimation.DeleteWhenStopped)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place(False)


# ============================================================ 文件夹 Chip（含进度 + ✕）
class CDSChip(QFrame):
    """高 28、圆角 999；`相册A 6/48` → `✓ 相册A 48`；当前扫描项 clay 高亮。"""

    removeRequested = Signal(str)

    def __init__(self, path, name, parent=None):
        super().__init__(parent)
        self.path = path
        self._name = name
        self._done = False
        self._active = False
        self.dark = True
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 4, 0)
        lay.setSpacing(6)
        self.lbl = QLabel(name, self)
        self.lbl.setFont(_font(12))
        lay.addWidget(self.lbl)
        self.btn_x = QPushButton("✕", self)
        self.btn_x.setFixedSize(16, 16)
        self.btn_x.setCursor(Qt.PointingHandCursor)
        self.btn_x.setFont(_font(10))
        self.btn_x.setToolTip("移除该文件夹")
        self.btn_x.clicked.connect(lambda: self.removeRequested.emit(self.path))
        lay.addWidget(self.btn_x)
        self.setFixedHeight(28)
        self.refresh_theme(True)

    def refresh_theme(self, dark: bool):
        self.dark = dark
        self._restyle()

    def _restyle(self):
        dark = self.dark
        if self._active:
            bg, bd, fg = T.c(dark, TT.clay_badge), T.c(dark, TT.border_clay), T.CLAY
        elif self._done:
            bg, bd, fg = T.c(dark, TT.muted), T.c(dark, TT.border_soft), T.c(dark, TT.text_2)
        else:
            bg, bd, fg = T.c(dark, TT.surface2), T.c(dark, TT.border), T.c(dark, TT.text)
        self.setStyleSheet(
            f"CDSChip {{ background: {bg}; border: 1px solid {bd}; border-radius: {T.R_FULL}px; }}")
        self.lbl.setStyleSheet(f"color: {fg}; background: transparent;")
        self.btn_x.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {T.c(dark, TT.text_3)};"
            f" border-radius: 8px; }}"
            f"QPushButton:hover {{ background: {T.c(dark, TT.hover)}; color: {T.c(dark, TT.text)}; }}")

    def text(self):
        return self.lbl.text()

    def set_state(self, text, done=False, active=False):
        self.lbl.setText(text)
        self._done, self._active = done, active
        self.setProperty("active", active)
        self._restyle()


# ============================================================ 安静三点（A：1.9s，延迟 .15 递进）
class CDSDots(QWidget):
    """尺寸 3px、间距 3.5px，轨迹 +2 → -2.5 → 0，clay 色。"""

    _PHASES = (0.0, 0.15, 0.30)
    _KEYS = ((0.0, 0.0, 2.0), (0.08, 0.0, 2.0), (0.22, 1.0, -2.5),
             (0.36, 1.0, 0.0), (0.70, 1.0, 0.0), (0.90, 0.0, 0.0), (1.0, 0.0, 0.0))

    def __init__(self, parent=None, color=None):
        super().__init__(parent)
        self.setFixedSize(22, 8)
        self.color = QColor(color or T.CLAY)
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self._running = False

    def start(self):
        self._running = True
        self._timer.start()
        self.show()

    def stop(self):
        self._running = False
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._t = (self._t + 0.04 / (T.ANIM_DOTS / 1000)) % 1.0
        self.update()

    @staticmethod
    def _interp(ph):
        ks = CDSDots._KEYS
        for i in range(len(ks) - 1):
            a, b = ks[i], ks[i + 1]
            if a[0] <= ph <= b[0]:
                r = (ph - a[0]) / max(1e-6, b[0] - a[0])
                return a[1] + (b[1] - a[1]) * r, a[2] + (b[2] - a[2]) * r
        return 0.0, 0.0

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        for i in range(3):
            ph = (self._t - self._PHASES[i]) % 1.0
            a, dy = self._interp(ph)
            col = QColor(self.color)
            col.setAlphaF(max(0.0, min(1.0, a)))
            p.setBrush(col)
            p.drawEllipse(QRectF(i * 7.0, 3.5 + dy, 3, 3))


# ============================================================ 细进度条（4px）
class CDSProgress(QWidget):
    """h=4 圆角 999，clay 填充；不确定态走骨架扫光。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(4)
        self._v = 0.0
        self._indet = False
        self.dark = True

    def set_value(self, v: float, indeterminate=False):
        self._v = max(0.0, min(1.0, v))
        self._indet = indeterminate
        self.update()

    def refresh_theme(self, dark: bool):
        self.dark = dark
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.c(self.dark, T.muted)))
        p.drawRoundedRect(QRectF(0, 0, self.width(), 4), 2, 2)
        if self._indet:
            g = QLinearGradient(0, 0, self.width(), 0)
            g.setColorAt(0, QColor(0, 0, 0, 0))
            g.setColorAt(0.5, QColor(T.CLAY))
            g.setColorAt(1, QColor(0, 0, 0, 0))
            p.setBrush(g)
            p.drawRoundedRect(QRectF(0, 0, self.width(), 4), 2, 2)
        else:
            p.setBrush(QColor(T.CLAY))
            p.drawRoundedRect(QRectF(0, 0, self.width() * self._v, 4), 2, 2)


# ============================================================ 空状态（B：48 图标底 + 标题 + 描述）
class CDSEmptyState(QWidget):
    def __init__(self, glyph="", title="", desc="", parent=None):
        super().__init__(parent)
        self.dark = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 32, 24, 32)
        lay.setSpacing(6)
        # **不再显示字符图标**：◍ 之类的符号在 Windows 字体下会渲染成怪异的网格球/
        # 实心方块，长期被误认为"消不掉的黑色方块 bug"。空状态只保留文字，干净无歧义。
        self.badge = QLabel("", self)
        self.badge.setVisible(False)
        self.title = QLabel(title, self)
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setFont(_font(13, bold=True))
        lay.addWidget(self.title)
        self.desc = QLabel(desc, self)
        self.desc.setAlignment(Qt.AlignCenter)
        self.desc.setFont(_font(12))
        self.desc.setWordWrap(True)
        lay.addWidget(self.desc)
        self.refresh_theme(True)

    def refresh_theme(self, dark: bool):
        self.dark = dark
        self.title.setStyleSheet(f"color: {T.c(dark, TT.text)};")
        self.desc.setStyleSheet(f"color: {T.c(dark, TT.text_2)};")


# ============================================================ Toast（B：右下 3000ms）
class CDSToast(QFrame):
    _stack = []

    def __init__(self, parent, text, kind="ok"):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.kind = kind
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        icon = {"ok": "✓", "warn": "!", "err": "✕"}.get(kind, "✓")
        self.ico = QLabel(icon, self)
        self.ico.setFont(_font(12, bold=True))
        self.lbl = QLabel(text, self)
        self.lbl.setFont(_font(12))
        lay.addWidget(self.ico)
        lay.addWidget(self.lbl)
        self.setFixedHeight(36)
        self.adjustSize()
        self.refresh_theme(getattr(parent, "theme", "dark") == "dark")
        self._op = 0.0
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(T.DUR_BASE)
        self._anim.setEasingCurve(T.EASE_OUT)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def get_op(self):
        return self._op

    def set_op(self, v):
        self._op = v
        self.setWindowOpacity(v)

    def refresh_theme(self, dark: bool):
        color = {"ok": T.c(dark, TT.ok), "warn": T.c(dark, TT.warn), "err": T.c(dark, TT.danger)}.get(
            self.kind, T.c(dark, TT.ok))
        self.setStyleSheet(
            f"CDSToast {{ background: {T.c(dark, TT.surface2)}; border: 1px solid {T.c(dark, TT.border)};"
            f" border-radius: {T.R_MD}px; }}")
        self.ico.setStyleSheet(f"color: {color}; background: transparent;")
        self.lbl.setStyleSheet(f"color: {T.c(dark, TT.text)}; background: transparent;")

    @classmethod
    def show(cls, parent, text, kind="ok"):
        t = cls(parent, text, kind)
        cls._stack.append(t)
        host = parent
        t.adjustSize()
        y = host.height() - T.TOAST_MARGIN - t.height() - (len(cls._stack) - 1) * (t.height() + 8)
        t.move(host.width() - T.TOAST_MARGIN - t.width(), max(T.TOAST_MARGIN + 48, y))
        t.show()
        QTimer.singleShot(T.TOAST_MS, t._fade_out)
        return t

    def _fade_out(self):
        a = QPropertyAnimation(self, b"windowOpacity", self)
        a.setDuration(T.DUR_BASE)
        a.setStartValue(1.0)
        a.setEndValue(0.0)
        a.finished.connect(self._done)
        a.start(QPropertyAnimation.DeleteWhenStopped)

    def _done(self):
        if self in CDSToast._stack:
            CDSToast._stack.remove(self)
        self.close()
        self.deleteLater()


# ============================================================ 骨架（扫光占位）
class CDSSkeleton(QWidget):
    """卡片占位：muted 底 + 斜向扫光（A：cds-skeleton-sweep）。"""

    def __init__(self, parent=None, radius=T.R_LG):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.radius = radius
        self._p = -1.0
        self.dark = True
        self._t = QTimer(self)
        self._t.setInterval(32)
        self._t.timeout.connect(self._tick)
        self._t.start()

    def _tick(self):
        self._p += 32 / T.ANIM_SKELETON
        if self._p > 1.0:
            self._p = -1.0
        self.update()

    def refresh_theme(self, dark: bool):
        self.dark = dark
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.c(self.dark, T.muted)))
        p.drawRoundedRect(QRectF(self.rect()), self.radius, self.radius)
        p.setClipRect(self.rect())
        # CDS 原文：2s linear、105deg、transparent 35% → sheen 50% → transparent 65%
        import math
        rad = math.radians(T.SHEEN_DEG)
        span = self.width() + self.height()
        x = -self.height() + self._p * span * 1.6
        g = QLinearGradient(x, 0, x + self.height() * math.cos(rad) + 40,
                            self.height() * math.sin(rad))
        c = QColor(T.c(self.dark, T.text_3))
        c.setAlphaF(0.14)
        g.setColorAt(0, QColor(0, 0, 0, 0))
        g.setColorAt(0.5, c)
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(g)
        p.drawRect(self.rect())


# ============================================================ 折叠分组（A：0fr→1fr 等价实现）
class CDSAccordion(QWidget):
    """标题行点击展开/收起内容（Qt 无 grid 0fr，用高度动画等价）。"""

    def __init__(self, title="", parent=None, open_=False):
        super().__init__(parent)
        self.dark = True
        self._open = open_
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.head = QPushButton(title, self)
        self.head.setCursor(Qt.PointingHandCursor)
        self.head.setFont(_font(12, bold=True))
        self.head.setFixedHeight(T.H_SM)
        self.head.clicked.connect(self.toggle)
        lay.addWidget(self.head)
        self.body = QWidget(self)
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 6, 0, 6)
        lay.addWidget(self.body)
        self.refresh_theme(True)
        self.body.setMaximumHeight(0 if not open_ else 16777215)
        self.body.setVisible(open_)

    def add(self, w):
        self.body_lay.addWidget(w)

    def toggle(self):
        self._open = not self._open
        self.body.setVisible(True)
        start = 0 if self._open else self.body.sizeHint().height()
        end = self.body.sizeHint().height() if self._open else 0
        a = QPropertyAnimation(self.body, b"maximumHeight", self)
        a.setDuration(T.DUR_BASE)
        a.setEasingCurve(QEasingCurve(QEasingCurve.InOutQuad))
        a.setStartValue(start)
        a.setEndValue(max(1, end))
        a.finished.connect(lambda: self.body.setVisible(self._open))
        a.start(QPropertyAnimation.DeleteWhenStopped)
        self.head.setText(self.head.text().replace("▸", "▾") if self._open
                          else self.head.text().replace("▾", "▸"))

    def refresh_theme(self, dark: bool):
        self.dark = dark
        self.head.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; text-align: left;"
            f" padding-left: 2px; color: {T.c(dark, TT.text)}; }}"
            f"QPushButton:hover {{ color: {T.CLAY}; }}")
        self.body.setStyleSheet("background: transparent;")


# ============================================================ 命令面板（B：⌘K）
class CDSCommandPalette(QWidget):
    """浮层：输入框 + 结果列表；↑↓ 选择，Enter 执行，Esc 关闭。"""

    def __init__(self, parent, commands):
        super().__init__(parent)
        self.cmds = commands          # [(显示名, 回调), ...]
        self.dark = True
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowFlags(Qt.FramelessWindowHint)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText("输入命令…（Esc 关闭）")
        self.edit.setFixedHeight(T.H_MD)
        self.edit.textChanged.connect(self._filter)
        self.edit.returnPressed.connect(self._run_current)
        lay.addWidget(self.edit)
        self.list = QListWidget(self)
        self.list.setFrameShape(QListWidget.NoFrame)
        self.list.itemActivated.connect(lambda _i: self._run_current())
        lay.addWidget(self.list)
        self._filter("")
        self.refresh_theme(True)
        self.resize(520, 380)
        self.edit.setFocus()

    def refresh_theme(self, dark: bool):
        self.dark = dark
        self.setStyleSheet(
            f"CDSCommandPalette {{ background: {T.c(dark, TT.surface2)};"
            f" border: 1px solid {T.c(dark, TT.border)}; border-radius: {T.R_XL}px; }}")
        self.edit.setStyleSheet(
            f"QLineEdit {{ background: {T.c(dark, TT.surface3)}; border: 1px solid {T.c(dark, TT.border)};"
            f" border-radius: {T.R_MD}px; padding: 0 10px; color: {T.c(dark, TT.text)}; }}")
        self.list.setStyleSheet(
            f"QListWidget {{ background: transparent; color: {T.c(dark, TT.text)}; }}"
            f"QListWidget::item {{ padding: 7px 10px; border-radius: {T.R_SM}px; }}"
            f"QListWidget::item:selected {{ background: {T.c(dark, TT.clay_fill)}; color: {T.CLAY}; }}")

    def _filter(self, q):
        self.list.clear()
        ql = q.strip().lower()
        for name, fn in self.cmds:
            if ql and ql not in name.lower():
                continue
            it = QListWidgetItem(name)
            it.setData(Qt.UserRole, fn)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _run_current(self):
        it = self.list.currentItem()
        if it is None:
            return
        fn = it.data(Qt.UserRole)
        self.close()
        if callable(fn):
            fn()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
        elif e.key() in (Qt.Key_Up, Qt.Key_Down):
            self.list.keyPressEvent(e)
        else:
            super().keyPressEvent(e)

    @classmethod
    def popup(cls, parent, commands):
        p = cls(parent, commands)
        p.move(parent.x() + (parent.width() - p.width()) // 2, parent.y() + 96)
        p.show()
        return p


# ============================================================ Tooltip 延时（B：350ms / 8px）
def install_tooltip_policy(delay=T.TOOLTIP_DELAY_MS):
    """Qt 只有一个全局 tooltip 延时；按 B 的 350ms 统一设置。"""
    try:
        QApplication.instance().setStyleSheet(
            QApplication.instance().styleSheet() +
            f"QToolTip {{ padding: 4px 8px; border-radius: {T.R_SM}px; }}")
    except Exception:
        pass
    return delay


# ============================================================ 点击涟漪（A：click-ring）
class CDSClickRing:
    """在控件上叠加一圈扩散消失的涟漪；用法：CDSClickRing.play(widget, pos)。"""

    @staticmethod
    def play(widget, pos: QPoint, color=QColor(217, 119, 87)):
        ov = QLabel(widget)
        ov.setAttribute(Qt.WA_TransparentForMouseEvents)
        ov.setGeometry(widget.rect())
        ov.show()
        a = QPropertyAnimation(ov, b"windowOpacity", widget)
        a.setDuration(T.ANIM_CLICK_RING)
        a.setStartValue(0.95)
        a.setEndValue(0.0)
        a.finished.connect(ov.deleteLater)
        a.start(QPropertyAnimation.DeleteWhenStopped)
        return ov
