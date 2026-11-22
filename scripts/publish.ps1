param(
    [string]$Tag,
    [string]$Title,
    [Parameter(Mandatory)][string]$NotesFile,
    [ValidateRange(1, 86400)][int]$UploadTimeoutSec = 3600
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
. (Join-Path $PSScriptRoot 'version.ps1')
$version = Get-AppVersion $projectRoot
$releaseTag = "v$version"
if ($Tag -and $Tag -cne $releaseTag) { throw "标签必须与 VERSION 一致（$releaseTag）。" }
$expectedTitle = "Ad Image Filter GUI $releaseTag"
$releaseTitle = if ($Title) { $Title.Trim() } else { $expectedTitle }
if ($releaseTitle -cne $expectedTitle) { throw "发行标题必须为 $expectedTitle。" }
$exe = Join-Path $projectRoot '广告图过滤.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw '请先执行 scripts/build.ps1。' }
Assert-ExeVersion $exe $version
$notes = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $NotesFile)).Trim()
if (-not $notes) { throw '请填写发行说明。' }
if ($notes -match '(?m)^\s*#\s+' -or $notes -match '(?m)^\s*\d{4}-\d{2}-\d{2}\s*$') {
    throw '发行说明正文直接写更新要点，不要包含一级标题或单独日期行。'
}
if ($notes -notmatch '[\u4e00-\u9fff]' -or $notes -notmatch '[A-Za-z]{4,}' -or $notes -notmatch '(?m)^\s*---\s*$') {
    throw '发行说明正文须包含中英文双语要点，并使用 --- 分隔。'
}
$previousPrompt = $env:GIT_TERMINAL_PROMPT
$previousInteractive = $env:GCM_INTERACTIVE
Push-Location $projectRoot
try {
    $remote = (git remote get-url origin).Trim()
    if ($remote -ne 'https://github.com/ElmaTony/Ad-Image-Filter-GUI.git') { throw 'origin 与本项目仓库不一致，停止发布。' }
    if (git status --porcelain --untracked-files=normal) { throw '请先提交本次源代码，再运行发布。' }
    $tagRef = "refs/tags/$releaseTag"
    $existingTag = git ls-remote --tags --refs origin $tagRef
    if ($LASTEXITCODE -ne 0) { throw '无法确认远端标签。' }
    if ($existingTag) { throw "发布标签 $releaseTag 已存在，请更新 VERSION 后重新打包。" }
    $token = $env:GH_TOKEN
    if (-not $token) {
        $env:GIT_TERMINAL_PROMPT = '0'
        $env:GCM_INTERACTIVE = 'Never'
        $credential = "protocol=https`nhost=github.com`n`n" | git credential fill 2>$null
        $passwordLine = $credential | Where-Object { $_.StartsWith('password=') } | Select-Object -First 1
        if (-not $passwordLine) { throw 'GitHub 凭据缺失，请先登录 GitHub。' }
        $token = $passwordLine.Substring(9)
        $credential = $null
        $passwordLine = $null
    }
    $headers = @{ Authorization=('Bearer ' + $token); Accept='application/vnd.github+json'; 'X-GitHub-Api-Version'='2022-11-28' }
    $token = $null
    git push origin HEAD
    if ($LASTEXITCODE -ne 0) { throw '源码推送失败。' }
    $commit = (git rev-parse HEAD).Trim()
    $api = 'https://api.github.com/repos/ElmaTony/Ad-Image-Filter-GUI'
    $body = @{ tag_name=$releaseTag; target_commitish=$commit; name=$releaseTitle; body=$notes; draft=$true; prerelease=$false } | ConvertTo-Json
    $release = Invoke-RestMethod -Method Post -Uri "$api/releases" -Headers $headers -Body ([Text.Encoding]::UTF8.GetBytes($body)) -ContentType 'application/json; charset=utf-8'
    Write-Output ('已创建发行草稿：' + $release.id)
    # GitHub 会规范化中文附件名；明确使用 ASCII 名称上传，无须复制出第二个 EXE。
    $upload = "https://uploads.github.com/repos/ElmaTony/Ad-Image-Filter-GUI/releases/$($release.id)/assets?name=AdImageFilter-Windows-x64.exe"
    $asset = Invoke-RestMethod -Method Post -Uri $upload -Headers $headers -InFile $exe -ContentType 'application/octet-stream' -TimeoutSec $UploadTimeoutSec
    $expected = 'sha256:' + (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($asset.state -ne 'uploaded' -or $asset.size -ne (Get-Item -LiteralPath $exe).Length -or $asset.digest -ne $expected) {
        throw '附件核对失败，发行版保留为草稿。'
    }
    $body = @{draft=$false; make_latest='true'} | ConvertTo-Json
    $published = Invoke-RestMethod -Method Patch -Uri "$api/releases/$($release.id)" -Headers $headers -Body ([Text.Encoding]::UTF8.GetBytes($body)) -ContentType 'application/json; charset=utf-8'
    if ($published.draft -or $published.tag_name -cne $releaseTag -or $published.name -cne $releaseTitle -or
        $published.body.Trim() -cne $notes.Trim()) { throw '发行版本或说明核对失败。' }
    $publishedAsset = $published.assets | Where-Object { $_.id -eq $asset.id }
    if (-not $publishedAsset -or $publishedAsset.digest -ne $expected) { throw '公开发行版的附件核对失败。' }
    [PSCustomObject]@{ Tag=$published.tag_name; Url=$published.html_url; Download=$publishedAsset.browser_download_url; Verified=$true }
} finally {
    $headers = $null
    $credential = $null
    $passwordLine = $null
    $token = $null
    $env:GIT_TERMINAL_PROMPT = $previousPrompt
    $env:GCM_INTERACTIVE = $previousInteractive
    Pop-Location
}
