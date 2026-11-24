- 在保持识别规则与命中精度完全一致的前提下，大幅加快大目录收集、二维码筛查、OCR 文字识别与相似图分组速度。
- 修复大批量扫描或切换筛选分类时，部分图片卡片和导出的 CSV 报告缺少「所在文件夹」信息的问题，并完善磁盘根目录下的来源名称与进度显示。
- 修复在「设置 → 扫描目录」中移除文件夹后，工作台仍残留该目录待审图片与勾选项的问题；修复重复拖入大小写不同的同一目录时的去重判断。
- 修复导入相同规则配置后首次扫描误清空已有扫描缓存的问题。
- 修复大图查看器中按「保留」后未即时刷新命中原因筛选项、极端长条图滚轮缩放上限受限，以及部分主题下标题栏与提示条显示不一致的问题。

---

- Significantly accelerated large-folder file discovery, QR screening, OCR text recognition, and visual similarity grouping with zero change to detection accuracy.
- Fixed missing source-folder labels on review cards and exported CSV reports after large scan batches or filter switches, and improved folder progress and naming when scanning drive roots.
- Fixed orphaned review items and selections remaining in the workbench after removing scan folders in Settings, and improved case-insensitive deduplication when adding folders.
- Fixed valid scan caches being unnecessarily cleared on the first scan after importing an unchanged configuration.
- Fixed stale hit-reason filter options after keeping an image in the Lightbox viewer, zoom limits on ultra-wide/tall images, and theme consistency across the title bar and toast notifications.
