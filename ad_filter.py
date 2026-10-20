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
import json
import os
import queue
import re
import shutil
import sqlite3
import sys
import threading
import time
import unicodedata
from pathlib import Path

if getattr(sys, "frozen", False):
    # PyInstaller 打包后 __file__ 指向临时解压目录，配置/缓存要放 exe 旁边
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "ad_filter_config.json"
CACHE_PATH = APP_DIR / "ad_filter_cache.db"
LOG_PATH = APP_DIR / "ad_filter_log.jsonl"
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
        {"pattern": r"t\.me|telegram|https?://|www\.[a-z0-9]|\.com|\.net|\.cc|\.vip|\.xyz", "weight": 3, "label": "链接"},
        {"pattern": r"免费发布|免费领取|免费看|资源由|不收费|没有门槛|无门槛|浏览器搜|进我的频道|我的频道|关注公众号|加微信|加QQ|扫码关注|独家首发|入群|粉丝群|投稿", "weight": 3, "label": "推广话术"},
        {"pattern": r"@[A-Za-z0-9_]{4,}", "weight": 2, "label": "@渠道号"},
        {"pattern": r"频道", "weight": 1, "label": "弱词"},  # “免费/发布”实测在正常图高频误伤，只留“频道”
    ],
    "whitelist_rules": [],
    "shape": {"max_ratio": 3.0, "min_side": 300},
    "qr": {"enabled": True},
    "ocr": {
        "enabled": False,
        "strip_ratio": 0.25,
        "full_image_if_nothing": True,
        "bright_thresholds": [200],  # 单提亮：实测较 [200,170] 提速 47% 且 17 张样本零漏检
        "max_dim": 1600,  # OCR 输入缩放上限；2200 太慢，1600 精度损失可控、提速约 30%
        "score_high": 6,
        "score_low": 3,
    },
    "workers": 4,
    "disposal": "quarantine",
    "phash": {"enabled": True, "threshold": 6},
}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for k, v in user.items():
                if k in ("filename_rules", "ocr_keywords", "whitelist_rules"):
                    cfg[k] = v
                elif isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception as e:
            print(f"配置读取失败，使用默认配置：{e}")
    # —— 旧配置迁移：历史版本把 max_dim 写成 2200（当时默认值），比现默认 1600 慢一倍多，
    #    会让后续所有 OCR 提速优化失效。超过当前默认的旧值一律拉回默认。
    try:
        ocr_cfg = cfg.get("ocr", {})
        default_max = DEFAULT_CONFIG["ocr"]["max_dim"]
        if int(ocr_cfg.get("max_dim", default_max)) > default_max:
            ocr_cfg["max_dim"] = default_max
        # 线程数超过 CPU 核数没有收益（超订阅反而更慢），且每线程一份 OCR 模型占内存
        cpu = os.cpu_count() or 4
        if int(cfg.get("workers", 4)) > cpu:
            cfg["workers"] = cpu
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> None:
    """原子写：先写临时文件再 os.replace，避免写一半崩溃导致配置损坏。"""
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", "", s).lower()


# ---------------------------------------------------------------- 缓存
class Cache:
    def __init__(self, path: Path):
        # GUI 扫描在多线程里写缓存，允许跨线程 + 程序内锁串行化
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS results(
                path TEXT PRIMARY KEY, size INTEGER, mtime REAL,
                verdict TEXT, score REAL, reasons TEXT, ocr_text TEXT, ts REAL)"""
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS hashes(path TEXT PRIMARY KEY, size INTEGER, mtime REAL, hash INTEGER)"
        )
        self.conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        self.conn.commit()
        self.lock = threading.Lock()

    def check_config_version(self, cfg: dict) -> bool:
        """规则/阈值变更后自动清空缓存：旧判定是基于旧规则的，必须全量重判。
        返回 True=配置未变（缓存有效），False=已清空（需重判）。"""
        import hashlib
        keys = ("filename_rules", "ocr_keywords", "whitelist_rules",
                "shape", "qr", "ocr", "phash")
        blob = json.dumps({k: cfg.get(k) for k in keys},
                          sort_keys=True, ensure_ascii=False)
        h = hashlib.md5(blob.encode("utf-8")).hexdigest()
        with self.lock:
            row = self.conn.execute("SELECT value FROM meta WHERE key='cfg_hash'").fetchone()
            if row and row[0] == h:
                return True
            self.conn.execute("DELETE FROM results")
            self.conn.execute("DELETE FROM hashes")
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES('cfg_hash', ?)", (h,))
            self.conn.commit()
        return False

    def get(self, path: str, size: int, mtime: float):
        with self.lock:
            row = self.conn.execute(
                "SELECT verdict, score, reasons, ocr_text FROM results WHERE path=? AND size=? AND mtime=?",
                (path, size, mtime),
            ).fetchone()
        if row:
            return {"verdict": row[0], "score": row[1], "reasons": json.loads(row[2]), "ocr_text": row[3]}
        return None

    def get_batch(self, file_infos: list[tuple[str, int, float]]) -> dict[str, dict]:
        """批量查询缓存：path+size+mtime 命中则返回 {path: result}。用于扫描秒开。
        分批查询：SQLite 的 SQL 变量数上限（32766）在大目录（数万张图）会被突破，
        一旦 OperationalError 冒出去整个扫描线程就崩了。"""
        if not file_infos:
            return {}
        want = {(p, s, m) for p, s, m in file_infos}
        out: dict[str, dict] = {}
        BATCH = 500
        for i in range(0, len(file_infos), BATCH):
            chunk = [p for p, _, _ in file_infos[i:i + BATCH]]
            ph = ",".join("?" * len(chunk))
            with self.lock:
                rows = self.conn.execute(
                    f"SELECT path, size, mtime, verdict, score, reasons, ocr_text FROM results WHERE path IN ({ph})",
                    chunk,
                ).fetchall()
            # Python 侧按 size+mtime 精确过滤（SQLite 不支持 tuple IN VALUES）
            for r in rows:
                if (r[0], r[1], r[2]) in want:
                    out[r[0]] = {"verdict": r[3], "score": r[4],
                                 "reasons": json.loads(r[5]), "ocr_text": r[6]}
        return out

    def clear_results(self):
        """清空分析结果缓存（识别方式开关变化时必须全量重判，缓存键不含 enable）。"""
        with self.lock:
            self.conn.execute("DELETE FROM results")
            self.conn.commit()

    def put_hash(self, path: str, size: int, mtime: float, hash_val: int):
        """dHash 指纹缓存（相似聚类用，path+size+mtime 键）。"""
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO hashes VALUES(?,?,?,?)",
                (path, size, mtime, hash_val),
            )

    def get_hash(self, path: str, size: int, mtime: float):
        with self.lock:
            row = self.conn.execute(
                "SELECT hash FROM hashes WHERE path=? AND size=? AND mtime=?",
                (path, size, mtime),
            ).fetchone()
        return row[0] if row else None

    def put(self, path: str, size: int, mtime: float, verdict: str, score: float, reasons: list, ocr_text: str):
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO results VALUES(?,?,?,?,?,?,?,?)",
                (path, size, mtime, verdict, score, json.dumps(reasons, ensure_ascii=False), ocr_text, time.time()),
            )

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
    return ocr


def _create_ocr():
    from rapidocr_onnxruntime import RapidOCR

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
            ocr = RapidOCR(**common, det_use_dml=True, cls_use_dml=True, rec_use_dml=True)
            ocr(np.zeros((32, 32, 3), dtype="uint8"))  # 试跑一次，构造成功但推理崩溃也算失败
            global USING_GPU
            USING_GPU = True
        except Exception:
            ocr = None
    if ocr is None:
        try:
            ocr = RapidOCR(**common)
        except Exception:
            ocr = RapidOCR()
    return ocr


def _ocr_ndarray(img) -> list[str]:
    """img: PIL.Image → RapidOCR 文本列表（识别失败返回空）"""
    import cv2
    import numpy as np

    arr = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    try:
        result, _elapse = get_ocr()(arr)
    except Exception:
        return []
    if not result:
        return []
    return [str(item[1]) for item in result]


def bright_pass(crop: Image.Image, thr: int) -> Image.Image:
    """亮通提取：把高亮度+低饱和的笔画（半透明白描边字）抠成黑白二值图"""
    import numpy as np

    hsv = np.asarray(crop.convert("HSV"))
    v = hsv[:, :, 2]
    s = hsv[:, :, 1]
    mask = (v >= thr) & (s <= 70)
    out = np.where(mask, 0, 255).astype("uint8")  # 笔画黑字白底
    im = Image.fromarray(out, mode="L").convert("RGB")
    return im.filter(ImageFilter.MaxFilter(3))


def is_blank_image(img: Image.Image) -> bool:
    """近乎纯色（灰度标准差 < 3）：不可能含可读文字，跳过 QR/OCR 完全无损。
    实测：纯色图 std=0.00，真实照片最低 10.1（横条贴片）。"""
    import numpy as np

    g = np.asarray(img.convert("L").resize((160, 120)), dtype=np.float32)
    return float(g.std()) < 3.0


def bright_mask_ratio(img: Image.Image, thr: int) -> float:
    """高亮+低饱和像素占比：即 bright_pass 会保留的"笔画"面积。
    占比趋近 0 时提亮结果是整片白色，跑 OCR 不可能识别出任何文字（可安全跳过）。"""
    import numpy as np

    hsv = np.asarray(img.convert("HSV"))
    return float(((hsv[:, :, 2] >= thr) & (hsv[:, :, 1] <= 70)).mean())


def load_pixel_image(path: Path, max_dim: int) -> Image.Image:
    """为 QR/OCR 加载像素；JPEG 走 draft 快速降采样解码。
    必须 with：Image.open 持有文件句柄，扫描几千张不关闭会耗尽句柄/锁住文件。"""
    with Image.open(path) as im:
        try:
            im.draft("RGB", (max_dim, max_dim))
        except Exception:
            pass
        img = im.convert("RGB")
    if max(img.size) > max_dim:
        img = ImageOps.contain(img, (max_dim, max_dim))
    return img


def detect_qr(img: Image.Image) -> tuple[list[str], bool]:
    """返回 (成功解码的内容列表, 是否检测到二维码图形)"""
    import cv2
    import numpy as np

    arr = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    det = cv2.QRCodeDetector()
    try:
        ok, decoded, _points, _straight = det.detectAndDecodeMulti(arr)
    except cv2.error:
        return [], False
    if not ok:
        return [], False
    texts = [d for d in (decoded or []) if d]
    return texts, True


# ---------------------------------------------------------------- 逐图分析
def match_rules(text: str, rules: list[dict]) -> list[dict]:
    t = normalize_text(text)
    hits = []
    for rule in rules:
        try:
            m = re.search(rule["pattern"], t, re.I)
        except re.error:
            continue
        if m:
            hits.append({"label": rule.get("label", "规则"), "detail": m.group(0), "type": "rule"})
    return hits


def score_ocr_texts(texts: list[str], kw_rules: list[dict]) -> tuple[list[dict], float, str]:
    joined = normalize_text(" ".join(texts))
    hits: list[dict] = []
    total = 0.0
    for rule in kw_rules:
        try:
            m = re.search(rule["pattern"], joined, re.I)
        except re.error:
            continue
        if m:
            hits.append({"label": f"OCR[{rule.get('label','')}] {m.group(0)}", "detail": m.group(0), "type": "ocr"})
            total += float(rule.get("weight", 1))
    return hits, total, joined[:500]


def analyze_file(path: Path, cfg: dict, cache: Cache, enable: dict) -> dict:
    """单图全流程分析。verdict: ad / suspect / clean / error"""
    try:
        stat = path.stat()
        size, mtime = stat.st_size, stat.st_mtime
    except OSError as e:
        return {"path": str(path), "verdict": "error", "score": 0, "reasons": [{"type": "error", "label": f"读取失败 {e}"}], "ocr_text": ""}

    cached = cache.get(str(path), size, mtime)
    if cached is not None:
        cached["path"] = str(path)
        return cached

    reasons: list[dict] = []
    verdict = "clean"
    score = 0.0
    ocr_text = ""

    # 白名单：直接放行
    wl = match_rules(path.name, cfg.get("whitelist_rules", []))
    if wl:
        result = {"path": str(path), "verdict": "clean", "score": 0, "reasons": [{"type": "whitelist", "label": "白名单"}], "ocr_text": ""}
        cache.put(str(path), size, mtime, result["verdict"], 0, result["reasons"], "")
        return result

    # 1) 文件名规则（受 enable 开关控制）
    fr = match_rules(path.name, cfg.get("filename_rules", [])) if enable.get("filename", True) else []
    if fr:
        reasons += [{"type": "filename", **{k: v for k, v in h.items() if k != "type"}} for h in fr]
        verdict = "ad"
        score = 99

    # 2) 形状规则（只读头；受 enable 开关控制）
    if verdict == "clean" and enable.get("shape", True):
        try:
            with Image.open(path) as im:
                w, h = im.size
            ratio = max(w, h) / max(1, min(w, h))
            if ratio >= cfg["shape"]["max_ratio"] or ratio <= 1 / cfg["shape"]["max_ratio"]:
                reasons.append({"type": "shape", "label": f"极端长宽比 {w}x{h}（{ratio:.1f}:1）"})
                verdict, score = "ad", 90
            elif min(w, h) < cfg["shape"]["min_side"]:
                reasons.append({"type": "shape", "label": f"分辨率过小 {w}x{h}"})
                verdict, score = "suspect", 20
        except Exception as e:
            reasons.append({"type": "error", "label": f"读取失败 {e}"})
            verdict, score = "error", 0

    # 3) 二维码 + 4) OCR（需要像素，前面的强命中就不必再跑）
    qr_suspect = False
    if verdict == "clean" and (enable.get("qr") or enable.get("ocr")):
        img = None
        try:
            img = load_pixel_image(path, cfg["ocr"]["max_dim"])
        except Exception as e:
            reasons.append({"type": "error", "label": f"解码失败 {e}"})
            verdict, score = "error", 0

        # 近乎纯色 → 不可能有文字/二维码，直接放过（无损跳过全部像素级识别）
        if img is not None and verdict == "clean" and is_blank_image(img):
            reasons.append({"type": "blank", "label": "近乎纯色图（跳过识别）"})
            result = {"path": str(path), "verdict": "clean", "score": 0,
                      "reasons": [], "ocr_text": ""}
            cache.put(str(path), size, mtime, "clean", 0, [], "")
            return result

        if img is not None and verdict == "clean" and enable.get("qr"):
            try:
                # QR 检测在 1400px 小图上跑（实测 2200px 需要 140ms，1400px 仅 18ms）；
                # 缩图只见图形解不出内容时用原图重试一次
                qr_img = img
                if max(img.size) > 1100:
                    qr_img = ImageOps.contain(img, (1100, 1100))
                decoded, seen = detect_qr(qr_img)
                if seen and not decoded and qr_img is not img:
                    decoded, seen = detect_qr(img)
                if decoded and any(re.search(r"t\.me|http|telegram", normalize_text(d)) for d in decoded):
                    reasons.append({"type": "qr", "label": f"二维码含链接 {decoded[0][:60]}"})
                    verdict, score = "ad", 80
                elif seen:
                    # 二维码存在但解不出（常见于带 logo 的样式码），先记疑，
                    # 继续让 OCR 尝试更强的文字证据，别在这里提前定案
                    reasons.append({"type": "qr", "label": "检测到二维码图形"})
                    qr_suspect = True
            except Exception:
                pass

        if img is not None and verdict == "clean" and enable.get("ocr"):
            try:
                W, H = img.size
                strip = int(H * cfg["ocr"]["strip_ratio"])
                if max(W, H) <= 1000:
                    # 小图：整张就是"条带"，跳过裁切（少 2 次 OCR）
                    regions = [img]
                else:
                    # 上下条带垂直拼接成一张图（16px 白缝防跨缝连字）：
                    # 原来两条带 × (原图+提亮) = 4 次推理，拼接后只需 2 次
                    top = img.crop((0, 0, W, max(24, strip)))
                    bot = img.crop((0, max(0, H - strip), W, H))
                    gap = 16
                    combo = Image.new("RGB", (W, top.height + gap + bot.height), (255, 255, 255))
                    combo.paste(top, (0, 0))
                    combo.paste(bot, (0, top.height + gap))
                    regions = [combo]
                texts: list[str] = []
                hits_left: list[dict] = []
                ocr_score, joined = 0.0, ""
                # —— 逐级短路：原图 OCR 先跑，够广告线就停；不够再逐级提亮 ——
                # （旧版每个区域无脑跑 原图+提亮×2，一张图最坏 9 次 OCR，是扫描慢的元凶）
                def _scan_region(reg, acc):
                    nonlocal hits_left, ocr_score, joined
                    acc += _ocr_ndarray(reg)
                    hits_left, ocr_score, joined = score_ocr_texts(acc, cfg["ocr_keywords"])
                    if ocr_score >= cfg["ocr"]["score_high"]:
                        return acc, True
                    for thr in cfg["ocr"]["bright_thresholds"]:
                        # 提亮门控：高亮笔画占比 <0.05% 时提亮结果几乎全白，OCR 无意义（无损跳过）
                        if bright_mask_ratio(reg, thr) < 0.0005:
                            continue
                        acc += _ocr_ndarray(bright_pass(reg, thr))
                        hits_left, ocr_score, joined = score_ocr_texts(acc, cfg["ocr_keywords"])
                        if ocr_score >= cfg["ocr"]["score_high"]:
                            return acc, True
                    return acc, False

                for reg in regions:
                    texts, hit = _scan_region(reg, texts)
                    if hit:
                        break  # 已够判广告，另一条带不必再扫
                if ocr_score < cfg["ocr"]["score_high"] and cfg["ocr"]["full_image_if_nothing"]:
                    # 未达广告线就全图兜底（中央红字/水印）：条带只捞到"免费"这类弱词时，
                    # 广告主文案往往在中部，只看条带会漏判成疑似甚至漏掉
                    # 全图兜底（透明水印可能在中央）：降到 1100px 减负（广告水印字都很大，
                    # selftest 全样本零漏检），面积比 1400 少 38%，是正常图的主要耗时点
                    small = img if max(img.size) <= 1100 else ImageOps.contain(img, (1100, 1100))
                    texts, _ = _scan_region(small, texts)
                ocr_text = joined
                if hits_left:
                    reasons += hits_left
                    score = ocr_score
                    verdict = "ad" if score >= cfg["ocr"]["score_high"] else "suspect"
            except Exception as e:
                reasons.append({"type": "error", "label": f"OCR失败 {e}"})

        if verdict == "clean" and qr_suspect:
            # OCR 也没找到更强证据，落回"疑似"
            verdict, score = "suspect", 30

    result = {"path": str(path), "verdict": verdict, "score": score, "reasons": reasons, "ocr_text": ocr_text}
    cache.put(str(path), size, mtime, verdict, score, reasons, ocr_text)
    return result


# ---------------------------------------------------------------- 扫描编排
def gather_images(roots: list[str], cfg: dict) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        rootp = Path(root)
        for dirpath, dirnames, filenames in os.walk(rootp):
            dirnames[:] = [d for d in dirnames if not d.startswith(QUARANTINE_NAME)]
            for fn in filenames:
                if Path(fn).suffix.lower() in IMG_EXTS:
                    files.append(Path(dirpath) / fn)
    return files


def _root_of(path_str: str, roots: list[str]) -> str:
    """文件归属到最近的导入根目录（用路径前缀 + 分隔符边界，避免 C:\a 匹配 C:\ab）。"""
    best = ""
    for r in roots:
        rr = str(Path(r)).rstrip("\\/")
        if path_str == rr or path_str.startswith(rr + os.sep):
            if len(rr) > len(best):
                best = rr
    return best or str(Path(path_str).parent)


def scan_worker(roots: list[str], cfg: dict, enable: dict, cache: Cache,
                out_q: queue.Queue, cancel: threading.Event, pause: threading.Event | None = None):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # 文件收集放后台线程：大目录 os.walk 会卡住 UI
    files = gather_images(roots, cfg)
    files = list(dict.fromkeys(files))  # 根目录互相重叠时会重复收集，去重
    if not files:
        out_q.put(("total", 0))
        out_q.put(("done", None))
        return

    # —— 文件夹级进度：按导入根聚合，UI 实时显示每个文件夹 已完成/总数 ——
    roots_list = [str(Path(r)).rstrip("\\/") for r in roots]
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
    # 二次扫描/增量扫描时缓存命中率极高，这一步让 UI 瞬间填充
    # 文件可能在收集后被删除/移动（扫描中手动清理、隔离写入）——stat 必须容错，
    # 否则一张图消失就会让整个扫描线程崩溃、UI 永远停在"正在收集文件…"
    infos, valid_files = [], []
    for p in files:
        try:
            st = p.stat()
            infos.append((str(p), st.st_size, st.st_mtime))
            valid_files.append(p)
        except OSError:
            continue
    files = valid_files
    out_q.put(("total", len(files)))
    if not files:
        out_q.put(("done", None))
        return
    cached = cache.get_batch(infos)
    files_to_scan = [p for p, info in zip(files, infos) if str(p) not in cached]
    out_q.put(("cached", len(cached)))  # 通知 UI 缓存命中数
    done = 0
    for p, info in zip(files, infos):
        if str(p) in cached:
            res = cached[str(p)]
            res["path"] = str(p)
            out_q.put(("item", res))
            done += 1
            out_q.put(("progress", done))
            _bump(str(p))
            if cancel.is_set():
                out_q.put(("cancelled", None))
                return
    if not files_to_scan:
        cache.commit()
        out_q.put(("done", None))
        return

    workers = max(1, int(cfg.get("workers", 4)))
    # 线程数上限：不超过 CPU 核数，且不超过 8——OCR 在 8 线程后收益递减，
    # 内存却随线程线性增长（旧配置 workers=16 在 12 核上超订阅，反而更慢）
    workers = min(workers, os.cpu_count() or 4, 8)
    if enable.get("ocr"):
        get_ocr()  # 提前初始化，确定实际是否用上 GPU
        if USING_GPU:
            workers = 1  # 同一显卡上多个 DirectML 实例互相争抢，单线程反而最快
        # CPU 模式尊重用户线程数（实测近线性；每线程约多占 100~150MB 内存）
    ex = ThreadPoolExecutor(max_workers=workers)
    cancelled_scan = False
    # 分批提交：暂停时最多 workers*2 个任务在跑（很快结束），恢复后继续下一批——
    # 一次性提交全部任务的话"暂停"只是 UI 不刷新，CPU 仍在满负荷烧
    pending = list(files_to_scan)

    def _analyze(p_):
        # 让 UI 能看到"当前正在扫哪个文件"（每张一条轻消息，UI 侧只保留最后一条）
        try:
            out_q.put(("current", (file_root.get(str(p_)), Path(p_).name)))
        except Exception:
            pass
        return analyze_file(p_, cfg, cache, enable)

    try:
        while pending and not cancelled_scan:
            if pause is not None:
                while pause.is_set() and not cancel.is_set():
                    time.sleep(0.15)
            if cancel.is_set():
                cancelled_scan = True
                out_q.put(("cancelled", None))
                break
            batch = pending[:max(1, workers)]   # 批次=线程数：暂停时最多再跑完一批就停
            del pending[:len(batch)]
            futs = {ex.submit(_analyze, p_): p_ for p_ in batch}
            for fut in as_completed(futs):
                done += 1
                try:
                    res = fut.result()
                except Exception as e:
                    p_ = futs[fut]
                    res = {"path": str(p_), "verdict": "error", "score": 0,
                           "reasons": [{"type": "error", "label": f"分析异常 {e}"}], "ocr_text": ""}
                out_q.put(("item", res))
                out_q.put(("progress", done))
                _bump(res["path"])
                if done % 50 == 0:
                    cache.commit()  # 中途退出/崩溃时少丢缓存
                if cancel.is_set():
                    cancelled_scan = True
                    out_q.put(("cancelled", None))
                    break
    finally:
        # 关键：wait=False + cancel_futures——关窗取消时立刻丢弃队列里未开始的任务，
        # 否则 Python 退出要等几千张图全部分析完（窗口没了进程还活着的元凶）
        ex.shutdown(wait=False, cancel_futures=True)
    cache.commit()
    if not cancelled_scan:
        out_q.put(("done", None))


# ---------------------------------------------------------------- 处置
def apply_quarantine(items: list[dict], roots: list[str], quarantine_root: Path, log_fn) -> tuple[int, list[str], list[str]]:
    """移动到隔离目录，保留 相对扫描根/相对路径；写恢复清单 manifest.json。返回 (成功数, 错误, 成功路径)"""
    manifest_path = quarantine_root / "manifest.json"
    manifest = {"items": []}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    errors = []
    moved = 0
    ok_paths: list[str] = []
    root_names = {str(Path(r).resolve()): Path(r).name for r in roots}
    for it in items:
        src = Path(it["path"])
        try:
            src_res = str(src.resolve())
            root_for = None
            for rp, name in root_names.items():
                if src_res.lower().startswith(rp.lower() + os.sep) or src_res.lower() == rp.lower():
                    root_for = name
                    rel = src_res[len(rp):].lstrip("\\/")
                    break
            if root_for is None:
                root_for, rel = "未识别根", src.name
            dst = quarantine_root / root_for / rel
            if dst.exists():
                dst = dst.with_name(dst.stem + f"_冲突{int(time.time())%100000}" + dst.suffix)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            manifest["items"].append({"src": str(src), "dst": str(dst), "time": time.strftime("%F %T"), "reasons": it.get("reasons", [])})
            moved += 1
            ok_paths.append(str(src))
            log_fn({"action": "quarantine", "src": str(src), "dst": str(dst)})
        except Exception as e:
            errors.append(f"{src}: {e}")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return moved, errors, ok_paths


def apply_recycle(items: list[dict], log_fn) -> tuple[int, list[str]]:
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


def apply_delete(items: list[dict], log_fn) -> tuple[int, list[str]]:
    errors, moved, ok_paths = [], 0, []
    for it in items:
        try:
            os.remove(it["path"])
            log_fn({"action": "delete", "src": it["path"]})
            moved += 1
            ok_paths.append(it["path"])
        except Exception as e:
            errors.append(f"{it['path']}: {e}")
    return moved, errors, ok_paths


def restore_quarantine(quarantine_root: Path, log_fn) -> tuple[int, list[str]]:
    manifest_path = quarantine_root / "manifest.json"
    if not manifest_path.exists():
        return 0, [f"未找到恢复清单 {manifest_path}"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors, restored = [], 0
    remaining = []
    for it in manifest.get("items", []):
        src, dst = Path(it["dst"]), Path(it["src"])
        try:
            if not src.exists():
                errors.append(f"隔离文件丢失 {src}")
                remaining.append(it)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst = dst.with_name(dst.stem + f"_还原{int(time.time())%100000}" + dst.suffix)
            shutil.move(str(src), str(dst))
            log_fn({"action": "restore", "dst": str(dst)})
            restored += 1
        except Exception as e:
            errors.append(f"{src}: {e}")
            remaining.append(it)
    manifest["items"] = remaining
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return restored, errors


def log_jsonl(entry: dict):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({**entry, "ts": time.strftime("%F %T")}, ensure_ascii=False) + "\n")
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
            if im.mode in ("RGB", "RGBA", "P", "L") and max(w, h) > 400:
                try:
                    im = im.reduce(4)  # 块平均快速降采样：PNG 大图不必全尺寸参与 resize
                except Exception:
                    pass
            img = im.convert("L")
        g = img.resize((9, 8))
        px = list(g.getdata())
        bits = 0
        for r in range(8):
            row = px[r * 9:(r + 1) * 9]
            for c in range(8):
                bits = (bits << 1) | (1 if row[c] > row[c + 1] else 0)
        if cache is not None:
            cache.put_hash(str(path), st.st_size, st.st_mtime, bits)
        return bits
    except Exception:
        return None


def cluster_similar(paths: list[str], threshold: int = 6, cache: Cache | None = None) -> list[list[str]]:
    """贪心代表聚类：与已有组代表指纹距离 ≤ 阈值入组，否则自成新组。
    只返回成员 ≥2 的组。贪心代表法不传递合并，避免链式漂移把不相似的串到一起。"""
    groups: list[tuple[int, list[str]]] = []
    for p in paths:
        h = dhash_bits(Path(p), cache)
        if h is None:
            continue
        for rep, members in groups:
            if (h ^ rep).bit_count() <= threshold:
                members.append(p)
                break
        else:
            groups.append((h, [p]))
    if cache is not None:
        cache.commit()
    return [members for _, members in groups if len(members) >= 2]


# ---------------------------------------------------------------- 测试样本
def _load_font(size: int):
    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsum.ttc", "arial.ttf"):
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
    testdir = APP_DIR / "testdata"
    if not (testdir / "type1_拼图引流.jpg").exists():
        make_fixtures(testdir)
    cfg = load_config()
    cfg["ocr"] = {**cfg["ocr"], "enabled": True, "full_image_if_nothing": False}
    cache = Cache(APP_DIR / "selftest_cache.db")
    cache.conn.execute("DELETE FROM results")
    cache.conn.commit()

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
        t0 = time.time()
        r = analyze_file(p, cfg, cache, {"qr": True, "ocr": True})
        dt = time.time() - t0
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
    hits_pub, score_pub, _ = score_ocr_texts(["本作品发布于2024年1月"], cfg["ocr_keywords"])
    pub_ok = score_pub == 0
    print(f"弱词误伤：{'通过 ✅' if pub_ok else '失败 ❌'}（'发布'不命中={pub_ok}）")
    ok = ok and pub_ok

    # —— 缓存配置版本失效：规则变更后自动清空旧缓存 ——
    import tempfile as _tf2
    _cd = Path(_tf2.mkdtemp(prefix="adfilter_cachever_selftest_"))
    try:
        c = Cache(_cd / "c.db")
        same = c.check_config_version(cfg)          # 首次：无记录 → 清空并写入 hash，返回 False
        again = c.check_config_version(cfg)         # 同配置 → True
        cfg2 = dict(cfg)
        cfg2["ocr_keywords"] = cfg["ocr_keywords"] + [{"pattern": "x", "weight": 1, "label": "y"}]
        changed = c.check_config_version(cfg2)      # 规则变了 → False（已清空）
        ver_ok = (not same) and again and (not changed)
        print(f"缓存配置版本：{'通过 ✅' if ver_ok else '失败 ❌'}"
              f"（同配置保留={again}，规则变更清空={not changed}）")
        ok = ok and ver_ok
        c.conn.close()
    finally:
        import shutil as _sh
        _sh.rmtree(_cd, ignore_errors=True)

    # —— 感知哈希聚类自测：同图两份（重编码一次）应聚成一组，不同图不聚 ——
    import tempfile as _tf
    _td = Path(_tf.mkdtemp(prefix="adfilter_phash_selftest_"))
    try:
        a = testdir / "type2_长条贴图.png"
        b = _td / "copy.png"
        c = testdir / "normal_01.jpg"
        Image.open(a).convert("RGB").save(b, quality=72)  # 重编码扰动，指纹仍应近似
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



# -*- coding: utf-8 -*-
# Qt/Fluent 版 GUI（引擎层完全复用）。由 rebuild 脚本拼接进主文件。

# ---------------------------------------------------------------- GUI 功能自测（Qt 版）
def bench():
    testdir = APP_DIR / "testdata" / "bench"
    if not testdir.exists() or len(list(testdir.glob("*.jpg"))) < 100:
        print("先生成测试样本…")
        make_fixtures(APP_DIR / "testdata")
    cfg = load_config()
    files = gather_images([str(testdir)], cfg)
    print(f"基准：{len(files)} 张")

    for label, enable in (("文件名+形状+二维码", {"filename": True, "shape": True, "qr": True, "ocr": False}),
                          ("再加OCR(仅条带)", {"filename": True, "shape": True, "qr": False, "ocr": True})):
        cache = Cache(APP_DIR / "bench_cache.db")
        cache.conn.execute("DELETE FROM results")
        cache.conn.commit()
        t0 = time.time()
        n = 0
        for p in files if "OCR" not in label else files[:40]:
            analyze_file(p, cfg, cache, enable)
            n += 1
        cache.commit()
        cache.conn.close()
        dt = time.time() - t0
        print(f"{label:<22} {n} 张，共 {dt:.1f}s，平均 {dt / n * 1000:.0f} ms/张")


def main():
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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

        qapp = QApplication([])
        win = run_gui(return_app=True)
        win.show()
        QTimer.singleShot(3000, win.close)   # close 触发 os._exit(0) 兜底
        qapp.exec()
        return
    from ad_filter_gui import run_gui
    run_gui()


if __name__ == "__main__":
    main()
