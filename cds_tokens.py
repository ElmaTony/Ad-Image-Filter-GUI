# -*- coding: utf-8 -*-
"""统一多主题与视觉令牌引擎（单一真源）。

内置 4 套精选主题（默认：宣纸暖陶 2.0 `atelier`），每套均含浅/深双外观与完整语义色阶，
并定义三档卡片网格密度（默认：紧凑 `compact`）。
"""
from PySide6.QtCore import QEasingCurve


def _t(light, dark):
    """构造「浅/深」双取值令牌，通过 c(dark, token) 取值。"""
    return {"light": light, "dark": dark}


def _rgba(rgb, alpha):
    r, g, b = rgb
    return f"rgba({r},{g},{b},{alpha})"


# ============================================================ 4 套内置主题色盘
PALETTE_ORDER = ("atelier", "slate", "sage", "obsidian")

PALETTES = {
    "atelier": {
        "name": "宣纸暖陶 2.0",
        "desc": "温和暖纸底 + 收敛陶土橙，层次清爽不发黄",
        "accent_rgb": {"light": (194, 89, 55), "dark": (224, 122, 87)},
        "brand_hex": "#C25937",
        "clay_hex": "#D97757",
        "clay_hover": _t("#A94A2B", "#EB8F70"),
        "root_bg": _t("#F6F4F0", "#161514"),
        "surface0": _t("#FDFCFA", "#1D1C1A"),
        "surface1": _t("#F6F4F0", "#161514"),
        "surface2": _t("#FFFFFF", "#242220"),
        "surface3": _t("#F9F7F3", "#2B2926"),
        "text": _t("#26231F", "#F2EFE9"),
        "text_2": _t("#6A645B", "#A8A298"),
        "text_3": _t("#8C857B", "#857F76"),
        "text_on_clay": _t("#FFFFFF", "#FFFFFF"),
        "border": _t("#E6E1D9", "#33302C"),
        "border_strong": _t("#CEC5B8", "#4B4640"),
        "border_soft": _t("#EFECE6", "#272522"),
        "clay": _t("#C25937", "#E07A57"),
        "muted": _t("#EFECE6", "#272522"),
        "hover": _t("rgba(38,35,31,0.048)", "rgba(255,255,255,0.06)"),
        "seg_track": _t("rgba(38,35,31,0.08)", "rgba(255,255,255,0.08)"),
        "seg_pill": _t("#FFFFFF", "#2E2B28"),
        "ok": _t("#2F855A", "#4CC38A"),
        "warn": _t("#B7791F", "#EAB354"),
        "danger": _t("#C8322B", "#F2746B"),
        "info": _t("#2B6CB0", "#7BA7F0"),
    },
    "slate": {
        "name": "石墨画廊",
        "desc": "冷中性灰阶 + 克制钴蓝，零色偏专业看图",
        "accent_rgb": {"light": (37, 99, 235), "dark": (59, 130, 246)},
        "brand_hex": "#2563EB",
        "clay_hex": "#3B82F6",
        "clay_hover": _t("#1D4ED8", "#60A5FA"),
        "root_bg": _t("#F3F4F6", "#111317"),
        "surface0": _t("#FFFFFF", "#171A20"),
        "surface1": _t("#F3F4F6", "#111317"),
        "surface2": _t("#FFFFFF", "#1C2028"),
        "surface3": _t("#F8FAFC", "#222731"),
        "text": _t("#111827", "#F3F4F6"),
        "text_2": _t("#4B5563", "#9CA3AF"),
        "text_3": _t("#6B7280", "#7B8494"),
        "text_on_clay": _t("#FFFFFF", "#FFFFFF"),
        "border": _t("#E5E7EB", "#2A303C"),
        "border_strong": _t("#CBD5E1", "#3F4758"),
        "border_soft": _t("#EEF0F3", "#20252E"),
        "clay": _t("#2563EB", "#3B82F6"),
        "muted": _t("#ECEEF2", "#222731"),
        "hover": _t("rgba(15,23,42,0.048)", "rgba(255,255,255,0.06)"),
        "seg_track": _t("rgba(15,23,42,0.07)", "rgba(255,255,255,0.08)"),
        "seg_pill": _t("#FFFFFF", "#282E3A"),
        "ok": _t("#059669", "#34D399"),
        "warn": _t("#D97706", "#FBBF24"),
        "danger": _t("#DC2626", "#F87171"),
        "info": _t("#7C3AED", "#A78BFA"),
    },
    "sage": {
        "name": "翠影青瓷",
        "desc": "雾灰白 / 深松烟 + 鼠尾草青绿，低疲劳护眼",
        "accent_rgb": {"light": (15, 118, 110), "dark": (45, 212, 191)},
        "brand_hex": "#0F766E",
        "clay_hex": "#14B8A6",
        "clay_hover": _t("#115E59", "#5EEAD4"),
        "root_bg": _t("#F2F5F3", "#101513"),
        "surface0": _t("#FBFCFB", "#161D1A"),
        "surface1": _t("#F2F5F3", "#101513"),
        "surface2": _t("#FFFFFF", "#1C2521"),
        "surface3": _t("#F5F8F6", "#222D28"),
        "text": _t("#18241E", "#ECF3EF"),
        "text_2": _t("#52655B", "#98ACA2"),
        "text_3": _t("#75887E", "#758A80"),
        "text_on_clay": _t("#FFFFFF", "#092421"),
        "border": _t("#DCE5DF", "#293731"),
        "border_strong": _t("#BFCFC5", "#3C4F47"),
        "border_soft": _t("#E9F0EC", "#1E2924"),
        "clay": _t("#0F766E", "#2DD4BF"),
        "muted": _t("#E8EFEA", "#202A25"),
        "hover": _t("rgba(24,36,30,0.048)", "rgba(255,255,255,0.06)"),
        "seg_track": _t("rgba(24,36,30,0.07)", "rgba(255,255,255,0.08)"),
        "seg_pill": _t("#FFFFFF", "#25312C"),
        "ok": _t("#059669", "#34D399"),
        "warn": _t("#C27803", "#FBBF24"),
        "danger": _t("#DC2626", "#F87171"),
        "info": _t("#2563EB", "#60A5FA"),
    },
    "obsidian": {
        "name": "极夜暗房",
        "desc": "高对比暗房阶梯，图文边界锐利分明",
        "accent_rgb": {"light": (24, 24, 27), "dark": (56, 189, 248)},
        "brand_hex": "#18181B",
        "clay_hex": "#38BDF8",
        "clay_hover": _t("#27272A", "#7DD3FC"),
        "root_bg": _t("#F7F7F8", "#09090B"),
        "surface0": _t("#FFFFFF", "#111113"),
        "surface1": _t("#F7F7F8", "#09090B"),
        "surface2": _t("#FFFFFF", "#18181B"),
        "surface3": _t("#F2F2F5", "#202024"),
        "text": _t("#09090B", "#FAFAFA"),
        "text_2": _t("#52525B", "#A1A1AA"),
        "text_3": _t("#71717A", "#7B7B85"),
        "text_on_clay": _t("#FFFFFF", "#082F49"),
        "border": _t("#E4E4E7", "#27272A"),
        "border_strong": _t("#A1A1AA", "#3F3F46"),
        "border_soft": _t("#EFEFF1", "#1B1B1F"),
        "clay": _t("#18181B", "#38BDF8"),
        "muted": _t("#ECECEF", "#202024"),
        "hover": _t("rgba(9,9,11,0.05)", "rgba(255,255,255,0.06)"),
        "seg_track": _t("rgba(9,9,11,0.07)", "rgba(255,255,255,0.08)"),
        "seg_pill": _t("#FFFFFF", "#27272A"),
        "ok": _t("#059669", "#34D399"),
        "warn": _t("#D97706", "#FBBF24"),
        "danger": _t("#E11D48", "#FB7185"),
        "info": _t("#4F46E5", "#818CF8"),
    },
}

# ============================================================ 三档卡片密度
DENSITY_ORDER = ("compact", "standard", "large")
DENSITIES = {
    "compact": {
        "label": "紧凑", "short": "密",
        "card_w": 164, "card_h": 192, "grid_w": 172, "grid_h": 200,
        "footer_h": 48, "show_reason": False,
    },
    "standard": {
        "label": "标准", "short": "中",
        "card_w": 196, "card_h": 234, "grid_w": 204, "grid_h": 242,
        "footer_h": 68, "show_reason": True,
    },
    "large": {
        "label": "大图", "short": "大",
        "card_w": 252, "card_h": 286, "grid_w": 260, "grid_h": 294,
        "footer_h": 68, "show_reason": True,
    },
}

DEFAULT_PALETTE = "atelier"
DEFAULT_DENSITY = "compact"

# ============================================================ 动态令牌视图（向后兼容旧属性名）
CLAY = "#D97757"
BRAND = "#C25937"
CLAY_HOVER = _t("#A94A2B", "#EB8F70")
ACCENT_RGB = {"light": (194, 89, 55), "dark": (224, 122, 87)}
_ACTIVE_PALETTE = DEFAULT_PALETTE


def _hex_rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


class T:
    """当前激活主题色盘的浅/深令牌集合。用法：c(dark, T.surface1)。"""

    root_bg = _t("#F6F4F0", "#161514")
    surface0 = _t("#FDFCFA", "#1D1C1A")
    surface1 = _t("#F6F4F0", "#161514")
    surface2 = _t("#FFFFFF", "#242220")
    surface3 = _t("#F9F7F3", "#2B2926")

    text = _t("#26231F", "#F2EFE9")
    text_2 = _t("#6A645B", "#A8A298")
    text_3 = _t("#8C857B", "#857F76")
    text_on_clay = _t("#FFFFFF", "#FFFFFF")

    border = _t("#E6E1D9", "#33302C")
    border_strong = _t("#CEC5B8", "#4B4640")
    border_soft = _t("#EFECE6", "#272522")
    border_clay = _t("rgba(194,89,55,0.34)", "rgba(224,122,87,0.40)")

    clay = _t("#C25937", "#E07A57")
    muted = _t("#EFECE6", "#272522")
    hover = _t("rgba(38,35,31,0.048)", "rgba(255,255,255,0.06)")
    clay_fill = _t("rgba(194,89,55,0.08)", "rgba(224,122,87,0.14)")
    seg_track = _t("rgba(38,35,31,0.08)", "rgba(255,255,255,0.08)")
    seg_pill = _t("#FFFFFF", "#2E2B28")

    ok = _t("#2F855A", "#4CC38A")
    warn = _t("#B7791F", "#EAB354")
    danger = _t("#C8322B", "#F2746B")
    info = _t("#2B6CB0", "#7BA7F0")

    fill = {}
    edge = {}


ROLE_BY_VERDICT = {"ad": "danger", "suspect": "warn", "error": "info", "clean": "ok"}
_ROLE_FG = {}


def set_palette(name: str) -> str:
    """切换当前激活的主题色盘，原地同步更新全部颜色令牌。"""
    global CLAY, BRAND, CLAY_HOVER, ACCENT_RGB, _ACTIVE_PALETTE
    key = name if name in PALETTES else DEFAULT_PALETTE
    spec = PALETTES[key]
    _ACTIVE_PALETTE = key
    CLAY = spec["clay_hex"]
    BRAND = spec["brand_hex"]
    CLAY_HOVER = dict(spec["clay_hover"])
    ACCENT_RGB = dict(spec["accent_rgb"])

    for field in (
        "root_bg", "surface0", "surface1", "surface2", "surface3",
        "text", "text_2", "text_3", "text_on_clay",
        "border", "border_strong", "border_soft",
        "clay", "muted", "hover", "seg_track", "seg_pill",
        "ok", "warn", "danger", "info",
    ):
        setattr(T, field, dict(spec[field]))

    ar_l, ar_d = spec["accent_rgb"]["light"], spec["accent_rgb"]["dark"]
    T.clay_fill = _t(_rgba(ar_l, 0.08), _rgba(ar_d, 0.14))
    T.border_clay = _t(_rgba(ar_l, 0.34), _rgba(ar_d, 0.42))

    ok_l, ok_d = _hex_rgb(spec["ok"]["light"]), _hex_rgb(spec["ok"]["dark"])
    wn_l, wn_d = _hex_rgb(spec["warn"]["light"]), _hex_rgb(spec["warn"]["dark"])
    dg_l, dg_d = _hex_rgb(spec["danger"]["light"]), _hex_rgb(spec["danger"]["dark"])
    in_l, in_d = _hex_rgb(spec["info"]["light"]), _hex_rgb(spec["info"]["dark"])
    gy_l, gy_d = _hex_rgb(spec["text_2"]["light"]), _hex_rgb(spec["text_2"]["dark"])

    T.fill = {
        "ok": _t(_rgba(ok_l, 0.11), _rgba(ok_d, 0.15)),
        "warn": _t(_rgba(wn_l, 0.12), _rgba(wn_d, 0.15)),
        "danger": _t(_rgba(dg_l, 0.10), _rgba(dg_d, 0.15)),
        "info": _t(_rgba(in_l, 0.11), _rgba(in_d, 0.15)),
        "clay": _t(_rgba(ar_l, 0.10), _rgba(ar_d, 0.15)),
        "gray": _t(_rgba(gy_l, 0.10), _rgba(gy_d, 0.13)),
    }
    T.edge = {
        "ok": _t(_rgba(ok_l, 0.34), _rgba(ok_d, 0.38)),
        "warn": _t(_rgba(wn_l, 0.36), _rgba(wn_d, 0.40)),
        "danger": _t(_rgba(dg_l, 0.34), _rgba(dg_d, 0.38)),
        "info": _t(_rgba(in_l, 0.34), _rgba(in_d, 0.38)),
        "clay": _t(_rgba(ar_l, 0.40), _rgba(ar_d, 0.44)),
        "gray": _t(_rgba(gy_l, 0.24), _rgba(gy_d, 0.28)),
    }
    _ROLE_FG.clear()
    _ROLE_FG.update({
        "ok": T.ok, "warn": T.warn, "danger": T.danger,
        "info": T.info, "clay": T.clay, "gray": T.text_2,
    })
    return key


def get_palette() -> str:
    return _ACTIVE_PALETTE


def palette_label(name: str) -> str:
    return PALETTES.get(name, PALETTES[DEFAULT_PALETTE])["name"]


def accent_rgb(dark: bool) -> tuple[int, int, int]:
    return ACCENT_RGB["dark" if dark else "light"]


def density_spec(name: str) -> dict:
    return DENSITIES.get(name, DENSITIES[DEFAULT_DENSITY])


def c(dark: bool, token: dict) -> str:
    """取当前主题下的色值。"""
    return token["dark" if dark else "light"]


def sem(dark: bool, role):
    """语义角色三件套：(主色, 填充, 边框)。"""
    key = ROLE_BY_VERDICT.get(role, role)
    if key not in _ROLE_FG:
        key = "gray"
    return c(dark, _ROLE_FG[key]), c(dark, T.fill[key]), c(dark, T.edge[key])


def pill_qss(dark: bool, role: str, radius: int = 12, pad: str = "2px 10px",
             border: bool = False) -> str:
    """语义色药丸 QSS（使用具体像素圆角，避免 Qt 将 999px 渲染为直角）。"""
    fg, fill, edge = sem(dark, role)
    b = f" border: 1px solid {edge};" if border else ""
    return f"background: {fill};{b} border-radius: {radius}px; padding: {pad}; color: {fg};"


# 初始化默认主题色盘
set_palette(DEFAULT_PALETTE)

# ============================================================ 圆角 / 尺寸 / 动效
R_SM = 4
R_MD = 6
R_LG = 8
R_PANEL = 10
R_CARD = 12
R_XL = 16
R_PILL = 12

H_MD = 32
DUR_BASE = 200
EASE_OUT = QEasingCurve.OutCubic
TOAST_MS = 3000
TOAST_MARGIN = 16
CARD_HOVER_LIFT = -2.0
