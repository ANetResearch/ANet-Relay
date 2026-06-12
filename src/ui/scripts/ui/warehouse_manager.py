"""仓库管理 数据库 + AR 码识别 + 摄像头画面"""
from __future__ import annotations

import os
import re
import shlex
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Set
from dataclasses import dataclass

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit,
    QDialogButtonBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QHBoxLayout, QMessageBox,
)

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image as RosImage
from geometry_msgs.msg import TransformStamped
from cv_bridge import CvBridge
import numpy as np

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "warehouse.db")
IMAGE_TOPIC = os.environ.get("WAREHOUSE_IMAGE_TOPIC", "/aruco_detector/debug_image")
MARKER_TOPIC = os.environ.get("WAREHOUSE_MARKER_TOPIC", "/aruco_detector/aruco_transform")
AR_LAUNCH_CMD = os.environ.get("WAREHOUSE_AR_LAUNCH_CMD", "").strip()
MARKER_ID_RE = re.compile(r"(-?\d+)$")

@dataclass
class GoodsEntry:
    marker_id: int
    name: str
    region: str
    timestamp: str


class WarehouseDB:
    def __init__(self, path: str = DB_PATH):
        self._path = os.path.abspath(path)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS goods ("
            "  marker_id INTEGER PRIMARY KEY,"
            "  name TEXT NOT NULL,"
            "  region TEXT NOT NULL DEFAULT '',"
            "  timestamp TEXT NOT NULL"
            ")"
        )
        # 兼容旧表：没有 region 列则自动添加
        try:
            self._conn.execute("ALTER TABLE goods ADD COLUMN region TEXT NOT NULL DEFAULT ''")
            # 刚添加的 region 列在最末尾，旧 INSERT 顺序是 (id,name,ts,region)
            # 但之前可能已经用错误顺序写入了数据，尝试修复
            self._conn.execute(
                "UPDATE goods SET region = timestamp, timestamp = region "
                "WHERE region = '' OR region = timestamp"
            )
        except sqlite3.OperationalError:
            pass
        self._conn.commit()

    def insert(self, marker_id: int, name: str, region: str = "") -> bool:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO goods (marker_id, name, region, timestamp) "
                    "VALUES (?, ?, ?, ?)",
                    (marker_id, name, region, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                )
                self._conn.commit()
                return True
            except Exception:
                return False

    def lookup(self, marker_id: int) -> Optional[GoodsEntry]:
        with self._lock:
            row = self._conn.execute(
                "SELECT marker_id, name, region, timestamp FROM goods WHERE marker_id=?",
                (marker_id,),
            ).fetchone()
        return GoodsEntry(*row) if row else None

    def delete(self, marker_id: int) -> bool:
        with self._lock:
            try:
                self._conn.execute("DELETE FROM goods WHERE marker_id=?", (marker_id,))
                self._conn.commit()
                return True
            except Exception:
                return False

    def list_all(self) -> List[GoodsEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT marker_id, name, region, timestamp FROM goods ORDER BY marker_id"
            ).fetchall()
        return [GoodsEntry(*r) for r in rows]


class CameraNode(Node):
    def __init__(self, image_topic: str = IMAGE_TOPIC, marker_topic: str = MARKER_TOPIC):
        super().__init__("warehouse_camera_node")
        self.bridge = CvBridge()
        self._latest_frame: Optional[np.ndarray] = None
        self._detected_ids: List[int] = []
        self._lock = threading.Lock()
        self._img_sub = self.create_subscription(
            RosImage, image_topic, self._img_cb, 10)
        self._tf_sub = self.create_subscription(
            TransformStamped, marker_topic, self._tf_cb, 10)

    def _img_cb(self, msg: RosImage):
        try:
            cv_img = self.bridge.imgmsg_to_cv2(msg, "bgr8")
            with self._lock:
                self._latest_frame = cv_img
        except Exception:
            pass

    def _tf_cb(self, msg: TransformStamped):
        with self._lock:
            marker_id = self._parse_marker_id(msg.child_frame_id)
            if marker_id is not None and marker_id not in self._detected_ids:
                self._detected_ids.append(marker_id)

    @staticmethod
    def _parse_marker_id(frame_id: str) -> Optional[int]:
        match = MARKER_ID_RE.search(frame_id or "")
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def get_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._latest_frame.copy() if self._latest_frame is not None else None

    def get_ids(self) -> List[int]:
        with self._lock:
            return list(self._detected_ids)

    def clear_ids(self):
        with self._lock:
            self._detected_ids.clear()


class WarehouseManager(QObject):
    frame_ready   = pyqtSignal(QPixmap)
    marker_found  = pyqtSignal(int)
    status_msg    = pyqtSignal(str)
    db_changed    = pyqtSignal()

    # ═══════════════════════════════════════════════════════════════
    # [demo代码，可删] 温度+位置检测 Demo 信号
    # ═══════════════════════════════════════════════════════════════
    inspection_countdown = pyqtSignal(int, float)  # (AR码ID, 剩余秒数)
    inspection_result    = pyqtSignal(int, bool)   # (AR码ID, True=正常/False=异常)
    inspection_reset     = pyqtSignal()            # 重置 Demo 状态
    inspection_log_updated = pyqtSignal()          # 巡检记录更新

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = WarehouseDB()
        self._ros_node: Optional[CameraNode] = None

        # [demo代码，可删] 检测计数器和定时器
        self._demo_lookup_count = 0
        self._demo_countdown_timer = QTimer(self)
        self._demo_countdown_timer.setInterval(100)  # 100ms → 100 步 = 平滑进度条
        self._demo_countdown_timer.timeout.connect(self._on_demo_tick)
        self._demo_countdown_ticks = 0
        self._demo_pending_marker_id: int | None = None
        self._demo_last_completed_id: int | None = None  # 上次检测完成的 ID，相同 ID 不重复触发
        self._inspection_log: list[dict] = []  # [demo代码，可删] 巡检记录
        self._ros_thread: Optional[threading.Thread] = None
        self._launch_proc: Optional[subprocess.Popen] = None
        self._running = False
        self._mode: str = "idle"
        self._storage_handled_ids: Set[int] = set()
        self._lookup_last_seen: Dict[int, float] = {}
        self._detection_paused = False
        self._lookup_cooldown_s = 2.5

        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(100)  # ~10 fps，减少卡顿
        self._frame_timer.timeout.connect(self._pump_frame)

        self._detect_timer = QTimer(self)
        self._detect_timer.setInterval(1000)  # 每秒检测一次
        self._detect_timer.timeout.connect(self._pump_detections)

    def start_ros(self):
        if self._running:
            if not self._frame_timer.isActive():
                self._frame_timer.start()
            if not self._detect_timer.isActive():
                self._detect_timer.start()
            return
        self._running = True
        self._start_external_recognizer()

        def _spin():
            try:
                if not rclpy.ok():
                    rclpy.init(args=[])
                self._ros_node = CameraNode()
                while self._running and rclpy.ok():
                    rclpy.spin_once(self._ros_node, timeout_sec=0.02)  # 短超时，减少卡顿
            except Exception as exc:
                self.status_msg.emit(f"摄像头识别节点启动失败：{exc}")
            finally:
                if self._ros_node is not None:
                    self._ros_node.destroy_node()
                    self._ros_node = None

        self._ros_thread = threading.Thread(target=_spin, daemon=True)
        self._ros_thread.start()
        self._frame_timer.start()
        self._detect_timer.start()
        self.status_msg.emit(
            f"摄像头识别已启动，图像话题：{IMAGE_TOPIC}，AR 码话题：{MARKER_TOPIC}"
        )

    def stop_ros(self):
        self._running = False
        self._frame_timer.stop()
        self._detect_timer.stop()
        self._mode = "idle"
        self._reset_scan_state()
        self._stop_external_recognizer()

    def _start_external_recognizer(self):
        if not AR_LAUNCH_CMD or self._launch_proc is not None:
            return
        try:
            self._launch_proc = subprocess.Popen(
                shlex.split(AR_LAUNCH_CMD),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            self.status_msg.emit(f"已启动外部 AR 识别命令：{AR_LAUNCH_CMD}")
        except Exception as exc:
            self._launch_proc = None
            self.status_msg.emit(f"外部 AR 识别命令启动失败：{exc}")

    def _stop_external_recognizer(self):
        proc = self._launch_proc
        self._launch_proc = None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()

    def set_mode_storage(self):
        self._mode = "storage"
        self._reset_scan_state()
        self.status_msg.emit("【商品入库】将 AR 码对准摄像头，检测到后输入货物名称")

    def set_mode_lookup(self):
        self._mode = "lookup"
        self._reset_scan_state()
        self.status_msg.emit("【货物识别】扫描 AR 码，自动匹配数据库...")

    # ═══════════════════════════════════════════════════════════════
    # [demo代码，可删] 导出巡检报告
    # ═══════════════════════════════════════════════════════════════
    def get_inspection_log(self) -> list:
        return list(self._inspection_log)

    def export_inspection_log(self, filepath: str) -> bool:
        """导出巡检记录到 Excel 文件"""
        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        except ImportError:
            self.status_msg.emit("✗ 缺少 openpyxl 库，请 pip3 install openpyxl")
            return False

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "巡检报告"

        # 标题行
        title_font = Font(name="微软雅黑", size=14, bold=True, color="1F4E79")
        ws.merge_cells("A1:G1")
        ws["A1"] = f"YixPlanner 货物巡检报告  —  {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        ws["A1"].font = title_font
        ws["A1"].alignment = Alignment(horizontal="center")

        # 表头
        headers = ["序号", "AR码 ID", "货物名称", "存放区域", "位置检测", "温度检测", "检测时间"]
        header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
        header_font = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
        thin_border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin"))

        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=3, column=col, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border

        # 数据行
        ok_fill = PatternFill(start_color="E2F0D9", end_color="E2F0D9", fill_type="solid")
        fail_fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
        data_font = Font(name="微软雅黑", size=10)

        for idx, entry in enumerate(self._inspection_log):
            row = idx + 4
            pos_text = "✓ 合规" if entry["position_ok"] else "✗ 偏移"
            temp_text = "✓ 正常" if entry["temp_ok"] else "✗ 异常"
            row_fill = ok_fill if entry["position_ok"] else fail_fill

            values = [idx+1, entry["marker_id"], entry["name"], entry["region"],
                      pos_text, temp_text, entry["time"]]
            for col, v in enumerate(values, 1):
                cell = ws.cell(row=row, column=col, value=v)
                cell.font = data_font
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.border = thin_border
                cell.fill = row_fill

        # 列宽
        widths = [6, 10, 18, 14, 12, 14, 20]
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

        # 统计行
        total = len(self._inspection_log)
        ok_count = sum(1 for e in self._inspection_log if e["position_ok"])
        fail_count = total - ok_count
        stat_row = total + 5
        ws.merge_cells(f"A{stat_row}:G{stat_row}")
        ws[f"A{stat_row}"] = f"共检测 {total} 件  |  合规 {ok_count} 件  |  异常 {fail_count} 件"
        ws[f"A{stat_row}"].font = Font(name="微软雅黑", size=11, bold=True)
        ws[f"A{stat_row}"].alignment = Alignment(horizontal="center")

        wb.save(filepath)
        self.status_msg.emit(f"✓ 巡检报告已导出: {filepath}")
        return True

    def pause_detections(self):
        self._detection_paused = True

    def resume_detections(self, clear_pending: bool = True):
        self._detection_paused = False
        if clear_pending and self._ros_node is not None:
            self._ros_node.clear_ids()

    def _reset_scan_state(self):
        self._storage_handled_ids.clear()
        self._lookup_last_seen.clear()
        self._detection_paused = False
        if self._ros_node is not None:
            self._ros_node.clear_ids()
        # [demo代码，可删] 重置检测计数
        self._demo_lookup_count = 0
        self._demo_countdown_timer.stop()
        self._demo_pending_marker_id = None
        self._demo_last_completed_id = None
        self._inspection_log.clear()
        self.inspection_log_updated.emit()
        self.inspection_reset.emit()

    # ═══════════════════════════════════════════════════════════════
    # [demo代码，可删] 温度+位置检测 Demo 逻辑
    # ID=0 → 正常, ID=1 → 异常
    # ═══════════════════════════════════════════════════════════════
    def _start_demo_inspection(self, marker_id: int):
        """开始 10s 倒计时检测（进行中 或 与上次相同ID 则忽略）"""
        if self._demo_pending_marker_id is not None:
            return
        if marker_id == self._demo_last_completed_id:
            return  # 同一个 ID 还没换，不重复检测
        self._demo_pending_marker_id = marker_id
        self._demo_countdown_ticks = 100  # 100 ticks × 100ms = 10秒
        self._demo_countdown_timer.start()
        remaining_s = self._demo_countdown_ticks / 10.0
        self.inspection_countdown.emit(marker_id, remaining_s)

    def _on_demo_tick(self):
        self._demo_countdown_ticks -= 1
        if self._demo_pending_marker_id is not None:
            remaining_s = max(0, self._demo_countdown_ticks / 10.0)
            self.inspection_countdown.emit(self._demo_pending_marker_id, remaining_s)
        if self._demo_countdown_ticks <= 0:
            self._demo_countdown_timer.stop()
            mid = self._demo_pending_marker_id
            self._demo_pending_marker_id = None
            if mid is not None:
                # ID=0 → 正常, 其他 → 异常
                is_ok = (mid == 0)
                self._demo_last_completed_id = mid
                self.inspection_result.emit(mid, is_ok)
                # [demo代码，可删] 记录巡检日志
                entry = self.db.lookup(mid)
                self._inspection_log.append({
                    "marker_id": mid,
                    "name": entry.name if entry else "未登记",
                    "region": entry.region if entry else "",
                    "position_ok": is_ok,
                    "temp_ok": is_ok,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                self.inspection_log_updated.emit()

    def store(self, marker_id: int, name: str, region: str = "") -> bool:
        ok = self.db.insert(marker_id, name, region)
        self.status_msg.emit(f"✓ 入库: id={marker_id} → {name} [{region}]" if ok else "✗ 入库失败")
        if ok:
            self.db_changed.emit()
        return ok

    def lookup(self, marker_id: int) -> Optional[GoodsEntry]:
        e = self.db.lookup(marker_id)
        if e:
            self.status_msg.emit(f"✓ 匹配: id={marker_id} → {e.name} [{e.region}] ({e.timestamp})")
        else:
            self.status_msg.emit(f"✗ id={marker_id} 无记录")
        return e

    def delete(self, marker_id: int) -> bool:
        ok = self.db.delete(marker_id)
        if ok:
            self.status_msg.emit(f"✓ 已删除 id={marker_id}")
            self.db_changed.emit()
        return ok

    def _pump_frame(self):
        node = self._ros_node
        if node is None:
            return
        frame = node.get_frame()
        if frame is None:
            return
        h, w, ch = frame.shape
        qimg = QImage(frame.data, w, h, ch * w, QImage.Format_BGR888)
        self.frame_ready.emit(QPixmap.fromImage(qimg))

    def _pump_detections(self):
        node = self._ros_node
        if node is None or self._mode == "idle" or self._detection_paused:
            return
        ids = node.get_ids()
        node.clear_ids()
        now = time.monotonic()
        for mid in ids:
            if self._mode == "storage":
                if mid in self._storage_handled_ids:
                    continue
                self._storage_handled_ids.add(mid)
                self._detection_paused = True
                self.marker_found.emit(mid)
                break
            if self._mode == "lookup":
                last_seen = self._lookup_last_seen.get(mid, 0.0)
                if now - last_seen < self._lookup_cooldown_s:
                    continue
                self._lookup_last_seen[mid] = now
                self.marker_found.emit(mid)
                # [demo代码，可删] 触发温度+位置检测流程
                self._demo_lookup_count += 1
                self._start_demo_inspection(mid)
                break


# ─────────────────────────────────────────────────────────────────
#  NameInputDialog — 入库弹窗
# ─────────────────────────────────────────────────────────────────

class NameInputDialog(QDialog):
    def __init__(self, marker_id: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"商品入库 — AR码 #{marker_id}")
        self.setMinimumWidth(400)
        self.setModal(True)
        self.setAttribute(Qt.WA_InputMethodEnabled, True)

        from ui.theme import ThemeManager
        c = ThemeManager.instance().colors()
        t = c["text_primary"]
        bg = c["panel_bg"]
        border = c["panel_border"]

        self.setStyleSheet(f"""
            QDialog {{ background-color: {bg}; }}
            QLabel {{ color: {t}; font-size: 14px; background: transparent; }}
            QLineEdit {{
                color: {t}; background-color: {bg};
                border: 1px solid {border}; border-radius: 6px;
                padding: 8px; font-size: 14px;
            }}
            QPushButton {{
                padding: 8px 20px; border-radius: 6px; font-size: 13px; font-weight: 600;
                color: {t}; background-color: {bg}; border: 1px solid {border};
            }}
            QPushButton:hover {{ background-color: {border}; }}
        """)

        lyt = QVBoxLayout(self)
        title = QLabel(f"<h3>检测到 AR 码 ID = {marker_id}</h3>")
        title.setStyleSheet(f"color: {t}; background: transparent;")
        lyt.addWidget(title)
        lyt.addWidget(QLabel("货物类别/名称："))
        self.edit = QLineEdit()
        self.edit.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.edit.setInputMethodHints(Qt.ImhNone)
        self.edit.setPlaceholderText("例：螺丝刀 / PCB板 / 线缆 / 电池 ...")
        self.edit.setMinimumHeight(32)
        self.edit.setMaxLength(80)
        self.edit.setClearButtonEnabled(True)
        lyt.addWidget(self.edit)

        lyt.addSpacing(4)
        lyt.addWidget(QLabel("存放区域/货位："))
        self.region_edit = QLineEdit()
        self.region_edit.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.region_edit.setInputMethodHints(Qt.ImhNone)
        self.region_edit.setPlaceholderText("例：A区 / B-3架 / 2号货位 ...")
        self.region_edit.setMinimumHeight(32)
        self.region_edit.setMaxLength(40)
        self.region_edit.setClearButtonEnabled(True)
        lyt.addWidget(self.region_edit)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = btns.button(QDialogButtonBox.Ok)
        ok_btn.setText("确认入库")
        ok_btn.setEnabled(False)
        ok_btn.setDefault(False)
        ok_btn.setAutoDefault(False)
        btns.button(QDialogButtonBox.Cancel).setAutoDefault(False)
        self.edit.textChanged.connect(lambda text: ok_btn.setEnabled(bool(text.strip())))
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lyt.addWidget(btns)
        QTimer.singleShot(0, lambda: self.edit.setFocus(Qt.OtherFocusReason))

    def name(self) -> str:
        return self.edit.text().strip()

    def region(self) -> str:
        return self.region_edit.text().strip()

    def accept(self):
        if not self.name():
            return
        super().accept()


# ─────────────────────────────────────────────────────────────────
#  DatabaseViewer — 数据库表格窗口
# ─────────────────────────────────────────────────────────────────

class DatabaseViewer(QDialog):
    def __init__(self, db: WarehouseDB, mgr: WarehouseManager, parent=None):
        super().__init__(parent)
        self.db = db
        self.mgr = mgr
        self.setWindowTitle("仓库数据库")
        self.setMinimumSize(560, 380)
        self.setModal(False)

        from ui.theme import ThemeManager
        c = ThemeManager.instance().colors()
        t, bg, bd, ts = c["text_primary"], c["panel_bg"], c["panel_border"], c["text_secondary"]
        self.setStyleSheet(f"""
            QDialog {{ background-color: {bg}; }}
            QLabel {{ color: {t}; background: transparent; }}
            QLineEdit {{
                color: {t}; background-color: {bg};
                border: 1px solid {bd}; border-radius: 6px; padding: 6px;
            }}
            QPushButton {{
                padding: 6px 16px; border-radius: 6px; font-size: 12px;
                color: {t}; background-color: {bg}; border: 1px solid {bd};
            }}
            QPushButton:hover {{ background-color: {bd}; }}
            QTableWidget {{
                color: {t}; background-color: {bg}; border: 1px solid {bd};
                gridline-color: {bd}; border-radius: 8px;
            }}
            QTableWidget::item {{ padding: 6px; }}
            QHeaderView::section {{
                color: {t}; background-color: {bd}; border: none; padding: 6px;
                font-weight: bold;
            }}
        """)

        lyt = QVBoxLayout(self)

        # 工具栏
        bar = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setAttribute(Qt.WA_InputMethodEnabled, True)
        self._search.setInputMethodHints(Qt.ImhNone)
        self._search.setPlaceholderText("搜索 marker ID 或货物名...")
        self._search.textChanged.connect(self._refresh)
        bar.addWidget(self._search)

        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._refresh)
        bar.addWidget(refresh_btn)

        del_btn = QPushButton("删除选中行")
        del_btn.clicked.connect(self._delete_selected)
        del_btn.setStyleSheet("color: #c0392b;")
        bar.addWidget(del_btn)

        lyt.addLayout(bar)

        # 表格
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Marker ID", "货物名称", "入库时间", "存放区域"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        lyt.addWidget(self._table)

        self._count_label = QLabel()
        lyt.addWidget(self._count_label)

        self._refresh()
        mgr.db_changed.connect(self._refresh)

    def _refresh(self, _=None):
        entries = self.db.list_all()
        keyword = self._search.text().strip().lower()
        if keyword:
            entries = [e for e in entries
                       if keyword in str(e.marker_id) or keyword in e.name.lower()]

        self._table.setRowCount(len(entries))
        for i, e in enumerate(entries):
            self._table.setItem(i, 0, QTableWidgetItem(str(e.marker_id)))
            self._table.setItem(i, 1, QTableWidgetItem(e.name))
            self._table.setItem(i, 2, QTableWidgetItem(e.timestamp))
            self._table.setItem(i, 3, QTableWidgetItem(e.region))
        self._count_label.setText(f"共 {len(entries)} 条记录")

    def _delete_selected(self):
        rows = set()
        for item in self._table.selectedItems():
            rows.add(item.row())
        if not rows:
            return
        reply = QMessageBox.question(
            self, "确认删除", f"确定删除 {len(rows)} 条记录？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        for row in sorted(rows, reverse=True):
            mid = int(self._table.item(row, 0).text())
            self.mgr.delete(mid)
        self._refresh()
