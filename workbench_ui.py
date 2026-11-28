# -*- coding: utf-8 -*-
"""统一主题工作台的视图构造；扫描和文件操作仍由 App 协调。"""
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListView, QAbstractItemView,
    QSplitter, QSizePolicy, QScrollArea, QPushButton, QButtonGroup, QGridLayout,
)
from qfluentwidgets import (
    TransparentPushButton, PrimaryPushButton, PushButton, ComboBox,
    SearchLineEdit, TextEdit, CaptionLabel, FluentIcon as FIF, isDarkTheme,
)
import cds_tokens as T


class ReviewCategories(QWidget):
    """可见的审查分类分段胶囊；原 ComboBox 继续提供兼容索引和变更信号。"""
    LABELS = ('全部待审', '广告', '疑似', '异常')
    ROLES = ('clay', 'ad', 'suspect', 'error')

    def __init__(self, control, parent):
        super().__init__(parent)
        self.control = control
        self.setObjectName('categoryTrack')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(32)
        self.counts = (0, 0, 0, 0)
        self._rendered = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self.group = QButtonGroup(self)
        self.buttons = []
        for index, label in enumerate(self.LABELS):
            button = QPushButton(label, self)
            button.setCheckable(True)
            button.setFixedHeight(26)
            button.setCursor(Qt.PointingHandCursor)
            self.group.addButton(button, index)
            layout.addWidget(button)
            self.buttons.append(button)
        self.group.idClicked.connect(control.setCurrentIndex)
        control.currentIndexChanged.connect(lambda _: self.update_counts(self.counts))
        self.update_counts(self.counts)

    def refresh_theme(self):
        self._rendered = None
        self.update_counts(self.counts)

    def update_counts(self, counts):
        self.counts = tuple(counts)
        dark = isDarkTheme()
        key = (self.counts, self.control.currentIndex(), dark, T.get_palette())
        if key == self._rendered:
            return
        self._rendered = key
        track_bg = T.c(dark, T.T.seg_track)
        pill_bg = T.c(dark, T.T.seg_pill)
        border = T.c(dark, T.T.border)
        accent = T.c(dark, T.T.clay)
        self.setStyleSheet(
            f'#categoryTrack {{ background:{track_bg}; border-radius:9px; }}')
        for index, (label, role, count, button) in enumerate(zip(self.LABELS, self.ROLES, counts, self.buttons)):
            active = self.control.currentIndex() == index
            button.setChecked(active)
            button.setText(f'{label} {count}')
            button.setAccessibleName(f'{label}，{count} 张')
            button.setToolTip(f'{label}：{count} 张；正常图片不进入待审列表')
            fg, fill, edge = T.sem(dark, role)
            if active:
                button.setStyleSheet(
                    f'QPushButton {{ color:{fg}; background:{pill_bg};'
                    f' border:1px solid {edge if index > 0 else border};'
                    f' border-radius:6px; padding:0 8px; font-size:12px; font-weight:600; }}'
                    f'QPushButton:focus {{ border:1.5px solid {accent}; }}')
            else:
                text_idle = T.c(dark, T.T.text_2) if index == 0 else fg
                button.setStyleSheet(
                    f'QPushButton {{ color:{text_idle}; background:transparent;'
                    f' border:1px solid transparent;'
                    f' border-radius:6px; padding:0 8px; font-size:12px; font-weight:400; }}'
                    f'QPushButton:hover {{ background:{fill}; color:{fg}; }}'
                    f'QPushButton:focus {{ border:1.5px solid {accent}; }}')


class DensitySwitcher(QWidget):
    """三档卡片网格密度切换器（紧凑 / 标准 / 大图）。"""

    def __init__(self, on_change, parent=None):
        super().__init__(parent)
        self._on_change = on_change
        self._current = T.DEFAULT_DENSITY
        self.setObjectName('densityTrack')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(28)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self.buttons = {}
        for key in T.DENSITY_ORDER:
            spec = T.DENSITIES[key]
            btn = QPushButton(spec['short'], self)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setCheckable(True)
            btn.setFixedSize(26, 24)
            btn.setToolTip(f"切换为{spec['label']}卡片密度")
            btn.setAccessibleName(f"{spec['label']}卡片密度")
            btn.clicked.connect(lambda _=False, k=key: self._on_change(k))
            lay.addWidget(btn)
            self.buttons[key] = btn
        self.refresh_theme()

    def set_density(self, key: str):
        if key not in self.buttons:
            key = T.DEFAULT_DENSITY
        self._current = key
        self.refresh_theme()

    def refresh_theme(self):
        dark = isDarkTheme()
        track = T.c(dark, T.T.seg_track)
        pill = T.c(dark, T.T.seg_pill)
        border = T.c(dark, T.T.border)
        accent = T.c(dark, T.T.clay)
        txt2 = T.c(dark, T.T.text_2)
        hov = T.c(dark, T.T.hover)
        self.setStyleSheet(f'#densityTrack {{ background:{track}; border-radius:7px; }}')
        for key, btn in self.buttons.items():
            active = key == self._current
            btn.setChecked(active)
            if active:
                btn.setStyleSheet(
                    f'QPushButton {{ background:{pill}; color:{accent}; border:1px solid {border};'
                    f' border-radius:5px; font-size:11px; font-weight:600; padding:0; }}')
            else:
                btn.setStyleSheet(
                    f'QPushButton {{ background:transparent; color:{txt2}; border:none;'
                    f' border-radius:5px; font-size:11px; font-weight:400; padding:0; }}'
                    f'QPushButton:hover {{ background:{hov}; color:{accent}; }}')


def adapt_workbench(app):
    """缩放使用逻辑尺寸；窄窗保留扫描主动作，低频入口转入文字菜单。"""
    if not hasattr(app, 'disposal_controls'):
        return
    compact = app.width() < 1180
    for name in ('btn_history', 'btn_restore', 'btn_palette', 'btn_theme', 'btn_about'):
        getattr(app, name).setVisible(not compact)
    app.btn_more_actions.setVisible(compact)
    narrow = app.width() < 1040
    if getattr(app, '_compact_disposal', None) != narrow:
        app._compact_disposal = narrow
        layout = app.bot.layout()
        layout.removeWidget(app.disposal_controls)
        layout.addWidget(app.disposal_controls, 1 if narrow else 0, 0 if narrow else 1,
                         1, 2 if narrow else 1)
    app.bot.setFixedHeight(96 if narrow else 60)
    app.local_notice.setVisible(not narrow)
    if hasattr(app, 'results_scope'):
        app.results_scope.setVisible(app.width() >= 1020)
    app.status.setMaximumWidth(max(160, app.width() - 410))


def configure_fonts():
    """离屏测试平台没有系统字体数据库，显式注册本机中文字体。"""
    from pathlib import Path
    from PySide6.QtGui import QFontDatabase, QFont
    from PySide6.QtWidgets import QApplication
    qt = QApplication.instance()
    if not QFontDatabase.families():
        for name in ('msyh.ttc', 'msyhbd.ttc', 'segoeui.ttf', 'seguisym.ttf', 'consola.ttf'):
            filename = Path('C:/Windows/Fonts') / name
            if filename.exists():
                QFontDatabase.addApplicationFont(str(filename))
    if qt and not qt.property('adfilterFontsReady'):
        font = QFont('Microsoft YaHei UI')
        font.setPixelSize(13)
        qt.setFont(font)
        qt.setProperty('adfilterFontsReady', True)


def build_workbench(app):
    # 延迟导入避免主模块与视图模块的初始化循环。
    from ad_filter_gui import FolderBar, StatusDot, Segmented, CardDelegate, ScrollFade
    from cds_widgets import CDSEmptyState
    from cds_layouts import LayoutManager

    root = QVBoxLayout(app)
    root.setContentsMargins(0, 38, 0, 0)
    root.setSpacing(0)
    app._root_layout = root
    app.brand = app._titlebar.brand

    def button(name, text, callback, parent, icon=None, primary=False):
        cls = PrimaryPushButton if primary else TransparentPushButton
        w = cls(icon, text, parent) if icon else cls(text, parent)
        w.setFixedHeight(32)
        w.clicked.connect(callback)
        setattr(app, name, w)
        return w

    # ---- Layer 1: 紧凑顶栏 (48px) ----
    top = QWidget(app)
    top.setObjectName('topBar')
    top.setFixedHeight(48)
    app.top_bar = top
    tl = QHBoxLayout(top)
    tl.setContentsMargins(16, 0, 16, 0)
    tl.setSpacing(5)
    tl.addWidget(button('btn_add', '添加文件夹', app.add_folder, top, FIF.FOLDER_ADD))
    tl.addWidget(button('btn_scan', '开始扫描', app.start_scan, top, FIF.PLAY, True))
    app.btn_scan.setFixedWidth(112)
    tl.addWidget(button('btn_pause', '暂停', app.toggle_pause, top, FIF.PAUSE))
    tl.addWidget(button('btn_cancel', '取消', app.cancel_scan, top, FIF.CANCEL))
    app.btn_pause.setEnabled(False)
    app.btn_cancel.setEnabled(False)
    tl.addWidget(app._vsep(top))
    tl.addWidget(button('btn_rules', '规则', app.open_rule_editor, top, FIF.EDIT))
    tl.addWidget(button('btn_cfg', '设置', app.open_config, top, FIF.SETTING))
    tl.addWidget(button('btn_history', '历史', app.open_history, top, FIF.HISTORY))
    tl.addWidget(button('btn_restore', '隔离区', app.restore_ui, top, FIF.FOLDER))
    tl.addStretch(1)
    tl.addWidget(button('btn_palette', '命令', app.open_palette, top, FIF.SEARCH))
    tl.addWidget(button('btn_theme', '主题', app.toggle_theme, top, FIF.BRIGHTNESS))
    tl.addWidget(button('btn_about', '关于', app.open_about, top, FIF.INFO))
    tl.addWidget(button('btn_more_actions', '更多', app.open_workbench_menu, top, FIF.MORE))
    root.addWidget(top)

    # ---- Layer 2: 紧凑来源文件夹条 (38px) ----
    app.folder_bar = FolderBar(app)
    app.folder_bar.rebuild()
    root.addWidget(app.folder_bar)

    mid = QHBoxLayout()
    mid.setSpacing(0)
    mid.setContentsMargins(0, 0, 0, 0)
    app.mid = mid
    splitter = QSplitter(Qt.Horizontal, app)
    splitter.setObjectName('workbenchSplitter')
    splitter.setChildrenCollapsible(False)
    splitter.setHandleWidth(1)
    app.splitter = splitter
    mid.addWidget(splitter)
    root.addLayout(mid, 1)

    left = QWidget(splitter)
    left.setMinimumWidth(480)
    ll = QVBoxLayout(left)
    ll.setContentsMargins(0, 0, 0, 0)
    ll.setSpacing(0)
    app.results_panel = left

    # ---- 精简双行筛选工具带 (88px，原 144px 省 56px) ----
    fbar = QWidget(left)
    fbar.setObjectName('filterBar')
    fbar.setFixedHeight(88)
    app.filter_bar = fbar
    fl = QVBoxLayout(fbar)
    fl.setContentsMargins(16, 8, 16, 6)
    fl.setSpacing(6)

    # 第 1 行：分类分段胶囊 + 清除筛选 | 显示范围 + 三档密度 + 导出
    heading = QHBoxLayout()
    heading.setSpacing(6)
    app.f_verdict = ComboBox(fbar)
    app.f_verdict.addItems(['全部待审', '广告', '疑似', '异常'])
    app.f_verdict.setFixedHeight(32)
    app.f_verdict.hide()
    app.verdict_tabs = ReviewCategories(app.f_verdict, fbar)
    heading.addWidget(app.verdict_tabs)
    app.btn_reset_filters = button('btn_reset_filters', '清除筛选', app.clear_filters, fbar)
    app.btn_reset_filters.setFixedHeight(28)
    heading.addWidget(app.btn_reset_filters)
    heading.addStretch(1)
    app.results_scope = CaptionLabel('显示 0 / 待审 0', fbar)
    heading.addWidget(app.results_scope)
    app.density_tabs = DensitySwitcher(app.set_density, fbar)
    heading.addWidget(app.density_tabs)
    heading.addWidget(button('btn_export', '导出', app.export_report, fbar, FIF.SAVE))
    app.btn_export.setFixedHeight(28)
    app.btn_export.setToolTip('导出当前筛选结果，保持当前排序')
    fl.addLayout(heading)
    app.f_verdict.currentIndexChanged.connect(lambda _: app.refresh())

    # 第 2 行：搜索框 + 原因 + 排序 + 批量选择工具
    filters = QHBoxLayout()
    filters.setSpacing(6)
    app.f_search = SearchLineEdit(fbar)
    app.f_search.setPlaceholderText('搜索文件名或路径')
    app.f_search.setMinimumWidth(96)
    app.f_search.setFixedHeight(30)
    app._search_timer = QTimer(app)
    app._search_timer.setSingleShot(True)
    app._search_timer.setInterval(250)
    app._search_timer.timeout.connect(app.refresh)
    app.f_search.textChanged.connect(lambda _: app._search_timer.start())
    filters.addWidget(app.f_search, 1)
    app.f_reason = ComboBox(fbar)
    app.f_reason.addItem('全部原因')
    app.f_reason.setFixedWidth(116)
    app.f_reason.setFixedHeight(30)
    app.f_reason.currentIndexChanged.connect(lambda _: app.refresh())
    filters.addWidget(app.f_reason)
    app.f_sort = ComboBox(fbar)
    app.f_sort.addItems(['默认顺序', '名称', '大小', '修改时间', '分数'])
    app.f_sort.setFixedWidth(98)
    app.f_sort.setFixedHeight(30)
    app.f_sort.currentIndexChanged.connect(lambda _: app.refresh())
    filters.addWidget(app.f_sort)

    app.selection_tools = QWidget(fbar)
    st = QHBoxLayout(app.selection_tools)
    st.setContentsMargins(0, 0, 0, 0)
    st.setSpacing(0)
    for name, text, fn in (
        ('btn_all', '全选', app.select_all), ('btn_invert', '反选', app.invert_sel),
        ('btn_clear', '清除', app.clear_sel), ('btn_similar', '相似组', app.select_similar_groups),
    ):
        b = button(name, text, fn, app.selection_tools)
        b.setFixedHeight(28)
        b.setMinimumWidth(38)
        st.addWidget(b)
    app.btn_similar.setToolTip('勾选全部相似组中的图片')
    filters.addWidget(app.selection_tools)
    fl.addLayout(filters)
    ll.addWidget(fbar)

    # ---- 图片墙画布 ----
    dspec = T.density_spec(getattr(app, 'density', T.DEFAULT_DENSITY))
    gwrap = QWidget(left)
    gwrap.setObjectName('gridWrap')
    gl = QVBoxLayout(gwrap)
    gl.setContentsMargins(12, 6, 8, 8)
    gl.setSpacing(0)
    grid = QListView(gwrap)
    grid.setObjectName('grid')
    grid.setViewMode(QListView.IconMode)
    grid.setResizeMode(QListView.Adjust)
    grid.setMovement(QListView.Static)
    grid.setUniformItemSizes(True)
    grid.setGridSize(QSize(dspec['grid_w'], dspec['grid_h']))
    grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
    grid.setSelectionRectVisible(False)
    grid.setModel(app.model)
    grid.setFrameShape(QListView.NoFrame)
    grid.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    grid.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    grid.setMinimumHeight(40)
    app.grid = grid
    app.delegate = CardDelegate(app.thumbs, app.model, grid)
    app.delegate.dark = isDarkTheme()
    app.delegate.set_density(getattr(app, 'density', T.DEFAULT_DENSITY))
    grid.setItemDelegate(app.delegate)
    grid.selectionModel().currentChanged.connect(app._on_current_changed)
    grid.selectionModel().selectionChanged.connect(app._on_multi_select)
    grid.doubleClicked.connect(lambda _: app.open_big())
    grid.setContextMenuPolicy(Qt.CustomContextMenu)
    grid.customContextMenuRequested.connect(app._on_context_menu)
    grid.viewport().installEventFilter(app)
    grid.installEventFilter(app)
    app._band_start = None
    app._sel_band = QWidget(grid.viewport())
    app._sel_band.setObjectName('selBand')
    app._sel_band.setAttribute(Qt.WA_TransparentForMouseEvents)
    app._sel_band.setAttribute(Qt.WA_StyledBackground, True)
    app._sel_band.hide()
    app._apply_selection_palette()
    app._fade_top = ScrollFade('top', grid.viewport())
    app._fade_bottom = ScrollFade('bottom', grid.viewport())
    app._fade_top.hide()
    app._fade_bottom.hide()
    grid.verticalScrollBar().valueChanged.connect(app._sync_fade_visibility)
    grid.verticalScrollBar().rangeChanged.connect(lambda *_: app._sync_fade_visibility())
    gl.addWidget(grid)
    ll.addWidget(gwrap, 1)

    # ---- 右侧详情面板 ----
    detail = QWidget(splitter)
    detail.setObjectName('detailPanel')
    detail.setMinimumWidth(320)
    detail.setMaximumWidth(520)
    app.detail = detail
    dv = QVBoxLayout(detail)
    dv.setContentsMargins(16, 10, 16, 10)
    dv.setSpacing(8)
    dh = QHBoxLayout()
    dh.addWidget(CaptionLabel('图片详情', detail))
    dh.addStretch()
    app.detail_badge = QLabel('未选中', detail)
    app.detail_badge.setObjectName('detailBadge')
    dh.addWidget(app.detail_badge)
    dv.addLayout(dh)
    app.detail_scroll = QScrollArea(detail)
    app.detail_scroll.setObjectName('detailScroll')
    app.detail_scroll.setWidgetResizable(True)
    app.detail_scroll.setFrameShape(QScrollArea.NoFrame)
    app.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    app.detail_scroll.setMinimumHeight(72)
    detail_body = QWidget(app.detail_scroll)
    detail_body.setObjectName('detailBody')
    body_layout = QVBoxLayout(detail_body)
    body_layout.setContentsMargins(0, 0, 4, 0)
    body_layout.setSpacing(8)
    app.detail_scroll.setWidget(detail_body)
    dv.addWidget(app.detail_scroll, 1)
    app.preview = QLabel('选择图片查看预览', detail_body)
    app.preview.setObjectName('previewCanvas')
    app.preview.setAlignment(Qt.AlignCenter)
    app.preview.setMinimumHeight(130)
    app.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
    body_layout.addWidget(app.preview, 2)
    app.detail_filename = QLabel('尚未选择图片', detail)
    app.detail_filename.setObjectName('detailFilename')
    app.detail_filename.setWordWrap(True)
    app.detail_filename.setTextInteractionFlags(Qt.TextSelectableByMouse)
    app.detail_filename.setMaximumHeight(40)
    body_layout.addWidget(app.detail_filename)
    app.detail_path = QLabel('', detail)
    app.detail_path.setObjectName('detailPath')
    app.detail_path.setWordWrap(True)
    app.detail_path.setMaximumHeight(34)
    app.detail_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
    body_layout.addWidget(app.detail_path)
    app.info = TextEdit(detail)
    app.info.setReadOnly(True)
    app.info.setMinimumHeight(96)
    app.info.setMaximumHeight(240)
    body_layout.addWidget(app.info, 1)
    nav = QHBoxLayout()
    nav.setSpacing(4)
    nav.addWidget(button('btn_prev', '上一张', lambda: app.step(-1), detail, FIF.LEFT_ARROW))
    app.preview_index = CaptionLabel('— / —', detail)
    app.preview_index.setAlignment(Qt.AlignCenter)
    nav.addWidget(app.preview_index, 1)
    nav.addWidget(button('btn_next', '下一张', lambda: app.step(1), detail, FIF.RIGHT_ARROW))
    dv.addLayout(nav)
    actions = QHBoxLayout()
    actions.setSpacing(6)
    app.btn_mark = PushButton('标记广告', detail)
    app.btn_mark.setToolTip('加入待处置；不修改自动判定和规则')
    app.btn_mark.clicked.connect(lambda: app.mark_current(True))
    app.btn_keep = PushButton('本次保留', detail)
    app.btn_keep.setToolTip('移出本次结果；下次扫描仍按规则判断')
    app.btn_keep.clicked.connect(lambda: app.mark_current(False))
    for w in (app.btn_mark, app.btn_keep):
        w.setFixedHeight(32)
        actions.addWidget(w)
    actions.addWidget(button('btn_big', '大图', app.open_big, detail, FIF.ZOOM_IN))
    dv.addLayout(actions)
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 0)
    splitter.setSizes([1020, 340])
    splitter.splitterMoved.connect(lambda *_: app._resize_timer.start())

    # ---- 紧凑底部处置条 (60px / 窄屏 96px) ----
    bot = QWidget(app)
    bot.setObjectName('bottomBar')
    bot.setFixedHeight(60)
    app.bot = bot
    bl = QGridLayout(bot)
    bl.setContentsMargins(16, 4, 16, 4)
    bl.setSpacing(4)
    app.lbl_count = QWidget(bot)
    cl = QVBoxLayout(app.lbl_count)
    cl.setContentsMargins(0, 0, 0, 0)
    cl.setSpacing(2)
    app.pill_sel = QLabel('已选 0 张', app.lbl_count)
    app.pill_sel.setObjectName('countPill')
    app.pill_sel.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
    app.selection_scope = CaptionLabel('当前可见 0 张', app.lbl_count)
    cl.addWidget(app.pill_sel)
    scope_row = QHBoxLayout()
    scope_row.setContentsMargins(0, 0, 0, 0)
    scope_row.setSpacing(6)
    scope_row.addWidget(app.selection_scope)
    app.btn_selected = button('btn_selected', '查看已选',
        lambda: app.set_selected_only(app.btn_selected.isChecked()), app.lbl_count)
    app.btn_selected.setCheckable(True)
    app.btn_selected.setFixedHeight(24)
    app.btn_selected.setMinimumWidth(68)
    scope_row.addWidget(app.btn_selected)
    scope_row.addStretch()
    cl.addLayout(scope_row)
    bl.addWidget(app.lbl_count, 0, 0)
    bl.setColumnStretch(0, 1)
    app.disposal_controls = QWidget(bot)
    controls = QHBoxLayout(app.disposal_controls)
    controls.setContentsMargins(0, 0, 0, 0)
    controls.setSpacing(8)
    app.lbl_disposal = CaptionLabel('处置方式', bot)
    controls.addWidget(app.lbl_disposal)
    app.seg_disposal = Segmented([('隔离', 'quarantine'), ('回收站', 'recycle'), ('删除', 'delete')], bot)
    app.seg_disposal.setFixedWidth(210)
    app.seg_disposal.setToolTip('隔离可撤销；回收站由系统恢复；删除不可恢复')
    app.btn_dq, app.btn_dr, app.btn_dd = app.seg_disposal._btns
    disposal_idx = {'quarantine': 0, 'recycle': 1, 'delete': 2}.get(app.cfg.get('disposal', 'quarantine'), 0)
    app.seg_disposal.set_index(disposal_idx, animate=False)
    app.seg_disposal.valueChanged.connect(app._on_disposal_changed)
    controls.addWidget(app.seg_disposal)
    controls.addWidget(button('btn_undo', '撤销处置', app.undo_disposal, bot, FIF.BACK_TO_WINDOW))
    app.btn_undo.setEnabled(False)
    controls.addWidget(button('btn_dispose', '隔离 0 张', app.execute_disposal, bot, FIF.DELETE, True))
    bl.addWidget(app.disposal_controls, 0, 1)
    app.btn_dispose.setMinimumWidth(128)
    root.addWidget(bot)

    # ---- 极简底栏状态 (30px) ----
    footer = QWidget(app)
    footer.setObjectName('footerBar')
    footer.setFixedHeight(30)
    app.footer = footer
    ft = QHBoxLayout(footer)
    ft.setContentsMargins(16, 0, 16, 0)
    ft.setSpacing(8)
    app.status_pill = QWidget(footer)
    app.status_pill.setObjectName('statusPill')
    app.status_pill.setFixedHeight(22)
    sl = QHBoxLayout(app.status_pill)
    sl.setContentsMargins(8, 0, 8, 0)
    sl.setSpacing(5)
    app.dot = StatusDot(app.status_pill)
    app.status = CaptionLabel('就绪', app.status_pill)
    app.status.setMaximumWidth(560)
    sl.addWidget(app.dot)
    sl.addWidget(app.status)
    app._status_role = 'gray'
    ft.addWidget(app.status_pill)
    app.pill_hit = QLabel('待审 0', footer)
    app.pill_hit.setObjectName('countPill')
    ft.addWidget(app.pill_hit)
    ft.addStretch(1)
    app.local_notice = CaptionLabel('仅在本地分析', footer)
    ft.addWidget(app.local_notice)
    ft.addWidget(button('btn_openq', '隔离目录', app.open_quarantine_dir, footer))
    app.btn_openq.setFixedHeight(26)
    ft.addWidget(button('btn_log', '操作日志', lambda: None, footer))
    app.btn_log.setFixedHeight(26)
    app.btn_log.setCheckable(True)
    app.btn_log.toggled.connect(app.toggle_log)
    root.addWidget(footer)
    app.log = TextEdit(app)
    app.log.setReadOnly(True)
    app.log.document().setMaximumBlockCount(500)
    app.log.setFixedHeight(0)
    app.log.hide()
    root.addWidget(app.log)
    app.progress = app.folder_bar.progress
    app.lm = LayoutManager(app)
    app.layout_mode = 'workbench'
    app.lm.apply('workbench')
    app.density_tabs.set_density(getattr(app, 'density', T.DEFAULT_DENSITY))
    app._empty = CDSEmptyState('', '开始一次图片审查', '添加本地文件夹，或把文件夹拖入窗口。\n所有识别均在本机完成。', gwrap)
    app._empty.setAttribute(Qt.WA_TransparentForMouseEvents)
    app._empty.hide()
    app._apply_styles()
    adapt_workbench(app)


def style_dialog(dialog):
    """所有配套窗口与主工作台使用相同表面和排版。"""
    from dialog_ui import style_dialog as apply
    return apply(dialog)
