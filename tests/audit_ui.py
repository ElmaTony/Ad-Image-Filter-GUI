# -*- coding: utf-8 -*-
"""UI 元素审查：位置 / 大小 / 颜色逐项断言（深浅主题、多尺寸审查工作台）。

背景：用户截图暴露过「药丸方盒」「胶囊裁切」「图标隐形」三类问题，
纯看截图容易漏，几何+像素断言更可靠。全部通过 = 每个元素在合理的位置/大小/颜色。

用法：python tests/audit_ui.py
"""
import argparse
import os
import sys
import time
import tempfile
import shutil
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

import ad_filter as af
import ad_filter_gui as g
from qfluentwidgets import isDarkTheme

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""),
          flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", help="可选：保存审查截图的目标目录（不传时仅写入临时目录并自动清理）")
    args = parser.parse_args()
    temp = Path(tempfile.mkdtemp(prefix='ad_ui_audit_'))
    out = Path(args.output) if args.output else (temp / "preview")
    out.mkdir(parents=True, exist_ok=True)
    original_paths = af.CACHE_PATH, af.CONFIG_PATH, af.LOG_PATH, af.HISTORY_PATH
    qapp = None
    app = None
    try:
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
        qapp = QApplication.instance() or QApplication([])
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
            img.save(str(out / f"{name}.png"))
            return img

        pump(1.0)
        dark0 = isDarkTheme()          # 记住启动主题，结束时还原
        app.add_folders([str(source)])
        app.start_scan()
        t0 = time.time()
        while time.time() - t0 < 60:
            pump(0.3)
            if app.btn_scan.isEnabled():
                break
        pump(1.0)
        if len(app.items) >= 2:
            app.model.checked = {app.items[0]["path"], app.items[1]["path"]}
            app.checked = app.model.checked
            app.refresh()

        for theme in ("dark", "light"):
            if (theme == "light") != (not isDarkTheme()):
                app.toggle_theme()
                pump(1.2)
            actual = "dark" if isDarkTheme() else "light"
            if actual != theme:
                raise RuntimeError(f"主题切换失败: 想要 {theme} 实际 {actual}")
            dark = isDarkTheme()
            app.set_layout("workbench")
            pump(0.7)
            audit(app, theme, check, shot, pump)
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

        # 语义色像素断言（深色工作台截图）
        verify_colors(out, check)

        if dark0 != isDarkTheme():
            app.toggle_theme()
            pump(0.5)
    finally:
        if app is not None:
            app.close()
            if qapp is not None:
                qapp.processEvents()
            try:
                app._cache.close()
            except Exception:
                pass
        af.CACHE_PATH, af.CONFIG_PATH, af.LOG_PATH, af.HISTORY_PATH = original_paths
        shutil.rmtree(temp, ignore_errors=True)

    passed = sum(1 for _, ok in results if ok)
    print(f"\nUI 审查：{passed}/{len(results)} 通过" + ("" if passed == len(results) else " ❌"))
    return 0 if passed == len(results) else 1


def audit(app, theme, check, shot, pump):
    """单主题工作台的几何/颜色断言。"""
    tag = f"{theme}/workbench"
    W, H = app.width(), app.height()

    # —— 标题栏 ——
    tb = app._titlebar
    close = tb.btn_close
    check(f"[{tag}] 关闭钮贴右缘", abs(close.mapTo(app, close.rect().topRight()).x() - (W - 1)) <= 2,
          f"right={close.mapTo(app, close.rect().topRight()).x()} win={W}")
    check(f"[{tag}] 标题栏按钮 46x30", close.width() == 46 and close.height() == 30)
    check(f"[{tag}] 品牌居中", abs((tb.brand.x() + tb.brand.width() // 2) - W // 2) <= 6)

    # —— 顶栏 ——
    top = app.top_bar
    check(f"[{tag}] 顶栏高 48", top.height() == 48, f"h={top.height()}")
    check(f"[{tag}] 扫描钮 112x32", app.btn_scan.width() == 112 and app.btn_scan.height() == 32)
    check(f"[{tag}] 状态在底部独立状态栏", app.status_pill.parent() is app.footer)
    check(f"[{tag}] 状态胶囊高≤30", app.status_pill.height() <= 30, f"h={app.status_pill.height()}")
    role = getattr(app, "_status_role", "gray")
    check(f"[{tag}] 状态角色语义化", role in ("ok", "gray", "warn", "danger", "clay"), role)

    # —— 筛选条 ——
    fb = app.filter_bar
    check(f"[{tag}] 筛选条紧凑双行高 88", fb.height() == 88, f"h={fb.height()}")
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

    # —— 网格卡片（默认紧凑密度 172x200）——
    check(f"[{tag}] 网格单元 172x200", app.grid.gridSize() == __import__("PySide6.QtCore", fromlist=["QSize"]).QSize(172, 200),
          f"gs={app.grid.gridSize()}")

    # —— 侧栏已删（导航与顶栏/底栏重复）：必须不存在 ——
    check(f"[{tag}] 无侧栏", app.lm.sidebar is None)

    # —— 底部条 ——
    bot = app.bot
    check(f"[{tag}] 底栏停靠全宽", bot.width() >= W - 4 and bot.height() == 60,
          f"w={bot.width()} h={bot.height()}")
    bl = bot.layout()
    overflow = [bl.itemAt(i).widget().__class__.__name__
                for i in range(bl.count())
                if bl.itemAt(i).widget() is not None and bl.itemAt(i).widget().isVisible()
                and bl.itemAt(i).widget().geometry().right() > bot.width()]
    check(f"[{tag}] 底栏内容无越界", not overflow, str(overflow))

    # —— 详情面板 ——
    check(f"[{tag}] 详情宽可调", 320 <= app.detail.width() <= 520, f"w={app.detail.width()}")
    check(f"[{tag}] 预览画布高≥130", app.preview.height() >= 130, f"h={app.preview.height()}")
    check(f"[{tag}] 详情头徽标存在", app.detail_badge.text() != "", app.detail_badge.text())
    app.grid.setCurrentIndex(app.model.index(0))
    pump(0.5)
    check(f"[{tag}] 选中后徽标含判定", ("广告" in app.detail_badge.text()
                                      or "疑似" in app.detail_badge.text()),
          app.detail_badge.text())
    check(f"[{tag}] 预览有图", app.preview.pixmap() is not None
          and not app.preview.pixmap().isNull())
    app.grid.clearSelection()

    shot(f"audit-{theme}-workbench")


def verify_colors(OUT, check):
    """像素级颜色断言：执行处置=红、已选 pill/品牌橙在场。"""
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
    import cds_tokens as _cdst
    ar, ag, ab = _cdst.accent_rgb(True)
    img2 = load("audit-dark-workbench")
    clay = ((abs(img2[:, :, 0].astype(int) - ar) < 40)
            & (abs(img2[:, :, 1].astype(int) - ag) < 45)
            & (abs(img2[:, :, 2].astype(int) - ab) < 45)).sum()
    check("[色] 品牌橙大量在场(按钮/勾选/已选)", clay > 800, f"clay_px={clay}")


if __name__ == "__main__":
    sys.exit(main())
