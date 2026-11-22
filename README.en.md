# Ad Image Filter GUI

[简体中文](README.md) | English

A 100% local Windows desktop tool for batch-screening promotional banners, watermarks, QR codes, and spam images in local photo folders. Detected candidates are gathered into an interactive review workbench so you can verify and dispose of them safely.

[Download Latest](https://github.com/ElmaTony/Ad-Image-Filter-GUI/releases/latest) · [All Releases](https://github.com/ElmaTony/Ad-Image-Filter-GUI/releases) · [Release Notes](RELEASE_NOTES.md) · [Feature Inventory](FEATURE_INVENTORY.md)

![Ad Image Filter GUI Review Workbench](docs/screenshots/workbench-light.png)

---

## Highlights

- **100% Local & Offline Detection**: Filename rules, image geometry checks, QR code decoding, OCR text recognition, and visual similarity grouping all run locally on your machine—no images or folder metadata ever leave your device.
- **4 Independent Detection Channels + Similarity Clustering**: Enable or disable each channel independently, prioritize allowlist rules, score weighted promotional keywords, and group duplicate/re-shared images via dHash (`⟲N`).
- **High-Density Review Workbench**: 4 built-in theme palettes with light/dark modes, 3 card grid density presets, full-resolution Lightbox review, and a `Ctrl+K` command palette.
- **Safe, Reversible Disposal**: Moves files into a reversible quarantine directory by default with one-click Undo, quarantine browser & selective restore, plus optional Recycle Bin and Permanent Delete modes.

---

## Download, Installation & Updates

### Portable Executable (Recommended)

For **Windows 10 / 11 (64-bit)**:

1. Download `AdImageFilter-Windows-x64.exe` from the [Releases page](https://github.com/ElmaTony/Ad-Image-Filter-GUI/releases/latest) (single-file portable EXE, no installer required).
   > This repository is currently private; please sign in with an authorized GitHub account to download releases.
2. Place the EXE in any permanent folder and double-click to launch. Configuration, scan cache, history, and logs are stored alongside the executable.

### Checking for Updates & Upgrading

- **In-App Update Check**: Open **About → Check for Updates** (in narrow windows: **More → About**). When a newer release is available, the dialog shows the version number and release/download link; clicking opens your browser so you stay in full control of downloading and replacing the binary.
- **Keeping Existing Settings**: Close the older app and replace the old EXE with the new one in the same folder to retain your rules, cache, and scan history.
- **Verifying Version**: Copy the version string in **About**, or inspect **Properties → Details** (File Version / Product Version) on the EXE.
- **Private Repo Authentication**: Update checks automatically reuse credentials stored in Git Credential Manager for GitHub, or read the `ADFILTER_GITHUB_TOKEN` environment variable (requiring only read-only **Contents** access to this repo). If no credentials are available, the dialog provides a direct link to the GitHub Releases page.

---

## Workflow

1. **Add Folders**
   Click **Add Folder** or drag-and-drop folders directly onto the window. Scans recursively over `JPG`, `JPEG`, `PNG`, `WebP`, `BMP`, and `GIF`, automatically skipping quarantine directories.
2. **Scan & Filter**
   - Pause, resume, or cancel scans at any time. A local SQLite cache speeds up repeat scans and automatically invalidates only when detection rules or toggles change (switching themes or grid density does not trigger a rescan).
   - Filter by category (**All / Ad / Suspect / Error**), hit reason, or path keyword, and sort by 5 modes (default, filename, file size, modified time, risk score).
   - Switch card grid density (`Compact / Standard / Large`) directly from the filter bar, open the detail pane to inspect rule scores and full OCR text, or double-click / press `Enter` to open the Lightbox viewer.
3. **Review & Cross-Filter Selection**
   - Check items or click **Mark as Ad** in the detail pane / Lightbox to add them to the disposal batch; **Keep This Time** removes an image only from the current review session without touching the file.
   - Selections persist across category and filter switches. Click **View Selected** to review every checked item—including those hidden by active filters—before taking action.
4. **Safe Disposal & Recovery**
   - Choose among three disposal modes in the bottom bar: **Quarantine** (default, reversible), **Recycle Bin** (Windows Recycle Bin), or **Delete** (permanent). A confirmation dialog summarizes file count, total size, and any hidden selected items outside the current filter.
   - By default, the quarantine folder (`_广告隔离`) is created at the root of the first scanned drive and can be customized under **Settings → Quarantine & Config**.
   - Use **Undo Disposal** to restore the entire last quarantine batch in one click, or open **Quarantine** to browse batches, selectively restore/delete files, or inspect previous quarantine directories.

---

## Themes & Grid Density

The unified theme engine ships with 4 curated palettes—each with complete light and dark modes and smooth cross-fade transitions.

### Built-in Theme Palettes

| Palette | Style Description | Default |
| --- | --- | --- |
| **宣纸暖陶 2.0** (`atelier`) | Warm paper surface with restrained terracotta accents—crisp hierarchy without yellow cast | Default |
| **石墨画廊** (`slate`) | Cool neutral grayscale with cobalt blue accents for color-neutral photo inspection | Optional |
| **翠影青瓷** (`sage`) | Soft mist gray / deep pine dark with sage-teal accents for low eye fatigue | Optional |
| **极夜暗房** (`obsidian`) | High-contrast darkroom surfaces with sharp image/text boundaries | Optional |

### Card Grid Densities

| Density | Short Toggle | Description | Default |
| --- | --- | --- | --- |
| **Compact** | `密` | Maximum cards on screen for rapid visual triage | Default |
| **Standard** | `中` | Balanced thumbnails with hit reason summary | Optional |
| **Large** | `大` | Larger previews for detailed inspection | Optional |

> **Where to switch**: Filter bar (density toggle), title/toolbar (light/dark toggle), **Settings → Appearance & Theme**, or press `Ctrl+K` to switch via the Command Palette.

---

## Detection Engine & Rule Management

All four detection channels can be enabled or disabled independently in **Settings**:

| Channel | How It Works | Default |
| --- | --- | --- |
| **Filename & Allowlist** | Regex matching for channel names, promo keywords, and `@handle` tags; allowlist rules take priority | Enabled |
| **Image Geometry** | Header-only dimension inspection flagging extreme aspect-ratio banners or tiny resolutions for review | Enabled |
| **QR Code** | Local OpenCV + pyzbar decoding; URL QR codes are flagged as ads, while plain-text or structurally valid undecodable QR codes are marked suspect | Enabled |
| **OCR Text** | Local RapidOCR model; crops top/bottom strips with brightness enhancement for semi-transparent watermarks, optional full-image fallback, and weighted keyword scoring | Disabled (optional) |

### Review Utilities

- **Visual Similarity Grouping (dHash)**: Groups duplicate or re-forwarded images with a `⟲N` badge and supports one-click batch selection for an entire group.
- **Rule Tester**: Test regex rules and OCR keyword scoring interactively in **Rules** before saving.
- **Import / Export & History**: Export/import configuration as JSON, export the current filtered list as CSV, and review the last 100 scans in **History**.

---

## Keyboard Shortcuts

| Context | Shortcut | Action |
| --- | --- | --- |
| Workbench | `Space` | Toggle selection on current image |
| Workbench | `Enter` | Open Lightbox viewer |
| Workbench | `Ctrl+A` | Toggle select all within current filter |
| Workbench | `Ctrl+K` | Toggle Command Palette |
| Workbench | `Delete` | Open disposal confirmation dialog |
| Lightbox | `←` / `→` | Previous / next image |
| Lightbox | Wheel / Drag / Double-click | Zoom, pan, reset view |
| Lightbox | `F` | Toggle clean view (hide floating bars) |
| Lightbox | `A` / `K` | Mark as Ad / Keep This Time |
| Lightbox | `Esc` | Close Lightbox |

---

## Privacy, Local Data & Network Behavior

- **Offline Image Analysis**: Decoding, QR scanning, OCR inference, and perceptual hashing happen exclusively on your PC. Images, filenames, OCR text, and directory paths are never uploaded.
- **Portable Local Storage**: All runtime data is stored beside the EXE (or source script):
  - `ad_filter_config.json`: rules, theme palette, grid density, and preferences
  - `ad_filter_cache.db`: local SQLite scan cache
  - `ad_filter_history.json`: recent scan history
  - `ad_filter_log.jsonl`: operation and disposal logs
- **Minimal Network Usage**: Network requests occur only when you manually click **Check for Updates**, querying GitHub release metadata only. Access tokens are never written to disk.

---

## Developer Guide

### Prerequisites & Running from Source

Requires **Windows 10 / 11 (64-bit)** and **Python 3.10+**. Run from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe ad_filter.py
```

### CLI Modes & Testing

`ad_filter.py` includes built-in verification and benchmark commands:

| Command | Description |
| --- | --- |
| `.\.venv\Scripts\python.exe ad_filter.py --selftest` | Headless engine, cache, and quarantine self-test |
| `.\.venv\Scripts\python.exe ad_filter.py --gui-test` | Full GUI functional regression suite in isolated temp directories |
| `.\.venv\Scripts\python.exe ad_filter.py --smoke` | GUI smoke test (auto-exits after 3 seconds; never touches user data) |
| `.\.venv\Scripts\python.exe ad_filter.py --bench` | Detection engine benchmark |
| `.\.venv\Scripts\python.exe ad_filter.py --make-fixtures` | Generate synthetic test samples in `testdata/` |

When running GUI tests headlessly, set `$env:QT_QPA_PLATFORM = 'offscreen'`. See [tests/README.md](tests/README.md) for the full test suite and isolation rules.

### Packaging & Publishing

1. Update [VERSION](VERSION) at the repository root using `MAJOR.MINOR.PATCH` (no `v` prefix). The in-app **About** dialog, Windows EXE version info, GitHub tag (`vX.Y.Z`), and release title (`Ad Image Filter GUI vX.Y.Z`) are all derived from `VERSION`.
2. Update [RELEASE_NOTES.md](RELEASE_NOTES.md) with concise, user-facing Chinese and English bullet points (no top-level heading or standalone date line).
3. Install build dependencies and build the single-file Windows EXE (`广告图过滤.exe` in repo root; `build/`, `dist/`, and `.spec` are cleaned automatically):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

4. After verifying the build and committing changes, publish the GitHub Release:

```powershell
.\scripts\publish.ps1 -NotesFile RELEASE_NOTES.md
```

See [AGENTS.md](AGENTS.md) for full release and testing conventions.

---

## Project Structure

```text
ad-filter-gui/
├── ad_filter.py          # Scan engine, 4 detection channels, dHash grouping, SQLite cache, quarantine/undo & CLI
├── ad_filter_gui.py      # Main window controller, interactions, shortcuts, command palette & --gui-test
├── workbench_ui.py       # Review workbench layout, filter bar, CardDelegate & density switcher
├── dialog_ui.py          # Settings, Rule Tester, Quarantine, History, About & confirmation dialogs
├── cds_tokens.py         # Unified multi-palette theme engine & 3-tier density tokens
├── cds_widgets.py        # Theme-aware custom widgets (CommandPalette, Toast, Badge, Segmented, etc.)
├── cds_layouts.py        # Responsive flow layout helper
├── update_check.py       # GitHub Release metadata fetcher & version comparator
├── scripts/              # Build (build.ps1), publish (publish.ps1) & version helper scripts
├── tests/                # Unit tests, UI audit & end-to-end regression suite
└── docs/screenshots/     # Documentation screenshots
```

---

## License

Released under the [MIT License](LICENSE).
