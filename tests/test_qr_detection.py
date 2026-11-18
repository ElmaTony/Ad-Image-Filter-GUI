# -*- coding: utf-8 -*-
"""用真实解码器回归二维码误报、损坏码及命中理由。"""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import qrcode
from PIL import Image, ImageDraw, ImageFilter
from pyzbar.pyzbar import decode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ad_filter as af


def qr_modules(text, version=4):
    qr = qrcode.QRCode(version=version, border=0, error_correction=qrcode.constants.ERROR_CORRECT_H)
    qr.add_data(text)
    qr.make(fit=False)
    return np.asarray(qr.get_matrix(), dtype=bool)


def render(modules, module_size=7):
    pixels = np.where(np.pad(modules, 4), 0, 255).astype(np.uint8)
    side = len(pixels) * module_size
    return Image.fromarray(pixels).resize((side, side), Image.Resampling.NEAREST).convert('RGB')


class QRDetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='adfilter_qr_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = af.Cache(self.root / 'cache.db')
        self.addCleanup(self.cache.conn.close)
        self.cfg = copy.deepcopy(af.DEFAULT_CONFIG)
        self.switches = dict(filename=False, shape=False, qr=True, ocr=False)

    def analyze(self, image, name='sample.png'):
        path = self.root / name
        image.save(path)
        return af.analyze_file(path, self.cfg, self.cache, self.switches)

    def test_linear_barcode_is_not_a_qr_code(self):
        # 全零 EAN-13，包含左右保护位及有效校验位。
        bars = '101' + '0001101' * 6 + '01010' + '1110010' * 6 + '101'
        image = Image.new('RGB', (460, 200), 'white')
        draw = ImageDraw.Draw(image)
        for i, bit in enumerate(bars):
            if bit == '1':
                draw.rectangle((40+4*i, 20, 43+4*i, 175), fill='black')
        self.assertIn('EAN13', [symbol.type for symbol in decode(image)])
        self.assertEqual(af.decode_qr_texts(image), [])
        self.assertEqual(self.analyze(image)['verdict'], 'clean')

    def test_checkerboard_stripes_and_tiles_with_finder_shapes_are_ignored(self):
        reference = qr_modules('https://t.me/qr')
        rows, columns = np.indices(reference.shape)
        rng = np.random.default_rng(0)
        for i, modules in enumerate(((rows+columns) % 2 == 0, columns % 2 == 0,
                                      rng.random(reference.shape) > .5)):
            with self.subTest(i=i):
                modules[:8, :8] = reference[:8, :8]
                modules[:8, -8:] = reference[:8, -8:]
                modules[-8:, :8] = reference[-8:, :8]
                image = render(modules)
                _, decoded, corners, _ = cv2.QRCodeDetector().detectAndDecodeMulti(np.asarray(image.convert('L')))
                self.assertIsNotNone(corners, '样本必须能触发原先的候选误报')
                self.assertFalse(any(decoded))
                self.assertEqual(af.detect_qr(image), ([], False))
                self.assertEqual(self.analyze(image, f'tiles_{i}.png')['verdict'], 'clean')

    def test_logo_and_blur_do_not_hide_a_decodable_qr_link(self):
        modules = qr_modules('https://t.me/demo')
        center = len(modules)//2
        modules[center-3:center+4, center-3:center+4] = False
        image = render(modules, module_size=4).filter(ImageFilter.GaussianBlur(.65))
        result = self.analyze(image)
        self.assertEqual((result['verdict'], result['score']), ('ad', 80))
        self.assertIn('https://t.me/demo', result['reasons'][0]['label'])

    def test_opencv_decodes_link_without_pyzbar(self):
        image = render(qr_modules('https://t.me/qr'))
        with patch.object(af, 'decode_qr_texts', return_value=[]):
            self.assertEqual(self.analyze(image)['verdict'], 'ad')

    def test_damaged_payload_with_valid_structure_remains_suspect(self):
        modules = qr_modules('https://t.me/qr')
        rng = np.random.default_rng(0)
        modules[9:, 9:] = rng.random(modules[9:, 9:].shape) > .5
        for degrees in (0, 90, 180, 270):
            with self.subTest(degrees=degrees):
                image = render(modules).rotate(degrees)
                self.assertEqual(af.decode_qr_texts(image), [])
                self.assertEqual(af.detect_qr(image), ([], True))
                result = self.analyze(image, f'damaged_{degrees}.png')
                self.assertEqual(result['verdict'], 'suspect')
                self.assertIn('未能解码', result['reasons'][0]['label'])

    def test_plain_text_qr_remains_suspect_and_evidence_uses_matching_code(self):
        plain = render(qr_modules('Local ID 0042'))
        result = self.analyze(plain)
        self.assertEqual(result['verdict'], 'suspect')
        self.assertEqual(result['reasons'][0]['label'], '二维码无链接内容')
        link = render(qr_modules('https://t.me/qr'))
        pair = Image.new('RGB', (plain.width + link.width, plain.height), 'white')
        pair.paste(plain, (0, 0))
        pair.paste(link, (plain.width, 0))
        self.assertCountEqual(af.decode_qr_texts(pair), ['Local ID 0042', 'https://t.me/qr'])
        result = self.analyze(pair, 'multiple.png')
        self.assertEqual(result['verdict'], 'ad')
        self.assertEqual(result['reasons'][0]['label'], '二维码含链接 https://t.me/qr')

    def test_prior_qr_false_positive_is_not_reused_from_cache(self):
        path = self.root / 'plain.png'
        image = Image.new('RGB', (320, 320), 'white')
        ImageDraw.Draw(image).rectangle((10, 10, 80, 80), fill='gray')
        image.save(path)
        stat = path.stat()
        import hashlib
        import json
        keys = ('filename_rules', 'ocr_keywords', 'whitelist_rules', 'shape', 'qr', 'ocr')
        old_blob = dict(config={key: self.cfg[key] for key in keys}, enable=self.switches, revision=2)
        old_version = hashlib.sha256(json.dumps(old_blob, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        self.cache.put(str(path), stat.st_size, stat.st_mtime, 'suspect', 30,
                       [{'type': 'qr', 'label': '检测到二维码图形'}], '', old_version)
        self.cache.commit()
        result = af.analyze_file(path, self.cfg, self.cache, self.switches)
        self.assertEqual((result['verdict'], result['reasons']), ('clean', []))


if __name__ == '__main__':
    unittest.main(verbosity=2)
