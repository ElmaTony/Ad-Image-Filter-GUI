# 广告图过滤

在 Windows 上批量筛查本地相册里的引流广告、推广水印和二维码。软件把广告、疑似图片和读取异常列入待审列表，由你核对并决定如何处理。

[下载最新版](https://github.com/ElmaTony/Ad-Image-Filter-GUI/releases/latest) · [浏览全部发行版](https://github.com/ElmaTony/Ad-Image-Filter-GUI/releases)

![广告图过滤浅色审查工作台](docs/screenshots/workbench-light.png)

## 下载与更新

在 Windows 10 或 11（64 位）上，从发行页下载 `AdImageFilter-Windows-x64.exe`，双击运行。当前仓库需要 GitHub 访问权限，下载时请用有权限的账号登录。单文件 EXE 可直接运行。

在软件里打开「关于 → 检查更新」。窄窗口可从「更多 → 关于」进入。发现新版时，弹窗会显示版本号和下载链接，点击链接才会交给浏览器。下载和替换由你手动完成。若升级后想沿用现有配置，请关闭旧程序，把下载的 EXE 放到原来的目录。

当前版本可在「关于」复制，也可在 EXE 的「属性 → 详细信息」中核对。

私有仓库的检查需要读取发行版的权限。软件可使用系统中 Git 凭据助手已保存的 GitHub 登录。也可在启动前设置环境变量 `ADFILTER_GITHUB_TOKEN`，使用仅有该仓库 Contents 读取权限的令牌。没有可用权限时，弹窗提供 GitHub 发行页链接。

## 扫描、审查与处置

1. **添加文件夹。** 点击「添加文件夹」，或把目录拖入窗口。软件会递归读取 JPG、JPEG、PNG、WebP、BMP 和 GIF。
2. **开始扫描。** 扫描期间可暂停、继续或取消。按「广告」「疑似」「异常」或命中原因筛选，也可搜索文件路径。打开详情可核对规则得分和 OCR 文字，双击图片可查看大图。
3. **核对勾选项。** 详情中的「标记广告」把图片加入待处置集合。「本次保留」只移出本轮结果。切换筛选时，软件会保留已勾选的图片。「查看已选」可核对包括隐藏项在内的完整处置范围。
4. **选择处置方式。** 「隔离」将文件移到可恢复的目录，「回收站」交给 Windows，「删除」永久移除。执行前确认图片数量、占用空间和筛选外的勾选项。

默认隔离区在首个扫描目录所在盘的根目录。可在「设置 → 隔离与配置」选择其他位置。用「撤销处置」恢复上一次隔离，或在「隔离区」查看、还原指定文件。切换隔离目录后，仍可手动打开旧隔离区。

| 快捷键 | 操作 |
| --- | --- |
| `Space` | 勾选或取消当前图片 |
| `Enter` | 查看大图 |
| `Ctrl+A` | 切换当前筛选内的全选 |
| `Ctrl+K` | 开关命令面板 |
| `Delete` | 打开处置确认 |
| 大图中的 `←` / `→`、`F`、`Esc` | 翻页、切换纯净查看、关闭 |
| 大图中的 `A` / `K` | 标记广告、本次保留 |

## 识别规则

在「设置」中可独立开启文件名、形状、二维码和 OCR 识别。在「规则」中用测试器试算文件名或识别文本，确认后再保存规则。

| 信号 | 用途 |
| --- | --- |
| 文件名和白名单 | 用正则匹配渠道名和推广词。白名单优先放行。 |
| 图片形状 | 极端横条和较小尺寸提示人工复核。 |
| 二维码 | 查找引流链接。普通文本码和结构可信但无法解码的码列为疑似。 |
| OCR | 按推广词库计分，可在条带识别后补扫全图。 |

相似图分组帮助找出多次转发的同一图片。配置可导入或导出为 JSON，当前筛选结果可导出为 CSV，历史窗口可查看最近的扫描记录。

## 数据与联网

图片扫描、OCR、二维码识别和分组都在本机完成。配置、扫描缓存、历史和操作日志放在 EXE 所在目录。点击「检查更新」时，软件只向 GitHub 查询发行版元数据，图片及扫描记录留在本机。更新检查不保存访问令牌。

## 从源码运行

需要 Python 3.10 或以上。克隆仓库后，在根目录执行以下 PowerShell 命令。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe ad_filter.py
```

## 测试、打包与发布

运行 `python ad_filter.py --selftest` 或 `python ad_filter.py --gui-test`，以及对应功能的回归脚本。GUI 测试可在 PowerShell 中设置 `$env:QT_QPA_PLATFORM = 'offscreen'`。[测试用例与临时目录约定](tests/README.md)

发布时先更新根目录的 [VERSION](VERSION)。软件「关于」、EXE 文件版本和产品版本、GitHub 发行标签和标题使用同一版本。安装构建依赖后生成 Windows 单文件 EXE。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

构建成功后，根目录会保留 `广告图过滤.exe`，构建目录自动清理。验证并提交后运行发布脚本。脚本核对 EXE 版本，从 `VERSION` 生成标签和标题版本，推送源码，上传并核对发行附件。

```powershell
.\scripts\publish.ps1 -Title '本次改进的标题' -NotesFile RELEASE_NOTES.md
```

更多细节见 [项目交付约定](AGENTS.md) 和 [功能清单](FEATURE_INVENTORY.md)。

## 许可证

[MIT](LICENSE)
