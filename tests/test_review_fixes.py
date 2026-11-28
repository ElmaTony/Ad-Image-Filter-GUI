# -*- coding: utf-8 -*-
"""审查缺陷回归：全部文件、缓存、配置和界面状态使用临时目录。"""
import copy
import json
import os
import queue
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ad_filter as af
from PIL import Image, ImageDraw, ImageOps


class EngineReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ad_review_regression_')
        self.root = Path(self.temp.name)
        self.cfg = copy.deepcopy(af.DEFAULT_CONFIG)

    def tearDown(self):
        self.temp.cleanup()

    def quarantine(self):
        source = self.root / 'photos'
        source.mkdir()
        path = source / 'image.jpg'
        path.write_bytes(b'original image bytes')
        isolated = self.root / 'isolated'
        result = af.apply_quarantine([{'path': str(path)}], [str(source)], isolated, lambda _: None)
        self.assertEqual(result[:2], (1, []))
        return source, path, isolated

    def test_copied_new_quarantine_only_deletes_its_own_file(self):
        _, _, isolated = self.quarantine()
        original = Path(af.list_quarantine(isolated)[0]['dst'])
        copied = self.root / 'copied'
        shutil.copytree(isolated, copied)
        entry = af.list_quarantine(copied)[0]
        self.assertTrue(Path(entry['dst']).is_relative_to(copied))
        self.assertEqual(af.delete_quarantine_items(copied, [entry['dst']], lambda _: None), (1, []))
        self.assertTrue(original.exists())
        self.assertFalse(Path(entry['dst']).exists())

    def test_copied_legacy_manifest_and_traversal_cannot_touch_original(self):
        _, _, isolated = self.quarantine()
        entry = af.list_quarantine(isolated)[0]
        copied = self.root / 'legacy_copy'
        copied.mkdir()
        for unsafe in ({k: v for k, v in entry.items() if k != 'dst_rel'},
                       {**entry, 'dst_rel': '../outside.jpg'}):
            (copied / 'manifest.json').write_text(json.dumps({'items': [unsafe]}), encoding='utf-8')
            count, errors = af.delete_quarantine_items(copied, [entry['dst']], lambda _: None)
            self.assertEqual(count, 0)
            self.assertTrue(errors)
            self.assertTrue(Path(entry['dst']).exists())

    def test_two_processes_preserve_all_quarantine_records(self):
        isolated = self.root / 'isolated'
        code = '''
import sys, time
from pathlib import Path
import ad_filter as af
source, isolated = map(Path, sys.argv[1:])
af.apply_quarantine([{'path': str(p)} for p in source.glob('*.jpg')],
                    [str(source)], isolated, lambda _: time.sleep(.01))
'''
        processes = []
        for name in ('first', 'second'):
            source = self.root / name
            source.mkdir()
            for i in range(15):
                (source / f'{i}.jpg').write_bytes(f'{name}-{i}'.encode())
            processes.append(subprocess.Popen([sys.executable, '-B', '-c', code, str(source), str(isolated)],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        for process in processes:
            output, error = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, (output, error))
        entries = af.list_quarantine(isolated)
        self.assertEqual(len(entries), 30)
        self.assertEqual(len({e['dst'] for e in entries}), 30)
        self.assertTrue(all(Path(e['dst']).exists() for e in entries))

    def test_interrupted_move_recovers_from_durable_intent(self):
        source = self.root / 'photos'
        source.mkdir()
        path = source / 'image.jpg'
        path.write_bytes(b'crash recovery payload')
        isolated = self.root / 'isolated'
        move = shutil.move
        def interrupted(*args):
            move(*args)
            raise KeyboardInterrupt('simulated process interruption')
        with patch.object(af.shutil, 'move', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                af.apply_quarantine([{'path': str(path)}], [str(source)], isolated, lambda _: None)
        self.assertEqual(len(af.list_quarantine(isolated)), 1)
        self.assertEqual(af.restore_quarantine(isolated, lambda _: None), (1, []))
        self.assertEqual(path.read_bytes(), b'crash recovery payload')

    def test_snapshot_failure_keeps_recoverable_journal(self):
        source = self.root / 'photos'
        source.mkdir()
        path = source / 'image.jpg'
        path.write_bytes(b'payload')
        isolated = self.root / 'isolated'
        save = af._write_json_atomic
        calls = []
        def fail_checkpoint(*args):
            calls.append(1)
            if len(calls) == 2:
                raise OSError('disk full at checkpoint')
            return save(*args)
        with patch.object(af, '_write_json_atomic', side_effect=fail_checkpoint):
            count, errors, _ = af.apply_quarantine([{'path': str(path)}], [str(source)], isolated, lambda _: None)
        self.assertEqual(count, 1)
        self.assertTrue(errors)
        self.assertEqual(len(af.list_quarantine(isolated)), 1)
        self.assertEqual(af.restore_quarantine(isolated, lambda _: None), (1, []))
        self.assertTrue(path.exists())

    def test_batch_snapshot_writes_do_not_grow_with_image_count(self):
        source = self.root / 'photos'
        source.mkdir()
        paths = [source / f'{i}.jpg' for i in range(50)]
        for path in paths:
            path.write_bytes(b'payload')
        with patch.object(af, '_write_json_atomic', wraps=af._write_json_atomic) as save:
            count, errors, _ = af.apply_quarantine([{'path': str(p)} for p in paths], [str(source)],
                                                 self.root / 'isolated', lambda _: None)
        self.assertEqual((count, errors), (50, []))
        self.assertEqual(save.call_count, 2)

    def test_sparse_link_is_not_skipped_and_actual_ocr_finds_it(self):
        path = self.root / 'plain.png'
        image = Image.new('RGB', (1600, 1200), 'white')
        ImageDraw.Draw(image).text((40, 40), 'https://t.me/demo', font=af._load_font(20), fill='black')
        image.save(path)
        self.assertFalse(af.is_blank_image(image))
        self.assertTrue(af.is_blank_image(Image.new('RGB', image.size, 'white')))
        cache = af.Cache(self.root / 'cache.db')
        try:
            result = af.analyze_file(path, self.cfg, cache, dict(filename=False, shape=False, qr=False, ocr=True))
            self.assertIn(result['verdict'], ('ad', 'suspect'))
            self.assertIn('t.me', result['ocr_text'].lower())
        finally:
            cache.conn.close()

    def test_other_instance_cannot_supply_cache_for_different_switches(self):
        path = self.root / '推广图.jpg'
        Image.new('RGB', (400, 400), '#557766').save(path)
        caches = [af.Cache(self.root / 'cache.db') for _ in range(2)]
        enabled = dict(filename=True, shape=False, qr=False, ocr=False)
        disabled = {**enabled, 'filename': False}
        try:
            a, b = caches
            a.check_config_version(self.cfg, enabled)
            self.assertEqual(af.analyze_file(path, self.cfg, a, enabled)['verdict'], 'ad')
            a.commit()
            b.check_config_version(self.cfg, disabled)
            self.assertEqual(af.analyze_file(path, self.cfg, b, disabled)['verdict'], 'clean')
            b.commit()
            self.assertEqual(af.analyze_file(path, self.cfg, a, enabled)['verdict'], 'ad')
            a.commit()
            st = path.stat()
            got = b.get_batch([(str(path), st.st_size, st.st_mtime)], af.result_signature(self.cfg, disabled))
            self.assertEqual(got[str(path)]['verdict'], 'clean')
        finally:
            for cache in caches:
                cache.conn.close()

    def test_config_types_and_invalid_values(self):
        cfg = af.normalize_config({'ocr': {'max_dim': '1600', 'strip_ratio': '.25'}, 'workers': '4'})
        self.assertIsInstance(cfg['ocr']['max_dim'], int)
        self.assertIsInstance(cfg['ocr']['strip_ratio'], float)
        for data in ({'qr': {'enabled': 'false'}}, {'ocr': {'max_dim': 1.5}},
                     {'ocr': {'strip_ratio': 2}}, {'ocr': {'score_high': float('nan')}},
                     {'ocr': {'bright_thresholds': [float('inf')]}}, {'workers': True}):
            with self.assertRaises((ValueError, TypeError)):
                af.normalize_config(data)

    def test_invalid_startup_config_falls_back_without_partial_values(self):
        path = self.root / 'invalid.json'
        path.write_text(json.dumps({'workers': 7, 'qr': {'enabled': 'false'}}), encoding='utf-8')
        with patch.object(af, 'CONFIG_PATH', path):
            self.assertEqual(af.load_config(), af.DEFAULT_CONFIG)

    def test_missing_or_unreadable_directory_reports_failure(self):
        cache = af.Cache(self.root / 'cache.db')
        try:
            out = queue.Queue()
            af.scan_worker([str(self.root / 'missing')], self.cfg, {}, cache, out, threading.Event())
            self.assertEqual(list(out.queue)[-1][0], 'scan_error')
            self.assertFalse(any(k == 'done' for k, _ in out.queue))
            with patch.object(af.os, 'walk', side_effect=lambda root, onerror: onerror(PermissionError(13, 'denied', str(root)))):
                with self.assertRaises(OSError):
                    af.gather_images([str(self.root)], self.cfg)
        finally:
            cache.conn.close()

    def test_indexed_similarity_matches_greedy_reference(self):
        rnd = random.Random(14)
        values = [0, 3, 1] + [rnd.getrandbits(64) for _ in range(500)]
        values += [h ^ (1 << rnd.randrange(64)) for h in values[::3]]
        paths = [str(i) for i in range(len(values))]
        for threshold in (0, 1, 6, 10, 16, 64):
            expected = []
            for path, value in zip(paths, values):
                for representative, members in expected:
                    if (representative ^ value).bit_count() <= threshold:
                        members.append(path)
                        break
                else:
                    expected.append((value, [path]))
            expected = [members for _, members in expected if len(members) >= 2]
            with patch.object(af, 'dhash_bits', side_effect=lambda path, cache=None: values[int(str(path))]):
                self.assertEqual(af.cluster_similar(paths, threshold), expected)


class DisplayReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        import ad_filter_gui as gui
        self.gui = gui
        self.temp = tempfile.TemporaryDirectory(prefix='ad_display_regression_')
        self.root = Path(self.temp.name)
        self.names = ('CONFIG_PATH', 'CACHE_PATH', 'LOG_PATH', 'HISTORY_PATH')
        self.original = [getattr(af, name) for name in self.names]
        for name in self.names:
            setattr(af, name, self.root / name.lower())
        self.app = gui.App(copy.deepcopy(af.DEFAULT_CONFIG))
        self.app._quarantine_override = self.root / 'isolated'
        self.app._toast = self.app._info = lambda *a, **k: None

    def tearDown(self):
        self.app.close()
        self.qt.processEvents()
        for name, original in zip(self.names, self.original):
            setattr(af, name, original)
        self.temp.cleanup()

    def wait(self, condition):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self.qt.processEvents()
            if condition():
                return
            time.sleep(.01)
        self.fail('background image load timed out')

    def test_exif_orientation_and_original_pixels_on_demand(self):
        path = self.root / 'rotated.jpg'
        image = Image.new('RGB', (1200, 3200), '#558877')
        exif = image.getexif()
        exif[274] = 6
        image.save(path, exif=exif)
        display, original = af.load_display_image(path, 400)
        self.assertEqual(original, (3200, 1200))
        self.assertGreater(display.width, display.height)
        viewer = self.gui.Lightbox(self.app, str(path), self.app)
        self.wait(lambda: viewer._pix is not None)
        self.assertEqual((viewer._pix.width(), viewer._pix.height()), (2400, 900))
        viewer._fit()
        viewer.scale = 8
        viewer._ensure_detail()
        self.wait(lambda: viewer._full_loaded)
        self.assertEqual((viewer._pix.width(), viewer._pix.height()), (3200, 1200))
        self.assertEqual(viewer.scale, 8)
        viewer.close()

    def test_display_loading_is_async_and_stale_result_is_ignored(self):
        release = threading.Event()
        path = self.root / 'image.jpg'
        Image.new('RGB', (400, 300), 'red').save(path)
        real_load = af.load_display_image
        def slow(*args):
            release.wait(3)
            return real_load(*args)
        with patch.object(af, 'load_display_image', side_effect=slow):
            start = time.monotonic()
            viewer = self.gui.Lightbox(self.app, str(path), self.app)
            self.assertLess(time.monotonic() - start, .5)
            self.assertIsNone(viewer._pix)
            viewer._load(str(self.root / 'missing.jpg'))
            release.set()
            self.wait(lambda: viewer._loader.jobs.empty())
            # 确定性注入旧代次，不能覆盖当前图片。
            viewer._loaded((viewer._loader.generation-1, str(path), bytes(3), 1, 1, (1, 1), True, ''))
            self.assertIsNone(viewer._pix)
            viewer.close()

    def test_import_normalizes_persisted_numeric_strings(self):
        from PySide6.QtWidgets import QFileDialog
        path = self.root / 'import.json'
        path.write_text(json.dumps({'ocr_keywords': self.app.cfg['ocr_keywords'],
                                   'ocr': {'max_dim': '1600'}}), encoding='utf-8')
        dialog = self.app.build_config()
        with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(path), '')):
            dialog._import_cfg()
        self.assertIsInstance(self.app.cfg['ocr']['max_dim'], int)
        self.assertIsInstance(json.loads(af.CONFIG_PATH.read_text(encoding='utf-8'))['ocr']['max_dim'], int)
        dialog.close()

    def test_queued_scan_must_finish_before_actions_are_reenabled(self):
        self.app._roots = [str(self.root)]
        self.app._scan_active = True
        self.app.total_files = 6000
        for i in range(6000):
            self.app.scan_q.put(('progress', i))
        self.app.scan_q.put(('done', None))
        self.app._poll_queue()
        self.assertTrue(self.app._scan_active)
        self.assertFalse(self.app.btn_scan.isEnabled())
        self.app.start_scan()
        self.assertIsNone(self.app.scan_thread)
        while not self.app.scan_q.empty():
            self.app._poll_queue()
        self.assertFalse(self.app._scan_active)
        self.assertTrue(self.app.btn_scan.isEnabled())

    def test_preview_pending_queue_is_bounded(self):
        loader = self.app._preview_loader
        release, entered = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            release.wait(3)
            return Image.new('RGB', (1, 1)), (1, 1)
        with patch.object(af, 'load_display_image', side_effect=slow):
            loader.request('first', 720)
            self.assertTrue(entered.wait(1))
            for i in range(100):
                loader.request(str(i), 720)
                self.assertLessEqual(loader.jobs.qsize(), 1)
            results = []
            loader.ready.connect(results.append)
            release.set()
            self.wait(lambda: bool(results))
            self.assertEqual([result[1] for result in results], ['99'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
