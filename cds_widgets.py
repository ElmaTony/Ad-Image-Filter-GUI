# -*- coding: utf-8 -*-
"""暖陶工作台基础交互组件：空状态、Toast 提示与快捷命令面板。"""
from PySide6.QtCore import Qt, QEvent, QPropertyAnimation, QTimer
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

import cds_tokens as T
from cds_tokens import T as TT


def _font(px=13, bold=False):
    f = QFont("Microsoft YaHei UI")
    f.setPixelSize(px)
    if bold:
        f.setBold(True)
    return f


# ============================================================ 空状态
class CDSEmptyState(QWidget):
    def __init__(self, glyph="", title="", desc="", parent=None):
        super().__init__(parent)
        self.dark = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 32, 24, 32)
        lay.setSpacing(6)
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


# ============================================================ Toast（右下角自消失）
class CDSToast(QFrame):
    _stack = []

    def __init__(self, parent, text, kind="ok"):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.kind = kind
        self._closing = False
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
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(T.DUR_BASE)
        self._anim.setEasingCurve(T.EASE_OUT)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self._fade_out)
        self.destroyed.connect(self._on_destroyed)

    def refresh_theme(self, dark: bool):
        color = {"ok": T.c(dark, TT.ok), "warn": T.c(dark, TT.warn), "err": T.c(dark, TT.danger)}.get(
            self.kind, T.c(dark, TT.ok))
        self.setStyleSheet(
            f"CDSToast {{ background: {T.c(dark, TT.surface2)}; border: 1px solid {T.c(dark, TT.border)};"
            f" border-radius: {T.R_MD}px; }}")
        self.ico.setStyleSheet(f"color: {color}; background: transparent;")
        self.lbl.setStyleSheet(f"color: {T.c(dark, TT.text)}; background: transparent;")

    @classmethod
    def _layout_stack(cls, host):
        alive = []
        for toast in cls._stack:
            try:
                if toast.parent() is host:
                    alive.append(toast)
            except RuntimeError:
                continue
        for idx, toast in enumerate(alive):
            max_w = max(120, host.width() - T.TOAST_MARGIN * 2)
            if toast.width() > max_w:
                toast.setMaximumWidth(max_w)
            toast.adjustSize()
            x = max(T.TOAST_MARGIN, host.width() - T.TOAST_MARGIN - toast.width())
            y = host.height() - T.TOAST_MARGIN - toast.height() - idx * (toast.height() + 8)
            toast.move(x, max(T.TOAST_MARGIN + 48, y))

    @classmethod
    def show(cls, parent, text, kind="ok"):
        t = cls(parent, text, kind)
        cls._stack.append(t)
        cls._layout_stack(parent)
        QFrame.show(t)
        t.raise_()
        t._dismiss_timer.start(T.TOAST_MS)
        return t

    def _fade_out(self):
        if self._closing:
            return
        self._closing = True
        self._anim.stop()
        self._out_anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._out_anim.setDuration(T.DUR_BASE)
        self._out_anim.setStartValue(self._effect.opacity())
        self._out_anim.setEndValue(0.0)
        self._out_anim.finished.connect(self._done)
        self._out_anim.start()

    def _on_destroyed(self, *_args):
        CDSToast._stack = [item for item in CDSToast._stack if item is not self]

    def _done(self):
        host = self.parent()
        if self in CDSToast._stack:
            CDSToast._stack.remove(self)
        self.close()
        self.deleteLater()
        if host is not None:
            CDSToast._layout_stack(host)


# ============================================================ 命令面板（Ctrl+K）
class CDSCommandPalette(QWidget):
    """浮层：输入框 + 结果列表；↑↓ 选择，Enter/单击 执行，Esc 或点击外部关闭。"""

    def __init__(self, parent, commands):
        super().__init__(parent)
        self.cmds = commands          # [(显示名, 回调), ...] 或 [(显示名, 回调, 是否可用), ...]
        self.dark = getattr(parent, 'theme', 'light') == 'dark'
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowFlags(Qt.Widget)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 12)
        lay.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel('快捷命令', self)
        title.setFont(_font(14, True))
        header.addWidget(title)
        header.addStretch()
        close = QPushButton('×', self)
        close.setAccessibleName('关闭命令面板')
        close.setCursor(Qt.PointingHandCursor)
        close.setFixedSize(28, 28)
        close.clicked.connect(self.close)
        header.addWidget(close)
        lay.addLayout(header)
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText("输入命令…（Esc 关闭）")
        self.edit.setFixedHeight(T.H_MD)
        self.edit.textChanged.connect(self._filter)
        self.edit.returnPressed.connect(self._run_current)
        self.edit.installEventFilter(self)
        lay.addWidget(self.edit)
        self.list = QListWidget(self)
        self.list.setFrameShape(QListWidget.NoFrame)
        self.list.itemClicked.connect(lambda _i: self._run_current())
        self.list.itemActivated.connect(lambda _i: self._run_current())
        lay.addWidget(self.list)
        hint = QLabel('↑ ↓ 选择    Enter 或单击执行    Esc 关闭', self)
        hint.setObjectName('commandHint')
        lay.addWidget(hint)
        self._escape = QShortcut(QKeySequence('Esc'), self)
        self._escape.setContext(Qt.WidgetWithChildrenShortcut)
        self._escape.activated.connect(self.close)
        self._host = parent
        if parent is not None:
            parent.installEventFilter(self)
        self._filter("")
        self.refresh_theme(self.dark)
        self.reposition(parent)
        self.edit.setFocus()

    def closeEvent(self, event):
        host = getattr(self, "_host", None)
        if host is not None:
            try:
                host.removeEventFilter(self)
            except RuntimeError:
                pass
            self._host = None
        super().closeEvent(event)

    def reposition(self, parent):
        if parent is None:
            self.resize(540, 430)
            return
        w = max(280, min(540, parent.width() - 32))
        top_margin = min(96, max(44, (parent.height() - 260) // 4))
        h = max(220, min(430, parent.height() - top_margin - 20))
        self.resize(w, h)
        x = max(16, (parent.width() - w) // 2)
        y = max(44, min(top_margin, parent.height() - h - 16))
        self.move(x, y)

    def refresh_theme(self, dark: bool):
        self.dark = dark
        accent = T.c(dark, TT.clay)
        self.setStyleSheet(
            f"CDSCommandPalette {{ background: {T.c(dark, TT.surface2)};"
            f" border: 1px solid {T.c(dark, TT.border)}; border-radius: {T.R_XL}px; }}"
            f"QLabel {{ color:{T.c(dark, TT.text)}; background:transparent; }}"
            f"QLabel#commandHint {{ color:{T.c(dark, TT.text_2)}; font-size:11px; }}"
            f"QPushButton {{ color:{T.c(dark, TT.text_2)}; border:none; border-radius:6px; }}"
            f"QPushButton:hover {{ background:{T.c(dark, TT.clay_fill)}; color:{accent}; }}")
        self.edit.setStyleSheet(
            f"QLineEdit {{ background: {T.c(dark, TT.surface3)}; border: 1px solid {T.c(dark, TT.border)};"
            f" border-radius: {T.R_MD}px; padding: 0 10px; color: {T.c(dark, TT.text)}; }}"
            f"QLineEdit:focus {{ border-color: {accent}; }}")
        self.list.setStyleSheet(
            f"QListWidget {{ background: transparent; color: {T.c(dark, TT.text)}; }}"
            f"QListWidget::item {{ padding: 7px 10px; border-radius: {T.R_SM}px; }}"
            f"QListWidget::item:selected {{ background: {T.c(dark, TT.clay_fill)}; color: {accent}; font-weight: 600; }}")

    def _filter(self, q):
        self.list.clear()
        ql = q.strip().lower()
        first_enabled = -1
        for entry in self.cmds:
            name, fn = entry[0], entry[1]
            enabled = bool(entry[2]) if len(entry) > 2 else True
            if ql and ql not in name.lower():
                continue
            it = QListWidgetItem(name if enabled else f"{name}（当前不可用）")
            it.setData(Qt.UserRole, fn)
            it.setData(Qt.UserRole + 1, enabled)
            if not enabled:
                it.setFlags(it.flags() & ~Qt.ItemIsEnabled)
            elif first_enabled < 0:
                first_enabled = self.list.count()
            self.list.addItem(it)
        if first_enabled >= 0:
            self.list.setCurrentRow(first_enabled)
        elif self.list.count():
            self.list.setCurrentRow(0)

    def _run_current(self):
        if not self.isVisible():
            return
        it = self.list.currentItem()
        if it is None or it.data(Qt.UserRole + 1) is False:
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

    def eventFilter(self, watched, event):
        if watched is getattr(self, "_host", None) and event.type() == QEvent.MouseButtonPress:
            if self.isVisible() and not self.geometry().contains(event.position().toPoint()):
                self.close()
            return False
        if watched is self.edit and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self.close()
                return True
            if event.key() in (Qt.Key_Up, Qt.Key_Down):
                self.list.keyPressEvent(event)
                return True
        return super().eventFilter(watched, event)

    @classmethod
    def popup(cls, parent, commands):
        p = cls(parent, commands)
        p.show()
        p.raise_()
        p.edit.setFocus(Qt.ShortcutFocusReason)
        return p
