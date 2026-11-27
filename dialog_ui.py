# -*- coding: utf-8 -*-
"""工作台配套窗口：统一标题、内容分区和固定操作栏。"""
import base64
from html import escape
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Thread

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import (QDesktopServices, QGuiApplication, QImage, QKeySequence,
                           QPainter, QPainterPath, QPixmap, QShortcut)
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
                               QVBoxLayout, QWidget)
from qfluentwidgets import PrimaryPushButton, PushButton, isDarkTheme, setCustomStyleSheet
import cds_tokens as T
import update_check


_ABOUT_ICON_IMAGE = None


def _rounded_app_pixmap(size: int = 56, radius: int = 13, dpr: float = 0.0) -> QPixmap:
    """加载内嵌高清应用图标并裁剪平滑圆角（支持高 DPI 物理像素渲染，兼容离屏测试与单文件打包）。"""
    global _ABOUT_ICON_IMAGE
    if _ABOUT_ICON_IMAGE is None:
        try:
            from icon_embed import ICON_B64
            _ABOUT_ICON_IMAGE = QImage.fromData(base64.b64decode(ICON_B64), 'PNG')
        except Exception:
            _ABOUT_ICON_IMAGE = QImage()
    if dpr <= 0:
        scr = QGuiApplication.primaryScreen()
        dpr = float(scr.devicePixelRatio()) if scr is not None else 1.0
    dpr = max(1.0, min(3.0, float(dpr)))
    px = max(1, int(round(size * dpr)))
    rad = radius * dpr
    if _ABOUT_ICON_IMAGE.isNull():
        pm = QPixmap(px, px)
        pm.fill(Qt.transparent)
        pm.setDevicePixelRatio(dpr)
        return pm
    scaled = QPixmap.fromImage(_ABOUT_ICON_IMAGE).scaled(
        px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    canvas = QPixmap(px, px)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(0, 0, px, px, rad, rad)
    painter.setClipPath(clip)
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    canvas.setDevicePixelRatio(dpr)
    return canvas


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
    accent = c(T.T.clay)
    accent_hover = c(T.CLAY_HOVER)
    on_accent = c(T.T.text_on_clay)
    dialog.setStyleSheet(
        f'QDialog#dialogWindow {{ background:{surface}; border:1px solid {border}; border-radius:16px; }}'
        f'QLabel {{ color:{text}; font-family:"Microsoft YaHei UI"; background:transparent; }}'
        f'QLabel#mutedText {{ color:{muted}; font-size:12px; }}'
        f'QLabel#sectionTitle {{ font-size:14px; font-weight:600; }}'
        f'QLabel#dialogTitle {{ font-size:19px; font-weight:600; }}'
        f'QFrame#settingsSection {{ background:{panel}; border:1px solid {border}; border-radius:12px; }}'
        f'QFrame#dialogHeader {{ background:transparent; border-bottom:1px solid {border}; margin:1px 1px 0 1px;'
        f' border-top-left-radius:15px; border-top-right-radius:15px; }}'
        f'QFrame#dialogFooter {{ background:{panel}; border-top:1px solid {border}; margin:0 1px 1px 1px;'
        f' border-bottom-left-radius:15px; border-bottom-right-radius:15px; }}'
        f'QListWidget#dialogNavigation {{ background:{panel}; border:none; border-radius:10px; padding:6px; color:{text}; outline:none; }}'
        f'QListWidget#dialogNavigation::item {{ padding:11px 10px; border-radius:7px; margin:2px 0; }}'
        f'QListWidget#dialogNavigation::item:selected {{ background:{c(T.T.clay_fill)}; color:{accent}; font-weight:600; }}'
        f'QPushButton#dialogClose {{ background:transparent; border:none; border-radius:8px; color:{muted}; font-size:20px; }}'
        f'QPushButton#dialogClose:hover {{ background:{c(T.T.surface3)}; color:{text}; }}'
        f'QLineEdit {{ background:{c(T.T.surface2)}; color:{text}; border:1px solid {border}; border-radius:7px; padding:7px 10px; selection-background-color:{accent}; }}'
        f'QLineEdit:focus {{ border-color:{accent}; }}'
        f'QSpinBox {{ background:{c(T.T.surface2)}; color:{text}; border:1px solid {border}; border-radius:6px; padding:5px; }}'
        f'QScrollArea {{ background:transparent; border:none; margin:0 1px; }}'
        f'QScrollArea > QWidget > QWidget {{ background:transparent; border:none; }}'
        f'QScrollBar:vertical {{ background:transparent; width:8px; margin:4px 2px 4px 0; }}'
        f'QScrollBar::handle:vertical {{ background:{c(T.T.border_strong)}; min-height:28px; border-radius:3px; }}'
        f'QScrollBar::handle:vertical:hover {{ background:{muted}; }}'
        f'QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,'
        f'QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:none; height:0px; }}'
    )
    primary_qss = (
        f'PrimaryPushButton {{ background:{accent}; color:{on_accent}; border:none; border-radius:8px; padding:6px 16px; }}'
        f'PrimaryPushButton:hover {{ background:{accent_hover}; }}'
        f'PrimaryPushButton:pressed {{ background:{accent}; }}'
        f'PrimaryPushButton:disabled {{ background:{c(T.T.muted)}; color:{c(T.T.text_3)}; }}'
    )
    if not getattr(dialog, '_custom_yes_style', False):
        setCustomStyleSheet(dialog.yesButton, primary_qss, primary_qss)
    # 将旧版正文中的标题与说明移到固定页头，其余内容可滚动。
    title = dialog.viewLayout.takeAt(0).widget()
    title.setObjectName('dialogTitle')
    dialog.setWindowTitle(title.text())
    header = DialogHeader(dialog)
    header.setObjectName('dialogHeader')
    row = QHBoxLayout(header)
    row.setContentsMargins(24, 16, 18, 16)
    words = QVBoxLayout()
    words.setSpacing(5)
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
    dialog.closeButton.setCursor(Qt.PointingHandCursor)
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
    width = max(320, min(dialog.width(), available.width() - 40))
    dialog.setMinimumWidth(width)
    dialog.setMaximumWidth(width)
    dialog.setMinimumHeight(0)
    height = max(220, min(getattr(dialog, 'preferred_height', 660), available.height() - 48))
    dialog.setMaximumHeight(height)
    dialog.resize(width, height)


class AboutDialog(WorkbenchDialog):
    """重构版「关于」窗口：品牌视觉主卡 + 引擎/隐私概览条 + 在线更新检查与快捷入口。"""

    def __init__(self, parent, version):
        super().__init__(parent)
        self.setFixedWidth(560)
        self.preferred_height = 496
        self.version = version
        self.header_description = False
        self.viewLayout.setContentsMargins(22, 16, 22, 14)
        self.viewLayout.setSpacing(12)

        dark = isDarkTheme()
        c = lambda tok: T.c(dark, tok)
        accent = c(T.T.clay)
        accent_hover = c(T.CLAY_HOVER)
        on_accent = c(T.T.text_on_clay)
        surf2 = c(T.T.surface2)
        surf3 = c(T.T.surface3)
        bdr = c(T.T.border)
        bdr_soft = c(T.T.border_soft)
        txt = c(T.T.text)
        txt2 = c(T.T.text_2)
        txt3 = c(T.T.text_3)

        # 页头标题（由 style_dialog 提取至顶部可拖拽栏）
        self.viewLayout.addWidget(QLabel('关于广告图过滤', self))

        # ---- 1. 品牌主视觉卡片 ----
        hero = QFrame(self)
        hero.setObjectName('aboutHeroCard')
        hero.setStyleSheet(
            f'QFrame#aboutHeroCard {{ background:{surf2}; border:1px solid {bdr}; border-radius:14px; }}'
        )
        hero_row = QHBoxLayout(hero)
        hero_row.setContentsMargins(16, 15, 16, 15)
        hero_row.setSpacing(14)

        self.icon_label = QLabel(hero)
        self.icon_label.setFixedSize(56, 56)
        self.icon_label.setPixmap(_rounded_app_pixmap(56, 13, self.devicePixelRatioF()))
        hero_row.addWidget(self.icon_label, 0, Qt.AlignTop)

        brand_col = QVBoxLayout()
        brand_col.setContentsMargins(0, 0, 0, 0)
        brand_col.setSpacing(5)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        app_title = QLabel('广告图过滤', hero)
        app_title.setStyleSheet(f'font-size:17px; font-weight:700; color:{txt};')
        app_en = QLabel('Ad Image Filter GUI', hero)
        app_en.setStyleSheet(f'font-size:12px; color:{txt3};')
        title_row.addWidget(app_title)
        title_row.addWidget(app_en, 0, Qt.AlignBottom)
        title_row.addStretch()
        brand_col.addLayout(title_row)

        tagline = QLabel('本地批量筛查相册引流广告、推广水印与二维码，支持可逆隔离审查。', hero)
        tagline.setWordWrap(True)
        tagline.setStyleSheet(f'font-size:12px; color:{txt2};')
        brand_col.addWidget(tagline)

        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 3, 0, 0)
        badge_row.setSpacing(6)
        self.version_label = QLabel(f'版本 v{version}', hero)
        self.version_label.setObjectName('versionBadge')
        self.version_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.version_label.setStyleSheet(
            f'QLabel#versionBadge {{ {T.pill_qss(dark, "clay", radius=10, pad="2px 10px", border=True)}'
            f' font-size:12px; font-weight:600; }}'
        )
        badge_row.addWidget(self.version_label)

        for badge_txt, role in (('纯本地离线', 'ok'), ('MIT 开源许可', 'gray')):
            pill = QLabel(badge_txt, hero)
            pill.setStyleSheet(
                f'QLabel {{ {T.pill_qss(dark, role, radius=10, pad="2px 9px")} font-size:11px; font-weight:600; }}'
            )
            badge_row.addWidget(pill)
        badge_row.addStretch()

        self.copy_version_button = PushButton('复制版本', hero)
        self.copy_version_button.setFixedHeight(28)
        self.copy_version_button.setCursor(Qt.PointingHandCursor)
        self.copy_version_button.setToolTip('复制当前版本号到剪贴板')
        self.copy_version_button.clicked.connect(self._copy_version)
        badge_row.addWidget(self.copy_version_button)
        brand_col.addLayout(badge_row)
        hero_row.addLayout(brand_col, 1)
        self.viewLayout.addWidget(hero)

        # ---- 2. 三列关键信息条 ----
        specs_row = QHBoxLayout()
        specs_row.setContentsMargins(0, 0, 0, 0)
        specs_row.setSpacing(8)
        pal_label = f"{T.palette_label(T.get_palette())} · {'深色' if dark else '浅色'}"
        for head, val in (
            ('识别通道', '文件名·形状·二维码·OCR'),
            ('隐私策略', '零上传 · 本地缓存与隔离'),
            ('当前主题', pal_label),
        ):
            cell = QFrame(self)
            cell.setObjectName('aboutSpecCard')
            cell.setStyleSheet(
                f'QFrame#aboutSpecCard {{ background:{surf3}; border:1px solid {bdr_soft}; border-radius:10px; }}'
            )
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(11, 8, 11, 9)
            cl.setSpacing(3)
            h_lbl = QLabel(head, cell)
            h_lbl.setStyleSheet(f'font-size:11px; color:{txt3}; font-weight:600;')
            v_lbl = QLabel(val, cell)
            v_lbl.setWordWrap(True)
            v_lbl.setStyleSheet(f'font-size:11px; color:{txt};')
            cl.addWidget(h_lbl)
            cl.addWidget(v_lbl)
            specs_row.addWidget(cell, 1)
        self.viewLayout.addLayout(specs_row)

        # ---- 3. 在线更新检查与快捷入口卡片 ----
        upd_card = QFrame(self)
        upd_card.setObjectName('aboutUpdateCard')
        upd_card.setStyleSheet(
            f'QFrame#aboutUpdateCard {{ background:{surf2}; border:1px solid {bdr}; border-radius:12px; }}'
        )
        upd_lay = QVBoxLayout(upd_card)
        upd_lay.setContentsMargins(16, 13, 16, 13)
        upd_lay.setSpacing(10)

        upd_top = QHBoxLayout()
        upd_top.setContentsMargins(0, 0, 0, 0)
        upd_top.setSpacing(10)
        upd_titles = QVBoxLayout()
        upd_titles.setContentsMargins(0, 0, 0, 0)
        upd_titles.setSpacing(2)
        upd_head = QLabel('版本检查与链接', upd_card)
        upd_head.setStyleSheet(f'font-size:13px; font-weight:600; color:{txt};')
        upd_sub = QLabel('仅按需请求 GitHub 发行版元数据，下载与替换由你手动确认', upd_card)
        upd_sub.setStyleSheet(f'font-size:11px; color:{txt3};')
        upd_titles.addWidget(upd_head)
        upd_titles.addWidget(upd_sub)
        upd_top.addLayout(upd_titles, 1)

        self.check_button = PrimaryPushButton('检查更新', upd_card)
        self.check_button.setFixedHeight(32)
        self.check_button.setMinimumWidth(92)
        self.check_button.setCursor(Qt.PointingHandCursor)
        chk_qss = (
            f'PrimaryPushButton {{ background:{accent}; color:{on_accent}; border:none; border-radius:8px; padding:4px 14px; font-size:12px; font-weight:600; }}'
            f'PrimaryPushButton:hover {{ background:{accent_hover}; }}'
            f'PrimaryPushButton:pressed {{ background:{accent}; }}'
            f'PrimaryPushButton:disabled {{ background:{c(T.T.muted)}; color:{txt3}; }}'
        )
        setCustomStyleSheet(self.check_button, chk_qss, chk_qss)
        self.check_button.clicked.connect(self._check_updates)
        upd_top.addWidget(self.check_button, 0, Qt.AlignVCenter)
        upd_lay.addLayout(upd_top)

        self.status_box = QFrame(upd_card)
        self.status_box.setObjectName('aboutStatusBox')
        status_lay = QHBoxLayout(self.status_box)
        status_lay.setContentsMargins(12, 8, 12, 8)
        status_lay.setSpacing(10)
        self.update_status = QLabel('点击「检查更新」核对 GitHub 最新发行版。', self.status_box)
        self.update_status.setTextFormat(Qt.PlainText)
        self.update_status.setWordWrap(True)
        self.update_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        status_lay.addWidget(self.update_status, 1)

        self.update_link = QLabel('', self.status_box)
        self.update_link.setTextFormat(Qt.RichText)
        self.update_link.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard)
        self.update_link.setOpenExternalLinks(True)
        self.update_link.hide()
        status_lay.addWidget(self.update_link, 0, Qt.AlignVCenter)
        upd_lay.addWidget(self.status_box)

        links_row = QHBoxLayout()
        links_row.setContentsMargins(0, 2, 0, 0)
        links_row.setSpacing(8)
        self.releases_button = PushButton('GitHub 发行页', upd_card)
        self.releases_button.setFixedHeight(28)
        self.releases_button.setCursor(Qt.PointingHandCursor)
        self.releases_button.setToolTip(update_check.RELEASES_URL)
        self.releases_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(update_check.RELEASES_URL)))
        self.data_dir_button = PushButton('打开数据目录', upd_card)
        self.data_dir_button.setFixedHeight(28)
        self.data_dir_button.setCursor(Qt.PointingHandCursor)
        self.data_dir_button.setToolTip('打开本地配置、缓存与日志所在目录')
        self.data_dir_button.clicked.connect(self._open_data_dir)
        links_row.addWidget(self.releases_button)
        links_row.addWidget(self.data_dir_button)
        links_row.addStretch()
        upd_lay.addLayout(links_row)
        self.viewLayout.addWidget(upd_card)
        self.viewLayout.addStretch()

        self.yesButton.hide()
        self.cancelButton.setText('关闭')
        style_dialog(self)
        self._style_status_box('gray')

        self._copy_reset_timer = QTimer(self)
        self._copy_reset_timer.setSingleShot(True)
        self._copy_reset_timer.setInterval(1600)
        self._copy_reset_timer.timeout.connect(lambda: self.copy_version_button.setText('复制版本'))

        self._update_timer = QTimer(self)
        self._update_timer.setInterval(80)
        self._update_timer.timeout.connect(self._show_update_result)
        self.finished.connect(self._update_timer.stop)
        self.finished.connect(self._copy_reset_timer.stop)

    def _style_status_box(self, role: str):
        dark = isDarkTheme()
        fg, fill, edge = T.sem(dark, role)
        text_color = T.c(dark, T.T.text_2) if role == 'gray' else fg
        self.status_box.setStyleSheet(
            f'QFrame#aboutStatusBox {{ background:{fill}; border:1px solid {edge}; border-radius:8px; }}'
        )
        self.update_status.setStyleSheet(f'font-size:12px; color:{text_color}; background:transparent;')

    def _copy_version(self):
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(f'v{self.version}')
        self.copy_version_button.setText('✓ 已复制')
        self._copy_reset_timer.start()

    @staticmethod
    def _open_data_dir():
        try:
            import ad_filter as af
            target = af.CONFIG_PATH.parent if hasattr(af, 'CONFIG_PATH') else af.APP_DIR
        except Exception:
            target = Path.cwd()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _check_updates(self):
        if self._update_timer.isActive():
            return
        self.check_button.setEnabled(False)
        self._style_status_box('gray')
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
        dark = isDarkTheme()
        accent = T.c(dark, T.T.clay)
        if isinstance(result, update_check.UpdateCheckError):
            self._style_status_box('warn')
            self.update_status.setText(str(result))
            url, label = update_check.RELEASES_URL, '打开 GitHub 发行页'
        elif result is None:
            self._style_status_box('ok')
            self.update_status.setText('当前版本无需更新。')
            self.update_link.hide()
            return
        else:
            self._style_status_box('clay')
            self.update_status.setText(
                f'发现新版 {result.version}' + ('。' if result.download_url else '，尚无 Windows EXE。'))
            url = result.download_url or result.release_url
            label = '下载 Windows EXE' if result.download_url else '查看新版发行页'
        self.update_link.setText(
            f'<a href="{escape(url, quote=True)}" '
            f'style="color:{accent}; text-decoration:none; font-weight:600; font-size:12px;">{label} ↗</a>'
        )
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
            self._custom_yes_style = True
            dark = isDarkTheme()
            danger = T.c(dark, T.T.danger)
            dis_bg = T.c(dark, T.T.muted)
            dis_fg = T.c(dark, T.T.text_3)
            danger_qss = (
                f'PrimaryPushButton {{ background:{danger}; color:#ffffff; border:none; border-radius:8px; padding:6px 16px; }}'
                f'PrimaryPushButton:hover {{ background:{danger}; }}'
                f'PrimaryPushButton:pressed {{ background:{danger}; }}'
                f'PrimaryPushButton:disabled {{ background:{dis_bg}; color:{dis_fg}; }}'
            )
            setCustomStyleSheet(self.yesButton, danger_qss, danger_qss)
        style_dialog(self)
