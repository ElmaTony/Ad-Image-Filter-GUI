# -*- coding: utf-8 -*-
"""生成三种骨架的实际渲染预览图（QImage + app.render()，grabWindow 在 DPR≠1 下会失真）。

用法：python tests/preview_layouts.py
输出：_preview/layout-studio.png / layout-workbench.png / layout-gallery.png
"""
import sys
import time
from pathlib import Path

sys.argv = ["preview"]
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))          # 脚本在 tests/ 下，需把项目根加入导入路径

import ad_filter as af
import ad_filter_gui as g
OUT = ROOT / "_preview"
OUT.mkdir(exist_ok=True)


def main():
    src = ROOT / "testdata"
    if not (src / "type1_拼图引流.jpg").exists():
        af.make_fixtures(src)

    qapp = QApplication([])
    app = g.run_gui(return_app=True)
    app._toast = lambda *a, **k: None
    app.show()
    app.resize(1440, 900)

    def pump(sec=0.3):
        t0 = time.time()
        while time.time() - t0 < sec:
            qapp.processEvents()
            time.sleep(0.02)

    pump(0.5)
    # 用合成样本快速出一屏结果（关 OCR，走文件名/形状/二维码，秒级）
    app.cfg["ocr"]["enabled"] = False
    app.cfg["qr"]["enabled"] = True
    app.cfg["_enable_filename"] = True
    app.cfg["_enable_shape"] = True
    app.add_folders([str(src)])
    app.start_scan()
    t0 = time.time()
    while time.time() - t0 < 60 and "扫描完成" not in app.status.text():
        qapp.processEvents()
        time.sleep(0.05)
    pump(0.8)
    print(f"扫描结果：{len(app.items)} 张命中")

    for mode in ("studio", "workbench", "gallery"):
        app.set_layout(mode)
        pump(0.8)
        img = QImage(app.size(), QImage.Format_ARGB32)
        img.fill(0)
        app.render(img)
        p = OUT / f"layout-{mode}.png"
        img.save(str(p))
        sb = app.lm.sidebar
        print(f"{mode}: 保存 {p.name}  ({img.width()}x{img.height()}) "
              f"侧栏={sb.width() if sb else '-'} 详情={'显' if app.detail.isVisible() else '隐'}")


if __name__ == "__main__":
    main()
