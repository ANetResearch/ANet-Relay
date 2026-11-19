"""应用常量：飞行模式、颜色令牌、主题定义。"""

MODE_OPTIONS = [
    ("MANUAL",         "MANUAL"),
    ("ALTCTL",         "ALTCTL"),
    ("POSCTL",         "POSCTL"),
    ("AUTO.MISSION",   "AUTO.MISSION"),
    ("AUTO.LOITER",    "AUTO.LOITER"),
    ("AUTO.RTL",       "AUTO.RTL"),
    ("AUTO.LAND",      "AUTO.LAND"),
    ("ACRO",           "ACRO"),
    ("OFFBOARD",       "OFFBOARD"),
    ("STABILIZED",     "STABILIZED"),
]

MODE_ALIAS_MAP = {
    "MISSION":     "AUTO.MISSION",
    "LOITER":      "AUTO.LOITER",
    "RTL":         "AUTO.RTL",
    "LAND":        "AUTO.LAND",
}

# 语义颜色 — 两种主题共用同一 key，值不同
LIGHT_COLORS = {
    "good":       "#1FB96A",
    "warning":    "#FFB340",
    "danger":     "#F06767",
    "normal":     "#8BA1BF",
    "info":       "#53A8FF",
    "accent":     "#4D8CFF",
    "bg_start":   "#F8FBFF",
    "bg_mid1":    "#EEF5FF",
    "bg_mid2":    "#F7FBFF",
    "bg_end":     "#EAF3FF",
    "card_bg":    "rgba(255, 255, 255, 0.82)",
    "card_border": "rgba(255, 255, 255, 0.88)",
    "panel_bg":    "rgba(255, 255, 255, 0.80)",
    "panel_border": "rgba(255, 255, 255, 0.92)",
    "sidebar_bg":  "rgba(255, 255, 255, 0.74)",
    "footer_bg":   "rgba(255, 255, 255, 0.78)",
    "text_primary":   "#1C2D42",
    "text_secondary": "#7890AF",
    "text_tertiary":  "#5D718C",
    "text_muted":     "#4A6383",
    "grid_pen":       "rgba(98, 135, 184, 22)",
    "blob_colors": [
        (102, 175, 255, 96),
        (87,  226, 255, 72),
        (128, 196, 255, 62),
        (170, 206, 255, 64),
    ],
}

DARK_COLORS = {
    "good":       "#2BD98B",
    "warning":    "#FFC043",
    "danger":     "#F07171",
    "normal":     "#6B7D99",
    "info":       "#5DADFF",
    "accent":     "#5D9EFF",
    "bg_start":   "#141829",
    "bg_mid1":    "#1A1F35",
    "bg_mid2":    "#161C30",
    "bg_end":     "#111528",
    "card_bg":    "rgba(26, 30, 50, 0.90)",
    "card_border": "rgba(48, 54, 82, 0.82)",
    "panel_bg":    "rgba(26, 30, 50, 0.88)",
    "panel_border": "rgba(48, 54, 82, 0.86)",
    "sidebar_bg":  "rgba(22, 26, 44, 0.92)",
    "footer_bg":   "rgba(22, 26, 44, 0.90)",
    "text_primary":   "#E2E8F4",
    "text_secondary": "#8899B4",
    "text_tertiary":  "#7A8EA8",
    "text_muted":     "#9AAAC0",
    "grid_pen":       "rgba(100, 120, 160, 18)",
    "blob_colors": [
        (60, 120, 210, 50),
        (100, 160, 230, 38),
        (40,  90,  200, 55),
        (140, 190, 240, 30),
    ],
}
