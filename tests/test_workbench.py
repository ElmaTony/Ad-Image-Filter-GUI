# -*- coding: utf-8 -*-
"""暖陶工作台回归：真实 Qt 组件，文件操作只访问临时目录。"""
import copy
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication, QFileDialog
from PySide6.QtCore import Qt
from PIL import Image
import ad_filter as af
import ad_filter_gui as gui


class WorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ad_workbench_')
        self.root = Path(self.temp.name)
        self.original = af.CACHE_PATH, af.CONFIG_PATH, af.LOG_PATH, af.HISTORY_PATH
        af.CACHE_PATH, af.CONFIG_PATH = self.root / 'cache.db', self.root / 'config.json'
        af.LOG_PATH, af.HISTORY_PATH = self.root / 'log.jsonl', self.root / 'history.json'
        cfg = copy.deepcopy(af.DEFAULT_CONFIG)
        cfg['ui_theme'] = 'light'
        cfg['phash']['enabled'] = False
        self.app = gui.App(cfg)
        self.app._quarantine_override = self.root / 'quarantine'
        self.app._toast = lambda *a, **kw: None
        self.app._info = lambda *a, **kw: None
        self.app._ask = lambda *a, **kw: True
        self.app.show()
        self.app.resize(1280, 800)
        self.pump(.1)
        self.rows = []
        for i, verdict in enumerate(('ad', 'suspect', 'error', 'ad')):
            p = self.root / f'Image_{i}.jpg'
            Image.new('RGB', (900, 600), (90 + i * 30, 120, 110)).save(p)
            self.rows.append(dict(path=str(p), verdict=verdict, score=99-i,
                                  reasons=[{'type': 'filename', 'label': f'规则 {i}'}],
                                  ocr_text='本地 OCR 文本 ' * 30))
        self.app.items = list(self.rows)
        self.app.refresh()
        self.pump(.2)

    def pump(self, seconds=0, until=None, timeout=10):
        start = time.monotonic()
        while time.monotonic() - start < (timeout if until else seconds):
            self.qt.processEvents()
            if until and until():
                return True
            time.sleep(.01)
        return not until

    def tearDown(self):
        self.app.close()
        self.qt.processEvents()
        self.pump(.1)
        if self.app._cache:
            self.app._cache.conn.close()
        af.CACHE_PATH, af.CONFIG_PATH, af.LOG_PATH, af.HISTORY_PATH = self.original
        self.temp.cleanup()

    def test_verdicts_and_case_insensitive_search(self):
        for index, verdict, n in ((1, 'ad', 2), (2, 'suspect', 1), (3, 'error', 1)):
            self.app.f_verdict.setCurrentIndex(index)
            self.assertEqual(len(self.app.model.items), n)
            self.assertTrue(all(it['verdict'] == verdict for it in self.app.model.items))
        self.app.f_verdict.setCurrentIndex(0)
        self.app.f_search.setText('image_2')
        self.app.refresh()
        self.assertEqual([it['path'] for it in self.app.model.items], [self.rows[2]['path']])

    def test_hidden_selection_and_visible_toggle(self):
        self.app.toggle_check(0)
        hidden = self.rows[0]['path']
        self.app.f_verdict.setCurrentIndex(2)
        self.assertIn('隐藏 1', self.app.selection_scope.text())
        self.app.select_all()
        self.assertEqual(self.app.checked, {hidden, self.rows[1]['path']})
        self.app.select_all()
        self.assertEqual(self.app.checked, {hidden})
        self.assertEqual(self.app.btn_dispose.text(), '隔离 1 张')
        self.app.invert_sel()
        self.assertEqual(len(self.app.checked), 2)
        self.app.clear_sel()
        self.assertFalse(self.app.btn_dispose.isEnabled())

    def test_confirmations_include_hidden_selection(self):
        self.app.select_all()
        self.app.f_verdict.setCurrentIndex(2)
        captured = []
        self.app._ask = lambda title, content: captured.append((title, content)) or False
        for index in range(3):
            self.app.seg_disposal.set_index(index)
            self.app.execute_disposal()
        self.assertEqual(len(captured), 3)
        for title, content in captured:
            self.assertIn('3 张被当前筛选隐藏', content)
            self.assertIn('异常 1', content)
        self.assertTrue(all(Path(it['path']).exists() for it in self.rows))

    def test_quarantine_and_undo_are_isolated(self):
        self.app._roots = [str(self.root)]
        self.app.toggle_check(0)
        self.app.execute_disposal()
        self.assertTrue(self.pump(until=lambda: not self.app._disposing))
        self.assertFalse(Path(self.rows[0]['path']).exists())
        self.assertTrue((self.root / 'quarantine' / 'manifest.json').exists())
        self.assertTrue(self.app.btn_undo.isEnabled())
        self.app.undo_disposal()
        self.assertTrue(self.pump(until=lambda: not self.app._disposing))
        self.assertTrue(Path(self.rows[0]['path']).exists())

    def test_delete_only_checked_temporary_file(self):
        self.app.toggle_check(2)
        self.app.seg_disposal.set_index(2)
        self.assertIn('永久删除 1', self.app.btn_dispose.text())
        self.app.execute_disposal()
        self.assertTrue(self.pump(until=lambda: not self.app._disposing))
        self.assertFalse(Path(self.rows[2]['path']).exists())
        self.assertTrue(Path(self.rows[0]['path']).exists())
        self.assertEqual(len(self.app.items), 3)

    def test_import_updates_form_and_preserves_defaults(self):
        data = {'ocr_keywords': [{'pattern': 'demo', 'weight': 3, 'label': '测试'}],
                'workers': 2, 'qr': {'enabled': False}, 'phash': {'threshold': 4}}
        filename = self.root / 'import.json'
        filename.write_text(json.dumps(data), encoding='utf-8')
        dialog = self.app.build_config()
        with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(filename), 'json')):
            dialog._import_cfg()
        self.assertFalse(dialog.cb_qr.isChecked())
        self.assertEqual(dialog.cb_workers.currentIndex(), 1)
        self.assertEqual(dialog.sim_threshold.value(), 4)
        self.assertIn('max_dim', self.app.cfg['ocr'])
        dialog.validate()
        self.assertFalse(self.app.cfg['qr']['enabled'])
        self.assertEqual(self.app.cfg['phash']['threshold'], 4)

    def test_full_ocr_and_preview_anchor(self):
        self.app.grid.setCurrentIndex(self.app.model.index(1, 0))
        self.app._flush_preview()
        self.assertTrue(self.pump(until=lambda: bool(self.app.preview.pixmap())))
        self.assertIn(self.rows[1]['ocr_text'], self.app.info.toPlainText())
        self.assertIn('Image_1', self.app.detail_filename.text())
        self.app.f_sort.setCurrentIndex(4)
        self.assertEqual(self.app._current_item()['path'], self.rows[1]['path'])
        self.app.items = []
        self.app.refresh()
        self.assertFalse(self.app.info.toPlainText())
        self.assertEqual(self.app.preview_index.text(), '— / —')

    def test_theme_and_task_controls(self):
        self.app.toggle_theme()
        self.pump(.4)
        self.assertEqual(self.app.theme, 'dark')
        self.assertEqual(self.app.cfg['ui_theme'], 'dark')
        self.app.open_palette()
        self.assertTrue(self.app._palette.dark)
        self.app._palette.close()
        self.app._file_busy = True
        self.app._sync_actions()
        self.assertFalse(self.app.btn_scan.isEnabled())
        self.assertFalse(self.app.btn_dispose.isEnabled())
        self.app._file_busy = False

    def test_small_window_and_many_folders(self):
        self.app.add_folders([str(self.root / ('长目录名称' + str(i))) for i in range(12)])
        self.app.resize(1080, 680)
        self.pump(.3)
        self.assertEqual((self.app.width(), self.app.height()), (1080, 680))
        self.assertEqual(len(self.app.folder_bar._chips), 12)
        for widget in (self.app.bot, self.app.footer, self.app.detail, self.app.filter_bar):
            point = widget.mapTo(self.app, widget.rect().bottomRight())
            self.assertLess(point.x(), self.app.width())
            self.assertLess(point.y(), self.app.height())


if __name__ == '__main__':
    unittest.main(verbosity=2)
