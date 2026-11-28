#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
广告图过滤小工具（纯本地）
================================
扫描本地文件夹中的图片，按多层信号识别疑似广告图：
  1. 文件名规则（关键词/正则，可编辑）
  2. 形状规则（极端长宽比 / 过小分辨率，只读文件头）
  3. 二维码检测（opencv，本地）
  4. OCR 文字识别（rapidocr 本地模型；上下条带裁切 + 原图/亮通两遍识别，
     专治半透明空心水印字），按推广词库聚合计分

命中的图列进审查列表，人工勾选后处置：
  隔离目录（可一键恢复，默认） / 系统回收站 / 直接删除（二次确认）

用法：
  python ad_filter.py                    # 启动 GUI
  python ad_filter.py --make-fixtures    # 生成测试样本到 testdata/
  python ad_filter.py --selftest         # 无界面跑识别引擎自测
  python ad_filter.py --smoke            # GUI 冒烟（起 2 秒自动退出）
  python ad_filter.py --bench            # 性能基准
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import queue
import re
import shutil
import sqlite3
import stat
import sys
import threading
import time
import tempfile
import math
import hashlib
from contextlib import contextmanager
from functools import lru_cache, wraps
import unicodedata
from pathlib import Path

# 单文件打包时，资源位于模块旁的解包目录，用户数据则仍在 EXE 旁。
APP_VERSION = Path(__file__).with_name('VERSION').read_text(encoding='utf-8').strip()

if getattr(sys, "frozen", False):
    # PyInstaller 打包后 __file__ 指向临时解压目录，配置/缓存要放 exe 旁边
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "ad_filter_config.json"
CACHE_PATH = APP_DIR / "ad_filter_cache.db"
LOG_PATH = APP_DIR / "ad_filter_log.jsonl"
HISTORY_PATH = APP_DIR / "ad_filter_history.json"
QUARANTINE_NAME = "_广告隔离"

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

    Image.MAX_IMAGE_PIXELS = None  # 本地可信文件，放开 DecompressionBomb 限制
except ImportError:
    print("缺少 pillow：pip install pillow")
    raise

DEFAULT_CONFIG = {
    "filename_rules": [
        {"pattern": r"电报群@|TG@|tg@|t\.me|telegraph", "label": "文件名TG渠道"},
        {"pattern": r"@[A-Za-z0-9_]{4,}", "label": "文件名@渠道号"},
        {"pattern": r"预览_|宣传图|推广图|广告图|banner", "label": "文件名推广词"},
    ],
    "ocr_keywords": [
        {"pattern": r"t\.me|telegram|https?://|www\.[a-z0-9]|(?:\.com|\.net|\.cc|\.vip|\.xyz)(?![a-z])", "weight": 3, "label": "链接"},
        {"pattern": r"免费发布|免费领取|免费看|资源由|不收费|没有门槛|无门槛|浏览器搜|进我的频道|我的频道|关注公众号|加微信|加QQ|扫码关注|独家首发|入群|粉丝群|投稿", "weight": 3, "label": "推广话术"},
        {"pattern": r"@[A-Za-z0-9_]{4,}", "weight": 2, "label": "@渠道号"},
        {"pattern": r"频道", "weight": 1, "label": "弱词"},  # “免费/发布”实测在正常图高频误伤，只留“频道”
    ],
    "whitelist_rules": [],
    "shape": {"max_ratio_hard": 8.0, "min_side": 300},
    "qr": {"enabled": True},
    "ocr": {
        "enabled": False,
        "strip_ratio": 0.25,
        "full_image_if_nothing": True,
        "bright_thresholds": [200],  # 单提亮：实测较 [200,170] 提速 47% 且 17 张样本零漏检
        "max_dim": 1600,  # OCR 输入缩放上限；2200 太慢，1600 精度损失可控、提速约 30%
        "score_high": 6,
    },
    "workers": 4,
    "disposal": "quarantine",
    "quarantine_dir": "",
    "quarantine_locations": [],
    "phash": {"enabled": True, "threshold": 6},
    "ui_palette": "atelier",
    "ui_density": "compact",
}


def normalize_config(user: dict) -> dict:
    """配置导入和启动共用的类型边界；先验证副本，再交给界面和引擎。"""
    if not isinstance(user, dict):
        raise ValueError('配置须为 JSON 对象')
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    for key, value in user.items():
        if isinstance(cfg.get(key), dict):
            if not isinstance(value, dict):
                raise ValueError(f'{key} 须为配置对象')
            cfg[key].update(value)
        else:
            cfg[key] = copy.deepcopy(value)

    def number(container, key, low, high, integer=False):
        raw = container[key]
        if isinstance(raw, bool):
            raise ValueError(f'{key} 须为数字')
        value = float(raw)
        if not math.isfinite(value) or not low <= value <= high or (integer and not value.is_integer()):
            raise ValueError(f'{key} 超出允许范围 {low}–{high}')
        container[key] = int(value) if integer else value

    def boolean(container, key):
        if not isinstance(container[key], bool):
            raise ValueError(f'{key} 须为 true 或 false')

    number(cfg, 'workers', 1, 256, True)
    number(cfg['phash'], 'threshold', 0, 64, True)
    number(cfg['shape'], 'min_side', 1, 100000, True)
    number(cfg['shape'], 'max_ratio_hard', 1, 100000)
    if 'max_ratio' in cfg['shape']:
        number(cfg['shape'], 'max_ratio', 1, 100000)
        cfg['shape'].pop('max_ratio', None)
    for key, low, high, integer in (
            ('strip_ratio', .001, 1, False), ('max_dim', 32, 100000, True),
            ('score_high', .001, 1000000, False)):
        number(cfg['ocr'], key, low, high, integer)
    if 'score_low' in cfg['ocr']:
        number(cfg['ocr'], 'score_low', .001, 1000000)
        cfg['ocr'].pop('score_low', None)
    thresholds = cfg['ocr']['bright_thresholds']
    if not isinstance(thresholds, list):
        raise ValueError('bright_thresholds 须为数字列表')
    cfg['ocr']['bright_thresholds'] = list(thresholds)
    for i in range(len(thresholds)):
        number(cfg['ocr']['bright_thresholds'], i, 0, 255, True)
    for section in ('ocr', 'qr', 'phash'):
        boolean(cfg[section], 'enabled')
    boolean(cfg['ocr'], 'full_image_if_nothing')
    for key in ('_enable_filename', '_enable_shape'):
        if key in cfg:
            boolean(cfg, key)
    for key in ('filename_rules', 'ocr_keywords', 'whitelist_rules'):
        rules = cfg[key]
        if not isinstance(rules, list):
            raise ValueError(f'{key} 须为规则列表')
        for rule in rules:
            if not isinstance(rule, dict) or not isinstance(rule.get('pattern'), str):
                raise ValueError(f'{key} 包含无效规则')
            re.compile(rule['pattern'])
            if 'label' in rule and not isinstance(rule['label'], str):
                raise ValueError('规则名称须为文本')
            if key == 'ocr_keywords':
                rule.setdefault('weight', 1)
                number(rule, 'weight', 1, 10000, True)
    if cfg['disposal'] not in ('quarantine', 'recycle', 'delete'):
        raise ValueError('处置方式无效')
    qdir = cfg['quarantine_dir']
    if not isinstance(qdir, str) or '\x00' in qdir:
        raise ValueError('隔离目录须为路径文本')
    if qdir.strip() and not Path(qdir.strip()).expanduser().is_absolute():
        raise ValueError('隔离目录须为完整路径')
    locations = cfg['quarantine_locations']
    if not isinstance(locations, list) or not all(
            isinstance(p, str) and '\x00' not in p and Path(p).is_absolute() for p in locations):
        raise ValueError('隔离区历史目录须为完整路径列表')
    if cfg.get('ui_palette', 'atelier') not in ('atelier', 'slate', 'sage', 'obsidian'):
        raise ValueError('主题色盘无效')
    if cfg.get('ui_density', 'compact') not in ('compact', 'standard', 'large'):
        raise ValueError('卡片密度无效')
    if 'ui_theme' in cfg and cfg['ui_theme'] not in ('light', 'dark'):
        raise ValueError('主题明暗模式无效')
    return cfg


def load_config() -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    loaded_user = False
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg = normalize_config(user)
            loaded_user = True
        except Exception as e:
            print(f"配置读取失败，使用默认配置：{e}")
    if loaded_user:
        # —— 旧配置迁移：历史版本默认把 max_dim 写成 2200，比现默认 1600 慢一倍多；
        #    仅迁移历史默认值 2200，保留用户显式设定的其他合法数值。
        try:
            ocr_cfg = cfg.get("ocr", {})
            default_max = DEFAULT_CONFIG["ocr"]["max_dim"]
            if int(ocr_cfg.get("max_dim", default_max)) == 2200:
                ocr_cfg["max_dim"] = default_max
            # 线程数超过 CPU 核数没有收益（超订阅反而更慢），但至少保留默认值 4 以兼容低核环境预期
            cpu = max(4, min(16, os.cpu_count() or 4))
            if int(cfg.get("workers", 4)) > cpu:
                cfg["workers"] = cpu
        except Exception:
            pass
    return cfg


def result_signature(cfg, enable=None):
    keys = ('filename_rules', 'ocr_keywords', 'whitelist_rules', 'shape', 'qr', 'ocr')
    switches = dict(filename=enable.get('filename', True), shape=enable.get('shape', True),
                    qr=enable.get('qr', False), ocr=enable.get('ocr', False)) if enable is not None else dict(
        filename=cfg.get('_enable_filename', True), shape=cfg.get('_enable_shape', True),
        qr=cfg['qr']['enabled'], ocr=cfg['ocr']['enabled'])
    conf = {k: copy.deepcopy(cfg.get(k)) for k in keys}
    if isinstance(conf.get('shape'), dict):
        conf['shape'].pop('max_ratio', None)
    if isinstance(conf.get('ocr'), dict):
        conf['ocr'].pop('score_low', None)
    blob = dict(config=conf, enable=switches, revision=4 if switches['qr'] else 2)
    return hashlib.sha256(json.dumps(blob, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def save_config(cfg: dict) -> None:
    """原子写：先写临时文件再 os.replace，避免写一半崩溃导致配置损坏。"""
    _write_json_atomic(CONFIG_PATH, cfg)


def _write_json_atomic(path: Path, data) -> None:
    tmp = None
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            tmp = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


_WS_RE = re.compile(r"\s+")
_HWS_MULTI_RE = re.compile(r"[^\S\n]+")
_ASCII_GAP_RE = re.compile(r"(?<=[A-Za-z0-9]) (?=[A-Za-z0-9])")
_DOT_GAP_RE = re.compile(r"([A-Za-z]{2,}\.) (?=[A-Za-z])")
_QR_LINK_RE = re.compile(
    r"t\.me|https?://|http|telegram|(?:weixin|wxp|mqqapi|alipays)://|www\.[a-z0-9-]+"
    r"|(?:^|[^a-z0-9@])[a-z0-9-]+\.(?:com|net|org|cn|cc|vip|xyz|top|me|io|tv|gg|app|club|site|online|link|work|shop)(?:[/\?#:\s]|$)",
    re.I,
)
_QR_COMPACT_LINK_RE = re.compile(r"t\.me|http|telegram")


@lru_cache(maxsize=512)
def _compile_rule_re(pattern: str):
    return re.compile(pattern, re.I)


def _normalize_line(line: str) -> str:
    line = _HWS_MULTI_RE.sub(" ", line).strip()
    if not line:
        return ""
    # 保留两个 ASCII 词及“英文单词+句点+英文单词”间的空格，避免跨词粘连引发 .com 误报或漏检
    line = _ASCII_GAP_RE.sub("\x00", line)
    line = _DOT_GAP_RE.sub("\\1\x00", line)
    return line.replace(" ", "").replace("\x00", " ").lower()


def normalize_text(s: str) -> str:
    if not s:
        return ""
    if not s.isascii():
        s = unicodedata.normalize("NFKC", s)
    if "\n" not in s:
        return _normalize_line(s)
    lines = [part for raw in s.splitlines() if (part := _normalize_line(raw))]
    return "\n".join(lines)


# ---------------------------------------------------------------- 缓存
class Cache:
    def __init__(self, path: Path):
        # GUI 扫描在多线程里写缓存，允许跨线程 + 程序内锁串行化
        self.conn = sqlite3.connect(str(path), timeout=15.0, check_same_thread=False)
        try:
            self.conn.execute("PRAGMA busy_timeout=15000")
            try:
                self.conn.execute("PRAGMA journal_mode=WAL")
                self.conn.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.OperationalError:
                pass
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS results(
                    path TEXT, size INTEGER, mtime REAL,
                    verdict TEXT, score REAL, reasons TEXT, ocr_text TEXT, ts REAL,
                    cfg_hash TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (path, cfg_hash))"""
            )
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS hashes(path TEXT PRIMARY KEY, size INTEGER, mtime REAL, hash INTEGER)"
            )
            self.conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
            cols = list(self.conn.execute("PRAGMA table_info(results)"))
            col_names = {row[1] for row in cols}
            pk_cols = {row[1] for row in cols if row[5] > 0}
            if 'cfg_hash' not in col_names:
                try:
                    self.conn.execute("ALTER TABLE results ADD COLUMN cfg_hash TEXT NOT NULL DEFAULT ''")
                except sqlite3.OperationalError:
                    cols = list(self.conn.execute("PRAGMA table_info(results)"))
                    col_names = {row[1] for row in cols}
                    if 'cfg_hash' not in col_names:
                        raise
            if cols and pk_cols != {'path', 'cfg_hash'}:
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS results_v2(
                        path TEXT, size INTEGER, mtime REAL,
                        verdict TEXT, score REAL, reasons TEXT, ocr_text TEXT, ts REAL,
                        cfg_hash TEXT NOT NULL DEFAULT '',
                        PRIMARY KEY (path, cfg_hash))"""
                )
                self.conn.execute(
                    """INSERT OR REPLACE INTO results_v2(path, size, mtime, verdict, score, reasons, ocr_text, ts, cfg_hash)
                       SELECT path, size, mtime, verdict, score, reasons, ocr_text, ts, cfg_hash FROM results"""
                )
                self.conn.execute("DROP TABLE results")
                self.conn.execute("ALTER TABLE results_v2 RENAME TO results")
            self.conn.commit()
        except Exception:
            self.conn.close()
            raise
        self.lock = threading.Lock()
        self.result_version = ''
        self._writes_since_prune = 0
        self.prune()

    def close(self):
        with self.lock:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def prune(self, max_rows: int = 80000, keep_rows: int = 60000):
        """超出容量上限时按时间/写入顺序淘汰最旧缓存，防止本地缓存无限膨胀。"""
        with self.lock:
            n_res = self.conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
            changed = False
            if n_res > max_rows:
                drop_n = max(0, n_res - keep_rows)
                if drop_n:
                    self.conn.execute(
                        "DELETE FROM results WHERE rowid IN (SELECT rowid FROM results ORDER BY ts ASC LIMIT ?)",
                        (drop_n,),
                    )
                    changed = True
            n_hash = self.conn.execute("SELECT COUNT(*) FROM hashes").fetchone()[0]
            if n_hash > max_rows:
                drop_h = max(0, n_hash - keep_rows)
                if drop_h:
                    self.conn.execute(
                        "DELETE FROM hashes WHERE rowid IN (SELECT rowid FROM hashes ORDER BY rowid ASC LIMIT ?)",
                        (drop_h,),
                    )
                    changed = True
            if changed:
                self.conn.commit()

    def check_config_version(self, cfg: dict, enable=None) -> bool:
        """比对当前配置签名，支持多配置签名并存缓存，不互相破坏历史结果。
        返回 True=当前签名与上次一致，False=配置已变更（已切换当前签名）。"""
        h = result_signature(cfg, enable)
        with self.lock:
            self.result_version = h
            row = self.conn.execute("SELECT value FROM meta WHERE key='cfg_hash'").fetchone()
            if row and row[0] == h:
                return True
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES('cfg_hash', ?)", (h,))
            self.conn.commit()
        return False

    def get(self, path: str, size: int, mtime: float, version=None):
        ver = self.result_version if version is None else version
        norm_p = os.path.normcase(path)
        with self.lock:
            if norm_p == path:
                row = self.conn.execute(
                    "SELECT verdict, score, reasons, ocr_text FROM results WHERE path=? AND size=? AND mtime=? AND cfg_hash=?",
                    (norm_p, size, mtime, ver),
                ).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT verdict, score, reasons, ocr_text FROM results WHERE path IN (?,?) AND size=? AND mtime=? AND cfg_hash=?",
                    (norm_p, path, size, mtime, ver),
                ).fetchone()
        if row and row[0] != 'error':
            reasons_raw = row[2]
            return {"verdict": row[0], "score": row[1],
                    "reasons": [] if reasons_raw == "[]" else json.loads(reasons_raw),
                    "ocr_text": row[3], "_stat": (size, mtime)}
        return None

    def get_batch(self, file_infos: list[tuple[str, int, float]], version=None) -> dict[str, dict]:
        """批量查询缓存：path+size+mtime 命中则返回 {path: result}（兼容 Windows 盘符与路径大小写差异）。
        分批查询：SQLite 的 SQL 变量数上限（32766）在大目录（数万张图）会被突破，
        一旦 OperationalError 冒出去整个扫描线程就崩了。"""
        if not file_infos:
            return {}
        ver = self.result_version if version is None else version
        want_by_norm: dict[str, list[tuple[str, int, float]]] = {}
        for p, s, m in file_infos:
            want_by_norm.setdefault(os.path.normcase(p), []).append((p, s, m))
        out: dict[str, dict] = {}
        BATCH = 400
        for i in range(0, len(file_infos), BATCH):
            query_keys = list(dict.fromkeys(
                k for p, _, _ in file_infos[i:i + BATCH] for k in (os.path.normcase(p), p)
            ))
            ph = ",".join("?" * len(query_keys))
            with self.lock:
                rows = self.conn.execute(
                    f"SELECT path, size, mtime, verdict, score, reasons, ocr_text FROM results WHERE path IN ({ph}) AND cfg_hash=?",
                    query_keys + [ver],
                ).fetchall()
            for r in rows:
                if r[3] == 'error':
                    continue
                candidates = want_by_norm.get(os.path.normcase(r[0]))
                if not candidates:
                    continue
                for orig_p, s, m in candidates:
                    if r[1] == s and r[2] == m:
                        reasons_raw = r[5]
                        out[orig_p] = {"verdict": r[3], "score": r[4],
                                       "reasons": [] if reasons_raw == "[]" else json.loads(reasons_raw),
                                       "ocr_text": r[6], "_stat": (r[1], r[2])}
        return out

    def clear_results(self):
        """清空分析结果缓存（识别方式开关变化时必须全量重判，缓存键不含 enable）。"""
        with self.lock:
            self.conn.execute("DELETE FROM results")
            self.conn.commit()

    def put_hash(self, path: str, size: int, mtime: float, hash_val: int):
        """dHash 指纹缓存（相似聚类用，normcase(path)+size+mtime 键）。"""
        norm_p = os.path.normcase(path)
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO hashes VALUES(?,?,?,?)",
                (norm_p, size, mtime, hash_val if hash_val < (1 << 63) else hash_val - (1 << 64)),
            )

    def put_hashes_batch(self, entries: list[tuple[str, int, float, int]]):
        if not entries:
            return
        rows = [(os.path.normcase(p), s, m, h if h < (1 << 63) else h - (1 << 64)) for p, s, m, h in entries]
        with self.lock:
            self.conn.executemany("INSERT OR REPLACE INTO hashes VALUES(?,?,?,?)", rows)

    def get_hash(self, path: str, size: int, mtime: float):
        norm_p = os.path.normcase(path)
        with self.lock:
            if norm_p == path:
                row = self.conn.execute(
                    "SELECT hash FROM hashes WHERE path=? AND size=? AND mtime=?",
                    (norm_p, size, mtime),
                ).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT hash FROM hashes WHERE path IN (?,?) AND size=? AND mtime=?",
                    (norm_p, path, size, mtime),
                ).fetchone()
        return (row[0] & ((1 << 64) - 1)) if row else None

    def get_hashes_batch(self, file_infos: list[tuple[str, int, float]]) -> dict[str, int]:
        if not file_infos:
            return {}
        want_by_norm: dict[str, list[tuple[str, int, float]]] = {}
        for p, s, m in file_infos:
            want_by_norm.setdefault(os.path.normcase(p), []).append((p, s, m))
        out: dict[str, int] = {}
        mask64 = (1 << 64) - 1
        BATCH = 400
        for i in range(0, len(file_infos), BATCH):
            query_keys = list(dict.fromkeys(
                k for p, _, _ in file_infos[i:i + BATCH] for k in (os.path.normcase(p), p)
            ))
            ph = ",".join("?" * len(query_keys))
            with self.lock:
                rows = self.conn.execute(
                    f"SELECT path, size, mtime, hash FROM hashes WHERE path IN ({ph})", query_keys
                ).fetchall()
            for r in rows:
                candidates = want_by_norm.get(os.path.normcase(r[0]))
                if not candidates:
                    continue
                for orig_p, s, m in candidates:
                    if r[1] == s and r[2] == m:
                        out[orig_p] = r[3] & mask64
        return out

    def put(self, path: str, size: int, mtime: float, verdict: str, score: float, reasons: list, ocr_text: str, version=None):
        reasons_str = "[]" if not reasons else json.dumps(reasons, ensure_ascii=False)
        norm_p = os.path.normcase(path)
        need_prune = False
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO results VALUES(?,?,?,?,?,?,?,?,?)",
                (norm_p, size, mtime, verdict, score, reasons_str, ocr_text, time.time(),
                 self.result_version if version is None else version),
            )
            self._writes_since_prune += 1
            if self._writes_since_prune >= 2000:
                self._writes_since_prune = 0
                need_prune = True
        if need_prune:
            self.prune()

    def commit(self):
        with self.lock:
            self.conn.commit()


# ---------------------------------------------------------------- OCR
# GPU 加速开关。实测（RTX 5070 / DirectML）：透明水印类大图全图 OCR 快 2.6 倍，
# 但日常扫描以小条带推理为主，整体反而比 CPU 慢约 11%，故默认关。
# 要开：pip install onnxruntime-directml 后把这里改成 True；GPU 不可用时自动回退 CPU。
USE_GPU = False
USING_GPU = False  # get_ocr 实际启用 GPU 后置 True（供 scan_worker 决定线程数）

_OCR_SINGLETON = None
_OCR_INIT_LOCK = threading.Lock()  # 多线程同时首次加载模型会互相抢磁盘/CPU，串行化
_OCR_LOCAL = threading.local()


def get_ocr():
    """共享单实例：onnxruntime 的 session.run 可被多线程并发调用（实测 200 张 8 线程 0 错误、
    吞吐比每线程一实例快 37%），并且只需加载一次模型（8 线程 = 原来 8 次加载 ≈ 12 秒启动）。"""
    global _OCR_SINGLETON
    ocr = _OCR_SINGLETON
    if ocr is None:
        with _OCR_INIT_LOCK:
            if _OCR_SINGLETON is None:
                _OCR_SINGLETON = _create_ocr()
            ocr = _OCR_SINGLETON
    # RapidOCR 的 detector 在每次调用时会改写 preprocess_op。只隔离这个
    # 可变外壳，底层 ONNX 会话仍共享；不同尺寸图片并行时不会串用预处理参数。
    if getattr(_OCR_LOCAL, 'source', None) is not ocr:
        local = copy.copy(ocr)
        local.text_det = copy.copy(ocr.text_det)
        _OCR_LOCAL.source, _OCR_LOCAL.engine = ocr, local
    return _OCR_LOCAL.engine


def _create_ocr():
    from rapidocr_onnxruntime import RapidOCR
    try:
        from rapidocr_onnxruntime.utils.infer_engine import OrtInferSession
    except Exception:
        OrtInferSession = None

    # 默认 limit_type='min' 会把宽条带放大到 736px 高，慢数倍；
    # 用 max 限制长边，广告水印字足够大，精度够用。
    # 每会话限 2 线程：N 会话 × 全核 intra 线程会过订阅（吞吐降 + CPU 抖动卡顿），
    # 多会话并发 + 少线程/会话才是大批量图的正确姿势（不影响识别精度）
    common = dict(det_limit_type="max", det_limit_side_len=1200,
                  intra_op_num_threads=2, inter_op_num_threads=1)
    # 装了 onnxruntime-directml 时 GPU 跑 OCR（RTX 5070 实测约 2.6 倍提速）；
    # 关闭或异常时回退 CPU。GPU 不生效/报错时把 USE_GPU 改为 False 即可。
    ocr = None
    if USE_GPU:
        try:
            import numpy as np
            ocr = RapidOCR(**common, det_use_dml=True, cls_use_dml=True, rec_use_dml=True)
            ocr(np.zeros((32, 32, 3), dtype="uint8"))  # 试跑一次，构造成功但推理崩溃也算失败
            global USING_GPU
            USING_GPU = True
        except Exception:
            ocr = None
    if ocr is None:
        # RapidOCR 默认关闭了 ONNX Runtime 的 CPU 内存池（enable_cpu_mem_arena=False），
        # 每次推理都会对中间张量频繁向系统申请/释放大块内存，拖慢 2~3 倍；
        # 启用内存池与内存模式复用完全不改变模型权重与算子数值（实测逐字 100% 一致）。
        orig_opts = getattr(OrtInferSession, "_init_sess_opts", None) if OrtInferSession is not None else None
        if orig_opts is not None:
            def _arena_opts(config):
                so = orig_opts(config)
                so.enable_cpu_mem_arena = True
                so.enable_mem_pattern = True
                return so
            OrtInferSession._init_sess_opts = staticmethod(_arena_opts)
        try:
            try:
                ocr = RapidOCR(**common)
            except Exception:
                ocr = RapidOCR()
        finally:
            if orig_opts is not None:
                OrtInferSession._init_sess_opts = orig_opts
    for sub in (getattr(ocr, "text_det", None), getattr(ocr, "text_cls", None), getattr(ocr, "text_rec", None)):
        sess = getattr(sub, "infer", None) or getattr(sub, "session", None)
        if sess is not None and hasattr(sess, "get_input_names") and hasattr(sess, "get_output_names"):
            try:
                in_names = list(sess.get_input_names())
                out_names = list(sess.get_output_names())
                sess.get_input_names = lambda _n=in_names: _n
                sess.get_output_names = lambda _n=out_names: _n
            except Exception:
                pass
    return ocr


def _ocr_ndarray(img) -> list[str]:
    """img: PIL.Image → RapidOCR 文本列表（识别失败返回空）"""
    import cv2
    import numpy as np

    mode = getattr(img, "mode", None)
    if mode == "RGB":
        arr = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    elif mode == "L":
        arr = cv2.cvtColor(np.asarray(img), cv2.COLOR_GRAY2BGR)
    else:
        arr = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    try:
        result, _elapse = get_ocr()(arr)
    except Exception as e:
        raise RuntimeError(f'OCR 推理失败：{e}') from e
    if not result:
        return []
    return [str(item[1]) for item in result]


def _bright_from_mask(mask) -> Image.Image:
    import cv2
    import numpy as np

    out = np.where(mask, np.uint8(0), np.uint8(255))  # 笔画黑字白底
    # 单通道形态学膨胀与原 3 通道 Pillow MaxFilter(3) 像素级 100% 等价，耗时从 ~38ms 降至 <1ms
    dilated = cv2.dilate(out, np.ones((3, 3), dtype=np.uint8))
    return Image.fromarray(dilated)


def is_blank_image(img: Image.Image) -> bool:
    """只有每个 RGB 通道均完全一致时才跳过；稀疏文字和等亮度色差不能跳过。"""
    rgb = img if img.mode == 'RGB' else img.convert('RGB')
    return all(low == high for low, high in rgb.getextrema())


def _pil_to_rgb(im: Image.Image) -> Image.Image:
    """无损安全转 8-bit RGB：
    1) 普通不透明 RGB/L/CMYK/YCbCr 保持原生快速路径，零额外开销；
    2) 16-bit / 浮点灰度图（I;16 / I / F）按动态范围线性映射到 0..255，避免 Pillow 直接截断成全白；
    3) 含透明通道的贴纸/水印/二维码（RGBA / LA / PA / P+transparency）按前景明度合成到对比衬底，
       避免黑字透明底或白字透明底在丢弃 Alpha 后变成纯黑/纯白导致漏检与黑块预览。"""
    mode = im.mode
    if mode == "RGB":
        return im.copy()
    if mode.startswith("I;16") or mode in ("I", "F"):
        import numpy as np
        arr = np.asarray(im, dtype=np.float32)
        if arr.size:
            finite = np.nan_to_num(arr, nan=0.0, posinf=65535.0, neginf=0.0)
            hi = float(finite.max())
            if mode.startswith("I;16") or hi > 255.0:
                scale = (255.0 / 65535.0) if hi <= 65535.0 else (255.0 / hi)
                finite = finite * scale
            elif mode == "F" and 0.0 < hi <= 1.0:
                finite = finite * 255.0
            u8 = np.clip(finite, 0.0, 255.0).astype(np.uint8)
        else:
            u8 = arr.astype(np.uint8)
        return Image.fromarray(u8).convert("RGB")
    if mode in ("RGBA", "LA", "PA") or (mode == "P" and "transparency" in im.info):
        rgba = im if mode == "RGBA" else im.convert("RGBA")
        lo, _ = rgba.getchannel("A").getextrema()
        if lo < 255:
            import numpy as np
            arr = np.asarray(rgba)
            sample = arr[::4, ::4] if min(arr.shape[:2]) >= 64 else arr
            fg = sample[:, :, :3][sample[:, :, 3] > 16]
            # 若前景几乎全为高亮浅色（如透明底白色水印贴纸），衬深色底以保留轮廓与提亮对比；否则衬白底
            bg_color = (48, 48, 52, 255) if (fg.size and float(fg.mean()) > 220.0) else (255, 255, 255, 255)
            bg = Image.new("RGBA", rgba.size, bg_color)
            return Image.alpha_composite(bg, rgba).convert("RGB")
        return rgba.convert("RGB")
    return im.convert("RGB")


def _get_exif_orientation(im: Image.Image) -> int:
    try:
        return int(im.getexif().get(274, 1) or 1)
    except Exception:
        return 1


def _decode_bounded_rgb(im: Image.Image, max_dim: int | None, transpose_exif: bool = False,
                        orient: int | None = None) -> Image.Image:
    """在安全内存边界内将图像转为 8-bit RGB（按需先做 JPEG draft 与快速整数降采样再合成/转置）。"""
    if max_dim is not None:
        try:
            im.draft("RGB", (max_dim, max_dim))
        except Exception:
            pass
        w, h = im.size
        long_side = max(w, h)
        if long_side >= max_dim * 2 and im.mode in ("RGB", "RGBA", "L", "LA"):
            factor = max(1, long_side // (max_dim * 2))
            if factor >= 2 and min(w, h) // factor >= 32:
                try:
                    im = im.reduce(factor)
                except Exception:
                    pass
    if transpose_exif:
        if orient is None:
            orient = _get_exif_orientation(im)
        if orient in (2, 3, 4, 5, 6, 7, 8):
            try:
                im = ImageOps.exif_transpose(im)
            except Exception:
                pass
    img = _pil_to_rgb(im)
    if max_dim is not None and max(img.size) > max_dim:
        img = ImageOps.contain(img, (max_dim, max_dim))
    return img


def load_pixel_image(path: Path, max_dim: int) -> Image.Image:
    """为 QR/OCR 加载像素；JPEG 走 draft 快速降采样，非 JPEG 大图整数缩减防内存暴涨。
    必须 with：Image.open 持有文件句柄，扫描几千张不关闭会耗尽句柄/锁住文件。"""
    with Image.open(path) as im:
        return _decode_bounded_rgb(im, max_dim, transpose_exif=False)


def load_display_image(path: Path, max_dim=None):
    """显示专用方向处理；不更改识别通道的像素、尺寸或算法。"""
    with Image.open(path) as im:
        original = im.size
        orient = _get_exif_orientation(im)
        if orient in (5, 6, 7, 8):
            original = original[::-1]
        image = _decode_bounded_rgb(im, max_dim, transpose_exif=True, orient=orient)
    return image, original


def _qr_format_word(value: int) -> int:
    """QR 格式信息的 5 位内容、10 位 BCH 校验及固定掩码。"""
    remainder = value << 10
    for shift in range(4, -1, -1):
        if remainder & (1 << (shift + 10)):
            remainder ^= 0x537 << shift
    return ((value << 10) | remainder) ^ 0x5412


_QR_FORMAT_WORDS = tuple(_qr_format_word(value) for value in range(32))


def _has_qr_structure(gray, corners) -> bool:
    """无法解码时，核对定位图案、双轴时序及两份格式信息。"""
    import cv2
    import numpy as np

    quad = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    if not np.isfinite(quad).all() or not cv2.isContourConvex(quad):
        return False
    min_edge = np.linalg.norm(quad - np.roll(quad, 1, axis=0), axis=1).min()
    if min_edge < 21:
        return False
    side = 354  # QR 最多 177×177 个模块，每个模块至少两点用于采样。
    target = np.float32([(0, 0), (side-1, 0), (side-1, side-1), (0, side-1)])
    warp = cv2.warpPerspective(gray, cv2.getPerspectiveTransform(quad, target), (side, side))
    _, binary = cv2.threshold(warp, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    # 8×8 模板包含 7×7 定位图案以及内侧的浅色分隔边。
    finder = np.zeros((8, 8), dtype=bool)
    finder[:7, :7] = True
    finder[1:6, 1:6] = False
    finder[2:5, 2:5] = True
    for size in range(21, min(177, int(min_edge)) + 1, 4):
        centers = ((np.arange(size) + .5) * side / size).astype(int)
        c_head, c_tail = centers[:8], centers[-8:]
        # 先只采样三个 8×8 角点，不通过则无需分配完整 size×size 模块网格
        if (np.count_nonzero((binary[np.ix_(c_head, c_head)] == 0) != finder) > 6
                or np.count_nonzero((binary[np.ix_(c_head, c_tail)] == 0)[:, ::-1] != finder) > 6
                or np.count_nonzero((binary[np.ix_(c_tail, c_head)] == 0)[::-1] != finder) > 6):
            continue
        grid = binary[np.ix_(centers, centers)] == 0
        timing = np.arange(8, size - 8) % 2 == 0
        if ((grid[6, 8:-8] == timing).mean() < .8 or (grid[8:-8, 6] == timing).mean() < .8):
            continue
        for modules in (grid, grid.T):  # 镜像图的格式信息沿对角线翻转。
            first = [*modules[:6, 8], modules[7, 8], modules[8, 8], modules[8, 7], *modules[8, :6][::-1]]
            second = [*modules[8, -8:][::-1], *modules[-7:, 8]]
            a, b = (sum(int(bit) << i for i, bit in enumerate(bits)) for bits in (first, second))
            if any((a ^ word).bit_count() <= 2 and (b ^ word).bit_count() <= 2 for word in _QR_FORMAT_WORDS):
                return True
    return False


_QR_LOCAL = threading.local()


def detect_qr(img: Image.Image, *, retry_image: Image.Image | None = None) -> tuple[list[str], bool]:
    """返回已解码的内容及可信二维码证据，候选轮廓本身不计入。"""
    import cv2
    import numpy as np

    verified = False
    detector = getattr(_QR_LOCAL, "detector", None)
    if detector is None:
        detector = cv2.QRCodeDetector()
        _QR_LOCAL.detector = detector
    for pass_idx, image in enumerate((img, retry_image)):
        if image is None:
            break
        if image.mode == "L":
            gray = np.asarray(image)
        else:
            rgb = image if image.mode == "RGB" else image.convert("RGB")
            gray = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2GRAY)
        try:
            _, decoded, points, _ = detector.detectAndDecodeMulti(gray)
        except (cv2.error, UnicodeDecodeError):
            break
        texts = [text for text in (decoded or ()) if text and text.strip()]
        if texts:
            return texts, True
        if points is None or not len(points):
            break
        # 缩图识别出的候选若具备定位结构或尺寸过小（<100px），才在更大图上重试解码；
        # 大尺寸非二维码矩形框（漫画分格/窗框）无需重复全图检测。
        has_small = False
        for corners in points:
            quad = np.asarray(corners, dtype=np.float32).reshape(4, 2)
            if np.isfinite(quad).all():
                if np.linalg.norm(quad - np.roll(quad, 1, axis=0), axis=1).min() < 100:
                    has_small = True
            if _has_qr_structure(gray, corners):
                verified = True
        if pass_idx == 0 and not verified and not has_small:
            break
    return [], verified


def decode_qr_texts(img: Image.Image) -> list[str]:
    """pyzbar 主解码通道：实测对 OpenCV 解不出的真实二维码（带 logo 的低清贴片）
    也能解出 t.me 链接，且仅 10~20ms/张，不比 OpenCV 慢。
    pyzbar 未安装或解码失败时返回 []（调用方回退 OpenCV 行为）。"""
    try:
        from pyzbar.pyzbar import ZBarSymbol, decode as _zb_decode
    except Exception:
        return []
    try:
        res = _zb_decode(img, symbols=[ZBarSymbol.QRCODE])
    except Exception:
        return []
    out = []
    for r in res:
        try:
            t = r.data.decode("utf-8", "replace").strip()
        except Exception:
            continue
        if t:
            out.append(t)
    return out


def _qr_link(texts):
    """返回实际命中的链接/裸域名，保留链接和 Telegram/微信等引流信号。"""
    for text in texts:
        raw = str(text or "").strip()
        if not raw:
            continue
        if _QR_LINK_RE.search(raw) or _QR_COMPACT_LINK_RE.search(normalize_text(raw)):
            return raw
    return None


# ---------------------------------------------------------------- 逐图分析
def match_rules(text: str, rules: list[dict]) -> list[dict]:
    if not rules:
        return []
    t = normalize_text(text)
    hits = []
    for rule in rules:
        try:
            m = _compile_rule_re(rule["pattern"]).search(t)
        except re.error:
            continue
        if m:
            hits.append({"label": rule.get("label", "规则"), "detail": m.group(0), "type": "rule"})
    return hits


def score_ocr_texts(texts: list[str], kw_rules: list[dict]) -> tuple[list[dict], float, str]:
    lines: list[str] = []
    seen: set[str] = set()
    for raw in texts:
        norm = normalize_text(str(raw))
        for part in norm.split("\n"):
            if part and part not in seen:
                seen.add(part)
                lines.append(part)
    joined = "\n".join(lines)
    hits: list[dict] = []
    total = 0.0
    for rule in kw_rules:
        try:
            m = _compile_rule_re(rule["pattern"]).search(joined)
        except re.error:
            continue
        if m:
            hits.append({"label": f"OCR[{rule.get('label','')}] {m.group(0)}", "detail": m.group(0), "type": "ocr"})
            total += float(rule.get("weight", 1))
    return hits, total, joined[:500]


def _bright_stroke_mask(rgb_arr, thr: int, vmax=None, vmin=None):
    """与 Pillow convert('HSV') 后 `(V >= thr) & (S <= 70)` 在全部 256^3 RGB 颜色上位级 100% 等价，
    跳过无用的色相计算；当高亮像素占比不足 0.05% 时直接短路返回 (None, vmax, vmin)。"""
    import cv2
    import numpy as np

    min_pixels = rgb_arr.shape[0] * rgb_arr.shape[1] * 0.0005
    if vmax is None:
        c0, c1, c2 = rgb_arr[:, :, 0], rgb_arr[:, :, 1], rgb_arr[:, :, 2]
        vmax = cv2.max(cv2.max(c0, c1), c2)
    bright = vmax >= thr
    if float(np.count_nonzero(bright)) < min_pixels:
        return None, vmax, vmin
    if vmin is None:
        c0, c1, c2 = rgb_arr[:, :, 0], rgb_arr[:, :, 1], rgb_arr[:, :, 2]
        vmin = cv2.min(cv2.min(c0, c1), c2)
    v16 = vmax.astype(np.uint16)
    d16 = (vmax - vmin).astype(np.uint16)
    low_sat = (vmax == 0) | (d16 * 255 < 71 * v16)
    mask = bright & low_sat
    if float(np.count_nonzero(mask)) < min_pixels:
        return None, vmax, vmin
    return mask, vmax, vmin


def _extract_frame_inputs(im: Image.Image, orient: int, max_dim: int, strip_ratio: float):
    """从当前帧提取缩放整图；若是竖长图（条漫/长截图），额外保留未压扁的首尾拼图。"""
    src = im
    transposed = False
    if orient in (2, 3, 4, 5, 6, 7, 8):
        try:
            src = ImageOps.exif_transpose(im)
            transposed = True
        except Exception:
            src = im
    fw, fh = src.size
    edge_combo = None
    if fh > fw * 2.5 and fh > max_dim:
        edge_h = min(fh // 2, max(fw, int(fh * strip_ratio)), fw * 2)
        if edge_h >= 24:
            part_cap = max(max_dim // 2, 780)
            top_part = _decode_bounded_rgb(src.crop((0, 0, fw, edge_h)), part_cap, transpose_exif=False)
            bot_part = _decode_bounded_rgb(src.crop((0, fh - edge_h, fw, fh)), part_cap, transpose_exif=False)
            gap = 16
            cw = max(top_part.width, bot_part.width)
            edge_combo = Image.new("RGB", (cw, top_part.height + gap + bot_part.height), (255, 255, 255))
            edge_combo.paste(top_part, (0, 0))
            edge_combo.paste(bot_part, (0, top_part.height + gap))
    img = _decode_bounded_rgb(src, max_dim, transpose_exif=(not transposed and orient != 1), orient=orient)
    return img, edge_combo


def analyze_file(path: Path, cfg: dict, cache: Cache, enable: dict,
                 _stat: tuple[int, float] | None = None,
                 _version: str | None = None,
                 _skip_cache_get: bool = False) -> dict:
    """单图全流程分析。verdict: ad / suspect / clean / error"""
    path_str = str(path)
    if _stat is not None:
        size, mtime = _stat
    else:
        try:
            stat = path.stat()
            size, mtime = stat.st_size, stat.st_mtime
        except OSError as e:
            return {"path": path_str, "verdict": "error", "score": 0,
                    "reasons": [{"type": "error", "label": f"读取失败 {e}"}], "ocr_text": ""}

    version = _version if _version is not None else result_signature(cfg, enable)
    if not _skip_cache_get:
        cached = cache.get(path_str, size, mtime, version)
        if cached is not None:
            cached["path"] = path_str
            return cached

    reasons: list[dict] = []
    verdict = "clean"
    score = 0.0
    ocr_text = ""

    # 白名单：直接放行
    wl = match_rules(path.name, cfg.get("whitelist_rules", []))
    if wl:
        result = {"path": path_str, "verdict": "clean", "score": 0,
                  "reasons": [{"type": "whitelist", "label": "白名单"}], "ocr_text": "",
                  "_stat": (size, mtime)}
        cache.put(path_str, size, mtime, result["verdict"], 0, result["reasons"], "", version)
        return result

    # 1) 文件名规则（受 enable 开关控制）
    fr = match_rules(path.name, cfg.get("filename_rules", [])) if enable.get("filename", True) else []
    if fr:
        reasons += [{"type": "filename", **{k: v for k, v in h.items() if k != "type"}} for h in fr]
        verdict = "ad"
        score = 99

    # 2) 形状规则 + 像素解码合并为单次文件打开（命中过小尺寸直接提前结束，不解码像素）
    shape_long = False
    need_shape = verdict == "clean" and enable.get("shape", True)
    need_pixels = bool(enable.get("qr") or enable.get("ocr"))
    img = None
    edge_combo = None
    is_anim = False
    n_frames = 1
    orient = 1
    if verdict == "clean" and (need_shape or need_pixels):
        opened = False
        max_dim = cfg["ocr"]["max_dim"]
        strip_ratio = cfg["ocr"]["strip_ratio"]
        try:
            with Image.open(path) as im:
                opened = True
                orient = _get_exif_orientation(im)
                n_frames = int(getattr(im, "n_frames", 1) or 1)
                is_anim = bool(getattr(im, "is_animated", False) and n_frames > 1)
                if need_shape:
                    w, h = im.size
                    if orient in (5, 6, 7, 8):
                        w, h = h, w
                    ratio = max(w, h) / max(1, min(w, h))
                    ratio_hard = float(cfg["shape"].get("max_ratio_hard", 8.0))
                    if w >= h and ratio >= ratio_hard:
                        reasons.append({"type": "shape", "label": f"极端横条 {w}x{h}（{ratio:.1f}:1）"})
                        shape_long = True      # 只记疑，继续走 QR/OCR 找内容证据
                    elif min(w, h) < cfg["shape"]["min_side"]:
                        reasons.append({"type": "shape", "label": f"分辨率过小 {w}x{h}"})
                        verdict, score = "suspect", 20
                if verdict == "clean" and need_pixels:
                    img, edge_combo = _extract_frame_inputs(im, orient, max_dim, strip_ratio)
        except Exception as e:
            prefix = "读取失败" if (need_shape and not opened) else ("解码失败" if need_pixels else "读取失败")
            reasons.append({"type": "error", "label": f"{prefix} {e}"})
            verdict, score = "error", 0

    def _analyze_frame(frame_img: Image.Image, frame_edge: Image.Image | None):
        nonlocal verdict, score, ocr_text
        frame_reasons: list[dict] = []
        qr_suspect = False
        if is_blank_image(frame_img) and (frame_edge is None or is_blank_image(frame_edge)):
            return False, []

        small_1100 = None

        def _get_1100():
            nonlocal small_1100
            if small_1100 is None:
                small_1100 = ImageOps.contain(frame_img, (1100, 1100)) if max(frame_img.size) > 1100 else frame_img
            return small_1100

        if enable.get("qr"):
            try:
                qr_targets = [frame_img] + ([frame_edge] if frame_edge is not None else [])
                all_texts: list[str] = []
                link = None
                structure = False
                for qtarget in qr_targets:
                    gray_img = qtarget if qtarget.mode == "L" else qtarget.convert("L")
                    texts = decode_qr_texts(gray_img)
                    all_texts.extend(texts)
                    link = _qr_link(texts)
                    if link:
                        break
                    qr_img = (ImageOps.contain(gray_img, (1100, 1100))
                              if max(gray_img.size) > 1100 else gray_img)
                    decoded, st_hit = detect_qr(qr_img, retry_image=gray_img if qr_img is not gray_img else None)
                    all_texts.extend(decoded)
                    structure = structure or st_hit
                    link = _qr_link(decoded)
                    if link:
                        break
                if link:
                    frame_reasons.append({"type": "qr", "label": f"二维码含链接 {link[:60]}"})
                    verdict, score = "ad", 80
                elif all_texts or structure:
                    label = '二维码无链接内容' if all_texts else '疑似二维码图形（未能解码）'
                    reason_item = {"type": "qr", "label": label}
                    if all_texts:
                        reason_item["detail"] = all_texts[0][:120]
                    frame_reasons.append(reason_item)
                    qr_suspect = True
            except Exception:
                pass

        if verdict == "clean" and enable.get("ocr"):
            try:
                if frame_edge is not None:
                    regions = [frame_edge]
                else:
                    W, H = frame_img.size
                    strip = int(H * cfg["ocr"]["strip_ratio"])
                    if max(W, H) <= 1000:
                        regions = [frame_img]
                    else:
                        band = max(24, strip)
                        top = frame_img.crop((0, 0, W, min(H, band)))
                        bot = frame_img.crop((0, max(0, H - band), W, H))
                        gap = 16
                        combo = Image.new("RGB", (W, top.height + gap + bot.height), (255, 255, 255))
                        combo.paste(top, (0, 0))
                        combo.paste(bot, (0, top.height + gap))
                        regions = [combo]
                texts: list[str] = []
                hits_left: list[dict] = []
                ocr_score, joined = 0.0, ""
                region_results = {}

                def _scan_region(reg, acc):
                    nonlocal hits_left, ocr_score, joined
                    key = (id(reg), None)
                    if key not in region_results:
                        region_results[key] = _ocr_ndarray(reg)
                    acc += region_results[key]
                    hits_left, ocr_score, joined = score_ocr_texts(acc, cfg["ocr_keywords"])
                    if ocr_score >= cfg["ocr"]["score_high"]:
                        return acc, True
                    rgb_arr = vmax = vmin = None
                    for thr in cfg["ocr"]["bright_thresholds"]:
                        key = (id(reg), thr)
                        if key not in region_results:
                            import numpy as np
                            if rgb_arr is None:
                                rgb_arr = np.asarray(reg if reg.mode == "RGB" else reg.convert("RGB"))
                            mask, vmax, vmin = _bright_stroke_mask(rgb_arr, thr, vmax, vmin)
                            region_results[key] = (_ocr_ndarray(_bright_from_mask(mask))
                                                   if mask is not None else [])
                        acc += region_results[key]
                        hits_left, ocr_score, joined = score_ocr_texts(acc, cfg["ocr_keywords"])
                        if ocr_score >= cfg["ocr"]["score_high"]:
                            return acc, True
                    return acc, False

                scanned_ids = {id(reg) for reg in regions}
                for reg in regions:
                    texts, hit = _scan_region(reg, texts)
                    if hit:
                        break
                if ocr_score < cfg["ocr"]["score_high"] and cfg["ocr"]["full_image_if_nothing"]:
                    small = _get_1100()
                    if id(small) not in scanned_ids:
                        texts, _ = _scan_region(small, texts)
                ocr_text = joined
                if hits_left:
                    frame_reasons += hits_left
                    score = ocr_score
                    verdict = "ad" if score >= cfg["ocr"]["score_high"] else "suspect"
            except Exception as e:
                frame_reasons.append({"type": "error", "label": f"OCR失败 {e}"})
                verdict, score = "error", 0

        return qr_suspect, frame_reasons

    # 3) 二维码 + 4) OCR（需要像素，前面的强命中就不必再跑）
    qr_suspect = False
    if verdict == "clean" and need_pixels and img is not None:
        qr_suspect, frame_reasons = _analyze_frame(img, edge_combo)
        reasons.extend(frame_reasons)
        # GIF / 动态 WebP：首帧干净或空白时抽检中帧与末帧（最多 2 帧），防止首帧留白藏尾帧广告
        if verdict == "clean" and not qr_suspect and is_anim and n_frames > 1:
            sample_indices = sorted({n_frames // 2, n_frames - 1} - {0})
            max_dim = cfg["ocr"]["max_dim"]
            strip_ratio = cfg["ocr"]["strip_ratio"]
            try:
                with Image.open(path) as anim_im:
                    for fidx in sample_indices:
                        try:
                            anim_im.seek(fidx)
                            f_img, f_edge = _extract_frame_inputs(anim_im.copy(), orient, max_dim, strip_ratio)
                        except Exception:
                            continue
                        qr_suspect, extra_reasons = _analyze_frame(f_img, f_edge)
                        if verdict != "clean" or qr_suspect:
                            reasons.extend(extra_reasons)
                            break
            except Exception:
                pass
        if verdict == "clean" and (qr_suspect or shape_long):
            verdict, score = "suspect", 30

    if verdict == "clean" and shape_long:
        verdict, score = "suspect", 30

    result = {"path": path_str, "verdict": verdict, "score": score, "reasons": reasons,
              "ocr_text": ocr_text, "_stat": (size, mtime)}
    if verdict != "error":
        cache.put(path_str, size, mtime, verdict, score, reasons, ocr_text, version)
    return result


# ---------------------------------------------------------------- 扫描编排
def _norm_root(root) -> str:
    """规范化扫描根目录字符串：保留盘符根目录（如 D:\\），其余去除尾部分隔符。"""
    p = Path(root)
    text = str(p)
    anchor = p.anchor
    if anchor and os.path.normcase(text) == os.path.normcase(anchor):
        return text
    stripped = text.rstrip("\\/")
    return stripped or text


def _path_is_within(path_str: str, root_str: str) -> bool:
    p_norm = os.path.normcase(path_str)
    r_norm = os.path.normcase(root_str)
    if p_norm == r_norm:
        return True
    prefix = r_norm if r_norm.endswith(os.sep) else r_norm + os.sep
    return p_norm.startswith(prefix)


_GATHER_STAT_TLS = threading.local()
_ORIG_OS_WALK = os.walk


def gather_images(roots: list[str], cfg: dict) -> list[Path]:
    files: list[Path] = []
    stats_cache: dict[str, tuple[int, float]] = {}
    excluded = []
    for p in [cfg.get('quarantine_dir', '')] + cfg.get('quarantine_locations', []):
        if p:
            try:
                excluded.append(Path(p).expanduser().resolve())
            except OSError:
                continue
    resolved_dirs: dict[str, Path | None] = {}

    def _resolve_dir(path: Path) -> Path | None:
        key = os.path.normcase(str(path))
        if key not in resolved_dirs:
            try:
                resolved_dirs[key] = path.resolve()
            except OSError:
                resolved_dirs[key] = None
        return resolved_dirs[key]

    def is_quarantined(path: Path) -> bool:
        if any(part.startswith(QUARANTINE_NAME) for part in path.parts):
            return True
        if not excluded:
            return False
        resolved = _resolve_dir(path)
        if resolved is None:
            return False
        return any(resolved == q or q in resolved.parents for q in excluded)

    for root in roots:
        rootp = Path(root)
        if is_quarantined(rootp):
            continue
        if not rootp.is_dir():
            raise OSError(f'扫描目录不存在或无法访问：{rootp}')
        if os.walk is not _ORIG_OS_WALK:
            def walk_error(error):
                raise OSError(f'无法读取扫描目录：{error.filename}：{error}') from error
            for dirpath, dirnames, filenames in os.walk(rootp, onerror=walk_error):
                dp = Path(dirpath)
                dirnames[:] = [d for d in dirnames if not d.startswith(QUARANTINE_NAME)
                               and not is_quarantined(dp / d)]
                for fn in filenames:
                    dot = fn.rfind(".")
                    if dot >= 0 and fn[dot:].lower() in IMG_EXTS:
                        files.append(dp / fn)
            continue
        stack = [rootp]
        while stack:
            dp = stack.pop()
            try:
                with os.scandir(dp) as entries:
                    subdirs: list[Path] = []
                    for entry in entries:
                        name = entry.name
                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                        except OSError:
                            is_dir = False
                        if is_dir:
                            if not name.startswith(QUARANTINE_NAME):
                                sub = dp / name
                                if not is_quarantined(sub):
                                    subdirs.append(sub)
                            continue
                        dot = name.rfind(".")
                        if dot >= 0 and name[dot:].lower() in IMG_EXTS:
                            fp = dp / name
                            files.append(fp)
                            try:
                                if not entry.is_symlink():
                                    st = entry.stat(follow_symlinks=False)
                                    stats_cache[str(fp)] = (st.st_size, st.st_mtime)
                            except OSError:
                                pass
                    if subdirs:
                        stack.extend(reversed(subdirs))
            except OSError as error:
                raise OSError(f'无法读取扫描目录：{dp}：{error}') from error
    _GATHER_STAT_TLS.stats = stats_cache
    return files


def _root_of(path_str: str, roots: list[str]) -> str:
    """文件归属到最近的导入根目录（用路径前缀 + 分隔符边界，兼容盘符根与 Windows 大小写）。"""
    best = ""
    for r in roots:
        rr = _norm_root(r)
        if _path_is_within(path_str, rr):
            if len(rr) > len(best):
                best = rr
    return best or str(Path(path_str).parent)


def scan_worker(roots: list[str], cfg: dict, enable: dict, cache: Cache,
                out_q: queue.Queue, cancel: threading.Event, pause: threading.Event | None = None):
    """异常也发送结束状态；取消后等在途任务结束再允许开始下一次扫描。"""
    try:
        _scan_worker(roots, cfg, enable, cache, out_q, cancel, pause)
    except Exception as e:
        out_q.put(('scan_error', str(e)))


def _scan_worker(roots, cfg, enable, cache, out_q, cancel, pause):
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

    def stopped():
        while pause is not None and pause.is_set() and not cancel.is_set():
            cancel.wait(.05)
        return cancel.is_set()

    if stopped():
        out_q.put(('cancelled', None))
        return

    # 文件收集放后台线程：大目录收集会卡住 UI
    _GATHER_STAT_TLS.stats = None
    files = gather_images(roots, cfg)
    pre_stats = getattr(_GATHER_STAT_TLS, 'stats', None) or {}
    _GATHER_STAT_TLS.stats = None
    seen_files: set[str] = set()
    unique_files: list[Path] = []
    resolved_parents: dict[str, str] = {}
    for p in files:
        try:
            if p.is_symlink():
                key = os.path.normcase(str(p.resolve()))
            else:
                parent_str = str(p.parent)
                parent_key = os.path.normcase(parent_str)
                if parent_key not in resolved_parents:
                    try:
                        resolved_parents[parent_key] = str(p.parent.resolve())
                    except OSError:
                        resolved_parents[parent_key] = parent_str
                key = os.path.normcase(os.path.join(resolved_parents[parent_key], p.name))
        except OSError:
            key = os.path.normcase(str(p))
        if key not in seen_files:
            seen_files.add(key)
            unique_files.append(p)
    files = unique_files
    if not files:
        out_q.put(("total", 0))
        out_q.put(("done", None))
        return

    # 先确认文件仍可访问，避免目录进度把已消失的文件算进总数。
    infos, valid_files = [], []
    stat_by_path: dict[str, tuple[int, float]] = {}
    for p in files:
        if stopped():
            out_q.put(('cancelled', None))
            return
        p_str = str(p)
        cached_st = pre_stats.get(p_str)
        if cached_st is not None:
            infos.append((p_str, cached_st[0], cached_st[1]))
            stat_by_path[p_str] = cached_st
            valid_files.append(p)
            continue
        try:
            st = p.stat()
            infos.append((p_str, st.st_size, st.st_mtime))
            stat_by_path[p_str] = (st.st_size, st.st_mtime)
            valid_files.append(p)
        except OSError:
            continue
    files = valid_files

    # —— 文件夹级进度：按导入根聚合，UI 实时显示每个文件夹 已完成/总数 ——
    roots_list = [_norm_root(r) for r in roots]
    file_root = {str(p): _root_of(str(p), roots_list) for p in files}
    folder_total: dict[str, int] = {}
    for r in file_root.values():
        folder_total[r] = folder_total.get(r, 0) + 1
    folder_done: dict[str, int] = {r: 0 for r in folder_total}
    out_q.put(("folders", folder_total))

    def _bump(p_str):
        r = file_root.get(p_str)
        if r is None:
            return
        folder_done[r] += 1
        out_q.put(("folder_progress", (r, folder_done[r], folder_total[r])))

    # —— 缓存秒开：先批量查缓存，命中直接上表，未命中才进分析 ——
    out_q.put(("total", len(files)))
    if not files:
        out_q.put(("done", None))
        return
    sig = result_signature(cfg, enable)
    cached = cache.get_batch(infos, sig)
    files_to_scan = [p for p in files if str(p) not in cached]
    out_q.put(("cached", len(cached)))  # 通知 UI 缓存命中数
    done = 0
    for p in files:
        if stopped():
            out_q.put(('cancelled', None))
            return
        p_str = str(p)
        if p_str in cached:
            res = cached[p_str]
            res["path"] = p_str
            out_q.put(("item", res))
            done += 1
            out_q.put(("progress", done))
            _bump(p_str)
            if cancel.is_set():
                out_q.put(("cancelled", None))
                return
    if not files_to_scan:
        cache.commit()
        out_q.put(("done", None))
        return

    workers = max(1, int(cfg.get("workers", 4)))
    workers = min(workers, os.cpu_count() or 4, 16)
    if enable.get("ocr"):
        get_ocr()  # 提前初始化，确定实际是否用上 GPU
        if USING_GPU:
            workers = 1
    ex = ThreadPoolExecutor(max_workers=workers)
    cancelled_scan = False
    pending = iter(files_to_scan)
    futs = {}
    exhausted = False
    paused_committed = False

    def _analyze(p_):
        p_str = str(p_)
        try:
            out_q.put(("current", (file_root.get(p_str), Path(p_).name)))
        except Exception:
            pass
        return analyze_file(p_, cfg, cache, enable, stat_by_path.get(p_str), sig, True)

    try:
        while futs or not exhausted:
            if cancel.is_set():
                cancelled_scan = True
                break
            if pause is None or not pause.is_set():
                paused_committed = False
                while len(futs) < workers and not exhausted and not cancel.is_set():
                    if pause is not None and pause.is_set():
                        break
                    p_ = next(pending, None)
                    if p_ is None:
                        exhausted = True
                        break
                    futs[ex.submit(_analyze, p_)] = p_
            if not futs:
                if not paused_committed:
                    cache.commit()  # 暂停期间主动释放写事务锁，避免阻塞其他进程
                    paused_committed = True
                cancel.wait(.05)
                continue
            completed, _ = wait(futs, timeout=.05, return_when=FIRST_COMPLETED)
            for fut in completed:
                p_ = futs.pop(fut)
                if cancel.is_set():
                    cancelled_scan = True
                    break
                done += 1
                try:
                    res = fut.result()
                except Exception as e:
                    res = {"path": str(p_), "verdict": "error", "score": 0,
                           "reasons": [{"type": "error", "label": f"分析异常 {e}"}], "ocr_text": ""}
                out_q.put(("item", res))
                out_q.put(("progress", done))
                _bump(res["path"])
                if done % 50 == 0:
                    cache.commit()  # 中途退出/崩溃时少丢缓存
                if cancel.is_set():
                    cancelled_scan = True
                    break
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
    cache.commit()
    if cancelled_scan or cancel.is_set():
        out_q.put(('cancelled', None))
    else:
        out_q.put(("done", None))


# ---------------------------------------------------------------- 处置
def _unique_destination(path: Path, label: str) -> Path:
    """同一秒多次同名冲突也不覆盖已有文件。"""
    if not path.exists():
        return path
    number = 1
    while True:
        candidate = path.with_name(f'{path.stem}_{label}{number}{path.suffix}')
        if not candidate.exists():
            return candidate
        number += 1


def _load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or not isinstance(manifest.get('items'), list):
        raise ValueError('隔离清单格式无效，已停止操作以保留原文件')
    if not all(isinstance(item, dict) and all(isinstance(item.get(k), str) and item[k]
                                             for k in ('src', 'dst')) for item in manifest['items']):
        raise ValueError('隔离清单包含无效文件记录，已停止操作')
    return manifest


_QUARANTINE_LOCKS = {}
_QUARANTINE_LOCKS_GUARD = threading.Lock()
_QUARANTINE_LOCK_DEPTH = threading.local()


def _quarantine_control(root, name):
    path = Path(root) / name
    if path.is_symlink():
        raise ValueError(f'隔离区控制文件不能是符号链接：{name}')
    return path


@contextmanager
def quarantine_lock(root, timeout=30):
    """同目录的线程锁 + 系统文件锁；支持同线程重入，异常退出后系统自动释放。"""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    key = os.path.normcase(str(root))
    with _QUARANTINE_LOCKS_GUARD:
        lock = _QUARANTINE_LOCKS.setdefault(key, threading.RLock())
    depths = getattr(_QUARANTINE_LOCK_DEPTH, 'counts', None)
    if depths is None:
        depths = {}
        _QUARANTINE_LOCK_DEPTH.counts = depths
    with lock:
        if depths.get(key, 0) > 0:
            depths[key] += 1
            try:
                yield root
            finally:
                depths[key] -= 1
            return
        fd = os.open(str(_quarantine_control(root, '.ad-filter.lock')), os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'r+b') as stream:
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b'0')
                stream.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    stream.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise OSError('该隔离区正被其他窗口使用，请稍后重试')
                    time.sleep(.05)
            depths[key] = 1
            try:
                yield root
            finally:
                depths[key] -= 1
                if depths[key] <= 0:
                    depths.pop(key, None)
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)


def _quarantine_serialized(root_index, timeout=30):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            root = args[root_index] if len(args) > root_index else kwargs['quarantine_root']
            with quarantine_lock(root, timeout):
                return fn(*args, **kwargs)
        return wrapped
    return decorate


def _safe_quarantine_item(item, root):
    root = Path(root).resolve()
    if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k] for k in ('src', 'dst')):
        raise ValueError('隔离记录格式无效')
    entry = dict(item)
    if not Path(entry['src']).is_absolute():
        raise ValueError('隔离清单中的原始位置不是完整路径')
    if 'dst_rel' in entry:
        if not isinstance(entry['dst_rel'], str) or not entry['dst_rel']:
            raise ValueError('隔离记录中的相对位置无效')
        relative = Path(entry['dst_rel'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('隔离清单包含越界路径，已停止操作')
        dst = (root / relative).resolve()
    else:
        dst = Path(entry['dst']).resolve()
    if dst == root or not dst.is_relative_to(root):
        raise ValueError('清单引用了当前隔离区以外的文件。旧版清单请在原隔离目录打开。')
    entry['dst'] = str(dst)
    entry['dst_rel'] = dst.relative_to(root).as_posix()
    return entry


def _load_quarantine(root):
    """快照 + 移动前持久化的小日志；复制新清单时按相对位置安全重定位。"""
    root = Path(root).resolve()
    path = _quarantine_control(root, 'manifest.json')
    manifest = _load_manifest(path) if path.exists() else {'items': []}
    manifest['items'] = [_safe_quarantine_item(item, root) for item in manifest['items']]
    known = {item['dst'] for item in manifest['items']}
    journal = _quarantine_control(root, '.ad-filter-journal.jsonl')
    if journal.exists():
        lines = journal.read_text(encoding='utf-8').splitlines(keepends=True)
        for index, line in enumerate(lines):
            try:
                record = json.loads(line)
            except ValueError:
                if index == len(lines) - 1 and not line.endswith('\n'):
                    break  # 写入中断；此条意图尚未持久化，文件也尚未移动。
                raise ValueError('隔离恢复日志损坏，已停止操作以保留文件')
            if not isinstance(record, dict) or not isinstance(record.get('size'), int) or record['size'] < 0:
                raise ValueError('隔离恢复日志格式无效')
            item = _safe_quarantine_item(record.get('item'), root)
            if item['dst'] in known:
                continue  # 快照成功、日志清理前中断，幂等重放。
            src, dst = Path(item['src']), Path(item['dst'])
            sizes = {record['size']}
            if isinstance(record.get('lstat_size'), int) and record['lstat_size'] >= 0:
                sizes.add(record['lstat_size'])
            if dst.exists() and not src.exists() and dst.stat().st_size in sizes:
                manifest['items'].append(item)
                known.add(item['dst'])
            elif src.exists() and not dst.exists():
                continue  # 意图已保存，但移动尚未开始或已回退。
            else:
                # 仅保留真正未决冲突记录，不阻断同批次已成功移动条目的读取与还原
                manifest.setdefault('_unresolved_journal', []).append(line if line.endswith('\n') else line + '\n')
    return manifest


def _save_quarantine(root, manifest):
    root = Path(root).resolve()
    unresolved = manifest.pop('_unresolved_journal', None) or []
    clean_manifest = {k: v for k, v in manifest.items() if k != '_unresolved_journal'}
    clean_manifest['items'] = [_safe_quarantine_item(item, root) for item in clean_manifest['items']]
    clean_manifest['version'] = 2
    _write_json_atomic(root / 'manifest.json', clean_manifest)
    journal_path = root / '.ad-filter-journal.jsonl'
    if unresolved:
        journal_path.write_text(''.join(unresolved), encoding='utf-8')
    else:
        journal_path.unlink(missing_ok=True)


def _journal_quarantine_move(root, item, size, lstat_size=None):
    payload = {'item': item, 'size': size}
    if lstat_size is not None and lstat_size != size:
        payload['lstat_size'] = lstat_size
    with open(_quarantine_control(root, '.ad-filter-journal.jsonl'), 'a', encoding='utf-8') as stream:
        stream.write(json.dumps(payload, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def _clear_readonly(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _safe_unlink(path: str | Path) -> None:
    try:
        os.remove(path)
    except PermissionError:
        _clear_readonly(Path(path))
        os.remove(path)


def _safe_move(src: Path, dst: Path) -> None:
    try:
        shutil.move(str(src), str(dst))
    except PermissionError:
        _clear_readonly(src)
        if dst.exists() and src.exists():
            # 跨盘移动已完成复制但因源文件只读未能删除源文件，或残留半截目标文件
            try:
                if dst.stat().st_size == src.stat().st_size:
                    os.remove(src)
                    return
                _safe_unlink(dst)
            except OSError:
                pass
        shutil.move(str(src), str(dst))
    except Exception:
        if dst.exists() and src.exists():
            try:
                if dst.stat().st_size != src.stat().st_size:
                    _safe_unlink(dst)
            except OSError:
                pass
        raise


def _root_label(root_path: Path) -> str:
    if root_path.name:
        return root_path.name
    drive = root_path.drive.rstrip(":\\/")
    return f"盘_{drive}" if drive else "根目录"


@_quarantine_serialized(2)
def apply_quarantine(items: list[dict], roots: list[str], quarantine_root: Path, log_fn,
                     *, dst_out: list[str] | None = None) -> tuple[int, list[str], list[str]]:
    """移动到隔离目录，保留 相对扫描根/相对路径；写恢复清单 manifest.json。返回 (成功数, 错误, 成功路径)"""
    quarantine_root = Path(quarantine_root).resolve()
    try:
        manifest = _load_quarantine(quarantine_root)
        # 先确认快照可写并清理已恢复的日志，失败时不开始移动。
        _save_quarantine(quarantine_root, manifest)
    except Exception as e:
        return 0, [f'隔离清单读取或保存失败：{e}'], []
    errors = []
    moved = 0
    ok_paths: list[str] = []
    unresolved_lines: list[str] = list(manifest.get('_unresolved_journal') or [])
    root_names = {}
    for r in roots:
        try:
            rp = Path(r).resolve()
        except (OSError, ValueError):
            clean_r = str(r).replace("\x00", "").strip()
            if not clean_r:
                continue
            rp = Path(os.path.abspath(clean_r))
        root_names[_norm_root(rp)] = _root_label(rp)
    sorted_roots = sorted(root_names.items(), key=lambda kv: len(kv[0]), reverse=True)
    for it in items:
        src = Path(it["path"])
        dst = None
        entry = None
        st_size = 0
        lst_size = None
        try:
            src_abs = Path(os.path.abspath(str(src))) if src.is_symlink() else src.resolve()
            src_res = str(src_abs)
            root_for = None
            for rp, name in sorted_roots:
                if _path_is_within(src_res, rp):
                    root_for = name
                    rel = src_res[len(rp):].lstrip("\\/")
                    break
            if root_for is None:
                root_for, rel = "未识别根", src.name
            dst = quarantine_root / root_for / rel
            if src_abs.is_relative_to(quarantine_root):
                raise ValueError('文件已经位于当前隔离区中')
            _safe_quarantine_item({'src': src_res, 'dst': str(dst)}, quarantine_root)
            dst = _unique_destination(dst, '冲突')
            dst.parent.mkdir(parents=True, exist_ok=True)
            entry = _safe_quarantine_item({'src': src_res, 'dst': str(dst),
                'time': time.strftime('%F %T'), 'reasons': it.get('reasons', [])}, quarantine_root)
            if src.is_symlink():
                lst_size = src.lstat().st_size
                st_size = src.stat().st_size if src.exists() else lst_size
            else:
                st_size = src.stat().st_size
            _journal_quarantine_move(quarantine_root, entry, st_size, lst_size)
            _safe_move(src, dst)
            manifest['items'].append(entry)
            moved += 1
            ok_paths.append(str(src))
            if dst_out is not None:
                dst_out.append(entry['dst'])
            log_fn({"action": "quarantine", "src": str(src), "dst": str(dst)})
        except Exception as e:
            errors.append(f"{src}: {e}")
            if dst is not None and entry is not None and dst.exists() and src.exists():
                rec = {'item': entry, 'size': st_size}
                if lst_size is not None and lst_size != st_size:
                    rec['lstat_size'] = lst_size
                unresolved_lines.append(json.dumps(rec, ensure_ascii=False) + '\n')
    if unresolved_lines:
        manifest['_unresolved_journal'] = unresolved_lines
    try:
        _save_quarantine(quarantine_root, manifest)
    except OSError as e:
        errors.append(f'文件已隔离，恢复记录保存在日志中，下次打开会自动恢复：{e}')
    return moved, errors, ok_paths


def apply_recycle(items: list[dict], log_fn) -> tuple[int, list[str], list[str]]:
    from send2trash import send2trash

    errors, moved, ok_paths = [], 0, []
    for it in items:
        try:
            send2trash(it["path"])
            log_fn({"action": "recycle", "src": it["path"]})
            moved += 1
            ok_paths.append(it["path"])
        except Exception as e:
            errors.append(f"{it['path']}: {e}")
    return moved, errors, ok_paths


def apply_delete(items: list[dict], log_fn) -> tuple[int, list[str], list[str]]:
    errors, moved, ok_paths = [], 0, []
    for it in items:
        try:
            _safe_unlink(it["path"])
            log_fn({"action": "delete", "src": it["path"]})
            moved += 1
            ok_paths.append(it["path"])
        except Exception as e:
            errors.append(f"{it['path']}: {e}")
    return moved, errors, ok_paths


@_quarantine_serialized(0)
def restore_quarantine(quarantine_root: Path, log_fn) -> tuple[int, list[str]]:
    quarantine_root = Path(quarantine_root).resolve()
    if not (quarantine_root / "manifest.json").exists() and not (quarantine_root / '.ad-filter-journal.jsonl').exists():
        return 0, []
    try:
        manifest = _load_quarantine(quarantine_root)
    except Exception as e:
        return 0, [f'读取清单失败：{e}']
    return restore_quarantine_items(quarantine_root, [item['dst'] for item in manifest.get('items', [])], log_fn)


# ---------- 扫描历史 ----------
def append_history(entry: dict, keep: int = 100) -> None:
    """追加一条扫描历史（时间/根/总数/命中/耗时），最多保留 keep 条。"""
    try:
        hist = load_history()
        hist.append(entry)
        trimmed = hist[-keep:] if keep > 0 else []
        _write_json_atomic(HISTORY_PATH, trimmed)
    except Exception:
        pass


def load_history() -> list[dict]:
    try:
        if HISTORY_PATH.exists():
            data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


# ---------- 隔离区条目化管理 ----------
def list_quarantine(quarantine_root: Path) -> list[dict]:
    """读 manifest.json，每条附 exists 标记；目录或清单不存在时直接返回空，不创建目录。"""
    quarantine_root = Path(quarantine_root)
    if not quarantine_root.exists():
        return []
    manifest_path = quarantine_root / "manifest.json"
    journal_path = quarantine_root / '.ad-filter-journal.jsonl'
    if not manifest_path.exists() and not journal_path.exists():
        return []
    with quarantine_lock(quarantine_root, timeout=.5) as root:
        if not (root / "manifest.json").exists() and not (root / '.ad-filter-journal.jsonl').exists():
            return []
        manifest = _load_quarantine(root)
        return [{**it, "exists": Path(it.get("dst", "")).exists()} for it in manifest.get("items", [])]


def _select_manifest(quarantine_root: Path, dsts: list[str]):
    """返回 (manifest, 选中条目, 剩余条目)。"""
    quarantine_root = Path(quarantine_root).resolve()
    manifest = _load_quarantine(quarantine_root)
    if (quarantine_root / '.ad-filter-journal.jsonl').exists():
        _save_quarantine(quarantine_root, manifest)
    want = set()
    for d in dsts:
        try:
            want.add(os.path.normcase(str(Path(d).resolve())))
        except OSError:
            want.add(os.path.normcase(str(d)))
    picked, rest = [], []
    for it in manifest.get("items", []):
        dst_key = os.path.normcase(str(it.get("dst", "")))
        if dst_key in want:
            picked.append(it)
        else:
            rest.append(it)
    return manifest, picked, rest


@_quarantine_serialized(0)
def restore_quarantine_items(quarantine_root: Path, dsts: list[str], log_fn) -> tuple[int, list[str]]:
    """按 dst 精确还原选中条目（manifest 中移除已还原项）。"""
    try:
        manifest, picked, rest = _select_manifest(quarantine_root, dsts)
    except Exception as e:
        return 0, [f"读取清单失败：{e}"]
    errors, restored = [], 0
    for it in picked:
        src, dst = Path(it["dst"]), Path(it["src"])
        try:
            if not src.exists():
                errors.append(f"隔离文件丢失 {src}")
                rest.append(it)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst = _unique_destination(dst, '还原')
            _safe_move(src, dst)
            log_fn({"action": "restore", "dst": str(dst)})
            restored += 1
        except Exception as e:
            errors.append(f"{src}: {e}")
            rest.append(it)
    if picked:
        manifest["items"] = rest
        try:
            _save_quarantine(quarantine_root, manifest)
        except OSError as e:
            errors.append(f'文件已还原，但保存清单失败：{e}')
    return restored, errors


@_quarantine_serialized(0)
def delete_quarantine_items(quarantine_root: Path, dsts: list[str], log_fn) -> tuple[int, list[str]]:
    """按 dst 永久删除选中的隔离文件（manifest 中移除记录）。"""
    try:
        manifest, picked, rest = _select_manifest(quarantine_root, dsts)
    except Exception as e:
        return 0, [f"读取清单失败：{e}"]
    errors, deleted = [], 0
    for it in picked:
        try:
            p = Path(it["dst"])
            if p.exists():
                _safe_unlink(p)
            log_fn({"action": "quarantine_delete", "dst": it["dst"]})
            deleted += 1
        except Exception as e:
            errors.append(f"{it.get('dst')}: {e}")
            rest.append(it)
    if picked:
        manifest["items"] = rest
        try:
            _save_quarantine(quarantine_root, manifest)
        except OSError as e:
            errors.append(f'文件已删除，但保存清单失败：{e}')
    return deleted, errors


_LOG_LOCK = threading.Lock()
_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_KEEP_BYTES = 2 * 1024 * 1024


def log_jsonl(entry: dict):
    try:
        line = json.dumps({**entry, "ts": time.strftime("%F %T")}, ensure_ascii=False) + "\n"
        with _LOG_LOCK:
            if LOG_PATH.exists() and LOG_PATH.stat().st_size > _LOG_MAX_BYTES:
                raw = LOG_PATH.read_bytes()
                tail = raw[-_LOG_KEEP_BYTES:]
                nl = tail.find(b"\n")
                if nl >= 0:
                    tail = tail[nl + 1:]
                tmp = LOG_PATH.with_suffix(".jsonl.tmp")
                tmp.write_bytes(tail)
                os.replace(tmp, LOG_PATH)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


def collect_dirs(mime) -> list[str]:
    """从拖放 mime 数据里取本地文件夹路径列表（忽略文件/无效项）。"""
    dirs = []
    if mime is not None and mime.hasUrls():
        for u in mime.urls():
            if u.isLocalFile():
                p = Path(u.toLocalFile())
                if p.is_dir():
                    dirs.append(str(p))
    return dirs


# ---------------------------------------------------------------- 感知哈希相似聚类
def dhash_bits(path: Path, cache: Cache | None = None) -> int | None:
    """dHash：9x8 灰度右邻差分 → 64bit 感知指纹（同一图转发/重压缩后仍近似）。
    带缓存：二次扫描聚类零开销。"""
    try:
        st = path.stat()
        if cache is not None:
            h = cache.get_hash(str(path), st.st_size, st.st_mtime)
            if h is not None:
                return h
        with Image.open(path) as im:
            try:
                im.draft("L", (9, 8))  # JPEG 快速降采样解码
            except Exception:
                pass
            w, h = im.size
            if im.mode in ("RGB", "L") and max(w, h) > 400:
                try:
                    im = im.reduce(4)  # 块平均快速降采样：PNG 大图不必全尺寸参与 resize
                except Exception:
                    pass
            img = im.convert("L") if im.mode in ("RGB", "L") else _pil_to_rgb(im).convert("L")
        g = img.resize((9, 8))
        px = g.tobytes()
        bits = 0
        for r in range(8):
            base = r * 9
            for c in range(8):
                bits = (bits << 1) | (1 if px[base + c] > px[base + c + 1] else 0)
        if cache is not None:
            cache.put_hash(str(path), st.st_size, st.st_mtime, bits)
        return bits
    except Exception:
        return None


_POPCOUNT_U8 = None


def cluster_similar(paths: list[str], threshold: int = 6, cache: Cache | None = None,
                    *, stats: dict[str, tuple[int, float]] | None = None) -> list[list[str]]:
    """贪心代表聚类：与已有组代表指纹距离 ≤ 阈值入组，否则自成新组。
    只返回成员 ≥2 的组。贪心代表法不传递合并，避免链式漂移把不相似的串到一起。"""
    from concurrent.futures import ThreadPoolExecutor

    unique_paths: list[str] = []
    seen: set[str] = set()
    for p in paths:
        key = os.path.normcase(str(p))
        if key not in seen:
            seen.add(key)
            unique_paths.append(p)
    if len(unique_paths) < 2:
        return []

    hash_map: dict[str, int | None] = {}
    if cache is not None:
        infos: list[tuple[str, int, float]] = []
        stat_map: dict[str, tuple[int, float]] = {}
        for p in unique_paths:
            known = stats.get(p) if stats else None
            if known is not None:
                p_str = str(Path(p))
                infos.append((p_str, known[0], known[1]))
                stat_map[p] = known
                continue
            try:
                st = Path(p).stat()
                p_str = str(Path(p))
                infos.append((p_str, st.st_size, st.st_mtime))
                stat_map[p] = (st.st_size, st.st_mtime)
            except OSError:
                hash_map[p] = None
        cached_hashes = cache.get_hashes_batch(infos)
        missing: list[str] = []
        for p in unique_paths:
            if p in hash_map:
                continue
            p_str = str(Path(p))
            if p_str in cached_hashes:
                hash_map[p] = cached_hashes[p_str]
            else:
                missing.append(p)
        if missing:
            workers = min(len(missing), os.cpu_count() or 4, 8)
            if workers >= 2 and len(missing) >= 4:
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    computed = list(ex.map(lambda p_: dhash_bits(Path(p_), None), missing))
            else:
                computed = [dhash_bits(Path(p_), None) for p_ in missing]
            to_store: list[tuple[str, int, float, int]] = []
            for p, h in zip(missing, computed):
                hash_map[p] = h
                if h is not None and p in stat_map:
                    sz, mt = stat_map[p]
                    to_store.append((str(Path(p)), sz, mt, h))
            cache.put_hashes_batch(to_store)
        cache.commit()
    else:
        for p in unique_paths:
            hash_map[p] = dhash_bits(Path(p), None)

    groups: list[tuple[int, list[str]]] = []
    # 汉明距离 <= t 的两个 64 位数，分成 t+1 段后至少一段完全相同。
    # 只缩小候选范围，仍按原代表创建顺序精确比较，分组语义完全一致。
    parts = threshold + 1 if 0 <= threshold <= 10 else 0
    blocks = [(64*i//parts, 64*(i+1)//parts - 64*i//parts) for i in range(parts)]
    indexes = [{} for _ in blocks]
    reps_np = None
    reps_cap = 0
    if not parts:
        import numpy as np
        global _POPCOUNT_U8
        if _POPCOUNT_U8 is None:
            _POPCOUNT_U8 = np.array([i.bit_count() for i in range(256)], dtype=np.uint8)
        reps_cap = min(max(64, len(unique_paths)), 1024)
        reps_np = np.empty(reps_cap, dtype=np.uint64)

    for p in unique_paths:
        h = hash_map.get(p)
        if h is None:
            continue
        if parts:
            candidates = set()
            for index, (shift, width) in zip(indexes, blocks):
                candidates.update(index.get((h >> shift) & ((1 << width) - 1), ()))
            for gi in sorted(candidates):
                rep, members = groups[gi]
                if (h ^ rep).bit_count() <= threshold:
                    members.append(p)
                    break
            else:
                gi = len(groups)
                groups.append((h, [p]))
                for index, (shift, width) in zip(indexes, blocks):
                    index.setdefault((h >> shift) & ((1 << width) - 1), []).append(gi)
        else:
            n_g = len(groups)
            if n_g >= 64:
                xor_bytes = (reps_np[:n_g] ^ np.uint64(h)).view(np.uint8).reshape(n_g, 8)
                hits = np.flatnonzero(_POPCOUNT_U8[xor_bytes].sum(axis=1) <= threshold)
                if hits.size:
                    groups[int(hits[0])][1].append(p)
                    continue
            else:
                matched = False
                for gi in range(n_g):
                    if (h ^ groups[gi][0]).bit_count() <= threshold:
                        groups[gi][1].append(p)
                        matched = True
                        break
                if matched:
                    continue
            if n_g >= reps_cap:
                reps_cap = max(reps_cap * 2, n_g + 64)
                grown = np.empty(reps_cap, dtype=np.uint64)
                grown[:n_g] = reps_np[:n_g]
                reps_np = grown
            reps_np[n_g] = np.uint64(h)
            groups.append((h, [p]))
    return [members for _, members in groups if len(members) >= 2]


# ---------------------------------------------------------------- 测试样本
def _load_font(size: int):
    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc", "arial.ttf"):
        p = Path("C:/Windows/Fonts") / name
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                continue
    return ImageFont.load_default()


def _synth_photo(w=1200, h=800, seed=0, path=None):
    """合成一张'正常照片'：渐变背景 + 随机色块圆 + 噪点"""
    import random

    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h))
    px = img.load()
    c1 = (rnd.randint(30, 200), rnd.randint(30, 200), rnd.randint(30, 200))
    c2 = (rnd.randint(30, 200), rnd.randint(30, 200), rnd.randint(30, 200))
    for y in range(h):
        t = y / h
        row = tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3))
        for x in range(w):
            px[x, y] = row
    d = ImageDraw.Draw(img)
    for _ in range(12):
        x0, y0 = rnd.randint(0, w - 60), rnd.randint(0, h - 60)
        col = (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
        if rnd.random() < 0.5:
            d.ellipse([x0, y0, x0 + rnd.randint(40, 200), y0 + rnd.randint(40, 200)], fill=col)
        else:
            d.rectangle([x0, y0, x0 + rnd.randint(40, 200), y0 + rnd.randint(40, 200)], fill=col)
    img = img.filter(ImageFilter.GaussianBlur(1))
    if path:
        img.save(path, quality=88)
    return img


def make_fixtures(testdir: Path):
    testdir.mkdir(parents=True, exist_ok=True)
    font_big = _load_font(56)
    font_mid = _load_font(40)

    # 正常图
    for i in range(3):
        _synth_photo(seed=i, path=testdir / f"normal_{i+1:02d}.jpg")

    # —— 第一种：TG 引流拼图（左截图 + 右二维码卡 + 红字推广） ——
    import qrcode

    W, H = 1470, 810
    canvas = Image.new("RGB", (W, H), (18, 22, 34))
    left = _synth_photo(700, H, seed=99)
    canvas.paste(left, (0, 0))
    d = ImageDraw.Draw(canvas)
    d.text((20, 300), "免费 免费 免费", font=font_big, fill=(255, 220, 60))
    d.text((20, 380), "3D 韩漫 每日更新", font=font_mid, fill=(255, 255, 255))
    card = Image.new("RGB", (500, 500), (245, 248, 252))
    canvas.paste(card, (820, 60))
    qr = qrcode.QRCode(border=2, box_size=6)
    qr.add_data("https://t.me/demo")
    qr.make(fit=True)
    qimg = qr.make_image(fill_color="#1a6fc4", back_color="white").convert("RGB").resize((320, 320))
    canvas.paste(qimg, (910, 110))
    d = ImageDraw.Draw(canvas)
    d.text((960, 445), "@DemoChannel", font=font_mid, fill=(26, 111, 196))
    d.text((20, 680), "想看高清版 可以进我的频道 浏览器搜 https://t.me/demo", font=font_mid, fill=(230, 40, 40))
    canvas.save(testdir / "type1_拼图引流.jpg", quality=88)

    # —— 第二种：牛皮癣长条（独立贴图） ——
    strip_txt = "本资源由 https://t.me/demo 免费发布 不收费没有门槛 浏览器搜前面链接就可以进入"
    strip = Image.new("RGB", (1720, 82), (216, 216, 216))
    ImageDraw.Draw(strip).text((14, 12), strip_txt, font=_load_font(44), fill=(70, 70, 70))
    strip.save(testdir / "type2_长条贴图.png")

    # —— 第二种变体：长条贴在正常图上 ——
    photo = _synth_photo(1200, 800, seed=7)
    photo.paste(strip.resize((1200, int(82 * 1200 / 1720))), (0, 0))
    ImageDraw.Draw(photo).text((600, 740), strip_txt[:24], font=_load_font(30), fill=(90, 90, 90))
    photo.save(testdir / "type2_贴在图上.jpg", quality=88)

    # —— 第三种：半透明空心字压在图上（左上 + 右下） ——
    photo2 = _synth_photo(1200, 800, seed=13).convert("RGBA")
    ov = Image.new("RGBA", photo2.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    od.text((40, 30), "https://t.me/demo 免费发布", font=_load_font(58),
            fill=(255, 255, 255, 50), stroke_width=3, stroke_fill=(255, 255, 255, 210))
    od.text((430, 700), "本资源由 t.me/demo 免费发布 不收费", font=_load_font(36),
            fill=(255, 255, 255, 40), stroke_width=2, stroke_fill=(255, 255, 255, 190))
    out = Image.alpha_composite(photo2, ov).convert("RGB")
    out.save(testdir / "type3_透明字水印.jpg", quality=88)

    # —— 性能基准目录 ——
    bench = testdir / "bench"
    bench.mkdir(exist_ok=True)
    for i in range(400):
        _synth_photo(512, 340, seed=1000 + i, path=bench / f"bench_{i:04d}.jpg")
    print(f"测试样本已生成：{testdir}")


# ---------------------------------------------------------------- 自测
def selftest():
    with tempfile.TemporaryDirectory(prefix='ad_selftest_') as directory:
        root = Path(directory)
        cache = Cache(root / 'cache.db')
        try:
            return _selftest(cache, root)
        finally:
            cache.conn.close()


def _selftest(cache, temporary):
    testdir = APP_DIR / "testdata"
    if not (testdir / "type1_拼图引流.jpg").exists():
        testdir = temporary / 'fixtures'
        make_fixtures(testdir)
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["ocr"] = {**cfg["ocr"], "enabled": True, "full_image_if_nothing": False}

    expect_ad = [
        "type1_拼图引流.jpg", "type2_长条贴图.png", "type2_贴在图上.jpg", "type3_透明字水印.jpg",
    ]
    # 真实样本（不入库）：命名为 real*.png/jpg 放进 testdata/ 即参与自测，缺失自动跳过
    expect_ad_optional = sorted(str(x.name) for x in testdir.glob("real*")
                                if x.suffix.lower() in IMG_EXTS)
    expect_clean = [f"normal_{i+1:02d}.jpg" for i in range(3)]

    rows, ok = [], True
    for name in expect_ad + [x for x in expect_ad_optional if (testdir / x).exists()] + expect_clean:
        p = testdir / name
        if not p.exists():
            rows.append((name, "MISSING", "-", []))
            ok = False
            continue
        r = analyze_file(p, cfg, cache, {"qr": True, "ocr": True})
        rows.append((name, r["verdict"], f"{r['score']:.0f}", [h["label"] for h in r["reasons"]]))
        want_ad = name in expect_ad or name in expect_ad_optional
        got_hit = r["verdict"] in ("ad", "suspect")
        if want_ad != got_hit:
            ok = False

    print(f"{'文件':<28}{'结果':<10}{'分':<6}命中")
    for name, v, s, labels in rows:
        print(f"{name:<28}{v:<10}{s:<6}{'; '.join(labels)[:110]}")
    print("\n自测结果：", "全部通过 ✅" if ok else "存在失败 ❌")

    # —— 弱词误伤回归："发布"已从弱词移除，正常图文案不应命中 ——
    _, score_pub, _ = score_ocr_texts(["本作品发布于2024年1月"], cfg["ocr_keywords"])
    pub_ok = score_pub == 0
    print(f"弱词误伤：{'通过 ✅' if pub_ok else '失败 ❌'}（'发布'不命中={pub_ok}）")
    ok = ok and pub_ok

    # —— 缓存配置版本失效：规则变更后自动清空旧缓存 ——
    _cd = Path(tempfile.mkdtemp(prefix="adfilter_cachever_selftest_"))
    try:
        with Cache(_cd / "c.db") as c:
            same = c.check_config_version(cfg)          # 首次：无记录 → 清空并写入 hash，返回 False
            again = c.check_config_version(cfg)         # 同配置 → True
            cfg2 = dict(cfg)
            cfg2["ocr_keywords"] = cfg["ocr_keywords"] + [{"pattern": "x", "weight": 1, "label": "y"}]
            changed = c.check_config_version(cfg2)      # 规则变了 → False（已清空）
        ver_ok = (not same) and again and (not changed)
        print(f"缓存配置版本：{'通过 ✅' if ver_ok else '失败 ❌'}"
              f"（同配置保留={again}，规则变更清空={not changed}）")
        ok = ok and ver_ok
    finally:
        shutil.rmtree(_cd, ignore_errors=True)

    # —— 感知哈希聚类自测：同图两份（重编码一次）应聚成一组，不同图不聚 ——
    _td = Path(tempfile.mkdtemp(prefix="adfilter_phash_selftest_"))
    try:
        a = testdir / "type2_长条贴图.png"
        b = _td / "copy.png"
        c = testdir / "normal_01.jpg"
        with Image.open(a) as im:
            im.convert("RGB").save(b)
        groups = cluster_similar([str(a), str(b), str(c)], threshold=6)
        pair = [g for g in groups if str(a) in g and str(b) in g]
        mixed = [g for g in groups if str(c) in g]
        phash_ok = len(pair) == 1 and not mixed
        print(f"相似聚类：{'通过 ✅' if phash_ok else '失败 ❌'}"
              f"（同图重编码聚组={len(pair) == 1}，正常图未误聚={not mixed}）")
        ok = ok and phash_ok
    finally:
        shutil.rmtree(_td, ignore_errors=True)
    print("\n最终结果：", "全部通过 ✅" if ok else "存在失败 ❌")
    return 0 if ok else 1


def bench():
    with tempfile.TemporaryDirectory(prefix="adfilter_bench_") as directory:
        root = Path(directory)
        testdir = APP_DIR / "testdata" / "bench"
        if not testdir.exists() or len(list(testdir.glob("*.jpg"))) < 100:
            print("先生成临时测试样本…")
            make_fixtures(root / "fixtures")
            testdir = root / "fixtures" / "bench"
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        files = gather_images([str(testdir)], cfg)
        print(f"基准：{len(files)} 张")
        if not files:
            print("无可用基准图片。")
            return

        for label, enable in (("文件名+形状+二维码", {"filename": True, "shape": True, "qr": True, "ocr": False}),
                              ("再加OCR(仅条带)", {"filename": True, "shape": True, "qr": False, "ocr": True})):
            with Cache(root / "bench_cache.db") as cache:
                cache.conn.execute("DELETE FROM results")
                cache.conn.commit()
                t0 = time.time()
                n = 0
                for p in files if "OCR" not in label else files[:40]:
                    analyze_file(p, cfg, cache, enable)
                    n += 1
                cache.commit()
            dt = time.time() - t0
            print(f"{label:<22} {n} 张，共 {dt:.1f}s，平均 {dt / max(1, n) * 1000:.0f} ms/张")


def main():
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="广告图过滤（纯本地）")
    ap.add_argument("--make-fixtures", action="store_true", help="生成测试样本")
    ap.add_argument("--selftest", action="store_true", help="引擎自测（无界面）")
    ap.add_argument("--smoke", action="store_true", help="GUI 冒烟测试")
    ap.add_argument("--gui-test", action="store_true", help="GUI 功能自测（真界面+临时目录，不碰真实数据）")
    ap.add_argument("--bench", action="store_true", help="性能基准")
    args = ap.parse_args()

    if args.make_fixtures:
        make_fixtures(APP_DIR / "testdata")
        return
    if args.selftest:
        sys.exit(selftest())
    if args.bench:
        bench()
        return
    if args.gui_test:
        from ad_filter_gui import gui_func_test
        sys.exit(gui_func_test())
    if args.smoke:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from ad_filter_gui import run_gui

        import ad_filter as engine
        names = ('CONFIG_PATH', 'CACHE_PATH', 'LOG_PATH', 'HISTORY_PATH')
        original = [getattr(engine, name) for name in names]
        with tempfile.TemporaryDirectory(prefix='ad_smoke_') as directory:
            root = Path(directory)
            win = None
            try:
                for name, filename in zip(names, ('cfg.json', 'cache.db', 'log.jsonl', 'history.json')):
                    setattr(engine, name, root / filename)
                qapp = QApplication.instance() or QApplication([])
                win = run_gui(return_app=True)
                win._quarantine_override = root / 'quarantine'
                win.show()
                QTimer.singleShot(3000, win.close)
                qapp.exec()
            finally:
                if win is not None:
                    try:
                        win._cache.close()
                    except Exception:
                        pass
                for name, filename in zip(names, original):
                    setattr(engine, name, filename)
        return
    from ad_filter_gui import run_gui
    run_gui()


if __name__ == "__main__":
    main()
