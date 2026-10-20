# 广告图过滤（Ad Image Filter GUI）

纯本地运行的图片广告过滤工具：扫描本地文件夹，用**多层信号**识别夹带推广信息/渠道引流的广告图，人工确认后批量处置（隔离可回滚 / 回收站 / 删除）。**所有分析都在本机完成，不上传任何数据。**

## 功能特性

- **多层识别信号**（可单独开关）：
  1. **文件名规则**——正则匹配（TG 渠道号、推广词等，可自定义）
  2. **形状规则**——极端长宽比 / 过小分辨率（只读文件头，不解码）
  3. **二维码检测**——OpenCV 本地解码，含链接直接判广告
  4. **OCR 文字**——RapidOCR 本地模型；上下条带裁切 + 提亮增强，专治半透明空心水印，按推广词库加权计分
- **图片墙界面**：缩略图卡片网格，单击看大图详情，空格/点圈勾选，`←` `→` 翻页，双击进画廊查看器（滚轮缩放 / 拖拽平移 / 双击 1.25×）
- **相似聚类**：dHash 感知指纹 + 贪心代表聚类，同一广告图的多次转发归为一组，一键批量勾选
- **文件夹维度进度**：每个导入文件夹实时显示 已扫/总数，完成打 ✓，并显示当前正在扫描的文件名
- **暂停 / 继续**：扫描随时暂停（真正停止计算，不是假 UI 暂停）
- **处置三模式**：移入隔离目录（附 manifest.json 清单，可一键恢复）/ 系统回收站 / 直接删除（二次确认）
- **缓存**：判定结果按 路径+大小+mtime 缓存，二次扫描秒开；规则变更自动失效
- **纯本地**：无网络请求、无遥测、无账号体系

## 环境要求

- Windows 10/11（macOS/Linux 理论可用，未测试）
- Python 3.10+

## 安装

```bash
git clone https://github.com/ElmaTony/Ad-Image-Filter-GUI.git
cd ad-filter-gui
pip install -r requirements.txt
```

依赖：PySide6、PyQt-Fluent-Widgets、qframelesswindow、Pillow、OpenCV、RapidOCR、send2trash。

## 快速开始

```bash
python ad_filter.py                 # 启动 GUI
python ad_filter.py --selftest      # 引擎自测（无界面）
python ad_filter.py --make-fixtures # 生成合成测试样本到 testdata/
python ad_filter.py --bench         # 性能基准
python ad_filter.py --gui-test      # GUI 功能自测（75 项）
```

### 使用流程

1. 点「添加文件夹」（或直接把文件夹拖进窗口）→ 可导入多个，chip 上可一键 ✕ 移除
2. 点「开始扫描」→ 顶栏呼吸灯变呼吸态，实时显示当前扫描的文件夹/文件名
3. 扫描完成 → 图片墙列出命中项（广告/疑似），逐张确认或多选批量勾选
4. 选处置方式（默认隔离）→ 「执行处置」；隔离过的随时「恢复隔离区」一键还原
5. ⚙ 设置里可调识别方式、线程数、全图兜底；「规则管理」里编辑文件名正则 / OCR 词库（带权重）/ 白名单

### 首次使用提示

- 没有真实样本也能跑：`--make-fixtures` 生成合成广告图用于验证识别管线
- 把自己的真实广告样本放进 `testdata/`（`real*.png/jpg`），自测会自动纳入
- 首次扫描会加载 OCR 模型（约 2 秒），之后模型常驻内存

## 工作原理

```
文件收集（跳过隔离目录）
  ├─ 1. 文件名正则 → 直接判 ad（99 分）
  ├─ 2. 只读图片头：长宽比 ≥ 3 → ad；最小边 < 300 → 疑似
  └─ 3. 需要像素时（前两级未命中）：
        ├─ QR 码解码：内容含 t.me/http → ad；仅检测到图形 → 记疑
        └─ OCR：上/下条带裁切 → 原图识别 → 未达广告线则逐级提亮重试
             → 仍未命中且开了"全图兜底"：整图降到 1100px 再识别一次
             → 加权计分：≥6 广告、≥3 疑似（词库可自定义）
```

- **逐级短路**：得分够广告线立即停止，不浪费推理
- **推理会话共享单实例**，线程数上限 8（再多无收益）
- **缓存**：结果按路径+大小+mtime 键存 SQLite；规则/阈值 hash 变化自动全量重判

## 自定义规则（⚙ 规则管理）

| 类别 | 说明 | 示例 |
|---|---|---|
| 文件名规则 | 匹配文件名的正则，命中直接判广告 | `t\.me\|TG@\|telegraph` |
| OCR 词库 | 匹配 OCR 文本的正则 + 权重，累计计分 | `免费发布`（权重 3）、`频道`（权重 1） |
| 白名单 | 匹配文件名的正则，命中直接放行 | 自己的签名图 |

规则修改后自动触发缓存失效，下次扫描全量重判。

## 构建 exe

```bash
pip install pyinstaller
pyinstaller --noconfirm --noconsole --onefile --name 广告图过滤 ^
  --collect-all rapidocr_onnxruntime --collect-all onnxruntime ^
  --collect-all qfluentwidgets --collect-all qframelesswindow ^
  --exclude-module transformers --exclude-module torch ... ad_filter.py
```

（完整排除清单见 `build-exe.txt`；onefile 打包约 165MB，首次启动需解压 5–10 秒。）

## 隐私声明

- 所有扫描、OCR、聚类均在本地完成，**无任何网络请求**
- 隔离目录位于各扫描根盘的 `_广告隔离/`，`manifest.json` 记录原始路径用于恢复
- 操作日志仅写在本地 `ad_filter_log.jsonl`

## License

MIT
