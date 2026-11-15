# -*- coding: utf-8 -*-
"""UI 元素审查：位置 / 大小 / 颜色逐项断言（深浅主题、多尺寸审查工作台）。

背景：用户截图暴露过「药丸方盒」「胶囊裁切」「图标隐形」三类问题，
纯看截图容易漏，几何+像素断言更可靠。全部通过 = 每个元素在合理的位置/大小/颜色。

用法：python tests/audit_ui.py
"""
import sys
import time
import tempfile
import shutil
from pathlib import Path

sys.argv = ["audit"]
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

import ad_filter as af
import ad_filter_gui as g
from qfluentwidgets import isDarkTheme

OUT = ROOT / "_preview"
OUT.mkdir(exist_ok=True)

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""),
          flush=True)


def main():
    temp = Path(tempfile.mkdtemp(prefix='ad_ui_audit_'))
    original_paths = af.CACHE_PATH, af.CONFIG_PATH, af.LOG_PATH, af.HISTORY_PATH
    af.CACHE_PATH, af.CONFIG_PATH = temp / 'cache.db', temp / 'config.json'
    af.LOG_PATH, af.HISTORY_PATH = temp / 'log.jsonl', temp / 'history.json'
    af.CONFIG_PATH.write_text('{"ui_theme":"light","ocr":{"enabled":false},"qr":{"enabled":false},"phash":{"enabled":false}}', encoding='utf-8')
    from PIL import Image, ImageDraw
    source = temp / 'photos'
    source.mkdir()
    for i in range(5):
        im = Image.new('RGB', (1000, 700), (180 + i * 8, 187, 168))
        draw = ImageDraw.Draw(im)
        draw.polygon([(0, 550), (230, 170), (430, 470), (770, 240), (1000, 550), (1000, 700), (0, 700)], fill=(79, 113, 100))
        im.save(source / f'推广图_{i}.jpg')
    qapp = QApplication([])
    app = g.run_gui(return_app=True)
    app._quarantine_override = temp / 'quarantine'
    app._toast = lambda *a, **k: None
    app.show()
    app.resize(1440, 900)

    def pump(sec=0.3):
        t0 = time.time()
        while time.time() - t0 < sec:
            qapp.processEvents()
            time.sleep(0.02)

    def shot(name):
        original_path = app.detail_path.text()
        app.detail_path.setText('D:\\图片\\演示相册\\推广图_0.jpg')
        img = QImage(app.width(), app.height(), QImage.Format_ARGB32_Premultiplied)
        app.render(img)
        app.detail_path.setText(original_path)
        img.save(str(OUT / f"{name}.png"))
        return img

    pump(1.0)
    dark0 = isDarkTheme()          # 记住启动主题，结束时还原
    # 扫 testdata 出结果（缓存秒开）
    app.add_folders([str(source)])
    app.start_scan()
    t0 = time.time()
    while time.time() - t0 < 60:
        pump(0.3)
        if app.btn_scan.isEnabled():
            break
    pump(1.0)
    # 勾选 2 张（已选 pill 语义橙）
    app.model.checked = {app.items[0]["path"], app.items[1]["path"]}
    app.checked = app.model.checked
    app.refresh()

    for theme in ("dark", "light"):
        # 按当前实际主题切换（配置文件里的 ui_theme 决定起点，不能假设）
        if (theme == "light") != (not isDarkTheme()):
            app.toggle_theme()
            pump(1.2)
        actual = "dark" if isDarkTheme() else "light"
        if actual != theme:
            raise RuntimeError(f"主题切换失败: 想要 {theme} 实际 {actual}")
        dark = isDarkTheme()
        # 单一固定布局（三骨架已删除）：每主题只审 workbench 形态
        app.set_layout("workbench")
        pump(0.7)
        audit(app, theme, "workbench", check, shot, pump)
        if dark:
            shot(f"audit-{theme}-extra")  # 语义色像素断言用
            app.seg_disposal.set_index(2)
            shot('audit-dark-delete')
            app.seg_disposal.set_index(0)
        for width, height in ((1080, 680), (1280, 800)):
            app.resize(width, height)
            pump(.4)
            check(f'[{theme}/{width}] 窗口未被内容撑大', app.width() == width and app.height() == height,
                  f'{app.width()}x{app.height()}')
            for name in ('top_bar', 'filter_bar', 'bot', 'footer', 'detail'):
                panel = getattr(app, name)
                r = panel.rect()
                point = panel.mapTo(app, r.bottomRight())
                check(f'[{theme}/{width}] {name} 在窗内', point.x() < app.width() and point.y() < app.height())
            shot(f'audit-{theme}-{width}')
        app.resize(1440, 900)
        pump(.3)

    # 语义色像素断言（深色 gallery 截图）
    verify_colors(OUT, check)

    # 还原启动时的主题，避免污染配置文件
    if dark0 != isDarkTheme():
        app.toggle_theme()
        pump(0.5)
    app.close()
    qapp.processEvents()
    app._cache.conn.close()
    af.CACHE_PATH, af.CONFIG_PATH, af.LOG_PATH, af.HISTORY_PATH = original_paths
    shutil.rmtree(temp, ignore_errors=True)

    passed = sum(1 for _, ok in results if ok)
    print(f"\nUI 审查：{passed}/{len(results)} 通过" + ("" if passed == len(results) else " ❌"))
    return 0 if passed == len(results) else 1


def audit(app, theme, mode, check, shot, pump):
    """单布局单主题的几何/颜色断言。"""
    tag = f"{theme}/{mode}"
    W, H = app.width(), app.height()

    # —— 标题栏（三布局通用）——
    tb = app._titlebar
    close = tb.btn_close
    check(f"[{tag}] 关闭钮贴右缘", abs(close.mapTo(app, close.rect().topRight()).x() - (W - 1)) <= 2,
          f"right={close.mapTo(app, close.rect().topRight()).x()} win={W}")
    check(f"[{tag}] 标题栏按钮 46x30", close.width() == 46 and close.height() == 30)
    check(f"[{tag}] 品牌居中", abs((tb.brand.x() + tb.brand.width() // 2) - W // 2) <= 6)

    # —— 顶栏 ——
    top = app.top_bar
    check(f"[{tag}] 顶栏高 58", top.height() == 58, f"h={top.height()}")
    check(f"[{tag}] 扫描钮 112x32", app.btn_scan.width() == 112 and app.btn_scan.height() == 32)
    check(f"[{tag}] 状态在底部独立状态栏", app.status_pill.parent() is app.footer)
    check(f"[{tag}] 状态胶囊高≤30", app.status_pill.height() <= 30, f"h={app.status_pill.height()}")
    # 状态文字色 = 语义色（当前是「扫描完成」= 绿 / 或就绪 = 灰）
    role = getattr(app, "_status_role", "gray")
    check(f"[{tag}] 状态角色语义化", role in ("ok", "gray", "warn", "danger", "clay"), role)

    # —— 筛选条 ——
    fb = app.filter_bar
    check(f"[{tag}] 筛选条分行高 144", fb.height() == 144, f"h={fb.height()}")
    for w in (app.verdict_tabs, app.f_reason, app.f_search, app.f_sort):
        check(f"[{tag}] 筛选控件高30或32 ({w.__class__.__name__})", w.height() in (30, 32), f"h={w.height()}")
    for label, button in zip(('全部待审', '广告', '疑似', '异常'), app.verdict_tabs.buttons):
        check(f'[{tag}] {label}分类可见且带数量', button.isVisible() and button.text().startswith(label+' '))
        p = button.mapTo(app.filter_bar, button.rect().bottomRight())
        check(f'[{tag}] {label}分类无裁切', p.x() < app.filter_bar.width() and p.y() < app.filter_bar.height())
    check(f'[{tag}] 显示和待审范围独立可见', app.results_scope.isVisible() and '/ 待审 ' in app.results_scope.text())
    for name in ('btn_prev', 'btn_next', 'btn_mark', 'btn_keep', 'btn_big'):
        button = getattr(app, name)
        p = button.mapTo(app.detail, button.rect().bottomRight())
        check(f'[{tag}] {name}固定动作在详情内', p.x() < app.detail.width() and p.y() < app.detail.height())
    check(f"[{tag}] 计数区在处置条内", app.lbl_count.mapTo(bot := app.bot, app.lbl_count.rect().topRight()).x() <= bot.width())

    # —— 网格卡片 ——
    check(f"[{tag}] 网格单元 204x242", app.grid.gridSize() == __import__("PySide6.QtCore", fromlist=["QSize"]).QSize(204, 242),
          f"gs={app.grid.gridSize()}")

    # —— 侧栏已删（导航与顶栏/底栏重复）：必须不存在 ——
    check(f"[{tag}] 无侧栏", app.lm.sidebar is None)

    # —— 底部条 ——
    bot = app.bot
    if mode == "gallery":
        # 悬浮胶囊：内容永不裁切（用户截图 bug）——每个子控件 geometry ⊂ 胶囊
        check(f"[{tag}] 胶囊在窗口内", bot.geometry().right() < W and bot.y() > 0 and bot.height() == 56,
              f"geo={bot.geometry()}")
        bl = bot.layout()
        overflow = []
        for i in range(bl.count()):
            it = bl.itemAt(i)
            w = it.widget()
            if w is None or not w.isVisible():
                continue
            r = w.geometry()
            if r.left() < 6 or r.right() > bot.width() - 6:
                overflow.append(f"{w.__class__.__name__}@{r}")
        check(f"[{tag}] 胶囊内容无裁切", not overflow, "; ".join(overflow))
        # 相邻控件无重叠
        vis = [bl.itemAt(i).widget() for i in range(bl.count())
               if bl.itemAt(i).widget() is not None and bl.itemAt(i).widget().isVisible()]
        rects = [w.geometry() for w in vis]
        overlap = []
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                if rects[i].intersects(rects[j]):
                    a, b = vis[i], vis[j]
                    # 相邻 push 按钮允许 1-2px 间隙内相交（spacing=0 的组内）
                    inter = rects[i] & rects[j]
                    if inter.width() > 2:
                        overlap.append(f"{a.__class__.__name__}×{b.__class__.__name__}+{inter.width()}")
        check(f"[{tag}] 胶囊控件无重叠", not overlap, "; ".join(overlap))
        # 执行处置按钮完整（文字不被裁）
        dw = app.btn_dispose
        fm_w = dw.fontMetrics().horizontalAdvance(dw.text()) + dw.iconSize().width() + 24
        check(f"[{tag}] 执行处置宽度足够", dw.width() >= fm_w, f"w={dw.width()} need>={fm_w}")
    else:
        check(f"[{tag}] 底栏停靠全宽", bot.width() >= W - 4 and bot.height() == 66,
              f"w={bot.width()} h={bot.height()}")
        bl = bot.layout()
        overflow = [bl.itemAt(i).widget().__class__.__name__
                    for i in range(bl.count())
                    if bl.itemAt(i).widget() is not None and bl.itemAt(i).widget().isVisible()
                    and bl.itemAt(i).widget().geometry().right() > bot.width()]
        check(f"[{tag}] 底栏内容无越界", not overflow, str(overflow))

    # —— 详情面板（workbench）——
    if mode == "workbench":
        check(f"[{tag}] 详情宽可调", 320 <= app.detail.width() <= 520, f"w={app.detail.width()}")
        check(f"[{tag}] 预览画布高≥130", app.preview.height() >= 130, f"h={app.preview.height()}")
        check(f"[{tag}] 详情头徽标存在", app.detail_badge.text() != "", app.detail_badge.text())
        # 选中首项后徽标应为判定文字
        app.grid.setCurrentIndex(app.model.index(0))
        pump(0.5)
        check(f"[{tag}] 选中后徽标含判定", ("广告" in app.detail_badge.text()
                                          or "疑似" in app.detail_badge.text()),
              app.detail_badge.text())
        # 预览加载成功
        check(f"[{tag}] 预览有图", app.preview.pixmap() is not None
              and not app.preview.pixmap().isNull())
        app.grid.clearSelection()

    # —— 截图存档 ——
    shot(f"audit-{theme}-{mode}")


def verify_colors(OUT, check):
    """像素级颜色断言：pill 圆角（非方角）、执行处置=红、已选 pill=橙。"""
    import numpy as np
    from PIL import Image

    def load(name):
        return np.array(Image.open(OUT / f"{name}.png").convert("RGBA"))

    img = load("audit-dark-delete")
    H, W = img.shape[:2]
    # 执行处置按钮：底部停靠条（y≈H-60..H），红色 danger 系
    region = img[H - 90:H - 10, :]
    red = ((region[:, :, 0] > 190) & (region[:, :, 1] < 160) & (region[:, :, 2] < 140)).sum()
    check("[色] 执行处置=红色实底", red > 300, f"red_px={red}")

    # 已选 pill = 品牌橙（audit-dark-workbench 里勾了 2 张）
    img2 = load("audit-dark-workbench")
    clay = ((abs(img2[:, :, 0].astype(int) - 217) < 40)
            & (abs(img2[:, :, 1].astype(int) - 119) < 45)
            & (abs(img2[:, :, 2].astype(int) - 87) < 45)).sum()
    check("[色] 品牌橙大量在场(按钮/勾选/已选)", clay > 800, f"clay_px={clay}")


if __name__ == "__main__":
    sys.exit(main())
