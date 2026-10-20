# -*- coding: utf-8 -*-
"""精度压力测试：8 类广告正样本 + 6 类干扰负样本，统计召回/误报"""
import sys, tempfile, shutil, random
from pathlib import Path
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:/Users/50653/.zcode/workspace/default/ad-filter-gui")
import ad_filter as af
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import numpy as np

td = Path(tempfile.mkdtemp(prefix="ad_acc_"))
af.CACHE_PATH = td/"c.db"; af.CONFIG_PATH = td/"cfg.json"; af.LOG_PATH = td/"l.jsonl"
af.CONFIG_PATH.write_text('{"ui_theme":"dark","workers":8}', encoding="utf-8")
F = af._load_font
rng = np.random.default_rng(11)

def photo_bg(w=1200, h=800, seed=0):
    r = np.random.default_rng(seed)
    base = np.zeros((h, w, 3), dtype=np.float32)
    for c in range(3):
        base[:, :, c] = np.linspace(30 + c*30, 180 + c*20, h)[:, None]
    base += r.normal(0, 18, (h, w, 3))
    im = Image.fromarray(base.clip(0, 255).astype("uint8"))
    return im.filter(ImageFilter.GaussianBlur(1))

pos = []   # 正样本（应判 ad/suspect）
neg = []   # 负样本（应判 clean）

# ---------- 正样本 ----------
# P1 拼图 + 二维码 + 引流文案（4 张）
for i in range(4):
    im = photo_bg(1200, 800, i)
    d = ImageDraw.Draw(im)
    q = Image.new("RGB", (200, 200), (255,255,255))
    qd = ImageDraw.Draw(q); qd.rectangle([10,10,190,190], outline=(0,0,0), width=6)
    for k in range(0, 200, 16): qd.line([(k,0),(k,200)], fill=(0,0,0), width=4)
    im.paste(q, (960, 580))
    d.text((60, 640), "想看高清版 进我的频道 浏览器搜 t.me/demo", font=F(38), fill=(230,40,40))
    p = td/f"p1_combo_{i}.jpg"; im.save(p, quality=88); pos.append((p, "拼图+QR+文案"))

# P2 长条横幅（形状命中）
for i in range(4):
    im = Image.new("RGB", (1800, 60), (20, 20, 20))
    d = ImageDraw.Draw(im)
    d.text((20, 8), "本资源由 t.me/demo 免费发布 不收费", font=F(34), fill=(255,255,255))
    p = td/f"p2_banner_{i}.png"; im.save(p); pos.append((p, "长条横幅"))

# P3 中央透明水印（提亮 OCR）
for i in range(4):
    im = photo_bg(1200, 900, 100+i)
    ov = Image.new("RGBA", im.size, (0,0,0,0))
    od = ImageDraw.Draw(ov)
    for r in range(4):
        od.text((120, 200 + r*140), "本资源由 t.me/demo 免费发布", font=F(60),
                fill=(255,255,255,45), stroke_width=2, stroke_fill=(255,255,255,150))
    im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    p = td/f"p3_watermark_{i}.jpg"; im.save(p, quality=88); pos.append((p, "中央透明水印"))

# P4 角落小水印 + 链接
for i in range(4):
    im = photo_bg(1000, 1000, 200+i)
    d = ImageDraw.Draw(im)
    d.text((700, 950), "t.me/demo", font=F(26), fill=(255,255,255))
    p = td/f"p4_corner_{i}.jpg"; im.save(p, quality=88); pos.append((p, "角落小水印"))

# P5 深色背景白字（底部字幕式）
for i in range(3):
    im = Image.new("RGB", (1280, 720), (10, 12, 20))
    d = ImageDraw.Draw(im)
    d.text((80, 620), "免费领取 加微信 abc123 扫码关注", font=F(44), fill=(255,255,255))
    p = td/f"p5_dark_{i}.jpg"; im.save(p, quality=88); pos.append((p, "深底白字广告"))

# P6 模糊小字水印
for i in range(3):
    im = photo_bg(900, 700, 300+i)
    d = ImageDraw.Draw(im)
    d.text((80, 60), "粉丝群 投稿 独家首发", font=F(30), fill=(255,255,255))
    im = im.filter(ImageFilter.GaussianBlur(1.2))
    p = td/f"p6_blur_{i}.jpg"; im.save(p, quality=80); pos.append((p, "模糊小字水印"))

# ---------- 负样本 ----------
# N1 普通风景（渐变 + 噪声，无文字）
for i in range(4):
    p = td/f"n1_photo_{i}.jpg"; photo_bg(1400, 900, 400+i).save(p, quality=88); neg.append((p, "普通风景"))

# N2 含正常文字（书页/菜单，无推广词）
for i in range(4):
    im = photo_bg(1000, 800, 500+i)
    d = ImageDraw.Draw(im)
    d.text((80, 120), "今日菜单", font=F(48), fill=(30,30,30))
    for r, txt in enumerate(["宫保鸡丁  38元", "鱼香肉丝  32元", "米饭  2元"]):
        d.text((80, 220 + r*70), txt, font=F(34), fill=(40,40,40))
    p = td/f"n2_menu_{i}.jpg"; im.save(p, quality=88); neg.append((p, "正常文字(菜单)"))

# N3 摄影作品 + 版权水印
for i in range(4):
    im = photo_bg(1200, 800, 600+i)
    d = ImageDraw.Draw(im)
    d.text((900, 760), "© 2024 Studio", font=F(24), fill=(255,255,255))
    p = td/f"n3_copyright_{i}.jpg"; im.save(p, quality=88); neg.append((p, "版权水印"))

# N4 图表截图（含数字）
for i in range(3):
    im = Image.new("RGB", (1000, 700), (250, 250, 248))
    d = ImageDraw.Draw(im)
    d.rectangle([100, 80, 900, 600], outline=(200,200,200))
    for r in range(5):
        d.line([(120, 500 - r*80), (880, 500 - r*80)], fill=(225,225,225))
        d.text((40, 485 - r*80), f"{r*25}", font=F(22), fill=(90,90,90))
    d.text((350, 630), "月度销量统计", font=F(30), fill=(40,40,40))
    p = td/f"n4_chart_{i}.png"; im.save(p); neg.append((p, "图表截图"))

# N5 纯色 / 渐变壁纸
for i in range(4):
    im = Image.new("RGB", (1400, 900), (30+i*40, 60, 90))
    p = td/f"n5_wall_{i}.png"; im.save(p); neg.append((p, "纯色壁纸"))

# N6 手机截图（UI 文字：时间/电量）
for i in range(3):
    im = Image.new("RGB", (900, 1600), (245, 245, 245))
    d = ImageDraw.Draw(im)
    d.text((40, 30), "9:41", font=F(34), fill=(20,20,20))
    d.text((780, 30), "100%", font=F(30), fill=(20,20,20))
    for r in range(6):
        d.rounded_rectangle([40, 200 + r*180, 860, 340 + r*180], 18, fill=(255,255,255))
        d.text((70, 240 + r*180), f"设置项 {r+1}", font=F(32), fill=(40,40,40))
    p = td/f"n6_shot_{i}.png"; im.save(p); neg.append((p, "手机截图"))

cfg = af.load_config()
cfg["ocr"].update({"enabled": True, "max_dim": 1600, "full_image_if_nothing": True})
cache = af.Cache(td/"c2.db"); cache.check_config_version(cfg)
enable = {"filename": True, "shape": True, "qr": True, "ocr": True}

print(f"正样本 {len(pos)} 张 / 负样本 {len(neg)} 张\n", flush=True)
tp = fn = fp = tn = 0
print("=== 正样本（应命中）===", flush=True)
for p, kind in pos:
    r = af.analyze_file(p, cfg, cache, enable)
    hit = r["verdict"] in ("ad", "suspect")
    tp += hit; fn += (not hit)
    mark = "✅" if hit else "❌漏检"
    print(f"{mark} {kind:<16} {p.name:<24} {r['verdict']:<8} {r['score']:.0f} 分", flush=True)
print("\n=== 负样本（应判 clean）===", flush=True)
for p, kind in neg:
    r = af.analyze_file(p, cfg, cache, enable)
    ok = r["verdict"] == "clean"
    tn += ok; fp += (not ok)
    mark = "✅" if ok else "❌误报"
    print(f"{mark} {kind:<16} {p.name:<24} {r['verdict']:<8} {r['score']:.0f} 分  {[h['label'][:20] for h in r['reasons']]}", flush=True)
recall = tp/len(pos)*100
spec = tn/len(neg)*100
print(f"\n召回率 {tp}/{len(pos)} = {recall:.1f}%   误报 {fp}/{len(neg)} = {100-spec:.1f}%   特异度 {spec:.1f}%", flush=True)
cache.conn.close()
shutil.rmtree(td, ignore_errors=True)
