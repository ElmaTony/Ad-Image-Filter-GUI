param(
    [Parameter(Mandatory)][string]$Tag,
    [Parameter(Mandatory)][string]$Title,
    [Parameter(Mandatory)][string]$NotesFile,
    [string]$Gh = 'gh'
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$exe = Join-Path $projectRoot '广告图过滤.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw '请先执行 scripts/build.ps1。' }
if (-not (Test-Path -LiteralPath $NotesFile)) { throw '缺少发行说明文件。' }
$previousToken = $env:GH_TOKEN
$previousPrompt = $env:GIT_TERMINAL_PROMPT
$previousInteractive = $env:GCM_INTERACTIVE
Push-Location $projectRoot
try {
    $remote = (git remote get-url origin).Trim()
    if ($remote -ne 'https://github.com/ElmaTony/Ad-Image-Filter-GUI.git') { throw 'origin 与本项目仓库不一致，停止发布。' }
    if (git status --porcelain --untracked-files=normal) { throw '请先提交本次源代码，再运行发布。' }
    # 复用该仓库已有的 Git 凭据，仅在当前进程中传给 gh，绝不输出或落盘令牌。
    if (-not $env:GH_TOKEN) {
        & $Gh auth status 2>$null
        if ($LASTEXITCODE -ne 0) {
            $env:GIT_TERMINAL_PROMPT = '0'
            $env:GCM_INTERACTIVE = 'Never'
            $credential = "protocol=https`nhost=github.com`n`n" | git credential fill 2>$null
            $passwordLine = $credential | Where-Object { $_.StartsWith('password=') } | Select-Object -First 1
            if (-not $passwordLine) { throw 'GitHub 尚未登录，请先执行 gh auth login。' }
            $env:GH_TOKEN = $passwordLine.Substring(9)
            $credential = $null
            $passwordLine = $null
        }
    }
    git push origin HEAD
    if ($LASTEXITCODE -ne 0) { throw '源码推送失败。' }
    $commit = (git rev-parse HEAD).Trim()
    & $Gh release create $Tag $exe --repo ElmaTony/Ad-Image-Filter-GUI --target $commit --title $Title --notes-file $NotesFile --latest
    if ($LASTEXITCODE -ne 0) { throw '创建发行版或上传 EXE 失败。' }
    & $Gh release view $Tag --repo ElmaTony/Ad-Image-Filter-GUI --json url,tagName,assets
} finally {
    $env:GH_TOKEN = $previousToken
    $env:GIT_TERMINAL_PROMPT = $previousPrompt
    $env:GCM_INTERACTIVE = $previousInteractive
    Pop-Location
}
