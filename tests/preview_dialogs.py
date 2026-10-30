# -*- coding: utf-8 -*-
"""使用独立合成数据输出配套窗口预览，不读取用户配置或历史。"""
import copy
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication
from PIL import Image
import ad_filter as af
import ad_filter_gui as gui
from dialog_ui import NoticeDialog


def main():
    out = Path(__file__).resolve().parent.parent / '_preview' / 'dialogs'
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ad_dialog_preview_') as temporary:
        root = Path(temporary)
        af.CONFIG_PATH, af.CACHE_PATH = root / 'cfg.json', root / 'cache.db'
        af.LOG_PATH, af.HISTORY_PATH = root / 'log.jsonl', root / 'hist.json'
        qt = QApplication.instance() or QApplication([])
        cfg = copy.deepcopy(af.DEFAULT_CONFIG)
        cfg['ui_theme'] = 'light'
        app = gui.App(cfg)
        app._quarantine_override = root / 'quarantine'
        app.show()
        app.resize(1280, 800)
        for i in range(4):
            af.append_history(dict(time=f'2026-10-{30-i} 14:20:00', roots=['旅行相册', '待整理图片'],
                                   total=240+i*120, hits=18, ad=10, suspect=7, error=1, duration=36+i*12))
        photos = root / 'photos'
        photos.mkdir()
        photo = photos / '推广图.jpg'
        Image.new('RGB', (640, 400), (145, 162, 130)).save(photo)
        af.apply_quarantine([{'path': str(photo)}], [str(photos)], app._quarantine_root(), lambda *a: None)

        def pump():
            until = time.monotonic() + .2
            while time.monotonic() < until:
                qt.processEvents()
                time.sleep(.01)

        for theme in ('light', 'dark'):
            if app.theme != theme:
                app.toggle_theme()
                pump()
            for name, dialog in (
                ('settings', app.build_config()), ('rules', app.build_rule_editor()),
                ('history', gui.HistoryDialog(app)),
                ('quarantine', gui.QuarantineDialog(app, app._quarantine_root())),
                ('confirm', NoticeDialog(app, '隔离确认', '共 18 张图片（广告 10 / 疑似 7 / 异常 1）\n总大小 24.6 MB\n\n将移动到指定的隔离目录，可随时恢复或撤销。')),
            ):
                if name == 'settings':
                    dialog.navigation.setCurrentRow(2)
                    dialog.quarantine_path.setText(r'D:\图片\广告隔离')
                if name == 'quarantine':
                    dialog.lbl_path.setText(r'D:\图片\广告隔离   ·   1 项')
                    dialog.locations.blockSignals(True)
                    dialog.locations.setItemText(0, r'D:\图片\广告隔离')
                    dialog.locations.blockSignals(False)
                    dialog.table.item(0, 1).setText(r'D:\图片\旅行相册\推广图.jpg')
                    dialog.table.selectRow(0)
                dialog.show()
                pump()
                dialog.grab().save(str(out / f'{name}-{theme}.png'))
                if name == 'settings':
                    for row, label in ((0, 'folders'), (1, 'recognition')):
                        dialog.navigation.setCurrentRow(row)
                        pump()
                        dialog.grab().save(str(out / f'settings-{label}-{theme}.png'))
                dialog.close()
                dialog.deleteLater()
            app.open_palette()
            pump()
            app._palette.grab().save(str(out / f'commands-{theme}.png'))
            app._palette.close()
        app.close()
        qt.processEvents()
    print(out)


if __name__ == '__main__':
    main()
