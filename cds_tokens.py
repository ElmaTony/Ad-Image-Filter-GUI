# -*- coding: utf-8 -*-
"""暖陶工作台设计令牌（单一真源）。"""
from PySide6.QtCore import QEasingCurve


def _t(light, dark):
    """构造「浅/深」双取值令牌，通过 c(dark, token) 取值。"""
    return {"light": light, "dark": dark}


# ============================================================ 品牌色
CLAY = "#D97757"
BRAND = "#C6613F"
CLAY_HOVER = _t("#CC5A36", "#E08264")


# ============================================================ 主题（深浅两套）
class T:
    """按当前主题取值的令牌集合。用法：c(dark, T.surface1)（dark 为 bool）。"""

    # —— Surface 阶梯 ——
    root_bg = _t("#F4F2EE", "#171716")       # 暖陶工作台底色
    surface0 = _t("#FCFBF9", "#20201E")      # 工具和详情表面
    surface1 = _t("#F4F2EE", "#171716")      # 图片墙
    surface2 = _t("#FFFFFF", "#292927")      # 卡片
    surface3 = _t("#FCFBF9", "#20201E")      # 输入框 / 浮层

    # —— 文本 ——
    text = _t("#302D29", "#EEECE6")
    text_2 = _t("#716C65", "#AAA59C")
    text_3 = _t("#827C73", "#928D84")
    text_on_clay = _t("#FFFFFF", "#FFFFFF")

    # —— 边框 / 分隔线 ——
    border = _t("#E5E0D8", "#363632")
    border_strong = _t("rgba(0,0,0,0.20)", "rgba(255,255,255,0.18)")
    border_soft = _t("#EFEDE8", "#262524")
    border_clay = _t("rgba(217,119,87,0.26)", "rgba(217,119,87,0.30)")

    # —— 品牌与填充 ——
    clay = _t("#B85E3F", "#E19676")
    muted = _t("#F1EFEA", "#232221")
    hover = _t("rgba(0,0,0,0.045)", "rgba(255,255,255,0.06)")
    clay_fill = _t("rgba(217,119,87,0.06)", "rgba(217,119,87,0.10)")

    # —— 语义色 ——
    ok = _t("#2F9E63", "#4CC38A")
    warn = _t("#D98A2B", "#F0B45F")
    danger = _t("#DC2626", "#F87171")
    info = _t("#2563EB", "#60A5FA")

    # —— 语义填充 / 边框（徽标药丸、状态条、分类按钮用）——
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


def c(dark: bool, token: dict) -> str:
    """取当前主题下的色值。"""
    return token["dark" if dark else "light"]


ROLE_BY_VERDICT = {"ad": "danger", "suspect": "warn", "error": "info", "clean": "ok"}
_ROLE_FG = {"ok": T.ok, "warn": T.warn, "danger": T.danger,
            "info": T.info, "clay": T.clay, "gray": T.text_2}


def sem(dark: bool, role):
    """语义角色三件套：(主色, 填充, 边框)。"""
    key = ROLE_BY_VERDICT.get(role, role)
    if key not in _ROLE_FG:
        key = "gray"
    return c(dark, _ROLE_FG[key]), c(dark, T.fill[key]), c(dark, T.edge[key])


def pill_qss(dark: bool, role: str, radius: int = 13, pad: str = "3px 10px",
             border: bool = False) -> str:
    """语义色药丸 QSS（使用具体像素圆角，避免 Qt 将 999px 渲染为直角）。"""
    fg, fill, edge = sem(dark, role)
    b = f" border: 1px solid {edge};" if border else ""
    return f"background: {fill};{b} border-radius: {radius}px; padding: {pad}; color: {fg};"


# ============================================================ 圆角 / 尺寸 / 动效
R_SM = 4
R_MD = 6
R_LG = 8
R_PANEL = 10
R_CARD = 12
R_XL = 16
R_PILL = 13

H_MD = 32
DUR_BASE = 200
EASE_OUT = QEasingCurve.OutCubic
TOAST_MS = 3000
TOAST_MARGIN = 16
CARD_HOVER_LIFT = -1.5
