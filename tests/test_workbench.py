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

    def test_palette_keyboard_and_repeated_button_toggle(self):
        from PySide6.QtTest import QTest
        self.app.open_palette()
        palette = self.app._palette
        palette.edit.setText('隔离')
        QTest.keyClick(palette.edit, Qt.Key_Escape)
        self.pump(.1)
        self.assertFalse(palette.isVisible())
        self.app.btn_palette.click()
        self.pump(.1)
        self.assertTrue(self.app._palette.isVisible())
        self.app.btn_palette.click()
        self.assertFalse(self.app._palette.isVisible())
        self.app.open_palette()
        palette = self.app._palette
        QTest.keyClick(palette.edit, Qt.Key_K, Qt.ControlModifier)
        self.assertFalse(palette.isVisible())
        self.app.open_palette()
        palette = self.app._palette
        QTest.keyClick(palette.edit, Qt.Key_Down)
        self.assertEqual(palette.list.currentRow(), 1)
        called = []
        palette.cmds = [('演示命令', lambda: called.append(True))]
        palette.edit.setText('演示')
        QTest.keyClick(palette.edit, Qt.Key_Return)
        self.assertEqual(called, [True])
        self.assertFalse(palette.isVisible())

    def test_custom_quarantine_save_scan_exclusion_and_restore(self):
        self.app._quarantine_override = None
        self.app._roots = [str(self.root)]
        custom = self.root / '自定义存储'
        dialog = self.app.build_config()
        dialog.quarantine_path.setText(str(custom))
        self.assertTrue(dialog.validate())
        self.assertEqual(self.app._quarantine_root(), custom)
        self.assertEqual(af.load_config()['quarantine_dir'], str(custom))
        self.app.toggle_check(0)
        self.app.execute_disposal()
        self.assertTrue(self.pump(until=lambda: not self.app._disposing))
        self.assertTrue((custom / 'manifest.json').exists())
        files = af.gather_images([str(self.root)], self.app.cfg)
        self.assertFalse(any(custom in p.parents for p in files))
        self.assertEqual(af.gather_images([str(custom)], self.app.cfg), [])
        other = self.root / '新的隔离区'
        dialog = self.app.build_config()
        dialog.quarantine_path.setText(str(other))
        dialog.validate()
        self.assertIn(str(custom), self.app.cfg['quarantine_locations'])
        self.assertFalse(any(custom in p.parents for p in af.gather_images([str(self.root)], self.app.cfg)))
        manager = gui.QuarantineDialog(self.app, custom)
        self.app._qdlg = manager
        manager.table.selectRow(0)
        self.assertTrue(manager.btn_rs.isEnabled())
        manager._restore_selected()
        self.assertTrue(self.pump(until=lambda: not self.app._file_busy))
        self.assertTrue(Path(self.rows[0]['path']).exists())
        self.assertEqual(manager.table.rowCount(), 0)
        self.assertFalse(manager.btn_rs.isEnabled())
        self.app._qdlg = None
        manager.close()

    def test_quarantine_setting_validation_and_import(self):
        dialog = self.app.build_config()
        original = copy.deepcopy(self.app.cfg)
        for path in ('relative/path', str(Path(self.root.anchor)), self.rows[0]['path']):
            dialog.quarantine_path.setText(path)
            self.assertFalse(dialog.validate())
            self.assertEqual(self.app.cfg, original)
        data = copy.deepcopy(af.DEFAULT_CONFIG)
        custom = self.root / '导入的隔离目录'
        data['quarantine_dir'] = str(custom)
        filename = self.root / 'custom.json'
        filename.write_text(json.dumps(data), encoding='utf-8')
        with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(filename), 'json')):
            dialog._import_cfg()
        self.assertEqual(dialog.quarantine_path.text(), str(custom))
        self.app._quarantine_override = None
        self.assertEqual(self.app._quarantine_root(), custom)
        dialog.quarantine_path.clear()
        self.assertTrue(dialog.validate())
        self.assertEqual(self.app.cfg['quarantine_dir'], '')
        self.assertIn(str(custom), self.app.cfg['quarantine_locations'])

    def test_dialog_navigation_history_search_and_escape(self):
        from PySide6.QtTest import QTest
        from dialog_ui import NoticeDialog
        af.append_history(dict(time='2026-10-30 10:00', roots=['演示目录'], total=10, hits=2))
        dialogs = [self.app.build_config(), self.app.build_rule_editor(),
                   gui.HistoryDialog(self.app), gui.QuarantineDialog(self.app, self.root / 'quarantine'),
                   NoticeDialog(self.app, '隔离确认', '示例内容\n' * 25)]
        for theme in ('light', 'dark'):
            if self.app.theme != theme:
                self.app.toggle_theme()
            for dialog in dialogs:
                dialog.show()
                self.pump(.1)
                self.assertTrue(dialog.closeButton.isVisible())
                self.assertTrue(dialog.cancelButton.isVisible() or dialog.yesButton.isVisible())
                for button in (dialog.cancelButton, dialog.yesButton):
                    if button.isVisible():
                        self.assertLessEqual(button.mapTo(dialog, button.rect().bottomRight()).y(), dialog.height())
                QTest.keyClick(dialog, Qt.Key_Escape)
                self.assertFalse(dialog.isVisible())
        settings, rules, history = dialogs[:3]
        settings.navigation.setCurrentRow(2)
        self.assertEqual(settings.page_stack.currentIndex(), 2)
        rules.pivot.setCurrentItem('tester')
        self.assertEqual(rules.navigation.currentRow(), 3)
        self.assertIs(rules.stack.currentWidget(), rules._pages['tester']['page'])
        history.search.setText('不存在')
        self.assertTrue(history.table.isRowHidden(0))
        history.search.setText('演示')
        self.assertFalse(history.table.isRowHidden(0))
        for dialog in dialogs:
            dialog.deleteLater()


if __name__ == '__main__':
    unittest.main(verbosity=2)
