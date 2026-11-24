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

    def test_quarantine_lock_reentrant_fixed_size_and_readonly_list(self):
        missing = self.root / 'never-created'
        self.assertEqual(af.list_quarantine(missing), [])
        self.assertFalse(missing.exists())

        qdir = self.root / 'locked-q'
        for _ in range(5):
            with af.quarantine_lock(qdir, timeout=1):
                # 同线程嵌套重入不应自死锁
                with af.quarantine_lock(qdir, timeout=0.2):
                    pass
        lock_file = qdir / '.ad-filter.lock'
        self.assertTrue(lock_file.exists())
        self.assertEqual(lock_file.stat().st_size, 1)

    def test_drive_root_path_matching_and_default_quarantine_skip(self):
        self.assertEqual(af._norm_root('D:\\'), 'D:\\')
        self.assertTrue(af._path_is_within('d:\\Album\\pic.jpg', 'D:\\'))
        self.assertFalse(af._path_is_within('D:\\ab\\pic.jpg', 'D:\\a'))
        self.assertEqual(af._root_of('d:\\Album\\pic.jpg', ['D:\\', 'D:\\Album']), 'D:\\Album')
        self.assertEqual(af._root_of('d:\\top.jpg', ['D:\\']), 'D:\\')

        default_q = self.root / af.QUARANTINE_NAME
        default_q.mkdir()
        Image.new('RGB', (400, 300), (80, 90, 100)).save(default_q / 'inside.jpg')
        self.assertEqual(af.gather_images([str(default_q)], copy.deepcopy(af.DEFAULT_CONFIG)), [])

    def test_blank_long_banner_retains_shape_suspect_with_qr_ocr_enabled(self):
        strip_img = self.root / 'plain_strip.png'
        Image.new('RGB', (1600, 80), (240, 240, 240)).save(strip_img)
        cfg = copy.deepcopy(af.DEFAULT_CONFIG)
        with af.Cache(self.root / 'blank.db') as cache:
            cache.check_config_version(cfg, {'filename': True, 'shape': True, 'qr': True, 'ocr': True})
            result = af.analyze_file(strip_img, cfg, cache, {'filename': True, 'shape': True, 'qr': True, 'ocr': True})
        self.assertEqual(result['verdict'], 'suspect')
        self.assertEqual(result['score'], 30)

    def test_load_display_image_tolerates_corrupt_exif(self):
        photo = self.root / 'bad_exif.jpg'
        Image.new('RGB', (120, 80), (60, 120, 180)).save(photo)
        with patch.object(Image.Image, 'getexif', side_effect=OSError('corrupt exif')):
            image, original = af.load_display_image(photo, max_dim=100)
        self.assertEqual(original, (120, 80))
        self.assertLessEqual(max(image.size), 100)

    def test_normalize_config_rejects_bad_regex_and_invalid_quarantine_paths(self):
        with self.assertRaises(Exception):
            af.normalize_config({'filename_rules': [{'pattern': '([bad', 'label': 'x'}]})
        with self.assertRaises(ValueError):
            af.normalize_config({'quarantine_dir': 'relative/path'})
        with self.assertRaises(ValueError):
            af.normalize_config({'quarantine_dir': 'C:\\bad\x00dir'})

    def test_recycle_delete_cluster_dedup_and_history_keep_zero(self):
        import types
        a = self.root / 'del_a.jpg'
        b = self.root / 'rec_b.jpg'
        Image.new('RGB', (400, 300), (10, 20, 30)).save(a)
        Image.new('RGB', (400, 300), (40, 50, 60)).save(b)
        missing = self.root / 'missing.jpg'

        trashed = []
        fake_mod = types.SimpleNamespace(send2trash=lambda p: trashed.append(p))
        with patch.dict(sys.modules, {'send2trash': fake_mod}):
            moved, errors, ok = af.apply_recycle([{'path': str(b)}], lambda _: None)
        self.assertEqual((moved, errors, ok), (1, [], [str(b)]))
        self.assertEqual(trashed, [str(b)])

        moved, errors, ok = af.apply_delete([{'path': str(a)}, {'path': str(missing)}], lambda _: None)
        self.assertEqual((moved, ok), (1, [str(a)]))
        self.assertEqual(len(errors), 1)
        self.assertFalse(a.exists())

        # 重复路径不应自成相似组
        self.assertEqual(af.cluster_similar([str(b), str(b)], threshold=6), [])

        orig_hist = af.HISTORY_PATH
        try:
            af.HISTORY_PATH = self.root / 'hist.json'
            af.append_history({'total': 1}, keep=10)
            self.assertEqual(len(af.load_history()), 1)
            af.append_history({'total': 2}, keep=0)
            self.assertEqual(af.load_history(), [])
        finally:
            af.HISTORY_PATH = orig_hist

    def test_bright_stroke_mask_and_dilate_match_pillow_reference(self):
        import numpy as np
        from PIL import ImageFilter
        rng = np.random.default_rng(2026)
        arr = rng.integers(0, 256, size=(120, 160, 3), dtype=np.uint8)
        arr[10:25, 20:80] = 245  # 高亮低饱和笔画块，确保超过 0.05% 门控
        reg = Image.fromarray(arr)
        hsv = np.asarray(reg.convert('HSV'))
        for thr in (200, 170, 0):
            ref_mask = (hsv[:, :, 2] >= thr) & (hsv[:, :, 1] <= 70)
            fast_mask, _, _ = af._bright_stroke_mask(arr, thr)
            self.assertIsNotNone(fast_mask)
            self.assertTrue(np.array_equal(ref_mask, fast_mask))
            ref_img = Image.fromarray(np.where(ref_mask, 0, 255).astype('uint8')).convert('RGB').filter(ImageFilter.MaxFilter(3))
            fast_img = af._bright_from_mask(fast_mask).convert('RGB')
            self.assertTrue(np.array_equal(np.asarray(ref_img), np.asarray(fast_img)))
        # 全暗图：低于 0.05% 门控直接返回 None
        dark = np.zeros((100, 100, 3), dtype=np.uint8)
        mask_none, _, _ = af._bright_stroke_mask(dark, 200)
        self.assertIsNone(mask_none)

    def test_single_open_preserves_error_labels_and_skips_decode_on_small_shape(self):
        bad = self.root / 'corrupt.jpg'
        bad.write_bytes(b'not an image')
        r_shape = af.analyze_file(bad, self.cfg, self.cache, dict(filename=False, shape=True, qr=True, ocr=False))
        self.assertEqual(r_shape['verdict'], 'error')
        self.assertTrue(r_shape['reasons'][0]['label'].startswith('读取失败'))
        r_decode = af.analyze_file(bad, self.cfg, self.cache, dict(filename=False, shape=False, qr=True, ocr=False))
        self.assertEqual(r_decode['verdict'], 'error')
        self.assertTrue(r_decode['reasons'][0]['label'].startswith('解码失败'))

        tiny = self.root / 'tiny.png'
        Image.new('RGB', (120, 120), (80, 90, 100)).save(tiny)
        with patch.object(af, 'decode_qr_texts', side_effect=AssertionError('should not decode pixels')):
            r_tiny = af.analyze_file(tiny, self.cfg, self.cache, dict(filename=False, shape=True, qr=True, ocr=False))
        self.assertEqual((r_tiny['verdict'], r_tiny['score']), ('suspect', 20))

    def test_batch_hash_cache_and_parallel_cluster_similar_equivalence(self):
        imgs = []
        base = Image.new('RGB', (480, 360), (40, 80, 120))
        ImageDraw.Draw(base).rectangle((60, 60, 300, 240), fill=(230, 180, 70))
        for i in range(6):
            p = self.root / f'sim_{i}.png'
            if i < 4:
                base.save(p)
            else:
                Image.new('RGB', (480, 360), (i * 30, 200 - i * 20, 90)).save(p)
            imgs.append(str(p))
        ref_groups = af.cluster_similar(imgs, threshold=6, cache=None)
        cached_groups_1 = af.cluster_similar(imgs, threshold=6, cache=self.cache)
        cached_groups_2 = af.cluster_similar(imgs, threshold=6, cache=self.cache)
        self.assertEqual(ref_groups, cached_groups_1)
        self.assertEqual(ref_groups, cached_groups_2)
        self.cache.put_hashes_batch([('high_bit', 10, 2.0, (1 << 64) - 3)])
        self.cache.commit()
        self.assertEqual(self.cache.get_hashes_batch([('high_bit', 10, 2.0)]), {'high_bit': (1 << 64) - 3})


if __name__ == '__main__':
    unittest.main(verbosity=2)
