- 修复透明背景 PNG/WebP 贴纸、水印与二维码以及 16 位灰度图片在识别与预览时变全黑或全白的问题。
- 修复扫描目录路径大小写不同时未能命中已有缓存的问题，并支持正常隔离、还原或删除带有「只读」属性的图片文件。
- 修复「规则管理」中修改单元格后未按回车直接点击保存会丢失当前编辑内容的问题；「隔离区管理 → 全部还原」新增确认提示以防误触。
- 提升高分屏缩放下的缩略图、详情预览与图标清晰度，优化大批量结果勾选响应与宽松阈值相似图分组速度，并在大图查看器中同步显示「已标广告」状态、增强导出 CSV 在表格软件中的安全性。

---

- Fixed transparent PNG/WebP stickers, watermarks, and QR codes as well as 16-bit grayscale images turning solid black or white during detection and preview.
- Fixed scan cache misses when folder paths differ in letter casing on Windows, and added support for quarantining, restoring, and deleting read-only image files without permission errors.
- Fixed uncommitted inline cell edits in **Rule Manager** being lost when clicking Save directly without pressing Enter, and added a confirmation prompt before **Restore All** in **Quarantine Manager**.
- Improved thumbnail, detail preview, and icon sharpness on High-DPI displays, sped up selection updates and higher-threshold similarity grouping on large result sets, added a marked-ad state indicator in Lightbox, and hardened exported CSV reports for spreadsheet apps.
