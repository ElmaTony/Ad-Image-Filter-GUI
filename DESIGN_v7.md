# 广告图过滤 · GUI v7 重做设计方案

> 依据三个来源的**真实抓取值**（非推测）：
> - **A｜CDS Storybook**（`claude-storybook.bystanderss.chatgpt.site`，无头 Chrome 抓取渲染后 DOM + 内联 React 源码，612KB 落盘）
> - **B｜claudecodeui**（`github.com/siteboon/claudecodeui` @0ce5630 前端源码）
> - **C｜open-claude-ui**（`github.com/Caho1/open-claude-ui`，CDS 仓库本体，用于交叉验证）
>
> 三个骨架方案**共用下面第 1–3 章的 token / 组件 / 动效**，只有第 4 章的骨架不同。
> 也就是说：无论选哪个，视觉品质都由同一套真实规格保证，差别只在信息怎么摆。

---

## 1. 设计 Token（来源 A，源码原值）

### 1.1 品牌与 Surface
| 用途 | 浅色 | 深色 |
|---|---|---|
| Clay 品牌色 | `#d97757` | 同 |
| Clay hover | `#cc5a36` | 同 |
| Clay 浅色衬底 | `#fbf3ef` | — |
| Surface 0（底基/侧栏） | `#f5f5f3` | `#0b0b0a` |
| Surface 1（主画布） | `#fcfcfb` | `#151515` |
| Surface 2（卡片） | `#ffffff` | `#1a1a19` |
| Surface 3（输入框/最高层） | `#ffffff` | `#20201e` |
| 应用根背景 | `#f8f9fa` | `#0f0f0f` |

10 级暖灰阶（源码 Colors story）：
`#ffffff` / `#fcfcfb` / `#f5f5f3` / `#dedcd6`(100) / `#a4a19b`(300) / `#6b6863`(500) / `#373634`(700) / `#20201e`(800) / `#151515`(850) / `#0b0b0a`(900)

分隔线一律 1px：浅 `rgba(0,0,0,0.08)`、深 `rgba(255,255,255,0.10)`

### 1.1b 深色分层完整表（来源 A，实际用法；**全部偏暖，非中性黑**）
| 部位 | 值 |
|---|---|
| body / Canvas 舞台底 | `#0f0f0f` |
| 根容器 | `#121212` |
| 左侧栏 | `#181818` |
| 顶栏 header | `#1e1e1e` |
| Canvas 卡片 | `#151515` |
| Composer / 输入框 | `#20201e`（R32 G32 B30） |
| 工具条 / 输入框底 | `#282828` |
| 激活态按钮底 | `#383838` |
| 侧栏激活项底 | `#2a2a2a` |

### 1.1c 选中态（两套并存，来源 A）
```css
/* A 中性选中：侧栏导航项 / 工具条 */
浅: bg-neutral-200/80 + text-neutral-900 + font-semibold + shadow-xs
深: bg-[#2a2a2a]      + text-white       + font-semibold
/* B 品牌选中：卡片 / 徽章 / 分段开关 */
徽章:     bg-clay/10 text-clay font-medium
卡片选中: border-clay bg-clay/5（深色 clay/10）
分段激活: bg-clay/10 border-clay/40 text-clay font-bold
```

### 1.1d 与 claudecodeui 的冲突及决策（来源 A×B 交叉）
| 维度 | claudecodeui | storybook(CDS) | **本方案决策** |
|---|---|---|---|
| 主色 | `#2563eb` 蓝（自选） | `#d97757` 陶土橙（Anthropic 官方） | **取陶土橙**——目标是"像 Claude"，且本项目一路沿用 |
| 暗色底 | `#141414` 中性黑 | 暖黑 `#0b0b0a–#20201e` | **取暖黑**——橙配中性黑会发脏 |
| 分层 | 1px 边框 + 8% 底，几乎无投影 | inset 描边 + 外投影 | 容器选中统一用 clay 低饱和填充（等价于对方 primary/5 + border/20），仅导航项用亮底+轻投影 |

### 1.2 圆角（scale）
| 名 | 值 | 用法 |
|---|---|---|
| XS | 5px | Tag、复选框 |
| SM | 6px | 小按钮、Kbd |
| MD | 8px | **标准按钮、菜单项** |
| LG | 10px | 卡片、Popover |
| XL | 14px | 大卡片 |
| 2XL | **20px** | Composer 外壳（Claude 桌面端标志） |
| Full | 999px | 分段胶囊、状态点 |

### 1.3 高度
`24 / 28 / 32（默认） / 40 / 48`

### 1.4 阴影（原值）
```css
/* 卡片常态 */
0 1px 2px 0 rgba(0,0,0,0.05), 0 2px 8px 0 rgba(0,0,0,0.04)     /* 浅 */
0 1px 2px 0 rgba(0,0,0,0.3)                                     /* 深 */
/* 卡片悬浮 */
0 2px 4px 0 rgba(0,0,0,0.06), 0 6px 16px 0 rgba(0,0,0,0.07)
/* Popover / 菜单 */
0 8px 24px rgba(0,0,0,0.12), 0 2px 6px rgba(0,0,0,0.08)
/* Composer 聚焦双环 */
0 0.25rem 1.25rem rgba(0,0,0,0.075), inset 0 0 0 1px rgba(0,0,0,0.16), 0 0 0 1px rgba(0,0,0,0.16)
/* Segmented 滑块 */
inset 0 0 0 1px rgba(0,0,0,0.06), 0 1px 2px 0 rgba(0,0,0,0.08)
```

### 1.5 动效
时长：`60 / 120 / 200 / 300 / 450ms`
曲线：
```
ease-out       cubic-bezier(.165,.84,.44,1)
ease-snap      cubic-bezier(.32,.72,0,1)
ease-overshoot cubic-bezier(.34,1.3,.64,1)
standard       cubic-bezier(.4,0,.2,1)      ← claudecodeui 默认 150ms
```
关键帧（CDS 原样移植）：
- **骨架扫光** `cds-skeleton-sweep`：translateX(-100%) → (100%)
- **安静三点** `timeline-quiet-dot`：1.9s `cubic-bezier(.4,0,.2,1)` infinite，三点延迟 0 / .15 / .3s，轨迹 +2 → -2.5 → 0
- **块入场** `timeline-block-enter`：opacity 0 + blur(4px) + translateY(5px) scale(.9) → 正常
- **消息上浮** `message-fade-up`：1.1s overshoot
- **逐字消解** `_grapheme-reveal`：blur(6px) → 0，90ms/字，delay = index×30ms
- **缩略图挤入** `file-thumbnail-grow-in`：0.25s `cubic-bezier(.165,.84,.44,1)`，max-width 0→8rem + scale .8→1
- **点击涟漪** `click-ring`：opacity .95 scale .22 → opacity 0 scale 1
- **对勾淡隐** `saved-check-decay`：0.3s ease-out 延迟 2.5s
- 弹簧预设：`button {600,30}` / `card {320,28}` / `bouncy {380,18}` / `drag {1200,80,mass .1}`

### 1.6 排版
- 字体：界面 Inter / system-ui；正文可用 Newsreader 衬线（CDS 用衬线做品牌与长文）
- 正文 13–15px / leading 1.5–1.65；次级 12px；微标签 11px
- `--cds-rem-scale: 0.941`
- 数字用 `tabular-nums`（计数、计时、分数）
- 标题 `text-wrap: balance`，正文 `text-wrap: pretty`
- 暗色光学字重补偿：浅色 500/600 → 暗色 **460/560**
- 图标尺寸阶梯 12 / 14 / **16** / 20 / 24

### 1.7 滚动条 / 焦点 / 层级
- 滚动条 6px；thumb `rgba(150,150,150,0.25)` 圆角 999px，hover 0.4
- 焦点环 `ring-2 ring-clay/60`（仅 focus-visible）
- z-index：Toast / 右键菜单 / 模态 9999，backdrop 9998

---

## 2. 组件规范（PySide6 落地对照）

| 组件 | 规格 |
|---|---|
| Button | 四档 24/28/**32**/40；Primary = clay 底 + 白字 + `active:scale-95`；Secondary = 边框 + Surface2；Ghost = 透明底 hover 浅底；Icon = 32×32 圆角 8 |
| Segmented | 高 **36px**，轨道 padding **2px**，圆角 999；滑块 `calc(2px + i*(100%-4px)/n)`，宽 `(100%-4px)/n`，transition 150ms `(.32,.72,0,1)` + 滑块阴影 |
| Card（图片卡） | 圆角 10；选中 = clay 5% 填充 + clay 20% 边框（来源 B 的克制选中态）+ 外侧 ring；hover 抬升用 SHADOW MD |
| Chip（文件夹） | 高 28，圆角 999，clay 衬底；含 ✕ 16px 按钮；进度文案 `相册A 6/48` → `✓ 相册A 48` |
| Input / Search | 高 32，圆角 8，Surface3 底，focus 边框转 clay + 双环阴影 |
| Dialog | 圆角 14（大）/ 8（小），backdrop `rgba(0,0,0,.5)` + blur；入场 scale .92→1 + fade 200ms |
| Table（规则/历史/隔离区） | 行高 34，表头 11px 大写次级色，选中行 clay 5%；无网格线，仅行分隔 |
| Progress | 细条 h=4 圆角 999，填色 clay；不确定态用骨架扫光 |
| Empty State | 48×48 圆角 12 图标底 + 标题 medium + 12px 次级描述（来源 B） |
| Toast | 右下角 `bottom-16 right-16`，3000ms 自消失，圆角 8（来源 B） |
| Tooltip | 延迟 **350ms**，偏移 8px，11px 字，深色底（来源 B） |
| Accordion（规则分组/详情） | `grid-template-rows: 0fr → 1fr`，200ms `(.19,1,.22,1)`，箭头 rotate 150ms（来源 A） |

---

## 3. 三个骨架方案（**请选一个**）

### D1｜CDS Studio —— 左导航 + 中央图片墙
```
┌──────────────────────────────────────────────────────────┐
│ 顶栏 48px  广告图过滤          [搜索]        ⌘K  ☾ ─ ☐ ✕ │
├────────────┬─────────────────────────────────────────────┤
│ 侧栏 256px │ 文件夹 chip 条（进度/当前文件）              │
│            ├─────────────────────────────────────────────┤
│ ▸ 扫描     │  图片墙（缩略图卡片网格，可滚动）            │
│ ▸ 结果 12  │  ┌────┐┌────┐┌────┐┌────┐                   │
│ ▸ 规则     │  │缩略││缩略││缩略││缩略│                   │
│ ▸ 历史     │  └────┘└────┘└────┘└────┘                   │
│ ▸ 隔离区 3 │                                              │
│            ├─────────────────────────────────────────────┤
│ (底部:设置)│ 底条 62px  [隔离][回收站][删除]  已选 3  处置│
└────────────┴─────────────────────────────────────────────┘
```
- 像 Claude Desktop：**导航常驻左侧**，主区纯粹看图
- 详情退为卡片 hover/点击后的右侧浮层或 Lightbox 内信息面板
- 优点：看图面积最大、导航清晰、动线最短

### D2｜CodeUI 工作台 —— 可调侧栏 + 右侧详情抽屉
```
┌──────────────────────────────────────────────────────────┐
│ 顶栏 48px                                                │
├──────────────┬──────────────────────┬────────────────────┤
│ 侧栏 288px   │ 图片墙               │ 详情抽屉 360–600   │
│ (220–640可调)│                      │ (可调,可折叠)      │
│ 文件夹树     │ ┌────┐┌────┐        │ 预览图             │
│ 规则/历史    │ └────┘└────┘        │ 判定/分数/原因     │
│              │                      │ OCR 文本           │
│              ├──────────────────────┤ 上一张/下一张      │
│              │ 底条 62px            │ 标记广告 / 保留    │
└──────────────┴──────────────────────┴────────────────────┘
```
- 像 claudecodeui：**三栏工作台**，文件夹以树形呈现，详情常驻右侧
- 分隔条可拖拽（步进 16，双击复位）
- 优点：信息密度最高、适合边看边判；缺点：看图面积最小

### D3｜沉浸画廊 —— 顶栏 + 全宽图片墙 + 悬浮操作胶囊
```
┌──────────────────────────────────────────────────────────┐
│ 顶栏 48px  ☰ 广告图过滤      [搜索⌘K]            ─ ☐ ✕ │
├──────────────────────────────────────────────────────────┤
│  图片墙（全宽，卡片自适应列数，容器查询 1/2/3–4 列）      │
│  ┌────┐┌────┐┌────┐┌────┐┌────┐┌────┐                    │
│  └────┘└────┘└────┘└────┘└────┘└────┘                    │
│                                                           │
│              ┌───────────────────────────┐                │
│              │ 悬浮胶囊 (20px 圆角, Composer 同款) │       │
│              │ 已选 3  [隔离][回收站][删除] 处置 │       │
│              └───────────────────────────┘                │
└──────────────────────────────────────────────────────────┘
```
- 侧栏收进 ☰ 抽屉（256px 浮层 + 遮罩 blur）
- 底栏是**悬浮胶囊**，用 CDS Composer 的 20px 圆角 + 双环阴影（最有辨识度的 CDS 元素）
- 优点：最像 Claude、视觉记忆点最强；缺点：操作区需悬浮，大屏略空

---

## 4. 三个方案通用的新增能力（可多选）

| # | 能力 | 来源 | 说明 |
|---|---|---|---|
| N1 | ⌘K 命令面板 | B | 搜命令/跳文件夹/切主题，Backspace 返回，Esc 关闭 |
| N2 | 可调分隔条 | B | 侧栏/详情宽度可拖（步进 16，双击复位） |
| N3 | 骨架屏 | A/B | 卡片加载用扫光骨架，替代空白 |
| N4 | 空状态插画 | B | 48px 图标底 + 标题 + 描述（无结果/无文件夹/隔离区空） |
| N5 | Toast 规范化 | B | 右下角 3000ms，成功/错误/普通三态 |
| N6 | Tooltip 350ms | B | 统一延迟与样式 |
| N7 | 点击涟漪 | A | 卡片/按钮点击 `click-ring` |
| N8 | 卡片挤入动画 | A | `file-thumbnail-grow-in` 0.25s |
| N9 | 逐字/渐进显示 | A | 状态文本用 grapheme reveal（blur 6px→0） |
| N10 | 折叠分组 | B | 规则/详情用 0fr→1fr accordion |
| N11 | 批量选择入口 | B | 非常驻复选框，点 "Select" 进入批量模式 |
| N12 | 键盘可发现性 | B | 输入框内显示 ⌘K 等 kbd 提示 |

---

## 5. 功能保全策略（一个都不能缺）

1. **API 契约不变**：新 GUI 必须实现 `FEATURE_INVENTORY.md` 第 9 章登记的约 60 个属性/方法名
   （`items / model / grid / folder_bar / f_verdict / f_sort / seg_disposal / btn_dq / status / log / _ask ...`）
2. **机器验收**：`python ad_filter.py --gui-test`（现有 105 项断言原样跑通）+ `tests/test_e2e.py` 37 项 + `tests/test_accuracy.py`
3. **人工对照**：`FEATURE_INVENTORY.md` 第 1–8 章逐条打勾
4. 引擎 `ad_filter.py` **一行不改**（识别逻辑零风险）

## 6. 实施步骤
1. 新建 `cds_tokens.py`：token 单一真源（颜色/圆角/高度/阴影/动效/排版）
2. 新建 `cds_widgets.py`：Button / Segmented / Card / Chip / Dialog / Table / Progress / EmptyState / Toast / Tooltip / Accordion / CommandPalette
3. 重写 `ad_filter_gui.py`：按选定骨架搭建，保留全部 API 契约
4. 测试：跑通 105 + 37，再补新骨架的结构断言
5. 视觉验证：`QImage + app.render()` 截图逐状态检查（grabWindow 在 DPR 1.25 下会失真——历史教训）
6. 不打包 exe（除非你要求）

## 7. Qt（QSS）落地的两条硬约束（来源：A 的抓取者实测建议）

1. **Composer 双环不要用真投影还原**。原值是
   `常态 inset 0 0 0 1px rgba(0,0,0,.08)` → `聚焦 inset 0 0 0 1px rgba(0,0,0,.16) + 0 0 0 1px rgba(0,0,0,.16)`。
   Qt 里直接写 `border: 1px solid rgba(0,0,0,.08)`，聚焦切 `.16`，**外投影全省**——描边加深在 Qt 里比 box-shadow 稳得多，跨平台不跑偏。
2. **放弃"暗色光学字重补偿"**（浅色 500/600 → 暗色 460/560）。Qt 字重档位太粗、跨平台字形不一致，最多做到"深色下 bold 降 medium"。

## 8. 已知风险
- 自绘组件多 → 首次渲染成本，需保持缩略图后台解码 + 每轮上表限 30 行（沿用 v6 防卡顿经验）
- 动效叠加可能显乱 → 统一走 token 里的时长/曲线，禁止临时数值
- 主题切换需重放组件自定义样式（历史坑：qfluentwidgets 子组件须 `setCustomStyleSheet`，且双套样式在 TextEdit 上不重放 → 按当前主题单套直设）

---

## 请确认

1. **骨架选哪个**：D1 / D2 / D3
2. **增强项选哪些**：N1–N12（可多选，我会全做的也可以直接说"全上"）
