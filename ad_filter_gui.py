# -*- coding: utf-8 -*-
"""广告图过滤 · GUI v6「图片墙」

形态：缩略图画布（自绘卡片网格）+ 右侧详情 + 顶部极简工具栏 + 底部状态条
交互：单击选中看详情；空格/点圆圈勾选；↑↓←→ 移动；双击/Enter 大图；Ctrl+A 全选
主题：Claude 暖色（陶土橙 #d97757）
"""
import json
import copy
import html
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

import ad_filter as af
import cds_tokens as CDST
from dialog_ui import WorkbenchDialog, NoticeDialog
from cds_tokens import T as CDSTT       # 主题类属性（surface / text / border / 判定色 …）

from PySide6.QtCore import (QAbstractListModel, Property, QEvent, QModelIndex, QObject, QRect, QSize,
                            Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QFontMetrics, QIcon, QImage,
                          QLinearGradient, QPainter, QKeySequence, QPalette, QShortcut,
                           QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QFrame,
                               QHBoxLayout,
                               QLabel, QListView, QListWidgetItem, QMenu, QPushButton,
                               QGraphicsOpacityEffect, QStackedWidget,
                               QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget)

from qfluentwidgets import (CaptionLabel, CheckBox, ComboBox, FluentIcon as FIF, InfoBar,
                            InfoBarPosition, ListWidget, Pivot,
                            PrimaryPushButton, ProgressBar, PushButton,
                            RadioButton, SearchLineEdit, SubtitleLabel, TableWidget, TextEdit,
                            Theme, ToolButton, TransparentPushButton, TransparentToolButton,
                            isDarkTheme, setCustomStyleSheet, setTheme, setThemeColor)
from qframelesswindow import FramelessWindow

VERDICT_TEXT = {"ad": "广告", "suspect": "疑似", "error": "异常"}
CARD_W, CARD_H = 196, 234


def _c(dark_hex, light_hex):
    return QColor(dark_hex if isDarkTheme() else light_hex)


def _verdict_color(verdict: str) -> QColor:
    """判定色 = 语义色（红/琥珀/蓝/绿），与品牌橙彻底分开。"""
    fg, _fill, _edge = CDST.sem(isDarkTheme(), verdict)
    return QColor(fg)


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
        self._failed: set = set()
        self._lock = threading.Lock()
        self._q = queue.Queue()
        self._stopped = False
        self._threads = [threading.Thread(target=self._worker, daemon=True) for _ in range(3)]
        for t in self._threads:
            t.start()

    def get(self, path: str):
        with self._lock:
            if path in self._failed:
                return None
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
        # 缩略图预缩放：高度须与绘制框一致——卡片 opt.rect 上下各内缩 5px（见 paint 的
        # r = opt.rect.adjusted(5,5,-5,-5)），故再减 10；否则 pix 比 img_r 高 10px，
        # 居中后向下溢出 5px 压住文件名（用户报"图片遮挡图片名"）
        pm = pm.scaled(CARD_W - 16, CARD_H - 10 - 86, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        with self._lock:
            self._cache[path] = pm
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)
            self._pending.discard(path)
        self.ready.emit(path)

    def _mark_failed(self, path):
        with self._lock:
            self._pending.discard(path)
            self._failed.add(path)
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
        # CDS 排版：正文 13px / 次级 11px / 等宽数字 11px（原 8pt≈10.7px 偏小，是"糙"的根源）
        self._f_name = QFont("Microsoft YaHei UI")
        self._f_name.setPixelSize(13)
        self._f_name.setWeight(QFont.DemiBold)
        self._f_reason = QFont("Microsoft YaHei UI")
        self._f_reason.setPixelSize(12)
        self._f_badge = QFont("Cascadia Mono", 8)   # 等宽数字（tabular-nums）
        self._f_badge.setPixelSize(11)
        self._f_badge.setBold(True)
        self._fm_name = QFontMetrics(self._f_name)
        self._fm_reason = QFontMetrics(self._f_reason)
        self._fm_badge = QFontMetrics(self._f_badge)
        self._birth: dict = {}   # path -> 出生时刻（入场动画用）
        # 主题由 App._apply_theme 统一推送（paint 里不再自己读 isDarkTheme——
        # 曾出现框架浅色而卡片画成深色的不一致，改为单一来源）
        self.dark = True

    def note_birth(self, paths):
        now = time.time()
        for x in paths:
            self._birth[x] = now
        self._prune_birth()

    def _prune_birth(self):
        now = time.time()
        if len(self._birth) > 512:
            self._birth = {k: v for k, v in self._birth.items() if now - v < 0.3}

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
        # —— 入场动画（CDS file-thumbnail-grow-in）：250ms scale 0.85→1 + 渐显 ——
        born = self._birth.get(it["path"])
        if born is not None:
            age = time.time() - born
            if age < 0:
                p.restore()
                return
            if age < 0.25:
                k = age / 0.25
                p.setOpacity(k)
                s = 0.85 + 0.15 * k
                c = opt.rect.center()
                p.translate(c.x(), c.y())
                p.scale(s, s)
                p.translate(-c.x(), -c.y())
        # 主题：**直接读主窗口当前的 theme 字段**（toggle_theme/__init__ 里与界面 QSS
        # 同一次赋值，天然同源；不经过任何缓存推送，杜绝"界面浅色而卡片深色"）。
        try:
            _win = self.parent().window() if self.parent() else None
            _t = getattr(_win, "theme", None)
            dark = (_t == "dark") if _t in ("dark", "light") else self.dark
        except Exception:
            dark = self.dark
        r = QRect(opt.rect).adjusted(5, 5, -5, -5)
        from PySide6.QtWidgets import QStyle
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        hovered = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        # hover：抬升 -1.5px（CDS CARD_HOVER_LIFT）+ 下缘投影
        # 原实现三层黑色圆角矩形**覆盖整个卡片区域**，靠不透明卡面盖住；一旦卡片
        # 半透明（选中态 clay_fill 6%）黑底就透出来——用户报"选中还是黑色"的根因。
        # 现在投影只画在卡片下缘之外的窄条，永不进入卡面。
        if hovered:
            for dy, al in ((7, 26 if dark else 16), (4, 34 if dark else 22)):
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, al))
                p.drawRoundedRect(QRect(r.left() + 4, r.bottom() - 3 + dy,
                                        r.width() - 8, 9), 6, 6)
            p.translate(0, CDST.CARD_HOVER_LIFT)
        # 卡面：**必须先铺不透明底**（选中/非选中相同），选中再叠一层淡 clay wash——
        # 这样任何下层内容（投影/视口底色）都不会从半透明填充里透出来
        p.setPen(Qt.NoPen)
        p.setBrush(_qc(CDST.c(dark, CDSTT.surface2)))
        p.drawRoundedRect(r, CDST.R_CARD, CDST.R_CARD)
        if selected:
            p.setBrush(_qc(CDST.c(dark, CDSTT.clay_fill)))
            p.drawRoundedRect(r, CDST.R_CARD, CDST.R_CARD)
        # 单闭合边框原则：只有卡片这一层闭合框
        p.setBrush(Qt.NoBrush)
        if selected:
            p.setPen(QPen(_qc(CDST.c(dark, CDSTT.border_clay)), 1.5))
        elif hovered:
            p.setPen(QPen(_qc(CDST.c(dark, CDSTT.border_strong)), 1))
        else:
            p.setPen(QPen(_qc(CDST.c(dark, CDSTT.border)), 1))
        p.drawRoundedRect(r, CDST.R_CARD, CDST.R_CARD)
        if selected:
            # 双环聚焦协议：外环 clay + 内环 surface1
            p.setPen(QPen(_qc(CDST.CLAY), 1.5))
            p.drawRoundedRect(r.adjusted(-2, -2, 2, 2), CDST.R_CARD + 2, CDST.R_CARD + 2)
            p.setPen(QPen(_qc(CDST.c(dark, CDSTT.surface1)), 1))
            p.drawRoundedRect(r.adjusted(2, 2, -2, -2), CDST.R_CARD - 2, CDST.R_CARD - 2)
        img_r = QRect(r.left() + 8, r.top() + 8, r.width() - 16, r.height() - 86)
        pix = self.thumbs.get(it["path"])
        if pix is not None and not pix.isNull():
            # 裁剪到图片区：任何尺寸误差都不允许盖到下方文件名行
            p.save()
            p.setClipRect(img_r)
            p.drawPixmap(img_r.left() + (img_r.width() - pix.width()) // 2,
                         img_r.top() + (img_r.height() - pix.height()) // 2, pix)
            p.restore()
        else:
            # —— 骨架屏 + 105° 倾角扫光（CDS cds-skeleton-sweep：2.0s 线性循环）——
            p.setPen(Qt.NoPen)
            p.setBrush(_qc(CDST.c(dark, CDSTT.surface3)))
            p.drawRoundedRect(img_r, CDST.R_LG, CDST.R_LG)
            t = (time.time() % 2.0) / 2.0
            w = img_r.width()
            gx = img_r.left() - w + t * 2.0 * w
            g = QLinearGradient(gx, img_r.top(), gx + w * 0.35, img_r.bottom())
            sheen = QColor(217, 119, 87, 22) if dark else QColor(198, 97, 63, 14)
            g.setColorAt(0.0, QColor(0, 0, 0, 0))
            g.setColorAt(0.35, QColor(0, 0, 0, 0))
            g.setColorAt(0.50, sheen)
            g.setColorAt(0.65, QColor(0, 0, 0, 0))
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(img_r, QBrush(g))
        # 判定语义色（标签行用：淡语义底 + 饱和语义字）
        vrole = CDST.ROLE_BY_VERDICT.get(it["verdict"], "info")
        vc, _vfill, _vedge = CDST.sem(dark, vrole)
        # 文件名（双向截断：扩展名永远保留）——单独一行
        name = self._elide_bidir(Path(it["path"]).name, self._fm_name, r.width() - 20)
        p.setPen(_qc(CDST.c(dark, CDSTT.clay)) if selected
                 else _qc(CDST.c(dark, CDSTT.text)))
        p.setFont(self._f_name)
        p.drawText(QRect(r.left() + 10, r.bottom() - 74, r.width() - 20, 16),
                   Qt.AlignLeft | Qt.AlignVCenter, name)
        # 标签行：左判定右格式，同一行、同款底座。
        # 判定信息重设计：语义色只落在 5px 圆点上，文字走中性灰——
        # 浅色下不出现任何深色重块，一眼扫色点即知类型（红=广告/琥珀=疑似/蓝=异常）
        vtext = VERDICT_TEXT.get(it["verdict"], it["verdict"])
        tw = self._fm_badge.horizontalAdvance(vtext)
        vbw = tw + 24                       # 圆点 + 间距 + 文字 + 左右 padding
        vbr = QRect(r.left() + 10, r.bottom() - 55, vbw, 15)
        p.setPen(Qt.NoPen)
        p.setBrush(_qc(CDST.c(dark, CDSTT.muted)))
        p.drawRoundedRect(vbr, CDST.R_SM, CDST.R_SM)
        p.setBrush(QColor(vc))
        p.drawEllipse(QRect(vbr.left() + 6, vbr.top() + 5, 5, 5))
        p.setPen(_qc(CDST.c(dark, CDSTT.text_2)))
        p.setFont(self._f_badge)
        p.drawText(QRect(vbr.left() + 15, vbr.top(), tw + 4, vbr.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, vtext)
        ext = Path(it["path"]).suffix.lstrip(".").upper()
        if ext:
            btxt = self._fm_badge.elidedText(ext, Qt.ElideRight, 34)
            bw = self._fm_badge.horizontalAdvance(btxt) + 10
            br = QRect(r.right() - bw - 8, r.bottom() - 55, bw, 15)
            p.setPen(Qt.NoPen)
            p.setBrush(_qc(CDST.c(dark, CDSTT.muted)))
            p.drawRoundedRect(br, CDST.R_SM, CDST.R_SM)
            p.setPen(_qc(CDST.c(dark, CDSTT.text_3)))
            p.setFont(self._f_badge)
            p.drawText(br, Qt.AlignCenter, btxt)
        # 命中原因：中性灰（不再整屏红字；语义色只保留在判定标签上）
        reason = self._fm_reason.elidedText("; ".join(h.get("label", "") for h in it["reasons"]),
                                            Qt.ElideRight, r.width() - 20)
        p.setPen(_qc(CDST.c(dark, CDSTT.text_2)))
        p.setFont(self._f_reason)
        p.drawText(QRect(r.left() + 10, r.bottom() - 37, r.width() - 20, 13),
                   Qt.AlignLeft | Qt.AlignVCenter, reason)
        # 来源文件夹行（📁 根文件夹名/子目录）
        folder = it.get("_folder") or ""
        if folder:
            ftext = self._fm_reason.elidedText(folder, Qt.ElideMiddle, r.width() - 34)
            p.setPen(_qc(CDST.c(dark, CDSTT.text_2)))
            p.setFont(self._f_reason)
            # 自绘文件夹小图标（不用 emoji——部分字体缺字形会显示成方块）
            p.setPen(Qt.NoPen)
            p.setBrush(_qc(CDST.c(dark, CDSTT.text_3)))
            p.drawRoundedRect(QRect(r.left() + 10, r.bottom() - 18, 11, 8), 2, 2)
            p.drawRoundedRect(QRect(r.left() + 10, r.bottom() - 21, 5, 3), 1, 1)
            p.setPen(_qc(CDST.c(dark, CDSTT.text_2)))
            p.drawText(QRect(r.left() + 25, r.bottom() - 21, r.width() - 35, 14),
                       Qt.AlignLeft | Qt.AlignVCenter, ftext)
        cr = QRect(r.left() + 8, r.top() + 8, 22, 22)
        checked = it["path"] in self.model.checked
        if checked:
            p.setBrush(_qc(CDST.c(dark, CDSTT.clay)))
            p.setPen(Qt.NoPen)
            p.drawEllipse(cr)
            p.setPen(QPen(_qc(CDST.c(dark, CDSTT.text_on_clay)), 2))
            p.drawLine(cr.left() + 6, cr.center().y(), cr.center().x() - 1, cr.bottom() - 7)
            p.drawLine(cr.center().x() - 1, cr.bottom() - 7, cr.right() - 5, cr.top() + 7)
        else:
            p.setBrush(_qc(CDST.c(dark, CDSTT.surface2)))
            p.setPen(QPen(_qc(CDST.c(dark, CDSTT.text_2)), 1.2))
            p.drawEllipse(cr)
        gi = self.model.sim_map.get(it["path"])
        if gi is not None:
            # 相似组用琥珀（warn）——与判定的 clay/红区分开，形成色彩层次
            p.setPen(QPen(_qc(CDST.c(dark, CDSTT.warn))))
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
        self.setFixedHeight(50)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 14, 6)
        lay.setSpacing(8)
        self._chips: dict[str, QLabel] = {}
        from PySide6.QtWidgets import QScrollArea
        self._scroll = QScrollArea(self)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet('QScrollArea { background: transparent; border: none; }')
        self._scroll.viewport().setStyleSheet('background: transparent;')
        self._chip_host = QWidget(self._scroll)
        self._box = QHBoxLayout(self._chip_host)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setAlignment(Qt.AlignLeft)
        self._box.setSpacing(6)
        self._scroll.setWidget(self._chip_host)
        lay.addWidget(self._scroll, 1)
        self.wave = WaveDots(self)
        self.wave.hide()
        self.lbl_current = QLabel("", self)
        self.lbl_current.setObjectName("currentFile")
        self.lbl_current.setFixedWidth(220)
        self.lbl_current.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.wave)
        lay.addWidget(self.lbl_current)
        self.progress = ProgressBar(self)
        self.progress.setFixedWidth(90)
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
            if not self.wave.isVisible():
                self.wave.show()
                self.wave.start()
            root = self.app._current_root or ""
            root_name = Path(root).name if root else ""
            fname = Path(cur_path).name
            text = f"正在扫描  {root_name}/{fname}" if root_name else f"正在扫描  {fname}"
            fm = QFontMetrics(self.lbl_current.font())
            self.lbl_current.setText(fm.elidedText(text, Qt.ElideMiddle, self.lbl_current.width() - 8))
            self.lbl_current.setToolTip(str(cur_path))
        else:
            self.wave.stop()
            self.wave.hide()
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
        # 总进度（扫描中才显示，完成后整组隐藏——100% 常驻是视觉噪音）
        total_all = self.app.total_files
        scanning = self.app.scan_thread is not None and self.app.scan_thread.is_alive()
        show_prog = bool(scanning and total_all > 0 and any_total)
        self.progress.setVisible(show_prog)
        self.lbl_pct.setVisible(show_prog)
        if show_prog:
            d_all = sum(done.get(k, 0) for k in totals)
            # 未归属根的也在跑：用引擎广播的进度更准（见 _poll_queue）
            self.progress.setValue(int(getattr(self.app, "_last_progress", 0) * 100 / max(1, total_all)))
            self.lbl_pct.setText(f"{int(self.progress.value())}%")
        elif not any_total:
            self.progress.setValue(0)
            self.lbl_pct.setText("")


# ================================================================ 标题栏（Windows 风格）
class TitleBar(QWidget):
    """自绘标题栏：左留白可拖拽、品牌正中、右上最小化/最大化/关闭（Windows 习惯）。"""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self._win = win
        self.setObjectName("titleBar")
        self.setFixedHeight(38)
        self.setAttribute(Qt.WA_StyledBackground, True)
        # 右侧按钮：Windows 顺序 最小化/最大化/关闭，46x30 成组紧贴右边缘
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        # 品牌不走布局：布局只能左对齐，"居中"必须按窗口真实宽度算（见 _place_brand）
        self.logo = QLabel(self)                    # AD 应用图标（标题栏与系统图标同步）
        self.logo.setObjectName("titleLogo")
        self.logo.setFixedSize(16, 16)
        self.logo.setPixmap(app_icon_pixmap(16))
        self.logo.setStyleSheet("background: transparent;")
        self.brand = QLabel("", self)
        self.brand.setObjectName("titleBrand")
        lay.addStretch(1)
        self.btn_min = self._mk_btn("—", "最小化")
        self.btn_min.clicked.connect(win.showMinimized)
        self.btn_max = self._mk_btn("☐", "最大化 / 还原")
        self.btn_max.clicked.connect(self._toggle_max)
        self.btn_close = self._mk_btn("✕", "关闭")
        self.btn_close.setObjectName("titleClose")
        self.btn_close.clicked.connect(win.close)
        for b in (self.btn_min, self.btn_max, self.btn_close):
            lay.addWidget(b)
    def _mk_btn(self, text, tip):
        from PySide6.QtWidgets import QPushButton
        b = QPushButton(text, self)
        b.setObjectName("titleBtn")
        b.setFixedSize(46, 30)
        b.setToolTip(tip)
        b.setCursor(Qt.ArrowCursor)
        b.setFocusPolicy(Qt.NoFocus)
        return b

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_brand()

    def _place_brand(self):
        """品牌按窗口真实宽度绝对居中（布局的左对齐做不到真正居中）。"""
        if not hasattr(self, "brand"):
            return
        self.brand.adjustSize()
        lw, bw = self.logo.width(), self.brand.width()
        gap = 6
        # 品牌文字严格居中，logo 挂在其左侧（整体居中会让文字被 logo 挤偏右）
        bx = max(12 + lw + gap, (self.width() - bw) // 2)
        ly = (38 - self.logo.height()) // 2
        self.brand.setGeometry(bx, (38 - self.brand.height()) // 2, bw, self.brand.height())
        self.logo.setGeometry(bx - lw - gap, ly, lw, self.logo.height())

    def _toggle_max(self):
        if self._win.isMaximized():
            self._win.showNormal()
            self.btn_max.setText("☐")
        else:
            self._win.showMaximized()
            self.btn_max.setText("❐")

    def mousePressEvent(self, e):
        # 标题栏拖动（用户报"窗口固定死不能移动"：自绘标题栏没实现拖动逻辑，
        # qframelesswindow 不会替我们拖）。最大化时忽略——与双击还原配合。
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if (e.buttons() & Qt.LeftButton and getattr(self, "_drag_pos", None) is not None
                and not self._win.isMaximized()):
            self._win.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        self._toggle_max()

    def apply_theme(self, dark, clay, base):
        bg = "#0b0b0b" if dark else "#f9f9f7"
        border = "rgba(217,119,87,0.16)" if dark else "rgba(198,97,63,0.22)"
        self.setStyleSheet(
            f"#titleBar {{ background: {bg}; border-bottom: 1px solid {border}; }}"
            f"#titleBtn {{ background: transparent; border: none; color: {'#a3a3a3' if dark else '#525252'};"
            f" font-size: 13px; font-family: 'Segoe UI', 'Microsoft YaHei UI'; }}"
            f"#titleBtn:hover {{ background: {'rgba(255,255,255,0.08)' if dark else 'rgba(0,0,0,0.06)'}; }}"
            f"#titleClose:hover {{ background: #c42b1c; color: #ffffff; }}")
        self.brand.setStyleSheet(
            'font-family: "Microsoft YaHei UI", "Segoe UI";'
            f"font-size: 13px; font-weight: 600; letter-spacing: 0.3px; color: {base};"
            "background: transparent;")
        self.brand.setText(f'<span style="color:{clay};">广告</span>图过滤')
        self._place_brand()
        # logo 已是 AD 应用图标（构造时 setPixmap），主题无关无需重刷


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
        self._enter = 1.0   # 入场动画进度（showEvent 里从 0 渐到 1）
        self._load(path)
        # 底部右侧：图内直接处置按钮（A 标广告 / K 保留）
        self.btn_ad = QPushButton("标记广告 (A)", self)
        self.btn_ad.setCursor(Qt.PointingHandCursor)
        self.btn_ad.setStyleSheet(
            "QPushButton { background: rgba(198,97,63,0.92); color: #fff; border: none;"
            " border-radius: 14px; padding: 6px 16px; font-size: 12px; }"
            "QPushButton:hover { background: rgba(217,119,87,1.0); }")
        self.btn_ad.clicked.connect(lambda: self._mark(True))
        self.btn_keep = QPushButton("保留 (K)", self)
        self.btn_keep.setCursor(Qt.PointingHandCursor)
        self.btn_keep.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.14); color: #f5f5f5; border: none;"
            " border-radius: 14px; padding: 6px 16px; font-size: 12px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.24); }")
        self.btn_keep.clicked.connect(lambda: self._mark(False))
        self._place_mark_btns()
        # 覆盖主窗口
        if parent is not None:
            self.resize(parent.size())
        self.setFocusPolicy(Qt.StrongFocus)
        self.show()
        self._play_enter()

    def _play_enter(self):
        """入场：backdrop 200ms 淡入 + 内容 250ms scale-in（CDS lightbox 关键帧）。"""
        from PySide6.QtCore import QPropertyAnimation, QEasingCurve
        self._enter = 0.0
        self._enter_anim = QPropertyAnimation()
        self._enter_anim.setPropertyName(b"enter")
        self._enter_anim.setTargetObject(self)
        self._enter_anim.setStartValue(0.0)
        self._enter_anim.setEndValue(1.0)
        self._enter_anim.setDuration(250)
        self._enter_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._enter_anim.valueChanged.connect(lambda _v: self.update())
        self._enter_anim.start(QPropertyAnimation.DeleteWhenStopped)

    def get_enter(self):
        return self._enter

    def set_enter(self, v):
        self._enter = v
        self.update()

    enter = Property(float, get_enter, set_enter)

    def _load(self, path):
        self.path = path
        from PIL import Image as _Im, ImageOps as _IO
        try:
            with _Im.open(path) as im:
                img = im.convert("RGB")
            if max(img.size) > 2400:
                img = _IO.contain(img, (2400, 2400))
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

    def _place_mark_btns(self):
        y = self.height() - 46
        w_ad, w_keep = 108, 84
        self.btn_keep.setGeometry(self.width() - 18 - w_keep, y, w_keep, 32)
        self.btn_ad.setGeometry(self.width() - 18 - w_keep - 10 - w_ad, y, w_ad, 32)
        self.btn_ad.raise_()
        self.btn_keep.raise_()

    def _mark(self, as_ad):
        self.app.mark_path(self.path, as_ad)
        self.update()
        if not as_ad:
            # 保留 = 移出结果列表：自动跳到下一张，没有则关闭
            if not self._nav(1) and not self._nav(-1):
                self.close()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "btn_ad"):
            self._place_mark_btns()

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
        # backdrop（入场前 200ms 淡入：alpha 按 enter*1.25 截断到 1）
        p.fillRect(self.rect(), QColor(11, 11, 11, int(240 * min(1.0, self._enter * 1.25))))
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
        # 内容入场（250ms scale 0.92→1 + 渐显，官方 lightbox-content-in）
        es = 0.92 + 0.08 * self._enter
        p.setOpacity(max(0.0, min(1.0, self._enter * 1.2)))
        p.scale(self.scale * es, self.scale * es)
        p.drawPixmap(int(-self._fit_w / 2), int(-self._fit_h / 2),
                     int(self._fit_w), int(self._fit_h), self._pix)
        p.restore()
        p.setOpacity(1.0)
        self._draw_chrome(p)

    def _draw_chrome(self, p):
        """顶部文件名 + 右上关闭；底部判定信息条。"""
        it = self._it()
        name = Path(self.path).name
        p.setPen(Qt.NoPen)
        p.setBrush(QColor('#20201e'))
        p.drawRect(QRect(0, 0, self.width(), 58))
        p.drawRect(QRect(0, self.height() - 62, self.width(), 62))
        # 顶部左：文件名
        p.setPen(QColor("#e8e6e0"))
        p.setFont(QFont("Microsoft YaHei UI", 10))
        p.drawText(QRect(20, 9, self.width() - 140, 24), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(name, Qt.ElideMiddle, self.width() - 140))
        p.setPen(QColor('#aaa59c'))
        p.setFont(QFont('Microsoft YaHei UI', 8))
        p.drawText(QRect(20, 32, self.width() - 140, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   f'大图审查   ·   缩放 {self.scale:.0%}   ·   ← → 切换   ·   Esc 关闭')
        # 右上：关闭
        cr = QRect(self.width() - 46, 14, 30, 30)
        p.setPen(QPen(QColor(255, 255, 255, 130), 1.4))
        p.setBrush(QColor(255, 255, 255, 20))
        p.drawRoundedRect(cr, 7, 7)
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
            txt = fm.elidedText(line, Qt.ElideMiddle, max(80, self.width() - 340))
            tw = min(max(80, self.width() - 320), fm.horizontalAdvance(txt) + 32)
            bx = 18
            pill = QRect(bx, self.height() - bar_h - 12, tw, 32)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(11, 11, 11, 200))
            p.drawRoundedRect(pill, 16, 16)
            p.setPen(QColor("#f5f5f5"))
            p.setFont(QFont("Microsoft YaHei UI", 9))
            p.drawText(pill, Qt.AlignCenter, txt)
        # 左右箭头（‹ 上一张 / › 下一张）：尖点朝外——原实现尖点朝内，视觉上左右反了
        p.setPen(QPen(QColor(255, 255, 255, 140), 2))
        p.setBrush(Qt.NoBrush)
        mid_y = self.height() // 2
        for x, d in ((24, -1), (self.width() - 24, 1)):
            tail = x + (8 if d < 0 else -8)   # 左侧尾在右、右侧尾在左
            p.drawLine(tail, mid_y - 9, x, mid_y)
            p.drawLine(tail, mid_y + 9, x, mid_y)

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
        x = pos.x()
        # 箭头热区（左 48px / 右 48px）里连续点击是"快速翻页"，**绝不能当双击缩放**——
        # 翻页已由 mouseReleaseEvent 处理，这里直接吞掉（用户报"连点下一张会放大图"）
        if x < 48 or x > self.width() - 48:
            return
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
            return False
        # 切换图片时轻量重放入场（80ms 快速淡入）
        idx = next((i for i, x in enumerate(items) if x["path"] == self.path), None)
        if idx is None:
            # 当前图已被移出列表（如刚点"保留"）：从锚点附近继续
            return self._nav_nearest()
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(items):
            # 已在首/末张：**不重载、不重放入场动画**（原来 clamp 后会重新
            # _load 同一张 + 播入场 → 用户报"最后一张后画面跳动/重复加载"）
            return False
        idx = new_idx
        self.app.grid.setCurrentIndex(self.app.model.index(idx, 0))
        self._load(items[idx]["path"])
        self._play_enter()
        self._enter_anim.setDuration(80)
        return True

    def _nav_nearest(self):
        items = self.app.model.items
        if not items:
            return False
        idx = self.app.grid.currentIndex().row()
        idx = max(0, min(len(items) - 1, idx))
        self.app.grid.setCurrentIndex(self.app.model.index(idx, 0))
        self._load(items[idx]["path"])
        self._play_enter()
        self._enter_anim.setDuration(80)
        return True

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Escape:
            self.close()
        elif k == Qt.Key_Left:
            self._nav(-1)
        elif k == Qt.Key_Right:
            self._nav(1)
        elif k == Qt.Key_A:
            self._mark(True)
        elif k == Qt.Key_K:
            self._mark(False)


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
            # 浅色：滑块纯白 + 6% 黑轨道对比不足（用户反馈"没考虑白色主题"），
            # 轨道与滑块描边同步加深，选中态一眼可辨
            self.setStyleSheet("""
                Segmented { background: rgba(0,0,0,0.10); border-radius: 15px; }
                Segmented QPushButton { background: transparent; border: none; color: #6b6863;
                    font-size: 12px; padding: 0 10px; }
                Segmented QPushButton[active="true"] { color: #151515; font-weight: 600; }
                #segIndicator { background: #ffffff; border-radius: 13px;
                    border: 1px solid rgba(0,0,0,0.14); }
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
        w = (self.width() - 4) / n
        x = 2 + self._index * w
        from PySide6.QtCore import QPropertyAnimation, QRect, QEasingCurve
        # 停掉进行中的动画。**不能**用 DeleteWhenStopped：它会在动画结束时直接回收
        # C++ 对象，Python 侧 self._slide 变成僵尸引用，下一次点击 stop() 立即
        # RuntimeError——用户报"切到回收站后点不了其他两种方式"的根因。
        slide = getattr(self, "_slide", None)
        if slide is not None:
            try:
                slide.stop()
            except RuntimeError:
                pass
            self._slide = None
        if animate:
            self._slide = QPropertyAnimation(self._indicator, b"geometry")
            self._slide.setDuration(300)   # CDS ease-snap 档
            self._slide.setEasingCurve(QEasingCurve.OutCubic)
            self._slide.setStartValue(self._indicator.geometry())
            self._slide.setEndValue(QRect(int(x), 2, int(w), self.height() - 4))
            # 结束只清引用不删对象（删对象正是上面的僵尸引用陷阱）
            self._slide.finished.connect(lambda: setattr(self, "_slide", None))
            self._slide.start()
        else:
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


class WaveDots(QWidget):
    """思考三点波浪（CDS timeline-quiet-dots）：1.9s 周期、3px 点、相位差 0.15s、
    轨迹 y +2 → -2.5 → 0、透明度按关键帧分段。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 14)
        self._t0 = time.time()
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(lambda: self.update())

    def start(self):
        self._t0 = time.time()
        self._timer.start()
        self.update()

    def stop(self):
        self._timer.stop()
        self.update()

    @staticmethod
    def _interp(ph):
        """官方关键帧：0-8% op0/y+2；22% op1/y-2.5；36-70% op1/y0；90-100% op0/y0。"""
        if ph < 0.08:
            return 0.0, 2.0
        if ph < 0.22:
            k = (ph - 0.08) / 0.14
            return k, 2.0 - 4.5 * k
        if ph < 0.36:
            k = (ph - 0.22) / 0.14
            return 1.0, -2.5 + 2.5 * k
        if ph < 0.70:
            return 1.0, 0.0
        if ph < 0.90:
            return 1.0 - (ph - 0.70) / 0.20, 0.0
        return 0.0, 0.0

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        c = QColor("#d97757")
        now = time.time()
        for i in range(3):
            ph = ((now - self._t0) % 1.9) / 1.9
            ph_i = (ph - i * (0.15 / 1.9)) % 1.0
            op, dy = self._interp(ph_i)
            col = QColor(c)
            col.setAlpha(int(255 * max(0.0, min(1.0, op))))
            p.setBrush(col)
            cy = 7 + dy
            p.drawEllipse(4 + i * 8, int(cy - 1.5), 3, 3)


class ScrollFade(QWidget):
    """滚动渐隐遮罩（CDS 7 档非线性 alpha 阶梯，贴合人眼幂律）。"""

    STOPS = [(0.15, 0.051), (0.30, 0.149), (0.45, 0.302),
             (0.60, 0.502), (0.75, 0.702), (0.87, 0.851), (1.00, 1.000)]

    def __init__(self, edge, parent=None):
        super().__init__(parent)
        self._edge = edge  # "top" | "bottom"
        self.setFixedHeight(28)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, _e):
        base = QColor(CDST.c(isDarkTheme(), CDSTT.surface1))
        p = QPainter(self)
        p.setPen(Qt.NoPen)
        if self._edge == "top":
            g = QLinearGradient(0, self.height(), 0, 0)  # 底(透明) -> 顶(实)
        else:
            g = QLinearGradient(0, 0, 0, self.height())
        for pos, a in self.STOPS:
            c = QColor(base)
            c.setAlphaF(a)
            g.setColorAt(1.0 - pos, c)
        p.fillRect(self.rect(), QBrush(g))


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
        d = isDarkTheme()
        # 语义色：running=橙(品牌动作) done=绿 error=红 warn=琥珀 ready=灰
        if self._state == "error":
            c, r = _qc(CDST.c(d, CDSTT.danger)), 3.0
        elif self._state == "done":
            c, r = _qc(CDST.c(d, CDSTT.ok)), 3.0
        elif self._state == "warn":
            c, r = _qc(CDST.c(d, CDSTT.warn)), 3.0
        elif self._state == "running":
            c = _qc(CDST.c(d, CDSTT.clay))
            # clay 呼吸：scale 0.86 → 1.0、亮度 0.96 → 1.06
            ph = (math.sin(self._t * 2 * math.pi) + 1) / 2
            r = 3.0 * (0.86 + 0.14 * ph)
            h, s, v, a = c.getHsvF()
            c = QColor.fromHsvF(h, s, min(1.0, v * (0.96 + 0.10 * ph)), a)
        else:
            c, r = _qc(CDST.c(d, CDSTT.text_3)), 3.0
        p.setBrush(c)
        p.drawEllipse(int(5 - r), int(5 - r), int(r * 2), int(r * 2))


# ================================================================ 主窗口
class App(FramelessWindow):
    _sig_preview = Signal(object)
    _sig_preview_err = Signal(object)

    def __init__(self, cfg):
        from workbench_ui import configure_fonts
        configure_fonts()
        super().__init__()
        self.setObjectName("mainWindow")
        self._command_shortcut = QShortcut(QKeySequence('Ctrl+K'), self)
        self._command_shortcut.activated.connect(self.open_palette)
        self.cfg = cfg
        self.theme = cfg.get("ui_theme", "light")
        setThemeColor(CDST.BRAND)
        setTheme(Theme.DARK if self.theme == "dark" else Theme.LIGHT)
        scr = self.screen().availableGeometry() if self.screen() else None
        self.resize(min(1500, scr.width() - 80) if scr else 1460,
                    min(940, scr.height() - 100) if scr else 920)
        self.setMinimumSize(1080, 680)
        if scr is not None:
            # 窗口居中（默认 pop 位置可能出现在屏幕右下，遮挡正在用的内容）
            self.move(scr.x() + (scr.width() - self.width()) // 2,
                      scr.y() + (scr.height() - self.height()) // 2)
        self._titlebar = TitleBar(self)
        self.setTitleBar(self._titlebar)
        self.setAcceptDrops(True)

        self.items: list = []
        self.checked: set = set()
        self.similar_groups: list = []
        self._sim_map: dict = {}
        self._disposing = False
        self._quarantine_override = None  # 测试注入临时隔离区，生产默认策略不变
        self._scan_generation = 0
        self._file_busy = False
        self._cached_count = 0
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
        self._undo_batch = None   # (quarantine_root, [src_paths]) 上一次隔离处置

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
        # 骨架扫光驱动：仅存在 pending 缩略图时跑（空闲零开销）
        self._sweep_timer = QTimer(self)
        self._sweep_timer.setInterval(50)
        self._sweep_timer.timeout.connect(lambda: self.grid.viewport().update())
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_queue)
        self._timer.start(120)

    # ---------- 主题 ----------
    def _apply_theme(self):
        """令牌驱动的主题 QSS。色彩分层原则：
        品牌橙只出现在「主动作 / 当前项 / 进度」；判定与状态一律走语义色；其余全用暖灰阶梯。"""
        d = self.theme == 'dark'
        TT = CDSTT
        root_bg = CDST.c(d, TT.root_bg)
        s0 = CDST.c(d, TT.surface0)
        s1 = CDST.c(d, TT.surface1)
        s2 = CDST.c(d, TT.surface2)
        s3 = CDST.c(d, TT.surface3)
        bdr = CDST.c(d, TT.border)
        bdr_soft = CDST.c(d, TT.border_soft)
        txt = CDST.c(d, TT.text)
        txt2 = CDST.c(d, TT.text_2)
        txt3 = CDST.c(d, TT.text_3)
        hov = CDST.c(d, TT.hover)
        clay = CDST.c(d, TT.clay)
        clay_fill = CDST.c(d, TT.clay_fill)
        clay_edge = CDST.c(d, TT.border_clay)
        ok_fg, ok_fill, ok_edge = CDST.sem(d, "ok")
        gray_fg, gray_fill, gray_edge = CDST.sem(d, "gray")
        # 滚动条走暖灰（不再用品牌橙满屏刷存在感）
        sb = "rgba(20,20,20,0.22)" if not d else "rgba(255,255,255,0.20)"
        sb_h = "rgba(20,20,20,0.38)" if not d else "rgba(255,255,255,0.34)"

        self.setStyleSheet(f"""
            #mainWindow {{ background: {root_bg}; }}
            /* 整窗统一底色（Claude 原则：靠留白与发丝分层，不做盒子套盒子）。
               顶/底/筛选/文件夹条全部透出根底色，只留一条极淡发丝。 */
            #topBar {{ background: {s0}; border-bottom: none; }}
            #bottomBar {{ background: {s0}; border-top: 1px solid {bdr}; }}
            #footerBar {{ background: {root_bg}; }}
            #filterBar {{ background: transparent; border: none; }}
            #detailPanel {{ background: {s0}; border-left: 1px solid {bdr}; }}
            #sectionTitle {{ color: {txt}; font-size: 20px; font-weight: 600; }}
            #detailFilename {{ color: {txt}; font-size: 13px; font-weight: 600; }}
            #detailPath {{ color: {txt2}; font-size: 11px; }}
            #workbenchSplitter::handle {{ background: {bdr}; }}
            #grid {{ background: transparent; border: none; }}
            #detailHead {{ background: transparent; border-bottom: 1px solid {bdr_soft}; }}
            #gridWrap {{ background: transparent; }}
            #folderBar {{ background: {s0}; border-bottom: 1px solid {bdr}; }}
            /* 预览画布：图片需要边界，保留 surface2 + 发丝 + 圆角 12 */
            #previewCanvas {{ background: {s2}; border: 1px solid {bdr};
                border-radius: {CDST.R_CARD}px; color: {txt3}; }}
            #vsep {{ background: {bdr_soft}; }}
            /* 文件夹 chip：淡底无边框（hover 才浮出），完成=绿字 */
            #folderChip {{ background: {hov}; border: 1px solid transparent; border-radius: {CDST.R_MD}px; }}
            #folderChip QLabel {{ background: transparent; color: {txt2}; font-size: 12px; }}
            #folderChip QToolButton {{
                border: none; background: transparent; color: {txt3};
                font-size: 11px; border-radius: {CDST.R_LG}px;
            }}
            #folderChip QToolButton:hover {{ color: {clay}; background: {clay_fill}; }}
            #folderChip[done="true"] {{ background: {ok_fill}; border-color: transparent; }}
            #folderChip[done="true"] QLabel {{ color: {ok_fg}; }}
            #folderChip[active="true"] {{ border-color: {clay_edge}; background: {clay_fill}; }}
            #folderChip[active="true"] QLabel {{ color: {clay}; }}
            #folderHint {{ color: {txt3}; font-size: 12px; }}
            #currentFile {{ color: {txt2}; font-size: 12px; }}
            QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {sb}; border-radius: 5px; min-height: 40px; }}
            QScrollBar::handle:vertical:hover {{ background: {sb_h}; }}
            QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
            QScrollBar::handle:horizontal {{ background: {sb}; border-radius: 5px; min-width: 40px; }}
            QScrollBar::handle:horizontal:hover {{ background: {sb_h}; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
            QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        """)
        self._apply_brand_style()
        self._apply_info_bg()
        if getattr(self, "delegate", None) is not None:
            self.delegate.dark = d          # 卡片绘制主题：与界面同源推送
        if hasattr(self, "log"):
            self._apply_log_bg()
        if hasattr(self, "seg_disposal"):
            self.seg_disposal.refresh_theme()   # 分段胶囊轨道/滑块/文字随主题重建
        if hasattr(self, "lm"):
            # 侧栏/导航项自绘样式：主题切换必须重刷（缺失时侧栏停留在旧主题色，
            # 用户报"左侧导航区域不随主题换色"的根因）
            self.lm.refresh_theme(d)
        if hasattr(self, "_empty"):
            # 空状态图标底同理（用户报浅色下残留黑色方块 = 深色 muted 底未重刷）
            self._empty.refresh_theme(d)
        self._apply_selection_palette()   # 框选色随主题（品牌橙半透明）
        if hasattr(self, "status_pill"):
            # 控件自定义样式按「当前主题」生成（setCustomStyleSheet 双套参数在部分
            # 组件上不重放——见踩坑记录），所以主题切换必须整体重刷一次
            self._apply_styles()

    def _apply_brand_style(self):
        # 品牌在 TitleBar 内置（双色字 + 主题同步）
        clay = CDST.c(isDarkTheme(), CDSTT.clay)
        base = CDST.c(isDarkTheme(), CDSTT.text)
        self._titlebar.apply_theme(isDarkTheme(), clay, base)

    def _apply_log_bg(self):
        from qfluentwidgets import setCustomStyleSheet
        d = isDarkTheme()
        qss = (f"TextEdit {{ background: {CDST.c(d, CDSTT.surface0)};"
               f" border-top: 1px solid {CDST.c(d, CDSTT.border)};"
               f" color: {CDST.c(d, CDSTT.text_2)}; }}")
        setCustomStyleSheet(self.log, qss, qss)

    def _apply_info_bg(self):
        d = isDarkTheme()
        qss = (f"TextEdit {{ background: {CDST.c(d, CDSTT.surface2)};"
               f" border: 1px solid {CDST.c(d, CDSTT.border)};"
               f" border-radius: {CDST.R_PANEL}px; color: {CDST.c(d, CDSTT.text_2)}; }}")
        setCustomStyleSheet(self.info, qss, qss)

    # ---------- 工具条零件 ----------
    @staticmethod
    def _vsep(parent, h=16):
        """1px 发丝竖分隔（CDS：容器内部只用单向发丝，不用双向描边）"""
        f = QFrame(parent)
        f.setObjectName("vsep")
        f.setFrameShape(QFrame.NoFrame)
        f.setFixedSize(1, h)
        return f

    # 状态语义：文本关键词 → 语义角色（决定胶囊底色 / 文字色 / 指示灯色）
    _STATUS_RULES = (
        ("ok", ("扫描完成", "处置完成", "恢复完成", "已选中", "已导出")),
        ("warn", ("暂停", "已取消", "取消中", "未发现")),
        ("danger", ("失败", "出错", "错误", "异常")),
        ("clay", ("正在", "收集", "扫描中", "预处理", "继续", "命中", "处置中", "/")),
    )
    _DOT_BY_ROLE = {"ok": "done", "warn": "warn", "danger": "error",
                    "clay": "running", "gray": "ready"}

    def set_status(self, text, role=None):
        """设置状态文本并按语义着色（替代裸 status.setText）。"""
        self.status.setText(text)
        if role is None:
            role = "gray"
            for r, keys in self._STATUS_RULES:
                if any(k in text for k in keys):
                    role = r
                    break
        self._status_role = role
        self._paint_status_pill()
        return self

    def _paint_status_pill(self):
        d = isDarkTheme()
        role = getattr(self, "_status_role", "gray")
        fg, fill, edge = CDST.sem(d, role)
        # 淡底无边框胶囊（Claude 风格）；13px 圆角——999px 在 Qt QSS 里是直角
        self.status_pill.setStyleSheet(
            f"#statusPill {{ background: {fill}; border-radius: {CDST.R_PILL}px; }}")
        self.status.setStyleSheet(f"color: {fg}; background: transparent;")
        self.dot.set_state(self._DOT_BY_ROLE.get(role, "ready"))

    def toggle_theme(self):
        # —— 主题切换 cross-fade：先抓旧画面，切换后旧画面渐隐、新主题透出 ——
        old_img = QImage(self.width(), self.height(), QImage.Format_ARGB32_Premultiplied)
        old_img.fill(Qt.transparent)
        self.render(old_img)
        old_pix = QPixmap.fromImage(old_img.copy())
        new = Theme.LIGHT if isDarkTheme() else Theme.DARK
        setTheme(new)
        self.theme = "dark" if new == Theme.DARK else "light"
        self.cfg["ui_theme"] = self.theme
        af.save_config(self.cfg)
        self._apply_theme()
        # 卡片徽章/选中环等 delegate 自绘内容：viewport().update() 在某些状态下
        # 不会重绘全部可见卡片（用户报"广告/疑似徽章不换色"），doItemsLayout
        # 强制全部 item 重排重绘，最稳
        self.grid.viewport().update()
        self.grid.doItemsLayout()
        # 覆盖层：旧主题截图，360ms 平滑淡出（InOutCubic，双方向都柔和）
        from PySide6.QtCore import QPropertyAnimation, QEasingCurve
        overlay = QLabel(self)
        overlay.setPixmap(old_pix)
        overlay.setGeometry(0, 0, self.width(), self.height())
        overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        overlay.raise_()
        eff = QGraphicsOpacityEffect(overlay)
        eff.setOpacity(1.0)
        overlay.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", self)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setDuration(360)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        anim.finished.connect(overlay.deleteLater)
        anim.start(QPropertyAnimation.DeleteWhenStopped)
        return self

    # ---------- UI ----------
    def _build(self):
        from workbench_ui import build_workbench
        build_workbench(self)

    def _apply_styles(self):
        """全部按 cds_tokens 生成，且按「角色」上色：
        品牌橙=主动作 / 红=破坏性+广告 / 绿=保留+完成 / 琥珀=疑似 / 蓝=异常 / 暖灰=中性。"""
        d = isDarkTheme()
        TT = CDSTT
        txt = CDST.c(d, TT.text)
        txt2 = CDST.c(d, TT.text_2)
        on = CDST.c(d, TT.text_on_clay)
        clay = CDST.c(d, TT.clay)
        clay_h = CDST.c(d, CDST.CLAY_HOVER)
        clay_fill = CDST.c(d, TT.clay_fill)
        hov = CDST.c(d, TT.hover)
        bdr = CDST.c(d, TT.border)
        surf2 = CDST.c(d, TT.surface2)
        surf3 = CDST.c(d, TT.surface3)
        dis_bg = "rgba(20,20,20,0.06)" if not d else "rgba(255,255,255,0.06)"
        dis_fg = "rgba(20,20,20,0.30)" if not d else "rgba(255,255,255,0.28)"
        dang_fg, dang_fill, dang_edge = CDST.sem(d, "danger")
        ok_fg, ok_fill, ok_edge = CDST.sem(d, "ok")
        gray_fg, gray_fill, gray_edge = CDST.sem(d, "gray")

        def pair(tpl):
            """同一套模板在两套主题下取色（模板里所有值都已按当前主题算好）。"""
            return tpl

        # 品牌橙实底（开始扫描）
        primary = pair(f"""PrimaryPushButton {{ background: {clay}; border: none;
            border-radius: {CDST.R_LG}px; padding: 6px 16px; color: {on}; }}
            PrimaryPushButton:hover {{ background: {clay_h}; }}
            PrimaryPushButton:pressed {{ background: {clay}; }}
            PrimaryPushButton:disabled {{ background: {dis_bg}; color: {dis_fg}; }}""")
        # 语义红实底（执行处置：破坏性动作）
        danger = pair(f"""PrimaryPushButton {{ background: {dang_fg}; border: none;
            border-radius: {CDST.R_LG}px; padding: 6px 16px; color: #ffffff; }}
            PrimaryPushButton:hover {{ background: {dang_fg}; }}
            PrimaryPushButton:disabled {{ background: {dis_bg}; color: {dis_fg}; }}""")
        # 中性幽灵（次要动作：一律暖灰，不再全用橙）
        trans = pair(f"""TransparentPushButton {{ background: transparent; border: none;
            border-radius: {CDST.R_LG}px; padding: 5px 10px; color: {txt2}; }}
            TransparentPushButton:hover {{ background: {hov}; color: {txt}; }}
            TransparentPushButton:disabled {{ color: {dis_fg}; }}""")
        # 语义描边按钮（Push：红=标记广告 / 绿=保留）
        def outline(fg, fill, edge):
            return pair(f"""PushButton {{ background: transparent; border: 1px solid {edge};
            border-radius: {CDST.R_LG}px; padding: 5px 14px; color: {fg}; }}
            PushButton:hover {{ background: {fill}; }}
            PushButton:pressed {{ background: {fill}; }}""")
        # 扁平输入（CDS：无重描边，靠底色与发丝区分层级）
        inp = pair(f"""ComboBox, SearchLineEdit {{ background: {surf3};
            border: 1px solid {bdr}; border-radius: {CDST.R_LG}px; padding: 4px 10px; color: {txt}; }}
            ComboBox:hover, SearchLineEdit:hover {{ border: 1px solid {gray_edge}; }}
            ComboBox:focus, SearchLineEdit:focus {{ border: 1px solid {clay}; }}
            ComboBox::drop-down {{ border: none; width: 24px; }}""")
        # 中性图标按钮（无边框，hover 才出底）
        tool = pair(f"""ToolButton {{ background: transparent; border: none;
            border-radius: {CDST.R_LG}px; color: {txt2}; }}
            ToolButton:hover {{ background: {hov}; color: {txt}; }}""")
        prog = pair(f"""ProgressBar {{ background: {gray_fill}; border: none; border-radius: 3px;
            height: 4px; min-height: 4px; max-height: 4px; }}
            ProgressBar::chunk {{ background: {clay}; border-radius: 3px; }}""")

        setCustomStyleSheet(self.btn_scan, primary, primary)
        disposal_css = danger if self._current_disposal() == 'delete' else primary
        setCustomStyleSheet(self.btn_dispose, disposal_css, disposal_css)
        for w in (self.btn_add, self.btn_cancel, self.btn_openq, self.btn_restore,
                  self.btn_big, self.btn_log, self.btn_undo, self.btn_pause,
                  self.btn_rules, self.btn_cfg, self.btn_history, self.btn_export,
                  self.btn_theme, self.btn_palette, self.btn_prev, self.btn_next,
                  self.btn_all, self.btn_invert, self.btn_clear, self.btn_similar):
            setCustomStyleSheet(w, trans, trans)
        mk = outline(dang_fg, dang_fill, dang_edge)
        kp = outline(ok_fg, ok_fill, ok_edge)
        setCustomStyleSheet(self.btn_mark, mk, mk)
        setCustomStyleSheet(self.btn_keep, kp, kp)
        for w in (self.f_verdict, self.f_reason, self.f_search, self.f_sort):
            setCustomStyleSheet(w, inp, inp)
        # Segmented 自带样式（不走 PushButton 模板）
        setCustomStyleSheet(self.progress, prog, prog)
        # 分隔发丝 / 标签 / 预览画布 / 徽标 上色
        self._paint_chrome(d)

    def _paint_chrome(self, d=None):
        """工具条分隔线、标签、预览画布、状态胶囊、计数药丸的主题同步。"""
        d = isDarkTheme() if d is None else d
        TT = CDSTT
        bdr = CDST.c(d, TT.border)
        txt2 = CDST.c(d, TT.text_2)
        txt3 = CDST.c(d, TT.text_3)
        surf2 = CDST.c(d, TT.surface2)
        for w in self.findChildren(QFrame, "vsep"):
            w.setStyleSheet(f"background: {bdr};")
        for name in ("lbl_disposal",):
            w = getattr(self, name, None)
            if w is not None:
                w.setStyleSheet(f"color: {txt2}; background: transparent;")
        pv = getattr(self, "preview", None)
        if pv is not None:
            pv.setStyleSheet(
                f"#previewCanvas {{ background: {surf2}; border: 1px solid {bdr};"
                f" border-radius: {CDST.R_CARD}px; color: {txt3}; }}")
        db = getattr(self, "detail_badge", None)
        if db is not None:
            cur = self._current_item()
            if cur:
                self._paint_detail_badge(cur)
            else:
                db.setText("未选中")
                db.setFixedHeight(26)
                db.setAlignment(Qt.AlignCenter)
                db.setStyleSheet(f"#detailBadge {{ {CDST.pill_qss(d, 'gray')} }}")
        self._paint_status_pill()
        self._update_count_label()

    # ---------- 网格交互 ----------
    def eventFilter(self, obj, ev):
        g = getattr(self, "grid", None)
        # —— 自绘框选矩形：跟踪 viewport 上的左键拖拽，驱动 _sel_band overlay ——
        if g is not None and obj is g.viewport() and getattr(self, "_sel_band", None) is not None:
            t = ev.type()
            if t == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
                self._band_start = ev.position().toPoint()
                self._sel_band.hide()
            elif t == QEvent.MouseMove and self._band_start is not None and (ev.buttons() & Qt.LeftButton):
                cur = ev.position().toPoint()
                r = QRect(self._band_start, cur).normalized()
                if r.width() >= 4 or r.height() >= 4:
                    self._sel_band.setGeometry(r)
                    self._sel_band.show()
                    self._sel_band.raise_()
            elif t in (QEvent.MouseButtonRelease, QEvent.Leave):
                # 框选（无论几张，含单张）结束后统一勾选命中项——原逻辑走
                # selectionChanged 且要求 ≥2 张，单张框选只选中不勾选（用户报告）
                if (t == QEvent.MouseButtonRelease and self._sel_band is not None
                        and self._sel_band.isVisible()):
                    changed = False
                    for ix in g.selectionModel().selectedIndexes():
                        r = ix.row()
                        if 0 <= r < len(self.model.items):
                            p = self.model.items[r]["path"]
                            if p not in self.model.checked:
                                self.model.checked.add(p)
                                changed = True
                    if changed:
                        self.checked = self.model.checked
                        self._update_count_label()
                        g.viewport().update()
                self._band_start = None
                if self._sel_band is not None:
                    self._sel_band.hide()
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
            elif k == Qt.Key_Delete:
                self.execute_disposal()
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
                elif k == Qt.Key_Delete:
                    self.execute_disposal()
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
        self._sync_sweep_timer()

    def _sync_fade_visibility(self, *_a):
        """渐隐遮罩只在该方向还有可滚内容时显示（静止时显示会盖住卡片顶部）。"""
        sb = self.grid.verticalScrollBar()
        self._fade_top.setVisible(sb.value() > 0)
        self._fade_bottom.setVisible(sb.value() < sb.maximum())

    def _sync_sweep_timer(self):
        """有 pending 缩略图才跑扫光动画。"""
        try:
            pending = len(self.thumbs._pending)
        except Exception:
            pending = 0
        if pending > 0 and not self._sweep_timer.isActive():
            self._sweep_timer.start()
        elif pending == 0 and self._sweep_timer.isActive():
            self._sweep_timer.stop()

    def _on_current_changed(self, cur, _prev):
        if cur.isValid() and cur.row() < len(self.model.items):
            self.show_preview(self.model.items[cur.row()]["path"])

    def _on_context_menu(self, pos):
        idx = self.grid.indexAt(pos)
        menu = QMenu(self)
        if idx.isValid():
            menu.addAction("勾选 / 取消勾选", lambda: self.toggle_check(idx.row()))
            menu.addAction("打开大图", self.open_big)
            menu.addAction("打开源文件文件夹", lambda: self.open_item_folder(idx.row()))
            menu.addSeparator()
        menu.addAction("全选", self.select_all)
        menu.addAction("反选", self.invert_sel)
        menu.addAction("清除选择", self.clear_sel)
        menu.addAction("选相似组", self.select_similar_groups)
        menu.exec(self.grid.viewport().mapToGlobal(pos))

    def open_item_folder(self, row: int):
        """在资源管理器中定位该图片（右键菜单入口）。"""
        if not (0 <= row < len(self.model.items)):
            return self
        path = self.model.items[row]["path"]
        folder = str(Path(path).parent)
        try:
            # explorer /select 高亮目标文件；文件已不在（被处置）则退化为打开目录
            if Path(path).exists():
                subprocess.Popen(["explorer", "/select,", str(Path(path))])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        except Exception as e:
            self._info("提示", f"无法打开文件夹：{e}")
        return self

    def select_all(self):
        """全选 / 再按一次取消全选（toggle）。"""
        items = list(self.model.items)
        if not items:
            return self
        visible_paths = {it['path'] for it in items}
        if visible_paths.issubset(self.model.checked):
            self.model.checked.difference_update(visible_paths)
            self._log("已取消全选")
        else:
            for it in items:
                self.model.checked.add(it["path"])
            self._log(f"已全选 {len(items)} 张")
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
            self.set_status("未发现相似组（或聚类尚未完成）")
            return self
        n = 0
        for g in self.similar_groups:
            for p in g:
                self.model.checked.add(p)
                n += 1
        self.checked = self.model.checked
        self.grid.viewport().update()
        self._update_count_label()
        self.set_status(f"已勾选 {n} 张相似图（{len(self.similar_groups)} 组）")
        return self

    # ---------- 弹窗 ----------
    def _ask(self, title, content):
        return bool(NoticeDialog(self, title, content).exec())

    def _info(self, title, content):
        NoticeDialog(self, title, content, confirmation=False).exec()

    def _toast(self, content, ok=True):
        """CDS Toast：右下角 3000ms 自消失（claudecodeui 规范）。

        用自绘 Toast 而非 InfoBar——InfoBar 是模态化的浮层，在自动化测试的事件泵里
        会挂住流程（历史坑），且位置固定顶部会遮挡状态文字。
        """
        try:
            from cds_widgets import CDSToast
            CDSToast.show(self, content, "ok" if ok else "warn")
        except Exception:
            pass

    def open_palette(self):
        """⌘K 命令面板（claudecodeui 范式）：所有操作一个入口。"""
        palette = getattr(self, '_palette', None)
        if palette is not None and palette.isVisible():
            palette.close()
            return self
        if palette is not None:
            palette.deleteLater()
        cmds = [
            ('添加文件夹', self.add_folder),
            ('扫描设置', self.open_config),
            ('打开隔离目录', self.open_quarantine_dir),
            ("开始扫描", self.start_scan),
            ("暂停 / 继续扫描", self.toggle_pause),
            ("取消扫描", self.cancel_scan),
            ("全选 / 取消全选", self.select_all),
            ("反选", self.invert_sel),
            ("清除选择", self.clear_sel),
            ("选相似组", self.select_similar_groups),
            ("执行处置", self.execute_disposal),
            ("撤销上次隔离处置", self.undo_disposal),
            ("规则管理…", self.open_rule_editor),
            ("扫描历史…", self.open_history),
            ("隔离区管理…", self.restore_ui),
            ("导出 CSV 报告", self.export_report),
            ("切换浅色 / 深色", self.toggle_theme),
        ]
        from cds_widgets import CDSCommandPalette
        self._palette = CDSCommandPalette.popup(self, cmds)
        return self

    def build_config(self):
        return ConfigDialog(self, self.cfg)

    def open_config(self):
        if self._disposing or self._file_busy or (self.scan_thread and self.scan_thread.is_alive()):
            self._toast('请在当前任务完成后修改设置', ok=False)
            return
        d = self.build_config()
        d.exec()

    def build_rule_editor(self):
        return RuleEditorLite(self, self.cfg)

    def open_rule_editor(self):
        if self._disposing or self._file_busy or (self.scan_thread and self.scan_thread.is_alive()):
            self._toast('请在当前任务完成后修改规则', ok=False)
            return
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
        if (self._disposing or self._file_busy) and not self._ask(
                "正在处置", "文件操作正在进行中，退出会中断操作。确定退出？"):
            e.ignore()
            return
        self.cancel.set()
        self.pause.clear()
        try:
            self._timer.stop()
        except Exception:
            pass
        try:
            self.thumbs.stop()
        except Exception:
            pass
        for name in ('_preview_timer', '_resize_timer', '_thumb_timer', '_sweep_timer'):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.stop()
        if self._big_win is not None:
            try:
                self._big_win.close()
            except RuntimeError:
                pass
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
        self.set_status(msg)
        if hasattr(self, "log"):
            from datetime import datetime
            self.log.append(f"[{datetime.now():%H:%M:%S}] {msg}")

    # ---------- 扫描 ----------
    def start_scan(self):
        if self._disposing or self._file_busy:
            self._info('提示', '请等待当前文件操作完成')
            return
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
        self._scan_generation += 1
        self._cached_count = 0
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
        self._stat_cache = {}
        self._scan_t0 = time.time()
        self.btn_scan.setEnabled(False)
        self.dot.set_state("running")
        self._log(f"开始扫描：{len(roots)} 个文件夹")
        self.set_status("正在收集文件…")
        self._last_progress = 0
        self.progress.setValue(0)
        self.folder_bar.update_progress()
        self.scan_thread = threading.Thread(
            target=af.scan_worker,
            args=(roots, copy.deepcopy(self.cfg), enable, self._cache, self.scan_q, self.cancel, self.pause),
            daemon=True)
        self.scan_thread.start()
        self._sync_actions()
        return self

    def toggle_pause(self):
        """暂停/继续扫描（引擎分批提交，暂停时真正停下不再烧 CPU）。"""
        if not self.scan_thread or not self.scan_thread.is_alive():
            return self
        if self.pause.is_set():
            self.pause.clear()
            self.btn_pause.setText("暂停")
            self.set_status("继续扫描…")
            self._log("继续扫描")
        else:
            self.pause.set()
            self.btn_pause.setText("继续")
            self.set_status(f"已暂停 · {getattr(self, '_last_progress', 0)}/{self.total_files} · 命中 {len(self.items)}")
            self._log(f"暂停扫描（{getattr(self, '_last_progress', 0)}/{self.total_files}）")
        return self

    def cancel_scan(self):
        self.cancel.set()
        self.pause.clear()
        self.set_status("取消中…")
        return self

    # ---------- 轮询 ----------
    def _poll_queue(self):
        items_batch = []
        latest_progress = None
        done = cancelled = False
        disposed = restored = None
        similar = None
        qop = None
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
                    self._cached_count = payload
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
                    generation, groups = payload
                    if generation == self._scan_generation:
                        similar = groups
                elif kind == "quarantine_op":
                    qop = payload
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
                    self.set_status(f"已暂停 · {latest_progress}/{self.total_files} · 命中 {len(self.items)}")
                else:
                    self.set_status(f"{latest_progress}/{self.total_files}{cached_hint} · 命中 {len(self.items)}")
            if folder_updated or latest_progress is not None or current_hit:
                self.folder_bar.update_progress()
            if cancelled:
                self.btn_scan.setEnabled(True)
                self.btn_pause.setEnabled(False)
                self.btn_pause.setText("暂停")
                self.dot.set_state("ready")
                self.set_status("已取消")
            elif disposed is not None:
                mode, moved, errors, ok_paths = disposed[0], disposed[1], disposed[2], disposed[3]
                q_root_str = disposed[4] if len(disposed) > 4 else None
                self._disposing = False
                if mode == "quarantine" and ok_paths and q_root_str:
                    self._undo_batch = (q_root_str, list(ok_paths))
                    self.btn_undo.setEnabled(True)
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
                self.set_status(msg)
                self._log("处置（" + {"quarantine": "隔离", "recycle": "回收站", "delete": "删除"}.get(mode, mode) + "）" + msg)
                self._toast(msg, ok=not errors)
                if errors:
                    self._info("处置结果", msg + "\n" + "\n".join(errors[:5]))
            elif restored is not None:
                restored_n, errors = restored
                self._disposing = False
                self.btn_restore.setEnabled(True)
                self.set_status(f"恢复完成：{restored_n} 张")
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
                n_su = sum(it['verdict'] == 'suspect' for it in self.items)
                n_er = sum(it['verdict'] == 'error' for it in self.items)
                self._log(f"扫描完成：{self.total_files} 张 · 广告 {n_ad} / 疑似 {n_su} / 异常 {n_er}")
                af.append_history({
                    "time": time.strftime("%F %T"),
                    "roots": [Path(r).name for r in self.get_roots()],
                    "total": self.total_files,
                    "hits": len(self.items), "ad": n_ad, "suspect": n_su, "error": n_er,
                    "duration": round(time.time() - getattr(self, "_scan_t0", time.time()), 1)})
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
            if qop is not None:
                self._file_busy = False
                verb, n, errors = qop
                self._log(f"隔离区{verb}：{n} 项" + (f"，{len(errors)} 项失败" if errors else ""))
                self._toast(f"隔离区{verb}完成：{n} 项", ok=not errors)
                dlg = getattr(self, "_qdlg", None)
                if dlg is not None:
                    try:
                        dlg.reload()
                    except RuntimeError:
                        self._qdlg = None
            self._sync_actions()
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
        # 补来源文件夹（根名/子路径），卡片下方展示
        roots = [str(Path(r)).rstrip("\\/") for r in self.get_roots()]
        for it in add:
            if it.get("_folder"):
                continue
            p_str = it["path"]
            best = ""
            for r in roots:
                if p_str == r or p_str.startswith(r + os.sep):
                    if len(r) > len(best):
                        best = r
            if best:
                rel = p_str[len(best):].lstrip("\\/")
                parent = str(Path(rel).parent).replace("\\", "/")
                if parent and parent != ".":
                    it["_folder"] = f"{Path(best).name}/{parent}"
                else:
                    it["_folder"] = Path(best).name
            else:
                it["_folder"] = Path(p_str).parent.name
        if add:
            start = len(self.model.items)
            self.model.beginInsertRows(QModelIndex(), start, start + len(add) - 1)
            self.model.items.extend(add)
            self.model.endInsertRows()
            self.grid.itemDelegate().note_birth([x["path"] for x in add])
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
        if v == "疑似" and it["verdict"] != "suspect":
            return False
        if v == "异常" and it['verdict'] != 'error':
            return False
        if r != "全部原因" and not any(h.get("label", "").startswith(r) for h in it["reasons"]):
            return False
        if q and q.casefold() not in it["path"].casefold():
            return False
        return True

    def _stat(self, path: str):
        """懒加载 size/mtime 缓存（排序用）；文件被处置后返回 None。"""
        if not hasattr(self, "_stat_cache"):
            self._stat_cache = {}
        if path not in self._stat_cache:
            try:
                st = os.stat(path)
                self._stat_cache[path] = (st.st_size, st.st_mtime)
            except OSError:
                self._stat_cache[path] = None
        return self._stat_cache[path]

    def _sorted_items(self, items):
        idx = self.f_sort.currentIndex() if hasattr(self, "f_sort") else 0
        if idx == 0:
            return items
        if idx == 1:
            return sorted(items, key=lambda it: Path(it["path"]).name.lower())
        if idx == 2:
            return sorted(items, key=lambda it: (self._stat(it["path"]) or (0, 0))[0], reverse=True)
        if idx == 3:
            return sorted(items, key=lambda it: (self._stat(it["path"]) or (0, 0))[1], reverse=True)
        return sorted(items, key=lambda it: it.get("score", 0), reverse=True)

    def refresh(self):
        current = self._current_item()
        current_path = current['path'] if current else None
        self.model.reset_items(self._sorted_items([it for it in self.items if self._passes_filter(it)]))
        self._sync_fade_visibility()
        self.model.checked = self.checked
        self.grid.viewport().update()
        self._update_count_label()
        if self.model.items:
            row = next((i for i, it in enumerate(self.model.items) if it['path'] == current_path), 0)
            self.grid.setCurrentIndex(self.model.index(row, 0))
        else:
            self._pending_preview = self._preview_path = None
            self._pix = None
            self.preview.clear()
            self.preview.setText('当前没有待审图片')
            self.info.clear()
            self.detail_filename.setText('尚未选择图片')
            self.detail_path.clear()
            self.preview_index.setText('— / —')
            self.detail_badge.setText('未选中')
            self.detail_badge.setStyleSheet(f"#detailBadge {{ {CDST.pill_qss(isDarkTheme(), 'gray')} }}")
        # 空状态：仅在「无结果且没在扫描」时出现
        e = getattr(self, "_empty", None)
        if e is not None:
            scanning = bool(self.scan_thread and self.scan_thread.is_alive())
            e.setVisible(len(self.model.items) == 0 and not scanning)
            if self.items:
                e.title.setText('没有符合筛选条件的图片')
                e.desc.setText('调整判定、命中原因或路径搜索。\n隐藏的已选图片仍保留在待处置集合中。')
            elif self.total_files:
                e.title.setText('本次审查已完成' if self._last_progress else '没有待审结果')
                e.desc.setText(f'本次扫描 {self.total_files} 张图片。可重新扫描或添加其他文件夹。')
            else:
                e.title.setText('开始一次图片审查')
                e.desc.setText('添加本地文件夹，或把文件夹拖入窗口。\n所有识别均在本机完成。')
            e.setGeometry(self.grid.geometry())

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
        """全局勾选与当前筛选分别计数，处置始终作用于全局勾选。"""
        n_hit, n_sel = len(self.model.items), len(self.checked)
        d = isDarkTheme()
        self.pill_hit.setText(f"待审 {n_hit}")
        self.pill_hit.setFixedHeight(26)
        self.pill_hit.setAlignment(Qt.AlignCenter)
        self.pill_hit.setStyleSheet(
            f"#countPill {{ {CDST.pill_qss(d, 'gray')} }}")
        picked = [it for it in self.items if it['path'] in self.checked]
        size = sum((self._stat(it['path']) or (0, 0))[0] for it in picked) / 1048576
        visible = sum(it['path'] in self.checked for it in self.model.items)
        self.pill_sel.setText(f"已选 {n_sel} 张 · {size:.1f} MB")
        self.pill_sel.setFixedHeight(26)
        self.pill_sel.setAlignment(Qt.AlignCenter)
        role = "clay" if n_sel else "gray"
        self.pill_sel.setStyleSheet(
            f"#countPill {{ {CDST.pill_qss(d, role)} }}")
        self.pill_sel.setVisible(True)
        self.lbl_count.setToolTip(f"待审 {n_hit} · 已选 {n_sel} · 当前筛选内 {visible}")
        if hasattr(self, 'selection_scope'):
            hidden = max(0, len(picked) - visible)
            self.selection_scope.setText(f'当前筛选内 {visible} 张' + (f'，隐藏 {hidden} 张' if hidden else ''))
            totals = {v: sum(it['verdict'] == v for it in self.items) for v in ('ad', 'suspect', 'error')}
            self.review_summary.setText(f"广告 {totals['ad']} · 疑似 {totals['suspect']} · 异常 {totals['error']}")
            mode = self._current_disposal()
            verb = {'quarantine': '隔离', 'recycle': '移入回收站', 'delete': '永久删除'}[mode]
            self.btn_dispose.setText(f'{verb} {len(picked)} 张')
            fg = CDST.c(d, CDSTT.danger if mode == 'delete' else CDSTT.clay)
            css = (f'PrimaryPushButton {{ background:{fg}; color:#ffffff; border:none;'
                   f' border-radius:8px; padding:6px 14px; }}'
                   f'PrimaryPushButton:disabled {{ background:{CDST.c(d, CDSTT.muted)};'
                   f' color:{CDST.c(d, CDSTT.text_3)}; }}')
            if getattr(self, '_dispose_css', None) != css:
                setCustomStyleSheet(self.btn_dispose, css, css)
                self._dispose_css = css
            self._sync_actions()

    def _sync_actions(self):
        if not hasattr(self, 'btn_dispose'):
            return
        scanning = bool(self.scan_thread and self.scan_thread.is_alive())
        busy = self._disposing or self._file_busy
        self.btn_scan.setEnabled(not scanning and not busy)
        self.btn_pause.setEnabled(scanning and not self.cancel.is_set())
        self.btn_cancel.setEnabled(scanning and not self.cancel.is_set())
        self.btn_pause.setVisible(scanning)
        self.btn_cancel.setVisible(scanning)
        self.btn_dispose.setEnabled(bool(self.checked) and not scanning and not busy)
        self.btn_restore.setEnabled(not scanning and not busy)
        self.btn_undo.setEnabled(bool(self._undo_batch) and not scanning and not busy)
        self.seg_disposal.setEnabled(not busy)
        for name in ('btn_cfg', 'btn_rules', 'btn_add'):
            getattr(self, name).setEnabled(not scanning and not busy)
        self.folder_bar._scroll.setEnabled(not scanning and not busy)

    def _quarantine_root(self):
        if self._quarantine_override is not None:
            return Path(self._quarantine_override)
        custom = self.cfg.get('quarantine_dir', '')
        if custom:
            return Path(custom).expanduser().resolve()
        roots = self.get_roots()
        base = Path(roots[0]).anchor if roots else Path.home().anchor
        return Path(base) / af.QUARANTINE_NAME

    def _paint_detail_badge(self, it):
        """详情头判定徽标：语义色（红=广告 / 琥珀=疑似 / 蓝=异常 / 绿=干净）。
        与计数药丸统一 26px 高。"""
        v = it.get("verdict", "clean")
        role = CDST.ROLE_BY_VERDICT.get(v, "gray")
        d = isDarkTheme()
        self.detail_badge.setText(
            f"{VERDICT_TEXT.get(v, v)} {it.get('score', 0):.0f}")
        self.detail_badge.setFixedHeight(26)
        self.detail_badge.setAlignment(Qt.AlignCenter)
        self.detail_badge.setStyleSheet(
            f"#detailBadge {{ {CDST.pill_qss(d, role)} font-weight: 600; }}")

    # ---------- 相似 ----------
    def _start_similar(self):
        if not self.cfg.get("phash", {}).get("enabled", True) or len(self.items) < 2:
            return
        paths = [it["path"] for it in self.items]
        thr = int(self.cfg.get("phash", {}).get("threshold", 6))
        generation = self._scan_generation

        def work():
            groups = af.cluster_similar(paths, thr, self._cache)
            self.scan_q.put(("similar", (generation, groups)))

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
            self.set_status(
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
        self.detail_filename.setText(Path(path).name)
        self.detail_filename.setToolTip(path)
        fm = QFontMetrics(self.detail_path.font())
        self.detail_path.setText(fm.elidedText(path, Qt.ElideMiddle, max(140, self.detail.width() - 40)))
        self.detail_path.setToolTip(path)
        row = self.grid.currentIndex().row()
        self.preview_index.setText(f'{row + 1} / {len(self.model.items)}')
        info = f"判定证据 · {VERDICT_TEXT.get(it['verdict'], it['verdict'])} · 规则得分 {it['score']:.0f}\n"
        info += "\n".join(f"· {h.get('label', '')}" for h in it["reasons"])
        if it.get("ocr_text"):
            info += f"\n\nOCR 识别文本\n{it['ocr_text']}"
        else:
            info += '\n\nOCR 识别文本\n未获取文字（可能未启用或由其他规则提前判定）。'
        gi = self._sim_map.get(path)
        if gi is not None:
            info += f"\n相似组: {len(self.similar_groups[gi])} 张"
        self.info.setPlainText(info)
        self._paint_detail_badge(it)

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
                try:
                    self._sig_preview_err.emit((path, str(e)))
                except RuntimeError:
                    pass

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

    def showEvent(self, e):
        super().showEvent(e)
        self._sync_titlebar()   # 首次显示时窗口尺寸才最终确定

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_K and (e.modifiers() & Qt.ControlModifier):
            self.open_palette()
            return
        super().keyPressEvent(e)

    def _apply_selection_palette(self):
        """选中相关配色：①原生 item 选中底走 QPalette.Highlight（品牌橙，替代深蓝）；
        ②框选矩形是自绘 overlay（原生绘制不认 palette），此处同步刷新它的样式。"""
        if not hasattr(self, "grid"):
            return
        dark = isDarkTheme()
        pal = self.grid.palette()
        pal.setColor(QPalette.Highlight, QColor(217, 119, 87, 56 if not dark else 64))
        pal.setColor(QPalette.HighlightedText, _qc(CDST.c(dark, CDSTT.text)))
        self.grid.setPalette(pal)
        band = getattr(self, "_sel_band", None)
        if band is not None:
            fill = "rgba(217,119,87,0.10)" if not dark else "rgba(217,119,87,0.14)"
            edge = "rgba(198,97,63,0.55)" if not dark else "rgba(224,130,100,0.60)"
            band.setStyleSheet(
                f"#selBand {{ background: {fill};"
                f" border: 1.5px solid {edge}; border-radius: 6px; }}")
        self.grid.viewport().update()

    def set_layout(self, mode: str = "workbench"):
        """布局已固定为单一形态（用户决定删除三骨架切换）。
        保留此方法仅为 API 兼容（gui-test / audit 引用），幂等无操作。"""
        if not hasattr(self, "lm"):
            return self
        self.lm.apply("workbench")
        return self

    def _sync_titlebar(self):
        """标题栏宽度必须始终等于窗口宽度。

        qframelesswindow 只在基类 resizeEvent 里做这件事；本类重载了 resizeEvent，
        若不显式同步，标题栏会停在初始默认宽度（640），右侧三个按钮就会悬在
        窗口中间——窗口越宽/最大化偏差越大。
        """
        tb = getattr(self, "_titlebar", None)
        if tb is None:
            return
        tb.setGeometry(0, 0, self.width(), tb.height())
        tb.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)          # 基类靠它拉宽标题栏，不能漏
        self._sync_titlebar()           # 双保险：不依赖基类实现
        if hasattr(self, "lm"):
            self.lm.on_resize()         # D3 悬浮胶囊需跟随重定位
        if getattr(self, "_empty", None) is not None:
            self._empty.setGeometry(self.grid.geometry())
        # 渐隐遮罩跟随 viewport 尺寸
        vp = self.grid.viewport()
        self._fade_top.setGeometry(0, 0, vp.width(), 28)
        self._fade_bottom.setGeometry(0, vp.height() - 28, vp.width(), 28)
        if getattr(self, "_preview_path", None):
            self._resize_timer.start()
        if hasattr(self, 'detail_filename'):
            compact = self.height() < 800
            self.detail.layout().setSpacing(6 if compact else 10)
            self.preview.setMinimumHeight(80 if compact else 130)
            self.info.setMinimumHeight(70 if compact else 100)
            self.detail_path.setVisible(not compact)
            self.lbl_disposal.setVisible(self.width() >= 1200)
            self.status.setMaximumWidth(max(240, self.width() - 560))

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

    def mark_path(self, path, as_ad):
        """按路径标记（Lightbox 内处置用）：广告=勾选，保留=移出结果列表。"""
        it = next((x for x in self.items if x["path"] == path), None)
        if not it:
            return self
        if as_ad:
            self.model.checked.add(path)
            self.checked = self.model.checked
            self.grid.viewport().update()
            self._update_count_label()
            self._log(f"标记广告：{Path(path).name}")
        else:
            self.items = [x for x in self.items if x["path"] != path]
            self._pending_rows = [x for x in self._pending_rows if x["path"] != path]
            self.model.checked.discard(path)
            self.checked = self.model.checked
            if hasattr(self, "_stat_cache"):
                self._stat_cache.pop(path, None)
            self.refresh()
            self._log(f"标记保留：{Path(path).name}")
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
        if self._disposing or self._file_busy:
            return self
        checked_items = [it for it in self.items if it["path"] in self.checked]
        if not checked_items:
            self._info("提示", "请先勾选要处置的图片（点卡片左上圆圈或按空格）")
            return
        if self.scan_thread and self.scan_thread.is_alive():
            self._info("提示", "扫描正在进行，请等扫描结束或先取消再处置\n（处置会让正在分析的文件消失，结果不可信）")
            return
        mode = self._current_disposal()
        # 处置前影响预览：按判定分类计数 + 总大小
        n_ad = sum(1 for it in checked_items if it["verdict"] == "ad")
        n_su = sum(1 for it in checked_items if it["verdict"] == "suspect")
        n_er = len(checked_items) - n_ad - n_su
        total_bytes = 0
        for it in checked_items:
            st = self._stat(it["path"])
            if st:
                total_bytes += st[0]
        size_mb = total_bytes / 1048576
        breakdown = f"共 {len(checked_items)} 张（广告 {n_ad} / 疑似 {n_su}" + \
                    (f" / 异常 {n_er}" if n_er else "") + f"）· 总大小 {size_mb:.1f} MB"
        visible = {it['path'] for it in self.model.items}
        hidden = sum(it['path'] not in visible for it in checked_items)
        if hidden:
            breakdown += f'\n其中 {hidden} 张被当前筛选隐藏，也会参与本次处置。'
        if mode == "delete" and not self._ask(
                "危险操作", f"{breakdown}\n将永久删除，不可恢复！\n确定继续？"):
            return
        roots = self.get_roots()
        q_root = None
        if mode == "quarantine":
            q_root = self._quarantine_root()
            if not self._ask("隔离确认",
                             f"{breakdown}\n将移动到\n{q_root}\n（可随时恢复 / 撤销）\n继续？"):
                return
        elif mode == "recycle":
            if not self._ask("回收站确认",
                             f"{breakdown}\n将移入系统回收站（可从回收站还原）\n继续？"):
                return
        self.btn_scan.setEnabled(False)
        self.btn_dispose.setEnabled(False)
        self.btn_restore.setEnabled(False)
        self.set_status("处置中…")
        self._disposing = True
        self._sync_actions()

        def work():
            moved, errors, ok_paths = 0, [], []
            try:
                if mode == "quarantine":
                    moved, errors, ok_paths = af.apply_quarantine(checked_items, roots, q_root, af.log_jsonl)
                elif mode == "recycle":
                    moved, errors, ok_paths = af.apply_recycle(checked_items, af.log_jsonl)
                else:
                    moved, errors, ok_paths = af.apply_delete(checked_items, af.log_jsonl)
            finally:
                self.scan_q.put(("disposed", (mode, moved, errors, ok_paths,
                                              str(q_root) if q_root else None)))

        threading.Thread(target=work, daemon=True).start()
        return self

    def open_quarantine_dir(self):
        q_root = self._quarantine_root()
        if not q_root.exists():
            self._info("提示", f"隔离目录还不存在：\n{q_root}\n（执行一次隔离处置后会自动创建）")
            return self
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(q_root)))
        return self

    def undo_disposal(self):
        """撤销上一次隔离处置：按 manifest 把该批文件移回原位置。"""
        if not self._undo_batch or self._disposing or self._file_busy or (self.scan_thread and self.scan_thread.is_alive()):
            return self
        q_root_str, srcs = self._undo_batch
        if not self._ask("撤销处置", f"将把上次隔离的 {len(srcs)} 张图移回原位置\n继续？"):
            return self
        self.btn_undo.setEnabled(False)
        self._undo_batch = None
        self._disposing = True
        self._sync_actions()

        def work():
            try:
                entries = af.list_quarantine(Path(q_root_str))
                want = set(srcs)
                dsts = [e["dst"] for e in entries if e.get("src") in want]
                restored, errors = af.restore_quarantine_items(Path(q_root_str), dsts, af.log_jsonl)
            except Exception as e:
                restored, errors = 0, [str(e)]
            finally:
                self.scan_q.put(("restored", (restored, errors)))

        threading.Thread(target=work, daemon=True).start()
        self._log(f"撤销上次隔离处置（{len(srcs)} 张）")
        return self

    def restore_ui(self):
        """隔离区管理：查看 / 还原选中 / 彻底删除 / 全部还原。"""
        if self._disposing or self._file_busy or (self.scan_thread and self.scan_thread.is_alive()):
            return self
        q_root = self._quarantine_root()
        if not q_root.exists():
            d = QFileDialog.getExistingDirectory(self, "选择隔离目录（含 manifest.json）")
            if not d:
                return self
            q_root = Path(d)
        self._qdlg = QuarantineDialog(self, q_root)
        try:
            self._qdlg.exec()
        finally:
            self._qdlg = None
        return self

    def open_history(self):
        HistoryDialog(self).exec()
        return self

    def export_report(self):
        """导出当前筛选后的结果为 CSV（Excel 可直接打开，带 BOM）。"""
        items = self.model.items
        if not items:
            self._info("提示", "当前没有可导出的结果")
            return self
        default = f"广告图过滤报告_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path, _ok = QFileDialog.getSaveFileName(self, "导出报告", default, "CSV 文件 (*.csv)")
        if not path:
            return self
        import csv
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["文件名", "路径", "所在文件夹", "判定", "分数", "命中原因"])
            for it in items:
                reasons = "; ".join(h.get("label", "") for h in it.get("reasons", []))
                w.writerow([Path(it["path"]).name, it["path"], it.get("_folder", ""),
                            VERDICT_TEXT.get(it["verdict"], it["verdict"]),
                            f"{it.get('score', 0):.0f}", reasons])
        self._toast(f"已导出 {len(items)} 条到 {Path(path).name}")
        self._log(f"导出报告：{path}（{len(items)} 条）")
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


class ConfigDialog(WorkbenchDialog):
    """扫描设置：文件夹 + 识别方式 + 线程 + 规则入口。"""

    def __init__(self, app, cfg):
        super().__init__(app)
        self.app = app
        self.cfg = cfg
        self.widget.setFixedWidth(880)

        self.viewLayout.addWidget(SubtitleLabel("扫描设置", self.widget))
        tip = CaptionLabel("按工作流程整理扫描、识别与文件处置选项。保存后生效。", self.widget)
        tip.setWordWrap(True)
        self.viewLayout.addWidget(tip)

        from dialog_ui import section, navigation
        folders, folder_layout = section('扫描目录', '可添加多个目录，也可直接将文件夹拖入下方列表。', self)
        recognition, recognition_layout = section('识别选项', '按需要开启识别方式，并调整扫描性能。', self)
        storage, storage_layout = section('隔离与配置', '指定文件隔离位置，或导入、导出配置。', self)
        self.folder_list = FolderDropList(self.widget)
        for r in app.get_roots():
            it = QListWidgetItem(str(r))
            it.setData(Qt.UserRole, str(r))
            self.folder_list.addItem(it)
        self.folder_list.setMinimumHeight(210)
        self.folder_list.setTextElideMode(Qt.ElideMiddle)
        self.folder_list.setAcceptDrops(True)
        folder_layout.addWidget(self.folder_list, 1)
        frow = QHBoxLayout()
        b_add = TransparentPushButton(FIF.FOLDER_ADD, "添加文件夹", self.widget)
        b_add.clicked.connect(self._add)
        b_rm = TransparentPushButton(FIF.REMOVE, "移除选中", self.widget)
        b_rm.clicked.connect(self._rm)
        b_rules = TransparentPushButton(FIF.SETTING, "规则管理…", self.widget)
        b_rules.clicked.connect(self._open_rules)
        b_exp = TransparentPushButton(FIF.SAVE, "导出配置", self.widget)
        b_exp.setToolTip("把当前配置（含规则词库）保存为 JSON，可分享给另一台机器")
        b_exp.clicked.connect(self._export_cfg)
        b_imp = TransparentPushButton(FIF.FOLDER_ADD, "导入配置", self.widget)
        b_imp.setToolTip("从 JSON 文件导入配置（覆盖当前配置并立即保存）")
        b_imp.clicked.connect(self._import_cfg)
        for b in (b_add, b_rm):
            frow.addWidget(b)
        frow.addStretch(1)
        folder_layout.addLayout(frow)

        recognition_layout.addWidget(CaptionLabel("识别方式", self.widget))
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
        recognition_layout.addLayout(crow)

        orow = QHBoxLayout()
        orow.addWidget(CaptionLabel("全图兜底", self.widget))
        self.ocr_scope = ComboBox(self.widget)
        self.ocr_scope.addItems(["条带优先（快）", "条带+全图兜底（稳）"])
        self.ocr_scope.setCurrentIndex(1 if cfg.get("ocr", {}).get("full_image_if_nothing", True) else 0)
        self.ocr_scope.setToolTip("未达到广告高阈值时补扫全图，以识别中央水印")
        orow.addWidget(self.ocr_scope)
        recognition_layout.addLayout(orow)
        orow = QHBoxLayout()
        orow.addWidget(CaptionLabel("线程", self.widget))
        self.cb_workers = ComboBox(self.widget)
        self.cb_workers.addItems([str(i) for i in range(1, 17)])
        self.cb_workers.setCurrentIndex(max(0, min(15, int(cfg.get("workers", 4)) - 1)))
        orow.addWidget(self.cb_workers)
        orow.addStretch(1)
        recognition_layout.addLayout(orow)

        from PySide6.QtWidgets import QSpinBox
        advanced = QHBoxLayout()
        advanced.addWidget(CaptionLabel('相似距离阈值', self.widget))
        self.sim_threshold = QSpinBox(self.widget)
        self.sim_threshold.setRange(0, 64)
        self.sim_threshold.setValue(int(cfg.get('phash', {}).get('threshold', 6)))
        self.sim_threshold.setToolTip('dHash 距离；越小越严格。修改后下次扫描生效。')
        advanced.addWidget(self.sim_threshold)
        advanced.addStretch()
        limit = min(os.cpu_count() or 4, 8)
        recognition_layout.addLayout(advanced)
        recognition_layout.addWidget(CaptionLabel(f'本机实际并行上限 {limit} 线程', self.widget))
        recognition_layout.addWidget(b_rules, 0, Qt.AlignLeft)
        recognition_layout.addStretch()

        from PySide6.QtWidgets import QLineEdit
        storage_layout.addWidget(CaptionLabel('隔离目录', self))
        self.quarantine_path = QLineEdit(self)
        self.quarantine_path.setPlaceholderText('留空：使用扫描盘的 _广告隔离 目录')
        self.quarantine_path.setText(cfg.get('quarantine_dir', ''))
        self.quarantine_path.setMinimumHeight(36)
        storage_layout.addWidget(self.quarantine_path)
        qrow = QHBoxLayout()
        choose = PushButton('选择目录…', self)
        choose.clicked.connect(self._choose_quarantine)
        reset = PushButton('恢复默认', self)
        reset.clicked.connect(self.quarantine_path.clear)
        qrow.addWidget(choose)
        qrow.addWidget(reset)
        qrow.addStretch()
        storage_layout.addLayout(qrow)
        hint = CaptionLabel('更改目录只影响之后的隔离操作，不移动已有文件。\n旧隔离区仍可在“隔离区管理”中切换打开；隔离目录会自动跳过扫描。', self)
        hint.setWordWrap(True)
        storage_layout.addWidget(hint)
        storage_layout.addSpacing(16)
        storage_layout.addWidget(CaptionLabel('配置备份', self))
        backup = QHBoxLayout()
        backup.addWidget(b_exp)
        backup.addWidget(b_imp)
        backup.addStretch()
        storage_layout.addLayout(backup)
        storage_layout.addStretch()
        self.viewLayout.addWidget(navigation(self, [
            ('扫描目录', folders), ('识别选项', recognition), ('隔离与配置', storage)]), 1)

        self.yesButton.setText("保存")
        self.cancelButton.setText("关闭")
        self.widget.setMinimumWidth(880)
        from workbench_ui import style_dialog
        style_dialog(self)

    def _reload_controls(self):
        cfg = self.cfg
        self.cb_fn.setChecked(cfg.get('_enable_filename', True))
        self.cb_shape.setChecked(cfg.get('_enable_shape', True))
        self.cb_qr.setChecked(cfg['qr']['enabled'])
        self.cb_ocr.setChecked(cfg['ocr']['enabled'])
        self.cb_sim.setChecked(cfg['phash']['enabled'])
        self.sim_threshold.setValue(int(cfg['phash']['threshold']))
        self.ocr_scope.setCurrentIndex(int(bool(cfg['ocr']['full_image_if_nothing'])))
        self.cb_workers.setCurrentIndex(max(0, min(15, int(cfg['workers']) - 1)))
        self.quarantine_path.setText(cfg.get('quarantine_dir', ''))

    def _form_config(self):
        data = copy.deepcopy(self.cfg)
        data['_enable_filename'] = self.cb_fn.isChecked()
        data['_enable_shape'] = self.cb_shape.isChecked()
        data['qr']['enabled'] = self.cb_qr.isChecked()
        data['ocr']['enabled'] = self.cb_ocr.isChecked()
        data['ocr']['full_image_if_nothing'] = self.ocr_scope.currentIndex() == 1
        data['phash'].update(enabled=self.cb_sim.isChecked(), threshold=self.sim_threshold.value())
        data['workers'] = self.cb_workers.currentIndex() + 1
        data['quarantine_dir'] = self.quarantine_path.text().strip()
        return data

    def _choose_quarantine(self):
        directory = QFileDialog.getExistingDirectory(self, '选择隔离目录', self.quarantine_path.text())
        if directory:
            self.quarantine_path.setText(directory)

    @staticmethod
    def _validate_quarantine(data):
        raw = data.get('quarantine_dir', '')
        if not isinstance(raw, str):
            raise ValueError('隔离目录须为路径文本')
        if not raw.strip():
            data['quarantine_dir'] = ''
            return
        directory = Path(raw.strip()).expanduser()
        if not directory.is_absolute():
            raise ValueError('请选择完整的隔离目录路径')
        directory = directory.resolve()
        if directory == Path(directory.anchor) or (directory.exists() and not directory.is_dir()):
            raise ValueError('请选择一个文件夹作为隔离目录，不能使用磁盘根目录或文件')
        data['quarantine_dir'] = str(directory)

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

    def _export_cfg(self):
        path, _ok = QFileDialog.getSaveFileName(self, "导出配置", "ad_filter_config.json",
                                                "JSON 文件 (*.json)")
        if not path:
            return
        try:
            data = {k: v for k, v in self._form_config().items() if not k.startswith("_last_")}
            Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            InfoBar.success(title="已导出", content=Path(path).name,
                            orient=Qt.Horizontal, isClosable=True,
                            position=InfoBarPosition.TOP, duration=2500, parent=self)
        except Exception as e:
            InfoBar.error(title="导出失败", content=str(e),
                          orient=Qt.Horizontal, isClosable=True,
                          position=InfoBarPosition.TOP, duration=4000, parent=self)

    def _import_cfg(self):
        path, _ok = QFileDialog.getOpenFileName(self, "导入配置", "", "JSON 文件 (*.json)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "ocr_keywords" not in data:
                raise ValueError("不是有效的广告图过滤配置（缺少 ocr_keywords）")
            merged = copy.deepcopy(af.DEFAULT_CONFIG)
            for key, value in data.items():
                if key in ('filename_rules', 'ocr_keywords', 'whitelist_rules'):
                    if not isinstance(value, list):
                        raise ValueError(f'{key} 须为规则列表')
                    for rule in value:
                        if not isinstance(rule, dict) or not isinstance(rule.get('pattern'), str):
                            raise ValueError(f'{key} 中存在无效规则')
                        re.compile(rule['pattern'])
                        if key == 'ocr_keywords':
                            weight = float(rule.get('weight', 1))
                            if not 1 <= weight <= 10000:
                                raise ValueError('规则权重须在 1–10000 范围内')
                    merged[key] = value
                elif isinstance(merged.get(key), dict):
                    if not isinstance(value, dict):
                        raise ValueError(f'{key} 须为配置对象')
                    merged[key].update(value)
                elif not key.startswith('_last_'):
                    merged[key] = value
            if not 1 <= int(merged['workers']) <= 16:
                raise ValueError('线程须在 1–16 范围内')
            if not 0 <= int(merged['phash']['threshold']) <= 64:
                raise ValueError('相似距离须在 0–64 范围内')
            for key in ('strip_ratio', 'max_dim', 'score_high', 'score_low'):
                if float(merged['ocr'][key]) <= 0:
                    raise ValueError(f'OCR 参数 {key} 须大于 0')
            self._validate_quarantine(merged)
            locations = merged.get('quarantine_locations', [])
            if not isinstance(locations, list) or not all(isinstance(p, str) and Path(p).is_absolute() for p in locations):
                raise ValueError('隔离区历史目录须为完整路径列表')
            old = str(self.app._quarantine_root())
            merged['quarantine_locations'] = list(dict.fromkeys(
                self.cfg.get('quarantine_locations', []) + locations + [old]))
        except Exception as e:
            InfoBar.error(title="导入失败", content=str(e),
                          orient=Qt.Horizontal, isClosable=True,
                          position=InfoBarPosition.TOP, duration=4000, parent=self)
            return
        # 原地更新（app.cfg 引用不变）并立即落盘；界面控件重开对话框即可见新值
        self.cfg.clear()
        self.cfg.update(merged)
        af.save_config(self.cfg)
        self._reload_controls()
        InfoBar.success(title="已导入", content="配置和当前设置控件已同步更新",
                        orient=Qt.Horizontal, isClosable=True,
                        position=InfoBarPosition.TOP, duration=3000, parent=self)

    def validate(self):
        roots = [self.folder_list.item(i).data(Qt.UserRole) for i in range(self.folder_list.count())]
        data = self._form_config()
        try:
            self._validate_quarantine(data)
        except ValueError as e:
            self.navigation.setCurrentRow(2)
            self.app._info('隔离目录无效', str(e))
            return False
        old = str(self.app._quarantine_root())
        known = list(self.cfg.get('quarantine_locations', []))
        if old not in known:
            known.append(old)
        data['quarantine_locations'] = known
        self.app._roots = [r for r in roots if r]
        self.cfg.clear()
        self.cfg.update(data)
        af.save_config(self.cfg)
        if hasattr(self.app, "folder_bar"):
            self.app.folder_bar.rebuild()
        self.accept()
        return True


# ================================================================ 规则编辑（精简版）
class RuleEditorLite(WorkbenchDialog):
    """规则管理：文件名 / OCR 词库 / 白名单，Pivot 切换（currentItemChanged 驱动）。"""

    def __init__(self, parent, cfg_ref):
        super().__init__(parent)
        self.cfg = cfg_ref
        self.show_errors = True
        self.widget.setFixedWidth(960)

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
            "tester": self._tester_page(),
        }
        for key, pg in self._pages.items():
            self.stack.addWidget(pg["page"])
            self.pivot.addItem(routeKey=key, text=pg["label"])
        self.pivot.currentItemChanged.connect(
            lambda k: self.stack.setCurrentWidget(self._pages[k]["page"]))
        from dialog_ui import navigation
        self.pivot.hide()  # 保留既有自动化与快捷切页接口
        body = navigation(self, [(pg['label'], pg['page']) for pg in self._pages.values()])
        self.stack.hide()
        self.stack = self.page_stack
        keys = list(self._pages)
        self.navigation.currentRowChanged.connect(lambda row: self.pivot.setCurrentItem(keys[row]))
        self.pivot.currentItemChanged.connect(lambda key: self.navigation.setCurrentRow(keys.index(key)))
        self.viewLayout.addWidget(body, 1)

        self.yesButton.setText("保存规则")
        self.cancelButton.setText("关闭")
        self.widget.setMinimumWidth(960)
        from workbench_ui import style_dialog
        style_dialog(self)

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

    def _collect_rules(self):
        """从三个表格实时收集规则（含未保存的编辑；无效正则/权重跳过）。"""
        import re
        out = {}
        for key, pg in self._pages.items():
            t = pg.get("table")
            if t is None:
                continue
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
                    continue
                rule = {"pattern": pat, "label": lab or "自定义"}
                if pg["weight"]:
                    w_it = t.item(r, 2)
                    try:
                        rule["weight"] = max(1, int(float((w_it.text() if w_it else "1").strip() or 1)))
                    except ValueError:
                        continue
                rules.append(rule)
            out[key] = rules
        return out

    def _tester_page(self):
        """规则测试器：贴 OCR 文本（可选文件名）试算得分与判定，用当前表格内容（不必先保存）。"""
        from PySide6.QtWidgets import QLineEdit
        page = QWidget(self.widget)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(CaptionLabel("文件名（可选，测文件名规则 / 白名单）", page))
        self.t_name = QLineEdit(page)
        self.t_name.setPlaceholderText("例如：预览_@DemoChannel.jpg")
        lay.addWidget(self.t_name)
        lay.addWidget(CaptionLabel("OCR 识别文本（把图上可能出现的文字贴进来）", page))
        self.t_in = TextEdit(page)
        self.t_in.setPlaceholderText("例如：免费发布 关注我的频道 t.me/demo 扫码进群")
        self.t_in.setMinimumHeight(110)
        lay.addWidget(self.t_in)
        btn = PrimaryPushButton("计算得分", page)
        btn.clicked.connect(self._run_test)
        lay.addWidget(btn)
        self.t_out = TextEdit(page)
        self.t_out.setReadOnly(True)
        self.t_out.setMinimumHeight(150)
        lay.addWidget(self.t_out, 1)
        return {"page": page, "label": "规则测试器"}

    def _run_test(self):
        rules = self._collect_rules()
        text = self.t_in.toPlainText()
        name = self.t_name.text().strip()
        ocr_cfg = self.cfg.get("ocr", {})
        high = float(ocr_cfg.get("score_high", 6))
        low = float(ocr_cfg.get("score_low", 3))
        lines = []

        wl_hits = af.match_rules(name, rules.get("whitelist_rules", [])) if name else []
        fn_hits = af.match_rules(name, rules.get("filename_rules", [])) if name else []
        hits, score, joined = af.score_ocr_texts([text], rules.get("ocr_keywords", [])) if text.strip() else ([], 0.0, "")

        if wl_hits:
            verdict, vtext = "clean", "正常（白名单放行）"
        elif fn_hits:
            verdict, vtext = "ad", "广告（文件名规则命中）"
        elif score >= high:
            verdict, vtext = "ad", f"广告（{score:.0f} ≥ 阈值 {high:.0f}）"
        elif score >= low:
            verdict, vtext = "suspect", f"疑似（{score:.0f} ≥ 阈值 {low:.0f}）"
        else:
            verdict, vtext = "clean", f"正常（{score:.0f} < 阈值 {low:.0f}）"

        lines.append(f"判定：{vtext}")
        lines.append(f"OCR 得分：{score:.0f}（广告 ≥{high:.0f} / 疑似 ≥{low:.0f}）")
        if wl_hits:
            lines.append("\n白名单命中：")
            lines += [f"  · {h['label']}（{h['detail']}）" for h in wl_hits]
        if fn_hits:
            lines.append("\n文件名规则命中：")
            lines += [f"  · {h['label']}（{h['detail']}）" for h in fn_hits]
        if hits:
            lines.append("\nOCR 词库命中：")
            lines += [f"  · {h['label']}" for h in hits]
        elif text.strip():
            lines.append("\nOCR 词库未命中任何规则")
        if not name and not text.strip():
            lines.append("（请输入文件名或 OCR 文本）")
        self.t_out.setPlainText("\n".join(lines))

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
            if "table" not in pg:
                continue
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


# ================================================================ 扫描历史
class HistoryDialog(WorkbenchDialog):
    """扫描历史：时间 / 文件夹 / 总数 / 命中 / 耗时（引擎侧 ad_filter_history.json）。"""

    def __init__(self, app):
        super().__init__(app)
        self.widget.setFixedWidth(960)
        self.viewLayout.addWidget(SubtitleLabel("扫描历史", self.widget))
        tip = CaptionLabel("最近 100 次扫描（新的在前）。规则变化后命中率的变化可在这里对比。", self.widget)
        tip.setWordWrap(True)
        self.viewLayout.addWidget(tip)

        from PySide6.QtWidgets import QHeaderView, QTableWidgetItem
        hist = list(reversed(af.load_history()))
        self.search = SearchLineEdit(self)
        self.search.setPlaceholderText('搜索时间或扫描目录')
        self.viewLayout.addWidget(self.search)
        summary = CaptionLabel(f'共 {len(hist)} 次扫描   ·   累计扫描 {sum(h.get("total", 0) for h in hist):,} 张图片', self)
        self.viewLayout.addWidget(summary)
        t = TableWidget(self.widget)
        t.setColumnCount(5)
        t.setHorizontalHeaderLabels(["时间", "文件夹", "扫描总数", "命中（广/疑/异）", "耗时"])
        t.verticalHeader().setVisible(False)
        t.setShowGrid(False)
        t.setBorderVisible(True)
        t.setBorderRadius(8)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        t.setWordWrap(False)
        t.setColumnWidth(0, 180)
        t.setColumnWidth(2, 90)
        t.setColumnWidth(3, 150)
        t.setColumnWidth(4, 76)
        t.setRowCount(len(hist))
        for r, h in enumerate(hist):
            t.setItem(r, 0, QTableWidgetItem(str(h.get("time", ""))))
            t.setItem(r, 1, QTableWidgetItem("、".join(h.get("roots", []))))
            t.setItem(r, 2, QTableWidgetItem(str(h.get("total", 0))))
            t.setItem(r, 3, QTableWidgetItem(
                f"{h.get('hits', 0)}（{h.get('ad', 0)} / {h.get('suspect', 0)} / {h.get('error', 0)}）"))
            dur = h.get("duration", 0)
            t.setItem(r, 4, QTableWidgetItem(f"{dur:.1f}s" if isinstance(dur, (int, float)) else str(dur)))
        t.setMinimumHeight(300)
        self.table = t
        self.search.textChanged.connect(self._filter)
        self.viewLayout.addWidget(t)
        if not hist:
            self.viewLayout.addWidget(CaptionLabel("还没有扫描记录，跑一次扫描后这里会出现历史。", self.widget))
        self.yesButton.setVisible(False)
        self.cancelButton.setText("关闭")
        self.widget.setMinimumWidth(960)
        from workbench_ui import style_dialog
        style_dialog(self)

    def _filter(self, text):
        query = text.strip().casefold()
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, not any(query in self.table.item(row, col).text().casefold()
                                                 for col in (0, 1)))


# ================================================================ 隔离区管理
class QuarantineDialog(WorkbenchDialog):
    """隔离区条目管理：查看 / 还原选中 / 彻底删除选中 / 全部还原。"""

    def __init__(self, app, q_root: Path):
        super().__init__(app)
        self.app = app
        self.q_root = q_root
        self.widget.setFixedWidth(960)
        self.viewLayout.addWidget(SubtitleLabel("隔离区管理", self.widget))
        self.lbl_path = CaptionLabel(str(q_root), self.widget)
        self.lbl_path.setWordWrap(True)
        self.viewLayout.addWidget(self.lbl_path)

        pathrow = QHBoxLayout()
        self.locations = ComboBox(self)
        known = list(dict.fromkeys([str(q_root)] + app.cfg.get('quarantine_locations', [])))
        self.locations.addItems(known)
        self.locations.setMinimumWidth(200)
        self.locations.currentTextChanged.connect(self._switch_location)
        self.btn_browse = PushButton('打开其他隔离区…', self)
        self.btn_browse.clicked.connect(self._browse_location)
        pathrow.addWidget(self.locations, 1)
        pathrow.addWidget(self.btn_browse)
        self.viewLayout.addLayout(pathrow)
        self.selection_info = CaptionLabel('请选择需要还原或删除的文件', self)
        self.viewLayout.addWidget(self.selection_info)

        from PySide6.QtWidgets import QHeaderView, QTableWidgetItem
        self._QTI = QTableWidgetItem
        t = TableWidget(self.widget)
        t.setColumnCount(4)
        t.setHorizontalHeaderLabels(["文件", "原位置", "隔离时间", "状态"])
        t.verticalHeader().setVisible(False)
        t.setShowGrid(False)
        t.setBorderVisible(True)
        t.setBorderRadius(8)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        t.setWordWrap(False)
        t.setColumnWidth(0, 160)
        t.setColumnWidth(2, 160)
        t.setColumnWidth(3, 110)
        t.setMinimumHeight(300)
        self.table = t
        t.itemSelectionChanged.connect(self._selection_changed)
        self.viewLayout.addWidget(t)

        row = QHBoxLayout()
        self.btn_rs = TransparentPushButton(FIF.BACK_TO_WINDOW, "还原选中", self.widget)
        self.btn_rs.clicked.connect(self._restore_selected)
        self.btn_dl = TransparentPushButton(FIF.DELETE, "彻底删除选中", self.widget)
        self.btn_dl.clicked.connect(self._delete_selected)
        self.btn_ra = TransparentPushButton("全部还原", self.widget)
        self.btn_ra.clicked.connect(self._restore_all)
        self.btn_rf = TransparentPushButton(FIF.SYNC, "刷新", self.widget)
        self.btn_rf.clicked.connect(self.reload)
        for b in (self.btn_rs, self.btn_dl, self.btn_ra, self.btn_rf):
            row.addWidget(b)
        row.addStretch(1)
        self.viewLayout.addLayout(row)

        self.yesButton.setVisible(False)
        self.cancelButton.setText("关闭")
        self.widget.setMinimumWidth(960)
        from workbench_ui import style_dialog
        style_dialog(self)
        self.reload()

    def _switch_location(self, directory):
        if directory and not self.app._file_busy:
            self.q_root = Path(directory)
            self.reload()

    def _browse_location(self):
        directory = QFileDialog.getExistingDirectory(self, '打开已有隔离区（含 manifest.json）', str(self.q_root))
        if directory:
            if not (Path(directory) / 'manifest.json').exists():
                self.app._info('无法打开隔离区', '所选目录中没有隔离清单 manifest.json。')
                return
            if self.locations.findText(directory) < 0:
                self.locations.addItem(directory)
            self.locations.setCurrentText(directory)

    def _selection_changed(self):
        count = len({i.row() for i in self.table.selectedIndexes()})
        self.selection_info.setText(f'共 {self.table.rowCount()} 项   ·   已选 {count} 项')
        busy = self.app._file_busy
        self.btn_rs.setEnabled(bool(count) and not busy)
        self.btn_dl.setEnabled(bool(count) and not busy)
        self.btn_ra.setEnabled(bool(self.table.rowCount()) and not busy)

    def reload(self):
        self.entries = af.list_quarantine(self.q_root)
        t = self.table
        t.setRowCount(len(self.entries))
        for r, e in enumerate(self.entries):
            dst = e.get("dst", "")
            it0 = self._QTI(Path(dst).name)
            it0.setData(Qt.UserRole, dst)
            t.setItem(r, 0, it0)
            t.setItem(r, 1, self._QTI(e.get("src", "")))
            t.setItem(r, 2, self._QTI(e.get("time", "")))
            t.setItem(r, 3, self._QTI("已隔离" if e.get("exists") else "文件丢失"))
        self.lbl_path.setText(f"{self.q_root}   ·   {len(self.entries)} 项")
        for b in (self.locations, self.btn_browse, self.btn_rf):
            b.setEnabled(not self.app._file_busy)
        self._selection_changed()

    def _selected_dsts(self):
        rows = {i.row() for i in self.table.selectedIndexes()}
        return [self.entries[r]["dst"] for r in sorted(rows) if r < len(self.entries)]

    def _run(self, fn, dsts, verb):
        if not dsts or self.app._file_busy or self.app._disposing:
            return
        self.app._file_busy = True
        self.app._sync_actions()
        for b in (self.btn_rs, self.btn_dl, self.btn_ra, self.locations, self.btn_browse, self.btn_rf):
            b.setEnabled(False)

        def work():
            try:
                n, errors = fn(self.q_root, dsts, af.log_jsonl)
            except Exception as e:
                n, errors = 0, [str(e)]
            self.app.scan_q.put(("quarantine_op", (verb, n, errors)))

        threading.Thread(target=work, daemon=True).start()

    def _restore_selected(self):
        self._run(af.restore_quarantine_items, self._selected_dsts(), "还原")

    def _delete_selected(self):
        dsts = self._selected_dsts()
        if not dsts:
            return
        if not self.app._ask("彻底删除", f"将永久删除隔离区中 {len(dsts)} 个文件，不可恢复！\n继续？"):
            return
        self._run(af.delete_quarantine_items, dsts, "删除")

    def _restore_all(self):
        self._run(af.restore_quarantine_items,
                  [e["dst"] for e in self.entries], "还原")


# ================================================================ 入口
def _qc(css) -> "QColor":
    """CSS 颜色串 → QColor。**QPainter 自绘必须用它，不能直接 QColor(str)**：
    QColor 只认 #RRGGBB / 整数 alpha，不认 CSS 的 rgba(r,g,b,0.06) 浮点语法，
    解析失败会得到无效色，QPainter 按黑色画——"浅色主题下选中卡变黑"的真正根因
    （选中态要叠一层 clay_fill = rgba(...,0.06)，于是整卡被刷黑）。"""
    s = str(css).strip()
    m = re.match(r"^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)"
                 r"(?:\s*,\s*([\d.]+))?\s*\)$", s)
    if m:
        r, g, b = (int(float(m.group(i))) for i in (1, 2, 3))
        a = m.group(4)
        if a is None:
            return QColor(r, g, b)
        af_ = float(a)
        alpha = int(round(af_ * 255)) if af_ <= 1.0 else int(af_)
        return QColor(r, g, b, max(0, min(255, alpha)))
    c = QColor(s)
    return c if c.isValid() else QColor(0, 0, 0, 0)


def app_icon_pixmap(size: int = 64) -> "QPixmap":
    """应用图标：圆角陶土橙底 + 白色扫帚 + 清扫星点（ImageGen 按 Material 风格
    设计共识生成：具象符号+情境元素、高对比、小尺寸可辨；水印已清理）。
    数据 base64 内嵌于 icon_embed.py——onefile 打包自包含，无需外部文件。
    语义：清扫广告图，而非广告本身。"""
    import base64
    import io
    from icon_embed import ICON_B64
    data = base64.b64decode(ICON_B64)
    qimg = QImage.fromData(data, "PNG")
    return QPixmap.fromImage(qimg).scaled(
        size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def run_gui(return_app=False):
    qapp = QApplication.instance() or QApplication(sys.argv)
    setThemeColor("#c6613f")   # CDS 官方主品牌色（陶土橙深阶）
    qapp.setWindowIcon(QIcon(app_icon_pixmap(256)))   # 任务栏/系统显示的窗口图标
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
    _c, _cf, _l, _h = af.CACHE_PATH, af.CONFIG_PATH, af.LOG_PATH, af.HISTORY_PATH
    af.CACHE_PATH = tmp / "cache.db"
    af.CONFIG_PATH = tmp / "cfg.json"
    af.LOG_PATH = tmp / "log.jsonl"
    af.HISTORY_PATH = tmp / "hist.json"
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
    app._quarantine_override = tmp / 'quarantine'
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
        app.select_all()
        check("全选再按=取消全选", len(app.model.checked) == 0)
        app.select_all()
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
                # ←→ 方向键翻页（清单 4：方向键联动）
                p1 = lb.path
                lb.keyPressEvent(type("K", (), {"key": lambda self: Qt.Key_Right})())
                check("Lightbox 方向键翻页", lb.path != p1 or app.model.rowCount() < 2, lb.path)
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

        # —— 卡片 tooltip 与拖拽 mime 解析（清单 4 / 2）——
        tip = app.model.data(app.model.index(0, 0), Qt.ToolTipRole)
        check("卡片 tooltip 有内容", bool(tip) and len(str(tip)) > 10, str(tip)[:40])
        from PySide6.QtCore import QMimeData, QUrl
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(str(scan_dir)),
                    QUrl.fromLocalFile(str(scan_dir / "sub"))])
        dirs = af.collect_dirs(md)
        check("拖拽文件夹解析", len(dirs) >= 1
              and all(Path(d).is_dir() for d in dirs), f"{dirs}")

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
        ipath = app._quarantine_root()
        check("隔离区指向专用测试目录", ipath == tmp / 'quarantine' and ipath.exists(), str(ipath))
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
        # 清理隔离产物（仅当目录里全是本次测试产生的文件，防止误删真实隔离区）
        try:
            if ipath.exists():
                leftovers = [p for p in ipath.rglob("*") if p.is_file() and p.name != "manifest.json"]
                if not leftovers:
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

        # —— 排序 ——
        app.f_sort.setCurrentIndex(1)  # 名称
        pump(0.2)
        names = [Path(it["path"]).name.lower() for it in app.model.items]
        check("按名称排序", names == sorted(names), names[:3])
        app.f_sort.setCurrentIndex(4)  # 分数
        pump(0.2)
        scores = [it.get("score", 0) for it in app.model.items]
        check("按分数排序", scores == sorted(scores, reverse=True), scores[:3])
        app.f_sort.setCurrentIndex(0)
        pump(0.2)

        # —— 扫描历史 ——
        hist = af.load_history()
        check("扫描历史已记录", len(hist) >= 2, f"n={len(hist)}")
        check("历史记录含耗时与命中",
              all("duration" in h and "hits" in h for h in hist))
        dlg_h = HistoryDialog(app)
        check("历史弹窗行数=记录数", dlg_h.table.rowCount() == len(hist),
              f"{dlg_h.table.rowCount()} vs {len(hist)}")
        dlg_h.close()

        # —— 导出报告 ——
        out_csv = tmp / "report.csv"
        _orig_save = QFileDialog.getSaveFileName
        _orig_open = QFileDialog.getOpenFileName
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(out_csv), "csv"))
        try:
            app.export_report()
        finally:
            QFileDialog.getSaveFileName = _orig_save
        check("导出 CSV 生成", out_csv.exists())
        if out_csv.exists():
            rows = out_csv.read_text(encoding="utf-8-sig").strip().splitlines()
            check("CSV 行数=命中+表头", len(rows) == len(app.model.items) + 1,
                  f"rows={len(rows)} items={len(app.model.items)}")

        # —— Lightbox 图内处置（保留 / 标记广告）——
        app.grid.setCurrentIndex(app.model.index(0, 0))
        p0 = app.model.items[0]["path"]
        lb2 = Lightbox(app, p0, parent=app)
        pump(0.3)
        n_bm = len(app.items)
        lb2._mark(False)
        pump(0.2)
        check("Lightbox 保留移出列表", len(app.items) == n_bm - 1,
              f"{n_bm}->{len(app.items)}")
        check("Lightbox 保留后跳到下一张", lb2.path != p0, lb2.path)
        lb2._mark(True)
        pump(0.2)
        check("Lightbox 标记广告=勾选", lb2.path in app.model.checked)
        try:
            lb2.close()
        except RuntimeError:
            pass
        pump(0.2)

        # —— 撤销处置（隔离）——
        app.seg_disposal.set_index(0)
        app.select_all()
        pump(0.15)
        app.execute_disposal()
        pump(until=lambda: "处置完成" in app.status.text(), timeout=60)
        check("隔离后撤销按钮可用", app.btn_undo.isEnabled())
        undo_srcs = list(app._undo_batch[1]) if app._undo_batch else []
        app.undo_disposal()
        pump(until=lambda: "恢复" in app.status.text(), timeout=60)
        check("撤销处置完成", "恢复" in app.status.text(), app.status.text())
        check("撤销后文件回原位", all(Path(p).exists() for p in undo_srcs) and len(undo_srcs) > 0,
              f"n={len(undo_srcs)}")
        ipath2 = app._quarantine_root()
        try:
            if ipath2.exists():
                leftovers = [p for p in ipath2.rglob("*") if p.is_file() and p.name != "manifest.json"]
                if not leftovers:
                    shutil.rmtree(ipath2, ignore_errors=True)
        except Exception:
            pass

        # —— 隔离区管理对话框（还原选中 / 删除选中）——
        qtest = tmp / "qtest"
        qtest.mkdir()
        (qtest / "a.jpg").write_bytes(b"x")
        (qtest / "b.jpg").write_bytes(b"y")
        (qtest / "manifest.json").write_text(json.dumps({"items": [
            {"src": str(scan_dir / "a.jpg"), "dst": str(qtest / "a.jpg"), "time": "2026-01-01 00:00:00"},
            {"src": str(scan_dir / "b.jpg"), "dst": str(qtest / "b.jpg"), "time": "2026-01-01 00:00:01"},
        ]}, ensure_ascii=False), encoding="utf-8")
        dlg_q = QuarantineDialog(app, qtest)
        app._qdlg = dlg_q
        check("隔离区条目加载", dlg_q.table.rowCount() == 2, f"rows={dlg_q.table.rowCount()}")
        dlg_q.table.selectRow(0)
        pump(0.1)
        dlg_q._restore_selected()
        ok_rs = pump(until=lambda: (scan_dir / "a.jpg").exists(), timeout=10)
        check("隔离区还原选中", ok_rs and (scan_dir / "a.jpg").exists())
        pump(0.3)
        check("还原后清单剩 1 项", dlg_q.table.rowCount() == 1, f"rows={dlg_q.table.rowCount()}")
        dlg_q.table.selectRow(0)
        pump(0.1)
        dlg_q._delete_selected()
        ok_dl = pump(until=lambda: not (qtest / "b.jpg").exists(), timeout=10)
        check("隔离区彻底删除选中", ok_dl)
        app._qdlg = None
        dlg_q.close()

        # —— 规则测试器 ——
        dlg3 = app.build_rule_editor()
        dlg3.pivot.setCurrentItem("tester")
        pump(0.15)
        dlg3.t_name.setText("normal.jpg")
        dlg3.t_in.setPlainText("免费发布 关注我的频道 t.me/demo 扫码关注")
        dlg3._run_test()
        out_t = dlg3.t_out.toPlainText()
        check("测试器判定广告", "广告" in out_t, out_t[:60])
        check("测试器显示得分", "OCR 得分" in out_t)
        dlg3.t_in.setPlainText("今天天气不错适合出门")
        dlg3._run_test()
        check("测试器判定正常", "正常" in dlg3.t_out.toPlainText(),
              dlg3.t_out.toPlainText()[:60])
        pt = dlg3._pages["whitelist_rules"]["table"]
        r = pt.rowCount()
        pt.setRowCount(r + 1)
        pt.setItem(r, 0, _QI("白样本"))
        pt.setItem(r, 1, _QI("测试白名单"))
        dlg3.t_name.setText("白样本_01.jpg")
        dlg3.t_in.setPlainText("t.me/demo 免费发布")
        dlg3._run_test()
        check("白名单优先放行", "白名单" in dlg3.t_out.toPlainText(),
              dlg3.t_out.toPlainText()[:60])
        dlg3.close()

        # —— 配置导入导出 ——
        cfg_backup = json.loads(json.dumps(app.cfg))
        dlg4 = app.build_config()
        cfg_out = tmp / "cfg_export.json"
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(cfg_out), "json"))
        try:
            dlg4._export_cfg()
        finally:
            QFileDialog.getSaveFileName = _orig_save
        check("配置导出文件生成", cfg_out.exists())
        exported = json.loads(cfg_out.read_text(encoding="utf-8")) if cfg_out.exists() else {}
        check("导出配置含词库", "ocr_keywords" in exported)
        exported["ocr_keywords"] = exported.get("ocr_keywords", [])[:1]
        cfg_in = tmp / "cfg_import.json"
        cfg_in.write_text(json.dumps(exported, ensure_ascii=False), encoding="utf-8")
        QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(cfg_in), "json"))
        try:
            dlg4._import_cfg()
        finally:
            QFileDialog.getOpenFileName = _orig_open
        check("导入后词库生效", len(app.cfg.get("ocr_keywords", [])) == 1,
              f"n={len(app.cfg.get('ocr_keywords', []))}")
        app.cfg.clear()
        app.cfg.update(cfg_backup)
        dlg4.close()

        # —— 标题栏几何：三按钮必须贴右边缘，且随窗口宽度同步 ——
        tb = app._titlebar
        check("标题栏宽度=窗口宽度", tb.width() == app.width(),
              f"tb={tb.width()} win={app.width()}")
        cr = tb.btn_close.geometry().right()
        check("关闭按钮贴右边缘", abs(cr - (app.width() - 1)) <= 2, f"right={cr} win={app.width()}")
        check("按钮顺序 最小<最大<关闭",
              tb.btn_min.geometry().x() < tb.btn_max.geometry().x() < tb.btn_close.geometry().x(),
              f"{tb.btn_min.geometry().x()}/{tb.btn_max.geometry().x()}/{tb.btn_close.geometry().x()}")
        bg = tb.brand.geometry()
        bc = bg.x() + bg.width() // 2
        check("品牌标题居中(偏差≤3)", abs(bc - app.width() // 2) <= 3,
              f"brand_center={bc} win_center={app.width() // 2}")
        w0, h0 = app.width(), app.height()
        app.resize(1200, 800)
        pump(0.4)
        cr2 = tb.btn_close.geometry().right()
        check("缩放后标题栏同步", tb.width() == app.width() and abs(cr2 - (app.width() - 1)) <= 2,
              f"tb={tb.width()} win={app.width()} right={cr2}")
        app.resize(w0, h0)
        pump(0.3)

        # —— 单一固定布局：无侧栏（导航与顶栏/底栏重复，用户决定删除），详情常驻 + 底部停靠 ——
        check("固定布局：无侧栏", app.lm.sidebar is None)
        check("固定布局：详情常驻", app.detail.isVisible() and 320 <= app.detail.width() <= 520,
              f"w={app.detail.width()}")
        check("固定布局：底部停靠", app.lm._float_bar is None)
        check("固定布局：无布局按钮", not hasattr(app, "btn_layout"))
        # set_layout 幂等：任意旧调用都不改变形态、勾选不丢
        app.model.checked = {"__t1__", "__t2__"}
        app.checked = app.model.checked
        n_sel = len(app.model.checked)
        for _ in range(3):
            app.set_layout("gallery"); pump(0.15); app.set_layout("studio"); pump(0.15)
        check("set_layout 幂等形态不变", app.layout_mode == "workbench"
              and app.detail.isVisible() and app.lm.sidebar is None, app.layout_mode)
        check("重复调用勾选保留", len(app.model.checked) == n_sel,
              f"{n_sel}->{len(app.model.checked)}")

        # —— 命令面板（⌘K）：布局命令已随骨架一起删除 ——
        app.open_palette()
        pump(0.25)
        has_layout_cmd = any("布局" in app._palette.list.item(i).text()
                             for i in range(app._palette.list.count()))
        check("命令面板无布局命令", not has_layout_cmd)
        # 右键菜单：打开源文件文件夹（用户需求）——方法存在且能正确解析路径
        check("右键打开源文件文件夹入口", hasattr(app, "open_item_folder"))
        # 框选矩形自绘：原生绘制不认 palette（windows11 style 画深色块）
        check("框选矩形为自绘 overlay", app._sel_band is not None
              and not app.grid.isSelectionRectVisible())
        check("框选 overlay 配色为品牌橙", "217,119,87" in app._sel_band.styleSheet(),
              app._sel_band.styleSheet()[:50])
        # 锁死"QColor 不能直接吃 rgba 浮点 alpha"这个坑（曾致选中卡整卡被刷黑）
        parsed_color = _qc("rgba(217,119,87,0.06)")
        check("颜色解析 rgba 浮点 alpha", parsed_color.isValid() and parsed_color.red() == 217 and parsed_color.alpha() == 15,
              str(parsed_color.getRgb()))
        check("颜色解析 hex 正常", _qc("#FFFFFF").getRgb() == (255, 255, 255, 255))
        if app.model.items:
            import pathlib as _pl
            _folder = _pl.Path(app.model.items[0]["path"]).parent
            check("源文件夹路径可解析", _folder.is_dir(), str(_folder)[:40])
        check("命令面板打开", app._palette is not None and app._palette.list.count() >= 12,
              f"n={app._palette.list.count() if app._palette else 0}")
        if app._palette is not None:
            app._palette.edit.setText("隔离")
            pump(0.15)
            check("命令面板过滤", 0 < app._palette.list.count() < 17,
                  f"n={app._palette.list.count()}")
            app._palette.edit.setText("")
            pump(0.1)

        # —— 空状态 ——
        app.items = []
        app.model.reset_items([])
        app.refresh()
        pump(0.3)
        check("无结果时空状态显示", app._empty.isVisible())

        # —— 色彩层级（语义色体系）——
        import cds_tokens as _CDST
        _dark = isDarkTheme()
        # 1. 状态胶囊：完成 → 绿；进行中 → 品牌橙；就绪 → 灰
        app.set_status("扫描完成 · 命中 2（广告 1 / 疑似 1）")
        pump(0.1)
        ok_fg, ok_fill, _ = _CDST.sem(_dark, "ok")
        check("状态胶囊完成=绿", app._status_role == "ok" and ok_fill in app.status_pill.styleSheet())
        app.set_status("正在收集文件…")
        pump(0.1)
        clay_fg, clay_fill, _ = _CDST.sem(_dark, "clay")
        check("状态胶囊进行中=橙", app._status_role == "clay" and clay_fill in app.status_pill.styleSheet())
        app.set_status("已取消")
        pump(0.1)
        check("状态胶囊取消=琥珀", app._status_role == "warn")
        app.set_status("就绪")
        pump(0.1)
        check("状态胶囊就绪=灰", app._status_role == "gray")
        # 2. 计数药丸：已选 >0 → 品牌橙，0 → 灰
        app.checked.clear()
        app.checked.add("some://path")
        app._update_count_label()
        pump(0.1)
        check("已选药丸非零=橙", clay_fill in app.pill_sel.styleSheet())
        app.checked.clear()
        app._update_count_label()
        gray_fg, gray_fill, _ = _CDST.sem(_dark, "gray")
        check("已选药丸为零=灰", gray_fill in app.pill_sel.styleSheet(),
              f"expect {gray_fill} got {app.pill_sel.styleSheet()[:90]}")
        # 3. 判定色与品牌橙分离：广告徽标用红系，不再复用 clay
        ad_fg, _, _ = _CDST.sem(_dark, "ad")
        clay_fg2 = _CDST.c(_dark, CDSTT.clay)
        check("广告判定色≠品牌橙", ad_fg.lower() != clay_fg2.lower(),
              f"ad={ad_fg} clay={clay_fg2}")
        # 4. 详情头徽标语义化
        app._paint_detail_badge({"verdict": "ad", "score": 98})
        pump(0.1)
        check("详情徽标含判定文字", "广告" in app.detail_badge.text())

    finally:
        app._cache.conn.close()
        app.close()
        qapp.processEvents()
        af.CACHE_PATH, af.CONFIG_PATH, af.LOG_PATH, af.HISTORY_PATH = _c, _cf, _l, _h
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, ok in results if ok)
    print(f"\nGUI 功能自测：{passed}/{len(results)} 通过" + ("" if passed == len(results) else " ❌"))
    return 0 if passed == len(results) else 1
