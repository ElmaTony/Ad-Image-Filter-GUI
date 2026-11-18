function Get-AppVersion([string]$ProjectRoot) {
    $version = [IO.File]::ReadAllText((Join-Path $ProjectRoot 'VERSION')).Trim()
    if ($version -notmatch '\A(?:0|[1-9][0-9]{0,4})(?:\.(?:0|[1-9][0-9]{0,4})){2}\z' -or
        @($version.Split('.') | Where-Object { [int]$_ -gt 65535 }).Count -gt 0) {
        throw 'VERSION 必须是三个以点分隔的整数，每段为 0 到 65535。'
    }
    return $version
}

function Assert-ExeVersion([string]$Path, [string]$Version) {
    $info = [Diagnostics.FileVersionInfo]::GetVersionInfo($Path)
    $fileNumber = @($info.FileMajorPart, $info.FileMinorPart, $info.FileBuildPart, $info.FilePrivatePart) -join '.'
    $productNumber = @($info.ProductMajorPart, $info.ProductMinorPart, $info.ProductBuildPart, $info.ProductPrivatePart) -join '.'
    if ($info.FileVersion -cne $Version -or $info.ProductVersion -cne $Version -or
        $fileNumber -cne "$Version.0" -or $productNumber -cne "$Version.0") {
        throw "EXE 文件版本和产品版本须与 VERSION（$Version）一致。请重新运行 scripts/build.ps1。"
    }
}
