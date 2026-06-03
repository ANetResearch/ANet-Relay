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
            "  timestamp TEXT NOT NULL"
            ")"
        )
        self._conn.commit()

    def insert(self, marker_id: int, name: str) -> bool:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO goods VALUES (?, ?, ?)",
                    (marker_id, name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                )
                self._conn.commit()
                return True
            except Exception:
                return False

    def lookup(self, marker_id: int) -> Optional[GoodsEntry]:
        with self._lock:
            row = self._conn.execute(
                "SELECT marker_id, name, timestamp FROM goods WHERE marker_id=?",
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
                "SELECT marker_id, name, timestamp FROM goods ORDER BY marker_id"
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = WarehouseDB()
        self._ros_node: Optional[CameraNode] = None
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

    def store(self, marker_id: int, name: str) -> bool:
        ok = self.db.insert(marker_id, name)
        self.status_msg.emit(f"✓ 入库: id={marker_id} → {name}" if ok else "✗ 入库失败")
        if ok:
            self.db_changed.emit()
        return ok

    def lookup(self, marker_id: int) -> Optional[GoodsEntry]:
        e = self.db.lookup(marker_id)
        if e:
            self.status_msg.emit(f"✓ 匹配: id={marker_id} → {e.name} ({e.timestamp})")
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
        lyt.addWidget(QLabel("请输入该货物对应的类别/名称："))
        self.edit = QLineEdit()
        self.edit.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.edit.setInputMethodHints(Qt.ImhNone)
        self.edit.setPlaceholderText("例：螺丝刀 / PCB板 / 线缆 / 电池 ...")
        self.edit.setMinimumHeight(32)
        self.edit.setMaxLength(80)
        self.edit.setClearButtonEnabled(True)
        lyt.addWidget(self.edit)

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
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Marker ID", "货物名称", "入库时间"])
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
