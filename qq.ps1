# qq 指令集 - PowerShell 入口（Windows）
# 由 $PROFILE dot-source 載入，定義 qq 函式（函式才能替目前的 PowerShell 切換目錄）。
# 專案：https://github.com/kuanfu0430/qq  （升級：重跑安裝指令）

if (-not $env:QQ_HOME) { $env:QQ_HOME = $PSScriptRoot }

function global:Get-QQPython {
    if ($env:QQ_PYTHON -and (Test-Path $env:QQ_PYTHON)) { return $env:QQ_PYTHON }
    $p = Join-Path $env:QQ_HOME 'python.txt'
    if (Test-Path $p) {
        $exe = (Get-Content -Raw $p).Trim()
        if ($exe -and (Test-Path $exe)) { return $exe }
    }
    foreach ($c in 'python', 'py', 'python3') {
        $cmd = Get-Command $c -CommandType Application -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notlike '*\WindowsApps\*' } | Select-Object -First 1
        if ($cmd) { return $cmd.Source }
    }
    throw 'qq: 找不到 Python（請安裝 Python 3.9+ 後重跑安裝指令）'
}

function global:Invoke-QQBin {
    param([string]$Name, [string[]]$Rest)
    $py = Get-QQPython
    $out = [IO.Path]::GetTempFileName()
    $env:QQ_OUT = $out
    $env:PYTHONUTF8 = '1'
    try {
        & $py (Join-Path $env:QQ_HOME "bin\$Name") @Rest
        $rc = $LASTEXITCODE
        $target = ''
        if (Test-Path $out) { $target = [IO.File]::ReadAllText($out, [Text.Encoding]::UTF8) }
        return [pscustomobject]@{ Code = $rc; Target = $target }
    } finally {
        Remove-Item Env:QQ_OUT -ErrorAction SilentlyContinue
        Remove-Item $out -ErrorAction SilentlyContinue
    }
}

function global:Test-QQHelp([string[]]$a) {
    foreach ($x in $a) {
        if ($x -eq '--') { return $false }
        if ($x -eq '-h' -or $x -eq '--help') { return $true }
    }
    return $false
}

function global:qq {
    $sub = if ($args.Count -gt 0) { [string]$args[0] } else { '' }
    $rest = @()
    if ($args.Count -gt 1) { $rest = @($args[1..($args.Count - 1)] | ForEach-Object { [string]$_ }) }

    switch ($sub) {
        { $_ -in 'find', 'grep' } {
            if (Test-QQHelp $rest) { & (Get-QQPython) (Join-Path $env:QQ_HOME "bin\qq-$sub") --help; return }
            $r = Invoke-QQBin "qq-$sub" $rest
            if ($r.Code -ne 0 -or -not $r.Target) { $global:LASTEXITCODE = [Math]::Max(1, $r.Code); return }
            $dest = $r.Target
            if (-not (Test-Path -LiteralPath $dest -PathType Container)) { $dest = [IO.Path]::GetDirectoryName($dest) }
            Set-Location -LiteralPath $dest
            [Console]::Error.WriteLine("→ $($r.Target)")
            $global:LASTEXITCODE = 0
        }
        'pick' {
            if (Test-QQHelp $rest) { & (Get-QQPython) (Join-Path $env:QQ_HOME 'bin\qq-pick') --help; return }
            $r = Invoke-QQBin 'qq-pick' $rest
            $global:LASTEXITCODE = $r.Code
            if ($r.Code -eq 0 -and $r.Target) { return $r.Target }
        }
        { $_ -in '', 'help', '-h', '--help' } {
            @'
qq 指令集

用法：
  qq <子指令> [參數...]

子指令：
  find    依名稱搜尋資料夾／檔案，互動選擇後切換目錄
  pick    依名稱搜尋，選完回傳完整路徑（人留在原地）
  grep    依檔案內容搜尋，互動選擇後切換到該檔所在資料夾
  help    顯示本說明

索引：
  qq-updatedb             立即重建索引（平常每天自動更新）

範例：
  qq find -d Downloads
  qq find -f hosts
  qq find --fresh -d mnt          不用索引，即時掃描
  notepad (qq pick -f hosts)
  Copy-Item (qq pick -f notes.md) .
  qq grep TODO
'@
        }
        default {
            [Console]::Error.WriteLine("qq: 未知的子指令「$sub」（qq help 看說明）")
            $global:LASTEXITCODE = 2
        }
    }
}

function global:qq-updatedb {
    $env:PYTHONUTF8 = '1'
    & (Get-QQPython) (Join-Path $env:QQ_HOME 'bin\qq-updatedb') @args
}

Register-ArgumentCompleter -CommandName qq -Native -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    $words = @($commandAst.CommandElements | ForEach-Object { $_.ToString() })
    $cands = if ($words.Count -le 2 -and -not ($words.Count -eq 2 -and $wordToComplete -eq '')) {
        'find', 'pick', 'grep', 'help'
    } elseif ($words[1] -eq 'grep') { '--regex', '--help' } else { '-d', '-f', '--fresh', '--help' }
    $cands | Where-Object { $_ -like "$wordToComplete*" } |
        ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }
}
