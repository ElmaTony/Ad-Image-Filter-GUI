# -*- coding: utf-8 -*-
"""扫描调度、OCR 复用与文件恢复回归；所有文件写入均在临时隔离目录。"""
import copy
import json
import queue
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image, ImageDraw
import ad_filter as af


class ScanEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ad_engine_regression_')
        self.root = Path(self.temporary.name)
        self.cache = af.Cache(self.root / 'cache.db')
        self.cfg = copy.deepcopy(af.DEFAULT_CONFIG)
        self.cfg['workers'] = 2
        self.enable = dict(filename=True, shape=False, qr=False, ocr=False)
        self.out = queue.Queue()
        self.cancel, self.pause = threading.Event(), threading.Event()

    def tearDown(self):
        self.cancel.set()
        self.pause.clear()
        self.cache.conn.close()
        self.temporary.cleanup()

    def files(self, count=4):
        paths = []
        for index in range(count):
            path = self.root / f'image_{index}.png'
            image = Image.new('RGB', (900, 650), '#404050')
            ImageDraw.Draw(image).text((40, 40), 'ABC', fill='white')
            ImageDraw.Draw(image).rectangle((100, 100, 500, 350), fill='#807060')
            image.save(path)
            paths.append(path)
        return paths

    def worker(self):
        af.scan_worker([str(self.root)], self.cfg, self.enable, self.cache,
                       self.out, self.cancel, self.pause)

    def test_unsigned_hash_roundtrip_and_threshold_does_not_clear_results(self):
        for index, value in enumerate((0, 1 << 63, (1 << 64) - 1)):
            self.cache.put_hash(str(index), 123, 1.0, value)
            self.assertEqual(self.cache.get_hash(str(index), 123, 1.0), value)
        self.cache.check_config_version(self.cfg)
        self.cache.put('one', 1, 1, 'ad', 99, [], '')
        self.cfg['phash']['threshold'] = 10
        self.assertTrue(self.cache.check_config_version(self.cfg))
        self.assertEqual(self.cache.get('one', 1, 1)['verdict'], 'ad')
        self.cfg['ocr']['score_high'] += 1
        self.assertFalse(self.cache.check_config_version(self.cfg))
        self.assertIsNone(self.cache.get('one', 1, 1))

    def test_small_image_reuses_inference_but_preserves_accumulated_text(self):
        path = self.files(1)[0]
        self.enable['ocr'] = True
        self.cfg['ocr']['bright_thresholds'] = []
        with patch.object(af, '_ocr_ndarray', return_value=['频道']) as inference:
            result = af.analyze_file(path, self.cfg, self.cache, self.enable)
        self.assertEqual(inference.call_count, 1)
        self.assertEqual(result['verdict'], 'suspect')
        self.assertEqual(result['ocr_text'], '频道频道')
        self.assertEqual(result['score'], 1)

    def test_inference_failure_is_visible_and_never_cached_as_clean(self):
        path = self.files(1)[0]
        self.enable['ocr'] = True
        with patch.object(af, '_ocr_ndarray', side_effect=RuntimeError('test inference error')):
            result = af.analyze_file(path, self.cfg, self.cache, self.enable)
        self.assertEqual(result['verdict'], 'error')
        self.assertEqual(result['reasons'][-1]['type'], 'error')
        with patch.object(af, '_ocr_ndarray', return_value=[]) as retry:
            result = af.analyze_file(path, self.cfg, self.cache, self.enable)
        self.assertTrue(retry.called)
        self.assertEqual(result['verdict'], 'clean')

    def test_ocr_thread_facades_share_sessions_without_shared_preprocessor(self):
        import types
        prototype = types.SimpleNamespace(text_det=types.SimpleNamespace(preprocess_op=None, infer=object()))
        barrier = threading.Barrier(2)
        def inspect(index):
            engine = af.get_ocr()
            engine.text_det.preprocess_op = index
            barrier.wait(timeout=3)
            return engine.text_det.preprocess_op, engine.text_det.infer
        with patch.object(af, '_OCR_SINGLETON', prototype):
            with ThreadPoolExecutor(2) as pool:
                values = list(pool.map(inspect, (1, 2)))
        self.assertEqual([v[0] for v in values], [1, 2])
        self.assertTrue(all(v[1] is prototype.text_det.infer for v in values))

    def test_slow_first_image_does_not_block_refilling_other_worker(self):
        paths = self.files(3)
        first_started, third_started, release = (threading.Event() for _ in range(3))
        def analyze(path, *_):
            if path == paths[0]:
                first_started.set()
                release.wait(3)
            if path == paths[2]:
                third_started.set()
            return dict(path=str(path), verdict='clean', score=0, reasons=[], ocr_text='')
        with patch.object(af, 'gather_images', return_value=paths), patch.object(af, 'analyze_file', side_effect=analyze):
            worker = threading.Thread(target=self.worker)
            worker.start()
            try:
                self.assertTrue(first_started.wait(2))
                self.assertTrue(third_started.wait(2), 'free worker waited for the slow image')
            finally:
                release.set()
                worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(sum(k == 'item' for k, _ in self.out.queue), 3)

    def test_cancel_waits_for_running_jobs_before_sending_terminal_event(self):
        paths = self.files(4)
        started, release = threading.Event(), threading.Event()
        def analyze(path, *_):
            started.set()
            release.wait(3)
            return dict(path=str(path), verdict='clean', score=0, reasons=[], ocr_text='')
        with patch.object(af, 'gather_images', return_value=paths), patch.object(af, 'analyze_file', side_effect=analyze):
            worker = threading.Thread(target=self.worker)
            worker.start()
            try:
                self.assertTrue(started.wait(2))
                self.cancel.set()
                time.sleep(.1)
                self.assertTrue(worker.is_alive())
                self.assertFalse(any(k == 'cancelled' for k, _ in self.out.queue))
            finally:
                release.set()
                worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(list(self.out.queue)[-1][0], 'cancelled')
        self.assertFalse(any(k == 'done' for k, _ in self.out.queue))
        count = self.out.qsize()
        time.sleep(.1)
        self.assertEqual(self.out.qsize(), count)

    def test_pause_stops_submitting_new_jobs(self):
        paths = self.files(4)
        started = []
        release = threading.Event()
        def analyze(path, *_):
            started.append(path)
            self.pause.set()
            release.wait(3)
            return dict(path=str(path), verdict='clean', score=0, reasons=[], ocr_text='')
        with patch.object(af, 'gather_images', return_value=paths), patch.object(af, 'analyze_file', side_effect=analyze):
            worker = threading.Thread(target=self.worker)
            worker.start()
            try:
                self.assertTrue(self.pause.wait(2))
                release.set()
                time.sleep(.15)
                self.assertLessEqual(len(started), 2)
                self.pause.clear()
                # Following jobs also pause themselves: cancel and drain cleanly.
                self.cancel.set()
            finally:
                self.pause.clear()
                release.set()
                self.cancel.set()
                worker.join(4)
        self.assertFalse(worker.is_alive())

    def test_initialization_failure_finishes_with_error(self):
        self.files(1)
        self.enable['ocr'] = True
        with patch.object(af, 'get_ocr', side_effect=RuntimeError('missing model')):
            self.worker()
        self.assertEqual(list(self.out.queue)[-1], ('scan_error', 'missing model'))

    def test_quarantine_and_restore_name_collisions_never_overwrite(self):
        photos, quarantine = self.root / 'photos', self.root / 'isolated'
        photos.mkdir()
        path = photos / 'same.jpg'
        payloads = []
        for index in range(4):
            payload = f'image {index}'.encode()
            path.write_bytes(payload)
            payloads.append(payload)
            moved, errors, _ = af.apply_quarantine([dict(path=str(path))], [str(photos)], quarantine, lambda _: None)
            self.assertEqual((moved, errors), (1, []))
        entries = af.list_quarantine(quarantine)
        self.assertEqual(len({item['dst'] for item in entries}), 4)
        self.assertEqual({Path(item['dst']).read_bytes() for item in entries}, set(payloads))
        path.write_bytes(b'existing original')
        restored, errors = af.restore_quarantine(quarantine, lambda _: None)
        self.assertEqual((restored, errors), (4, []))
        self.assertEqual(path.read_bytes(), b'existing original')
        self.assertEqual({p.read_bytes() for p in photos.iterdir()}, set(payloads + [b'existing original']))

    def test_corrupt_manifest_preserves_source_and_manifest(self):
        path = self.files(1)[0]
        quarantine = self.root / 'isolated'
        quarantine.mkdir()
        manifest = quarantine / 'manifest.json'
        for content in ('{broken', '{"items":null}', '{"items":[{}]}'):
            manifest.write_text(content, encoding='utf-8')
            moved, errors, paths = af.apply_quarantine([dict(path=str(path))], [str(self.root)], quarantine, lambda _: None)
            self.assertEqual((moved, paths), (0, []))
            self.assertTrue(errors)
            self.assertTrue(path.exists())
            self.assertEqual(manifest.read_text(encoding='utf-8'), content)

    def test_manifest_write_failure_rolls_back_quarantine_move(self):
        path = self.files(1)[0]
        original = path.read_bytes()
        quarantine = self.root / 'isolated'
        with patch.object(af, '_write_json_atomic', side_effect=OSError('test disk full')):
            moved, errors, paths = af.apply_quarantine([dict(path=str(path))], [str(self.root)], quarantine, lambda _: None)
        self.assertEqual((moved, paths), (0, []))
        self.assertTrue(errors)
        self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main(verbosity=2)
