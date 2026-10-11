# qq 安裝程式（Windows）
#   irm https://raw.githubusercontent.com/kuanfu0430/qq/main/install.ps1 | iex
#   $env:QQ_VERSION='v1.0.0'; irm … | iex      安裝指定版本（預設 main）
#   $env:QQ_UNINSTALL=1; irm … | iex           移除
# 不需要系統管理員權限。重跑 = 升級。
& {
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$Repo   = 'kuanfu0430/qq'
$Ref    = if ($env:QQ_VERSION) { $env:QQ_VERSION } else { 'main' }
$QQDir  = if ($env:QQ_DIR) { $env:QQ_DIR } else { Join-Path $env:LOCALAPPDATA 'qq\app' }
$Cache  = Join-Path $env:LOCALAPPDATA 'qq'
$Task   = 'qq-updatedb'
$Begin  = '# >>> qq >>>'
$End    = '# <<< qq <<<'

function Say($m)  { Write-Host "qq-install: $m" }
function Warn($m) { Write-Host "qq-install: $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "qq-install: $m" -ForegroundColor Red; throw "qq-install failed" }

# 同時寫 Windows PowerShell 5.1 與 PowerShell 7 的 profile
$docs = [Environment]::GetFolderPath('MyDocuments')
$Profiles = @(
    (Join-Path $docs 'WindowsPowerShell\Microsoft.PowerShell_profile.ps1'),
    (Join-Path $docs 'PowerShell\Microsoft.PowerShell_profile.ps1')
)

function Remove-Block($file) {
    if (-not (Test-Path $file)) { return }
    $lines = [IO.File]::ReadAllLines($file)
    $out = New-Object System.Collections.Generic.List[string]
    $skip = $false
    foreach ($l in $lines) {
        if ($l -eq $Begin) { $skip = $true; continue }
        if ($l -eq $End) { $skip = $false; continue }
        if (-not $skip) { $out.Add($l) }
    }
    while ($out.Count -gt 0 -and $out[$out.Count - 1] -eq '') { $out.RemoveAt($out.Count - 1) }
    [IO.File]::WriteAllLines($file, $out, (New-Object Text.UTF8Encoding($true)))
}

if ($env:QQ_UNINSTALL) {
    foreach ($p in $Profiles) { Remove-Block $p }
    Unregister-ScheduledTask -TaskName $Task -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $Cache -ErrorAction SilentlyContinue
    Remove-Item Env:QQ_UNINSTALL -ErrorAction SilentlyContinue
    Say '已移除。開新的 PowerShell 後 qq 就不在了。'
    return
}

# ------------------------------------------------------------ Python
$py = $null
foreach ($c in 'python', 'py', 'python3') {
    $cmd = Get-Command $c -CommandType Application -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike '*\WindowsApps\*' } | Select-Object -First 1
    if ($cmd) {
        $exe = & $cmd.Source -c "import sys;print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) { $py = "$exe".Trim(); break }
    }
}
if (-not $py) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say '找不到 Python，用 winget 安裝 Python 3.12（使用者層級）…'
        winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Null
        $cand = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
        if (Test-Path $cand) { $py = $cand }
    }
    if (-not $py) { Die '需要 Python 3.8 以上：https://www.python.org/downloads/ （安裝時勾 Add to PATH）' }
}
& $py -c "import sys;sys.exit(0 if sys.version_info>=(3,8) else 1)"
if ($LASTEXITCODE -ne 0) { Die "Python 版本太舊：$py" }
Say "Python：$py"

& $py -c "import curses" 2>$null
if ($LASTEXITCODE -ne 0) {
    Say '安裝 windows-curses（互動選單用）…'
    & $py -m pip install --user --disable-pip-version-check -q windows-curses
    & $py -c "import curses" 2>$null
    if ($LASTEXITCODE -ne 0) { Die 'windows-curses 安裝失敗（手動：python -m pip install --user windows-curses）' }
}

# ------------------------------------------------------------ 下載
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("qq-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    $zip = Join-Path $tmp 'qq.zip'
    $url = "https://codeload.github.com/$Repo/zip/$Ref"
    Say "下載 $Repo@$Ref"
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $src = Get-ChildItem $tmp -Directory | Select-Object -First 1
    if (-not (Test-Path (Join-Path $src.FullName 'bin\qq_lib.py'))) { Die '下載內容不完整' }

    New-Item -ItemType Directory -Force -Path $QQDir | Out-Null
    Remove-Item -Recurse -Force (Join-Path $QQDir 'bin') -ErrorAction SilentlyContinue
    Copy-Item -Recurse (Join-Path $src.FullName 'bin') (Join-Path $QQDir 'bin')
    Copy-Item (Join-Path $src.FullName 'qq.ps1') $QQDir -Force
    Copy-Item (Join-Path $src.FullName 'README.md') $QQDir -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $QQDir 'bin\qq-cli') -ErrorAction SilentlyContinue
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
[IO.File]::WriteAllText((Join-Path $QQDir 'python.txt'), $py)
[IO.File]::WriteAllText((Join-Path $QQDir 'VERSION'), "ref=$Ref`nos=windows`ninstalled=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`n")

# ------------------------------------------------------------ profile
$shim = Join-Path $QQDir 'qq.ps1'
foreach ($p in $Profiles) {
    $dir = Split-Path $p -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    Remove-Block $p
    $block = "`r`n$Begin`r`nif (Test-Path '$shim') { . '$shim' }`r`n$End`r`n"
    [IO.File]::AppendAllText($p, $block, (New-Object Text.UTF8Encoding($true)))
}
Say '已寫入 PowerShell profile（5.1 與 7）'

$pol = Get-ExecutionPolicy -Scope CurrentUser
if ($pol -in 'Undefined', 'Restricted', 'AllSigned') {
    $eff = Get-ExecutionPolicy
    if ($eff -in 'Restricted', 'AllSigned') {
        Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force
        Say '執行原則（目前使用者）設為 RemoteSigned，profile 才能載入'
    }
}

# ------------------------------------------------------------ 索引排程
New-Item -ItemType Directory -Force -Path $Cache | Out-Null
$pyw = Join-Path (Split-Path $py -Parent) 'pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = $py }
$action  = New-ScheduledTaskAction -Execute $pyw -Argument ("`"" + (Join-Path $QQDir 'bin\qq-updatedb') + "`" --quiet")
$trigger = New-ScheduledTaskTrigger -Daily -At '12:30'
$set     = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $Task -Action $action -Trigger $trigger -Settings $set -Description 'qq 指令集：每天重建檔名索引' -Force | Out-Null
Start-ScheduledTask -TaskName $Task
Say "排程：工作排程器「$Task」每天 12:30；首次建索引已在背景執行（約 3～10 分鐘），期間可先用 --fresh"

if (-not (Get-Command rg -ErrorAction SilentlyContinue)) { Warn '選用：winget install BurntSushi.ripgrep.MSVC 可加快 qq grep' }
if (-not (Get-Command fd -ErrorAction SilentlyContinue)) { Warn '選用：winget install sharkdp.fd 可加快 --fresh' }

# 目前這個 session 也直接載入
. $shim
Say '完成 ✔  這個視窗已可用 qq（新視窗會自動載入）：qq help'
}
