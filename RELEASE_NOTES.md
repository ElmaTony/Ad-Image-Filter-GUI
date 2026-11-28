- 新增竖长图（条漫/长截图）首尾高清切片识别、GIF/WebP 动图多帧抽检、EXIF 自动转正与二维码裸域名/明文摘要识别，降低大尺寸图并发扫描内存占用。
- 优化 OCR 文本行间拼接与英文句点分词，大幅减少英文句子和跨行文本误报；切换扫描选项或规则后保留历史配置缓存，支持多模式瞬时切换且自动控制缓存体积。
- 修复点击「清除」后选中高亮残留、状态灯在相似分组完成后误闪烁、隔离批处理单文件冲突阻塞整批记录、处置方式重启丢失、设置跨机导入盘符拒收、大图查看器无法缩小与重复取消标记等问题。
- 改进工作台交互细节：支持同名扫描目录自动消歧、准确的原因筛选、命令面板按状态置灰与点击外部关闭、弹窗可拖拽调整尺寸及操作日志完整记录。

---

- Added high-resolution head/tail strip detection for tall vertical images (long comics/screenshots), multi-frame sampling for animated GIF/WebP files, EXIF orientation alignment, naked-domain QR recognition with text summary in the detail pane, and lower memory usage when decoding large images.
- Refined OCR line joining and English punctuation normalization to prevent cross-line and sentence false positives; preserved multi-configuration scan cache across mode switches with automatic cache size pruning.
- Fixed selection highlight lingering after **Clear**, status indicator staying active after similarity clustering, single-file conflicts blocking entire quarantine batches, disposal mode resetting on restart, cross-machine config import failing on missing drives, and Lightbox zoom-out / toggle-ad behavior.
- Improved workbench polish: automatic disambiguation for duplicate folder names, exact hit-reason filtering, context-aware Command Palette states and click-outside dismissal, resizable dialogs, and complete action logging.
