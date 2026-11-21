# -*- coding: utf-8 -*-
"""工作台配套窗口：统一标题、内容分区和固定操作栏。"""
from html import escape
from queue import Empty, SimpleQueue
from threading import Thread

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QPushButton, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)
from qfluentwidgets import PrimaryPushButton, PushButton, isDarkTheme
import cds_tokens as T
import update_check


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
        if self.validate() and self.result() != QDialog.Accepted:
            self.accept()

    def validate(self):
        return True

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        if parent:
            center = parent.mapToGlobal(parent.rect().center())
            x = center.x() - self.width() // 2
            y = center.y() - self.height() // 2
            screen = parent.screen() or self.screen() or QGuiApplication.primaryScreen()
            if screen is not None:
                avail = screen.availableGeometry()
                x = max(avail.left(), min(x, avail.right() - self.width() + 1))
                y = max(avail.top(), min(y, avail.bottom() - self.height() + 1))
            self.move(x, y)


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
    parent = dialog.parentWidget()
    screen = (parent.screen() if parent else None) or dialog.screen() or QGuiApplication.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
    elif parent is not None:
        available = parent.geometry()
    else:
        available = dialog.geometry()
    # 固定宽度由各窗口指定；桌面较窄时仍保留可操作边界。
    width = max(320, min(dialog.width(), available.width() - 40))
    dialog.setMinimumWidth(width)
    dialog.setMaximumWidth(width)
    dialog.setMinimumHeight(0)
    height = max(220, min(getattr(dialog, 'preferred_height', 660), available.height() - 48))
    dialog.setMaximumHeight(height)
    dialog.resize(width, height)


class AboutDialog(WorkbenchDialog):
    def __init__(self, parent, version):
        super().__init__(parent)
        self.setFixedWidth(400)
        self.preferred_height = 316
        self.version = version
        self.header_description = False
        self.viewLayout.addWidget(QLabel('关于广告图过滤', self))
        self.version_label = QLabel(f'版本 v{version}', self)
        self.version_label.setObjectName('sectionTitle')
        self.version_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.viewLayout.addWidget(self.version_label)
        description = QLabel('在本机扫描、识别和审查图片。', self)
        description.setObjectName('mutedText')
        description.setWordWrap(True)
        self.viewLayout.addWidget(description)
        self.check_button = PushButton('检查更新', self)
        self.check_button.setFixedHeight(34)
        self.check_button.clicked.connect(self._check_updates)
        self.viewLayout.addWidget(self.check_button, 0, Qt.AlignLeft)
        self.update_status = QLabel('', self)
        self.update_status.setTextFormat(Qt.PlainText)
        self.update_status.setObjectName('mutedText')
        self.update_status.setWordWrap(True)
        self.viewLayout.addWidget(self.update_status)
        self.update_link = QLabel('', self)
        self.update_link.setTextFormat(Qt.RichText)
        self.update_link.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard)
        self.update_link.setOpenExternalLinks(True)
        self.update_link.hide()
        self.viewLayout.addWidget(self.update_link)
        self.viewLayout.addStretch()
        self.yesButton.hide()
        self.cancelButton.setText('关闭')
        style_dialog(self)
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(80)
        self._update_timer.timeout.connect(self._show_update_result)
        self.finished.connect(self._update_timer.stop)

    def _check_updates(self):
        if self._update_timer.isActive():
            return
        self.check_button.setEnabled(False)
        self.update_status.setText('正在检查 GitHub 最新发行版…')
        self.update_link.hide()
        results = self._update_results = SimpleQueue()
        version = self.version

        def fetch():
            # 工作线程只写结果队列，关闭弹窗后也不会访问 Qt 控件。
            try:
                results.put(update_check.check_latest(version))
            except update_check.UpdateCheckError as error:
                results.put(error)
            except Exception:
                results.put(update_check.UpdateCheckError('检查更新时出现异常，请稍后重试。'))

        self._update_timer.start()
        Thread(target=fetch, daemon=True).start()

    def _show_update_result(self):
        try:
            result = self._update_results.get_nowait()
        except Empty:
            return
        self._update_timer.stop()
        self.check_button.setEnabled(True)
        if isinstance(result, update_check.UpdateCheckError):
            self.update_status.setText(str(result))
            url, label = update_check.RELEASES_URL, '打开 GitHub 发行页'
        elif result is None:
            self.update_status.setText('当前版本无需更新。')
            return
        else:
            self.update_status.setText(f'发现新版 {result.version}' + ('。' if result.download_url else '，尚无 Windows EXE。'))
            url = result.download_url or result.release_url
            label = '下载 Windows EXE' if result.download_url else '查看新版发行页'
        self.update_link.setText(f'<a href="{escape(url, quote=True)}">{label}</a>')
        self.update_link.setToolTip(url)
        self.update_link.show()


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
