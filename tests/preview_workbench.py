# -*- coding: utf-8 -*-
"""审查工作台真实 Qt 预览；数据完全临时，输出目录由调用者指定。"""
import argparse
import copy
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication
from unittest.mock import patch
from PySide6.QtCore import QRect
import ad_filter as af
import ad_filter_gui as gui


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    qt = QApplication.instance() or QApplication([])
    def pump():
        end = time.monotonic() + .4
        while time.monotonic() < end:
            qt.processEvents()
            time.sleep(.01)
    with tempfile.TemporaryDirectory(prefix='ad_workbench_visual_') as temporary:
        root = Path(temporary)
        for name in ('CONFIG_PATH', 'CACHE_PATH', 'LOG_PATH', 'HISTORY_PATH'):
            setattr(af, name, root / name.lower())
        cfg = copy.deepcopy(af.DEFAULT_CONFIG)
        cfg['phash']['enabled'] = False
        # 960×540 逻辑桌面相当于常见 1080p 屏幕的 200% 缩放。
        screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 960, 540))
        with patch.object(gui.App, 'screen', return_value=screen):
            app = gui.App(cfg)
        app._quarantine_override = root / 'isolated'
        app._toast = app._info = lambda *a, **k: None
        app.show()
        app.resize(1280, 800)
        def shot(name):
            pump()
            app.grab().save(str(args.output / (name + '.png')))
        shot('initial')
        paths = []
        for i in range(12):
            source = root / f'来源相册_{i+1}'
            source.mkdir()
            paths.append(str(source))
        app.add_folders(paths)
        shot('sources-added')
        rows = []
        for i, verdict in enumerate(('ad', 'suspect', 'error', 'ad', 'suspect', 'ad')):
            image = Image.new('RGB', (1000, 700), (160+i*10, 180, 169))
            draw = ImageDraw.Draw(image)
            draw.polygon([(0, 560), (220, 170), (480, 470), (750, 240), (1000, 560), (1000, 700), (0, 700)], fill=(72, 109+i*8, 101))
            draw.ellipse((690, 70, 820, 200), fill='#f1c47c')
            draw.text((40, 610), f'本地审查示例 {i+1}', font=af._load_font(36), fill='white')
            path = Path(paths[i]) / f'示例照片_{i+1}.png'
            image.save(path)
            rows.append(dict(path=str(path), verdict=verdict, score=0 if verdict == 'error' else 6,
                reasons=[dict(type='error' if verdict == 'error' else 'ocr',
                              label='解码失败：示例异常原因' if verdict == 'error' else 'OCR[链接] 示例规则命中')],
                ocr_text='' if verdict == 'error' else '本地 OCR 识别文本。可核对规则证据，并选择复制。'))
        app.items = rows
        app.refresh()
        app._reload_reason_filter()
        for theme in ('light', 'dark'):
            if app.theme != theme:
                app.toggle_theme()
            app.set_status('就绪')
            for width, height in ((1440, 900), (1280, 800), (1080, 680)):
                app.resize(width, height)
                app.clear_filters()
                shot(f'{theme}-mixed-{width}')
            app.resize(1280, 800)
            app.checked.update((rows[0]['path'], rows[2]['path']))
            app.f_verdict.setCurrentIndex(2)
            shot(theme+'-hidden-selected')
            app.set_selected_only(True)
            shot(theme+'-all-selected')
            app.set_selected_only(False)
            app.f_search.setText('没有匹配项')
            app.refresh()
            shot(theme+'-filter-empty')
            app.clear_filters()
            app.f_verdict.setCurrentIndex(3)
            shot(theme+'-error')
            app.clear_filters()
            app.btn_log.setChecked(True)
            app._log('部分隔离完成：1 张成功，1 张失败。失败项保留在待审集合。')
            shot(theme+'-log')
            app.btn_log.setChecked(False)
        app.resize(928, 508)
        shot('compact-desktop')
        panel = app.folder_bar.build_more()
        panel.show()
        pump()
        panel.grab().save(str(args.output / 'all-folders.png'))
        panel.close()
        app.close()
        qt.processEvents()
    print(args.output)


if __name__ == '__main__':
    main()
