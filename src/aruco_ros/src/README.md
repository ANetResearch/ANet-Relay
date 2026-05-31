# ArUco 库源代码

本目录包含 ArUco 标记检测与姿态估计库的核心 C++ 实现，基于 OpenCV。它是 `aruco_ros` ROS 功能包进行基准标记（fiducial marker）定位的底层引擎。

---

## 文件概览

### 相机与可视化

**[cameraparameters.cpp](aruco/src/aruco/cameraparameters.cpp)**

相机标定参数管理（内参矩阵、畸变系数、外参矩阵）。主要功能：
- 从 XML 和纯文本文件读写相机参数
- 按不同图像分辨率缩放相机参数
- 通过旋转/平移向量计算相机在 3D 空间中的位置
- 生成 OpenGL 和 Ogre3D 投影矩阵，供 AR 渲染使用
- 处理双目相机外参

**[cvdrawingutils.cpp](aruco/src/aruco/cvdrawingutils.cpp)**

基于 OpenCV 的可视化工具，将检测结果绘制到图像上：
- `draw3dAxis()` — 在标记位姿处绘制 RGB 三色 X/Y/Z 坐标轴
- `draw3dCube()` — 在标记平面上绘制投影的 3D 立方体，支持 Y 轴垂直于标记平面模式

---

### 标记字典

**[dictionary.cpp](aruco/src/aruco/dictionary.cpp)**

定义 ArUco 标记字典（即二进制编码集合）。包含：
- 预定义字典：`ARUCO`（5×5, 25 位）、`ARTAG`（6×6, 36 位）、`ARTOOLKITPLUS`（6×6, 36 位）、`ARTOOLKITPLUSBCH`（36 位），以及 AprilTag 系列的 16h5、25h9、36h10、36h11 等
- 从纯文本文件加载自定义字典
- 计算标记间汉明距离（`tau`），衡量字典的纠错能力
- 将单个标记渲染为 OpenCV 图像

**[dictionary_based.cpp](aruco/src/aruco/dictionary_based.cpp)**

基于字典的标记识别器（实现 `MarkerLabeler` 接口）。对候选图像区域依次执行：
1. 大津法（Otsu）二值化
2. 将区域划分为网格，提取内部二进制编码（排除外围黑色边框）
3. 计算编码的全部 4 个旋转方向
4. 与字典条目匹配，支持基于汉明距离的纠错（最多容忍 `max_correction_rate × tau` 位错误）

**[markerlabeler.cpp](aruco/src/aruco/markerlabeler.cpp)**

`MarkerLabeler` 的工厂类。根据输入类型，将创建路由到 `DictionaryBased`（标准字典）或自定义/分形标记识别器。

---

### 标记检测流水线

**[markerdetector.cpp](aruco/src/aruco/markerdetector.cpp)**

标记检测的公开 API 门面（采用 Pimpl 模式封装 `MarkerDetector_Impl`）。提供：
- `detect()` — 主入口函数，返回检测到的标记列表，可选姿态估计
- 通过 `Params` 结构体进行完整配置（检测模式、阈值方法、角点精化、字典选择、纠错率、跟踪参数）
- 参数的序列化与反序列化

**[markerdetector_impl.cpp](aruco/src/aruco/markerdetector_impl.cpp)**

检测核心引擎，实现完整的检测流水线：

1. **图像金字塔** — 构建多分辨率金字塔，实现尺度不变检测
2. **阈值化** — 支持自适应阈值（`THRES_ADAPTIVE`）和固定阈值配合大津法自动调优（`THRES_AUTO_FIXED`）；利用多线程并行处理多个阈值窗口
3. **轮廓检测与四边形拟合** — 查找轮廓，用多边形逼近，保留凸四边形；支持封闭标记（腐蚀）模式以处理空心边框
4. **候选预过滤** — 将角点按逆时针方向排序，剔除重复候选和贴近图像边界的候选
5. **透视矫正** — 通过单应性变换将各候选区域映射为规范正方形
6. **标记分类** — 委托 `MarkerLabeler` 解码二进制图案并分配 ID
7. **角点精化** — 可选的亚像素精化（`cv::cornerSubPix`）或基于轮廓线交点的精化，获取更精确的角点坐标
8. **姿态估计** — 调用 `Marker::calculateExtrinsics()`（solvePnP）计算旋转与平移
9. **标记跟踪** — 当检测暂时丢失时，利用空间邻近性跨帧跟踪标记，维护每个标记 ID 的连续检测计数
10. **去重** — 移除同一标记 ID 的重复检测，保留周长较大者

三种检测模式：
- `DM_NORMAL` — 自适应阈值，完整流水线
- `DM_FAST` — 固定阈值，不启用自动尺寸加速
- `DM_VIDEO_FAST` — 固定阈值，启用自动尺寸加速和帧间跟踪

---

### 姿态估计

**[ippe.cpp](aruco/src/aruco/ippe.cpp)**

IPPE（无穷小平面的姿态估计）求解器。给定 4 对共面的 2D-3D 点对应关系，计算由平面歧义性产生的两个有效姿态解：
- `solveSquare()` — 正方形标记专用求解
- `solveGeneric()` — 通用平面物体求解
- 从物点计算到归一化图像坐标的单应性矩阵，然后通过解析分解得到两个"旋转+平移"解
- 按重投影误差对解排序
- 包含 `HomographyHO` — 基于各向同性归一化的单应性估计方法（Hartley-Odysseos 方法）

**[marker.cpp](aruco/src/aruco/marker.cpp)**

标记数据结构（继承 `std::vector<cv::Point2f>`）。存储：
- 角点像素坐标、标记 ID、物理边长（`ssize`）
- 旋转向量（`Rvec`）和平移向量（`Tvec`），表示标记相对于相机的姿态
- `calculateExtrinsics()` — 通过 OpenCV `solvePnP` 计算姿态，支持鱼眼相机模型和 Y 轴垂直模式
- `draw()` — 绘制标记轮廓，角点颜色编码（红=左上、绿=右上、蓝=右下），可叠加 ID 标签
- 二进制流的序列化/反序列化
- OpenGL 模型视图矩阵和 Ogre3D 姿态参数的生成

**[posetracker.cpp](aruco/src/aruco/posetracker.cpp)**

姿态精化与跟踪：
- `MarkerPoseTracker` — 跟踪单个标记的姿态：使用 IPPE 进行初始化，采用 Levenberg-Marquardt 优化（配合 Huber 鲁棒估计器）进行跨帧精化
- `MarkerPoseTrackerMap` — 跟踪已知标记地图（多标记配置）的全局姿态：先求解各标记的独立姿态进行重定位并选出最优全局姿态，再通过多点 PnP 进行精化
- 实现了 Horn 闭式刚体变换方法（基于四元数）

---

### 分形标记

**[fractaldetector.cpp](aruco/src/aruco/fractaldetector.cpp)**

分形标记（嵌套多标记图案）检测器。对标准标记检测进行扩展：
- 通过 `FractalMarkerLabeler` 实现分形标记的专项识别
- 通过单应性映射实现分形内部点的 2D 可视化
- 分形立方体和坐标轴的 3D 可视化，带可见性剔除（仅显示面向相机的点）

**[fractallabelers/](aruco/src/aruco/fractallabelers/)**

分形标记配置系统的子目录：
- `fractallabeler.cpp` — 识别分形图案中各个标记的识别器
- `fractalmarker.cpp` — 表示分形排列中的单个标记
- `fractalmarkerset.cpp` — 定义完整的分形标记配置（多个标记位于不同位置/尺度）
- `fractalposetracker.cpp` — 分形标记集的专用姿态跟踪器，提供内部 3D 点的访问

---

### 工具

**[debug.cpp](aruco/src/aruco/debug.cpp)**

轻量级调试工具，支持可配置的日志级别和用于运行时诊断数据的键-值字符串映射。

**[markermap.cpp](aruco/src/aruco/markermap.cpp)**

标记地图/标定板配置管理器：
- 定义多个标记在统一参考坐标系中的 3D 位置
- 通过 OpenCV `FileStorage` 进行 YAML/XML 序列化
- 像素坐标与米制坐标之间的相互转换
- 将各标记渲染到指定位置，生成可视化的地图图像
- `calculateExtrinsics()` — 根据观测到的地图标记，使用 `solvePnPRansac` 估计全局相机姿态

---

## 架构总览

```
MarkerDetector（公开 API）
  └── MarkerDetector_Impl（核心引擎）
        ├── 图像金字塔 + 多窗口阈值化（并行）
        ├── 轮廓检测 → 四边形候选
        ├── MarkerLabeler（字典匹配 或 分形标记）
        │     └── DictionaryBased（二进制网格提取 + 汉明距离匹配）
        ├── 角点精化（亚像素 或 轮廓线拟合）
        ├── 姿态估计（solvePnP）
        └── 帧间跟踪
              └── MarkerPoseTracker / MarkerMapPoseTracker
                    └── IPPE（初始姿态） + Levenberg-Marquardt（精化）
```

## 依赖

- **OpenCV** — `core`、`imgproc`、`calib3d`、`features2d`
- **Eigen**（可选）— 用于 `LevMarq` 的 Levenberg-Marquardt 优化
- C++11 或更高版本（多线程、lambda 表达式、`std::bitset`）
