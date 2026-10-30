# -*- coding: utf-8 -*-
"""工作台配套窗口：统一标题、内容分区和固定操作栏。"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QPushButton, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)
from qfluentwidgets import PrimaryPushButton, PushButton, isDarkTheme
import cds_tokens as T


class DialogHeader(QFrame):
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()
        super().mousePressEvent(event)


class WorkbenchDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setModal(True)
        self.setObjectName('dialogWindow')
        self.widget = self
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.setSpacing(0)
        self.viewLayout = QVBoxLayout()
        self.viewLayout.setContentsMargins(24, 20, 24, 20)
        self.viewLayout.setSpacing(14)
        self.vBoxLayout.addLayout(self.viewLayout, 1)
        self.footer = QFrame(self)
        self.footer.setObjectName('dialogFooter')
        row = QHBoxLayout(self.footer)
        row.setContentsMargins(24, 14, 24, 14)
        self.footer_hint = QLabel('Esc 关闭', self.footer)
        self.footer_hint.setObjectName('mutedText')
        row.addWidget(self.footer_hint)
        row.addStretch()
        self.cancelButton = PushButton('取消', self.footer)
        self.yesButton = PrimaryPushButton('确定', self.footer)
        for button in (self.cancelButton, self.yesButton):
            button.setMinimumWidth(96)
            button.setFixedHeight(36)
        row.addWidget(self.cancelButton)
        row.addWidget(self.yesButton)
        self.vBoxLayout.addWidget(self.footer)
        self.cancelButton.clicked.connect(self.reject)
        self.yesButton.clicked.connect(self._confirm)
        self._escape = QShortcut(QKeySequence('Esc'), self)
        self._escape.setContext(Qt.WidgetWithChildrenShortcut)
        self._escape.activated.connect(self.reject)

    def _confirm(self):
        if self.validate():
            self.accept()

    def validate(self):
        return True

    def showEvent(self, event):
        super().showEvent(event)
        if self.parentWidget():
            center = self.parentWidget().mapToGlobal(self.parentWidget().rect().center())
            self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)


def section(title, description='', parent=None):
    frame = QFrame(parent)
    frame.setObjectName('settingsSection')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 18)
    layout.setSpacing(12)
    label = QLabel(title, frame)
    label.setObjectName('sectionTitle')
    layout.addWidget(label)
    if description:
        hint = QLabel(description, frame)
        hint.setWordWrap(True)
        hint.setObjectName('mutedText')
        layout.addWidget(hint)
    return frame, layout


def navigation(dialog, entries):
    """entries: [(标题, 页面)]，页面在自身内部处理滚动。"""
    body = QWidget(dialog)
    layout = QHBoxLayout(body)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(20)
    nav = QListWidget(body)
    nav.setObjectName('dialogNavigation')
    nav.setFixedWidth(144)
    nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    stack = QStackedWidget(body)
    for title, page in entries:
        nav.addItem(title)
        stack.addWidget(page)
    nav.currentRowChanged.connect(stack.setCurrentIndex)
    nav.setCurrentRow(0)
    layout.addWidget(nav)
    layout.addWidget(stack, 1)
    dialog.navigation = nav
    dialog.page_stack = stack
    return body


def style_dialog(dialog):
    dark = isDarkTheme()
    c = lambda token: T.c(dark, token)
    surface, panel, text, muted, border = [c(x) for x in
        (T.T.surface0, T.T.surface1, T.T.text, T.T.text_2, T.T.border)]
    dialog.setStyleSheet(
        f'QDialog#dialogWindow {{ background:{surface}; border:1px solid {border}; border-radius:16px; }}'
        f'QLabel {{ color:{text}; font-family:"Microsoft YaHei UI"; background:transparent; }}'
        f'QLabel#mutedText {{ color:{muted}; font-size:12px; }}'
        f'QLabel#sectionTitle {{ font-size:14px; font-weight:600; }}'
        f'QLabel#dialogTitle {{ font-size:22px; font-weight:600; }}'
        f'QFrame#settingsSection {{ background:{panel}; border:1px solid {border}; border-radius:12px; }}'
        f'QFrame#dialogHeader {{ border-bottom:1px solid {border}; }}'
        f'QFrame#dialogFooter {{ background:{panel}; border-top:1px solid {border}; border-bottom-left-radius:16px; border-bottom-right-radius:16px; }}'
        f'QListWidget#dialogNavigation {{ background:{panel}; border:none; border-radius:10px; padding:6px; color:{text}; outline:none; }}'
        f'QListWidget#dialogNavigation::item {{ padding:12px 10px; border-radius:7px; margin:2px 0; }}'
        f'QListWidget#dialogNavigation::item:selected {{ background:{c(T.T.clay_fill)}; color:{T.CLAY}; }}'
        f'QPushButton#dialogClose {{ background:transparent; border:none; border-radius:8px; color:{muted}; font-size:20px; }}'
        f'QPushButton#dialogClose:hover {{ background:{c(T.T.surface3)}; color:{text}; }}'
        f'QLineEdit {{ background:{c(T.T.surface2)}; color:{text}; border:1px solid {border}; border-radius:7px; padding:7px 10px; selection-background-color:{T.BRAND}; }}'
        f'QLineEdit:focus {{ border-color:{T.BRAND}; }}'
        f'QSpinBox {{ background:{c(T.T.surface2)}; color:{text}; border:1px solid {border}; border-radius:6px; padding:5px; }}'
        f'QScrollArea, QScrollArea > QWidget > QWidget {{ background:{surface}; border:none; }}'
    )
    # 将旧版正文中的标题与说明移到固定页头，其余内容可滚动。
    title = dialog.viewLayout.takeAt(0).widget()
    title.setObjectName('dialogTitle')
    dialog.setWindowTitle(title.text())
    header = DialogHeader(dialog)
    header.setObjectName('dialogHeader')
    row = QHBoxLayout(header)
    row.setContentsMargins(24, 18, 18, 18)
    words = QVBoxLayout()
    words.setSpacing(6)
    words.addWidget(title)
    first = dialog.viewLayout.itemAt(0)
    if first and isinstance(first.widget(), QLabel) and getattr(dialog, 'header_description', True):
        subtitle = dialog.viewLayout.takeAt(0).widget()
        subtitle.setObjectName('mutedText')
        subtitle.setWordWrap(True)
        words.addWidget(subtitle)
    row.addLayout(words, 1)
    dialog.closeButton = QPushButton('×', header)
    dialog.closeButton.setObjectName('dialogClose')
    dialog.closeButton.setAccessibleName('关闭窗口')
    dialog.closeButton.setFixedSize(32, 32)
    dialog.closeButton.clicked.connect(dialog.reject)
    row.addWidget(dialog.closeButton, 0, Qt.AlignTop)
    content = QWidget(dialog)
    dialog.vBoxLayout.removeItem(dialog.viewLayout)
    dialog.viewLayout.setParent(None)
    content.setLayout(dialog.viewLayout)
    scroll = QScrollArea(dialog)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    dialog.content_scroll = scroll
    dialog.vBoxLayout.insertWidget(0, scroll, 1)
    dialog.vBoxLayout.insertWidget(0, header)
    screen = dialog.screen()
    available = screen.availableGeometry() if screen else dialog.parentWidget().geometry()
    # 固定宽度由各窗口指定；桌面较窄时仍保留可操作边界。
    width = min(dialog.width(), available.width() - 40)
    dialog.setMinimumWidth(width)
    dialog.setMaximumWidth(width)
    dialog.setMinimumHeight(0)
    height = min(getattr(dialog, 'preferred_height', 660), available.height() - 48)
    dialog.setMaximumHeight(height)
    dialog.resize(width, height)


class NoticeDialog(WorkbenchDialog):
    def __init__(self, parent, title, content, confirmation=True):
        super().__init__(parent)
        self.setFixedWidth(540)
        self.preferred_height = 340
        self.header_description = False
        self.viewLayout.addWidget(QLabel(title, self))
        text = QLabel(content, self)
        text.setWordWrap(True)
        text.setTextFormat(Qt.PlainText)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.viewLayout.addWidget(text)
        self.viewLayout.addStretch()
        self.yesButton.setText('继续' if confirmation else '知道了')
        self.cancelButton.setVisible(confirmation)
        if confirmation and any(word in title for word in ('删除', '危险')):
            self.yesButton.setStyleSheet('QPushButton { background:#bd493c; color:white; border:none; border-radius:6px; }')
        style_dialog(self)
