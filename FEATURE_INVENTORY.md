# 功能清单（GUI 重做验收基线）

> 重做 GUI 时逐条对照，**一条都不能少**。括号内是现有实现位置/名称。
> 暖陶工作台验收（2026-10-30）：GUI 131/131、E2E 37/37、工作台回归 9/9、UI 审查 72/72。
> 精度合成样本召回 22/22、误报 0/22；保持单工作台，深浅主题及多窗口尺寸。完整功能映射见 `UI_REDESIGN_PLAN.md`。

## 1. 窗口与标题栏
- 无边框窗口 + 自绘标题栏（高 38）
- 标题栏：内嵌扫帚应用图标 + 品牌文字「广告图过滤」（文字严格居中）
- 右侧三按钮：最小化 / 最大化·还原 / 关闭（46×30，贴右边缘，关闭 hover 红）
- 双击标题栏最大化 / 还原；标题栏可拖拽
- 窗口居中显示；关闭时停线程、停定时器、无僵尸进程
- 深浅主题切换 + 切换时 cross-fade（360ms）

## 2. 扫描工作流
- 添加文件夹（按钮 / 设置弹窗 / **拖拽文件夹直接导入**）
- 文件夹 chip 条：`相册A 6/48` → `✓ 相册A 48`，当前扫描项橙色高亮
- chip ✕ 一键移除；chip 右键菜单（在资源管理器中打开 / 移除）
- 启动不记忆上次文件夹
- 文件夹维度进度 + 状态条显示「正在扫描 相册A/xxx.jpg」
- 暂停 / 继续（真正停止提交新任务）
- 取消扫描
- 二次扫描走缓存秒开；规则/识别方式变更自动失效缓存
- **扫描历史**（最近 100 次：时间/文件夹/总数/命中/耗时）

## 3. 识别能力（引擎，开关都在设置里）
- 文件名规则 / 形状规则 / 二维码 / OCR 文字 —— 四个开关独立生效
- OCR 全图兜底（条带优先 / 条带+全图兜底）
- 线程数 1–16
- 相似聚类（dHash，阈值可配），卡片标注 ⟲N，「选相似组」批量勾选

## 4. 结果浏览（图片墙）
- 缩略图卡片网格：文件名（双向截断保后缀）、来源文件夹、判定色、类型徽标
- 相似标注、骨架扫光、入场动画
- 选中态：陶土色 wash + 双环
- 分行筛选：全部待审 / 广告 / 疑似 / 异常，命中原因下拉和路径搜索（防抖 250ms）
- **排序下拉**：默认 / 名称 / 大小 / 修改时间 / 分数
- 详情面板：宽度 320–520 可调，预览按区域自适应；判定/分数/原因/完整已保存 OCR 文本
- 上一张 / 下一张按钮 + 方向键联动预览
- 双击 / Enter 打开大图（Lightbox）
- 大图在上下工具栏之外适配完整图片；缩放和平移裁剪到图片区，F 切换纯净查看
- 卡片 tooltip（路径/判定/分数/原因）
- 右键菜单：勾选切换 / 打开大图 / 全选 / 反选 / 清除选择 / 选相似组
- 滚动渐隐遮罩（仅可滚动时显示）

## 5. 勾选与标记
- 点卡片圆圈勾选 / 空格勾选当前项
- Ctrl+A 全选（再按取消全选）
- 反选 / 清除选择
- 多选（≥2）自动勾选
- 标记广告（=勾选）/ 标记保留（=移出列表）
- Lightbox 内 A 标广告 / K 保留
- 保留后停留在相邻图片，重新扫描刷新缩略图；延迟相似分组不会重新勾选已保留图片

## 6. 处置与恢复
- 三种处置方式：隔离 / 回收站 / 删除（三个可见按钮）
- 处置前**影响预览**（广告/疑似/异常计数 + 总大小）
- 删除 / 回收站二次确认
- 隔离保留 manifest.json，可整体恢复
- **撤销上一次隔离处置**
- **隔离区管理对话框**：条目表格 + 还原选中 / 彻底删除选中 / 全部还原 / 刷新
- 打开隔离目录按钮
- 自定义隔离目录（设置中选择或填写、留空恢复默认），当前与历史隔离目录跳过扫描；隔离区窗口可切换旧位置或打开其他隔离区
- 处置中禁用按钮；处置后从列表移除 + 日志

## 7. 规则与配置
- 规则管理：文件名规则 / OCR 词库（含权重）/ 白名单 / 规则测试器，侧边导航切换
- 表格编辑（双击/单击进入编辑）、新增 / 删除行、正则校验（无效拒收）
- **规则测试器**：贴 OCR 文本 + 文件名试算得分与判定（白名单 > 文件名 > 阈值）
- 设置弹窗：扫描目录 / 识别选项 / 隔离与配置分区；文件夹列表可拖入，包含识别开关、全图兜底、线程与相似阈值
- **配置导入 / 导出 JSON**

## 8. 其他
- 操作日志面板（底部折叠展开，与状态条双写）
- 结果导出 CSV 报告
- 扫描历史按时间或目录搜索，显示累计扫描数量
- 配套弹窗统一标题、关闭按钮、固定操作栏与 Esc 关闭；命令面板支持 Esc、Ctrl+K、重复点击入口关闭
- Toast 通知（InfoBar）
- 快捷键：空格勾选、Enter 大图、Delete 处置、Ctrl+A 切换全选、←→ 翻页、Esc 关大图
- 引擎 CLI：`--selftest` / `--gui-test` / `--smoke` / `--bench` / `--make-fixtures`

## 9. API 契约（重做时这些名字必须保留，否则测试全废）

测试直接调用的 `App` 成员（gui-test 105 项 + E2E 37 项）：

| 类别 | 名字 |
|---|---|
| 数据 | `items`、`model.items/checked/rowCount/index/sim_map/sim_groups`、`checked`、`similar_groups`、`total_files`、`cfg`、`_roots`、`get_roots()` |
| 扫描 | `start_scan()`、`toggle_pause()`、`scan_thread`、`scan_q`、`cancel`、`pause`、`_cache`、`_last_progress`、`_current_file`、`_current_root`、`_folder_totals`、`_folder_done`、`_stat_cache` |
| 文件夹条 | `folder_bar.rebuild()`、`folder_bar._chips`（dict：path→chip，chip 有 `text()`/`btn_x`/`property("active")`）、`folder_bar.lbl_current`、`folder_bar.progress`、`add_folders()`、`remove_folder()` |
| 网格/卡片 | `grid`（QListView：currentIndex/doubleClicked/visualRect/viewport/selectionModel）、`model`、`delegate`、`toggle_check(row)`、`step(delta)`、`open_big()`、`_big_win`、`mark_current(as_ad)`、`mark_path(path, as_ad)` |
| 筛选/排序 | `f_verdict`、`f_reason`、`f_search`、`f_sort`、`refresh()` |
| 详情面板 | `preview.pixmap()`、`info.toPlainText()`、`btn_prev`、`btn_next` |
| 选择 | `select_all()`、`clear_sel()`、`invert_sel()`、`select_similar_groups()` |
| 处置 | `seg_disposal.set_index(i)`、`btn_dq/btn_dr/btn_dd`（isVisible/setChecked）、`btn_dispose`、`_current_disposal()`、`execute_disposal()`、`undo_disposal()`、`_undo_batch`、`restore_ui()`、`_qdlg`、`open_quarantine_dir()` |
| 日志/状态 | `status.text()`、`_log()`、`btn_log`、`log`（isVisible/height/toPlainText）、`_ask()`、`_info()`、`_toast()` |
| 弹窗 | `build_config()`、`build_rule_editor()`、`open_history()`、`export_report()`、`toggle_theme()`、`theme` |
| 标题栏 | `_titlebar`（`btn_min/btn_max/btn_close`、`brand`、`logo`、`width()`） |
| 测试开关 | `_ask`/`_info`/`_toast` 可被测试替换；`RuleEditorLite.show_errors=False` 规避模态 InfoBar |
