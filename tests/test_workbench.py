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

    def test_lightbox_photo_edges_zoom_panning_and_clean_view(self):
        from PySide6.QtTest import QTest
        from PySide6.QtCore import QPointF
        from PIL import ImageDraw
        for size in ((900, 1600), (1700, 650), (1000, 1000)):
            path = self.root / 'edge_check.png'
            image = Image.new('RGB', size, '#546868')
            ImageDraw.Draw(image).rectangle((0, 0, size[0]-1, size[1]-1), outline='#ff0000', width=25)
            image.save(path)
            viewer = gui.Lightbox(self.app, str(path), self.app)
            viewer.resize(1080, 680)
            self.pump(.3)
            viewer._fit()
            viewport = viewer._image_rect()
            self.assertLessEqual(viewer._fit_w, viewport.width() + .01)
            self.assertLessEqual(viewer._fit_h, viewport.height() + .01)
            screenshot = viewer.grab().toImage()
            dpr = screenshot.devicePixelRatio()
            cx = viewport.x() + viewport.width()/2
            cy = viewport.y() + viewport.height()/2
            for x, y in ((cx, cy-viewer._fit_h/2+2), (cx, cy+viewer._fit_h/2-3),
                         (cx-viewer._fit_w/2+2, cy), (cx+viewer._fit_w/2-3, cy)):
                pixel = screenshot.pixelColor(int(x*dpr), int(y*dpr))
                self.assertGreater(pixel.red(), 200, 'photo edge hidden by controls')
                self.assertLess(pixel.green(), 40)
            viewer.scale = 3
            viewer.tx = viewer.ty = 100000
            viewer._clamp_translate()
            self.assertAlmostEqual(viewer.ty, max(0, (viewer._fit_h*3-viewport.height())/2))
            self.assertAlmostEqual(viewer.tx, max(0, (viewer._fit_w*3-viewport.width())/2))
            QTest.keyClick(viewer, Qt.Key_F)
            self.assertEqual(viewer._image_rect(), viewer.rect())
            self.assertFalse(viewer.btn_keep.isVisible())
            QTest.keyClick(viewer, Qt.Key_F)
            self.assertTrue(viewer.btn_keep.isVisible())
            viewer.close()
            self.pump(.05)
        missing = gui.Lightbox(self.app, str(self.root / 'missing.png'), self.app)
        class Wheel:
            def position(self): return QPointF(200, 200)
        missing.wheelEvent(Wheel())  # 无效图片不再因访问 _pix.width() 崩溃。
        missing.close()

    def test_scan_error_recovers_controls_without_recording_history(self):
        self.app.scan_q.put(('scan_error', '测试模型加载失败'))
        self.app._poll_queue()
        self.assertIn('扫描失败', self.app.status.text())
        self.assertTrue(self.app.btn_scan.isEnabled())
        self.assertEqual(af.load_history(), [])

    def test_keep_retains_next_photo_and_late_similarity_ignores_removed_paths(self):
        self.app.grid.setCurrentIndex(self.app.model.index(2, 0))
        self.app.mark_current(False)
        self.assertEqual(self.app._current_item()['path'], self.rows[3]['path'])
        self.app._apply_similar([[self.rows[2]['path'], self.rows[3]['path']]])
        self.assertEqual(self.app.similar_groups, [])
        self.app.items = []
        self.app._apply_similar([[self.rows[0]['path'], self.rows[1]['path']]])
        self.assertEqual(self.app.similar_groups, [])
        self.assertFalse(self.app.model.sim_map)

    def test_rule_tester_low_score_matches_scan_and_nonfinite_weight_rejected(self):
        from PySide6.QtWidgets import QTableWidgetItem
        dialog = self.app.build_rule_editor()
        dialog.show_errors = False
        dialog.t_in.setPlainText('频道')
        dialog._run_test()
        self.assertIn('疑似', dialog.t_out.toPlainText())
        table = dialog._pages['ocr_keywords']['table']
        table.setItem(0, 2, QTableWidgetItem('inf'))
        self.assertFalse(dialog.do_save())
        dialog.close()

    def test_thumbnail_reset_retries_failures_and_ignores_old_scan_results(self):
        thumbs = self.app.thumbs
        old_generation = thumbs._generation
        path = self.rows[0]['path']
        thumbs._mark_failed(path)
        self.assertIn(path, thumbs._failed)
        thumbs.clear()
        self.assertNotIn(path, thumbs._failed)
        thumbs._on_store((old_generation, path, None, 0, 0, True))
        self.assertNotIn(path, thumbs._failed)
        thumbs.get(path)
        self.assertTrue(self.pump(until=lambda: path in thumbs._cache))

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

    def test_visible_categories_sync_with_compatible_filter_and_global_counts(self):
        for index, label, expected in ((0, '全部待审', 4), (1, '广告', 2), (2, '疑似', 1), (3, '异常', 1)):
            button = self.app.verdict_tabs.buttons[index]
            button.click()
            self.assertEqual(self.app.f_verdict.currentIndex(), index)
            self.assertEqual(self.app.f_verdict.currentText(), label)
            self.assertTrue(button.isChecked())
            self.assertEqual(button.text(), f'{label} {expected}')
            self.assertEqual(self.app.results_scope.text(), f'显示 {expected} / 待审 4')
        self.app.f_verdict.setCurrentIndex(1)
        self.assertTrue(self.app.verdict_tabs.buttons[1].isChecked())
        self.assertEqual(self.app.pill_hit.text(), '待审 4')
        self.assertEqual(self.app.verdict_tabs.buttons[3].text(), '异常 1')

    def test_view_all_checked_then_restore_original_filters(self):
        selected = {self.rows[0]['path'], self.rows[2]['path']}
        self.app.checked.update(selected)
        self.app.f_verdict.setCurrentIndex(2)
        self.app.f_sort.setCurrentIndex(4)
        self.app.f_search.setText('Image')
        self.app.refresh()
        self.assertIn('隐藏 2', self.app.selection_scope.text())
        self.app.btn_selected.click()
        self.assertTrue(self.app._selected_only)
        self.assertEqual({it['path'] for it in self.app.model.items}, selected)
        self.assertEqual(self.app.checked, selected)
        self.assertEqual(self.app.btn_selected.text(), '返回筛选')
        self.app.btn_selected.click()
        self.assertEqual(self.app.f_verdict.currentIndex(), 2)
        self.assertEqual(self.app.f_search.text(), 'Image')
        self.assertEqual(self.app.f_sort.currentIndex(), 4)
        self.assertEqual(self.app.checked, selected)
        self.assertEqual([it['path'] for it in self.app.model.items], [self.rows[1]['path']])

    def test_uncheck_inside_selected_view_removes_only_that_item(self):
        self.app.checked.update((self.rows[0]['path'], self.rows[2]['path']))
        self.app.set_selected_only(True)
        self.app.toggle_check(0)
        self.assertEqual(self.app.checked, {self.rows[2]['path']})
        self.assertEqual([it['path'] for it in self.app.model.items], [self.rows[2]['path']])
        self.app.clear_sel()
        self.assertFalse(self.app.model.items)
        self.assertEqual(self.app._empty.title.text(), '当前没有已选图片')
        self.assertTrue(self.app.btn_selected.isVisible())
        self.app.clear_filters()
        self.assertEqual(len(self.app.model.items), 4)
        self.assertFalse(self.app.checked)

    def test_reset_empty_filters_preserves_selection_and_sort(self):
        self.app.toggle_check(0)
        self.app.f_verdict.setCurrentIndex(3)
        self.app.f_search.setText('does-not-exist')
        self.app.f_sort.setCurrentIndex(1)
        self.app.refresh()
        self.assertEqual(self.app.results_scope.text(), '显示 0 / 待审 4')
        self.assertTrue(self.app.btn_reset_filters.isEnabled())
        self.app.btn_reset_filters.click()
        self.assertEqual(self.app.f_verdict.currentIndex(), 0)
        self.assertEqual(self.app.f_search.text(), '')
        self.assertEqual(self.app.f_sort.currentIndex(), 1)
        self.assertEqual(self.app.checked, {self.rows[0]['path']})
        self.assertEqual(len(self.app.model.items), 4)

    def test_keyboard_selection_is_limited_to_visible_category(self):
        from PySide6.QtTest import QTest
        self.app.toggle_check(0)
        self.app.verdict_tabs.buttons[3].click()
        self.app.grid.setFocus()
        QTest.keyClick(self.app.grid, Qt.Key_A, Qt.ControlModifier)
        self.assertEqual(self.app.checked, {self.rows[0]['path'], self.rows[2]['path']})
        self.assertEqual(self.app.btn_all.text(), '取消全选')
        QTest.keyClick(self.app.grid, Qt.Key_A, Qt.ControlModifier)
        self.assertEqual(self.app.checked, {self.rows[0]['path']})
        QTest.keyClick(self.app.grid, Qt.Key_Space)
        self.assertIn(self.rows[2]['path'], self.app.checked)

    def test_folder_overflow_lists_all_paths_progress_and_safe_removal(self):
        roots = [str(self.root / f'来源相册_{i}') for i in range(12)]
        self.app.add_folders(roots)
        self.app.resize(1080, 680)
        self.pump(.1)
        bar = self.app.folder_bar
        self.assertEqual(len(bar._chips), 12)
        self.assertLessEqual(sum(chip.isVisible() for chip in bar._chips.values()), 3)
        self.assertTrue(bar.btn_more.isVisible())
        dialog = bar.build_more()
        self.assertEqual(dialog.table.rowCount(), 12)
        self.app._folder_totals[roots[0]] = 5
        self.app._folder_done[roots[0]] = 5
        dialog.refresh_sources()
        self.assertEqual(dialog.table.item(0, 0).text(), roots[0])
        self.assertEqual(dialog.table.item(0, 1).text(), '✓ 5/5')
        self.app._scan_active = True
        dialog.refresh_sources()
        self.assertFalse(dialog.remove_buttons[0].isEnabled())
        self.app.remove_folder(roots[0])
        self.assertEqual(len(self.app.get_roots()), 12)
        self.app._scan_active = False
        dialog.refresh_sources()
        dialog.remove_buttons[0].click()
        self.assertEqual(len(self.app.get_roots()), 11)
        self.assertEqual(dialog.table.rowCount(), 11)
        dialog.close()
        dialog.deleteLater()

    def test_partial_quarantine_keeps_failed_item_selected(self):
        missing = Path(self.rows[1]['path'])
        missing.unlink()
        self.app._roots = [str(self.root)]
        self.app.checked.update((self.rows[0]['path'], str(missing)))
        errors = []
        self.app._info = lambda title, content: errors.append(content)
        self.app.execute_disposal()
        self.assertTrue(self.pump(until=lambda: not self.app._disposing))
        self.assertEqual(self.app.checked, {str(missing)})
        self.assertTrue(any(item['path'] == str(missing) for item in self.app.items))
        self.assertFalse(any(item['path'] == self.rows[0]['path'] for item in self.app.items))
        self.assertIn('1 张失败', self.app.status.text())
        self.assertTrue(errors)

    def test_streaming_errors_have_their_own_category_and_reason(self):
        self.app.items = []
        self.app.refresh()
        self.app.scan_q.put(('item', {**self.rows[2], 'reasons': [{'type': 'error', 'label': '解码失败 示例'}]}))
        self.app._poll_queue()
        self.assertEqual(self.app.verdict_tabs.buttons[3].text(), '异常 1')
        self.assertEqual(self.app.verdict_tabs.buttons[2].text(), '疑似 0')
        self.assertGreaterEqual(self.app.f_reason.findText('解码失败'), 0)
        self.app.verdict_tabs.buttons[3].click()
        self.app._flush_preview()
        self.assertIn('解码失败 示例', self.app.info.toPlainText())

    def test_empty_scan_cancel_and_failure_states_are_distinct(self):
        self.app.items = []
        self.app.total_files = 0
        self.app.scan_q.put(('done', None))
        self.app._poll_queue()
        self.assertEqual(self.app._empty.title.text(), '本次扫描没有待审图片')
        self.app.scan_q.put(('cancelled', None))
        self.app._poll_queue()
        self.assertEqual(self.app._empty.title.text(), '扫描已取消')
        self.app.scan_q.put(('scan_error', '目录不可访问'))
        self.app._poll_queue()
        self.assertEqual(self.app._empty.title.text(), '扫描未完成')

    def test_available_logical_screen_and_compact_actions_fit(self):
        from PySide6.QtCore import QRect
        from types import SimpleNamespace
        screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 960, 540))
        with patch.object(gui.App, 'screen', return_value=screen):
            compact = gui.App(copy.deepcopy(self.app.cfg))
        compact._quarantine_override = self.root / 'compact-quarantine'
        compact._toast = compact._info = lambda *a, **k: None
        try:
            compact.show()
            self.pump(.2)
            self.assertLessEqual(compact.width(), 960)
            self.assertLessEqual(compact.height(), 540)
            self.assertTrue(compact.btn_more_actions.isVisible())
            for name in ('top_bar', 'bot', 'footer', 'btn_dispose', 'btn_mark', 'btn_keep', 'btn_big'):
                widget = getattr(compact, name)
                p = widget.mapTo(compact, widget.rect().bottomRight())
                self.assertLess(p.x(), compact.width(), name)
                self.assertLess(p.y(), compact.height(), name)
        finally:
            compact.close()
            self.qt.processEvents()

    def test_poll_queue_handles_concurrent_scan_and_disposal_events(self):
        self.app._scan_active = True
        self.app._disposing = True
        self.app.scan_q.put(('cancelled', None))
        self.app.scan_q.put(('disposed', ('quarantine', 1, [], [self.rows[0]['path']],
                                          str(self.app._quarantine_root()), [])))
        self.app._poll_queue()
        self.assertFalse(self.app._scan_active)
        self.assertFalse(self.app._disposing)
        self.assertFalse(any(it['path'] == self.rows[0]['path'] for it in self.app.items))

    def test_theme_toggle_refreshes_segmented_button_text_contrast(self):
        from qfluentwidgets import isDarkTheme
        import cds_tokens as CDST
        self.app.seg_disposal.set_index(0, animate=False)
        before_dark = isDarkTheme()
        before_qss = self.app.btn_dq.styleSheet()
        self.app.toggle_theme()
        self.pump(.1)
        after_dark = isDarkTheme()
        after_qss = self.app.btn_dq.styleSheet()
        self.assertNotEqual(before_dark, after_dark)
        expected_color = CDST.c(after_dark, CDST.T.text)
        self.assertIn(expected_color, after_qss)
        self.assertNotEqual(before_qss, after_qss)

    def test_checkbox_click_with_small_drag_does_not_recheck_card(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QEvent
        path0 = self.app.model.items[0]['path']
        self.app.toggle_check(0)
        self.assertIn(path0, self.app.checked)
        vr = self.app.grid.visualRect(self.app.model.index(0, 0))
        start = QPointF(vr.left() + 20, vr.top() + 20)
        end = QPointF(start.x() + 10, start.y() + 10)
        vp = self.app.grid.viewport()
        self.app.eventFilter(vp, QMouseEvent(QEvent.MouseButtonPress, start, start, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        self.app.eventFilter(vp, QMouseEvent(QEvent.MouseMove, end, end, Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        self.app.eventFilter(vp, QMouseEvent(QEvent.MouseButtonRelease, end, end, Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
        self.assertNotIn(path0, self.app.checked)
        self.assertFalse(self.app._sel_band.isVisible())

    def test_lightbox_wheel_zoom_direction_and_close_clears_reference(self):
        from PySide6.QtCore import QPoint, QPointF
        self.app.grid.setCurrentIndex(self.app.model.index(0, 0))
        self.app.open_big()
        viewer = self.app._big_win
        self.assertTrue(self.pump(until=lambda: viewer._pix is not None, timeout=5))
        center = QPointF(viewer.width() / 2, viewer.height() / 2)

        class Wheel:
            def __init__(self, dy): self._dy = dy
            def angleDelta(self): return QPoint(0, self._dy)
            def position(self): return center

        s0 = viewer.scale
        viewer.wheelEvent(Wheel(120))
        self.assertGreater(viewer.scale, s0)
        s1 = viewer.scale
        viewer.wheelEvent(Wheel(-120))
        self.assertLess(viewer.scale, s1)
        viewer.close()
        self.pump(.05)
        self.assertIsNone(self.app._big_win)

    def test_export_report_handles_oserror_without_crashing(self):
        from PySide6.QtWidgets import QFileDialog
        errors = []
        self.app._info = lambda title, content: errors.append((title, content))
        with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(self.root / 'locked.csv'), 'csv')), \
                patch('builtins.open', side_effect=PermissionError('file in use')):
            self.app.export_report()
        self.assertTrue(errors and errors[0][0] == '导出失败')

    def test_undo_only_restores_latest_batch_not_older_same_src_and_retries_on_failure(self):
        q_root = self.app._quarantine_root()
        target = Path(self.rows[0]['path'])
        # 第一批：先隔离一次，然后在原位置再放一张同名新图
        old_bytes = target.read_bytes()
        moved, errors, _ = af.apply_quarantine([{'path': str(target)}], [str(self.root)], q_root, lambda _: None)
        self.assertEqual((moved, errors), (1, []))
        Image.new('RGB', (400, 300), (200, 40, 40)).save(target)
        new_bytes = target.read_bytes()
        self.assertNotEqual(old_bytes, new_bytes)

        # 第二批通过工作台隔离，再执行撤销：应只还原第二批，第一批仍保留在隔离区
        self.app._roots = [str(self.root)]
        self.app.checked = {str(target)}
        self.app.model.checked = self.app.checked
        self.app.execute_disposal()
        self.assertTrue(self.pump(until=lambda: not self.app._disposing))
        self.assertEqual(len(af.list_quarantine(q_root)), 2)

        # 模拟第一次撤销失败 -> 保留 _undo_batch 可重试
        with patch.object(af, 'restore_quarantine_items', return_value=(0, ['占用失败'])):
            self.app.undo_disposal()
            self.assertTrue(self.pump(until=lambda: not self.app._disposing))
        self.assertIsNotNone(self.app._undo_batch)
        self.assertTrue(self.app.btn_undo.isEnabled())

        # 第二次真实撤销 -> 仅恢复第二批，第一批历史文件不误还原
        self.app.undo_disposal()
        self.assertTrue(self.pump(until=lambda: not self.app._disposing))
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(), new_bytes)
        self.assertEqual(len(af.list_quarantine(q_root)), 1)

    def test_remove_folder_clears_items_and_add_folders_dedups_case_insensitive(self):
        sub_a = self.root / 'FolderA'
        sub_b = self.root / 'FolderB'
        sub_a.mkdir()
        sub_b.mkdir()
        pa = sub_a / 'a.jpg'
        pb = sub_b / 'b.jpg'
        Image.new('RGB', (320, 240), (10, 20, 30)).save(pa)
        Image.new('RGB', (320, 240), (30, 20, 10)).save(pb)
        self.app.add_folders([str(sub_a), str(sub_a).lower() + '\\', str(sub_b)])
        self.assertEqual(len(self.app.get_roots()), 2)
        self.app.items = [
            {'path': str(pa), 'verdict': 'ad', 'score': 90, 'reasons': [], 'ocr_text': ''},
            {'path': str(pb), 'verdict': 'ad', 'score': 90, 'reasons': [], 'ocr_text': ''},
        ]
        self.app.checked = {str(pa), str(pb)}
        self.app.model.checked = self.app.checked
        self.app.refresh()
        self.app.remove_folder(str(sub_a))
        self.assertEqual([it['path'] for it in self.app.items], [str(pb)])
        self.assertEqual(self.app.checked, {str(pb)})

    def test_rule_editor_rejects_nonpositive_weight_and_deletes_multiple_rows(self):
        from PySide6.QtWidgets import QTableWidgetItem
        dlg = self.app.build_rule_editor()
        dlg.show_errors = False
        try:
            tbl = dlg._pages['ocr_keywords']['table']
            before_rows = tbl.rowCount()
            dlg._add_row(tbl, True)
            r = tbl.rowCount() - 1
            tbl.setItem(r, 0, QTableWidgetItem('test_kw'))
            tbl.setItem(r, 2, QTableWidgetItem('0'))
            self.assertFalse(dlg.do_save())
            tbl.setItem(r, 2, QTableWidgetItem('1.5'))
            self.assertFalse(dlg.do_save())
            # 选中最后两行验证批量删除
            tbl.clearSelection()
            tbl.selectRow(r)
            tbl.item(r - 1, 0).setSelected(True)
            tbl.item(r, 0).setSelected(True)
            del_btn = next(b for b in dlg._pages['ocr_keywords']['page'].findChildren(gui.TransparentPushButton)
                           if '删除' in b.text())
            del_btn.click()
            self.assertEqual(tbl.rowCount(), before_rows - 1)
        finally:
            dlg.close()
            dlg.deleteLater()

    def test_real_toast_and_compact_command_palette_fit_window(self):
        from cds_widgets import CDSToast, CDSCommandPalette
        CDSToast._stack.clear()
        self.app.resize(640, 480)
        self.pump(.1)
        toast = CDSToast.show(self.app, '一条很长的提示消息用于验证窄窗下不会被裁切出左边界', 'ok')
        self.pump(.05)
        self.assertGreaterEqual(toast.x(), 0)
        self.assertLessEqual(toast.x() + toast.width(), self.app.width())
        self.assertIsNotNone(toast.graphicsEffect())
        toast._done()
        self.pump(.05)
        self.assertNotIn(toast, CDSToast._stack)

        ran = []
        pal = CDSCommandPalette.popup(self.app, [('执行示例', lambda: ran.append(True))])
        self.pump(.05)
        self.assertGreaterEqual(pal.x(), 0)
        self.assertGreaterEqual(pal.y(), 0)
        self.assertLessEqual(pal.x() + pal.width(), self.app.width())
        self.assertLessEqual(pal.y() + pal.height(), self.app.height())
        pal.list.itemClicked.emit(pal.list.item(0))
        self.assertEqual(ran, [True])

    def test_multi_palette_and_three_density_modes_persist(self):
        import cds_tokens as CDST
        from PySide6.QtCore import QSize
        # 默认：宣纸暖陶 2.0 + 紧凑密度
        self.assertEqual(self.app.palette_name, 'atelier')
        self.assertEqual(self.app.density, 'compact')
        self.assertEqual(self.app.grid.gridSize(), QSize(172, 200))

        # 依次切换全部 4 套主题色盘
        for key in CDST.PALETTE_ORDER:
            self.app.set_palette(key)
            self.pump(.05)
            self.assertEqual(CDST.get_palette(), key)
            self.assertEqual(self.app.cfg['ui_palette'], key)
            r, g, b = CDST.accent_rgb(self.app.theme == 'dark')
            self.assertIn(f'{r},{g},{b}', self.app._sel_band.styleSheet())

        # 依次通过 UI 切换三档密度并验证网格和委托尺寸
        for key, expected in (('standard', QSize(204, 242)), ('large', QSize(260, 294)), ('compact', QSize(172, 200))):
            self.app.density_tabs.buttons[key].click()
            self.pump(.05)
            self.assertEqual(self.app.density, key)
            self.assertEqual(self.app.cfg['ui_density'], key)
            self.assertEqual(self.app.grid.gridSize(), expected)
            self.assertTrue(self.app.density_tabs.buttons[key].isChecked())

        # 通过设置弹窗「外观与主题」页修改并保存
        dlg = self.app.build_config()
        try:
            self.assertEqual(dlg.navigation.count(), 4)
            dlg.navigation.setCurrentRow(3)
            dlg.cb_palette.setCurrentIndex(1)  # slate
            dlg.cb_density.setCurrentIndex(1)  # standard
            self.assertTrue(dlg.validate())
            self.assertEqual(self.app.palette_name, 'slate')
            self.assertEqual(self.app.density, 'standard')
            reloaded = af.load_config()
            self.assertEqual(reloaded['ui_palette'], 'slate')
            self.assertEqual(reloaded['ui_density'], 'standard')
        finally:
            dlg.close()
            dlg.deleteLater()
            self.app.set_palette('atelier', save=False)
            self.app.set_density('compact', save=False)

    def test_folder_labels_populated_across_large_done_batch_and_drive_roots(self):
        self.app.items.clear()
        self.app._pending_rows.clear()
        album = self.root / 'AlbumOne'
        sub = album / 'Sub'
        sub.mkdir(parents=True)
        self.app.add_folders([str(album), 'D:\\'])
        self.app.f_verdict.setCurrentIndex(1)  # 扫描中切到「广告」，验证被过滤的「疑似」同样获得 _folder
        for i in range(45):
            verdict = 'ad' if i < 5 else 'suspect'
            p = str(sub / f'img_{i:02d}.jpg')
            self.app.scan_q.put(('item', {'path': p, 'verdict': verdict, 'score': 50,
                                          'reasons': [{'type': 'qr', 'label': '二维码无链接内容'}], 'ocr_text': ''}))
        self.app.scan_q.put(('item', {'path': 'd:\\TopSub\\drive_pic.jpg', 'verdict': 'ad', 'score': 80,
                                      'reasons': [{'type': 'qr', 'label': '二维码含链接 x'}], 'ocr_text': ''}))
        self.app.scan_q.put(('done', None))
        self.app._poll_queue()
        self.assertEqual(len(self.app.items), 46)
        self.assertTrue(all(bool(it.get('_folder')) for it in self.app.items))
        self.assertEqual(self.app.items[0]['_folder'], 'AlbumOne/Sub')
        self.assertEqual(self.app.items[-1]['_folder'], '盘_D/TopSub')

    def test_config_dialog_prunes_removed_folders_and_scan_keeps_valid_cache(self):
        dir_a = self.root / 'KeepDir'
        dir_b = self.root / 'DropDir'
        dir_a.mkdir()
        dir_b.mkdir()
        img_a = dir_a / 'normal_a.jpg'
        img_b = dir_b / 'ad_b.jpg'
        Image.new('RGB', (400, 300), (60, 70, 80)).save(img_a)
        Image.new('RGB', (400, 300), (90, 70, 60)).save(img_b)
        self.app.add_folders([str(dir_a), str(dir_b)])
        self.app.items = [
            {'path': str(img_a), 'verdict': 'suspect', 'score': 30, 'reasons': [{'type': 'qr', 'label': '原因A'}], 'ocr_text': ''},
            {'path': str(img_b), 'verdict': 'ad', 'score': 90, 'reasons': [{'type': 'filename', 'label': '原因B'}], 'ocr_text': ''},
        ]
        self.app.checked = {str(img_a), str(img_b)}
        self.app.model.checked = self.app.checked
        self.app.refresh()
        self.app._reload_reason_filter()

        dlg = self.app.build_config()
        try:
            # 在设置弹窗内移除第二个目录并保存，应自动清理该目录残留结果与勾选
            for i in range(dlg.folder_list.count() - 1, -1, -1):
                if 'DropDir' in dlg.folder_list.item(i).text():
                    dlg.folder_list.takeItem(i)
            self.assertTrue(dlg.validate())
        finally:
            dlg.close()
            dlg.deleteLater()
        self.assertEqual([it['path'] for it in self.app.items], [str(img_a)])
        self.assertEqual(self.app.checked, {str(img_a)})
        self.assertNotIn('原因B', [self.app.f_reason.itemText(i) for i in range(self.app.f_reason.count())])

        # 验证缺少 _last_enable（模拟导入同规则配置后）首次扫描不会误清空签名匹配的缓存
        enable = {'filename': self.app.cfg.get('_enable_filename', True),
                  'shape': self.app.cfg.get('_enable_shape', True),
                  'qr': self.app.cfg['qr']['enabled'],
                  'ocr': self.app.cfg['ocr']['enabled']}
        self.app.cfg.pop('_last_enable', None)
        cache = af.Cache(af.CACHE_PATH)
        self.app._cache = cache
        cache.check_config_version(self.app.cfg, enable)
        st = img_a.stat()
        cache.put(str(img_a), st.st_size, st.st_mtime, 'ad', 88, [{'type': 'filename', 'label': '缓存命中验证'}], '')
        cache.commit()
        self.app.start_scan()
        self.assertTrue(self.pump(until=lambda: not self.app._scan_running(), timeout=5))
        self.assertEqual(self.app._cached_count, 1)
        self.assertEqual(len(self.app.items), 1)
        self.assertEqual(self.app.items[0]['reasons'][0]['label'], '缓存命中验证')

    def test_lightbox_keep_reloads_reasons_and_palette_guards_double_trigger(self):
        from cds_widgets import CDSCommandPalette
        p1 = self.root / 'keep_me.jpg'
        p2 = self.root / 'stay.jpg'
        Image.new('RGB', (400, 300), (20, 40, 60)).save(p1)
        Image.new('RGB', (400, 300), (60, 40, 20)).save(p2)
        self.app.items = [
            {'path': str(p1), 'verdict': 'suspect', 'score': 30, 'reasons': [{'type': 'qr', 'label': '独有原因X'}], 'ocr_text': ''},
            {'path': str(p2), 'verdict': 'ad', 'score': 80, 'reasons': [{'type': 'qr', 'label': '保留原因Y'}], 'ocr_text': ''},
        ]
        self.app.refresh()
        self.app._reload_reason_filter()
        self.assertIn('独有原因X', [self.app.f_reason.itemText(i) for i in range(self.app.f_reason.count())])
        self.app.mark_path(str(p1), False)
        self.assertNotIn('独有原因X', [self.app.f_reason.itemText(i) for i in range(self.app.f_reason.count())])

        calls = []
        pal = CDSCommandPalette.popup(self.app, [('单次触发', lambda: calls.append(1))])
        self.pump(.05)
        item = pal.list.item(0)
        pal.list.itemClicked.emit(item)
        pal.list.itemActivated.emit(item)
        self.assertEqual(calls, [1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
