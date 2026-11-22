<div align="center">

<img src="assets/icon.png" width="84" height="84" alt="广告图过滤图标" />

# 广告图过滤 · Ad Image Filter GUI

**面向 Windows 的纯本地相册广告图批量筛查与人工审查工作台**

<p>
  <a href="https://github.com/ElmaTony/Ad-Image-Filter-GUI/releases/latest">
    <img src="https://img.shields.io/badge/Release-Windows%20x64-C25937?style=flat-square" alt="Windows x64 Release" />
  </a>
  <img src="https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011-26231F?style=flat-square" alt="Windows 10/11" />
  <img src="https://img.shields.io/badge/Python-3.10%2B%20%C2%B7%20PySide6-2B6CB0?style=flat-square" alt="Python 3.10+ PySide6" />
  <img src="https://img.shields.io/badge/Privacy-100%25%20%E6%9C%AC%E5%9C%B0%E7%A6%BB%E7%BA%BF-2F855A?style=flat-square" alt="100% Local Offline" />
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-6A645B?style=flat-square" alt="MIT License" />
  </a>
</p>

<p>
  <strong>简体中文</strong> · <a href="README.en.md">English</a>
</p>

<p>
  <a href="https://github.com/ElmaTony/Ad-Image-Filter-GUI/releases/latest">下载最新版</a> ·
  <a href="#核心特性">核心特性</a> ·
  <a href="#快速上手">快速上手</a> ·
  <a href="#外观与视图">外观与视图</a> ·
  <a href="#识别引擎">识别引擎</a> ·
  <a href="#开发者指南">开发者指南</a>
</p>

<br />

<img src="docs/screenshots/workbench-light.png" width="900" alt="广告图过滤审查工作台预览" />

</div>

---

## 核心特性

<table>
  <tr>
    <td width="50%">
      <strong>🔍 四路独立本地识别</strong><br />
      文件名正则与白名单、图片长宽比/分辨率检测、OpenCV + pyzbar 二维码解码、RapidOCR 本地文字识别，按需独立开关。
    </td>
    <td width="50%">
      <strong>🖼️ 高密度审查工作台</strong><br />
      支持 4 套主题色盘、浅/深外观、3 档卡片网格密度、相似图分组（<code>⟲N</code>）、全屏 Lightbox 核对与 <code>Ctrl+K</code> 命令面板。
    </td>
  </tr>
  <tr>
    <td width="50%">
      <strong>🛡️ 安全可逆的文件处置</strong><br />
      默认移入可恢复隔离区，支持一键「撤销处置」、隔离区浏览与单文件还原；也可按需切换系统回收站或永久删除。
    </td>
    <td width="50%">
      <strong>🔒 纯本地零上传隐私保护</strong><br />
      图片解码、OCR 推理与缓存计算全部在本机完成，配置与缓存随程序便携存放，绝不上传图片或目录记录。
    </td>
  </tr>
</table>

---

## 快速上手

### 下载与运行

适用于 **Windows 10 / 11（64 位）**，单文件免安装：

1. 前往 [GitHub Releases](https://github.com/ElmaTony/Ad-Image-Filter-GUI/releases/latest) 下载 `AdImageFilter-Windows-x64.exe`（私有仓库需先登录有权限的 GitHub 账号）。
2. 将 EXE 放入任意固定目录后双击运行，配置、缓存与日志会自动保存在程序同目录中。
3. 升级时关闭旧程序，用新版 EXE 覆盖原文件即可保留全部规则、缓存与扫描历史。

> [!TIP]
> **应用内检查更新**：在主窗口打开 **关于 → 检查更新**（窄窗口为 **更多 → 关于**），发现新版后会展示版本号与发行页链接，下载与替换完全由你手动确认。私有仓库自动复用系统 Git 凭据助手登录，或读取环境变量 `ADFILTER_GITHUB_TOKEN`（仅需本仓库 **Contents 只读** 权限，不会落盘保存）。

### 四步完成清理

| 步骤 | 操作 | 核心细节 |
| :---: | --- | --- |
| **1** | **添加文件夹** | 点击「添加文件夹」或直接拖入窗口，支持多目录递归扫描 `JPG / JPEG / PNG / WebP / BMP / GIF`，自动跳过隔离区 |
| **2** | **扫描与筛选** | 扫描中随时暂停/继续/取消；SQLite 本地缓存加速复扫；支持按分类（全部待审/广告/疑似/异常）、命中原因、路径与 5 种排序筛选 |
| **3** | **核对与勾选** | 点选详情查看规则得分与完整 OCR 文本，双击或按 `Enter` 打开大图核对；跨筛选保留勾选，「查看已选」集中复核隐藏项 |
| **4** | **处置与恢复** | 支持 **隔离**（默认）/ **回收站** / **删除**；执行前展示总数、体积与筛选外勾选项；支持一键「撤销处置」与自定义/旧隔离区还原 |

---

## 外观与视图

内置统一多主题引擎，所有色盘均完整适配 **浅色 / 深色** 双外观并支持平滑切换动画；在筛选栏右侧、**设置 → 外观与主题** 或命令面板（`Ctrl+K`）中即可一键切换。

| 主题色盘 | 主色调 | 风格定位 | 状态 |
| --- | :---: | --- | :---: |
| **宣纸暖陶 2.0** | `#C25937` | 温和暖纸底搭配收敛陶土橙，层次清爽不发黄 | 默认 |
| **石墨画廊** | `#2563EB` | 冷中性灰阶搭配克制钴蓝，零色偏专业看图 | 可选 |
| **翠影青瓷** | `#0F766E` | 雾灰白 / 深松烟搭配鼠尾草青绿，低疲劳护眼 | 可选 |
| **极夜暗房** | `#18181B` | 高对比暗房阶梯，图文边界锐利分明 | 可选 |

**三档卡片网格密度**：`紧凑（密 · 默认）` 适合单屏高速扫图 · `标准（中）` 均衡展示缩略图与命中摘要 · `大图（大）` 放大预览便于细节复核。

---

## 识别引擎

各识别通道可在「设置」中独立开关；配合 **dHash 相似图分组**（`⟲N` 徽标，支持一键勾选整组）、**规则测试器**、**JSON 配置导入/导出**、**CSV 报表导出** 与 **最近 100 条扫描历史** 使用。

| 通道 | 判定机制 | 默认 |
| --- | --- | :---: |
| **文件名与白名单** | 正则匹配渠道名、推广词与 `@渠道号`；白名单规则优先放行 | 开启 |
| **图片形状** | 仅读取图片头尺寸，标记极端长宽比横幅或过小分辨率待复核 | 开启 |
| **二维码** | OpenCV + pyzbar 本地解码；链接判为广告，文本码或结构可信但未解码的码列为疑似 | 开启 |
| **OCR 文字** | RapidOCR 本地推理；优先裁切上下条带并提亮增强以识别半透明水印，必要时补扫全图并按词库权重计分 | 关闭 |

<details>
<summary><strong>⌨️ 快捷键速查（主工作台 &amp; 大图查看器）</strong></summary>

<br />

| 场景 | 快捷键 | 功能 |
| --- | --- | --- |
| 主工作台 | `Space` | 勾选或取消当前图片 |
| 主工作台 | `Enter` | 打开大图查看器（Lightbox） |
| 主工作台 | `Ctrl+A` | 切换当前筛选范围内的全选 |
| 主工作台 | `Ctrl+K` | 打开 / 关闭命令面板 |
| 主工作台 | `Delete` | 打开当前勾选项的处置确认弹窗 |
| 大图查看器 | `←` / `→` | 上一张 / 下一张 |
| 大图查看器 | 滚轮 / 拖拽 / 双击 | 缩放、平移画布、重置视图 |
| 大图查看器 | `F` | 切换纯净查看模式（隐藏悬浮工具栏） |
| 大图查看器 | `A` / `K` | 标记广告 / 本次保留 |
| 大图查看器 | `Esc` | 关闭大图查看器 |

</details>

<details>
<summary><strong>🔒 本地数据文件与联网范围说明</strong></summary>

<br />

- **纯离线分析**：图片解码、二维码、OCR 与相似图计算均在本机完成，不向外部发送任何图片、文本或目录路径。
- **便携数据存储**（位于 EXE 或源码同级目录）：
  - `ad_filter_config.json`：识别规则、主题色盘、网格密度与偏好设置
  - `ad_filter_cache.db`：本地 SQLite 扫描缓存（仅规则/开关变更时自动失效，切换主题或密度不触发重扫）
  - `ad_filter_history.json`：最近 100 条扫描历史
  - `ad_filter_log.jsonl`：操作与处置日志
- **最小化联网**：仅在主动点击「检查更新」时请求 GitHub Release 元数据，不保存访问令牌。

</details>

---

## 开发者指南

需要 **Windows 10 / 11（64 位）** 与 **Python 3.10+**。更多文档见 [功能清单](FEATURE_INVENTORY.md)、[更新说明](RELEASE_NOTES.md)、[测试约定](tests/README.md) 与 [交付规范](AGENTS.md)。

<details>
<summary><strong>🛠️ 从源码运行与 CLI 调试命令</strong></summary>

<br />

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe ad_filter.py
```

| 命令 | 用途 |
| --- | --- |
| `.\.venv\Scripts\python.exe ad_filter.py --selftest` | 无界面运行识别引擎、缓存与隔离逻辑自测 |
| `.\.venv\Scripts\python.exe ad_filter.py --gui-test` | 在独立临时目录运行完整 GUI 回归测试 |
| `.\.venv\Scripts\python.exe ad_filter.py --smoke` | GUI 冒烟启动测试（3 秒后自动退出，不写用户数据） |
| `.\.venv\Scripts\python.exe ad_filter.py --bench` | 运行识别引擎性能基准 |
| `.\.venv\Scripts\python.exe ad_filter.py --make-fixtures` | 生成合成测试样本到 `testdata/` |

> 无头环境运行 GUI 测试前请先执行 `$env:QT_QPA_PLATFORM = 'offscreen'`。

</details>

<details>
<summary><strong>📦 打包单文件 EXE 与发布流程</strong></summary>

<br />

1. 更新根目录 [VERSION](VERSION)（三段语义化数字，如 `1.5.1`），软件「关于」、EXE 文件/产品版本、GitHub 标签与标题均由此统一生成。
2. 在 [RELEASE_NOTES.md](RELEASE_NOTES.md) 中填写简短的用户向中英双语要点（使用 `---` 分隔，不写一级标题和日期行）。
3. 安装构建依赖并打包单文件 EXE（生成根目录 `广告图过滤.exe`，自动清理 `build/`、`dist/` 与 `.spec`）：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

4. 验证打包产物并提交源码后，运行发布脚本：

```powershell
.\scripts\publish.ps1 -NotesFile RELEASE_NOTES.md
```

</details>

<details>
<summary><strong>🗂️ 项目目录结构</strong></summary>

<br />

```text
ad-filter-gui/
├── ad_filter.py          # 扫描引擎、四路识别、dHash 分组、SQLite 缓存、隔离/撤销与 CLI
├── ad_filter_gui.py      # 主窗口控制器、交互逻辑、快捷键、命令面板与 --gui-test
├── workbench_ui.py       # 审查工作台布局、筛选条、卡片委托（CardDelegate）与密度切换
├── dialog_ui.py          # 设置、规则测试器、隔离区、历史、关于与确认弹窗
├── cds_tokens.py         # 统一多主题色盘与三档密度视觉令牌
├── cds_widgets.py        # 主题感知自定义控件（CommandPalette、Toast、Badge、Segmented 等）
├── cds_layouts.py        # 响应式流式布局组件
├── update_check.py       # GitHub Release 元数据检查与版本比较
├── scripts/              # 打包（build.ps1）、发布（publish.ps1）与版本同步脚本
├── tests/                # 单元测试、UI 审计与端到端回归测试套件
├── assets/               # 应用图标资源
└── docs/screenshots/     # 文档预览截图
```

</details>

---

<div align="center">
  基于 <a href="LICENSE">MIT License</a> 开源
</div>
