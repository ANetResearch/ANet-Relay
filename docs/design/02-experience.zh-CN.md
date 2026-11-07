# ANet Relay 体验设计（设计 v1.0，2026-11-06）

> 本文规定 ANet Relay 长什么样、怎么用：信息架构、布局、每个界面的内容与状态、交互、文案、动效、三维、手柄与穿环。需求编号见 01，接口与安全规则见 03。尺寸单位 px（1 rem = 16 px），时长 ms。
> 视觉 token 全部来自 ANet Graphite v1（ANet4D `apps/web/src/styles/theme.css`，原样复制，不新增色值）。下文写 `g900`、`r500` 等即指该文件中的变量。

## 1. 设计原则

1. **机群是主语。** 打开 Relay 看到的第一件事是"有哪些飞机、各自怎样、谁在控制"，而不是某一架飞机的仪表盘。
2. **控制权是一等公民。** 每架飞机任何时刻都明确显示控制者；附身、认领、交还有专门的视觉语言（§5）和一条贯穿底部的控制权时间线（§4.5）。这是 Relay 区别于 QGC 的签名。
3. **一个红色。** `r500` 只表示两件事：已解锁（Armed）和严重告警；同屏只允许一种红色语义（§2.3）。品牌红只在启动动画的标识里出现。
4. **舞台是空间，面板是事实。** 三维舞台负责"在哪、朝哪、怎么动"；面板负责精确数字。舞台上不堆数字，面板上不画花哨图形。
5. **诚实的空白。** 拿不到的数据显示 `—`，鼠标悬停说明原因（"飞控未发送 BATTERY_STATUS"）。不显示假数据，不显示 0 冒充未知。
6. **危险的事要慢，救命的事要快。** 解锁、写参数要输入确认；急停一键、全局快捷键、手柄组合键都直达。
7. **时刻要有质感。** 启动、手柄接入、穿环、完成航线这四个时刻有专门编排的动效；其余界面安静。

## 2. 视觉系统落地

### 2.1 层级

| 层 | 背景 | 边框 | 用途 |
|---|---|---|---|
| L0 窗口 | `g950` | — | 顶栏、底部时间线、舞台背景 |
| L1 面板 | `g900` | 1px `border`（白 8 %） | 机群栏、检查器 |
| L2 面板内块 | `g850` | 1px `border` | 卡片选中态、输入框、分组 |
| L3 浮层 | `g850`（popover） | 1px 白 12 % | 菜单、命令面板、对话框、提示卡 |
| 舞台 HUD | `hud`（g900 92 %） | 1px `border` | 舞台上的浮层，必须有底，避免文字压在模型上 |

阴影只用于 L3：`0 8px 24px oklch(0 0 0 / 0.35)`，一层。无渐变、无玻璃、无发光。

### 2.2 文字

| 角色 | 字号 / 行高 | 字重 | 颜色 | 字体 |
|---|---|---|---|---|
| 大数字（HUD、计时） | hud-kpi 22 / 28 | 500 | `g50` | Mono |
| 面板标题 | hud-title 13 / 18 | 560 | `g100` | Inter |
| 正文 / 值 | ed-value 12 / 16 | 450 | `g100` | Inter；数字用 Mono |
| 次要说明 | hud-sub 11 / 16 | 450 | `g400` | Inter |
| 小标签（大写） | hud-cap 10 / 12，字距 0.08em | 560 | `g500` | Inter |
| 地址、ID、原始消息 | 11 / 16 | 450 | `g300` | Mono |

数字一律 `tabular-nums`，变化时不抖动。中文与英文同字号；中文界面小标签不用大写与字距。

### 2.3 状态颜色与形状

状态不只靠颜色，每个状态都有形状或文字（色盲可辨）：

| 状态 | 视觉 |
|---|---|
| 链路 ok | 实心圆点 6px `g300` + "Live" |
| 链路 delayed | 空心圆点 `g300` + "Delayed 3.4 s"（秒数实时） |
| 链路 lost | 斜杠圆点 `g50` + "Lost 7 s"；严重告警时文字 `r500`（若当前红色语义空闲，见下） |
| 离线 | 圆点 `g600` + "Offline" |
| 已解锁 | 文字 "ARMED" 560 `r500` + 卡片左侧 2px `r500` 条 |
| 未解锁 | 文字 "Disarmed" `g400` |
| 人工附身 | 卡片 1px `g50` 实线外框 + 胶囊 "YOU · PAD 1"（`g50` 底 `g950` 字） |
| 智能体控制 | 胶囊 "AGENT · harness-a"（`g800` 底、`g200` 字、1px 虚线 `g500` 边） |
| 认领被挂起 | 胶囊 "harness-a · waiting"（同上，文字 `g500`，图标 pause） |
| 无控制者 | 不显示胶囊，显示 "Idle" `g500` |
| 模拟机 | 卡片 1px 虚线边框 + 名称前小标 "SIM"（Mono 10，1px `g600` 框） + 舞台模型半透明线框底座（§6.4） |

**红色唯一规则**（由前端统一的 `useRedOwner()` 计算，所有组件只问它）：
1. 存在未确认的严重告警 → 只有严重告警（告警条、对应卡片条、对应舞台标签）用 `r500`；已解锁改为 `g50` 粗体 "ARMED"。
2. 否则若有已解锁的飞机 → 只有 "ARMED" 文字与卡片条用 `r500`。
3. 否则全屏无红色（启动动画的标识除外）。

### 2.4 间距与尺寸

4px 基数。面板内边距 12；组间距 16；控件高 28（紧凑）/ 32（标准）；圆角 `radius` 7.2px（0.45 rem），胶囊 999px。图标 16（行内）/ 20（按钮）。

### 2.5 图标（morphicons，经 `StateIcon`）

| 位置 | 图标对（alt 状态） |
|---|---|
| 附身按钮 | `hand` ↔ `hand-release`（附身 / 释放） |
| 跟随视角 | `cam.follow` ↔ `cam.free` |
| 图层显隐 | `layer.visible` ↔ `layer.hidden` |
| 复制 | `copy` ↔ `check` |
| 播放回看 | `tl.play` ↔ `tl.pause` |
| 电池 | `battery.full` / `battery.mid` / `battery.low`（按等级） |
| 链路 | `signal` ↔ `signal-off` |
| 手柄 | `gamepad` ↔ `gamepad-off` |
| 解锁 | `lock` ↔ `unlock` |
| 折叠 | `chevron.down` ↔ `chevron.up` |
| 语言 | `globe` |
| 急停 | `octagon`（无形变） |

实现时若 morphicons 无同名图标，取语义最近者，并在 `web/src/ui/icons.ts` 集中登记；禁止散落引用。

## 3. 信息架构

```
窗口
├── 顶栏（工作区切换 · 机群摘要 · 输入设备 · 语言 · 全部急停）
├── 工作区
│   ├── Fly（缺省）：机群栏 │ 舞台 │ 检查器
│   ├── Agents：会话 │ 认领矩阵 │ 接口与文档
│   └── Records：记录列表 │ 单次回看
├── 控制权时间线（底部，三个工作区共享）
└── 浮层：命令面板 ⌘K、设置、接入（扫描 / 直连 / 配网向导）、解锁确认、告警中心、手柄提示、穿环完成
```

快捷键总表：

| 键 | 作用 | 生效范围 |
|---|---|---|
| ⌘K / Ctrl+K | 命令面板 | 全局 |
| ⌘. / Ctrl+. | **全部急停** | 全局，含对话框内 |
| Esc | 关闭最上层浮层；无浮层时 = 选中飞机急停（油门归零 + 申请上锁）；附身中且无浮层 = 急停该机 | 全局 |
| 1 / 2 / 3 | 切到 Fly / Agents / Records | 无文本焦点时 |
| ↑ ↓ | 在机群中切换选中 | 机群栏有焦点或无焦点 |
| P | 附身 / 释放选中飞机（键盘输入源） | Fly |
| W A S D | 俯仰 / 横滚（附身中） | Fly，附身中 |
| Q E | 偏航 | 同上 |
| R F（按住） | 油门升 / 降（松开保持） | 同上 |
| Space | 杆量回中（油门保持） | 同上 |
| C | 舞台视角循环：总览 → 追尾 → 俯视 | Fly |
| G | 开始 / 中止穿环 | Fly，选中飞机 |
| ⌘, | 设置 | 全局 |
| ⌘1 / ⌘2 | 中文 / 英文（亦可在顶栏切换） | 全局 |

## 4. 主窗口

### 4.1 网格

缺省 1440 × 900；最小 1100 × 720。

```
┌──────────────────────────────────────────────────────────────────────────┐ 44  顶栏 L0
├──────────┬───────────────────────────────────────────────┬───────────────┤
│ 机群栏   │                     舞台                       │   检查器       │
│ 296      │                   自适应                       │   360          │
│ L1       │                                               │   L1           │
├──────────┴───────────────────────────────────────────────┴───────────────┤ 104 控制权时间线 L0（可折叠到 28）
└──────────────────────────────────────────────────────────────────────────┘
```

- 外边距：面板与窗口边 8，面板之间 8（舞台无边框，贴满中间列，四角圆角 radius）。
- 机群栏、检查器可拖拽宽度（shadcn resizable）：机群栏 248–360，检查器 320–440；双击分隔条复位。⌥⌘B / ⌥⌘I 折叠机群栏 / 检查器（折叠后舞台变宽，左右边缘留 28 宽的竖条可点开）。
- 1280 × 800：机群栏 264，检查器 336，时间线缺省折叠为 28。
- 1100 × 720：检查器缺省折叠；选中飞机时检查器以 L3 抽屉（sheet）从右侧滑出覆盖舞台。

### 4.2 顶栏（44）

```
[红绿灯留白 76][ANet Relay 字标][   Fly | Agents | Records   ][机群摘要][输入设备][EN/中][■ Stop all]
```

- 左：macOS 留 76 给 hiddenInset 红绿灯（其他平台 12）；字标 = `anet-logo.svg` 反白版（高 14）+ 竖分隔 1px `g700` + "Relay"（13 / 560 / `g100`，字距 −0.01em）。整条顶栏是窗口拖拽区（按钮除外）。
- 中：工作区分段控件（shadcn toggle-group），宽 264，三段等宽，选中段 `g800` 底 `g50` 字。
- 右侧依次：
  - 机群摘要：`6 aircraft · 2 armed · 1 lost`（Mono 11，`g400`；"armed" 数字随红色规则；点击打开告警中心）。
  - 输入设备：手柄图标 + `2 pads`（无手柄时 `No pads`，`g500`）；点击弹出手柄面板（§7.4）。
  - 语言切换：`EN` / `中`（toggle，宽 64）。
  - **Stop all**：高 28、宽 96，`g850` 底、1px `g600` 边、八边形图标 + "Stop all"；hover 边框 `g300`；按下 `g800`。不用红色——它是动作不是状态；它的醒目来自位置固定与形状。有飞机已解锁时，按钮右侧出现 1px `r500` 竖条（受红色规则约束，若红色被严重告警占用则改 `g50`）。

### 4.3 机群栏（Fly，左）

结构：标题行 40 → 列表（滚动）→ 底部操作 92。

**标题行**：`Fleet` 13/560 + 右侧排序菜单（Name / Battery / Link / Controller）与 `+` 按钮（打开接入浮层 §8）。

**列表**：分组 `REAL` 与 `SIMULATED`（hud-cap 小标题，组内计数），真机在上。每架一张卡（高 76，组内间距 4，卡片 L1→选中 L2）：

```
┌──────────────────────────────────────────┐
│▌ Quad 03            ● Live      ARMED     │  行1：名字 13/560 · 链路 · 解锁
│  10.7.7.36 · PX4    STABILIZED   02:14    │  行2：地址 Mono 11 · 固件 · 模式 · 飞行时长
│  ▮▮▮▮▯ 11.6 V 62 %  [YOU · PAD 1]         │  行3：电池条 + 数值 · 控制者胶囊
└──────────────────────────────────────────┘
```

- 左边 2px 状态条：未解锁 `g700`、已解锁 `r500`（按红色规则）、链路丢失 `g50` 斜纹。
- 电池条：5 格，每格 6 × 8，间距 2；格色 `g200`，空格 `g700`；warn 时最后一格闪烁（opacity 1↔0.4，1 s，reduced-motion 下不闪）；critical 按红色规则。
- 卡片右键菜单（context-menu）：Possess / Release、Rename、Calibrate signs、Open in Records、Remove（真机）/ Delete（模拟）。
- 键盘：↑↓ 选择，Enter 打开检查器，P 附身。
- 16 架时：卡片高 76 × 16 + 分组 ≈ 1300，滚动；滚动条用 shadcn scroll-area，细 4px。

**空状态**（无任何飞机）：卡片区居中偏上一段文字（左对齐，宽 220）：

> **No aircraft yet**
> Relay listens on UDP 14550. Aircraft on this network appear here automatically.
> [Scan network]  [Connect by address]  [Add simulated aircraft]

中文：**还没有飞机** / Relay 正在监听 UDP 14550，同一网络中的飞机会自动出现在这里。/ [扫描网络] [按地址连接] [添加模拟机]。

**底部操作（92）**：两行：`Scan network`（全宽 32，扫描中变为进度：`Scanning 10.7.7.0/24 · 126 / 254` + 细进度线）；下行左右两半：`Connect…`、`Add sim`。

### 4.4 舞台（Fly，中）

见 §6。舞台上的叠加层（L-HUD）：

- 左上 16/16：选中飞机身份块：名字 13/560、`10.7.7.36 · PX4 · STABILIZED`、控制者胶囊。
- 右上：视角切换（Overview / Chase / Top，toggle-group，图标 + Tooltip）、图层菜单（网格、安全区、航线、标签、轨迹）。
- 底部居中：选中飞机 HUD 条（宽 ≤ 640，高 64）：`ROLL +01.4°  PITCH −00.8°  YAW 184°  ALT 1.26 m  VS +0.2 m/s  BAT 11.6 V`——6 项，标签 hud-cap、数值 hud-kpi。无数据项显示 `—`。
- 右下：图传画中画（§9），缺省 320 × 180，可拖角缩放到 640 × 360、可拖到四角吸附、可关闭。
- 顶部居中：穿环 HUD（§10），仅在航线进行中。
- 附身中：舞台左下角出现杆量可视化（两个 72 × 72 方框内的点 + 油门竖条），见 §5.3。

### 4.5 控制权时间线（底部，三个工作区共享）

Relay 的签名元素：一眼看到人与 AI 之间的交接。

- 高 104（展开）/ 28（折叠，只留标题与当前摘要）。⌥⌘T 折叠切换。
- 左 160 为飞机名列，右侧为时间轴：缺省最近 5 分钟，右端为"现在"，每秒左移；可选 1 / 5 / 15 分钟。
- 每架飞机一条泳道（高 14，间距 4；超过 5 架时泳道可滚动）：
  - 智能体控制：`g500` 实心段，段上 Mono 9 写会话名（宽度够时）。
  - 人工附身：`g50` 实心段。
  - 认领被挂起（人在附身）：附身段下方 2px `g500` 虚线，表示"智能体在等"。
  - 无控制者：空。
  - 已解锁区间：泳道底部 1px 线（`r500`，受红色规则）。
  - 事件刻度：交还（↺ 小三角 `g50`）、急停（竖线 `g50`）、坠毁、链路丢失（斜纹）。
- 悬停显示时间与事件；点击跳到 Records 的对应时刻。
- 标题行：`Relay timeline` + 当前摘要 `2 agent · 1 human · 3 idle`。

## 5. 附身与接力（核心交互）

### 5.1 入口

- 检查器 Control 页的主按钮 **Possess**（全宽 36，`g50` 底 `g950` 字——全应用唯一的实心浅色主按钮）。
- 快捷键 P（键盘输入源）。
- 手柄：按住 Options 1 s（附身"当前选中且未被其他输入源附身"的飞机；手柄面板可预先绑定）。
- 机群卡右键 / 命令面板 "Possess Quad 03"。

### 5.2 附身的过程

1. 若该机由智能体控制：按钮下方提前显示一行 `harness-a is flying this aircraft. Possessing suspends its claim.`（中文：`harness-a 正在控制这架飞机。附身会挂起它的认领。`）。不再二次确认——抢占是产品核心，必须快。
2. 进入附身：卡片外框 `g50` 实线出现（quick 150 ms）；胶囊变为 `YOU · KEYBOARD` / `YOU · PAD 1`；舞台标签同样；时间线该泳道开始 `g50` 段，原智能体段下方出现等待虚线。
3. 进入附身时的模式处理（03 §5.4）：若当前模式不接受杆量（GUIDED、AUTO、OFFBOARD 等），Relay 切换到设置中的"附身模式"（缺省：有定高能力 → ALT_HOLD / ALTCTL，否则 STABILIZE / STABILIZED），并在 toast 中说明 `Mode set to ALT_HOLD for manual control.`；油门杆中位 = 该机悬停点（§5.5），保证切换瞬间不掉高。
4. Toast（底部居中）：`You are flying Quad 03 · PAD 1` 2 s。

### 5.3 附身中

- 检查器 Control 页显示：杆量可视化（左杆 yaw/throttle、右杆 roll/pitch，方框 96 × 96，1px `g700`，十字线 `g800`，点 8px `g50`；油门竖条 8 × 96，悬停点刻度 `g400`）；当前模式 select；Arm / Disarm；Release。
- 键盘输入源时，舞台左下显示按键提示（Kbd 组件）：`W A S D  Q E  R F  Space  Esc`。
- 屏幕摇杆：Control 页的两个方框可直接拖动（指针捕获，松手回中，油门不回中）。
- 窗口失焦：杆量立即回中且停发（油门保持值仍显示但不发送），舞台顶部提示 `Input paused · window not focused`；回到焦点后需动一下杆才恢复发送。
- 链路 delayed：卡片与 HUD 显示 `Delayed`，继续发送；lost：停止发送，舞台中央出现 L3 提示卡 `Link lost · Quad 03 · sticks stopped`，附身保持；恢复后提示 `Link restored · move a stick to resume`。

### 5.4 释放与交还

- 释放：Release 按钮 / P / 手柄 Options 按住 1 s / 右键菜单。
- 若有被挂起的认领且会话存活：
  - 卡片胶囊从 `YOU · PAD 1` 形变为 `AGENT · harness-a`（胶囊宽度过渡 fast 250 ms，文字交叉淡入）；
  - 舞台标签同步；时间线插入交还刻度 ↺；
  - Toast：`Quad 03 handed back to harness-a`（中文：`Quad 03 已交还给 harness-a`），3 s。
- 若没有认领：胶囊消失，`Idle`；toast：`Released Quad 03 · no agent holds it`。若飞机仍在空中且无人控制：**常驻告警（warn）** `Quad 03 is airborne with no controller`，直到有人或智能体接管、或降落。

### 5.5 标定（Control 页下部 "Calibration" 折叠组）

- 通道符号：三行 Roll / Pitch / Yaw，各一个 `+1 / −1` 切换；说明文字：`Remove the propellers. Push each stick and check the aircraft's reported RC input or attitude target moves the right way.`；底部 `Mark signs as verified`（写入确认，03 §6）。
- 悬停点：滑块 0.20–0.70，缺省模拟机 0.50、真机 0.35；说明：`Throttle at stick centre. Adjust after a short hover.`。

## 6. 三维舞台

### 6.1 渲染

- three 0.186 WebGLRenderer（与 GLTFLoader 同一 three 包），antialias，像素比上限 2（≥ 8 架时 1.5），色调映射 ACES，sRGB。目标 60 fps；16 架时 ≥ 45 fps（M1 MacBook Air 量级）。
- 场景：背景 `scene-clear`（g950）；地面无限网格：细线 0.5 m `scene-grid-minor`，主线 2.5 m `scene-grid-major`，向远处线性淡出（半径 30 m）；原点处 1 m 十字 `g600`。
- 灯光：半球光（天 g200 / 地 g950，强度 0.6）+ 平行光（从东南上方，强度 1.4，投影 1024 贴图只投在 4 m 半径内）+ 接触阴影（drei ContactShadows，模糊 2.4，opacity 0.35）——保证模型有落地感。
- 安全区：3 × 3 × 2 m（设置可调）线框立方体，1px `g700`，地面投影矩形 `g800`；仅在有飞机具备位置时显示，或在图层菜单中强制显示。

### 6.2 模型

- Caddx GoFilm 20 glTF，真实尺寸（≈ 133 × 123 × 39 mm），不缩放、不重新着色；坐标与桨叶节点见 `refs/demo-anet4d-gcs/docs/desktop-design.md` §6（文件正面 +Z，挂到父级时 `setFromUnitVectors(+Z, 机头 −Z)`）。
- 桨叶：未解锁静止；解锁后转速 = 8 + 40 × 油门（转 / 秒，视觉上转盘化）；加运动模糊盘（半透明圆盘，opacity 0.08 × 油门）。
- 机头标记：机头前方 3 cm 一个 2 × 2 cm 的 `g50` 小三角贴片（便于远看分辨朝向）；已解锁且红色语义空闲时为 `r500`。
- 选中：模型下方地面一个 0.24 m 半径的 1px `g50` 圆环；附身：圆环加一圈外环（0.30 m）。
- 每架飞机头顶标签（屏幕空间，drei Html，不随距离缩放）：名字 Mono 11 `g100`，下一行控制者小字；ARMED 时名字前加 `ARMED`（按红色规则）；链路丢失时标签变 `g500` 并加 `Lost 6 s`。
- 高度垂线：离地 > 0.2 m 时，从机体到地面 1px `g600` 线，地面端 Mono 10 写高度。
- 轨迹：最近 20 s，1px，`trail-default`；选中飞机 `trail-selected`。

### 6.3 摆放

- **有共同坐标系的飞机**（模拟机；同一本地坐标原点的真机；都有 GPS 3D 定位的真机按首架为原点投影）：按位置摆放；位置与姿态做 120 ms 指数平滑（显示用，不影响数据）。
- **没有位置估计的真机**：放在"停机坪槽位"——舞台南侧一排，间距 0.6 m，从西到东按机群顺序；槽位地面画 0.4 × 0.4 m 虚线方框，框边 Mono 9 写 `No position`；姿态照常实时。
- 模拟机出生点：2 m 网格（4 × 2），出生点画虚线方框与编号。

### 6.4 模拟机区分

模型照常渲染，另在机体下方 2 cm 加一个线框底座（0.18 m 方形，1px `g500` 虚线），标签前加 `SIM`。真机永远没有底座。

### 6.5 视角

| 视角 | 相机 | 说明 |
|---|---|---|
| Overview（缺省） | fov 32；目标 = 所有可摆放飞机的包围盒中心；距离使包围盒（最小 4 × 4 m）占视口宽 60 %；俯角 32°；方位 −35°（从东南看） | 1 架时一架约占画面宽 1/10；16 架时全部入画。可轨道拖拽、滚轮缩放；双击飞机 = 选中并平滑飞到它（medium 350 ms）。 |
| Chase | 选中 / 附身飞机后方 1.2 m、上方 0.45 m，看向机前 2 m；跟随偏航（平滑 200 ms），不跟随横滚俯仰 | 像 FPV 模拟器的第三人称；附身并开始穿环时自动切入（可在设置关闭）。无位置估计的真机不可用（按钮禁用并 Tooltip 说明）。 |
| Top | 正上方 8 m，正交投影 | 看队形与航线 |

视角切换用相机插值（slow 400 ms，smooth-out）；reduced-motion 直接切。

## 7. 手柄

### 7.1 首次交互门

浏览器在用户与页面交互之前读不到手柄。Relay 启动后若未交互：顶栏输入设备区显示 `Click to enable controllers`（`g400`，可点击）。交互后消失。

### 7.2 接入提示（R-42）

手柄接入是一个"时刻"。提示卡（L3，底部居中，距底 120，宽 400，高 88，圆角 radius × 1.4）：

```
┌───────────────────────────────────────────────────────┐
│  [手柄线描 48×32]   DualSense connected                 │
│                     Controller 1 · flying SIM-1          │
│                     [Fly the ring course]   [Dismiss]    │
└───────────────────────────────────────────────────────┘
```

- 手柄线描：自绘 SVG（1.25px 线，`g50`），入场时线条描出（stroke-dashoffset，very-slow 500 ms，smooth-out），描完后两个摇杆圆点依次亮起（`g50`，stagger 80 ms）——DJI 设备接入那种"被识别"的仪式感，但只用线与明度。
- 卡片入场：opacity 0→1 + translateY(12→0)，fast 250 ms，smooth-out；同时舞台中被绑定的那架飞机的选中圆环做一次扩散（半径 0.24→0.5 m、opacity 0.8→0，medium 350 ms）。
- 文案：
  - 第一行：`DualSense connected`（识别到 DualSense）/ `Controller connected`（其他标准映射手柄）/ `Controller connected · unrecognised layout`（非标准映射，按钮可能不对）。
  - 第二行：`Controller 1 · flying SIM-1`（已自动绑定，§7.3）/ `Controller 2 · hold Options to possess the selected aircraft` / `Controller 3 · no free aircraft`。
  - 按钮：`Fly the ring course`（没有模拟机时文案为 `Add a sim and fly the ring course`）；`Dismiss`。
- 6 s 后自动离开（opacity + translateY(8)，fast），悬停时暂停计时。多个手柄依次接入：卡片纵向堆叠（间距 8，最多 2 张，第三张替换最旧的）。
- 中文：`DualSense 已连接` / `手柄 1 · 正在控制 SIM-1` / `手柄 2 · 按住 Options 附身当前选中的飞机` / `手柄 3 · 没有空闲的飞机` / `开始穿环练习` / `添加模拟机并开始穿环` / `关闭`。
- reduced-motion：无描线、无扩散，直接出现。

### 7.3 绑定规则

- 手柄按接入顺序编号 1–4（第 5 个提示 `Only 4 controllers can be used at once`）。
- **自动绑定只针对模拟机**：新手柄接入时，若存在未被任何输入源附身的模拟机，自动附身编号最小的那架（这正是"插上手柄就能飞"）。真机永不自动附身——必须用户按住 Options 或点击 Possess。
- 一个手柄同一时刻附身一架；按住 Options 1 s：若本手柄未附身 → 附身当前选中飞机；若已附身 → 释放。
- 手柄断开：杆量立即停发；附身保持 3 s 等待重连（同一手柄 id 重连则恢复），超时后释放（触发正常交还流程）。断开期间卡片胶囊显示 `PAD 1 · disconnected`，舞台提示卡 `Controller 1 disconnected · Quad 03 sticks stopped`。模拟机断开时自动切 LOITER 悬停（模拟飞控行为）；真机按飞控自身失控保护。

### 7.4 手柄面板（顶栏输入设备点击）

popover，宽 320：每个手柄一行（编号、型号、电量若可读、绑定飞机 select、实时输入小预览：两个 24 × 24 摇杆点）、映射说明链接、死区滑块（0.04–0.15，缺省 0.08）、`Expo`（0–0.6，缺省 0.3）。

### 7.5 映射（Mode 2，DualSense 标准映射）

> v0.3.0 起：△ 改为"悬停辅助"、R1 射击、十字键上 / 下切换手感档位，并新增 Mode 1、遥控器与映射学习，见 `05-v030.zh-CN.md` §6。

| 输入 | 作用 |
|---|---|
| 左摇杆 上下 | 油门：分段线性，最下 0、中位 = 悬停点、最上 1（自稳类模式）；定高 / 定点类模式下中位 = 保持高度（飞控处理） |
| 左摇杆 左右 | 偏航 |
| 右摇杆 上下 / 左右 | 俯仰 / 横滚 |
| Options 按住 1 s | 附身 / 释放 |
| L1 + R1 按住 1 s | 解锁 / 上锁（**仅模拟机**；真机上按下时提示 `Type ARM in Relay to arm a real aircraft`） |
| L2 + R2 同时按到底 | **急停**本机（立即，无需按住） |
| △ | 模式：自稳类 ↔ 定高类 |
| □ | 开始 / 中止穿环 |
| ○ 按住 1 s | 模拟机：复位到出生点（上锁状态下） |
| 十字键 左 / 右 | 未附身时切换选中飞机 |
| 触控板按下 | 视角循环 |
| × | 无作用（防误触） |

按住类操作的进度：在该机机群卡底边与舞台标签下显示 1px `g50` 进度线，从左到右，时长即按住时长；松手取消，无残留。

## 8. 发现与接入（`+` 浮层，dialog 宽 560）

三个标签页：**Scan** · **Address** · **Set up a new bridge**。

- Scan：列出本机网卡与网段（`en0 · 10.7.7.5 / 24`），`Scan` 按钮，结果列表（地址、数传版本、是否已在机群）。扫描中显示进度线与计数。
- Address：IPv4 输入（Mono）+ 端口（缺省 14555）+ `Connect`；校验错误行内显示。
- Set up a new bridge（配网向导，R-04），四步，顶部步骤条（1 Connect to the bridge · 2 Read · 3 Configure · 4 Rejoin）：
  1. **Connect to the bridge**：说明 `Join the aircraft's Wi-Fi hotspot (for example anet4d-a01) on this computer.`；Relay 检测本机是否在 192.168.4.0/24 上：是 → 显示 `Connected to 192.168.4.1` 并自动下一步；否 → 显示等待状态与 `I have joined it`。
  2. **Read**：读取数传当前设置（固件版本、模式 AP/Station、AP SSID、Station SSID、端口、波特率），只读表格。
  3. **Configure**：表单——Wi-Fi mode `Station`（缺省选中）、Network name（缺省 = 用户上次输入，或从本机最近的非 192.168.4.x 网络推断，不能推断则空）、Password（密码框，可显示）、IP 获取 `DHCP`（IP / 网关 / 子网 0.0.0.0；可展开改为固定 IP）、Host port 14550、Client port 14555、Baud 保持读到的值、AP 热点名与密码保持（作为回落）。底部说明 `If the bridge cannot join this network, it falls back to its own hotspot after a timeout.`。按钮 `Write and restart bridge`。
  4. **Rejoin**：`Switch this computer back to <network>. Relay will look for the aircraft.`；Relay 轮询扫描，发现新飞机时显示 `Found 10.7.7.36` 与 `Done`；60 s 未发现给出排查清单（2.4 GHz、密码、AP 隔离、DHCP 租约）。
- 未知固件：第 2 步读不到参数 → 显示 `This bridge does not expose a known setup interface.` 与 `Open its setup page`（在 Relay 内的独立窗口打开 `http://<ip>/`）。

## 9. 图传（R-17）

- 检查器 Overview 页的 "Video" 组：`Video source` select（列出 `navigator.mediaDevices` 的视频输入，例如 `USB Video`），`Bind to this aircraft`。绑定按设备标签记住。
- 显示：舞台画中画（§4.4）+ 检查器 Overview 顶部 16:9 缩略（点击放大为舞台画中画）。
- 状态：无设备 `No video device` / 未授权 `Camera access is off · Open system settings` / 无信号（黑帧 > 2 s）`No signal`；角标 `LIVE` `g50` 小字；延迟不估计不显示。
- 画中画不遮挡穿环 HUD 与急停。

## 10. 穿环航线（R-43、R-44、R-45）

### 10.1 入口

- 检查器 Control 页 "Ring course" 组：航线 select（`Cage loop · 4 rings`、`Slalom · 6 rings`、`Climb · 5 rings`、自定义…）、`Start` / `Stop`、最佳成绩。
- 快捷键 G、手柄 □、手柄接入提示的按钮、命令面板。
- 真机无位置估计：组内显示 `Needs a local position estimate (optical flow, motion capture or UWB). This aircraft reports none.`，Start 禁用。

### 10.2 环的造型

- 环 = 圆环体，内半径 = 航线半径（缺省 0.45 m），管半径 1.2 cm；材质 MeshStandard（metalness 0.1、roughness 0.6），颜色按状态；每个环底部一根 1px 竖线落地（`g700`），地面一个 Mono 10 序号。
- 状态：
  - **下一个环**：`g50`，环外侧 4 个 3 cm 刻度（时钟 12/3/6/9 位）缓慢旋转（20 s 一圈，reduced-motion 下静止）——用形状而不是颜色强调；从飞机到下一环中心一条 1px 虚线 `g400`（只对选中 / 附身飞机）。
  - **之后的环**：`g500`，opacity 0.7。
  - **已通过**：`g700`，opacity 0.35。
- 不使用红色（红色留给已解锁与危险）。

### 10.3 过环瞬间

- 环体：亮度闪到 `white`（micro 80 ms），随后整体放大 1.0→1.18 并淡出到"已通过"状态（medium 350 ms，smooth-out）。
- 环中心弹出分段时间 `+3.42`（Mono 13，`g50`，数字跳入配方 digit pop-in，500 ms 后淡出）。
- 顶部 HUD 的计数与分段同步跳动。
- reduced-motion：只做颜色切换与数字替换。

### 10.4 计时 HUD（舞台顶部居中，宽 480，高 56，HUD 底）

```
 CAGE LOOP      RING 3 / 4      00:12.48      SPLIT +3.42     BEST 00:18.20
```

hud-cap 标签 + Mono 数值（总时 hud-kpi）。等待起飞时显示 `Take off to start`；计时从离地（03 §8）开始。多架同时挑战：HUD 跟随选中飞机；其他挑战中的飞机在机群卡第 3 行显示 `Ring 2/4 · 00:07.1`。

### 10.5 完成

舞台中央 L3 卡片（宽 360）：`Course complete` / 大数字总时间 hud-kpi × 1.5（Mono 33）/ 分段列表（每环一行，lieflat 表）/ `New best` 标记（若是）/ `Fly again` `Done`。入场：scale 0.97→1 + opacity，fast。智能体完成的航线不弹卡，只发 toast `harness-a finished Cage loop in 00:21.37`。

### 10.6 中止

上锁、坠毁、链路丢失、位置估计丢失、用户 Stop：HUD 变为 `Aborted · <原因>` 2 s 后消失，成绩记为 aborted。

## 11. 检查器（Fly，右）

标题区（高 64）：名字（可双击重命名）+ 类型标 + 控制者胶囊；第二行地址、固件、sysid。下方 tabs（高 36，5 个）：**Overview · Control · Telemetry · Parameters · Log**。未选中飞机时检查器显示 `Select an aircraft` 与快捷键提示。

- **Overview**：起飞前检查摘要（`Preflight · 2 blocking`，点开跳到 Control）、关键数值网格（2 列：电池 / 链路 / 模式 / 着陆状态 / 高度 / 飞行时长 / GPS / 位置能力）、视频缩略、最近 3 条状态文本、能力（Manual · Altitude hold · Position，三个 badge，没有的用 `g600` 文字加删除线）。
- **Control**：附身区（§5）、Arm / Disarm（Arm 点击后行内展开 74 高的确认区：`Type ARM to arm Quad 03.` + Mono 输入 + `Arm`，只有输入完全等于 ARM 时可点；模拟机直接一步 `Arm`）、起飞前检查清单（每项一行：图标 + 文字 + 状态；人工确认项带 `Confirm` 按钮）、模式 select、技能按钮（Take off 1.0 m · Land · Hold，只在能力具备时出现）、Ring course、Calibration（§5.5）、Motor test（折叠，真机未解锁时可用：选择电机 1–4、油门 5–15 %、2 s，`Type MOTOR to run` 确认）。
- **Telemetry**：分组（Attitude、Position & velocity、Battery、GPS、Sensors（表：名称 / 存在 / 启用 / 健康）、RC & servo outputs（8 通道条形）、Link（rx / tx Hz、丢包、心跳年龄、来源））；`Raw MAVLink` 子页：消息名列表（名称、频率、最后时间），点开显示字段表（Mono），可暂停。
- **Parameters**：搜索框 + 表（名称 Mono / 值 / 类型）；虚拟滚动；改值：行内编辑 → 行下展开确认 `Type <NAME> to write.` → 写入 → 回读一致显示对勾（copy→check 形变）。顶部 `Refresh`、进度 `Received 812 / 812`。
- **Log**：本机状态文本 + 本机事件（附身、交还、模式、解锁、告警），时间倒序，severity 用文字等级（`EMERG` … `DEBUG`，`CRIT` 以上按红色规则）。

## 12. Agents 工作区

- 左（360）：会话列表（名字、存活 / 过期倒计时 `lease 12 s`、认领的飞机数、最近一次调用时间）。空状态：`No agent sessions` + 一段 6 行的 Python 快速示例（Mono，可复制）+ `Open API reference`。
- 中：**认领矩阵**——行 = 飞机，列 = 会话 + "Human"；格子里是状态点（认领中 / 挂起 / 附身）。下方：选中会话的最近 50 次 API 调用（时间、方法、路径、结果码、耗时），lieflat 表。
- 右（320）：接口信息：`API http://127.0.0.1:8765/api/v1`（复制）、`WebSocket …/stream`、`OpenAPI …/openapi.json`、`MCP: anet-relay-mcp`（安装命令复制）、文档链接；限速与租约说明。

## 13. Records 工作区

- 左（320）：会话列表（日期时间、飞机名、时长、最大高度、是否有穿环成绩、是否有告警），按日分组；搜索。
- 右：单次回看：
  - 顶部摘要（时长、飞行时长、电压起止、最大高度、附身次数、交还次数、穿环最佳）。
  - 曲线：电压、高度、横滚 / 俯仰、油门（lieflat 线图，共享时间轴，高各 96）。
  - 事件轨与控制权轨（与底部时间线同一视觉语言）。
  - 时间游标：拖动时所有图联动；可回放（tl.play / tl.pause），舞台在回看模式下显示当时的姿态（Fly 工作区不受影响——回看时舞台临时进入"Replay"状态，左上角写 `Replay · 14:02:31`，按 Esc 退出）。
  - 穿环成绩表。
  - `Export CSV` `Export JSON`。

## 14. 告警

| 级别 | 呈现 | 例 |
|---|---|---|
| info | toast 3 s | 已连接、已交还 |
| warn | toast 5 s + 机群卡标记 + 告警中心 | 电压 warn、链路 delayed、空中无控制者 |
| critical | 顶栏下方常驻告警条（高 32，`g900` 底，左侧 2px `r500` 条，文字 `g50`，右侧 `Acknowledge`）+ 卡片 + 舞台标签；多条时只显示最新一条并计数 `+2` | 链路 lost（已解锁时）、电压 critical、坠毁、14550 被占用 |

告警中心（顶栏摘要点击）：popover 列表，可逐条确认；确认只关闭视觉打扰，不改变状态。

## 15. 启动（R-62）

> v0.3.0 起，开屏由 `05-v030.zh-CN.md` §2"导演剪辑"覆盖；本节描述的 1.2 s 版本保留为设置中的 `Minimal` 选项，失败与慢启动规则不变。

DJI 式的质感来自"克制的仪式"：只有标识、只有明度、节奏准确。

时间线（总 1200 ms，后端就绪更晚时在第 4 段等待）：

| 时间 | 元素 | 动作 |
|---|---|---|
| 0 | 全窗 `g950`（index.html 内联，JS 未加载即生效） | — |
| 120–520 | `anet-logo.svg`（宽 168，居中偏上 −24） | 从左到右的遮罩揭开（clip-path inset 100%→0），同时 opacity 0→1；400 ms，smooth-out |
| 420–820 | 字标下方 20 处 "RELAY"（Inter 300，13，字距 0.42em，`g300`） | opacity 0→1，letter-spacing 0.6em→0.42em；400 ms，smooth-out |
| 520–1200 | 底部居中（距底 64）一条 160 × 1 的发丝进度线（`lf-track` 底、`g50` 填充） | 绑定后端就绪进度：Electron 主进程启动后端 → 健康检查通过 → WebSocket 第一帧 |
| 就绪后 | 整个遮罩 | opacity → 0，fast 250 ms；主界面三块面板依次入场（translateY 6→0 + opacity，fast，stagger 40 ms：机群栏 → 舞台 → 检查器 → 时间线） |

- 后端 8 s 未就绪：进度线下方出现 `Starting services…`（11，`g500`）；20 s：`The backend did not start.` + `View log` `Retry` `Quit`（按钮为 L1 小按钮）。
- 端口 8765–8775 全被占用：`Ports 8765–8775 are in use. Close other Relay instances.`
- 运行中后端崩溃：主界面上方覆盖 L3 卡 `Relay lost its backend.` + `Restart backend` `View log`；飞机无法控制的事实写清楚：`Sticks are not being sent. Aircraft follow their own failsafes.`
- reduced-motion：标识与字标直接出现，300 ms 后淡出，总 ≤ 400 ms。

## 16. 设置（dialog，宽 640，左侧分类）

General（语言、单位 m / ft、启动时视角）· Safety（安全区尺寸、电压门限每节 warn / critical、附身模式、空中无控制者告警开关、杆量超时只读 0.8 s）· Controllers（死区、Expo、映射说明）· Network（监听端口 14550、数传端口 14555、API 端口只读与复制）· Data（数据目录、打开目录、记录保留天数）· About（版本、后端版本、许可、链接 GitHub / ANet4D）。

## 17. 文案原则与核心词表

- 语气：企业级、工业级、陈述句；不用感叹号、不口语、不卖萌；按钮用动词开头。
- 英文句首大写，其余小写（Sentence case）；中文不加句末句号于按钮与标签。

| 键 | EN | ZH |
|---|---|---|
| status.live | Live | 在线 |
| status.delayed | Delayed | 延迟 |
| status.lost | Lost | 丢失 |
| status.offline | Offline | 离线 |
| status.armed | ARMED | 已解锁 |
| status.disarmed | Disarmed | 未解锁 |
| status.idle | Idle | 空闲 |
| ctrl.you | YOU | 我 |
| ctrl.agent | AGENT | 智能体 |
| ctrl.waiting | waiting | 等待中 |
| kind.sim | SIM | 模拟 |
| action.possess | Possess | 附身 |
| action.release | Release | 释放 |
| action.arm | Arm | 解锁 |
| action.disarm | Disarm | 上锁 |
| action.stop | Stop | 急停 |
| action.stopAll | Stop all | 全部急停 |
| ws.fly / ws.agents / ws.records | Fly / Agents / Records | 飞行 / 智能体 / 记录 |
| relay.handback | {aircraft} handed back to {agent} | {aircraft} 已交还给 {agent} |

完整键集在实现中维护于 `web/src/i18n/{en,zh}.ts`，两者键集必须一致（测试保证）。

## 18. 动效清单

| 动效 | 触发 | 属性 | 时长 / 缓动 | reduced-motion |
|---|---|---|---|---|
| 启动（开屏，v0.3.0） | 应用启动 | 05 §2.3：体素组字、斜切入片、影片、等待、体素揭开；设置 → General → `Opening sequence`（Full / Short / Minimal） | Full ≈ 5.6 s、Short ≈ 3.2 s、Minimal = §15（1200 ms）；`--boot-*` token | 不加载影片；标识与字标直接出现，300 ms 后揭开，总 ≤ 400 ms |
| 体素载入 `VoxelLoader` | 面板级加载（记录列表、回放、参数表、航线、Copilot 会话） | 3 × 3 × 3 体素逐块点亮再熄灭（transform + opacity），占一个循环名额 | `--voxel-cycle` 1200 循环 | 静态立方体 |
| 体素擦除 `VoxelWipe` | 工作区切换、打开回放 | 内容区内 16 px 斜向体素波，`g850`→透明 | `--voxel-wipe` 300 | 关闭 |
| 舞台载入 `StageLoading` | 三维舞台首次挂载、GLB 载入 | 40 px `VoxelLoader` + `Loading aircraft model`，就绪后体素散开淡出 | `--voxel-scatter` 350 | 无散开（软件渲染同） |
| 面板入场 | 启动完成 | translateY 6→0、opacity | fast、smooth-out、stagger 40 | 无 |
| 卡片选中 | 点击 | 背景 | quick | 同 |
| 附身框 | 附身 / 释放 | 外框 opacity | quick | 同 |
| 胶囊形变 | 控制者变化 | 宽度 + 文字交叉淡入 | fast | 直接替换 |
| 数字变化 | 值变化（计时、计数） | digit pop-in | 500 / bounce | 直接替换 |
| Toast | 出现 / 消失 | translateY 8、opacity | fast / quick | opacity |
| 浮层 | 打开 / 关闭 | scale 0.97→1、opacity | fast / quick | opacity |
| 手柄接入 | 接入 | §7.2 | §7.2 | 无描线 |
| 过环 | 通过 | §10.3 | §10.3 | 颜色切换 |
| 视角切换 | C / 按钮 | 相机插值 | slow 400 | 直接 |
| 电池 warn 格 | 告警 | opacity | 1000 循环 | 静止 |
| 下一个环刻度 | 航线中 | 旋转 | 20 s 一圈 | 静止 |

## 19. 应用图标（R-63）

- 源：ANet4D-swarm 图标（深石墨圆角方块、四架白色线描四旋翼沿红色曲线穿过等距线框立方体），`brand/icon-1024.png`。
- macOS：1024 画布内 824 × 824 圆角矩形（连续曲率近似，圆角半径 185），居中，外围完全透明；边缘做 4× 超采样抗锯齿，**不得**出现深色或浅色描边（旧版黑边的成因是原图圆角方块铺满画布、被系统再次加底）。生成 16–1024 全尺寸（含 @2x）的 icns。
- Windows ico（16–256）、Linux png（512）、关于窗口用同一源。
- Dock 中的视觉体量与系统应用一致（这正是 824 栅格的目的）。

## 20. 走查清单（发布前逐条过）

1. 1440 × 900、1280 × 800、1100 × 720 三种尺寸下无裁切、无重叠、无横向滚动。
2. 中英文切换后无溢出（中文字符宽、英文词长两种情况）。
3. 全屏任何时刻至多一种红色语义。
4. 每个面板都有空 / 加载 / 错误状态。
5. 急停在所有浮层打开时可用（键盘与按钮）。
6. 模拟机在卡片、舞台、时间线、记录中都可区分。
7. 所有 "—" 都有悬停原因。
8. reduced-motion 下所有动效降级。
9. 无 emoji、无非 Graphite 色值、无非 shadcn 组件、图标全部经 StateIcon。
10. 启动到可操作 ≤ 2.5 s（后端冷启动在 M1 上）。
