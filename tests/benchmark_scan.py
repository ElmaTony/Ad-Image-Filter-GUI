# -*- coding: utf-8 -*-
"""与指定 Git 基线逐字段对比识别结果，并计时；只使用临时缓存和合成/只读样本。"""
import argparse
import copy
import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ad_filter as current
from PIL import Image, ImageDraw


def run(module, paths, root, cfg, name, parallel):
    cache = module.Cache(root / (name + '.db'))
    cache.check_config_version(cfg)
    calls = [0]
    original = module._ocr_ndarray
    def counted(image):
        calls[0] += 1
        return original(image)
    module._ocr_ndarray = counted
    start = time.perf_counter()
    try:
        if parallel:
            out = queue.Queue()
            gather = module.gather_images
            module.gather_images = lambda *_: list(paths)
            try:
                module.scan_worker([str(root)], cfg, dict(filename=True, shape=True, qr=True, ocr=True),
                                   cache, out, threading.Event(), threading.Event())
            finally:
                module.gather_images = gather
            events = list(out.queue)
            assert any(k == 'done' for k, _ in events), 'Scan did not complete'
            results = {p['path']: p for k, p in events if k == 'item'}
        else:
            results = {str(p): module.analyze_file(p, cfg, cache,
                       dict(filename=True, shape=True, qr=True, ocr=True)) for p in paths}
        duration = time.perf_counter() - start
    finally:
        module._ocr_ndarray = original
        cache.conn.close()
    return results, dict(seconds=round(duration, 3), ocr_calls=calls[0], images=len(paths))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', default='24389df')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    legacy = types.ModuleType('legacy_scan_baseline')
    legacy.__file__ = str(ROOT / 'ad_filter.py')
    source = subprocess.check_output(['git', 'show', args.baseline + ':ad_filter.py'], cwd=ROOT)
    exec(compile(source, legacy.__file__, 'exec'), legacy.__dict__)
    with tempfile.TemporaryDirectory(prefix='ad_scan_compare_') as temporary:
        root = Path(temporary)
        paths = []
        for i, text in enumerate(('今日菜单 宫保鸡丁 38元', '© 2024 Studio', '9:41 100%',
                                  '频道', 't.me/demo 免费发布', '扫码关注 加微信')):
            for size in ((900, 650), (1200, 900)):
                image = Image.new('RGB', size, (42, 56, 68))
                draw = ImageDraw.Draw(image)
                draw.rectangle((10, 10, size[0]-10, size[1]-10), outline='#b8afa0', width=5)
                draw.text((60, size[1]//2), text, font=current._load_font(36), fill='white')
                path = root / f'case_{len(paths):02d}.png'
                image.save(path)
                paths.append(path)
        # 真实样本只读，报告不记录其文件名、路径或识别文本。
        private_samples = sorted(p for p in (ROOT / 'testdata').glob('real*')
                                 if p.suffix.lower() in current.IMG_EXTS)
        paths.extend(private_samples)
        cfg = copy.deepcopy(current.DEFAULT_CONFIG)
        cfg['ocr']['enabled'] = True
        cfg['workers'] = 4
        for module in (legacy, current):
            module.CONFIG_PATH, module.HISTORY_PATH = root / 'cfg.json', root / 'hist.json'
            module.LOG_PATH, module.CACHE_PATH = root / 'log.jsonl', root / 'cache.db'
        current.get_ocr()  # 预热模型；两版共用同一套权重与 ONNX 会话。
        legacy._OCR_SINGLETON = current._OCR_SINGLETON
        report = dict(baseline=args.baseline, synthetic=12, local_readonly=len(private_samples), runs={})
        oracle = None
        for parallel in (False, True):
            for label, module in (('before', legacy), ('after', current)):
                name = label + ('_parallel' if parallel else '_serial')
                results, timing = run(module, paths, root, cfg, name, parallel)
                if oracle is None:
                    oracle = results
                changed = [i for i, path in enumerate(paths) if results[str(path)] != oracle[str(path)]]
                if changed:
                    raise AssertionError(f'{name}: result mismatch at sample indexes {changed}')
                report['runs'][name] = timing
                print(json.dumps({name: timing, 'identical': True}), flush=True)
        report['identical'] = True
        for mode in ('serial', 'parallel'):
            before = report['runs']['before_' + mode]['seconds']
            after = report['runs']['after_' + mode]['seconds']
            report[mode + '_speedup'] = round(before / after, 3)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
