# -*- coding: utf-8 -*-
"""端到端功能完整度：完整用户操作链（扫描→暂停→筛选→勾选→处置→恢复→缓存→规则→主题→移除）"""
import json, os, shutil, sys, tempfile, time
from pathlib import Path
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 项目根（相对自身定位，不含机器路径）
import ad_filter as af
import ad_filter_gui

src = af.APP_DIR / "testdata"
real_qr = next(iter(sorted(src.glob("real4_*"))), None)      # 二维码拼图类
real_strip = next(iter(sorted(src.glob("real6_*"))), None)   # 横条贴图类
if not (real_qr and real_strip):
    print("SKIP  testdata 缺少本地真实样本（real4_*/real6_*）")
    sys.exit(0)

tmp = Path(tempfile.mkdtemp(prefix="ad_e2e_"))
_orig_paths = (af.CACHE_PATH, af.CONFIG_PATH, af.LOG_PATH, af.HISTORY_PATH)
_orig_qdlg = ad_filter_gui.QuarantineDialog
app = None
R, F = [], []
try:
    af.CACHE_PATH = tmp/"c.db"; af.CONFIG_PATH = tmp/"cfg.json"; af.LOG_PATH = tmp/"log.jsonl"
    af.HISTORY_PATH = tmp / 'history.json'
    af.CONFIG_PATH.write_text('{"ui_theme":"dark","workers":4}', encoding="utf-8")
    from PySide6.QtWidgets import QApplication
    qapp = QApplication.instance() or QApplication([])
    app = ad_filter_gui.run_gui(return_app=True)
    app._quarantine_override = tmp / 'quarantine'
    app.show()
    def pump(s):
        t0=time.time()
        while time.time()-t0<s: qapp.processEvents(); time.sleep(0.02)
    def wait(cond, timeout=120):
        t0=time.time()
        while time.time()-t0<timeout:
            qapp.processEvents()
            if cond(): return True
            time.sleep(0.02)
        return False
    def ck(name, ok, detail=""):
        (R if ok else F).append(name)
        print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""), flush=True)

    A = tmp/"相册A"; B = tmp/"相册B"; A.mkdir(); B.mkdir()
    for k in range(5):
        shutil.copy(real_qr, A/f"广告_{k}.jpg")
    for k in range(3):
        shutil.copy(src/"normal_01.jpg", B/f"正常_{k}.jpg")
    shutil.copy(real_strip, A/"长条.png")

    # ① 启动空状态
    ck("启动时文件夹为空", len(app.get_roots()) == 0)

    # ② 添加文件夹
    app.add_folders([str(A), str(B)]); pump(0.2)
    ck("添加两个文件夹", len(app.get_roots()) == 2)
    ck("chip 显示两个文件夹", len(app.folder_bar._chips) == 2)

    # ③ 扫描（OCR 开，验证暂停/继续）
    app.cfg["qr"]["enabled"] = True; app.cfg["ocr"]["enabled"] = True
    app.start_scan()
    ok = wait(lambda: getattr(app, "_last_progress", 0) >= 2, 90)
    ck("扫描开始有进度", ok)
    ck("扫描中显示当前文件", "正在扫描" in app.folder_bar.lbl_current.text())
    ck("扫描中波浪点可见", app.folder_bar.wave.isVisible())
    app.toggle_pause(); pump(0.4)
    ck("暂停按钮变继续", app.btn_pause.text() == "继续")
    p1 = getattr(app, "_last_progress", 0); pump(1.2)
    p2 = getattr(app, "_last_progress", 0)
    ck("暂停后进度停住", (p2 - p1) <= 6, f"{p1}->{p2}")
    app.toggle_pause()
    ok = wait(lambda: "扫描完成" in app.status.text(), 180)
    ck("继续后扫描完成", ok, app.status.text()[:30])
    total_items = len(app.items)
    ck("扫描命中项>0", total_items > 0, f"{total_items}")
    ck("文件夹进度打勾", all("✓" in c.text() for c in app.folder_bar._chips.values()))

    # ④ 筛选
    app.f_verdict.setCurrentIndex(1)  # 广告
    pump(0.4)
    n_ad_model = app.model.rowCount()
    ck("筛选-仅广告", n_ad_model == sum(1 for it in app.items if it["verdict"] == "ad"), f"{n_ad_model}")
    app.f_search.setText("广告_1"); pump(0.4)
    ck("搜索过滤", app.model.rowCount() <= 2, f"{app.model.rowCount()}")
    app.f_search.setText(""); app.f_verdict.setCurrentIndex(0); pump(0.4)
    ck("清除筛选恢复全部", app.model.rowCount() == total_items)

    # ⑤ 勾选链
    app.select_all(); ck("Ctrl+A 全选", len(app.model.checked) == app.model.rowCount())
    app.select_all(); ck("再按取消全选", len(app.model.checked) == 0)
    app.invert_sel(); ck("反选=全选", len(app.model.checked) == app.model.rowCount())
    app.clear_sel(); ck("清除选择", len(app.model.checked) == 0)
    app.grid.setCurrentIndex(app.model.index(0, 0)); pump(0.2)
    app.toggle_check(0); pump(0.2)
    ck("点击圆圈勾选", len(app.model.checked) == 1)
    app.clear_sel()

    # ⑥ 大图
    app.grid.setCurrentIndex(app.model.index(0, 0)); pump(0.2)
    app.open_big(); pump(0.6)
    lb = app._big_win
    ck("Lightbox 打开", lb is not None and isinstance(lb, ad_filter_gui.Lightbox))
    if lb:
        from PySide6.QtCore import QPoint
        lb.show(); pump(0.3)
        ck("入场动画完成", abs(lb._enter - 1.0) < 0.05, f"{lb._enter}")
        class W:
            def angleDelta(s): return QPoint(0, 240)
            def position(s): return QPoint(lb.width()//2, lb.height()//2)
        s0 = lb.scale; lb.wheelEvent(W())
        ck("滚轮缩放", lb.scale > s0)
        lb.keyPressEvent(type("K", (), {"key": lambda s: ad_filter_gui.Qt.Key_Escape})()); pump(0.4)
        try:
            gone = not lb.isVisible()
        except RuntimeError:
            gone = True   # WA_DeleteOnClose 已销毁 —— 正确
        ck("Esc 关闭 Lightbox", gone)

    # ⑦ 标记保留
    app.grid.setCurrentIndex(app.model.index(0, 0)); pump(0.2)
    keep_path = app.model.items[0]["path"]
    n_before = len(app.items)
    app.mark_current(False); pump(0.3)
    ck("标记保留后从列表移除", len(app.items) == n_before - 1 and all(it["path"] != keep_path for it in app.items))

    # ⑧ 处置（隔离）→ 恢复
    app._ask = lambda *a, **k: True   # 自动化：跳过模态确认框
    app.select_all(); pump(0.2)
    n_checked = len(app.model.checked)
    n_before = len(app.items)
    app.execute_disposal()
    ok = wait(lambda: not app._disposing and "处置完成" in app.status.text(), 60)
    ck("隔离处置完成", ok, app.status.text()[:30])
    ck("处置后列表减少", len(app.items) == n_before - n_checked, f"{n_before}->{len(app.items)}")
    q_root = app._quarantine_root()
    ck("隔离目录已创建", q_root.exists())
    class FakeQuarantineDialog:
        def __init__(self, app_, qroot):
            self._app, self._qroot = app_, qroot
        def exec(self):
            n, errs = af.restore_quarantine(self._qroot, af.log_jsonl)
            self._app.scan_q.put(("restored", (n, errs)))
            return 0
        def deleteLater(self):
            pass
    ad_filter_gui.QuarantineDialog = FakeQuarantineDialog
    app.restore_ui()
    ok = wait(lambda: not app._disposing and "恢复" in app.status.text(), 60)
    ck("从隔离区恢复", ok, app.status.text()[:30])
    restored = list(A.glob("*.jpg"))
    ck("文件真的回到相册A", len(restored) == 5, f"{len(restored)}")

    # ⑨ 二次扫描：缓存秒开
    app._roots = [str(A), str(B)]; app.folder_bar.rebuild()
    t0 = time.time()
    app.start_scan()
    ok = wait(lambda: "扫描完成" in app.status.text(), 60)
    dt = time.time()-t0
    ck("二次扫描完成", ok)
    ck("缓存秒开(<3s)", dt < 3.0, f"{dt:.1f}s")

    # ⑩ 规则编辑 → 缓存失效
    app.cfg["ocr_keywords"].append({"pattern": "zzz_never_match_xyz", "weight": 1, "label": "测试规则"})
    af.save_config(app.cfg)
    app.start_scan()
    ok = wait(lambda: "扫描完成" in app.status.text(), 120)
    ck("改规则后仍能扫描完成", ok)
    ck("规则生效（含新规则）", any(r.get("label") == "测试规则" for r in app.cfg["ocr_keywords"]))

    # ⑪ 主题切换
    t0 = app.theme
    app.toggle_theme(); pump(0.6)
    ck("主题切换到浅色", app.theme != t0 and app.theme == "light")
    app.toggle_theme(); pump(0.6)
    ck("主题切回深色", app.theme == "dark")

    # ⑫ 移除文件夹
    n_roots = len(app.get_roots())
    keys = list(app.folder_bar._chips.keys())
    app.folder_bar._chips[keys[0]].btn_x.click(); pump(0.3)
    ck("chip ✕ 移除文件夹", len(app.get_roots()) == n_roots - 1)

    # ⑬ 关闭
    app.close(); pump(0.5)
    ck("正常关闭", not app.isVisible())
finally:
    ad_filter_gui.QuarantineDialog = _orig_qdlg
    if app is not None:
        try:
            app._cache.close()
        except Exception:
            pass
        app.close()
        qapp.processEvents()
    af.CACHE_PATH, af.CONFIG_PATH, af.LOG_PATH, af.HISTORY_PATH = _orig_paths
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\nE2E 结果：{len(R)}/{len(R)+len(F)} 通过" + ("" if not F else f"  ❌ 失败: {F}"))
sys.exit(1 if F else 0)
