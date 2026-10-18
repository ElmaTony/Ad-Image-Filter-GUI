# -*- coding: utf-8 -*-
"""广告图过滤 · GUI v6「图片墙」

形态：缩略图画布（自绘卡片网格）+ 右侧详情 + 顶部极简工具栏 + 底部状态条
交互：单击选中看详情；空格/点圆圈勾选；↑↓←→ 移动；双击/Enter 大图；Ctrl+A 全选
主题：Claude 暖色（陶土橙 #d97757）
"""
import os
import queue
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

import ad_filter as af

from PySide6.QtCore import (QAbstractListModel, QEvent, QModelIndex, QObject, QRect, QSize,
                            Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QFontMetrics, QImage, QPainter,
                           QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QHBoxLayout,
                               QLabel, QListView, QListWidgetItem, QMenu, QPushButton,
                               QStackedWidget, QStyledItemDelegate, QToolButton,
                               QVBoxLayout, QWidget)

from qfluentwidgets import (CaptionLabel, CheckBox, ComboBox, FluentIcon as FIF, InfoBar,
                            InfoBarPosition, ListWidget, MessageBox, MessageBoxBase, Pivot,
                            PrimaryPushButton, ProgressBar, PushButton,
                            RadioButton, SearchLineEdit, SubtitleLabel, TableWidget, TextEdit,
                            Theme, ToolButton, TransparentPushButton, TransparentToolButton,
                            isDarkTheme, setCustomStyleSheet, setTheme, setThemeColor)
from qframelesswindow import FramelessWindow

VERDICT_TEXT = {"ad": "广告", "suspect": "疑似", "error": "异常"}
CARD_W, CARD_H = 196, 216


def _c(dark_hex, light_hex):
    return QColor(dark_hex if isDarkTheme() else light_hex)


def _verdict_color(verdict: str) -> QColor:
    if isDarkTheme():
        return QColor({"ad": "#f08a72", "suspect": "#e6b445", "error": "#c99ad4"}.get(verdict, "#a3a098"))
    return QColor({"ad": "#b3402a", "suspect": "#9a6a12", "error": "#8f4a99"}.get(verdict, "#6f6d66"))


# ================================================================ 缩略图缓存
class ThumbCache(QObject):
    """后台线程生成缩略图（QImage），主线程转 QPixmap 缓存；LRU 上限 800。
    工作线程严禁碰 QPixmap/QTimer——统一经 _sig_store 信号排队回主线程。"""

    ready = Signal(str)
    _sig_store = Signal(object)

    def __init__(self, max_items=400, size=260):
        super().__init__()
        self._sig_store.connect(self._on_store)
        self._size = size
        self._max = max_items
        self._cache: "OrderedDict[str, QPixmap]" = OrderedDict()
        self._pending: set = set()
        self._lock = threading.Lock()
        self._q = queue.Queue()
        self._stopped = False
        self._threads = [threading.Thread(target=self._worker, daemon=True) for _ in range(3)]
        for t in self._threads:
            t.start()

    def get(self, path: str):
        with self._lock:
            if path in self._cache:
                self._cache.move_to_end(path)
                return self._cache[path]
            if path in self._pending:
                return None
            self._pending.add(path)
        self._q.put(path)
        return None

    def _worker(self):
        while not self._stopped:
            try:
                path = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if path is None or self._stopped:
                return
            try:
                from PIL import ImageOps
                img = af.load_pixel_image(Path(path), self._size)
                img = ImageOps.contain(img, (self._size, self._size))
                rgb = img.convert("RGB")
                try:
                    self._sig_store.emit((path, rgb.tobytes(), rgb.width, rgb.height, False))
                except RuntimeError:
                    return  # 对象已销毁（关窗瞬间）
            except Exception:
                try:
                    self._sig_store.emit((path, None, 0, 0, True))
                except RuntimeError:
                    return

    def _on_store(self, data):
        path, buf, w, h, failed = data
        if failed:
            self._mark_failed(path)
        else:
            self._store(path, buf, w, h)

    def _store(self, path, buf, w, h):
        qimg = QImage(buf, w, h, w * 3, QImage.Format_RGB888).copy()
        pm = QPixmap.fromImage(qimg)
        # 预缩放到卡片显示尺寸：委托 paint 每帧 scaled 会被滚动放大成 CPU 热点
        pm = pm.scaled(CARD_W - 16, CARD_H - 46, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        with self._lock:
            self._cache[path] = pm
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)
            self._pending.discard(path)
        self.ready.emit(path)

    def _mark_failed(self, path):
        with self._lock:
            self._pending.discard(path)
        self.ready.emit(path)

    def stop(self):
        """停止工作线程（App 关闭时调用，避免销毁后 emit）。"""
        self._stopped = True
        for _ in self._threads:
            self._q.put(None)


# ================================================================ 数据模型
class ResultModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.items: list = []
        self.checked: set = set()
        self.sim_map: dict = {}
        self.sim_groups: list = []

    def rowCount(self, parent=QModelIndex()):
        return len(self.items)

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid() or idx.row() >= len(self.items):
            return None
        it = self.items[idx.row()]
        if role == Qt.DisplayRole:
            return Path(it["path"]).name
        if role == Qt.UserRole:
            return it["path"]
        if role == Qt.ToolTipRole:
            reasons = "; ".join(h.get("label", "") for h in it.get("reasons", []))
            return (f"{it['path']}\n判定：{VERDICT_TEXT.get(it['verdict'], it['verdict'])}"
                    f"  分数 {it.get('score', 0):.0f}\n原因：{reasons}")
        return None

    def reset_items(self, items):
        self.beginResetModel()
        self.items = list(items)
        self.endResetModel()


# ================================================================ 卡片绘制
class CardDelegate(QStyledItemDelegate):
    def __init__(self, thumbs, model, parent=None):
        super().__init__(parent)
        self.thumbs = thumbs
        self.model = model
        self._f_name = QFont("Microsoft YaHei UI", 8)
        self._f_reason = QFont("Microsoft YaHei UI", 7)
        self._f_badge = QFont("Consolas", 7)
        self._f_badge.setBold(True)
        self._fm_name = QFontMetrics(self._f_name)
        self._fm_reason = QFontMetrics(self._f_reason)
        self._fm_badge = QFontMetrics(self._f_badge)

    @staticmethod
    def _elide_bidir(name, fm, width):
        """双向智能截断：保留扩展名，中间用 … 连接（附件磁贴算法）。"""
        if fm.horizontalAdvance(name) <= width:
            return name
        stem, dot, ext = name.rpartition(".")
        tail = (stem[-7:] + dot + ext) if dot else name[-9:]
        head_budget = width - fm.horizontalAdvance("…" + tail)
        head = fm.elidedText(stem if dot else name, Qt.ElideRight, max(10, head_budget))
        return head + "…" + tail

    def paint(self, p, opt, idx):
        if idx.row() >= len(self.model.items):
            return
        it = self.model.items[idx.row()]
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRect(opt.rect).adjusted(5, 5, -5, -5)
        from PySide6.QtWidgets import QStyle
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        hovered = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        # hover 底影（两层递减 alpha 模拟提升）
        if hovered and not selected:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 26) if isDarkTheme() else QColor(0, 0, 0, 14))
            p.drawRoundedRect(QRect(r.left() + 1, r.top() + 3, r.width(), r.height()), 10, 10)
        p.setBrush(_c("#1a1a19", "#ffffff"))
        p.setPen(QPen(_c("#8a7a6a", "#b9b2a4") if selected else
                      (_c("#3a3a37", "#dedbd2") if hovered else _c("#2a2a28", "#e7e4dc")),
                      1.6 if selected else 1))
        p.drawRoundedRect(r, 10, 10)
        # 选中双环：外环 clay 1.5px + 内环 surface-1 1px（CDS 双环聚焦协议）
        if selected:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(_c("#d97757", "#c6613f"), 1.5))
            p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 11, 11)
            p.setPen(QPen(_c("#151515", "#fcfcfb"), 1))
            p.drawRoundedRect(r.adjusted(2, 2, -2, -2), 8, 8)
        img_r = QRect(r.left() + 8, r.top() + 8, r.width() - 16, r.height() - 46)
        pix = self.thumbs.get(it["path"])
        if pix is not None and not pix.isNull():
            # 缓存里已按卡片尺寸预缩放，直接贴（避免每帧 SmoothTransformation）
            p.drawPixmap(img_r.left() + (img_r.width() - pix.width()) // 2,
                         img_r.top() + (img_r.height() - pix.height()) // 2, pix)
        else:
            p.setPen(QPen(_c("#666666", "#8a8a8a")))
            p.drawText(img_r, Qt.AlignCenter, "…")
        p.setPen(Qt.NoPen)
        p.setBrush(_verdict_color(it["verdict"]))
        p.drawRoundedRect(QRect(r.left() + 34, r.top() + 4, 30, 3), 1.5, 1.5)
        # 文件名（双向截断：扩展名永远保留）+ 右上类型徽标
        name = self._elide_bidir(Path(it["path"]).name, self._fm_name, r.width() - 20)
        p.setPen(_c("#f5f5f5", "#151515"))
        p.setFont(self._f_name)
        p.drawText(QRect(r.left() + 10, r.bottom() - 34, r.width() - 20, 16),
                   Qt.AlignLeft | Qt.AlignVCenter, name)
        ext = Path(it["path"]).suffix.lstrip(".").upper()
        if ext:
            btxt = self._fm_badge.elidedText(ext, Qt.ElideRight, 34)
            bw = self._fm_badge.horizontalAdvance(btxt) + 10
            br = QRect(r.right() - bw - 8, r.bottom() - 33, bw, 15)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 96) if isDarkTheme() else QColor(20, 20, 20, 24))
            p.drawRoundedRect(br, 4, 4)
            p.setPen(QColor("#d9d6cd" if isDarkTheme() else "#525252"))
            p.setFont(self._f_badge)
            p.drawText(br, Qt.AlignCenter, btxt)
        reason = self._fm_reason.elidedText("; ".join(h.get("label", "") for h in it["reasons"]),
                                            Qt.ElideRight, r.width() - 20)
        p.setPen(_verdict_color(it["verdict"]))
        p.setFont(self._f_reason)
        p.drawText(QRect(r.left() + 10, r.bottom() - 18, r.width() - 20, 13),
                   Qt.AlignLeft | Qt.AlignVCenter, reason)
        cr = QRect(r.left() + 8, r.top() + 8, 22, 22)
        checked = it["path"] in self.model.checked
        if checked:
            p.setBrush(_c("#d97757", "#c15f3c"))
            p.setPen(Qt.NoPen)
            p.drawEllipse(cr)
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.drawLine(cr.left() + 6, cr.center().y(), cr.center().x() - 1, cr.bottom() - 7)
            p.drawLine(cr.center().x() - 1, cr.bottom() - 7, cr.right() - 5, cr.top() + 7)
        elif hovered:
            p.setBrush(QColor(0, 0, 0, 80))
            p.setPen(QPen(QColor(255, 255, 255, 150), 1.4))
            p.drawEllipse(cr)
        gi = self.model.sim_map.get(it["path"])
        if gi is not None:
            p.setPen(QPen(_c("#e5a184", "#8a5a44")))
            p.setFont(self._f_reason)
            p.drawText(QRect(r.right() - 48, r.top() + 6, 40, 16),
                       Qt.AlignRight | Qt.AlignVCenter, f"⟲{len(self.model.sim_groups[gi])}")
        p.restore()

    def sizeHint(self, opt, idx):
        return QSize(CARD_W, CARD_H)


# ================================================================ 文件夹条
class FolderChip(QWidget):
    """文件夹 chip：名称 + 扫描进度 + ✕ 一键移除。"""

    def __init__(self, path, name, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.path = path
        self.setObjectName("folderChip")
        self.setAttribute(Qt.WA_StyledBackground, True)  # QWidget 走 QSS 背景必须开
        self.setToolTip(str(path))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 2, 4, 2)
        lay.setSpacing(2)
        self.lbl = QLabel(name, self)
        lay.addWidget(self.lbl)
        self.btn_x = QToolButton(self)
        self.btn_x.setText("✕")
        self.btn_x.setToolTip("移除该文件夹")
        self.btn_x.setCursor(Qt.PointingHandCursor)
        self.btn_x.setFixedSize(16, 16)
        self.btn_x.clicked.connect(lambda: app.remove_folder(path))
        lay.addWidget(self.btn_x)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.setProperty("done", False)
        self.setProperty("active", False)

    def text(self):
        return self.lbl.text()

    def set_state(self, text, done, active):
        if self.lbl.text() != text:
            self.lbl.setText(text)
        if self.property("done") != done or self.property("active") != active:
            self.setProperty("done", done)
            self.setProperty("active", active)
            self.style().unpolish(self)
            self.style().polish(self)

    def _menu(self, pos):
        menu = QMenu(self)
        menu.addAction("在资源管理器中打开",
                       lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.path)))
        menu.addSeparator()
        menu.addAction("移除该文件夹", lambda: self.app.remove_folder(self.path))
        menu.exec(self.mapToGlobal(pos))


class FolderBar(QWidget):
    """已导入文件夹的 chip 条：显示每个文件夹 已完成/总数，扫完打 ✓。
    引擎的 folders / folder_progress 消息驱动，扫描时实时更新。"""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setObjectName("folderBar")
        self.setFixedHeight(42)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 14, 6)
        lay.setSpacing(8)
        self._chips: dict[str, QLabel] = {}
        self._box = QHBoxLayout()
        self._box.setSpacing(6)
        lay.addLayout(self._box)
        lay.addStretch(1)
        self.lbl_current = QLabel("", self)
        self.lbl_current.setObjectName("currentFile")
        self.lbl_current.setFixedWidth(300)
        self.lbl_current.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.lbl_current)
        self.progress = ProgressBar(self)
        self.progress.setFixedWidth(120)
        lay.addWidget(self.progress)
        self.lbl_pct = CaptionLabel("", self)
        self.lbl_pct.setFixedWidth(52)
        lay.addWidget(self.lbl_pct)

    def rebuild(self):
        """文件夹增删后重建 chips。"""
        while self._box.count():
            item = self._box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)   # 立即脱离显示：deleteLater 之前旧控件仍会画在原位
                w.deleteLater()
        self._chips.clear()
        roots = self.app.get_roots()
        for r in roots:
            name = Path(r).name or r
            chip = FolderChip(str(r), name, self.app, self)
            self._box.addWidget(chip)
            self._chips[str(Path(r)).rstrip("\\/")] = chip
        if not roots:
            hint = QLabel("未添加文件夹 —— 点左上「添加文件夹」或把文件夹拖进窗口", self)
            hint.setObjectName("folderHint")
            self._box.addWidget(hint)
        self.update_progress()

    def _chip_menu(self, path, chip, pos):
        menu = QMenu(self)
        menu.addAction("在资源管理器中打开", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
        menu.addSeparator()
        menu.addAction("移除该文件夹", lambda: self.app.remove_folder(path))
        menu.exec(chip.mapToGlobal(pos))

    def update_progress(self):
        # 当前扫描的文件（文件夹 / 文件名），扫描结束清空
        cur_path = getattr(self.app, "_current_file", None)
        if cur_path and self.app.scan_thread and self.app.scan_thread.is_alive():
            root = self.app._current_root or ""
            root_name = Path(root).name if root else ""
            fname = Path(cur_path).name
            text = f"正在扫描  {root_name}/{fname}" if root_name else f"正在扫描  {fname}"
            fm = QFontMetrics(self.lbl_current.font())
            self.lbl_current.setText(fm.elidedText(text, Qt.ElideMiddle, 292))
            self.lbl_current.setToolTip(str(cur_path))
        else:
            self.lbl_current.setText("")
            self.lbl_current.setToolTip("")

        totals = self.app._folder_totals
        done = self.app._folder_done
        cur_root = str(self.app._current_root).rstrip("\\/") if getattr(self.app, "_current_root", None) else None
        any_total = bool(totals)
        for key, chip in self._chips.items():
            total = totals.get(key)
            d = done.get(key, 0)
            name = Path(key).name
            if total is None:
                text, is_done = name, False
            elif d >= total:
                text, is_done = f"✓ {name}  {total}", True
            else:
                text, is_done = f"{name}  {d}/{total}", False
            is_active = (cur_root is not None and key == cur_root
                         and self.app.scan_thread is not None
                         and self.app.scan_thread.is_alive())
            chip.set_state(text, is_done, is_active)
        # 总进度（扫描中）
        total_all = self.app.total_files
        if total_all > 0 and any_total:
            d_all = sum(done.get(k, 0) for k in totals)
            # 未归属根的也在跑：用引擎广播的进度更准（见 _poll_queue）
            self.progress.setValue(int(getattr(self.app, "_last_progress", 0) * 100 / max(1, total_all)))
            self.lbl_pct.setText(f"{int(self.progress.value())}%")
        elif not any_total:
            self.progress.setValue(0)
            self.lbl_pct.setText("")


# ================================================================ CDS 组件
class Lightbox(QWidget):
    """画廊查看器（按 CDS 逆向物理常数）：
    - 暗色 backdrop（rgba(11,11,11,0.94)）
    - 滚轮缩放：scale *= exp(-deltaY * 0.01)，以光标为不动锚点
    - 拖拽平移（4px 死区过滤手抖）
    - 双击：1.0 ↔ 1.25 切换；scale <= 1.05 吸附回 1.0
    - ← / → 切换前后图；Esc 关闭
    - 底部信息条：文件名 + 判定 + 分数 + 原因
    """
    SNAP_UNDER = 1.05
    DBL_SCALE = 1.25
    WHEEL_FACTOR = 0.01
    DEAD_ZONE = 4

    def __init__(self, app, path, parent=None):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.app = app
        self.path = path
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._pix = None
        self._fit_w = self._fit_h = 0.0
        self.scale = 1.0
        self.tx = self.ty = 0.0
        self._drag_start = None
        self._dragging = False
        self._drag_tx0 = self._drag_ty0 = 0.0
        self._press_pos = None
        self._load(path)
        # 覆盖主窗口
        if parent is not None:
            self.resize(parent.size())
        self.setFocusPolicy(Qt.StrongFocus)
        self.show()

    def _load(self, path):
        self.path = path
        from PIL import Image as _Im
        try:
            with _Im.open(path) as im:
                img = im.convert("RGB")
            if max(img.size) > 2400:
                img = ImageOps.contain(img, (2400, 2400))
            rgb = img.convert("RGB")
            qimg = QImage(rgb.tobytes(), rgb.width, rgb.height, rgb.width * 3,
                          QImage.Format_RGB888).copy()
            self._pix = QPixmap.fromImage(qimg)
        except Exception:
            self._pix = None
        self.scale = 1.0
        self.tx = self.ty = 0.0
        self.update()

    def _it(self):
        return next((x for x in self.app.items if x["path"] == self.path), None)

    def _fit(self):
        if not self._pix or self._pix.isNull():
            return
        vw, vh = self.width(), self.height()
        iw, ih = self._pix.width(), self._pix.height()
        s = min(vw / iw, vh / ih)
        self._fit_w, self._fit_h = iw * s, ih * s

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        # backdrop
        p.fillRect(self.rect(), QColor(11, 11, 11, 240))
        if not self._pix or self._pix.isNull():
            p.setPen(QColor("#a3a3a3"))
            p.drawText(self.rect(), Qt.AlignCenter, "无法加载图片")
            self._draw_chrome(p)
            return
        self._fit()
        w = self._fit_w * self.scale
        h = self._fit_h * self.scale
        cx = self.width() / 2 + self.tx
        cy = self.height() / 2 + self.ty
        p.save()
        p.translate(cx, cy)
        p.scale(self.scale, self.scale)
        p.drawPixmap(int(-self._fit_w / 2), int(-self._fit_h / 2),
                     int(self._fit_w), int(self._fit_h), self._pix)
        p.restore()
        self._draw_chrome(p)

    def _draw_chrome(self, p):
        """顶部文件名 + 右上关闭；底部判定信息条。"""
        it = self._it()
        name = Path(self.path).name
        # 顶部左：文件名
        p.setPen(QColor("#e8e6e0"))
        p.setFont(QFont("Microsoft YaHei UI", 10))
        p.drawText(QRect(18, 14, self.width() - 100, 24), Qt.AlignLeft | Qt.AlignVCenter, name)
        # 右上：关闭
        cr = QRect(self.width() - 46, 14, 30, 30)
        p.setPen(QPen(QColor(255, 255, 255, 130), 1.4))
        p.setBrush(QColor(255, 255, 255, 20))
        p.drawEllipse(cr)
        p.drawLine(cr.left() + 8, cr.top() + 8, cr.right() - 8, cr.bottom() - 8)
        p.drawLine(cr.right() - 8, cr.top() + 8, cr.left() + 8, cr.bottom() - 8)
        # 底部信息条
        if it:
            verdict = VERDICT_TEXT.get(it["verdict"], it["verdict"])
            score = it.get("score", 0)
            reasons = "; ".join(h.get("label", "") for h in it.get("reasons", []))
            line = f"{verdict} · {score:.0f} 分"
            if reasons:
                line += f"  |  {reasons}"
            bar_h = 40
            bar = QRect(0, self.height() - bar_h - 16, self.width(), bar_h)
            fm = QFontMetrics(QFont("Microsoft YaHei UI", 9))
            txt = fm.elidedText(line, Qt.ElideMiddle, self.width() - 60)
            tw = min(self.width() - 40, fm.horizontalAdvance(txt) + 32)
            bx = (self.width() - tw) // 2
            pill = QRect(bx, self.height() - bar_h - 12, tw, 32)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(11, 11, 11, 200))
            p.drawRoundedRect(pill, 16, 16)
            p.setPen(QColor("#f5f5f5"))
            p.setFont(QFont("Microsoft YaHei UI", 9))
            p.drawText(pill, Qt.AlignCenter, txt)
        # 左右箭头
        p.setPen(QPen(QColor(255, 255, 255, 140), 2))
        p.setBrush(Qt.NoBrush)
        mid_y = self.height() // 2
        for x, d in ((24, -1), (self.width() - 24, 1)):
            p.drawLine(x + (0 if d < 0 else 8), mid_y - 9, x + (8 if d < 0 else 0), mid_y)
            p.drawLine(x + (0 if d < 0 else 8), mid_y + 9, x + (8 if d < 0 else 0), mid_y)

    def wheelEvent(self, e):
        import math
        factor = math.exp(-e.angleDelta().y() * self.WHEEL_FACTOR)
        old = self.scale
        max_scale = max(2.0, (self._pix.width() / max(1, self._fit_w)))
        self.scale = max(0.5, min(max_scale * 1.2, self.scale * factor))
        if self.scale < self.SNAP_UNDER:
            self.scale = 1.0
        # 光标锚点（以指针位置为不动点）
        pos = e.position()
        cx, cy = self.width() / 2, self.height() / 2
        ox, oy = pos.x() - cx, pos.y() - cy
        c = self.scale / old if old else 1
        self.tx = ox - (ox - self.tx) * c
        self.ty = oy - (oy - self.ty) * c
        self._clamp_translate()
        self.update()

    def _clamp_translate(self):
        self._fit()
        max_tx = max(0.0, (self._fit_w * self.scale - self.width()) / 2)
        max_ty = max(0.0, (self._fit_h * self.scale - self.height()) / 2)
        self.tx = max(-max_tx, min(max_tx, self.tx))
        self.ty = max(-max_ty, min(max_ty, self.ty))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            # 关闭按钮
            if QRect(self.width() - 46, 14, 30, 30).contains(e.position().toPoint()):
                self.close()
                return
            self._press_pos = e.position()
            self._drag_start = e.position()
            self._drag_tx0, self._drag_ty0 = self.tx, self.ty

    def mouseMoveEvent(self, e):
        if self._drag_start is None:
            return
        d = e.position() - self._drag_start
        if not self._dragging and (d.x() ** 2 + d.y() ** 2) ** 0.5 < self.DEAD_ZONE:
            return  # 4px 死区
        self._dragging = True
        self.tx = self._drag_tx0 + d.x()
        self.ty = self._drag_ty0 + d.y()
        self._clamp_translate()
        self.update()

    def mouseReleaseEvent(self, e):
        if not self._dragging:
            # 未拖拽且点击左右箭头 → 切换
            x = e.position().x()
            if x < 48:
                self._nav(-1)
            elif x > self.width() - 48:
                self._nav(1)
        self._drag_start = None
        self._dragging = False

    def mouseDoubleClickEvent(self, e):
        pos = e.position()
        cx, cy = self.width() / 2, self.height() / 2
        ox, oy = pos.x() - cx, pos.y() - cy
        if self.scale > 1.0:
            self.scale, self.tx, self.ty = 1.0, 0.0, 0.0
        else:
            old = self.scale
            self.scale = self.DBL_SCALE
            c = self.scale / old
            self.tx = ox - (ox - self.tx) * c
            self.ty = oy - (oy - self.ty) * c
            self._clamp_translate()
        self.update()

    def _nav(self, delta):
        items = self.app.model.items
        if not items:
            return
        try:
            idx = next(i for i, x in enumerate(items) if x["path"] == self.path)
        except StopIteration:
            return
        idx = max(0, min(len(items) - 1, idx + delta))
        self.app.grid.setCurrentIndex(self.app.model.index(idx, 0))
        self._load(items[idx]["path"])

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Escape:
            self.close()
        elif k == Qt.Key_Left:
            self._nav(-1)
        elif k == Qt.Key_Right:
            self._nav(1)


class Segmented(QWidget):
    """处置方式分段胶囊：轨道（999px 圆角 + 半透明底）+ 几何等分滑动指示器。"""

    valueChanged = Signal(int)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self._options = options
        self._index = 0
        self.setFixedHeight(30)
        self._btn_w = 0
        from PySide6.QtWidgets import QHBoxLayout as _HB
        lay = _HB(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        from PySide6.QtWidgets import QPushButton
        self._btns = []
        for i, (label, _val) in enumerate(options):
            b = QPushButton(label, self)
            b.setFlat(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setCheckable(False)
            b.setFixedHeight(26)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _c=False, i=i: self.set_index(i))
            self._btns.append(b)
            lay.addWidget(b, 1)
        self._indicator = QWidget(self)
        self._indicator.setObjectName("segIndicator")
        self._indicator.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._indicator.setAttribute(Qt.WA_StyledBackground, True)
        self._indicator.lower()
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._apply_style()
        self._style_buttons()
        self._position_indicator(animate=False)

    def _style_buttons(self):
        from qfluentwidgets import setCustomStyleSheet
        if isDarkTheme():
            qss = ("QPushButton { background: transparent; border: none; color: #a3a3a3;"
                   " font-size: 12px; padding: 0 10px; font-family: 'Microsoft YaHei UI'; }")
        else:
            qss = ("QPushButton { background: transparent; border: none; color: #525252;"
                   " font-size: 12px; padding: 0 10px; font-family: 'Microsoft YaHei UI'; }")
        for b in self._btns:
            setCustomStyleSheet(b, qss, qss)

    def _apply_style(self):
        if isDarkTheme():
            self.setStyleSheet("""
                Segmented { background: rgba(255,255,255,0.08); border-radius: 15px; }
                Segmented QPushButton { background: transparent; border: none; color: #a3a3a3;
                    font-size: 12px; padding: 0 10px; }
                Segmented QPushButton[active="true"] { color: #f5f5f5; }
                #segIndicator { background: #2c2c2a; border-radius: 13px;
                    border: 1px solid rgba(255,255,255,0.10); }
            """)
        else:
            self.setStyleSheet("""
                Segmented { background: rgba(0,0,0,0.06); border-radius: 15px; }
                Segmented QPushButton { background: transparent; border: none; color: #525252;
                    font-size: 12px; padding: 0 10px; }
                Segmented QPushButton[active="true"] { color: #151515; }
                #segIndicator { background: #ffffff; border-radius: 13px;
                    border: 1px solid rgba(0,0,0,0.08); }
            """)

    def set_index(self, i, animate=True):
        if i == self._index:
            return
        self._index = i
        self._position_indicator(animate)
        self.valueChanged.emit(i)

    def index(self):
        return self._index

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._position_indicator(animate=False)

    def _position_indicator(self, animate=True):
        n = len(self._btns)
        if not n or self.width() <= 0:
            return
        self._indicator.raise_() if False else None
        w = (self.width() - 4) / n
        x = 2 + self._index * w
        from PySide6.QtCore import QPropertyAnimation, QRectF
        if animate and hasattr(self, "_anim"):
            self._anim.stop()
        self._indicator.setGeometry(int(x), 2, int(w), self.height() - 4)
        # 按钮文字态
        from qfluentwidgets import setCustomStyleSheet
        if isDarkTheme():
            qss_a = ("QPushButton { background: transparent; border: none; color: #f5f5f5;"
                     " font-size: 12px; font-weight: 600; padding: 0 10px; font-family: 'Microsoft YaHei UI'; }")
            qss_n = ("QPushButton { background: transparent; border: none; color: #a3a3a3;"
                     " font-size: 12px; padding: 0 10px; font-family: 'Microsoft YaHei UI'; }")
        else:
            qss_a = ("QPushButton { background: transparent; border: none; color: #151515;"
                     " font-size: 12px; font-weight: 600; padding: 0 10px; font-family: 'Microsoft YaHei UI'; }")
            qss_n = ("QPushButton { background: transparent; border: none; color: #525252;"
                     " font-size: 12px; padding: 0 10px; font-family: 'Microsoft YaHei UI'; }")
        for i, b in enumerate(self._btns):
            setCustomStyleSheet(b, qss_a if i == self._index else qss_n,
                                qss_a if i == self._index else qss_n)

    def refresh_theme(self):
        self._apply_style()


class StatusDot(QWidget):
    """6px 状态指示灯：ready 常亮陶土 / running 呼吸（3s）/ error 红。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._state = "ready"
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(66)
        self._timer.timeout.connect(self._tick)

    def set_state(self, s):
        self._state = s
        if s == "running":
            self._timer.start()
        else:
            self._timer.stop()
            self._t = 0.0
        self.update()

    def _tick(self):
        self._t += 0.066 / 3.0  # 3s 周期
        self.update()

    def paintEvent(self, _e):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        if self._state == "error":
            c = QColor("#f87171" if isDarkTheme() else "#dc2626")
            r = 3.0
        elif self._state == "running":
            c = QColor("#d97757")
            # clay 呼吸：scale 0.86 → 1.0、亮度 0.96 → 1.06
            ph = (math.sin(self._t * 2 * math.pi) + 1) / 2
            r = 3.0 * (0.86 + 0.14 * ph)
            h, s, v, a = c.getHsvF()
            c = QColor.fromHsvF(h, s, min(1.0, v * (0.96 + 0.10 * ph)), a)
        else:
            c = QColor("#d97757")
            r = 3.0
        p.setBrush(c)
        p.drawEllipse(int(5 - r), int(5 - r), int(r * 2), int(r * 2))


# ================================================================ 主窗口
class App(FramelessWindow):
    _sig_preview = Signal(object)
    _sig_preview_err = Signal(object)

    def __init__(self, cfg):
        super().__init__()
        self.setObjectName("mainWindow")
        self.cfg = cfg
        self.theme = cfg.get("ui_theme", "dark")
        setTheme(Theme.DARK if self.theme == "dark" else Theme.LIGHT)
        scr = self.screen().availableGeometry() if self.screen() else None
        self.resize(min(1500, scr.width() - 80) if scr else 1460,
                    min(940, scr.height() - 100) if scr else 920)
        self.setMinimumSize(1080, 680)
        from qfluentwidgets import FluentTitleBar
        self.setTitleBar(FluentTitleBar(self))
        self.setAcceptDrops(True)

        self.items: list = []
        self.checked: set = set()
        self.similar_groups: list = []
        self._sim_map: dict = {}
        self._disposing = False
        self.scan_q = queue.Queue()
        self.cancel = threading.Event()
        self.scan_thread = None
        self.pause = threading.Event()
        self.total_files = 0
        self._pix = None
        self._preview_path = None
        self._pending_preview = None
        self._pending_rows: list = []
        self._folder_totals: dict = {}
        self._folder_done: dict = {}
        self._last_progress = 0
        self._current_file = None
        self._current_root = None
        self._big_win = None
        self._cache = None  # run_gui 注入；直接构造 App 时为 None，扫描前防御
        self._roots = []   # 不记忆上次的文件夹：每次启动从空列表开始
        self._rules_editor = None

        self.thumbs = ThumbCache()
        self.model = ResultModel()
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(160)
        self._preview_timer.timeout.connect(self._flush_preview)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(250)
        self._resize_timer.timeout.connect(self._reload_preview_for_size)
        self._sig_preview.connect(lambda m: self._apply_preview_bytes(*m))
        self._sig_preview_err.connect(self._on_preview_err)

        self._build()
        self._apply_theme()
        # 缩略图到达合并刷新：每张都 viewport.update() 会在滚动/加载时打成重绘风暴
        self._thumb_dirty = False
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(150)
        self._thumb_timer.timeout.connect(self._flush_thumb_update)
        self.thumbs.ready.connect(self._on_thumb_ready)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_queue)
        self._timer.start(120)

    # ---------- 主题 ----------
    def _apply_theme(self):
        if isDarkTheme():
            self.setStyleSheet("""
                /* CDS 真实令牌：surface-1 主画布 / surface-0 侧栏底层 / surface-2 卡片 / surface-3 高层 */
                #mainWindow { background: #151515; }
                #topBar { background: #0b0b0b; border-bottom: 1px solid rgba(255,255,255,0.09); }
                #bottomBar { background: #0b0b0b; border-top: 1px solid rgba(255,255,255,0.09); }
                #detailPanel { background: #1a1a19; border-left: 1px solid rgba(255,255,255,0.09); }
                #folderBar { background: #101010; border-bottom: 1px solid rgba(255,255,255,0.07); }
                #folderChip {
                    background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.09);
                    border-radius: 6px;
                }
                #folderChip QLabel { background: transparent; color: #a3a3a3; font-size: 12px; }
                #folderChip QToolButton {
                    border: none; background: transparent; color: rgba(217,214,205,0.42);
                    font-size: 11px; border-radius: 8px;
                }
                #folderChip QToolButton:hover { color: #ff8a6b; background: rgba(217,119,87,0.20); }
                #folderChip[done="true"] { background: rgba(143,188,143,0.12); border-color: rgba(143,188,143,0.45); }
                #folderChip[done="true"] QLabel { color: #a8cf9f; }
                #folderChip[active="true"] {
                    border-color: rgba(217,119,87,0.85); background: rgba(217,119,87,0.10);
                }
                #folderChip[active="true"] QLabel { color: #f0b39a; }
                #folderHint { color: #666666; font-size: 12px; }
                #currentFile { color: #a3a3a3; font-size: 12px; }
            """)
        else:
            self.setStyleSheet("""
                #mainWindow { background: #fcfcfb; }
                #topBar { background: #f9f9f7; border-bottom: 1px solid rgba(20,20,20,0.10); }
                #bottomBar { background: #f9f9f7; border-top: 1px solid rgba(20,20,20,0.10); }
                #detailPanel { background: #ffffff; border-left: 1px solid rgba(20,20,20,0.10); }
                #folderBar { background: #f6f6f4; border-bottom: 1px solid rgba(20,20,20,0.07); }
                #folderChip {
                    background: #ffffff; border: 1px solid rgba(20,20,20,0.10);
                    border-radius: 6px;
                }
                #folderChip QLabel { background: transparent; color: #525252; font-size: 12px; }
                #folderChip QToolButton {
                    border: none; background: transparent; color: rgba(74,71,63,0.40);
                    font-size: 11px; border-radius: 8px;
                }
                #folderChip QToolButton:hover { color: #b3402a; background: rgba(193,95,60,0.12); }
                #folderChip[done="true"] { background: rgba(61,122,74,0.08); border-color: rgba(61,122,74,0.35); }
                #folderChip[done="true"] QLabel { color: #3d7a4a; }
                #folderChip[active="true"] {
                    border-color: rgba(193,95,60,0.8); background: rgba(193,95,60,0.07);
                }
                #folderChip[active="true"] QLabel { color: #8a5a44; }
                #folderHint { color: #8a8a8a; font-size: 12px; }
                #currentFile { color: #525252; font-size: 12px; }
            """)
        self._apply_brand_style()
        self._apply_info_bg()
        if hasattr(self, "log"):
            self._apply_log_bg()

    def _apply_brand_style(self):
        color = "#f5f5f5" if isDarkTheme() else "#151515"
        self.brand.setStyleSheet(
            'font-family: Georgia, "Times New Roman", "Songti SC", "SimSun", serif;'
            f"font-size: 19px; font-weight: 600; letter-spacing: 0.5px; color: {color};")

    def _apply_log_bg(self):
        from qfluentwidgets import setCustomStyleSheet
        qss = ("TextEdit { background: #0b0b0b; border-top: 1px solid rgba(255,255,255,0.09);"
               " color: #a3a3a3; }"
               if isDarkTheme() else
               "TextEdit { background: #f9f9f7; border-top: 1px solid rgba(20,20,20,0.10);"
               " color: #525252; }")
        setCustomStyleSheet(self.log, qss, qss)

    def _apply_info_bg(self):
        qss = ("TextEdit { background: #20201f; border: 1px solid rgba(255,255,255,0.09);"
               " border-radius: 10px; color: #a3a3a3; }") if isDarkTheme() else \
              ("TextEdit { background: #ffffff; border: 1px solid rgba(20,20,20,0.10);"
               " border-radius: 10px; color: #525252; }")
        setCustomStyleSheet(self.info, qss, qss)

    def toggle_theme(self):
        new = Theme.LIGHT if isDarkTheme() else Theme.DARK
        setTheme(new)
        self.theme = "dark" if new == Theme.DARK else "light"
        self.cfg["ui_theme"] = self.theme
        af.save_config(self.cfg)
        self._apply_theme()
        self.grid.viewport().update()
        return self

    # ---------- UI ----------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 48, 0, 0)
        root.setSpacing(0)

        # 顶部工具栏
        top = QWidget(self)
        top.setObjectName("topBar")
        top.setFixedHeight(54)
        tl = QHBoxLayout(top)
        tl.setContentsMargins(18, 8, 14, 8)
        tl.setSpacing(8)
        self.brand = QLabel("广告图过滤", top)
        tl.addWidget(self.brand)
        tl.addSpacing(8)
        self.btn_add = TransparentPushButton(FIF.FOLDER_ADD, "添加文件夹", top)
        self.btn_add.clicked.connect(self.add_folder)
        tl.addWidget(self.btn_add)
        self.btn_scan = PrimaryPushButton(FIF.PLAY, "开始扫描", top)
        self.btn_scan.setFixedWidth(112)
        self.btn_scan.clicked.connect(self.start_scan)
        tl.addWidget(self.btn_scan)
        self.btn_pause = TransparentPushButton(FIF.PAUSE, "暂停", top)
        self.btn_pause.clicked.connect(self.toggle_pause)
        self.btn_pause.setEnabled(False)
        tl.addWidget(self.btn_pause)
        self.btn_cancel = TransparentPushButton(FIF.CANCEL, "取消", top)
        self.btn_cancel.clicked.connect(self.cancel_scan)
        tl.addWidget(self.btn_cancel)
        self.btn_cfg = ToolButton(FIF.SETTING, top)
        self.btn_cfg.setToolTip("扫描设置与规则")
        self.btn_cfg.clicked.connect(self.open_config)
        tl.addWidget(self.btn_cfg)
        tl.addStretch(1)
        self.dot = StatusDot(top)
        tl.addWidget(self.dot)
        self.status = CaptionLabel("就绪", top)
        tl.addWidget(self.status)
        self.btn_theme = TransparentToolButton(FIF.BRIGHTNESS, top)
        self.btn_theme.setToolTip("切换浅色 / 深色")
        self.btn_theme.clicked.connect(self.toggle_theme)
        tl.addWidget(self.btn_theme)
        root.addWidget(top)

        # —— 文件夹条（已导入文件夹 + 每个文件夹扫描进度）——
        self.folder_bar = FolderBar(self)
        self.folder_bar.rebuild()
        root.addWidget(self.folder_bar)

        # 中部：图片墙 + 详情
        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)

        left = QWidget(self)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(14, 10, 10, 8)
        ll.setSpacing(8)
        frow = QHBoxLayout()
        frow.setSpacing(8)
        self.f_verdict = ComboBox(left)
        self.f_verdict.addItems(["全部", "广告", "疑似"])
        self.f_verdict.setFixedWidth(86)
        self.f_verdict.currentIndexChanged.connect(lambda _i: self.refresh())
        frow.addWidget(self.f_verdict)
        self.f_reason = ComboBox(left)
        self.f_reason.addItems(["全部原因"])
        self.f_reason.setFixedWidth(150)
        self.f_reason.currentIndexChanged.connect(lambda _i: self.refresh())
        frow.addWidget(self.f_reason)
        self.f_search = SearchLineEdit(left)
        self.f_search.setPlaceholderText("路径过滤…")
        self.f_search.setFixedWidth(180)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.refresh)
        self.f_search.textChanged.connect(lambda _t: self._search_timer.start())
        frow.addWidget(self.f_search)
        frow.addStretch(1)
        self.lbl_count = CaptionLabel("命中 0 · 已选 0", left)
        frow.addWidget(self.lbl_count)
        ll.addLayout(frow)

        self.grid = QListView(left)
        self.grid.setObjectName("grid")
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setGridSize(QSize(CARD_W + 8, CARD_H + 8))
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setSelectionRectVisible(True)
        self.grid.setModel(self.model)
        self.delegate = CardDelegate(self.thumbs, self.model, self.grid)
        self.grid.setItemDelegate(self.delegate)
        self.grid.setFrameShape(QListView.NoFrame)
        self.grid.setStyleSheet("QListView { background: transparent; }")
        self.grid.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.grid.selectionModel().currentChanged.connect(self._on_current_changed)
        self.grid.doubleClicked.connect(lambda _idx: self.open_big())          # 双击开大图
        self.grid.selectionModel().selectionChanged.connect(self._on_multi_select)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._on_context_menu)
        ll.addWidget(self.grid, 1)
        self.grid.viewport().installEventFilter(self)
        self.grid.installEventFilter(self)
        mid.addWidget(left, 1)

        detail = QWidget(self)
        detail.setObjectName("detailPanel")
        detail.setFixedWidth(360)
        dv = QVBoxLayout(detail)
        dv.setContentsMargins(14, 14, 14, 12)
        dv.setSpacing(8)
        dv.addWidget(CaptionLabel("详情", detail))
        self.preview = QLabel("选中图片查看预览", detail)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("color:rgba(140,134,124,0.7);")
        self.preview.setMinimumHeight(150)
        dv.addWidget(self.preview, 1)
        self.info = TextEdit(detail)
        self.info.setReadOnly(True)
        self.info.setFixedHeight(152)
        dv.addWidget(self.info)
        drow = QHBoxLayout()
        drow.setSpacing(6)
        self.btn_prev = ToolButton(FIF.LEFT_ARROW, detail)
        self.btn_prev.setToolTip("上一张（←）")
        self.btn_prev.clicked.connect(lambda: self.step(-1))
        drow.addWidget(self.btn_prev)
        self.btn_next = ToolButton(FIF.RIGHT_ARROW, detail)
        self.btn_next.setToolTip("下一张（→）")
        self.btn_next.clicked.connect(lambda: self.step(1))
        drow.addWidget(self.btn_next)
        self.btn_mark = PushButton("标记广告", detail)
        self.btn_mark.clicked.connect(lambda: self.mark_current(True))
        drow.addWidget(self.btn_mark)
        self.btn_keep = PushButton("保留", detail)
        self.btn_keep.clicked.connect(lambda: self.mark_current(False))
        drow.addWidget(self.btn_keep)
        drow.addStretch(1)
        self.btn_big = TransparentPushButton(FIF.ZOOM_IN, "大图", detail)
        self.btn_big.clicked.connect(self.open_big)
        drow.addWidget(self.btn_big)
        dv.addLayout(drow)
        mid.addWidget(detail)
        root.addLayout(mid, 1)

        # 底部状态条
        bot = QWidget(self)
        bot.setObjectName("bottomBar")
        bot.setFixedHeight(62)
        bl = QHBoxLayout(bot)
        bl.setContentsMargins(16, 12, 14, 12)
        bl.setSpacing(12)
        self.progress = self.folder_bar.progress  # 进度条合并到文件夹条
        self.btn_log = TransparentPushButton("操作日志", bot)
        self.btn_log.setCheckable(True)
        self.btn_log.toggled.connect(self.toggle_log)
        bl.addWidget(self.btn_log)
        bl.addWidget(CaptionLabel("处置", bot))
        self.seg_disposal = Segmented(
            [("隔离", "quarantine"), ("回收站", "recycle"), ("删除", "delete")], bot)
        self.seg_disposal.setFixedWidth(240)
        self.seg_disposal.setToolTip("隔离=可恢复；回收站=进系统回收站；删除=永久删除")
        self.cb_disposal = None
        # 测试兼容别名
        self.btn_dq = self.seg_disposal._btns[0]
        self.btn_dr = self.seg_disposal._btns[1]
        self.btn_dd = self.seg_disposal._btns[2]
        bl.addWidget(self.seg_disposal)
        bl.addStretch(1)
        self.btn_openq = TransparentPushButton("打开隔离目录", bot)
        self.btn_openq.clicked.connect(self.open_quarantine_dir)
        bl.addWidget(self.btn_openq)
        self.btn_restore = TransparentPushButton("恢复隔离区…", bot)
        self.btn_restore.clicked.connect(self.restore_ui)
        bl.addWidget(self.btn_restore)
        self.btn_dispose = PrimaryPushButton(FIF.DELETE, "执行处置", bot)
        self.btn_dispose.clicked.connect(self.execute_disposal)
        bl.addWidget(self.btn_dispose)
        root.addWidget(bot)

        # 操作日志（默认折叠，点"操作日志"展开）
        self.log = TextEdit(self)
        self.log.setReadOnly(True)
        self.log.setFixedHeight(0)
        self.log.setVisible(False)
        root.addWidget(self.log)

        self._apply_styles()

    def _apply_styles(self):
        W = {
            "disp": ("""PushButton { background: transparent; border: 1px solid rgba(20,20,20,0.18); border-radius: 8px; padding: 4px 14px; color: #151515; }
            PushButton:hover { background: rgba(20,20,20,0.05); }
            PushButton:checked { background: #c6613f; border-color: #c6613f; color: #ffffff; }""",
                """PushButton { background: transparent; border: 1px solid rgba(255,255,255,0.18); border-radius: 8px; padding: 4px 14px; color: #f5f5f5; }
            PushButton:hover { background: rgba(255,255,255,0.06); }
            PushButton:checked { background: #c6613f; border-color: #c6613f; color: #ffffff; }"""),
            "primary": ("""PrimaryPushButton { background: #c6613f; border: none; border-radius: 8px; padding: 6px 16px; color: #ffffff; }
            PrimaryPushButton:hover { background: #cc5a36; }
            PrimaryPushButton:disabled { background: rgba(20,20,20,0.10); color: rgba(20,20,20,0.30); }""",
                """PrimaryPushButton { background: #c6613f; border: none; border-radius: 8px; padding: 6px 16px; color: #ffffff; }
            PrimaryPushButton:hover { background: #d97757; }
            PrimaryPushButton:disabled { background: rgba(255,255,255,0.08); color: rgba(255,255,255,0.28); }"""),
            "trans": ("""TransparentPushButton { background: transparent; border: none; border-radius: 8px; padding: 4px 10px; color: #a34a2c; }
            TransparentPushButton:hover { background: rgba(198,97,63,0.08); }""",
                """TransparentPushButton { background: transparent; border: none; border-radius: 8px; padding: 4px 10px; color: #e5a184; }
            TransparentPushButton:hover { background: rgba(217,119,87,0.12); }"""),
            "push": ("""PushButton { background: transparent; border: 1px solid rgba(20,20,20,0.18); border-radius: 8px; padding: 5px 14px; color: #151515; }
            PushButton:hover { background: rgba(20,20,20,0.05); }""",
                """PushButton { background: transparent; border: 1px solid rgba(255,255,255,0.18); border-radius: 8px; padding: 5px 14px; color: #f5f5f5; }
            PushButton:hover { background: rgba(255,255,255,0.06); }"""),
            "input": ("""ComboBox, SearchLineEdit { background: #ffffff; border: 1px solid rgba(20,20,20,0.10); border-radius: 8px; padding: 4px 10px; color: #151515; }
            ComboBox:focus, SearchLineEdit:focus { border: 1px solid #c6613f; }
            ComboBox::drop-down { border: none; width: 24px; }""",
                """ComboBox, SearchLineEdit { background: #20201f; border: 1px solid rgba(255,255,255,0.09); border-radius: 8px; padding: 4px 10px; color: #f5f5f5; }
            ComboBox:focus, SearchLineEdit:focus { border: 1px solid #d97757; }
            ComboBox::drop-down { border: none; width: 24px; }"""),
            "radio": ("""RadioButton { color: #1f1e1d; spacing: 5px; min-height: 24px; padding: 2px 0; }
            RadioButton::indicator { width: 14px; height: 14px; border-radius: 7px; border: 1px solid rgba(31,30,29,0.25); }
            RadioButton::indicator:checked { background: #c15f3c; border-color: #c15f3c; }""",
                """RadioButton { color: #f5f4ef; spacing: 5px; min-height: 24px; padding: 2px 0; }
            RadioButton::indicator { width: 14px; height: 14px; border-radius: 7px; border: 1px solid rgba(245,244,239,0.28); }
            RadioButton::indicator:checked { background: #d97757; border-color: #d97757; }"""),
            "tool": ("""ToolButton { background: transparent; border: 1px solid rgba(20,20,20,0.16); border-radius: 8px; }
            ToolButton:hover { background: rgba(20,20,20,0.05); }""",
                """ToolButton { background: transparent; border: 1px solid rgba(255,255,255,0.16); border-radius: 8px; }
            ToolButton:hover { background: rgba(255,255,255,0.06); }"""),
            "progress": ("""ProgressBar { background: rgba(31,30,29,0.08); border: none; border-radius: 3px; height: 4px; min-height: 4px; max-height: 4px; }
            ProgressBar::chunk { background: #c15f3c; border-radius: 3px; }""",
                """ProgressBar { background: rgba(245,244,239,0.08); border: none; border-radius: 3px; height: 4px; min-height: 4px; max-height: 4px; }
            ProgressBar::chunk { background: #d97757; border-radius: 3px; }"""),
        }
        for w in (self.btn_scan, self.btn_dispose):
            setCustomStyleSheet(w, *W["primary"])
        for w in (self.btn_add, self.btn_cancel, self.btn_openq, self.btn_restore, self.btn_big):
            setCustomStyleSheet(w, *W["trans"])
        for w in (self.btn_mark, self.btn_keep):
            setCustomStyleSheet(w, *W["push"])
        for w in (self.f_verdict, self.f_reason, self.f_search):
            setCustomStyleSheet(w, *W["input"])
        # Segmented 自带样式，不走 W["disp"]
        for w in (self.btn_cfg, self.btn_theme, self.btn_prev, self.btn_next):
            setCustomStyleSheet(w, *W["tool"])
        setCustomStyleSheet(self.btn_log, *W["trans"])
        setCustomStyleSheet(self.progress, *W["progress"])

    # ---------- 网格交互 ----------
    def eventFilter(self, obj, ev):
        g = getattr(self, "grid", None)
        if g is not None and obj is g and ev.type() == QEvent.KeyPress:
            k = ev.key()
            if k == Qt.Key_Space:
                idx = g.currentIndex()
                if idx.isValid():
                    self.toggle_check(idx.row())
                    return True
            elif k in (Qt.Key_Return, Qt.Key_Enter):
                self.open_big()
                return True
            elif k == Qt.Key_A and (ev.modifiers() & Qt.ControlModifier):
                self.select_all()
                return True
            return False
        if g is not None and obj is g.viewport():
            if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
                idx = self.grid.indexAt(ev.position().toPoint())
                if idx.isValid():
                    vr = self.grid.visualRect(idx)
                    cr = QRect(vr.left() + 13, vr.top() + 13, 22, 22)
                    if cr.contains(ev.position().toPoint()):
                        self.toggle_check(idx.row())
                        return True
            elif ev.type() == QEvent.KeyPress:
                k = ev.key()
                if k == Qt.Key_Space:
                    idx = self.grid.currentIndex()
                    if idx.isValid():
                        self.toggle_check(idx.row())
                        return True
                elif k in (Qt.Key_Return, Qt.Key_Enter):
                    self.open_big()
                    return True
                elif k in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_A):
                    if k == Qt.Key_A and (ev.modifiers() & Qt.ControlModifier):
                        self.select_all()
                        return True
        return super().eventFilter(obj, ev)

    def toggle_check(self, row):
        if row < 0 or row >= len(self.model.items):
            return
        p = self.model.items[row]["path"]
        if p in self.model.checked:
            self.model.checked.discard(p)
        else:
            self.model.checked.add(p)
        self.checked = self.model.checked
        self.grid.viewport().update()
        self._update_count_label()

    def _on_multi_select(self, *_args):
        """多选（Ctrl/Shift/框选）≥2 张时自动勾选——批量处置的主要入口。"""
        if self._table_loading if hasattr(self, "_table_loading") else False:
            return
        rows = {ix.row() for ix in self.grid.selectionModel().selectedRows()}
        if len(rows) < 2:
            return
        changed = False
        for r in rows:
            if 0 <= r < len(self.model.items):
                p = self.model.items[r]["path"]
                if p not in self.model.checked:
                    self.model.checked.add(p)
                    changed = True
        if changed:
            self.checked = self.model.checked
            self.grid.viewport().update()
            self._update_count_label()

    def _on_thumb_ready(self, _path):
        if not self._thumb_dirty:
            self._thumb_dirty = True
            self._thumb_timer.start()

    def _flush_thumb_update(self):
        self._thumb_dirty = False
        self.grid.viewport().update()

    def _on_current_changed(self, cur, _prev):
        if cur.isValid() and cur.row() < len(self.model.items):
            self.show_preview(self.model.items[cur.row()]["path"])

    def _on_context_menu(self, pos):
        idx = self.grid.indexAt(pos)
        menu = QMenu(self)
        if idx.isValid():
            menu.addAction("勾选 / 取消勾选", lambda: self.toggle_check(idx.row()))
            menu.addAction("打开大图", self.open_big)
            menu.addSeparator()
        menu.addAction("全选", self.select_all)
        menu.addAction("反选", self.invert_sel)
        menu.addAction("清除选择", self.clear_sel)
        menu.addAction("选相似组", self.select_similar_groups)
        menu.exec(self.grid.viewport().mapToGlobal(pos))

    def select_all(self):
        for it in list(self.model.items):
            self.model.checked.add(it["path"])
        self.checked = self.model.checked
        self.grid.viewport().update()
        self._update_count_label()
        return self

    def clear_sel(self):
        self.model.checked.clear()
        self.checked = self.model.checked
        self.grid.viewport().update()
        self._update_count_label()
        return self

    def invert_sel(self):
        """反选：已勾的取消、没勾的勾上。"""
        for it in list(self.model.items):
            p = it["path"]
            if p in self.model.checked:
                self.model.checked.discard(p)
            else:
                self.model.checked.add(p)
        self.checked = self.model.checked
        self.grid.viewport().update()
        self._update_count_label()
        return self

    def remove_folder(self, path=None):
        """移除一个导入文件夹（chip 右键 / 配置弹窗）。"""
        if path is None:
            return self
        self._roots = [r for r in self._roots if str(r) != str(path)]
        af.save_config(self.cfg)
        self.folder_bar.rebuild()
        self._toast(f"已移除文件夹：{Path(path).name}")
        return self

    def select_similar_groups(self):
        if not self.similar_groups:
            self.status.setText("未发现相似组（或聚类尚未完成）")
            return self
        n = 0
        for g in self.similar_groups:
            for p in g:
                self.model.checked.add(p)
                n += 1
        self.checked = self.model.checked
        self.grid.viewport().update()
        self._update_count_label()
        self.status.setText(f"已勾选 {n} 张相似图（{len(self.similar_groups)} 组）")
        return self

    # ---------- 弹窗 ----------
    def _ask(self, title, content):
        return bool(MessageBox(title, content, self).exec())

    def _info(self, title, content):
        MessageBox(title, content, self).exec()

    def _toast(self, content, ok=True):
        (InfoBar.success if ok else InfoBar.warning)(
            title="", content=content, orient=Qt.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP, duration=2500, parent=self)

    def build_config(self):
        return ConfigDialog(self, self.cfg)

    def open_config(self):
        d = self.build_config()
        d.exec()

    def build_rule_editor(self):
        return RuleEditorLite(self, self.cfg)

    def open_rule_editor(self):
        self.build_rule_editor().exec()

    # ---------- 拖入 ----------
    def dragEnterEvent(self, e):
        if af.collect_dirs(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if af.collect_dirs(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        dirs = af.collect_dirs(e.mimeData())
        if dirs:
            self.add_folders(dirs)
            e.acceptProposedAction()
        else:
            e.ignore()

    def closeEvent(self, e):
        self.cancel.set()
        try:
            self._timer.stop()
        except Exception:
            pass
        try:
            self.thumbs.stop()
        except Exception:
            pass
        if self._disposing:
            if not self._ask("正在处置", "文件处置正在进行中，现在退出会中断移动。\n确定退出？"):
                e.ignore()
                return
        e.accept()

    # ---------- 文件夹 ----------
    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "选择要扫描的文件夹")
        if d:
            self.add_folders([d])

    def add_folders(self, dirs):
        before = len(self._roots)
        self._roots = list(dict.fromkeys(self._roots + [str(d) for d in dirs]))
        added = len(self._roots) - before
        if added:
            af.save_config(self.cfg)
            self._toast(f"已添加 {added} 个文件夹（共 {len(self._roots)} 个）")
        if hasattr(self, "folder_bar"):
            self.folder_bar.rebuild()
        return self

    def get_roots(self):
        return list(self._roots)

    def toggle_log(self, on):
        self.log.setVisible(bool(on))
        self.log.setFixedHeight(130 if on else 0)
        self.btn_log.setText("收起日志" if on else "操作日志")

    def _log(self, msg):
        """状态栏 + 操作日志面板双写。"""
        if not msg:
            return
        self.status.setText(msg)
        if hasattr(self, "log"):
            from datetime import datetime
            self.log.append(f"[{datetime.now():%H:%M:%S}] {msg}")

    # ---------- 扫描 ----------
    def start_scan(self):
        roots = self.get_roots()
        if not roots:
            self._info("提示", "请先在设置里添加要扫描的文件夹")
            return
        if self.scan_thread and self.scan_thread.is_alive():
            self._info("提示", "扫描正在进行中")
            return
        enable = {"filename": self.cfg.get("_enable_filename", True),
                  "shape": self.cfg.get("_enable_shape", True),
                  "qr": self.cfg.get("qr", {}).get("enabled", True),
                  "ocr": self.cfg.get("ocr", {}).get("enabled", True)}
        if not any(enable.values()):
            self._info("提示", "至少启用一种识别方式")
            return
        if self._cache is None:
            self._cache = af.Cache(af.CACHE_PATH)
        self.cfg["disposal"] = self._current_disposal()
        # 识别方式变化后旧判定不可信（缓存键只有 path/size/mtime），必须清缓存全量重判
        if self.cfg.get("_last_enable") != enable:
            try:
                self._cache.clear_results()
            except Exception:
                pass
            self.cfg["_last_enable"] = dict(enable)
        # 规则/阈值变化（同一会话内改了规则或 OCR 选项）也要即时清缓存
        try:
            self._cache.check_config_version(self.cfg)
        except Exception:
            pass
        af.save_config(self.cfg)
        self.cancel.clear()
        self.pause.clear()
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("暂停")
        self.items.clear()
        self.checked.clear()
        self.model.checked.clear()
        self.model.reset_items([])
        self.similar_groups = []
        self._sim_map = {}
        self.model.sim_map = {}
        self.model.sim_groups = []
        self._folder_totals = {}
        self._folder_done = {}
        self._pending_rows.clear()
        self._current_file = None
        self._current_root = None
        self.btn_scan.setEnabled(False)
        self.dot.set_state("running")
        self._log(f"开始扫描：{len(roots)} 个文件夹")
        self.status.setText("正在收集文件…")
        self._last_progress = 0
        self.progress.setValue(0)
        self.folder_bar.update_progress()
        self.scan_thread = threading.Thread(
            target=af.scan_worker,
            args=(roots, self.cfg, enable, self._cache, self.scan_q, self.cancel, self.pause),
            daemon=True)
        self.scan_thread.start()
        return self

    def toggle_pause(self):
        """暂停/继续扫描（引擎分批提交，暂停时真正停下不再烧 CPU）。"""
        if self.pause.is_set():
            self.pause.clear()
            self.btn_pause.setText("暂停")
            self.status.setText("继续扫描…")
            self._log("继续扫描")
        else:
            self.pause.set()
            self.btn_pause.setText("继续")
            self.status.setText(f"已暂停 · {getattr(self, '_last_progress', 0)}/{self.total_files} · 命中 {len(self.items)}")
            self._log(f"暂停扫描（{getattr(self, '_last_progress', 0)}/{self.total_files}）")
        return self

    def cancel_scan(self):
        self.cancel.set()
        self.pause.clear()
        self.status.setText("取消中…")
        return self

    # ---------- 轮询 ----------
    def _poll_queue(self):
        items_batch = []
        latest_progress = None
        done = cancelled = False
        disposed = restored = None
        similar = None
        cached_n = None
        folder_updated = False
        current_hit = False
        try:
            while True:
                kind, payload = self.scan_q.get_nowait()
                if kind == "total":
                    self.total_files = payload
                    self.progress.setValue(0)
                elif kind == "cached":
                    cached_n = payload
                elif kind == "current":
                    root, fname = payload
                    self._current_root = root
                    self._current_file = (str(Path(root) / fname) if root else fname)
                    current_hit = True
                elif kind == "folders":
                    self._folder_totals = {str(k).rstrip("\\/"): int(v) for k, v in (payload or {}).items()}
                    folder_updated = True
                elif kind == "folder_progress":
                    r, d, t = payload
                    self._folder_done[str(r).rstrip("\\/")] = d
                    self._folder_totals[str(r).rstrip("\\/")] = t
                    folder_updated = True
                elif kind == "item":
                    if payload["verdict"] in ("ad", "suspect", "error"):
                        self.items.append(payload)
                        items_batch.append(payload)
                elif kind == "progress":
                    latest_progress = payload
                elif kind == "cancelled":
                    cancelled = True
                elif kind == "disposed":
                    disposed = payload
                elif kind == "restored":
                    restored = payload
                elif kind == "similar":
                    similar = payload
                elif kind == "done":
                    done = True
        except queue.Empty:
            pass
        except Exception:
            pass

        try:
            if items_batch:
                self._pending_rows.extend(items_batch)
            self._flush_rows()
            if latest_progress is not None:
                self._last_progress = latest_progress
                self.progress.setValue(int(latest_progress * 100 / max(1, self.total_files)))
                cached_hint = f" · 缓存 {cached_n}" if cached_n else ""
                if self.pause.is_set():
                    self.status.setText(f"已暂停 · {latest_progress}/{self.total_files} · 命中 {len(self.items)}")
                else:
                    self.status.setText(f"{latest_progress}/{self.total_files}{cached_hint} · 命中 {len(self.items)}")
            if folder_updated or latest_progress is not None or current_hit:
                self.folder_bar.update_progress()
            if cancelled:
                self.btn_scan.setEnabled(True)
                self.btn_pause.setEnabled(False)
                self.btn_pause.setText("暂停")
                self.dot.set_state("ready")
                self.status.setText("已取消")
            elif disposed is not None:
                mode, moved, errors, ok_paths = disposed
                self._disposing = False
                pset = set(ok_paths)
                self.items = [it for it in self.items if it["path"] not in pset]
                for p in pset:
                    self.model.checked.discard(p)
                self.checked = self.model.checked
                self.similar_groups = []
                self._sim_map = {}
                self.model.sim_map = {}
                self.model.sim_groups = []
                self.refresh()
                self._reload_reason_filter()
                self.btn_scan.setEnabled(True)
                self.btn_dispose.setEnabled(True)
                self.btn_restore.setEnabled(True)
                msg = f"处置完成：{moved} 张成功" + (f"，{len(errors)} 张失败" if errors else "")
                self.status.setText(msg)
                self._log("处置（" + {"quarantine": "隔离", "recycle": "回收站", "delete": "删除"}.get(mode, mode) + "）" + msg)
                self._toast(msg, ok=not errors)
                if errors:
                    self._info("处置结果", msg + "\n" + "\n".join(errors[:5]))
            elif restored is not None:
                restored_n, errors = restored
                self._disposing = False
                self.btn_restore.setEnabled(True)
                self.status.setText(f"恢复完成：{restored_n} 张")
                self._log(f"从隔离区恢复 {restored_n} 张")
                self._toast(f"恢复完成：{restored_n} 张")
                if errors:
                    self._info("恢复结果", "\n".join(errors[:5]))
            elif done:
                self.btn_scan.setEnabled(True)
                self.btn_pause.setEnabled(False)
                self.btn_pause.setText("暂停")
                self.dot.set_state("ready")
                n_ad = sum(1 for it in self.items if it["verdict"] == "ad")
                n_su = len(self.items) - n_ad
                self.status.setText(f"扫描完成 · 命中 {len(self.items)}（广告 {n_ad} / 疑似 {n_su}）")
                self._log(f"扫描完成：命中 {len(self.items)}（广告 {n_ad} / 疑似 {n_su}）")
                self._current_file = None
                self._current_root = None
                self.folder_bar.update_progress()
                self._pending_rows.clear()
                self.refresh()
                self._reload_reason_filter()
                self.folder_bar.update_progress()
                self._start_similar()
            if similar is not None:
                self._apply_similar(similar)
        except Exception:
            import traceback
            traceback.print_exc()

    def _flush_rows(self, limit=30):
        if not self._pending_rows:
            return
        batch = self._pending_rows[:limit]
        del self._pending_rows[:limit]
        fp = self._filter_params()
        add = [it for it in batch if self._passes_filter(it, fp)]
        if add:
            start = len(self.model.items)
            self.model.beginInsertRows(QModelIndex(), start, start + len(add) - 1)
            self.model.items.extend(add)
            self.model.endInsertRows()
            self._update_count_label()

    # ---------- 筛选 ----------
    def _filter_params(self):
        return (self.f_verdict.currentText(), self.f_reason.currentText(),
                self.f_search.text().strip())

    def _passes_filter(self, it, fp=None):
        if fp is None:
            fp = self._filter_params()
        v, r, q = fp
        if v == "广告" and it["verdict"] != "ad":
            return False
        if v == "疑似" and it["verdict"] not in ("suspect", "error"):
            return False
        if r != "全部原因" and not any(h.get("label", "").startswith(r) for h in it["reasons"]):
            return False
        if q and q not in it["path"]:
            return False
        return True

    def refresh(self):
        self.model.reset_items([it for it in self.items if self._passes_filter(it)])
        self.model.checked = self.checked
        self.grid.viewport().update()
        self._update_count_label()

    def _reload_reason_filter(self):
        labels = set()
        for it in self.items:
            for h in it["reasons"]:
                lab = h.get("label", "")
                key = (lab.split("]")[0] + "]") if lab.startswith("OCR") else lab.split(" ")[0]
                if key:
                    labels.add(key)
        items = ["全部原因"] + sorted(labels)
        cur = self.f_reason.currentText()
        self.f_reason.blockSignals(True)
        self.f_reason.clear()
        self.f_reason.addItems(items)
        self.f_reason.setCurrentIndex(items.index(cur) if cur in items else 0)
        self.f_reason.blockSignals(False)

    def _update_count_label(self):
        self.lbl_count.setText(f"命中 {len(self.model.items)} · 已选 {len(self.checked)}")

    # ---------- 相似 ----------
    def _start_similar(self):
        if not self.cfg.get("phash", {}).get("enabled", True) or len(self.items) < 2:
            return
        paths = [it["path"] for it in self.items]
        thr = int(self.cfg.get("phash", {}).get("threshold", 6))

        def work():
            groups = af.cluster_similar(paths, thr, self._cache)
            self.scan_q.put(("similar", groups))

        threading.Thread(target=work, daemon=True).start()

    def _apply_similar(self, groups):
        if not self.items:
            return  # 处置后延迟到达的相似消息：结果已清空，忽略
        self.similar_groups = groups
        self._sim_map = {p: i for i, g in enumerate(groups) for p in g}
        self.model.sim_map = self._sim_map
        self.model.sim_groups = groups
        self.grid.viewport().update()
        if groups:
            n_p = sum(len(g) for g in groups)
            self.status.setText(
                f"命中 {len(self.items)} 张 · 相似 {len(groups)} 组共 {n_p} 张（右键可批量勾选）")

    # ---------- 预览 ----------
    def _current_item(self):
        idx = self.grid.currentIndex()
        if idx.isValid() and idx.row() < len(self.model.items):
            return self.model.items[idx.row()]
        return None

    def show_preview(self, path):
        self._pending_preview = path
        self._preview_timer.start()

    def _flush_preview(self):
        path = self._pending_preview
        if not path:
            return
        self._preview_path = path
        it = next((x for x in self.items if x["path"] == path), None)
        if not it:
            return
        p_disp = path if len(path) <= 46 else (path[:14] + "…" + path[-28:])  # 360px 宽单行约 46 字符
        info = f"{p_disp}\n判定: {VERDICT_TEXT.get(it['verdict'], it['verdict'])}  分数: {it['score']:.0f}\n"
        info += "\n".join(f"· {h.get('label', '')}" for h in it["reasons"])
        if it.get("ocr_text"):
            info += f"\nOCR: {it['ocr_text'][:180]}"
        gi = self._sim_map.get(path)
        if gi is not None:
            info += f"\n相似组: {len(self.similar_groups[gi])} 张"
        self.info.setPlainText(info)

        tw = max(200, self.preview.width() - 6)
        th = max(120, self.preview.height() - 6)

        def work():
            try:
                from PIL import ImageOps
                img = af.load_pixel_image(Path(path), max(tw, th, 720))
                img = ImageOps.contain(img, (tw, th))
                rgb = img.convert("RGB")
                self._sig_preview.emit((path, rgb.tobytes(), rgb.width, rgb.height))
            except Exception as e:
                self._sig_preview_err.emit((path, str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _apply_preview_bytes(self, path, data, w, h):
        if path != self._preview_path:
            return
        qimg = QImage(data, w, h, w * 3, QImage.Format_RGB888).copy()
        self._pix = QPixmap.fromImage(qimg)
        self.preview.setPixmap(self._pix)

    def _on_preview_err(self, msg):
        path, err = msg
        if self._preview_path == path:
            self._pix = None
            self.preview.setText(f"预览失败 {err}")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "_preview_path", None):
            self._resize_timer.start()

    def _reload_preview_for_size(self):
        if self._preview_path:
            self._pending_preview = self._preview_path
            self._flush_preview()

    def step(self, delta):
        n = len(self.model.items)
        if not n:
            return
        row = self.grid.currentIndex().row()
        row = max(0, min(n - 1, (row if row >= 0 else 0) + delta))
        self.grid.setCurrentIndex(self.model.index(row, 0))
        return self

    def mark_current(self, as_ad):
        cur = self._current_item()
        if not cur:
            return
        if as_ad:
            self.model.checked.add(cur["path"])
            self.checked = self.model.checked
            self.grid.viewport().update()
            self._update_count_label()
        else:
            gone = cur["path"]
            self.items = [x for x in self.items if x["path"] != gone]
            self._pending_rows = [x for x in self._pending_rows if x["path"] != gone]
            self.model.checked.discard(gone)
            self.checked = self.model.checked
            self.refresh()
        self.step(1)
        return self

    def open_big(self):
        """CDS 画廊查看器：暗底 + 滚轮缩放/拖拽平移/双击切换 + ←→/Esc 关闭。"""
        cur = self._current_item()
        if not cur:
            return self
        win = getattr(self, "_big_win", None)
        if win is not None:
            try:
                win.close()
            except RuntimeError:
                pass
        self._big_win = Lightbox(self, cur["path"], parent=self)
        return self

    # ---------- 处置 ----------
    def _current_disposal(self):
        seg = getattr(self, "seg_disposal", None)
        idx = seg.index() if seg is not None else 0
        return {1: "recycle", 2: "delete"}.get(idx, "quarantine")

    def execute_disposal(self):
        checked_items = [it for it in self.items if it["path"] in self.checked]
        if not checked_items:
            self._info("提示", "请先勾选要处置的图片（点卡片左上圆圈或按空格）")
            return
        if self.scan_thread and self.scan_thread.is_alive():
            self._info("提示", "扫描正在进行，请等扫描结束或先取消再处置\n（处置会让正在分析的文件消失，结果不可信）")
            return
        mode = self._current_disposal()
        if mode == "delete" and not self._ask(
                "危险操作", f"将永久删除 {len(checked_items)} 张图片，不可恢复！\n确定继续？"):
            return
        roots = self.get_roots()
        q_root = None
        if mode == "quarantine":
            base = Path(roots[0]).anchor if roots else str(Path.home().anchor)
            q_root = Path(base) / af.QUARANTINE_NAME
            if not self._ask("隔离确认",
                             f"将把 {len(checked_items)} 张图移动到\n{q_root}\n（可随时恢复）\n继续？"):
                return
        self.btn_scan.setEnabled(False)
        self.btn_dispose.setEnabled(False)
        self.btn_restore.setEnabled(False)
        self.status.setText("处置中…")

        def work():
            self._disposing = True
            moved, errors, ok_paths = 0, [], []
            try:
                if mode == "quarantine":
                    moved, errors, ok_paths = af.apply_quarantine(checked_items, roots, q_root, af.log_jsonl)
                elif mode == "recycle":
                    moved, errors, ok_paths = af.apply_recycle(checked_items, af.log_jsonl)
                else:
                    moved, errors, ok_paths = af.apply_delete(checked_items, af.log_jsonl)
            finally:
                self.scan_q.put(("disposed", (mode, moved, errors, ok_paths)))

        threading.Thread(target=work, daemon=True).start()
        return self

    def open_quarantine_dir(self):
        roots = self.get_roots()
        base = Path(roots[0]).anchor if roots else str(Path.home().anchor)
        q_root = Path(base) / af.QUARANTINE_NAME
        if not q_root.exists():
            self._info("提示", f"隔离目录还不存在：\n{q_root}\n（执行一次隔离处置后会自动创建）")
            return self
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(q_root)))
        return self

    def restore_ui(self):
        d = QFileDialog.getExistingDirectory(self, "选择隔离目录（含 manifest.json）")
        if not d:
            return
        self.btn_restore.setEnabled(False)

        def work():
            self._disposing = True
            restored, errors = 0, []
            try:
                restored, errors = af.restore_quarantine(Path(d), af.log_jsonl)
            finally:
                self.scan_q.put(("restored", (restored, errors)))

        threading.Thread(target=work, daemon=True).start()
        return self


# ================================================================ 配置弹窗
class FolderDropList(ListWidget):
    """可拖入文件夹的列表。"""

    def dragEnterEvent(self, e):
        if af.collect_dirs(e.mimeData()):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if af.collect_dirs(e.mimeData()):
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        dirs = af.collect_dirs(e.mimeData())
        if not dirs:
            super().dropEvent(e)
            return
        existing = [self.item(i).data(Qt.UserRole) for i in range(self.count())]
        for d in dict.fromkeys(dirs):
            if d in existing:
                continue
            it = QListWidgetItem(str(d))
            it.setData(Qt.UserRole, str(d))
            self.addItem(it)
        e.acceptProposedAction()


class ConfigDialog(MessageBoxBase):
    """扫描设置：文件夹 + 识别方式 + 线程 + 规则入口。"""

    def __init__(self, app, cfg):
        super().__init__(app)
        self.app = app
        self.cfg = cfg
        self.widget.setFixedWidth(640)

        self.viewLayout.addWidget(SubtitleLabel("扫描设置", self.widget))
        tip = CaptionLabel("添加要扫描的文件夹、选择识别方式。", self.widget)
        tip.setWordWrap(True)
        self.viewLayout.addWidget(tip)

        self.viewLayout.addWidget(CaptionLabel("文件夹（可直接拖入）", self.widget))
        self.folder_list = FolderDropList(self.widget)
        for r in app.get_roots():
            it = QListWidgetItem(str(r))
            it.setData(Qt.UserRole, str(r))
            self.folder_list.addItem(it)
        self.folder_list.setFixedHeight(140)
        self.folder_list.setTextElideMode(Qt.ElideMiddle)
        self.folder_list.setAcceptDrops(True)
        self.viewLayout.addWidget(self.folder_list)
        frow = QHBoxLayout()
        b_add = TransparentPushButton(FIF.FOLDER_ADD, "添加文件夹", self.widget)
        b_add.clicked.connect(self._add)
        b_rm = TransparentPushButton(FIF.REMOVE, "移除选中", self.widget)
        b_rm.clicked.connect(self._rm)
        b_rules = TransparentPushButton(FIF.SETTING, "规则管理…", self.widget)
        b_rules.clicked.connect(self._open_rules)
        for b in (b_add, b_rm, b_rules):
            frow.addWidget(b)
        frow.addStretch(1)
        self.viewLayout.addLayout(frow)

        self.viewLayout.addWidget(CaptionLabel("识别方式", self.widget))
        crow = QHBoxLayout()
        self.cb_fn = CheckBox("文件名", self.widget)
        self.cb_fn.setChecked(cfg.get("_enable_filename", True))
        self.cb_shape = CheckBox("形状", self.widget)
        self.cb_shape.setChecked(cfg.get("_enable_shape", True))
        self.cb_qr = CheckBox("二维码", self.widget)
        self.cb_qr.setChecked(cfg.get("qr", {}).get("enabled", True))
        self.cb_ocr = CheckBox("OCR 文字", self.widget)
        self.cb_ocr.setChecked(cfg.get("ocr", {}).get("enabled", True))
        self.cb_sim = CheckBox("相似聚类", self.widget)
        self.cb_sim.setChecked(cfg.get("phash", {}).get("enabled", True))
        for c in (self.cb_fn, self.cb_shape, self.cb_qr, self.cb_ocr, self.cb_sim):
            crow.addWidget(c)
        crow.addStretch(1)
        self.viewLayout.addLayout(crow)

        orow = QHBoxLayout()
        orow.addWidget(CaptionLabel("全图兜底", self.widget))
        self.ocr_scope = ComboBox(self.widget)
        self.ocr_scope.addItems(["条带优先（快）", "条带+全图兜底（稳）"])
        self.ocr_scope.setCurrentIndex(1 if cfg.get("ocr", {}).get("full_image_if_nothing", True) else 0)
        self.ocr_scope.setToolTip("条带没找到广告词汇时，是否再对整张图做一次 OCR（中央水印必须开）")
        orow.addWidget(self.ocr_scope)
        orow.addSpacing(16)
        orow.addWidget(CaptionLabel("线程", self.widget))
        self.cb_workers = ComboBox(self.widget)
        self.cb_workers.addItems([str(i) for i in range(1, 17)])
        self.cb_workers.setCurrentIndex(max(0, min(15, int(cfg.get("workers", 4)) - 1)))
        orow.addWidget(self.cb_workers)
        orow.addStretch(1)
        self.viewLayout.addLayout(orow)

        self.yesButton.setText("保存")
        self.cancelButton.setText("关闭")
        self.widget.setMinimumWidth(640)

    def _add(self):
        d = QFileDialog.getExistingDirectory(self, "选择要扫描的文件夹")
        if not d:
            return
        existing = [self.folder_list.item(i).data(Qt.UserRole) for i in range(self.folder_list.count())]
        if d in existing:
            return
        it = QListWidgetItem(d)
        it.setData(Qt.UserRole, d)
        self.folder_list.addItem(it)

    def _rm(self):
        for it in self.folder_list.selectedItems():
            self.folder_list.takeItem(self.folder_list.row(it))

    def _open_rules(self):
        self.app.build_rule_editor().exec()

    def validate(self):
        roots = [self.folder_list.item(i).data(Qt.UserRole) for i in range(self.folder_list.count())]
        self.app._roots = [r for r in roots if r]
        self.cfg["_enable_filename"] = self.cb_fn.isChecked()
        self.cfg["_enable_shape"] = self.cb_shape.isChecked()
        self.cfg["qr"]["enabled"] = self.cb_qr.isChecked()
        self.cfg["ocr"]["enabled"] = self.cb_ocr.isChecked()
        self.cfg["ocr"]["full_image_if_nothing"] = (self.ocr_scope.currentIndex() == 1)
        self.cfg["phash"] = {"enabled": self.cb_sim.isChecked(),
                             "threshold": int(self.cfg.get("phash", {}).get("threshold", 6))}
        self.cfg["workers"] = self.cb_workers.currentIndex() + 1
        af.save_config(self.cfg)
        if hasattr(self.app, "folder_bar"):
            self.app.folder_bar.rebuild()
        self.accept()
        return True


# ================================================================ 规则编辑（精简版）
class RuleEditorLite(MessageBoxBase):
    """规则管理：文件名 / OCR 词库 / 白名单，Pivot 切换（currentItemChanged 驱动）。"""

    def __init__(self, parent, cfg_ref):
        super().__init__(parent)
        self.cfg = cfg_ref
        self.show_errors = True
        self.widget.setFixedWidth(820)

        self.viewLayout.addWidget(SubtitleLabel("规则管理", self.widget))
        tip = CaptionLabel("双击单元格编辑；保存时校验正则与权重。规则变更后下次扫描自动全量重判。",
                           self.widget)
        tip.setWordWrap(True)
        self.viewLayout.addWidget(tip)

        self.pivot = Pivot(self.widget)
        self.stack = QStackedWidget(self.widget)
        self._pages = {
            "filename_rules": self._rule_page(cfg_ref.get("filename_rules", []), False, "文件名规则"),
            "ocr_keywords": self._rule_page(cfg_ref.get("ocr_keywords", []), True, "OCR 词库"),
            "whitelist_rules": self._rule_page(cfg_ref.get("whitelist_rules", []), False, "白名单"),
        }
        for key, pg in self._pages.items():
            self.stack.addWidget(pg["page"])
            self.pivot.addItem(routeKey=key, text=pg["label"])
        self.pivot.currentItemChanged.connect(
            lambda k: self.stack.setCurrentWidget(self._pages[k]["page"]))
        self.viewLayout.addWidget(self.pivot)
        self.viewLayout.addWidget(self.stack)

        self.yesButton.setText("保存规则")
        self.cancelButton.setText("关闭")
        self.widget.setMinimumWidth(820)

    def _rule_page(self, rules, with_weight, label):
        from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout as HB, QHeaderView, QTableWidgetItem, QWidget
        page = QWidget(self.widget)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        t = TableWidget(page)
        cols = ["规则（正则）", "标签"] + (["权重"] if with_weight else [])
        t.setColumnCount(len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.verticalHeader().setVisible(False)
        t.setShowGrid(False)
        t.setWordWrap(False)
        t.setBorderVisible(True)
        t.setBorderRadius(8)
        t.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                          | QAbstractItemView.AnyKeyPressed | QAbstractItemView.SelectedClicked)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        t.setColumnWidth(1, 160)
        if with_weight:
            t.setColumnWidth(2, 76)
        t.verticalHeader().setDefaultSectionSize(34)
        t.setRowCount(len(rules))
        for r, rule in enumerate(rules):
            t.setItem(r, 0, QTableWidgetItem(str(rule.get("pattern", ""))))
            t.setItem(r, 1, QTableWidgetItem(str(rule.get("label", ""))))
            if with_weight:
                t.setItem(r, 2, QTableWidgetItem(str(rule.get("weight", 1))))
        lay.addWidget(t, 1)
        row = HB()
        row.setContentsMargins(0, 6, 0, 0)
        btn_add = TransparentPushButton(FIF.ADD, "新增规则", page)
        btn_add.clicked.connect(lambda: self._add_row(t, with_weight))
        row.addWidget(btn_add)
        btn_del = TransparentPushButton(FIF.DELETE, "删除选中行", page)
        btn_del.clicked.connect(lambda: t.removeRow(t.currentRow()) if t.currentRow() >= 0 else None)
        row.addWidget(btn_del)
        row.addStretch(1)
        lay.addLayout(row)
        t.setMinimumHeight(260)
        return {"page": page, "table": t, "weight": with_weight, "label": label}

    def _add_row(self, t, with_weight):
        r = t.rowCount()
        t.setRowCount(r + 1)
        from PySide6.QtWidgets import QTableWidgetItem
        t.setItem(r, 0, QTableWidgetItem(""))
        t.setItem(r, 1, QTableWidgetItem("自定义"))
        if with_weight:
            t.setItem(r, 2, QTableWidgetItem("1"))

    def validate(self):
        return self.do_save()

    def do_save(self):
        import re
        bad = []
        new_rules = {}
        for key, pg in self._pages.items():
            t, with_weight, label = pg["table"], pg["weight"], pg["label"]
            rules = []
            for r in range(t.rowCount()):
                pat_it, lab_it = t.item(r, 0), t.item(r, 1)
                pat = (pat_it.text() if pat_it else "").strip()
                lab = (lab_it.text() if lab_it else "").strip()
                if not pat:
                    continue
                try:
                    re.compile(pat)
                except re.error:
                    bad.append(f"{label} 第{r + 1}行：正则无效「{pat[:40]}」")
                    continue
                rule = {"pattern": pat, "label": lab or "自定义"}
                if with_weight:
                    w_it = t.item(r, 2)
                    try:
                        rule["weight"] = max(1, int(float((w_it.text() if w_it else "1").strip() or 1)))
                    except ValueError:
                        bad.append(f"{label} 第{r + 1}行：权重须为数字")
                        continue
                rules.append(rule)
            new_rules[key] = rules
        if bad:
            if self.show_errors:
                InfoBar.error(title="无法保存", content="；".join(bad[:3]),
                              orient=Qt.Horizontal, isClosable=True,
                              position=InfoBarPosition.TOP, duration=4000, parent=self)
            return False
        for key, rules in new_rules.items():
            self.cfg[key] = rules
        af.save_config(self.cfg)
        InfoBar.success(title="已保存", content="规则已写入配置，下次扫描生效",
                        orient=Qt.Horizontal, isClosable=True,
                        position=InfoBarPosition.TOP, duration=2500, parent=self)
        self.accept()
        return True


# ================================================================ 入口
def run_gui(return_app=False):
    qapp = QApplication.instance() or QApplication(sys.argv)
    setThemeColor("#c6613f")   # CDS 官方主品牌色（陶土橙深阶）
    cfg = af.load_config()
    app = App(cfg)
    app._cache = af.Cache(af.CACHE_PATH)
    app._cache.check_config_version(cfg)
    if return_app:
        return app
    app.show()
    sys.exit(qapp.exec())   # 主事件循环——缺了它窗口一闪而过（exe 闪退根因）


# ================================================================ GUI 功能自测（v6 网格）
def gui_func_test() -> int:
    """GUI 功能自测：真窗口 + 临时目录，不碰真实数据。"""
    import json
    import shutil
    import tempfile

    results: list = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond)))
        print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""),
              flush=True)

    tmp = Path(tempfile.mkdtemp(prefix="adfilter_guitest_"))
    _c, _cf, _l = af.CACHE_PATH, af.CONFIG_PATH, af.LOG_PATH
    af.CACHE_PATH = tmp / "cache.db"
    af.CONFIG_PATH = tmp / "cfg.json"
    af.LOG_PATH = tmp / "log.jsonl"
    af.CONFIG_PATH.write_text(json.dumps({"ui_theme": "dark", "workers": 2}), encoding="utf-8")

    src_dir = af.APP_DIR / "testdata"
    if not (src_dir / "type1_拼图引流.jpg").exists():
        af.make_fixtures(src_dir)
    scan_dir = tmp / "photos"
    (scan_dir / "sub").mkdir(parents=True)
    for nm in ("type1_拼图引流.jpg", "type2_长条贴图.png", "normal_01.jpg"):
        shutil.copy(src_dir / nm, scan_dir / nm)
    # 相似组样本：type2 重编码副本
    from PIL import Image as _Im
    _Im.open(scan_dir / "type2_长条贴图.png").convert("RGB").save(
        scan_dir / "sub" / "copy_type2.png", quality=72)

    qapp = QApplication.instance() or QApplication([])
    app = run_gui(return_app=True)
    app._ask = lambda title, content: True
    app._info = lambda title, content: None
    app._toast = lambda content, ok=True: None
    app.show()

    def pump(seconds=0.0, until=None, timeout=120):
        t0 = time.time()
        while time.time() - t0 < timeout:
            qapp.processEvents()
            if until and until():
                return True
            if seconds > 0 and time.time() - t0 >= seconds:
                return False
            time.sleep(0.02)
        return False

    try:
        # —— 启动时不记忆上次扫描的文件夹 ——
        check("启动时文件夹列表为空", len(app.get_roots()) == 0,
              f"roots={app.get_roots()}")

        # —— 扫描 ——
        app.add_folders([str(scan_dir)])
        app.cfg["qr"]["enabled"] = True
        app.cfg["ocr"]["enabled"] = False
        app.cfg["_enable_filename"] = True
        app.cfg["_enable_shape"] = True
        app.start_scan()
        done = pump(until=lambda: "扫描完成" in app.status.text(), timeout=90)
        check("扫描完成", done)
        check("网格随命中出现", len(app.model.items) > 0, f"model={len(app.model.items)}")
        check("命中数正确(3 广告)", len(app.items) == 3, f"items={len(app.items)}")
        check("网格项数=命中数", app.model.rowCount() == len(app.items),
              f"rows={app.model.rowCount()}")
        check("正常图不进网格", all("normal_01" not in it["path"] for it in app.items))
        check("状态栏构成明示", "广告" in app.status.text() and "疑似" in app.status.text(),
              app.status.text())
        check("扫描按钮恢复可用", app.btn_scan.isEnabled())

        # —— 文件夹 chip（已导入文件夹 + 每个文件夹的扫描进度）——
        chips = app.folder_bar._chips
        check("已导入文件夹显示为 chip", len(chips) >= 1, f"chips={list(chips.keys())}")
        chip_texts = [c.text() for c in chips.values()]
        check("chip 显示扫描完成打勾", all("✓" in t for t in chip_texts), chip_texts)
        check("chip 带张数", all(any(ch.isdigit() for ch in t) for t in chip_texts), chip_texts)

        # —— 筛选 ——
        app.f_verdict.setCurrentIndex(1)  # 广告
        pump(0.2)
        check("筛选-广告", all(it["verdict"] == "ad" for it in app.model.items) and len(app.model.items) > 0,
              f"rows={len(app.model.items)}")
        app.f_verdict.setCurrentIndex(0)
        pump(0.2)
        check("筛选-全部恢复", len(app.model.items) == len(app.items))

        # —— 预览 ——
        app.grid.setCurrentIndex(app.model.index(0, 0))
        app._flush_preview()
        pump(0.7)
        check("预览加载成功", app.preview.pixmap() is not None and not app.preview.pixmap().isNull())
        check("预览信息面板有内容", len(app.info.toPlainText().strip()) > 0)
        first = app.model.items[0]["path"]
        app.step(1)
        pump(0.4)
        check("下一张切换", app.grid.currentIndex().row() == 1)
        app.step(-1)
        pump(0.4)
        check("上一张切换", app.grid.currentIndex().row() == 0)

        # —— 勾选 ——
        app.clear_sel()
        from PySide6.QtTest import QTest
        QTest.keyClick(app.grid, Qt.Key_Space)
        pump(0.15)
        check("空格勾选当前项", len(app.model.checked) == 1, f"checked={len(app.model.checked)}")
        QTest.keyClick(app.grid, Qt.Key_Space)
        pump(0.15)
        check("空格取消勾选", len(app.model.checked) == 0)

        # 勾选圈点击（卡片左上角圆圈）
        idx0 = app.model.index(0, 0)
        vr = app.grid.visualRect(idx0)
        from PySide6.QtCore import QPoint
        pos = QPoint(vr.left() + 13 + 11, vr.top() + 13 + 11)
        QTest.mouseClick(app.grid.viewport(), Qt.LeftButton, Qt.NoModifier, pos)
        pump(0.15)
        check("点击圆圈勾选", len(app.model.checked) == 1, f"checked={len(app.model.checked)}")

        app.select_all()
        check("全选", len(app.model.checked) == len(app.model.items))
        app.invert_sel()
        check("反选", len(app.model.checked) == 0, f"checked={len(app.model.checked)}")
        app.invert_sel()
        check("反选再反选=全选", len(app.model.checked) == len(app.model.items))
        app.clear_sel()
        check("清除选择", len(app.model.checked) == 0)

        # —— 多选（Ctrl/框选）自动勾选 ——
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        app.clear_sel()
        app.grid.clearSelection()
        sel = QItemSelection(app.model.index(0, 0), app.model.index(1, 0))
        app.grid.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        pump(0.15)
        check("多选自动勾选", len(app.model.checked) == 2, f"checked={len(app.model.checked)}")
        app.clear_sel()
        app.grid.clearSelection()

        # —— 上一张 / 下一张按钮 ——
        app.grid.setCurrentIndex(app.model.index(0, 0))
        app.btn_next.click()
        pump(0.3)
        check("下一张按钮", app.grid.currentIndex().row() == 1)
        app.btn_prev.click()
        pump(0.3)
        check("上一张按钮", app.grid.currentIndex().row() == 0)

        # —— 双击开大图 ——
        app.grid.setCurrentIndex(app.model.index(0, 0))
        app.grid.doubleClicked.emit(app.model.index(0, 0))
        pump(0.4)
        bw2 = getattr(app, "_big_win", None)
        check("双击开大图", bw2 is not None and isinstance(bw2, Lightbox))
        if bw2 is not None:
            lb = bw2
            lb.show()
            from PySide6.QtCore import QPoint
            class _W:
                def __init__(s, dy): s._dy = dy
                def angleDelta(s): return QPoint(0, s._dy)
                def position(s): return QPoint(lb.width() // 2, lb.height() // 2)
            s0 = lb.scale
            lb.wheelEvent(_W(-240)); lb.wheelEvent(_W(-240))
            check("滚轮放大", lb.scale > s0, f"{s0}->{lb.scale}")
            class _D:
                def position(s): return QPoint(lb.width() // 2, lb.height() // 2)
            lb.mouseDoubleClickEvent(_D())
            check("双击回缩 1.0", lb.scale == 1.0, lb.scale)
            lb.mouseDoubleClickEvent(_D())
            check("双击放大 1.25", abs(lb.scale - 1.25) < 0.01, lb.scale)
            if app.model.rowCount() >= 2:
                p0 = lb.path
                lb._nav(1)
                check("Lightbox 切换下一张", lb.path != p0, lb.path)
            lb.keyPressEvent(type("K", (), {"key": lambda self: Qt.Key_Escape})())
            pump(0.3)
            try:
                gone = not lb.isVisible()
            except RuntimeError:
                gone = True
            check("Esc 关闭 Lightbox", gone)
        pump(0.2)

        # —— 操作日志面板 ——
        app.btn_log.setChecked(True)
        pump(0.2)
        check("日志面板展开", app.log.isVisible() and app.log.height() > 60,
              f"visible={app.log.isVisible()} h={app.log.height()}")
        check("日志有内容", len(app.log.toPlainText().strip()) > 0, app.log.toPlainText()[:40])
        app.btn_log.setChecked(False)
        pump(0.2)
        check("日志面板收起", not app.log.isVisible())

        # —— 相似组 ——
        sim_ok = pump(until=lambda: bool(app.similar_groups), timeout=30)
        check("相似聚类完成", sim_ok)
        pair = [g for g in app.similar_groups
                if any("type2_长条贴图" in p for p in g) and any("copy_type2" in p for p in g)]
        check("同图副本聚成一组", len(pair) == 1, f"groups={app.similar_groups}")
        check("卡片相似标注", bool(app.model.sim_map), f"map={app.model.sim_map}")
        app.select_similar_groups()
        check("选相似组批量勾选", len(app.model.checked) >= 2, f"n={len(app.model.checked)}")
        app.clear_sel()

        # —— 主题切换 ——
        t0_dark = isDarkTheme()
        app.toggle_theme()
        pump(0.2)
        check("主题切换生效", isDarkTheme() != t0_dark)
        app.toggle_theme()
        pump(0.2)

        # —— 大图窗口：连开两次不崩（WA_DeleteOnClose 后引用保护）——
        app.grid.setCurrentIndex(app.model.index(0, 0))
        app.open_big()
        pump(0.35)
        try:
            app.open_big()
            big_ok = True
        except RuntimeError:
            big_ok = False
        check("大图连开两次不崩", big_ok)
        bw = getattr(app, "_big_win", None)
        if bw is not None:
            try:
                bw.close()
            except RuntimeError:
                pass
        pump(0.2)

        # —— 标记广告 / 保留 ——
        app.clear_sel()
        n_mark = len(app.items)
        app.grid.setCurrentIndex(app.model.index(0, 0))
        app.mark_current(True)
        pump(0.1)
        check("标记广告=勾选", len(app.checked) >= 1)
        app.clear_sel()
        app.grid.setCurrentIndex(app.model.index(0, 0))
        app.mark_current(False)
        pump(0.15)
        check("保留=移出当前结果", len(app.items) == n_mark - 1,
              f"items={len(app.items)} expect={n_mark - 1}")

        # —— 增量二次扫描（缓存秒开，被保留的图会重新命中）——
        t_scan = time.time()
        app.start_scan()
        done2 = pump(until=lambda: "扫描完成" in app.status.text(), timeout=60)
        elapsed2 = time.time() - t_scan
        check("二次扫描完成", done2)
        check("二次扫描结果一致", len(app.items) == n_mark, f"items={len(app.items)} expect={n_mark}")
        check("二次扫描快速(缓存)", elapsed2 < 15, f"{elapsed2:.1f}s")

        # —— 配置弹窗：保存生效 ——
        from PySide6.QtWidgets import QListWidgetItem as _LWI
        dlg3 = app.build_config()
        newdir = tmp / "新文件夹"
        newdir.mkdir()
        it3 = _LWI(str(newdir))
        it3.setData(Qt.UserRole, str(newdir))
        dlg3.folder_list.addItem(it3)
        dlg3.ocr_scope.setCurrentIndex(0)
        ok_save = dlg3.validate()
        pump(0.15)
        check("配置保存返回真", ok_save is True)
        check("配置保存后 roots 生效", str(newdir) in app.get_roots())
        check("全图兜底开关已接线", app.cfg["ocr"]["full_image_if_nothing"] is False)

        # —— 移除文件夹 ——
        n_roots_before = len(app.get_roots())
        app.remove_folder(str(newdir))
        pump(0.1)
        check("移除文件夹生效", len(app.get_roots()) == n_roots_before - 1
              and str(newdir) not in app.get_roots())

        # —— chip 上的 ✕ 一键移除 ——
        tmp_rm = tmp / "临时文件夹X"
        tmp_rm.mkdir()
        app.add_folders([str(tmp_rm)])
        pump(0.1)
        keys_rm = [k for k in app.folder_bar._chips if "临时文件夹X" in k]
        check("chip 有 ✕ 按钮", bool(keys_rm) and hasattr(app.folder_bar._chips[keys_rm[0]], "btn_x"),
              f"chips={list(app.folder_bar._chips.keys())}")
        if keys_rm:
            app.folder_bar._chips[keys_rm[0]].btn_x.click()
            pump(0.1)
            check("点击 ✕ 移除文件夹", str(tmp_rm) not in app.get_roots(),
                  f"roots={app.get_roots()}")
        app._roots = [r for r in app.get_roots() if r != str(newdir)]
        app.cfg["ocr"]["full_image_if_nothing"] = True

        # —— 识别开关真的生效（审计修复项：此前勾掉仍执行）——
        app.cfg["_enable_filename"] = False
        app.cfg["_enable_shape"] = False
        app.start_scan()
        pump(until=lambda: "扫描完成" in app.status.text(), timeout=60)
        check("关掉形状/文件名后命中大幅减少", len(app.items) < n_mark,
              f"items={len(app.items)} expect<{n_mark}")
        app.cfg["_enable_filename"] = True
        app.cfg["_enable_shape"] = True
        app.start_scan()
        pump(until=lambda: "扫描完成" in app.status.text(), timeout=60)
        check("恢复开关后重新命中", len(app.items) == n_mark, f"items={len(app.items)}")

        # —— 处置（隔离，指向扫描根盘根）——
        app.btn_dq.setChecked(True)
        app.select_all()
        n_before = len(app.items)
        app.execute_disposal()
        pump(until=lambda: "处置完成" in app.status.text(), timeout=60)
        check("处置完成状态", "处置完成" in app.status.text(), app.status.text())
        check("网格随处置移除", len(app.items) == 0, f"items={len(app.items)}")
        check("网格已清空", app.model.rowCount() == 0)
        q_root = Path(str(scan_dir)).anchor
        ipath = Path(q_root) / af.QUARANTINE_NAME
        check("隔离区指向扫描根盘根", ipath.exists(), str(ipath))
        man = ipath / "manifest.json" if ipath.exists() else Path("_none_")
        n_added = 0
        if man.exists():
            entries = json.loads(man.read_text(encoding="utf-8")).get("items", [])
            n_added = sum(1 for e in entries
                          if str(scan_dir) in str(e.get("orig", e.get("src", ""))) or
                          "photos" in str(e.get("orig", e.get("src", ""))))
        check("处置清单含本次文件", n_added >= n_before, f"added={n_added} expect>={n_before}")
        # —— 恢复隔离区：文件应回到原位 ——
        if ipath.exists():
            restored_n, rerrs = af.restore_quarantine(ipath, af.log_jsonl)
            check("恢复隔离区", restored_n >= n_before and not rerrs,
                  f"restored={restored_n} errs={rerrs[:2]}")
            check("文件已回原位", (scan_dir / "type1_拼图引流.jpg").exists())
        # 清理隔离产物
        try:
            shutil.rmtree(ipath, ignore_errors=True)
        except Exception:
            pass

        # —— 暂停 / 继续 ——
        slow_src = next(iter(sorted(src_dir.glob("real4*.jpg"))), None) \
            or next(iter(sorted(src_dir.glob("real*.jpg"))), None) \
            or src_dir / "type1_拼图引流.jpg"   # 无真实样本时用合成拼图（文件名不含引流词则走 OCR）
        slow_dir = tmp / "慢相册"
        slow_dir.mkdir()
        for k in range(16):
            shutil.copy(slow_src, slow_dir / f"{k}_r4.jpg")
        app._roots = [str(slow_dir)]
        app.folder_bar.rebuild()   # 直接改 _roots 后须重建 chips（正常流程走 add_folders 会自动重建）
        app.cfg["ocr"]["enabled"] = True
        app.cfg["qr"]["enabled"] = True
        app.start_scan()
        started = pump(until=lambda: getattr(app, "_last_progress", 0) >= 3, timeout=60)
        # —— 扫描中显示"当前扫描的文件名" + 当前文件夹高亮 ——
        cur_label = app.folder_bar.lbl_current.text()
        check("扫描中显示当前文件", "正在扫描" in cur_label and "." in cur_label, cur_label)
        active = [c for c in app.folder_bar._chips.values() if c.property("active")]
        check("当前文件夹 chip 高亮", len(active) >= 1, f"active={len(active)}")
        app.toggle_pause()
        pump(0.3)
        p1 = getattr(app, "_last_progress", 0)
        check("暂停按钮变继续", app.btn_pause.text() == "继续", app.btn_pause.text())
        pump(1.6)
        p2 = getattr(app, "_last_progress", 0)
        # 语义：暂停后不再提交新任务，已在跑的当前批次（≤workers 个）会跑完才停
        check("暂停后进度停住", started and (p2 - p1) <= 6, f"p1={p1} p2={p2}")
        app.toggle_pause()
        finished = pump(until=lambda: "扫描完成" in app.status.text(), timeout=120)
        check("继续后扫描完成", finished, app.status.text()[:40])
        check("暂停按钮复位", app.btn_pause.text() == "暂停" and not app.btn_pause.isEnabled())

        # —— 处置方式三个按钮 ——
        check("处置方式三个可见按钮",
              app.btn_dq.isVisible() and app.btn_dr.isVisible() and app.btn_dd.isVisible())
        app.seg_disposal.set_index(2)
        check("处置方式切换生效", app._current_disposal() == "delete")
        app.seg_disposal.set_index(0)
        check("处置方式切回隔离", app._current_disposal() == "quarantine")

        # —— 配置弹窗 ——
        dlg = app.build_config()
        check("配置弹窗含文件夹项", dlg.folder_list.count() >= 1, f"count={dlg.folder_list.count()}")
        check("配置弹窗识别开关", dlg.cb_qr is not None and dlg.cb_ocr is not None)
        dlg.close()

        # —— 规则弹窗：Pivot 切换 + 无效正则 ——
        dlg2 = app.build_rule_editor()
        before = dlg2.stack.currentWidget()
        dlg2.pivot.setCurrentItem("ocr_keywords")
        pump(0.15)
        check("规则标签切换生效", dlg2.stack.currentWidget() is not before)
        page_t = dlg2._pages["filename_rules"]["table"]
        r = page_t.rowCount()
        page_t.setRowCount(r + 1)
        from PySide6.QtWidgets import QTableWidgetItem as _QI
        page_t.setItem(r, 0, _QI(r"坏正则(["))
        check("无效正则拒收", dlg2.do_save() is False)
        dlg2.close()

    finally:
        app.close()
        qapp.processEvents()
        af.CACHE_PATH, af.CONFIG_PATH, af.LOG_PATH = _c, _cf, _l
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, ok in results if ok)
    print(f"\nGUI 功能自测：{passed}/{len(results)} 通过" + ("" if passed == len(results) else " ❌"))
    return 0 if passed == len(results) else 1
