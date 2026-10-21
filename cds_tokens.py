# -*- coding: utf-8 -*-
"""CDS（Claude Design System）设计令牌 —— 单一真源。

数值全部来自三个来源的**真实抓取**，不是估的：
  A. claude-storybook.bystanderss.chatgpt.site（无头 Chrome 拿渲染后 DOM + 内联源码）
  B. github.com/siteboon/claudecodeui（前端源码）
  C. 与 open-claude-ui 仓库交叉验证

Qt 落地的三条硬约束（研究者实测建议，已固化在此）：
  1. 毛玻璃全禁——Qt 无原生 backdrop-filter；但子控件 alpha 必须保留（弹窗遮罩靠它），
     故 Composer/Header 这类半透明一律拍平成实色（见 SURFACE_FLAT）。
  2. Composer 双环不用 box-shadow——Qt box-shadow 只认合成层、跨平台不一致；
     改 QFrame + border + 动态属性 + unpolish/polish（见 TOKENS 注释与 cds_widgets）。
  3. 暗色字重补偿放弃——Qt 在非可变字体上会把 460/560 吸附成 Normal/Bold，做了白做。
"""
from PySide6.QtCore import QEasingCurve

def _t(light, dark):
    """构造「浅/深」双取值令牌。"""
    return {"light": light, "dark": dark}


# ============================================================ 品牌色
CLAY = "#D97757"          # --cds-clay，两模式不变
BRAND = "#C6613F"         # --brand：Claude 桌面主品牌，两模式不变
CLAY_LIGHT = "#FBF3EF"    # --cds-clay-light

# hover 分模式（cds-theme.css 原文）：浅色加深、深色调亮
CLAY_HOVER = _t("#CC5A36", "#E08264")
CLAY_ACTIVE = "#A8452A"
DANGER = _t("#EF4444", "#F87171")

# Alpha 四级（CDS 用 alpha 替 border，几乎不用 border）
ALPHA = {"light": (0.05, 0.10, 0.20, 0.35), "dark": (0.05, 0.09, 0.18, 0.30)}
# 发丝/基础/激活/焦点 四档描边
RING = (0.5, 1.0, 1.5, 2.0)

# ============================================================ 主题（深浅两套）
class T:
    """按当前主题取值的令牌集合。用法：T.surface1[dark]（dark 为 bool）"""

    # —— Surface 阶梯（深色全程偏暖：R ≥ G > B，见 1.1b）——
    # —— Surface Ladder（C：cds-theme.css 原文）——
    root_bg     = _t("#F6F4EF", "#121212")   # 应用根背景
    surface0    = _t("#F9F9F7", "#0B0B0B")   # 侧边栏 / 根底
    surface1    = _t("#FCFCFB", "#151515")   # 主聊天画布
    surface2    = _t("#FFFFFF", "#1A1A19")   # 卡片 / 消息容器
    surface3    = _t("#FFFFFF", "#20201F")   # 输入框 / 浮层
    popover     = _t("#FFFFFF", "#2C2C2A")   # 浮层专用
    sidebar     = _t("#F5F5F3", "#181818")   # 侧栏专用（A）
    header      = _t("#FCFCFB", "#1E1E1E")   # 顶栏专用（A）

    # —— 拍平替代（毛玻璃禁用后：bg-α/60~95 按 60% 混不透明底合成）——
    flat_header = _t("#FDFDFC", "#1D1D1D")   # header/60~95 + blur → 实色
    flat_composer = _t("#FFFFFF", "#20201E") # 输入区外壳

    # —— 文本 ——
    text        = _t("#2A2724", "#F5F4F2")   # 主文本（B：HSL 36 25% 4% / 40 8% 93%）
    text_2      = _t("#6B6863", "#A8A29A")   # 次级（灰阶 500 / 暖灰）
    text_3      = _t("#A4A19B", "#6E6B66")   # 三级 / 占位符
    text_on_clay = _t("#FFFFFF", "#FFFFFF")

    # —— 边框 / 分隔线 ——
    border       = _t("#E5E1DA", "#2E2C2A")  # alpha2：1px 基础边框
    border_strong = _t("rgba(0,0,0,0.20)", "rgba(255,255,255,0.18)")  # alpha3：hover / 激活
    border_soft  = _t("#EFEDE8", "#262524")  # alpha1：更弱的分隔
    # 灰阶层级（10 级暖灰，供文字/底/边框取用；避免"只有一个橙"）
    gray = {"10": _t("#FCFCFB", "#151515"), "20": _t("#F5F5F3", "#0B0B0B"),
            "100": _t("#DEDCD6", "#2E2C2A"), "300": _t("#A4A19B", "#6E6B66"),
            "500": _t("#6B6863", "#A8A29A"), "700": _t("#373634", "#E8E6E0"),
            "850": _t("#151515", "#FCFCFB"), "900": _t("#0B0B0A", "#FFFFFF")}
    border_clay = _t("rgba(217,119,87,0.40)", "rgba(217,119,87,0.45)")

    # —— 品牌（动作 / 选中 / 品牌标记专用；判定色不得复用它）——
    clay        = _t("#C6613F", "#E08264")

    # —— 填充 ——
    muted       = _t("#F1EFEA", "#232221")   # muted / accent 底
    hover       = _t("rgba(0,0,0,0.045)", "rgba(255,255,255,0.06)")
    clay_fill   = _t("rgba(217,119,87,0.06)", "rgba(217,119,87,0.10)")  # clay/5~10
    clay_badge  = _t("rgba(217,119,87,0.10)", "rgba(217,119,87,0.14)")  # clay/10

    # —— 选中态 A：中性实底（导航项 / 工具条）——
    sel_neutral_bg   = _t("#F0EEE9", "#2A2A2A")
    sel_neutral_text = _t("#1A1917", "#FFFFFF")

    # —— 语义色 ——
    ok      = _t("#2F9E63", "#4CC38A")   # emerald
    warn    = _t("#D98A2B", "#F0B45F")   # amber
    danger  = _t("#DC2626", "#F87171")   # red
    info    = _t("#2563EB", "#60A5FA")   # blue

    # —— 判定色（六角色体系：橙=品牌动作 / 红=广告 / 琥珀=疑似 / 蓝=异常 / 绿=干净）——
    # 关键：判定色必须与品牌橙拉开色相，否则整屏"只有一个颜色"
    verdict_ad      = _t("#C0392B", "#F0715F")   # 红（去重：不复用 clay）
    verdict_suspect = _t("#B87A2E", "#E0A85C")   # 琥珀
    verdict_error   = _t("#3B6FD4", "#7FA6F0")   # 蓝（程序异常，非内容判定）
    verdict_clean   = _t("#2F9E63", "#4CC38A")   # 绿

    # —— 语义填充 / 边框（徽标药丸、状态条用）——
    fill = {
        "ok":     _t("rgba(47,158,99,0.12)",   "rgba(76,195,138,0.14)"),
        "warn":   _t("rgba(217,138,43,0.13)",  "rgba(240,180,95,0.15)"),
        "danger": _t("rgba(192,57,43,0.12)",   "rgba(240,113,95,0.15)"),
        "info":   _t("rgba(59,111,212,0.12)",  "rgba(127,166,240,0.15)"),
        "clay":   _t("rgba(217,119,87,0.10)",  "rgba(217,119,87,0.14)"),
        "gray":   _t("rgba(107,104,99,0.10)",  "rgba(168,162,154,0.13)"),
    }
    edge = {
        "ok":     _t("rgba(47,158,99,0.34)",   "rgba(76,195,138,0.38)"),
        "warn":   _t("rgba(184,122,46,0.36)",  "rgba(224,168,92,0.40)"),
        "danger": _t("rgba(192,57,43,0.34)",   "rgba(240,113,95,0.38)"),
        "info":   _t("rgba(59,111,212,0.34)",  "rgba(127,166,240,0.38)"),
        "clay":   _t("rgba(217,119,87,0.42)",  "rgba(217,119,87,0.45)"),
        "gray":   _t("rgba(107,104,99,0.26)",  "rgba(168,162,154,0.28)"),
    }

    # —— 遮罩（子控件 alpha 保留的场合）——
    scrim          = _t("rgba(20,18,16,0.42)", "rgba(0,0,0,0.58)")
    scrim_soft     = _t("rgba(20,18,16,0.18)", "rgba(0,0,0,0.30)")


def c(dark: bool, token: dict) -> str:
    """取当前主题下的色值。"""
    return token["dark" if dark else "light"]


# —— 语义角色 ——
ROLE_BY_VERDICT = {"ad": "danger", "suspect": "warn", "error": "info", "clean": "ok"}
_ROLE_FG = {"ok": T.ok, "warn": T.warn, "danger": T.danger,
            "info": T.info, "clay": T.clay, "gray": T.text_2}


def sem(dark: bool, role):
    """语义角色三件套：(主色, 填充, 边框)。role ∈ ok/warn/danger/info/clay/gray 或 ad/suspect/error/clean"""
    key = ROLE_BY_VERDICT.get(role, role)
    if key not in _ROLE_FG:
        key = "gray"
    return c(dark, _ROLE_FG[key]), c(dark, T.fill[key]), c(dark, T.edge[key])


def pill_qss(dark: bool, role: str, radius: int = 999, pad: str = "3px 8px") -> str:
    """语义色药丸（徽标/状态条），返回可直接拼进 setStyleSheet 的片段。"""
    fg, fill, edge = sem(dark, role)
    return (f"background: {fill}; border: 1px solid {edge}; "
            f"border-radius: {radius}px; padding: {pad}; color: {fg};")


# ============================================================ 圆角（C：tokens.json 原文）
R_XS   = 2    # badge / dot
R_SM   = 4    # checkbox / kbd
R_MD   = 6    # chip / icon button
R_LG   = 8    # button / panel
R_PANEL = 10  # 折叠面板 / 工具条外壳
R_CARD = 12   # 卡片
R_XL   = 16   # 模态框 / 大容器
R_2XL  = 24
R_COMPOSER = 20   # Composer 外壳（Claude 桌面标志）
R_FULL = 999      # pill / 分段 / 滚动条
R_CONCENTRIC = 12  # 同心公式：容器 12 时子元素 4（差值里的最小值）

# ============================================================ 高度（A：scale）
H_XS, H_SM, H_MD, H_LG, H_TITLEBAR = 24, 28, 32, 40, 48
H_SIDEBAR_ITEM = 32
H_TABLE_ROW = 34
H_BOTTOM_BAR = 62

# ============================================================ 间距
SP_1, SP_2, SP_3, SP_4, SP_5, SP_6 = 4, 6, 8, 12, 16, 24

# ============================================================ 字号 / 行高（C：Typography）
# token 原始（rem）：body 13/18? — 按 tokens.json：body .8125/1.375rem，prose .875/1.25rem
# 生产实测（D）：正文 14px/20px；故本表以实测为准，13px 仅用于侧栏密集区
FS_MICRO, FS_CAPTION, FS_BODY, FS_PROSE, FS_SUBTITLE, FS_TITLE = 10, 11, 13, 14, 16, 18
LH = {"body": 20, "caption": 16, "prose": 20, "title": 24}

# ============================================================ 动效（A：token + B：默认）
DUR_FAST, DUR_SNAP, DUR_BASE, DUR_SHEET, DUR_SLOW = 60, 120, 200, 300, 450
DUR_HOVER, DUR_ACTIVE = 100, 50          # B
DUR_STANDARD = 150                        # B：默认过渡

# Qt 没有任意 cubic-bezier，取最接近的预置曲线（近似关系已核对）
EASE_OUT = QEasingCurve.OutCubic          # ≈ cubic-bezier(.165,.84,.44,1)
EASE_SNAP = QEasingCurve.OutQuint         # ≈ cubic-bezier(.32,.72,0,1)
EASE_STANDARD = QEasingCurve.InOutQuad    # ≈ cubic-bezier(.4,0,.2,1)
EASE_OVERSHOOT = QEasingCurve.OutBack     # ≈ cubic-bezier(.34,1.3,.64,1)
EASE_INOUT = QEasingCurve.InOutCubic

# 九条命名曲线（C：curve 表），第一列为 Qt 近似（Qt 无任意 cubic-bezier）
# 名称            原值                              Qt 近似
CURVES = {
    "swift":    ("cubic-bezier(0.22, 0.61, 0.36, 1)", QEasingCurve.OutCubic),
    "expand":   ("cubic-bezier(0.4, 0, 0.2, 1)",      QEasingCurve.InOutQuad),
    "enter":    ("cubic-bezier(0.16, 1, 0.3, 1)",     QEasingCurve.OutQuart),
    "out":      ("cubic-bezier(0.33, 1, 0.68, 1)",    QEasingCurve.OutCubic),
    "snap":     ("cubic-bezier(0.32, 0.72, 0, 1)",    QEasingCurve.OutQuint),
    "overshoot":("cubic-bezier(0.34, 1.3, 0.64, 1)",  QEasingCurve.OutBack),
    "pop":      ("cubic-bezier(0.16, 1.4, 0.3, 1)",   QEasingCurve.OutBack),
    "pulse":    ("ease-in-out",                       QEasingCurve.InOutQuad),
    "rise":     ("cubic-bezier(0.16, 1, 0.3, 1)",     QEasingCurve.OutQuart),
    "fall":     ("cubic-bezier(0.4, 0, 1, 1)",        QEasingCurve.InQuad),
}
SWIFT, EXPAND, ENTER, OUT, SNAP = (CURVES[k][1] for k in
                                   ("swift", "expand", "enter", "out", "snap"))
OVERSHOOT, POP, PULSE, RISE, FALL = (CURVES[k][1] for k in
                                     ("overshoot", "pop", "pulse", "rise", "fall"))

# 骨架扫光（C 原文）：2s linear，105deg，transparent 35% → sheen 50% → transparent 65%
ANIM_SKELETON = 2000
SHEEN_DEG = 105
SHEEN_STOPS = (0.35, 0.50, 0.65)

# 滚动渐隐（D 原文）：28px、7 档 alpha
FADE_H = 28
FADE_STOPS = (0x00, 0x0d, 0x26, 0x4d, 0x80, 0xb3, 0xd9)   # 0 → 100%

# 焦点环三层（C 原文）
FOCUS_RING = "inset 0 0 0 1px {surface1}; 0 0 0 1.5px {clay}; 0 0 6px 1px {clay}25"
# 拖放辉光（C 原文）
DROP_GLOW = "inset 0 0 24px -2px {clay}20; inset 0 0 0 2px {clay}"

# 状态点（D）：6×6；running 1.2s blink(.3↔1)；clay 呼吸 3s
DOT_SIZE = 6
ANIM_BLINK = 1200
ANIM_BREATHE = 3000

# 弹簧（C/D 校正）：超调由阻尼比决定，不是"要不要弹"
# 名称        stiffness damping mass   ζ      超调
SPRINGS = {
    "button":  (600, 30, 1.0, 0.612, 0.088),   # +8.8%
    "card":    (320, 28, 1.0, 0.783, 0.019),   # +1.9%
    "snap":    (1200, 80, 0.1, 1.0, 0.0),      # 吸附，不弹
    "sheet":   (420, 28, 1.0, 0.683, 0.050),   # 浮层
    "handle":  (260, 16, 1.0, 0.497, 0.170),   # 拖动手柄
    "stagger": (300, 35, 1.0, 1.010, 0.0),
}
# 保留旧名兼容（唯一权威定义；下面那份 3 元组重复已删除，避免两处真值）
SPRING_BUTTON, SPRING_CARD, SPRING_DRAG = SPRINGS["button"], SPRINGS["card"], SPRINGS["snap"]

# 关键帧时长（A 原值）
ANIM_DOTS = 1900          # 安静三点：1.9s infinite，延迟 0/.15/.3s
ANIM_DOTS_STEP = 150
ANIM_BLOCK_ENTER = 450    # 块入场 blur(4px)+translateY(5px)+scale(.9)
ANIM_MSG_UP = 1100        # 上浮 1.1s overshoot
ANIM_GRAPHEME = 90        # 逐字消解 blur(6px)→0，每字 90ms，步进 30ms
ANIM_GRAPHEME_STEP = 30
ANIM_THUMB_GROW = 250     # 缩略图挤入 0.25s
ANIM_CLICK_RING = 420     # 点击涟漪
ANIM_CHECK_DECAY = 300    # 对勾淡隐（延迟 2500ms）
ANIM_SEGMENT = 150        # 分段滑块

# 弹簧预设（A Spring Lab：stiffness/damping/mass）
# （原 3 元组重复定义已删；权威值见上方 SPRINGS 表）
# SPRING_BOUNCY = (380, 18, 1) —— 注意：这是库作者的自建档，经 cds-theme.css 原文
# 与 Spring Lab 交叉验证**不是 Claude 官方真值**（真值是 {duration:.32, bounce:.15}），
# 因此不收录，避免把错值混进设计。

# ============================================================ 反馈类（B）
TOAST_MS = 3000
TOAST_MARGIN = 16
TOOLTIP_DELAY_MS = 350
TOOLTIP_OFFSET = 8

# ============================================================ 骨架尺寸（B + 本项目）
SIDEBAR_W = 256           # A：w-64
SIDEBAR_MIN, SIDEBAR_MAX, SIDEBAR_STEP = 220, 640, 16   # B
DETAIL_W = 360
DETAIL_MIN, DETAIL_MAX = 300, 640
CARD_W, CARD_H = 196, 234
CARD_GAP = 8

# ============================================================ z 序（B）
Z_TOAST, Z_MENU, Z_MODAL = 9999, 9999, 9999
Z_BACKDROP = 9998


# ============================================================================
# 追加：Qt 缺失的三项能力（任意 cubic-bezier / 结构化阴影 / 弹簧求解器）
# 来源：open-claude-ui `code-examples/cds-theme.css` + `ClaudeMotionPresets.ts` 原文。
# 只做追加，不修改上方任何既有名字。
# ============================================================================

# ----------------------------------------------------------------------------
# 1) 任意 cubic-bezier —— Qt 只有 40 余种预置曲线，没有任意三次贝塞尔，
#    这正是"动效像不像 CDS"的关键。用 Newton-Raphson + 二分兜底求解。
#    注意：下方 BEZIER_CDS 与上方 CURVES 数值有出入，两者不可混用：
#      CURVES     = storybook 采样值（近似，已配好 Qt 预置曲线）
#      BEZIER_CDS = cds-theme.css 原文值（精确，走本节的自定义曲线）
#    追求"高保真抄 CDS"时用本节；追求"不引入自定义曲线"时用上方。
# ----------------------------------------------------------------------------
BEZIER_CDS = {
    # 名称          (x1,  y1,   x2,   y2)      cds-theme.css 原文 / 用途
    "swift":     (0.16, 1.0,  0.30, 1.0),   # 幽灵按钮、卡片悬停抬升
    "expand":    (0.19, 1.0,  0.22, 1.0),   # Grid 0fr→1fr 展开防抖
    "enter":     (0.22, 1.0,  0.36, 1.0),   # 消息入场消融
    "out":       (0.165, 0.84, 0.44, 1.0),  # 标准平滑出场
    "snap":      (0.32, 0.72, 0.0,  1.0),   # 磁吸快速对齐
    "overshoot": (0.34, 1.30, 0.64, 1.0),   # 果冻过冲 +30%
    "pop":       (0.34, 1.56, 0.64, 1.0),   # 弹窗强回弹 +56%
    "pulse":     (0.40, 0.0,  0.20, 1.0),   # 思考三点呼吸
    "rise":      (0.30, 0.70, 0.40, 1.0),   # 状态行抬升
    "fall":      (0.0,  0.0,  0.20, 1.0),   # 状态行下沉
    # 文本扫光 / Epitaxy 专用（非对称）
    "shimmer":   (0.714, 0.121, 0.211, 0.888),
    "shimmer_strong": (0.40, 0.15, 0.60, 0.85),
    "epitaxy":   (0.45, 0.0, 0.55, 1.0),
}


def cubic_bezier(x1, y1, x2, y2):
    """返回 f(t) -> progress（t ∈ [0,1]）的缓动函数。

    先 Newton-Raphson 解出参数 u 使 Bx(u) = t，再求 By(u)；
    导数退化时回退二分法，保证收敛（8 次 NR + 32 次二分，误差 < 1e-7）。

    用法（喂给 Qt）：
        curve = QEasingCurve()
        curve.setCustomType(cubic_bezier(*BEZIER_CDS["swift"]))
    """
    def _bx(u):
        m = 1 - u
        return 3 * m * m * u * x1 + 3 * m * u * u * x2 + u ** 3

    def _by(u):
        m = 1 - u
        return 3 * m * m * u * y1 + 3 * m * u * u * y2 + u ** 3

    def _dbx(u):
        m = 1 - u
        return 3 * m * m * x1 + 6 * m * u * (x2 - x1) + 3 * u * u * (1 - x2)

    def ease(t):
        if t <= 0.0:
            return 0.0
        if t >= 1.0:
            return 1.0
        u = t
        for _ in range(8):
            err = _bx(u) - t
            if abs(err) < 1e-7:
                return _by(u)
            d = _dbx(u)
            if abs(d) < 1e-9:
                break
            u -= err / d
        lo, hi = 0.0, 1.0
        u = t
        for _ in range(32):
            v = _bx(u)
            if abs(v - t) < 1e-7:
                break
            if v < t:
                lo = u
            else:
                hi = u
            u = (lo + hi) / 2.0
        return _by(u)

    return ease


def qt_easing(name: str) -> QEasingCurve:
    """按 BEZIER_CDS 的名字构造一个自定义 QEasingCurve。

        anim.setEasingCurve(qt_easing("overshoot"))

    未知名字回退到 "out"。
    """
    curve = QEasingCurve()
    curve.setCustomType(cubic_bezier(*BEZIER_CDS.get(name, BEZIER_CDS["out"])))
    return curve


# ----------------------------------------------------------------------------
# 2) 结构化阴影 —— Qt 无 box-shadow，只能靠 QGraphicsDropShadowEffect（外投影）
#    或自绘（内阴影）。这里把 CDS 的多层复合阴影拆成层，
#    外投影层可直连 effect，内阴影层必须自绘。
# ----------------------------------------------------------------------------
class ShadowLayer:
    """单层投影。inset=True 为内阴影（Qt 无原生等效，必须 paintEvent 自绘）。"""

    __slots__ = ("ox", "oy", "blur", "spread", "color", "inset")

    def __init__(self, ox=0.0, oy=0.0, blur=0.0, spread=0.0,
                 color="#00000000", inset=False):
        self.ox, self.oy = ox, oy
        self.blur, self.spread = blur, spread
        self.color, self.inset = color, inset

    def to_effect_params(self):
        """→ (offset_x, offset_y, blur_radius, color)，可直接构造
        QGraphicsDropShadowEffect。Qt 无 spread，按 blur + spread 近似。"""
        return (self.ox, self.oy, self.blur + self.spread, self.color)

    def css(self):
        inset = "inset " if self.inset else ""
        sp = f" {self.spread:g}px" if self.spread else ""
        return f"{inset}{self.ox:g}px {self.oy:g}px {self.blur:g}px{sp} {self.color}"

    def __repr__(self):
        return f"ShadowLayer({self.css()})"


class ShadowSpec:
    """多层复合阴影。CDS 全部阴影都是 2~4 层，绝无单层死黑。"""

    __slots__ = ("layers",)

    def __init__(self, *layers):
        self.layers = tuple(layers)

    @property
    def outer(self):
        """外投影层 → 可映射到 QGraphicsDropShadowEffect"""
        return tuple(l for l in self.layers if not l.inset)

    @property
    def inset(self):
        """内阴影层 → 必须自绘"""
        return tuple(l for l in self.layers if l.inset)

    def to_effect_params(self):
        """主外投影参数（取模糊最大的一层）。

        Qt 一个 widget 只能挂一个 QGraphicsDropShadowEffect，多层必须自绘；
        这里给出视觉上主导的那层，够用。需要多层请自绘 paintEvent。
        """
        if not self.outer:
            return (0.0, 0.0, 0.0, "#00000000")
        return max(self.outer, key=lambda l: l.blur + l.spread).to_effect_params()

    def to_effects(self):
        """所有外投影层的参数列表（自绘多层时用）。"""
        return [l.to_effect_params() for l in self.outer]

    def max_extent(self):
        """阴影向外占用的最大像素 —— 给容器预留透明 padding 用，别让阴影被裁掉。"""
        ext = 0.0
        for l in self.outer:
            ext = max(ext, abs(l.ox) + abs(l.oy) + (l.blur + l.spread) / 2.0)
        return ext

    def to_effect(self):
        """直接构造一个 QGraphicsDropShadowEffect（内部延迟导入 QtWidgets）。"""
        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        from PySide6.QtGui import QColor
        ox, oy, blur, color = self.to_effect_params()
        eff = QGraphicsDropShadowEffect()
        eff.setOffset(ox, oy)
        eff.setBlurRadius(blur)
        eff.setColor(QColor(color))
        return eff

    def css(self):
        return ", ".join(l.css() for l in self.layers)

    def __repr__(self):
        return f"ShadowSpec({self.css()})"


def _sh(*layers):
    return ShadowSpec(*layers)


# —— 卡片：常态 → hover 抬升（CDS 原文 --cds-panel-shadow / -hover）——
# 浅色：0 0 0 1px border + sm 双层；hover 时边框加深 + 4px 16px -2px
SHADOW_CARD = _t(
    _sh(ShadowLayer(0, 0, 0, spread=1, color="rgba(20,18,16,0.10)"),
        ShadowLayer(0, 1, 2, color="rgba(0,0,0,0.05)"),
        ShadowLayer(0, 2, 8, color="rgba(0,0,0,0.08)")),
    _sh(ShadowLayer(0, 0, 0, spread=1, color="rgba(255,255,255,0.09)"),
        ShadowLayer(0, 1, 2, color="rgba(0,0,0,0.40)"),
        ShadowLayer(0, 2, 8, color="rgba(0,0,0,0.40)")),
)
SHADOW_CARD_HOVER = _t(
    _sh(ShadowLayer(0, 0, 0, spread=1, color="rgba(20,18,16,0.20)"),
        ShadowLayer(0, 4, 16, spread=-2, color="rgba(0,0,0,0.09)")),
    _sh(ShadowLayer(0, 0, 0, spread=1, color="rgba(255,255,255,0.18)"),
        ShadowLayer(0, 6, 20, color="rgba(0,0,0,0.60)")),
)
#: 卡片 hover 的位移量（CDS 原文 translateY(-1.5px)），配 SHADOW_CARD_HOVER 使用
CARD_HOVER_LIFT = -1.5

# —— Popover 两层：浅色=外框 1px + 双层漫反射；深色=外框归 0 + 内倒角高光 ——
# 深色那层 inset 1px rgba(255,255,255,.10) 是"顶光倒角"，必须自绘，
# to_effect_params() 只会返回外层那道重投影。
SHADOW_POPOVER = _t(
    _sh(ShadowLayer(0, 0, 0, spread=1, color="rgba(20,18,16,0.10)"),
        ShadowLayer(0, 8, 24, color="rgba(0,0,0,0.12)"),
        ShadowLayer(0, 2, 6, color="rgba(0,0,0,0.08)")),
    _sh(ShadowLayer(0, 0, 0, spread=1, color="rgba(255,255,255,0.10)", inset=True),
        ShadowLayer(0, 8, 28, color="rgba(0,0,0,0.65)"),
        ShadowLayer(0, 2, 8, color="rgba(0,0,0,0.45)")),
)

# —— Composer 三态双环（CDS 原文，常态/hover/focus）——
SHADOW_COMPOSER = _t(
    _sh(ShadowLayer(0, 4, 20, color="rgba(0,0,0,0.035)"),
        ShadowLayer(0, 0, 0, spread=1, color="rgba(20,18,16,0.10)", inset=True)),
    _sh(ShadowLayer(0, 4, 20, color="rgba(0,0,0,0.40)"),
        ShadowLayer(0, 0, 0, spread=1, color="rgba(255,255,255,0.09)", inset=True)),
)
SHADOW_COMPOSER_HOVER = _t(
    _sh(ShadowLayer(0, 4, 24, color="rgba(0,0,0,0.06)"),
        ShadowLayer(0, 0, 0, spread=1, color="rgba(20,18,16,0.20)", inset=True)),
    _sh(ShadowLayer(0, 4, 24, color="rgba(0,0,0,0.55)"),
        ShadowLayer(0, 0, 0, spread=1, color="rgba(255,255,255,0.18)", inset=True)),
)
SHADOW_COMPOSER_FOCUS = _t(
    _sh(ShadowLayer(0, 4, 24, color="rgba(0,0,0,0.08)"),
        ShadowLayer(0, 0, 0, spread=1, color="rgba(20,18,16,0.20)", inset=True),
        ShadowLayer(0, 0, 0, spread=1, color="rgba(20,18,16,0.20)")),
    _sh(ShadowLayer(0, 4, 24, color="rgba(0,0,0,0.65)"),
        ShadowLayer(0, 0, 0, spread=1, color="rgba(255,255,255,0.22)", inset=True),
        ShadowLayer(0, 0, 0, spread=1, color="rgba(255,255,255,0.22)")),
)

# —— 焦点环三层 / 拖放辉光（结构化版，与上方 FOCUS_RING/DROP_GLOW 字符串等价）——
SHADOW_FOCUS = _t(
    _sh(ShadowLayer(0, 0, 0, spread=1, color="#FCFCFB", inset=True),
        ShadowLayer(0, 0, 0, spread=1.5, color=CLAY),
        ShadowLayer(0, 0, 6, spread=1, color="rgba(217,119,87,0.25)")),
    _sh(ShadowLayer(0, 0, 0, spread=1, color="#151515", inset=True),
        ShadowLayer(0, 0, 0, spread=1.5, color=CLAY),
        ShadowLayer(0, 0, 8, spread=2, color="rgba(217,119,87,0.35)")),
)
SHADOW_DROP_GLOW = _t(
    _sh(ShadowLayer(0, 0, 24, spread=-2, color="rgba(217,119,87,0.20)", inset=True),
        ShadowLayer(0, 0, 0, spread=2, color=CLAY, inset=True)),
    _sh(ShadowLayer(0, 0, 24, spread=-2, color="rgba(217,119,87,0.30)", inset=True),
        ShadowLayer(0, 0, 0, spread=2, color=CLAY, inset=True)),
)


def shadow(dark: bool, spec: dict) -> ShadowSpec:
    """取当前主题下的阴影规格。用法同 c()。"""
    return spec["dark" if dark else "light"]


# ----------------------------------------------------------------------------
# 3) 二阶阻尼谐振子求解器 —— 弹簧动画用
#     m·x'' + c·x' + k·(x − x_target) = 0,  ζ = c / (2√(m·k))
#     移植自 ClaudeMotionPresets.ts `calcDampedHarmonicOscillator`
# ----------------------------------------------------------------------------
def damped_oscillator(k, c, m, t):
    """归一化位移（0 → 1），t 为秒。

      ζ < 1   欠阻尼：有超调振荡（CDS 主要区间，如 button ζ=.612 超调 +8.8%）
      ζ ≈ 1   临界阻尼：最快无振荡收敛
      ζ > 1   过阻尼：无超调平滑慢收敛（如 snap: k=1200 c=80 m=.1 → ζ=3.65）

    返回 0~1 的进度，超调时 > 1（例：0.22s 处约 1.04）。
    """
    import math
    w0 = math.sqrt(k / m)
    zeta = c / (2.0 * math.sqrt(m * k))

    if zeta < 1:                                   # 欠阻尼
        wd = w0 * math.sqrt(1 - zeta * zeta)
        return (1 - math.exp(-zeta * w0 * t)
                * (math.cos(wd * t)
                   + (zeta / math.sqrt(1 - zeta * zeta)) * math.sin(wd * t)))
    if abs(zeta - 1) < 1e-4:                       # 临界阻尼
        return 1 - math.exp(-w0 * t) * (1 + w0 * t)
    root = math.sqrt(zeta * zeta - 1)              # 过阻尼
    s1 = -w0 * (zeta - root)
    s2 = -w0 * (zeta + root)
    return 1 - (s2 * math.exp(s1 * t) - s1 * math.exp(s2 * t)) / (s2 - s1)


def spring_frames(k, c, m, duration_ms, steps=60):
    """把弹簧采样成 QVariantAnimation 的 keyValue 列表。

    返回 [(progress 0~1, value 0~1+overshoot), ...]，直接喂 setKeyValues。
    """
    total = duration_ms / 1000.0
    return [(i / steps, damped_oscillator(k, c, m, total * i / steps))
            for i in range(steps + 1)]


#: 客户端有明确调用点的弹簧参数（k, c, m, ζ, 超调, 收敛秒）
#: 注：380/18 是 open-claude-ui 作者自建补足档，客户端搜不到，**不是 Claude 观测值**，
#:     故不收录。需要"明显两周期跳跃感"时请改用 {duration:.32, bounce:.15}（≈244/26.6）。
SPRING_PRESETS_QT = {
    # 名称        k     c    m     ζ      超调    收敛    客户端调用点
    "button":   (600,  30,  1.0, 0.612, 0.088, 0.22),  # 按钮/开关/图标按压
    "card":     (320,  28,  1.0, 0.783, 0.019, 0.35),  # 卡片展开/Popover/拖拽回弹
    "snap":     (1200, 80,  0.1, 3.652, 0.000, 0.14),  # 侧栏吸附/面板宽度，零过冲
    "overlay":  (420,  28,  1.0, 0.683, 0.053, 0.30),  # 浮层 scale .25→1
    "handle":   (260,  16,  1.0, 0.496, 0.166, 0.55),  # 拖动手柄 rotate
    "stagger":  (300,  35,  1.0, 1.010, 0.000, 0.40),  # 列表逐项入场
    "bubble":   (450,  24,  1.0, 0.728, 0.040, 0.28),  # 用户气泡 scale .94→1
    "modal":    (400,  32,  1.0, 0.800, 0.015, 0.32),  # 模态 scale .96→1, y 10→0
}

#: 按钮按压的 15 阶纯 CSS linear() 弹簧（--cds-btn-spring，cds-theme.css 原文）
#: 等距采样在 0~1 时间轴上（14 段），40% 处冲到 1.0915（+9.15% 过冲）后收敛。
BUTTON_SPRING_SAMPLES = (
    0.0, 0.2459, 0.6526, 0.9468, 1.0764, 1.0915, 1.0585, 1.0219,
    0.9993, 0.9914, 0.9921, 0.9957, 0.9988, 1.0004, 1.0,
)
BUTTON_PRESS_SCALE = 0.975   # --cds-btn-press-sx / sy：按下 scale(.975)


# ----------------------------------------------------------------------------
# 4) Windows 字体回退栈 —— 不要 PingFang SC / SF Mono（Windows 上没有）
# ----------------------------------------------------------------------------
FONT_SANS = (
    '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", system-ui, '
    '-apple-system, "Helvetica Neue", Arial, sans-serif'
)
FONT_SERIF = (
    '"Source Han Serif SC", "Noto Serif CJK SC", Georgia, '
    '"Times New Roman", "Microsoft YaHei UI", serif'
)
FONT_MONO = (
    '"Cascadia Mono", "JetBrains Mono", Consolas, "SF Mono", '
    'Menlo, ui-monospace, monospace'
)
#: 等宽数字专用 —— 计时器 / token 计数 / 行号不加这个会整行抖动
FONT_NUMERIC = '"Cascadia Mono", "JetBrains Mono", Consolas, monospace'
