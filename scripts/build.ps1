param([string]$Python)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
. (Join-Path $PSScriptRoot 'version.ps1')
$version = Get-AppVersion $projectRoot
if (-not $Python) { $Python = Join-Path $projectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw '请先创建 .venv 并安装 requirements.txt 和 pyinstaller。' }
$buildPath = Join-Path $projectRoot 'build'
$distPath = Join-Path $projectRoot 'dist'
$outputPath = Join-Path $projectRoot '广告图过滤.exe'
$versionResource = Join-Path $buildPath 'version-info.txt'
$versionData = (Join-Path $projectRoot 'VERSION') + ':.'
$arguments = @('-m', 'PyInstaller', '--noconfirm', '--clean', '--noconsole', '--onefile',
    '--name', '广告图过滤', '--icon', (Join-Path $projectRoot 'assets\icon.ico'),
    '--add-data', $versionData, '--version-file', $versionResource,
    '--workpath', $buildPath, '--distpath', $distPath, '--specpath', $buildPath)
foreach ($module in @('rapidocr_onnxruntime','onnxruntime','qfluentwidgets','qframelesswindow','pyzbar')) {
    $arguments += @('--collect-all', $module)
}
foreach ($module in @('cds_tokens','cds_widgets','cds_layouts','workbench_ui','dialog_ui','icon_embed')) {
    $arguments += @('--hidden-import', $module)
}
foreach ($module in @('transformers','sklearn','scipy','nltk','torch','cupy','dask','pandas','matplotlib',
    'boto3','uvicorn','websockets','soundfile','av','lxml','grpc','pydantic','jsonschema','anyio','orjson',
    'tkinter','qframelesswindow.webengine','qfluentwidgets.multimedia','PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineWidgets','PySide6.QtWebChannel','PySide6.QtMultimedia','PySide6.QtMultimediaWidgets',
    'PySide6.QtQml','PySide6.QtQuick','PySide6.QtQuickWidgets','PySide6.QtQmlModels')) {
    $arguments += @('--exclude-module', $module)
}
$arguments += (Join-Path $projectRoot 'ad_filter.py')
$previousConfigDir = $env:PYINSTALLER_CONFIG_DIR
$env:PYINSTALLER_CONFIG_DIR = Join-Path $buildPath 'cache'
Push-Location $projectRoot
try {
    New-Item -ItemType Directory -Path $buildPath -Force | Out-Null
    $versionTuple = ($version.Split('.') -join ', ') + ', 0'
    $resource = @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($versionTuple), prodvers=($versionTuple),
                    mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904b0', [
    StringStruct('FileDescription', '广告图过滤'),
    StringStruct('FileVersion', '$version'),
    StringStruct('InternalName', '广告图过滤'),
    StringStruct('OriginalFilename', '广告图过滤.exe'),
    StringStruct('ProductName', '广告图过滤'),
    StringStruct('ProductVersion', '$version')
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"@
    [IO.File]::WriteAllText($versionResource, $resource, [Text.UTF8Encoding]::new($false))
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) { throw "打包失败，退出码 $LASTEXITCODE" }
    $builtExe = Join-Path $distPath '广告图过滤.exe'
    if (-not (Test-Path -LiteralPath $builtExe)) { throw '未找到打包产物。' }
    Assert-ExeVersion $builtExe $version
    Copy-Item -LiteralPath $builtExe -Destination $outputPath -Force
    # 只清理本工程内明确的构建目录；验证绝对路径后才递归删除。
    foreach ($candidate in @($buildPath, $distPath, (Join-Path $projectRoot '__pycache__'),
            (Join-Path $projectRoot 'tests\__pycache__'))) {
        $resolved = [IO.Path]::GetFullPath($candidate)
        if (-not $resolved.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "拒绝清理工程外路径：$resolved"
        }
        if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
    }
    Get-ChildItem -LiteralPath $projectRoot -Filter '*.spec' -File | Remove-Item -Force
    Write-Output "已生成 $outputPath（v$version）"
    Write-Output ('大小：{0:N1} MB' -f ((Get-Item -LiteralPath $outputPath).Length / 1MB))
    Get-FileHash -LiteralPath $outputPath -Algorithm SHA256
} finally {
    $env:PYINSTALLER_CONFIG_DIR = $previousConfigDir
    Pop-Location
}
