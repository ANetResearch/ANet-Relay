"""
高风险区热值生成面板 — 仓库热力图

导入巡检 Excel，按区域统计异常率，在仓库地图上渲染连续渐变热力值。
布局: A/B/C 上方, D/E 下方, 中央通道, HUB 基地。
"""

from __future__ import annotations

import os
import re

import numpy as np
import matplotlib
import matplotlib.font_manager as _fm

# ── 字体修复：优先注册系统可用中文字体，彻底解决方块字问题 ──
def _setup_cjk_font() -> None:
    _candidates = [
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ]
    _registered = None
    for _p in _candidates:
        if os.path.exists(_p):
            _fm.fontManager.addfont(_p)
            _hits = [f.name for f in _fm.fontManager.ttflist if f.fname == _p]
            if _hits:
                _registered = _hits[0]
                break
    _sans = [_registered] if _registered else []
    _sans += ["WenQuanYi Zen Hei", "WenQuanYi Micro Hei",
              "Noto Sans CJK SC", "Noto Sans CJK JP",
              "DejaVu Sans"]
    matplotlib.rcParams["font.sans-serif"] = _sans
    matplotlib.rcParams["axes.unicode_minus"] = False

_setup_cjk_font()

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import FancyBboxPatch, Rectangle, Circle, FancyArrowPatch
from matplotlib.cm import ScalarMappable
from scipy.ndimage import gaussian_filter

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QFileDialog, QSizePolicy,
)

from ui.theme import ThemeManager


# ══════════════════════════════════════════════
#  配色 & 热力色板
# ══════════════════════════════════════════════

# 热力色：冰蓝 → 青 → 黄绿 → 橙 → 深红
HEAT_COLORS = [
    "#0a0f1e",   # 极低  — 近黑蓝
    "#0d3060",   # 低    — 深海蓝
    "#0077b6",   # 偏低  — 蓝
    "#00b4d8",   # 中低  — 青蓝
    "#90e0ef",   # 中    — 冰青
    "#f4d35e",   # 中高  — 黄
    "#f07f3a",   # 高    — 橙
    "#d62828",   # 极高  — 红
    "#6a0d0d",   # 危险  — 暗红
]
HEAT_CMAP = LinearSegmentedColormap.from_list("risk_heat", HEAT_COLORS, N=512)

# 背景深色系
C_BG        = "#07090f"        # 最深背景
C_WALL      = "#0d1525"        # 仓库底板
C_GRID      = (0, .6, .9, .04) # 网格线
C_CYAN      = "#00c8ff"        # 主青色
C_CYAN_MID  = (0, .78, 1, .18)
C_CYAN_DIM  = (0, .78, 1, .07)
C_WHITE_SUB = (1, 1, 1, .06)
C_WHITE_DIM = (1, 1, 1, .10)
C_TEXT_HI   = (1,  1,  1, .95)
C_TEXT_MID  = (1,  1,  1, .60)
C_TEXT_LO   = (1,  1,  1, .30)


# ══════════════════════════════════════════════
#  仓库布局（坐标系 0-100 × 0-60）
# ══════════════════════════════════════════════

# (x1, y1, x2, y2)  y 轴向上为正（imshow origin="lower"）
ZONE_LAYOUT: dict[str, tuple[float, float, float, float]] = {
    "A区": ( 3, 32, 31, 57),
    "B区": (35, 32, 65, 57),
    "C区": (69, 32, 97, 57),
    "D区": ( 3,  3, 31, 28),
    "E区": (69,  3, 97, 28),
}
CORRIDOR_Y   = (28, 32)   # 中央通道 y 范围
HUB_POS      = (49, 15.5) # HUB 基地坐标

# 货架行列数
SHELF_GRID: dict[str, tuple[int, int]] = {
    "A区": (4, 3), "B区": (4, 3), "C区": (4, 3),
    "D区": (4, 3), "E区": (4, 3),
}


# ══════════════════════════════════════════════
#  主组件
# ══════════════════════════════════════════════

class HeatmapAnalyzer(QFrame):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("heatmapAnalyzer")
        self._tm = ThemeManager.instance()
        self._tm.changed.connect(self._refresh_style)
        self._zone_data: dict[str, dict] = {}
        self._fig: Figure | None = None
        self._canvas: FigureCanvas | None = None
        self._build_ui()
        self._refresh_style()
        # 启动时先画空白地图
        self._draw()

    # ──────────────────────────────────────────
    #  UI 构建
    # ──────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # ── 标题栏 ──
        header = QHBoxLayout()
        self._title = QLabel("高风险区热值生成")
        self._title.setStyleSheet("font-size: 20px; font-weight: bold;")
        header.addWidget(self._title)
        header.addStretch(1)

        self._import_btn = QPushButton("📂  导入巡检 Excel")
        self._import_btn.setMinimumHeight(42)
        self._import_btn.setMinimumWidth(200)
        self._import_btn.clicked.connect(self._import_excel)
        header.addWidget(self._import_btn)
        root.addLayout(header)

        # ── 画布 ──
        self._fig = Figure(figsize=(14, 8), dpi=100)
        self._fig.patch.set_facecolor(C_BG)
        self._canvas = FigureCanvas(self._fig)
        self._canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._canvas.setMinimumHeight(520)
        root.addWidget(self._canvas, 1)

        # ── 统计条 ──
        self._stats_label = QLabel("请导入巡检 Excel 文件以生成热力图")
        self._stats_label.setAlignment(Qt.AlignCenter)
        self._stats_label.setStyleSheet("font-size: 13px;")
        root.addWidget(self._stats_label)

    # ──────────────────────────────────────────
    #  Excel 导入 & 解析
    # ──────────────────────────────────────────

    def _import_excel(self) -> None:
        dlg = QFileDialog(self, "导入巡检 Excel", "", "Excel 文件 (*.xlsx);;所有文件 (*)")
        dlg.setAcceptMode(QFileDialog.AcceptOpen)
        dlg.setOption(QFileDialog.DontUseNativeDialog, True)
        dlg.setStyleSheet(
            "QFileDialog,QWidget{background:white;color:black;}"
            "QPushButton{background:#e0e0e0;border:1px solid #aaa;padding:4px 12px;}"
            "QLineEdit,QTreeView{background:white;border:1px solid #aaa;}"
            "QHeaderView::section{background:#e0e0e0;}"
        )
        if dlg.exec_() != QFileDialog.Accepted:
            return
        paths = dlg.selectedFiles()
        if not paths:
            return
        try:
            self._parse_excel(paths[0])
            self._draw()
            self._update_stats()
        except Exception as exc:
            self._stats_label.setText(f"✗ 解析失败: {exc}")

    def _parse_excel(self, path: str) -> None:
        import openpyxl
        wb = openpyxl.load_workbook(path)
        ws = wb.active
        self._zone_data.clear()

        header_row = None
        for row in ws.iter_rows(min_row=1, max_row=10, values_only=True):
            if row and row[0] == "序号":
                header_row = [str(c) if c else "" for c in row]
                break
        if header_row is None:
            raise ValueError("未找到表头行（第一列应为'序号'）")

        ci  = {h: i for i, h in enumerate(header_row)}
        zc  = ci.get("存放区域", 3)
        pc  = ci.get("位置检测", 4)
        tc  = ci.get("温度检测", 5)
        zre = re.compile(r"([A-E]区)")

        for row in ws.iter_rows(min_row=5, values_only=True):
            if not row or row[0] is None:
                continue
            try:
                zs = str(row[zc] if zc < len(row) else "")
                ps = str(row[pc] if pc < len(row) else "")
                ts = str(row[tc] if tc < len(row) else "")
            except IndexError:
                continue
            m = zre.search(zs)
            if not m:
                continue
            zone = m.group(1)
            ab = any(k in s for k in ("✗", "异常", "偏移") for s in (ps, ts))
            self._zone_data.setdefault(zone, {"total": 0, "abnormal": 0})
            self._zone_data[zone]["total"] += 1
            if ab:
                self._zone_data[zone]["abnormal"] += 1

        if not self._zone_data:
            raise ValueError("未解析到有效数据，请检查表格格式")

    # ──────────────────────────────────────────
    #  主绘制
    # ──────────────────────────────────────────

    def _draw(self) -> None:
        self._fig.clear()

        # 主地图轴 + 右侧颜色条轴
        ax = self._fig.add_axes([0.03, 0.08, 0.88, 0.88])
        ax_cb = self._fig.add_axes([0.93, 0.18, 0.015, 0.60])

        ax.set_xlim(0, 100)
        ax.set_ylim(0, 60)
        ax.set_aspect("equal")
        ax.axis("off")
        ax.set_facecolor(C_BG)
        self._fig.patch.set_facecolor(C_BG)

        self._draw_background(ax)
        self._draw_heatmap(ax)
        self._draw_zones(ax)
        self._draw_corridor(ax)
        self._draw_hub(ax)
        self._draw_zone_labels(ax)
        self._draw_colorbar(ax_cb)
        self._draw_title(ax)

        self._canvas.draw()

    # ── 背景：网格 + 外墙 ──
    def _draw_background(self, ax) -> None:
        # 仓库底板
        floor = FancyBboxPatch((0, 0), 100, 60,
                               boxstyle="round,pad=0.3",
                               fc=C_WALL, ec=C_CYAN_MID, lw=1.8)
        ax.add_patch(floor)

        # 细网格
        for x in np.arange(5, 100, 5):
            ax.axvline(x, color=C_GRID, lw=0.4)
        for y in np.arange(5, 60, 5):
            ax.axhline(y, color=C_GRID, lw=0.4)

        # 四角 HUD 装饰框
        L = 6
        for cx, cy, dx, dy in [(0,0,1,1),(100,0,-1,1),(0,60,1,-1),(100,60,-1,-1)]:
            ax.plot([cx, cx+dx*L], [cy, cy],       color=C_CYAN, lw=1.5, alpha=.6)
            ax.plot([cx, cx],       [cy, cy+dy*L], color=C_CYAN, lw=1.5, alpha=.6)

    # ── 热力底图（高分辨率 imshow + 高斯模糊） ──
    def _draw_heatmap(self, ax) -> None:
        RES = 400
        img = np.zeros((RES, RES), dtype=np.float32)

        for zone, (x1, y1, x2, y2) in ZONE_LAYOUT.items():
            rate = self._get_rate(zone)
            r1 = int(y1 / 60 * RES); r2 = int(y2 / 60 * RES)
            c1 = int(x1 / 100 * RES); c2 = int(x2 / 100 * RES)
            img[r1:r2, c1:c2] = rate / 100.0

        # 中央通道 & 中间空白清零
        cy1, cy2 = int(CORRIDOR_Y[0]/60*RES), int(CORRIDOR_Y[1]/60*RES)
        img[cy1:cy2, :] = 0.0

        gap_c1, gap_c2 = int(31/100*RES), int(69/100*RES)
        gap_r1, gap_r2 = int(3/60*RES),  int(28/60*RES)
        img[gap_r1:gap_r2, gap_c1:gap_c2] = 0.0

        # 高斯模糊：区域边界平滑过渡
        sigma = RES / 55
        img = gaussian_filter(img, sigma=sigma)

        # 重新把通道区和中间空白压回 0（防止模糊扩散）
        img[cy1:cy2, :] *= 0.15
        img[gap_r1:gap_r2, gap_c1:gap_c2] *= 0.1

        ax.imshow(
            img,
            extent=[0, 100, 0, 60],
            origin="lower",
            cmap=HEAT_CMAP,
            vmin=0, vmax=0.55,
            aspect="auto",
            alpha=0.88,
            interpolation="bilinear",
            zorder=1,
        )

    # ── 区域边框 + 货架纹理 ──
    def _draw_zones(self, ax) -> None:
        for zone, (x1, y1, x2, y2) in ZONE_LAYOUT.items():
            # 区域外框（发光感）
            for lw, alpha in [(3.0, 0.08), (1.2, 0.22)]:
                rect = FancyBboxPatch(
                    (x1+.3, y1+.3), x2-x1-.6, y2-y1-.6,
                    boxstyle="round,pad=0.4",
                    fc="none",
                    ec=(*matplotlib.colors.to_rgb(C_CYAN), alpha),
                    lw=lw, zorder=3,
                )
                ax.add_patch(rect)

            # 货架小格子（体现仓储感）
            cols, rows = SHELF_GRID.get(zone, (3, 2))
            pw = (x2 - x1 - 3.0) / cols
            ph = (y2 - y1 - 3.5) / rows
            for ci in range(cols):
                for ri in range(rows):
                    sx = x1 + 1.5 + ci * pw
                    sy = y1 + 1.8 + ri * ph
                    r = FancyBboxPatch(
                        (sx + .15, sy + .15), pw - .4, ph - .3,
                        boxstyle="round,pad=0.15",
                        fc=C_WHITE_SUB, ec=C_WHITE_DIM, lw=0.5, zorder=2,
                    )
                    ax.add_patch(r)

    # ── 中央通道 ──
    def _draw_corridor(self, ax) -> None:
        y1, y2 = CORRIDOR_Y
        h = y2 - y1

        ch = Rectangle((0, y1), 100, h, fc=C_CYAN_DIM, ec="none", zorder=2)
        ax.add_patch(ch)

        # 上下发光线
        for ly in (y1, y2):
            ax.plot([0, 100], [ly, ly],
                    color=C_CYAN, lw=1.0, alpha=.35, zorder=4)

        # 通道中心虚线
        mid = (y1 + y2) / 2
        ax.plot([2, 98], [mid, mid],
                color=C_CYAN, lw=0.6, alpha=.25, ls=(0, (8, 6)), zorder=4)

        # 文字
        ax.text(50, mid, "=  中 央 主 通 道  =",
                ha="center", va="center",
                fontsize=8.5, color=(0, .78, 1, .40),
                fontweight="bold", zorder=5)

        # 通道两端箭头（示意流向）
        for xs, xe in [(4, 14), (86, 96)]:
            ax.annotate(
                "", xy=(xe, mid), xytext=(xs, mid),
                arrowprops=dict(
                    arrowstyle="->", color=(0, .78, 1, .30),
                    lw=1.0, mutation_scale=10,
                ),
                zorder=5,
            )

    # ── HUB 基地 ──
    def _draw_hub(self, ax) -> None:
        bx, by = HUB_POS
        # 多层发光圆环
        rings = [(16, 0.06), (12, 0.12), (8, 0.22), (5, 0.40), (2.2, 0.80)]
        for r, a in rings:
            ax.add_patch(Circle((bx, by), r,
                                fc="none",
                                ec=(0, .78, 1, a),
                                lw=1.2, zorder=4))

        # 中心实心圆
        ax.plot(bx, by, "o",
                color=C_CYAN, markersize=6,
                markeredgewidth=1.8, markeredgecolor=(0, .78, 1, .35),
                zorder=5)

        # 十字准线
        L = 3.5
        for dx, dy in [(L,0),(-L,0),(0,L),(0,-L)]:
            ax.plot([bx, bx+dx], [by, by+dy],
                    color=C_CYAN, lw=0.8, alpha=.45, zorder=4)

        # 标签
        ax.text(bx, by - 3.8, "[HUB]  BASE",
                ha="center", va="center",
                fontsize=8, color=C_CYAN,
                fontweight="bold", fontstyle="italic",
                zorder=5)

    # ── 区域信息标注 ──
    def _draw_zone_labels(self, ax) -> None:
        for zone, (x1, y1, x2, y2) in ZONE_LAYOUT.items():
            rate = self._get_rate(zone)
            data = self._zone_data.get(zone, {"total": 0, "abnormal": 0})
            cx, cy = (x1+x2)/2, (y1+y2)/2

            # 根据异常率选标注颜色
            if rate >= 40:
                accent = "#ff4444"
            elif rate >= 20:
                accent = "#ff9900"
            elif rate >= 5:
                accent = "#f4d35e"
            else:
                accent = C_CYAN

            # 半透明标注背景
            bg = FancyBboxPatch(
                (cx - 10.5, cy - 1.5), 21, 9.5,
                boxstyle="round,pad=0.5",
                fc=(7/255, 9/255, 15/255, 0.78),
                ec=(*matplotlib.colors.to_rgb(accent), 0.30),
                lw=0.8, zorder=6,
            )
            ax.add_patch(bg)

            # 区域名
            ax.text(cx, cy + 6.2, zone,
                    ha="center", va="center",
                    fontsize=14, fontweight="bold",
                    color=accent, zorder=7)

            # 异常率（大字）
            ax.text(cx, cy + 3.4,
                    f"{rate:.0f}%",
                    ha="center", va="center",
                    fontsize=13, fontweight="bold",
                    color=C_TEXT_HI if rate > 0 else C_TEXT_LO,
                    zorder=7)

            # 件数
            ax.text(cx, cy + 1.2,
                    f"异常 {data['abnormal']} / 共 {data['total']} 件",
                    ha="center", va="center",
                    fontsize=7.5, color=C_TEXT_MID,
                    zorder=7)

            # 危险等级色块
            if rate > 0:
                level = "危险" if rate >= 40 else ("警告" if rate >= 20 else "注意")
                lvl_bg = FancyBboxPatch(
                    (cx - 5.5, cy - 1.2), 11, 2.4,
                    boxstyle="round,pad=0.2",
                    fc=(*matplotlib.colors.to_rgb(accent), 0.20),
                    ec=(*matplotlib.colors.to_rgb(accent), 0.50),
                    lw=0.6, zorder=7,
                )
                ax.add_patch(lvl_bg)
                ax.text(cx, cy - 0.05, f"▲ {level}",
                        ha="center", va="center",
                        fontsize=7, fontweight="bold",
                        color=accent, zorder=8)

    # ── 颜色条（竖向，右侧） ──
    def _draw_colorbar(self, ax_cb) -> None:
        sm = ScalarMappable(cmap=HEAT_CMAP, norm=Normalize(0, 100))
        sm.set_array([])
        cb = self._fig.colorbar(sm, cax=ax_cb, orientation="vertical")
        cb.set_label("异常率 (%)", fontsize=9, color="#888888", labelpad=6)
        cb.set_ticks([0, 20, 40, 60, 80, 100])
        cb.ax.tick_params(colors="#888888", labelsize=8, length=2)
        cb.outline.set_visible(False)
        cb.ax.yaxis.set_tick_params(pad=2)

    # ── 右上角标题 + 时间戳 ──
    def _draw_title(self, ax) -> None:
        ax.text(50, 59.0, "WAREHOUSE  RISK  HEATMAP",
                ha="center", va="top",
                fontsize=10, fontweight="bold",
                color=(0, .78, 1, .55),
                fontfamily="monospace",
                zorder=9)

        import datetime
        ts = datetime.datetime.now().strftime("%Y-%m-%d  %H:%M")
        ax.text(98, 58.5, ts,
                ha="right", va="top",
                fontsize=7, color=(1, 1, 1, .22),
                fontfamily="monospace",
                zorder=9)

    # ──────────────────────────────────────────
    #  工具方法
    # ──────────────────────────────────────────

    def _get_rate(self, zone: str) -> float:
        d = self._zone_data.get(zone, {"total": 0, "abnormal": 0})
        return d["abnormal"] / max(d["total"], 1) * 100

    def _update_stats(self) -> None:
        ta = sum(d["total"]    for d in self._zone_data.values())
        aa = sum(d["abnormal"] for d in self._zone_data.values())
        ra = aa / max(ta, 1) * 100
        wz = max(
            self._zone_data.items(),
            key=lambda x: x[1]["abnormal"] / max(x[1]["total"], 1),
            default=("—", {"abnormal": 0, "total": 1}),
        )
        self._stats_label.setText(
            f"总计 {ta} 件  ·  异常 {aa} 件（{ra:.1f}%）  ·  "
            f"⚠ 高风险区：{wz[0]}  {wz[1]['abnormal']}/{wz[1]['total']} 件"
        )

    def _refresh_style(self, _mode: str = "") -> None:
        c = self._tm.colors()
        self.setStyleSheet(
            f"QFrame#heatmapAnalyzer {{ background: {c['card_bg']}; }}"
            f"QLabel {{ color: {c['text_primary']}; background: transparent; }}"
        )
        self._import_btn.setStyleSheet(
            "QPushButton {"
            "  color: #FFFFFF;"
            "  background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "    stop:0 #4D8CFF, stop:1 #1FB96A);"
            "  border: none; border-radius: 10px;"
            "  padding: 10px 24px;"
            "  font-size: 14px; font-weight: bold;"
            "}"
            "QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "  stop:0 #6aa3ff, stop:1 #35d47f); }"
        )
        if self._fig:
            self._fig.patch.set_facecolor(C_BG)
            self._canvas.draw()