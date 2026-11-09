# -*- coding: utf-8 -*-
"""独立合成数据预览；--interactive 打开大图供原生窗口检查。"""
import argparse
import copy
import sys
import tempfile
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from PIL import Image, ImageDraw
import ad_filter as af
import ad_filter_gui as gui


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--interactive', action='store_true')
    args = parser.parse_args()
    out = ROOT / '_preview'
    out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ad_layout_preview_') as directory:
        root = Path(directory)
        af.CONFIG_PATH, af.CACHE_PATH = root / 'cfg.json', root / 'cache.db'
        af.LOG_PATH, af.HISTORY_PATH = root / 'log.jsonl', root / 'hist.json'
        qt = QApplication.instance() or QApplication([])
        cfg = copy.deepcopy(af.DEFAULT_CONFIG)
        cfg['phash']['enabled'] = False
        cfg['ui_theme'] = 'light'
        app = gui.App(cfg)
        app._quarantine_override = root / 'quarantine'
        app._toast = lambda *a, **kw: None
        app.show()
        app.resize(1280, 800)
        paths = []
        for name, size in (('portrait', (1100, 1600)), ('landscape', (1800, 900))):
            image = Image.new('RGB', size, '#526d69')
            draw = ImageDraw.Draw(image)
            w, h = size
            draw.rectangle((0, 0, w-1, h-1), outline='#ffb44a', width=26)
            for x in range(100, w, 100):
                draw.line((x, 26, x, h-26), fill='#9eb6a3', width=2)
            for y in range(100, h, 100):
                draw.line((26, y, w-26, y), fill='#9eb6a3', width=2)
            for y, text in ((35, '顶部边缘完整可见'), (h-105, '底部边缘完整可见')):
                draw.text((w//2, y), text, anchor='mt', font=af._load_font(44), fill='white')
            draw.text((w//2, h//2), '大图审查 / 完整边缘检查', anchor='mm', font=af._load_font(52), fill='white')
            path = root / (name + '.png')
            image.save(path)
            paths.append(path)
        app.items = [dict(path=str(path), verdict='suspect', score=20,
                          reasons=[dict(type='shape', label='演示图片')], ocr_text='') for path in paths]
        app.refresh()
        def pump(seconds=.35):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                qt.processEvents()
                time.sleep(.01)
        pump()
        for path in paths:
            viewer = gui.Lightbox(app, str(path), app)
            viewer.resize(1280, 800)
            pump()
            viewer.grab().save(str(out / ('lightbox-' + path.stem + '.png')))
            viewer.toggle_controls()
            pump()
            viewer.grab().save(str(out / ('lightbox-' + path.stem + '-clean.png')))
            viewer.close()
            pump(.05)
        if args.interactive:
            app.grid.setCurrentIndex(app.model.index(0, 0))
            app.open_big()
            QTimer.singleShot(180000, app.close)
            qt.exec()
        app.close()
        qt.processEvents()
    print(out)


if __name__ == '__main__':
    main()
