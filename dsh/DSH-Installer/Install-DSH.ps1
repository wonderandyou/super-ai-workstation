#Requires -Version 5.1
<#
================================================================================
  DSH（DeepSeek Harness）全自动安装脚本  ·  蓝色渐变图形界面版
  Install-DSH.ps1                                          Version 1.0.0
--------------------------------------------------------------------------------
  脚本做四件事（全部自动，不需要你敲命令）：
    1. 从 nodejs.org 下载并安装 Node.js（默认取官方最新 LTS；MSI 标准安装 / ZIP 便携安装）
    2. 执行  npm install -g @deepseek-ai/dsh
    3. 在装好的包里注入 “dsh web” 启动器，并创建桌面 / 开始菜单快捷方式
    4. 打开时弹出 DeepSeek 开放平台 API 密钥购买提示（含完整购买流程）

  提示：脚本只做上面这些事，不会上传任何信息；API 密钥只写入本机用户环境变量。
  普通用法：双击 Install-DSH.cmd 即可。
  静默用法（无界面，纯命令行日志）：
      powershell -NoProfile -STA -ExecutionPolicy Bypass -File .\Install-DSH.ps1 -Silent
================================================================================
#>
[CmdletBinding()]
param(
    # 安装方式：Msi = 官方 MSI 标准安装（需要管理员权限）；Zip = 免安装便携版（无需管理员权限）
    [ValidateSet('Msi', 'Zip')]
    [string]$NodeInstallMode = 'Msi',

    # 指定 Node.js 版本（例如 v24.21.0）。留空 = 自动获取官方最新 LTS
    [string]$NodeVersion = '',

    # 要安装的 npm 包名（图片里的命令就是 npm install -g @deepseek-ai/dsh）
    [string]$DshPackage = '@deepseek-ai/dsh',

    # dsh web 监听端口
    [int]$Port = 3080,

    # 可选：直接带上 API 密钥（sk-...），配合 -SaveApiKey 写入用户环境变量
    [string]$ApiKey = '',
    [switch]$SaveApiKey,

    # 使用 npmmirror 国内镜像加速 npm 安装（默认关闭，走官方源）
    [switch]$UseNpmMirror,

    # 静默安装：不显示界面，日志直接打印到控制台
    [switch]$Silent,

    # 跳过启动时的 API 密钥购买提示弹窗
    [switch]$SkipApiNotice,

    # 不创建桌面 / 开始菜单快捷方式
    [switch]$NoShortcuts,

    # 工作目录（下载缓存与安装日志都放这里）
    [string]$WorkRoot = (Join-Path $env:LOCALAPPDATA 'DSH-Installer')
)

# 允许脚本内部使用 TLS 1.2 / 1.3 访问 nodejs.org、registry.npmjs.org
try {
    [System.Net.ServicePointManager]::SecurityProtocol =
        [System.Net.SecurityProtocolType]::Tls12 -bor [System.Net.SecurityProtocolType]::Tls11
} catch { }

$ErrorActionPreference = 'Stop'

#region ───────────────────────────── 全局常量 ─────────────────────────────

$script:AppTitle = 'DSH 全自动安装程序'
$script:AppVersion = '1.0.0'

# 官方最新 LTS 获取失败时使用的兜底版本
$script:FallbackNode = 'v24.21.0'

# Node.js 最低要求（DSH 需要现代 Node 运行时）
$script:MinNodeMajor = 20

# DeepSeek 开放平台相关地址
$script:ApiPlatformUrl = 'https://platform.deepseek.com/'
$script:ApiKeysUrl = 'https://platform.deepseek.com/api_keys'
$script:ApiPricingUrl = 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing'
$script:ApiDocsUrl = 'https://api-docs.deepseek.com/zh-cn/'

# 需要在后台 runspace 里运行的核心函数清单（图形界面会把这些函数定义复制过去）
$script:CoreFunctions = @(
    'Write-DshLog'
    'Set-DshStage'
    'Get-DshHttpText'
    'Get-DshRemoteFile'
    'Test-DshAdmin'
    'Update-DshProcessPath'
    'Resolve-DshNodePaths'
    'Install-DshNodeMsi'
    'Install-DshNodeZip'
    'Install-DshNpmPackage'
    'New-DshShortcut'
    'Write-DshLauncherFile'
    'Install-DshWebLauncher'
    'Invoke-DshInstall'
    'Start-DshInstallWorker'
)

#endregion

#region ─────────────────────────── 核心安装逻辑 ───────────────────────────
# 下面的函数既能在图形界面（后台 runspace）里跑，也能在 -Silent 静默模式下跑。
# 所有状态都通过 $Ctx（普通哈希表）和 $Ctx.Sync（线程安全哈希表）传递。

function Write-DshLog {
    param(
        $Ctx,
        [string]$Message,
        [ValidateSet('info', 'ok', 'warn', 'error', 'npm')]
        [string]$Level = 'info'
    )
    $line = '[' + (Get-Date).ToString('HH:mm:ss') + '] ' + $Message

    if ($Ctx -and $Ctx.Sync) {
        try { $Ctx.Sync.Logs.Enqueue(@{ Level = $Level; Text = $line }) } catch { }
    }
    if ($Ctx -and $Ctx.Console) {
        switch ($Level) {
            'error' { Write-Host $line -ForegroundColor Red }
            'warn' { Write-Host $line -ForegroundColor Yellow }
            'ok' { Write-Host $line -ForegroundColor Green }
            'npm' { Write-Host ('        | ' + $Message) -ForegroundColor DarkGray }
            default { Write-Host $line }
        }
    }
    if ($Ctx -and $Ctx.LogPath) {
        try { Add-Content -LiteralPath $Ctx.LogPath -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue } catch { }
    }
}

function Set-DshStage {
    param(
        $Ctx,
        [string]$Status,
        [double]$Percent = -1,
        [switch]$Busy
    )
    if (-not $Ctx -or -not $Ctx.Sync) { return }
    if ($Status) { $Ctx.Sync.Status = $Status }
    if ($Percent -ge 0) { $Ctx.Sync.Percent = [Math]::Round($Percent, 1) }
    if ($PSBoundParameters.ContainsKey('Busy')) { $Ctx.Sync.Busy = [bool]$Busy }
}

function Get-DshHttpText {
    param([string]$Url, [int]$TimeoutSec = 30, $Ctx = $null)
    $req = [System.Net.HttpWebRequest]::Create($Url)
    $req.UserAgent = 'DSH-Installer/1.0'
    $req.Timeout = $TimeoutSec * 1000
    $req.AllowAutoRedirect = $true
    $resp = $req.GetResponse()
    try {
        $reader = New-Object System.IO.StreamReader($resp.GetResponseStream(), [System.Text.Encoding]::UTF8)
        try { return $reader.ReadToEnd() } finally { $reader.Close() }
    } finally { $resp.Close() }
}

# 分块下载，边下边把百分比打进界面；可选校验 SHA256
function Get-DshRemoteFile {
    param(
        $Ctx,
        [string]$Url,
        [string]$DestPath,
        [double]$PercentFrom = 0,
        [double]$PercentTo = 100,
        [string]$Label = '文件',
        [string]$ExpectedSha256 = ''
    )
    $req = [System.Net.HttpWebRequest]::Create($Url)
    $req.UserAgent = 'DSH-Installer/1.0'
    $req.Timeout = 60000
    $req.ReadWriteTimeout = 120000
    $req.AllowAutoRedirect = $true
    $resp = $req.GetResponse()
    $total = $resp.ContentLength
    try {
        $in = $resp.GetResponseStream()
        $out = [System.IO.File]::Create($DestPath)
        try {
            $buf = New-Object byte[] 131072
            $read = [long]0
            $lastReport = -100.0
            while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
                $out.Write($buf, 0, $n)
                $read += $n
                $pct = $PercentFrom
                if ($total -gt 0) {
                    $pct = $PercentFrom + ($PercentTo - $PercentFrom) * ($read / [double]$total)
                }
                if (($pct - $lastReport) -ge 0.8) {
                    $mb = [Math]::Round($read / 1MB, 1)
                    if ($total -gt 0) {
                        $totMb = [Math]::Round($total / 1MB, 1)
                        Set-DshStage -Ctx $Ctx -Status ("正在下载 $Label … $mb MB / $totMb MB") -Percent $pct -Busy
                    } else {
                        Set-DshStage -Ctx $Ctx -Status ("正在下载 $Label … $mb MB") -Percent $pct -Busy
                    }
                    $lastReport = $pct
                }
            }
        } finally { $out.Close() }
    } finally { $resp.Close() }

    if ($total -gt 0) {
        $realLen = (Get-Item -LiteralPath $DestPath).Length
        if ($realLen -ne $total) {
            throw ("下载不完整：期望 $total 字节，实际 $realLen 字节（$Url）")
        }
    }

    if ($ExpectedSha256) {
        $actual = (Get-FileHash -LiteralPath $DestPath -Algorithm SHA256).Hash
        if ($actual -ne $ExpectedSha256.ToUpperInvariant()) {
            Remove-Item -LiteralPath $DestPath -Force -ErrorAction SilentlyContinue
            throw ("SHA256 校验失败：官方值 $ExpectedSha256，实际 $actual")
        }
        Write-DshLog -Ctx $Ctx -Message 'SHA256 校验通过（文件未被篡改）' -Level 'ok'
    }
}

function Test-DshAdmin {
    try {
        $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object System.Security.Principal.WindowsPrincipal($id)
        return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return $false }
}

# 安装完 Node.js 后，当前进程的 PATH 还是旧的，需要重新从注册表读一次
function Update-DshProcessPath {
    $machine = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [System.Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = ($machine, $user | Where-Object { $_ }) -join ';'
}

function Resolve-DshNodePaths {
    param($Ctx)
    Update-DshProcessPath

    $candidates = @()
    if ($env:ProgramFiles) { $candidates += (Join-Path $env:ProgramFiles 'nodejs') }
    if (${env:ProgramFiles(x86)}) { $candidates += (Join-Path ${env:ProgramFiles(x86)} 'nodejs') }
    $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\nodejs')

    foreach ($dir in $candidates) {
        $exe = Join-Path $dir 'node.exe'
        if (Test-Path -LiteralPath $exe) {
            $Ctx.NodeDir = $dir
            $Ctx.NodeExe = $exe
            break
        }
    }

    if (-not $Ctx.NodeExe) {
        $whereNode = (Get-Command node.exe -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($whereNode) {
            $Ctx.NodeExe = $whereNode.Source
            $Ctx.NodeDir = Split-Path -Parent $whereNode.Source
        }
    }
    if (-not $Ctx.NodeExe) { throw '安装完成后仍找不到 node.exe，请手动重启电脑后重试（PATH 需要刷新）。' }

    # npm.cmd 一定和 node.exe 同目录
    $npm = Join-Path $Ctx.NodeDir 'npm.cmd'
    if (Test-Path -LiteralPath $npm) {
        $Ctx.NpmCmd = $npm
    } else {
        $whereNpm = (Get-Command npm.cmd -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($whereNpm) { $Ctx.NpmCmd = $whereNpm.Source } else { throw '找不到 npm.cmd，Node.js 可能没有安装完整。' }
    }

    # 把 Node 目录和 npm 全局命令目录补进用户 PATH（以后新开的命令行窗口也能直接用 node / npm / dsh）
    try {
        $userPath = [System.Environment]::GetEnvironmentVariable('Path', 'User')
        if (-not $userPath) { $userPath = '' }
        $parts = @($userPath.Split(';') | Where-Object { $_ })
        $added = @()
        foreach ($extra in @($Ctx.NodeDir, (Join-Path $env:APPDATA 'npm'))) {
            if (-not $extra) { continue }
            $found = $false
            foreach ($p in $parts) {
                if ($p.TrimEnd('\') -ieq $extra.TrimEnd('\')) { $found = $true }
            }
            if (-not $found) {
                $parts += $extra
                $added += $extra
            }
        }
        if ($added.Count -gt 0) {
            [System.Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User')
            foreach ($a in $added) { Write-DshLog -Ctx $Ctx -Message ('已把 ' + $a + ' 加入用户 PATH') }
        }
    } catch {
        Write-DshLog -Ctx $Ctx -Message ('写入用户 PATH 失败（不影响使用）：' + $_.Exception.Message) -Level 'warn'
    }

    $ver = (& $Ctx.NodeExe -v) 2>$null
    Write-DshLog -Ctx $Ctx -Message ("Node.js: " + $ver + "   路径: " + $Ctx.NodeExe) -Level 'ok'
    Write-DshLog -Ctx $Ctx -Message ("npm:    " + $Ctx.NpmCmd) -Level 'ok'

    $major = 0
    if ($ver -match '^v(\d+)') { $major = [int]$Matches[1] }
    if ($major -gt 0 -and $major -lt $Ctx.MinNodeMajor) {
        Write-DshLog -Ctx $Ctx -Message ("检测到 Node.js " + $ver + " 偏旧，DSH 建议 Node.js " + $Ctx.MinNodeMajor + " 及以上。") -Level 'warn'
    }
    $Ctx.NodeVersionInstalled = "$ver"
}

function Install-DshNodeMsi {
    param($Ctx, [string]$MsiPath)
    Set-DshStage -Ctx $Ctx -Status '步骤 4/7 · 正在静默安装 Node.js（如弹出 UAC 请点“是”）…' -Percent 55 -Busy
    $msiLog = Join-Path $Ctx.WorkRoot 'msiexec.log'
    $arguments = @(
        '/i', ('"' + $MsiPath + '"'),
        '/qn', '/norestart', '/l*v', ('"' + $msiLog + '"'),
        'ADDLOCAL=ALL'
    )
    $p = Start-Process -FilePath 'msiexec.exe' -ArgumentList $arguments -Wait -PassThru
    $code = $p.ExitCode
    Write-DshLog -Ctx $Ctx -Message ("msiexec 退出代码：" + $code)

    # 0 = 成功，3010 = 成功但需要重启
    if ($code -ne 0 -and $code -ne 3010) {
        throw ("Node.js MSI 安装失败（退出代码 " + $code + "），详细日志：" + $msiLog)
    }

    # msiexec 有时会提前返回，等一下 node.exe 真正出现
    $exe = Join-Path $env:ProgramFiles 'nodejs\node.exe'
    for ($i = 0; $i -lt 60 -and -not (Test-Path -LiteralPath $exe); $i++) { Start-Sleep -Milliseconds 500 }
    if (-not (Test-Path -LiteralPath $exe)) {
        throw ('Node.js 安装程序已返回，但 ' + $exe + ' 不存在，请手动重启后重试。')
    }
    Write-DshLog -Ctx $Ctx -Message 'Node.js MSI 安装完成' -Level 'ok'
}

function Install-DshNodeZip {
    param($Ctx, [string]$ZipPath)
    Set-DshStage -Ctx $Ctx -Status '步骤 4/7 · 正在解压 Node.js 便携版…' -Percent 55 -Busy
    $target = Join-Path $env:LOCALAPPDATA 'Programs\nodejs'
    $extract = Join-Path $Ctx.WorkRoot 'node-extract'
    if (Test-Path -LiteralPath $extract) { Remove-Item -LiteralPath $extract -Recurse -Force }
    New-Item -ItemType Directory -Path $extract -Force | Out-Null

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($ZipPath, $extract)

    $inner = Get-ChildItem -LiteralPath $extract -Directory | Select-Object -First 1
    if (-not $inner) { throw 'ZIP 解压结果异常：找不到 node 目录。' }
    if (-not (Test-Path -LiteralPath $target)) { New-Item -ItemType Directory -Path $target -Force | Out-Null }
    Copy-Item -Path (Join-Path $inner.FullName '*') -Destination $target -Recurse -Force
    Remove-Item -LiteralPath $extract -Recurse -Force -ErrorAction SilentlyContinue
    Write-DshLog -Ctx $Ctx -Message ('Node.js 便携版已释放到 ' + $target) -Level 'ok'
}

function Install-DshNpmPackage {
    param($Ctx)
    Set-DshStage -Ctx $Ctx -Status '步骤 5/7 · 正在执行 npm install -g @deepseek-ai/dsh（首次可能要几分钟）…' -Percent 70 -Busy

    $npmArgs = @('install', '-g', $Ctx.Package, '--no-fund', '--no-audit')

    # ── 放开被 npm 默认拦截的依赖安装脚本 ──────────────────────────────────
    # 新版 npm（11.5+）默认不跑依赖的 preinstall/install/postinstall 脚本。
    # 而 DSH 依赖的几个包必须跑脚本才能用：
    #   node-pty                  终端 / 执行命令（DSH 的核心功能）
    #   koffi                     FFI 本地调用
    #   @deepseek-ai/dsh-subprocess-local   本地子进程
    #   @google/genai / protobufjs          预编译产物
    # 不放开的话：装完看着一切正常，但「让 DSH 跑命令」会失败。
    $allowList = '@deepseek-ai/dsh-subprocess-local,koffi,node-pty,@google/genai,protobufjs'
    $npmVer = ''
    try { $npmVer = (& $Ctx.NpmCmd -v 2>$null | Select-Object -Last 1) } catch { }
    $supportsAllow = $false
    if ($npmVer) {
        $vv = ($npmVer -replace '^v', '').Trim()
        $pp = $vv.Split('.')
        if ($pp.Count -ge 2) {
            try {
                $maj = [int]$pp[0]; $min = [int]$pp[1]
                if ($maj -gt 11 -or ($maj -eq 11 -and $min -ge 5)) { $supportsAllow = $true }
            } catch { }
        }
    }
    if ($supportsAllow) {
        $npmArgs += ('--allow-scripts=' + $allowList)
        Write-DshLog -Ctx $Ctx -Message ('npm ' + $npmVer + '：已带上 --allow-scripts（放开 node-pty 等原生模块构建）') -Level 'ok'
    } else {
        Write-DshLog -Ctx $Ctx -Message ('npm ' + $npmVer + ' 不支持 --allow-scripts（需 11.5+），跳过；若终端功能异常请手动放开脚本') -Level 'warn'
    }

    if ($Ctx.UseNpmMirror) { $npmArgs += @('--registry', 'https://registry.npmmirror.com') }

    Write-DshLog -Ctx $Ctx -Message ('执行命令：npm ' + ($npmArgs -join ' '))

    $npmCli = Join-Path $Ctx.NodeDir 'node_modules\npm\bin\npm-cli.js'
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.WorkingDirectory = $Ctx.WorkRoot
    # 必须先开启重定向，否则设置 StandardOutputEncoding 会在 Start() 抛
    # “只有在重定向标准输出时才支持 StandardOutputEncoding”。
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8

    if (Test-Path -LiteralPath $npmCli) {
        # 直接用 node 跑 npm 的 JS 入口，最稳（避开 .cmd 批处理的引号问题）
        $psi.FileName = $Ctx.NodeExe
        $psi.Arguments = ('"' + $npmCli + '" ' + ($npmArgs -join ' '))
    } else {
        $psi.FileName = $env:ComSpec
        $psi.Arguments = ('/d /s /c ""' + $Ctx.NpmCmd + '" ' + ($npmArgs -join ' ') + '"')
    }

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()
    # 两个管道都用异步读取，避免输出过多时死锁
    $outTask = $proc.StandardOutput.ReadToEndAsync()
    $errTask = $proc.StandardError.ReadToEndAsync()
    $started = Get-Date
    while (-not $proc.HasExited) {
        Start-Sleep -Milliseconds 500
        $elapsed = [int]((Get-Date) - $started).TotalSeconds
        Set-DshStage -Ctx $Ctx -Status ("步骤 5/7 · npm 正在下载并安装依赖… 已用时 " + $elapsed + " 秒（npm 结束后一次性显示输出）") -Percent 72 -Busy
        if ($elapsed -gt 3600) { try { $proc.Kill() } catch { }; throw 'npm 安装超过 1 小时，已中止。' }
    }
    $proc.WaitForExit()
    $exitCode = $proc.ExitCode
    $stdout = ''
    $stderr = ''
    try { $stdout = $outTask.Result } catch { }
    try { $stderr = $errTask.Result } catch { }

    if ($stdout) {
        foreach ($ln in ($stdout -split "`r?`n")) {
            if ($ln.Trim()) { Write-DshLog -Ctx $Ctx -Message $ln -Level 'npm' }
        }
    }
    if ($stderr) {
        foreach ($ln in ($stderr -split "`r?`n")) {
            if ($ln.Trim()) { Write-DshLog -Ctx $Ctx -Message $ln -Level 'npm' }
        }
    }

    if ($exitCode -ne 0) {
        throw ('npm install 失败（退出代码 ' + $exitCode + '）。常见原因：网络无法访问 registry.npmjs.org，可改用国内镜像后重试。')
    }
    Write-DshLog -Ctx $Ctx -Message 'npm 全局安装完成' -Level 'ok'
}

function New-DshShortcut {
    param(
        [string]$Path,
        [string]$TargetPath,
        [string]$Arguments = '',
        [string]$WorkingDirectory = '',
        [string]$IconPath = '',
        [string]$Description = ''
    )
    try {
        $shell = New-Object -ComObject WScript.Shell
        $lnk = $shell.CreateShortcut($Path)
        $lnk.TargetPath = $TargetPath
        if ($Arguments) { $lnk.Arguments = $Arguments }
        if ($WorkingDirectory) { $lnk.WorkingDirectory = $WorkingDirectory }
        if ($IconPath) { $lnk.IconLocation = $IconPath }
        if ($Description) { $lnk.Description = $Description }
        $lnk.Save()
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
        return $true
    } catch {
        return $false
    }
}

# 生成 “dsh web” 启动器（纯 ASCII 内容，避免任何代码页乱码问题）
function Write-DshLauncherFile {
    param([string]$Path, [string]$DshCmd, [int]$Port)

    $text = @'
@echo off
chcp 65001 >nul
setlocal EnableExtensions
title DSH Web  -  DeepSeek Harness

rem ============================================================
rem  dsh web launcher  (injected by Install-DSH.ps1 v1.0.0)
rem  Edit DSH_PORT below to change the listen port.
rem ============================================================

set "DSH_CMD=__DSH_CMD__"
if not exist "%DSH_CMD%" set "DSH_CMD="
if not defined DSH_CMD for /f "delims=" %%I in ('where dsh.cmd 2^>nul') do if not defined DSH_CMD set "DSH_CMD=%%I"

if not defined DSH_CMD (
  echo.
  echo [ERROR] dsh.cmd not found. Re-run the DSH installer ^(Install-DSH.cmd^).
  echo         Official alternative without global install:
  echo             npx -y @deepseek-ai/dsh web
  echo.
  pause
  exit /b 1
)

set "DSH_PORT=__PORT__"
if "%~1"=="" ( set "DSH_ARGS=web --port %DSH_PORT%" ) else ( set "DSH_ARGS=%*" )

echo ============================================================
echo   DeepSeek Harness - Web UI
echo   command : "%DSH_CMD%" %DSH_ARGS%
echo   The browser opens automatically with an authorized URL.
echo   Keep this window open while using DSH. Ctrl+C stops it.
echo ============================================================
echo.

call "%DSH_CMD%" %DSH_ARGS%
set "DSH_EXIT=%ERRORLEVEL%"

echo.
if not "%DSH_EXIT%"=="0" (
  echo [ERROR] dsh web exited with code %DSH_EXIT%.
  echo         - API key problems: see the DeepSeek platform purchase guide.
  echo         - "frontend is not built" / missing files: reinstall DSH.
) else (
  echo dsh web stopped.
)
echo.
pause
'@

    $text = $text.Replace('__DSH_CMD__', $DshCmd).Replace('__PORT__', "$Port")
    # 批处理文件统一用 CRLF，避免部分 Windows 版本解析异常
    $text = ($text -replace "`r`n", "`n") -replace "`n", "`r`n"
    [System.IO.File]::WriteAllText($Path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

function Install-DshWebLauncher {
    param($Ctx)

    # 1) 定位 dsh 全局安装位置
    $prefix = ''
    try {
        $prefix = (& $Ctx.NpmCmd prefix -g 2>$null | Select-Object -Last 1)
        if ($prefix) { $prefix = $prefix.Trim() }
    } catch { }
    if (-not $prefix) { $prefix = Join-Path $env:APPDATA 'npm' }
    $Ctx.NpmPrefix = $prefix

    $pkgDir = Join-Path $prefix 'node_modules\@deepseek-ai\dsh'
    $Ctx.PkgDir = $pkgDir
    $dshCmd = Join-Path $prefix 'dsh.cmd'

    if (-not (Test-Path -LiteralPath $pkgDir)) {
        throw ('找不到已安装的 dsh 包目录：' + $pkgDir)
    }
    if (-not (Test-Path -LiteralPath $dshCmd)) {
        Write-DshLog -Ctx $Ctx -Message ('警告：未找到 ' + $dshCmd + '，启动器将改为在 PATH 中查找 dsh.cmd') -Level 'warn'
    }

    # 2) 把启动器“塞进”这个包里
    $inPkg = Join-Path $pkgDir 'dsh-web.cmd'
    Write-DshLauncherFile -Path $inPkg -DshCmd $dshCmd -Port $Ctx.Port
    Write-DshLog -Ctx $Ctx -Message ('已写入包内启动器：' + $inPkg) -Level 'ok'

    # 3) 同时在 npm 全局目录放一份，方便从命令行调用
    $inPrefix = Join-Path $prefix 'dsh-web.cmd'
    Write-DshLauncherFile -Path $inPrefix -DshCmd $dshCmd -Port $Ctx.Port
    Write-DshLog -Ctx $Ctx -Message ('已写入全局启动器：' + $inPrefix) -Level 'ok'

    # 4) 包内留一份中文说明
    $note = @"
DSH Web 启动器（由 Install-DSH.ps1 自动注入）
==================================================
双击 dsh-web.cmd 即可启动 DeepSeek Harness 的网页界面。

· 默认监听端口：$($Ctx.Port)（地址 http://127.0.0.1:$($Ctx.Port)）
· 浏览器会自动打开带授权令牌的地址；手动打开首页是打不开的，请用自动打开的那个地址。
· 想换端口：用记事本打开 dsh-web.cmd，改 DSH_PORT= 那一行。
· 想传别的参数：dsh-web.cmd --port 8080 或 dsh-web.cmd --no-open
· 关闭启动器的黑窗口 = 停止 DSH 服务。

dsh 包目录：$pkgDir
npm 全局目录：$prefix
"@
    try {
        [System.IO.File]::WriteAllText((Join-Path $pkgDir 'dsh-web-启动器说明.txt'), $note, (New-Object System.Text.UTF8Encoding($true)))
    } catch { }

    $Ctx.LauncherPath = $inPkg

    # 5) 桌面 / 开始菜单快捷方式
    if (-not $Ctx.NoShortcuts) {
        $icon = if (Test-Path -LiteralPath $Ctx.NodeExe) { $Ctx.NodeExe } else { '' }
        $desktop = [System.Environment]::GetFolderPath('Desktop')
        $ok1 = New-DshShortcut -Path (Join-Path $desktop 'DSH Web.lnk') -TargetPath $inPkg `
            -WorkingDirectory $pkgDir -IconPath $icon -Description 'DeepSeek Harness 网页界面'
        $programs = [System.Environment]::GetFolderPath('Programs')
        $groupDir = Join-Path $programs 'DSH'
        if (-not (Test-Path -LiteralPath $groupDir)) { New-Item -ItemType Directory -Path $groupDir -Force | Out-Null }
        $ok2 = New-DshShortcut -Path (Join-Path $groupDir 'DSH Web.lnk') -TargetPath $inPkg `
            -WorkingDirectory $pkgDir -IconPath $icon -Description 'DeepSeek Harness 网页界面'
        $ok3 = New-DshShortcut -Path (Join-Path $groupDir 'DeepSeek 开放平台（购买 API 密钥）.lnk') `
            -TargetPath $Ctx.ApiPlatformUrl -Description '购买 / 申请 DeepSeek API 密钥'
        if ($ok1) { Write-DshLog -Ctx $Ctx -Message '已创建桌面快捷方式：DSH Web' -Level 'ok' }
        if ($ok2) { Write-DshLog -Ctx $Ctx -Message '已创建开始菜单快捷方式：DSH > DSH Web' -Level 'ok' }
    }
}

function Start-DshInstallWorker {
    param($Sync, $Options)
    $Ctx = @{
        Sync        = $Sync
        Console     = [bool]$Options.Console
        LogPath     = $Options.LogPath
        WorkRoot    = $Options.WorkRoot
        Package     = $Options.Package
        Port        = [int]$Options.Port
        NodeVersion = $Options.NodeVersion
        Mode        = $Options.Mode
        ApiKey      = $Options.ApiKey
        SaveKey     = [bool]$Options.SaveKey
        UseNpmMirror = [bool]$Options.UseNpmMirror
        NoShortcuts = [bool]$Options.NoShortcuts
        MinNodeMajor = [int]$Options.MinNodeMajor
        FallbackNode = [string]$Options.FallbackNode
        ApiPlatformUrl = [string]$Options.ApiPlatformUrl
        NodeExe     = ''
        NodeDir     = ''
        NpmCmd      = ''
        NpmPrefix   = ''
        PkgDir      = ''
        LauncherPath = ''
        Admin       = $false
    }
    try {
        Invoke-DshInstall -Ctx $Ctx
        $Sync.Ok = $true
        $Sync.Status = '全部完成'
        $Sync.Percent = 100
        $Sync.Busy = $false
        $Sync.Results = @{
            NodeExe = $Ctx.NodeExe
            NodeDir = $Ctx.NodeDir
            PkgDir = $Ctx.PkgDir
            NpmPrefix = $Ctx.NpmPrefix
            LauncherPath = $Ctx.LauncherPath
            NodeVersion = $Ctx.NodeVersionInstalled
        }
    } catch {
        $Sync.Ok = $false
        $Sync.Error = $_.Exception.Message
        Write-DshLog -Ctx $Ctx -Message ('安装失败：' + $_.Exception.Message) -Level 'error'
        if ($_.ScriptStackTrace) { Write-DshLog -Ctx $Ctx -Message $_.ScriptStackTrace -Level 'error' }
    } finally {
        $Sync.Done = $true
        $Sync.Busy = $false
    }
}

function Invoke-DshInstall {
    param($Ctx)

    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    # ── 步骤 1/7：环境检查 ────────────────────────────────────────────────
    Set-DshStage -Ctx $Ctx -Status '步骤 1/7 · 检查系统环境…' -Percent 2 -Busy
    Write-DshLog -Ctx $Ctx -Message ('=== DSH 安装开始 ' + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' ===')
    Write-DshLog -Ctx $Ctx -Message ('操作系统：' + [System.Environment]::OSVersion.VersionString)
    Write-DshLog -Ctx $Ctx -Message ('CPU 架构：' + $env:PROCESSOR_ARCHITECTURE)

    if (-not [System.Environment]::Is64BitOperatingSystem) {
        throw '本脚本只支持 64 位 Windows。'
    }

    try {
        $drive = (Get-Item -LiteralPath $Ctx.WorkRoot -ErrorAction SilentlyContinue)
        if (-not $drive) {
            $root = [System.IO.Path]::GetPathRoot($Ctx.WorkRoot)
            $drive = Get-PSDrive -Name $root.TrimEnd(':', '\') -ErrorAction SilentlyContinue
        }
        if ($drive -and $drive.Free -and $drive.Free -lt 2GB) {
            throw ('磁盘剩余空间不足（需要至少 2 GB）：' + $drive.Free + ' 字节可用。')
        }
    } catch [System.Management.Automation.RuntimeException] {
        throw
    } catch { }

    if (-not (Test-Path -LiteralPath $Ctx.WorkRoot)) { New-Item -ItemType Directory -Path $Ctx.WorkRoot -Force | Out-Null }

    $Ctx.Admin = Test-DshAdmin
    if ($Ctx.Admin) {
        Write-DshLog -Ctx $Ctx -Message '当前以管理员身份运行 ✔'
    } else {
        Write-DshLog -Ctx $Ctx -Message '当前不是管理员身份。' -Level 'warn'
        if ($Ctx.Mode -eq 'Msi') {
            $Ctx.Mode = 'Zip'
            Write-DshLog -Ctx $Ctx -Message '已自动切换为「便携安装(ZIP)」方式，无需管理员权限。' -Level 'warn'
        }
    }

    # Node.js 架构
    $arch = 'x64'
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { $arch = 'arm64' }
    $Ctx.Arch = $arch
    Write-DshLog -Ctx $Ctx -Message ('将安装 Node.js 架构：' + $arch + '，方式：' + $Ctx.Mode)

    # ── 步骤 2/7：确定 Node.js 版本 ───────────────────────────────────────
    Set-DshStage -Ctx $Ctx -Status '步骤 2/7 · 获取 Node.js 最新 LTS 版本号…' -Percent 6 -Busy
    $version = $Ctx.NodeVersion
    if ($version) {
        if ($version -notmatch '^v') { $version = 'v' + $version }
        Write-DshLog -Ctx $Ctx -Message ('使用指定版本：' + $version)
    } else {
        try {
            Write-DshLog -Ctx $Ctx -Message '正在查询 https://nodejs.org/dist/index.json …'
            $json = Get-DshHttpText -Url 'https://nodejs.org/dist/index.json' -TimeoutSec 30 -Ctx $Ctx
            $all = $json | ConvertFrom-Json
            $lts = $all | Where-Object { $_.lts } | Select-Object -First 1
            if (-not $lts) { throw '官网返回的数据里没有 LTS 版本。' }
            $version = $lts.version
            Write-DshLog -Ctx $Ctx -Message ('最新 LTS：' + $version + '（' + $lts.lts + '，内置 npm ' + $lts.npm + '）') -Level 'ok'
        } catch {
            $version = $Ctx.FallbackNode
            Write-DshLog -Ctx $Ctx -Message ('无法获取最新 LTS（' + $_.Exception.Message + '），改用内置版本 ' + $version) -Level 'warn'
        }
    }

    # ── 步骤 3/7：下载 Node.js ────────────────────────────────────────────
    Set-DshStage -Ctx $Ctx -Status ('步骤 3/7 · 准备下载 Node.js ' + $version + '…') -Percent 9 -Busy
    if ($Ctx.Mode -eq 'Msi') {
        $fileName = 'node-' + $version + '-' + $arch + '.msi'
    } else {
        $fileName = 'node-' + $version + '-win-' + $arch + '.zip'
    }
    $url = 'https://nodejs.org/dist/' + $version + '/' + $fileName
    $dest = Join-Path $Ctx.WorkRoot $fileName

    # 先拿官方 SHA256，顺便做完整性校验
    $expected = ''
    try {
        $sums = Get-DshHttpText -Url ('https://nodejs.org/dist/' + $version + '/SHASUMS256.txt') -TimeoutSec 30 -Ctx $Ctx
        foreach ($ln in ($sums -split "`n")) {
            if ($ln -match ('^([0-9a-fA-F]{64})\s+\*?' + [regex]::Escape($fileName) + '\s*$')) {
                $expected = $Matches[1].ToUpperInvariant()
                break
            }
        }
        if ($expected) { Write-DshLog -Ctx $Ctx -Message ('官方 SHA256：' + $expected) }
    } catch {
        Write-DshLog -Ctx $Ctx -Message ('获取官方校验值失败（不影响安装）：' + $_.Exception.Message) -Level 'warn'
    }

    $needDownload = $true
    if (Test-Path -LiteralPath $dest) {
        if ($expected -and (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash -eq $expected) {
            $needDownload = $false
            Write-DshLog -Ctx $Ctx -Message ('发现已下载且校验一致的安装包，跳过下载：' + $dest) -Level 'ok'
            Set-DshStage -Ctx $Ctx -Status '步骤 3/7 · 复用已下载的安装包' -Percent 48
        } else {
            Write-DshLog -Ctx $Ctx -Message '已存在同名文件但校验不一致，重新下载。' -Level 'warn'
            Remove-Item -LiteralPath $dest -Force -ErrorAction SilentlyContinue
        }
    }

    if ($needDownload) {
        Write-DshLog -Ctx $Ctx -Message ('下载地址：' + $url)
        Get-DshRemoteFile -Ctx $Ctx -Url $url -DestPath $dest -PercentFrom 10 -PercentTo 48 `
            -Label ('Node.js ' + $version) -ExpectedSha256 $expected
        Write-DshLog -Ctx $Ctx -Message ('下载完成：' + $dest) -Level 'ok'
    }

    # ── 步骤 4/7：安装 Node.js ────────────────────────────────────────────
    $installed = $false
    if ($Ctx.Mode -eq 'Msi') {
        try {
            Install-DshNodeMsi -Ctx $Ctx -MsiPath $dest
            $installed = $true
        } catch {
            Write-DshLog -Ctx $Ctx -Message ('MSI 安装失败：' + $_.Exception.Message) -Level 'error'
            Write-DshLog -Ctx $Ctx -Message '自动回退到「便携安装(ZIP)」方式…' -Level 'warn'
            $Ctx.Mode = 'Zip'
        }
    }

    if (-not $installed) {
        $zipName = 'node-' + $version + '-win-' + $arch + '.zip'
        $zipPath = Join-Path $Ctx.WorkRoot $zipName
        if ($Ctx.Mode -eq 'Msi') { $Ctx.Mode = 'Zip' }
        if (-not (Test-Path -LiteralPath $zipPath)) {
            $zipUrl = 'https://nodejs.org/dist/' + $version + '/' + $zipName
            Write-DshLog -Ctx $Ctx -Message ('下载便携版：' + $zipUrl)
            $zipExpected = ''
            try {
                $sums2 = Get-DshHttpText -Url ('https://nodejs.org/dist/' + $version + '/SHASUMS256.txt') -TimeoutSec 30 -Ctx $Ctx
                foreach ($ln in ($sums2 -split "`n")) {
                    if ($ln -match ('^([0-9a-fA-F]{64})\s+\*?' + [regex]::Escape($zipName) + '\s*$')) { $zipExpected = $Matches[1].ToUpperInvariant(); break }
                }
            } catch { }
            Get-DshRemoteFile -Ctx $Ctx -Url $zipUrl -DestPath $zipPath -PercentFrom 50 -PercentTo 62 `
                -Label ('Node.js ' + $version + ' 便携版') -ExpectedSha256 $zipExpected
        } else {
            Write-DshLog -Ctx $Ctx -Message ('复用已下载的便携版：' + $zipPath) -Level 'ok'
        }
        Install-DshNodeZip -Ctx $Ctx -ZipPath $zipPath
    }

    Set-DshStage -Ctx $Ctx -Status '步骤 4/7 · 检查 node / npm 是否可用…' -Percent 64 -Busy
    Resolve-DshNodePaths -Ctx $Ctx

    # ── 步骤 5/7：npm 安装 DSH ────────────────────────────────────────────
    Install-DshNpmPackage -Ctx $Ctx

    # ── 步骤 6/7：验证安装 ────────────────────────────────────────────────
    Set-DshStage -Ctx $Ctx -Status '步骤 6/7 · 验证 dsh 是否安装成功…' -Percent 90 -Busy
    $prefix = (& $Ctx.NpmCmd prefix -g 2>$null | Select-Object -Last 1)
    if ($prefix) { $prefix = $prefix.Trim() } else { $prefix = Join-Path $env:APPDATA 'npm' }
    $Ctx.NpmPrefix = $prefix
    $pkgDir = Join-Path $prefix 'node_modules\@deepseek-ai\dsh'
    $Ctx.PkgDir = $pkgDir
    if (-not (Test-Path -LiteralPath $pkgDir)) { throw ('npm 报告安装成功，但找不到包目录：' + $pkgDir) }

    $pkgJson = Join-Path $pkgDir 'package.json'
    if (Test-Path -LiteralPath $pkgJson) {
        try {
            $meta = Get-Content -LiteralPath $pkgJson -Raw -Encoding UTF8 | ConvertFrom-Json
            Write-DshLog -Ctx $Ctx -Message ('已安装 ' + $meta.name + ' ' + $meta.version) -Level 'ok'
        } catch { }
    }

    # 用官方入口做一次 --help 冒烟测试（只打印帮助，不会启动服务）
    $binJs = Join-Path $pkgDir 'lib\bin.js'
    if (Test-Path -LiteralPath $binJs) {
        try {
            $out = & $Ctx.NodeExe $binJs '--help' 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-DshLog -Ctx $Ctx -Message 'dsh 可执行入口自检通过（dsh --help 正常返回）' -Level 'ok'
            } else {
                Write-DshLog -Ctx $Ctx -Message ('dsh --help 返回代码 ' + $LASTEXITCODE + '，请留意后续使用。') -Level 'warn'
            }
            if ($out) { Write-DshLog -Ctx $Ctx -Message ('提示：可用命令示例 → dsh web / dsh --profile headless "任务"') }
        } catch {
            Write-DshLog -Ctx $Ctx -Message ('自检失败（可以忽略）：' + $_.Exception.Message) -Level 'warn'
        }
    }

    # ── 步骤 7/7：注入 dsh web 启动器 ─────────────────────────────────────
    Set-DshStage -Ctx $Ctx -Status '步骤 7/7 · 正在把 dsh web 启动器写入 DSH 包…' -Percent 95 -Busy
    Install-DshWebLauncher -Ctx $Ctx

    # 可选：把 API 密钥写进用户环境变量
    if ($Ctx.SaveKey -and $Ctx.ApiKey) {
        try {
            [System.Environment]::SetEnvironmentVariable('DEEPSEEK_API_KEY', $Ctx.ApiKey, 'User')
            $masked = $Ctx.ApiKey.Substring(0, [Math]::Min(6, $Ctx.ApiKey.Length)) + '****'
            Write-DshLog -Ctx $Ctx -Message ('已把 API 密钥写入用户环境变量 DEEPSEEK_API_KEY（' + $masked + '，长度 ' + $Ctx.ApiKey.Length + '）') -Level 'ok'
            Write-DshLog -Ctx $Ctx -Message '注意：已经打开的窗口读不到新变量，请用新启动的 DSH Web 生效。' -Level 'warn'
        } catch {
            Write-DshLog -Ctx $Ctx -Message ('写入环境变量失败：' + $_.Exception.Message) -Level 'warn'
        }
    }

    $sw.Stop()
    $mins = [Math]::Round($sw.Elapsed.TotalMinutes, 1)
    Write-DshLog -Ctx $Ctx -Message ('=== 安装完成，总耗时 ' + $mins + ' 分钟 ===') -Level 'ok'
    Write-DshLog -Ctx $Ctx -Message ('启动方式 1：双击桌面「DSH Web」快捷方式')
    Write-DshLog -Ctx $Ctx -Message ('启动方式 2：双击 ' + $Ctx.LauncherPath)
    Write-DshLog -Ctx $Ctx -Message ('启动方式 3：命令行执行 dsh web（默认端口 ' + $Ctx.Port + '）')
}

#endregion

#region ───────────────────────── 静默（无界面）模式 ─────────────────────────

function Invoke-DshSilent {
    if (-not (Test-Path -LiteralPath $WorkRoot)) { New-Item -ItemType Directory -Path $WorkRoot -Force | Out-Null }
    $logPath = Join-Path $WorkRoot ('install-' + (Get-Date).ToString('yyyyMMdd-HHmmss') + '.log')
    $sync = [hashtable]::Synchronized(@{
            Status  = '静默安装中'
            Percent = 0
            Logs    = [System.Collections.Queue]::Synchronized((New-Object System.Collections.Queue))
            Done    = $false
            Ok      = $false
            Error   = ''
            Busy    = $false
            Results = @{}
        })

    $options = @{
        Console      = $true
        LogPath      = $logPath
        WorkRoot     = $WorkRoot
        Package      = $DshPackage
        Port         = $Port
        NodeVersion  = $NodeVersion
        Mode         = $NodeInstallMode
        ApiKey       = $ApiKey
        SaveKey      = [bool]$SaveApiKey
        UseNpmMirror = [bool]$UseNpmMirror
        NoShortcuts  = [bool]$NoShortcuts
        MinNodeMajor = $script:MinNodeMajor
        FallbackNode = $script:FallbackNode
        ApiPlatformUrl = $script:ApiPlatformUrl
    }

    Write-Host ''
    Write-Host '================================================================' -ForegroundColor Cyan
    Write-Host '  DSH 静默安装模式（无界面）' -ForegroundColor Cyan
    Write-Host ('  日志文件：' + $logPath) -ForegroundColor Cyan
    Write-Host '================================================================' -ForegroundColor Cyan

    Start-DshInstallWorker -Sync $sync -Options $options

    Write-Host ''
    if ($sync.Ok) {
        Write-Host '安装成功 ✔  可用 `dsh web` 或桌面快捷方式「DSH Web」启动。' -ForegroundColor Green
        return 0
    } else {
        Write-Host ('安装失败：' + $sync.Error) -ForegroundColor Red
        Write-Host ('详见日志：' + $logPath) -ForegroundColor Red
        return 1
    }
}

#endregion

#region ─────────────────────────── 图形界面模式 ───────────────────────────

$script:XamlMain = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="DSH 全自动安装程序"
        Width="880" Height="660"
        WindowStartupLocation="CenterScreen"
        WindowStyle="None" AllowsTransparency="True" Background="Transparent"
        ResizeMode="NoResize" FontFamily="Microsoft YaHei UI, Segoe UI" UseLayoutRounding="True"
        TextOptions.TextFormattingMode="Display">
  <Window.Resources>
    <LinearGradientBrush x:Key="PrimaryBrush" StartPoint="0,0" EndPoint="1,0">
      <GradientStop Color="#7FD8FF" Offset="0"/>
      <GradientStop Color="#E8F7FF" Offset="1"/>
    </LinearGradientBrush>
    <Style x:Key="PrimaryButton" TargetType="Button">
      <Setter Property="Foreground" Value="#08306B"/>
      <Setter Property="FontSize" Value="14"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="Bd" CornerRadius="9" Padding="20,10"
                    Background="#FFFFFF" BorderBrush="#BBDDFF" BorderThickness="1">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Bd" Property="Background" Value="#DCEFFF"/>
              </Trigger>
              <Trigger Property="IsEnabled" Value="False">
                <Setter TargetName="Bd" Property="Opacity" Value="0.4"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="GhostButton" TargetType="Button">
      <Setter Property="Foreground" Value="#EAF4FF"/>
      <Setter Property="FontSize" Value="13"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="Bd" CornerRadius="9" Padding="16,9"
                    Background="#1FFFFFFF" BorderBrush="#7FB6F0" BorderThickness="1">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Bd" Property="Background" Value="#38FFFFFF"/>
              </Trigger>
              <Trigger Property="IsEnabled" Value="False">
                <Setter TargetName="Bd" Property="Opacity" Value="0.4"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="TitleBarButton" TargetType="Button">
      <Setter Property="Foreground" Value="#D8ECFF"/>
      <Setter Property="FontSize" Value="15"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="Bd" Width="34" Height="26" CornerRadius="6" Background="Transparent">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Bd" Property="Background" Value="#40FFFFFF"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="BarStyle" TargetType="ProgressBar">
      <Setter Property="Height" Value="12"/>
      <Setter Property="Background" Value="#1E3F73"/>
      <Setter Property="Foreground" Value="{StaticResource PrimaryBrush}"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="ProgressBar">
            <Border Background="{TemplateBinding Background}" CornerRadius="6" ClipToBounds="True">
              <Grid>
                <Rectangle x:Name="PART_Track"/>
                <Rectangle x:Name="PART_Indicator" HorizontalAlignment="Left"
                           Fill="{TemplateBinding Foreground}" RadiusX="6" RadiusY="6"/>
              </Grid>
            </Border>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
  </Window.Resources>

  <Border x:Name="RootCard" CornerRadius="16" Margin="14">
    <Border.Background>
      <LinearGradientBrush StartPoint="0,0" EndPoint="1,1">
        <GradientStop Color="#04122E" Offset="0"/>
        <GradientStop Color="#0A3D8F" Offset="0.45"/>
        <GradientStop Color="#1479D7" Offset="0.78"/>
        <GradientStop Color="#4FC3F7" Offset="1"/>
      </LinearGradientBrush>
    </Border.Background>
    <Border.Effect>
      <DropShadowEffect BlurRadius="22" ShadowDepth="0" Opacity="0.45" Color="#04122E"/>
    </Border.Effect>

    <Grid>
      <Grid.RowDefinitions>
        <RowDefinition Height="48"/>
        <RowDefinition Height="Auto"/>
        <RowDefinition Height="Auto"/>
        <RowDefinition Height="Auto"/>
        <RowDefinition Height="*"/>
        <RowDefinition Height="Auto"/>
      </Grid.RowDefinitions>

      <!-- 标题栏 -->
      <Grid x:Name="TitleBar" Grid.Row="0" Background="Transparent">
        <TextBlock Text="DSH 全自动安装程序" Margin="22,0,0,0" VerticalAlignment="Center"
                   FontSize="13" Foreground="#CFE6FF"/>
        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" VerticalAlignment="Center" Margin="0,0,12,0">
          <Button x:Name="BtnMin" Style="{StaticResource TitleBarButton}" Content="—" ToolTip="最小化"/>
          <Button x:Name="BtnClose" Style="{StaticResource TitleBarButton}" Content="✕" ToolTip="关闭"/>
        </StackPanel>
      </Grid>

      <!-- 大标题 -->
      <StackPanel Grid.Row="1" Margin="30,6,30,0">
        <TextBlock Text="DeepSeek Harness 一键安装" FontSize="29" FontWeight="Bold" Foreground="#FFFFFF"/>
        <TextBlock Margin="0,8,0,0" FontSize="13" Foreground="#D6EAFF" TextWrapping="Wrap"
                   Text="自动下载 Node.js ▸ 执行 npm install -g @deepseek-ai/dsh ▸ 在包里注入 dsh web 启动器 ▸ 建好桌面快捷方式"/>
      </StackPanel>

      <!-- 进度卡片 -->
      <Border Grid.Row="2" Margin="30,18,30,0" CornerRadius="12" Padding="20,16" Background="#26FFFFFF">
        <StackPanel>
          <Grid>
            <TextBlock x:Name="TxtStatus" FontSize="14" Foreground="#FFFFFF" TextWrapping="Wrap"
                       Text="准备就绪。点击右下角「开始安装」。" VerticalAlignment="Center"/>
            <TextBlock x:Name="TxtPercent" FontSize="15" FontWeight="Bold" Foreground="#BFE9FF"
                       HorizontalAlignment="Right" VerticalAlignment="Center" Text="0%"/>
          </Grid>
          <ProgressBar x:Name="Bar" Style="{StaticResource BarStyle}" Margin="0,12,0,0" Minimum="0" Maximum="100" Value="0"/>
        </StackPanel>
      </Border>

      <!-- 选项卡片 -->
      <Border Grid.Row="3" Margin="30,14,30,0" CornerRadius="12" Padding="20,14" Background="#F2F8FF">
        <StackPanel>
          <StackPanel Orientation="Horizontal">
            <TextBlock Text="安装方式：" FontSize="13" Foreground="#0B3D91" VerticalAlignment="Center" Width="72"/>
            <RadioButton x:Name="RbMsi" Content="标准安装 MSI（需要管理员权限，推荐）" FontSize="13"
                         Foreground="#0B3D91" VerticalAlignment="Center" IsChecked="True"/>
            <RadioButton x:Name="RbZip" Content="便携安装 ZIP（免管理员权限）" FontSize="13"
                         Foreground="#0B3D91" Margin="18,0,0,0" VerticalAlignment="Center"/>
          </StackPanel>
          <StackPanel Orientation="Horizontal" Margin="0,10,0,0">
            <TextBlock Text="Node 版本：" FontSize="13" Foreground="#0B3D91" VerticalAlignment="Center" Width="72"/>
            <TextBox x:Name="TxtNode" Width="120" Height="28" VerticalContentAlignment="Center" Padding="6,0"
                     Text="" ToolTip="留空 = 自动获取官方最新 LTS"/>
            <TextBlock Text="留空=最新LTS" FontSize="11" Foreground="#5A7CA8" VerticalAlignment="Center" Margin="6,0,0,0"/>
            <TextBlock Text="监听端口：" FontSize="13" Foreground="#0B3D91" VerticalAlignment="Center" Margin="20,0,0,0"/>
            <TextBox x:Name="TxtPort" Width="70" Height="28" VerticalContentAlignment="Center" Padding="6,0" Text="3080"/>
            <CheckBox x:Name="ChkMirror" Content="用国内镜像加速" FontSize="12" Foreground="#0B3D91"
                      Margin="20,0,0,0" VerticalAlignment="Center"/>
          </StackPanel>
          <StackPanel Orientation="Horizontal" Margin="0,10,0,0">
            <TextBlock Text="API 密钥：" FontSize="13" Foreground="#0B3D91" VerticalAlignment="Center" Width="72"/>
            <PasswordBox x:Name="PwdKey" Width="330" Height="28" VerticalContentAlignment="Center" Padding="6,0"
                         ToolTip="可留空，稍后在 DSH 网页的「设置 → 模型」里填写"/>
            <CheckBox x:Name="ChkSave" Content="写入用户环境变量 DEEPSEEK_API_KEY" FontSize="12"
                      Foreground="#0B3D91" Margin="12,0,0,0" VerticalAlignment="Center" IsChecked="True"/>
          </StackPanel>
        </StackPanel>
      </Border>

      <!-- 日志 -->
      <Border Grid.Row="4" Margin="30,14,30,0" CornerRadius="12" Padding="6" Background="#66040F22">
        <TextBox x:Name="TxtLog" IsReadOnly="True" AcceptsReturn="True" TextWrapping="Wrap"
                 Background="Transparent" BorderThickness="0" Foreground="#9BE7FF"
                 FontFamily="Consolas, Microsoft YaHei UI" FontSize="12"
                 VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled"
                 Padding="10,6"/>
      </Border>

      <!-- 按钮区 -->
      <Grid Grid.Row="5" Margin="30,16,30,22">
        <StackPanel Orientation="Horizontal" HorizontalAlignment="Left">
          <Button x:Name="BtnPlatform" Style="{StaticResource GhostButton}" Content="打开 DeepSeek 开放平台"/>
          <Button x:Name="BtnLog" Style="{StaticResource GhostButton}" Content="打开安装日志" Margin="10,0,0,0"/>
        </StackPanel>
        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
          <Button x:Name="BtnWeb" Style="{StaticResource GhostButton}" Content="启动 DSH Web" IsEnabled="False"/>
          <Button x:Name="BtnStart" Style="{StaticResource PrimaryButton}" Content="开始安装" Margin="10,0,0,0"/>
        </StackPanel>
      </Grid>
    </Grid>
  </Border>
</Window>
'@

$script:XamlNotice = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="DeepSeek API 密钥提示"
        Width="760" Height="620"
        WindowStartupLocation="CenterOwner"
        WindowStyle="None" AllowsTransparency="True" Background="Transparent"
        ResizeMode="NoResize" FontFamily="Microsoft YaHei UI, Segoe UI" UseLayoutRounding="True"
        TextOptions.TextFormattingMode="Display">
  <Border CornerRadius="16" Margin="14">
    <Border.Background>
      <LinearGradientBrush StartPoint="0,0" EndPoint="1,1">
        <GradientStop Color="#04122E" Offset="0"/>
        <GradientStop Color="#0A3D8F" Offset="0.5"/>
        <GradientStop Color="#1E88E5" Offset="0.85"/>
        <GradientStop Color="#4FC3F7" Offset="1"/>
      </LinearGradientBrush>
    </Border.Background>
    <Border.Effect>
      <DropShadowEffect BlurRadius="24" ShadowDepth="0" Opacity="0.5" Color="#04122E"/>
    </Border.Effect>
    <Grid Margin="28,20,28,22">
      <Grid.RowDefinitions>
        <RowDefinition Height="Auto"/>
        <RowDefinition Height="*"/>
        <RowDefinition Height="Auto"/>
        <RowDefinition Height="Auto"/>
      </Grid.RowDefinitions>

      <StackPanel Grid.Row="0">
        <TextBlock Text="开始前：先准备一个 DeepSeek API 密钥" FontSize="23" FontWeight="Bold" Foreground="#FFFFFF"/>
        <TextBlock Margin="0,9,0,0" FontSize="13" Foreground="#D6EAFF" TextWrapping="Wrap"
                   Text="DSH（DeepSeek Harness）本身不含密钥，需要到 DeepSeek 开放平台购买 / 申请。下面 5 步大约 2 分钟，建议先看完再点「继续安装」。"/>
      </StackPanel>

      <Border Grid.Row="1" Margin="0,16,0,0" CornerRadius="12" Padding="20,16" Background="#F5FAFF">
        <ScrollViewer VerticalScrollBarVisibility="Auto">
          <StackPanel>
            <TextBlock FontSize="14" FontWeight="Bold" Foreground="#08306B">
              <Run Text="第 1 步 · 打开开放平台并登录"/></TextBlock>
            <TextBlock Margin="0,4,0,0" FontSize="12.5" Foreground="#20456F" TextWrapping="Wrap"
                       Text="浏览器打开 platform.deepseek.com ，支持手机号验证码登录或微信扫码；没有账号时登录即自动注册，不需要单独填资料。"/>

            <TextBlock Margin="0,12,0,0" FontSize="14" FontWeight="Bold" Foreground="#08306B">
              <Run Text="第 2 步 · 充值余额（购买 API 额度）"/></TextBlock>
            <TextBlock Margin="0,4,0,0" FontSize="12.5" Foreground="#20456F" TextWrapping="Wrap"
                       Text="登录后点左侧菜单「充值」→ 选择金额（¥10 / ¥20 / ¥50 或自定义）→ 选择支付宝或微信支付 → 点「去支付」完成付款。充值立刻到账，可在左侧「用量信息」里核对余额。"/>
            <TextBlock Margin="0,4,0,0" FontSize="12" Foreground="#A0522D" TextWrapping="Wrap"
                       Text="提示：网页版 / App 里的免费聊天不消耗这里的余额；API 余额是给程序和 DSH 调用模型用的，两者相互独立。"/>

            <TextBlock Margin="0,12,0,0" FontSize="14" FontWeight="Bold" Foreground="#08306B">
              <Run Text="第 3 步 · 创建 API key"/></TextBlock>
            <TextBlock Margin="0,4,0,0" FontSize="12.5" Foreground="#20456F" TextWrapping="Wrap"
                       Text="点左侧菜单「API keys」→ 点右上角黑色按钮「创建 API key」→ 起一个方便识别的名字 → 确定。"/>

            <TextBlock Margin="0,12,0,0" FontSize="14" FontWeight="Bold" Foreground="#08306B">
              <Run Text="第 4 步 · 立刻复制并保存密钥"/></TextBlock>
            <TextBlock Margin="0,4,0,0" FontSize="12.5" Foreground="#20456F" TextWrapping="Wrap"
                       Text="创建成功后会弹出一串以 sk- 开头的字符，点「复制」并先存到安全的地方。关闭弹窗后就再也看不到完整密钥了（只显示前后几位）。"/>

            <TextBlock Margin="0,12,0,0" FontSize="14" FontWeight="Bold" Foreground="#08306B">
              <Run Text="第 5 步 · 交给 DSH"/></TextBlock>
            <TextBlock Margin="0,4,0,0" FontSize="12.5" Foreground="#20456F" TextWrapping="Wrap"
                       Text="两种方式任选：① 安装完成后启动 DSH Web，在网页里的「设置 → 模型（Models）」页粘贴密钥；② 直接在本安装界面下方的「API 密钥」输入框里粘贴，安装脚本会帮你写入用户环境变量 DEEPSEEK_API_KEY。"/>

            <Border Margin="0,14,0,0" Padding="12,10" CornerRadius="8" Background="#E7F1FF">
              <StackPanel>
                <TextBlock FontSize="12.5" FontWeight="Bold" Foreground="#08306B" Text="计费与排错小贴士"/>
                <TextBlock Margin="0,4,0,0" FontSize="12" Foreground="#20456F" TextWrapping="Wrap"
                           Text="· 按 token 计费，价格随模型不同，详见官方价格页 api-docs.deepseek.com/zh-cn/quick_start/pricing"/>
                <TextBlock FontSize="12" Foreground="#20456F" TextWrapping="Wrap"
                           Text="· 报 401 Authentication Fails：密钥填错或没保存；报 402 Insufficient Balance：余额不足，回第 2 步充值；报 429：请求太频繁，稍后再试"/>
                <TextBlock FontSize="12" Foreground="#20456F" TextWrapping="Wrap"
                           Text="· 密钥等同密码：不要发到群里、不要提交到 GitHub；怀疑泄露就立刻到「API keys」页删除并新建一个"/>
                <TextBlock FontSize="12" Foreground="#20456F" TextWrapping="Wrap"
                           Text="· 单个账号最多保留 100 个 API key，建议按用途分别创建，方便单独吊销"/>
              </StackPanel>
            </Border>
          </StackPanel>
        </ScrollViewer>
      </Border>

      <StackPanel Grid.Row="2" Orientation="Horizontal" Margin="0,14,0,0">
        <Button x:Name="NBtnPlatform" Content="打开开放平台" FontSize="12.5" Padding="14,7" Cursor="Hand"
                Background="#33FFFFFF" Foreground="#EAF4FF" BorderBrush="#7FB6F0"/>
        <Button x:Name="NBtnKeys" Content="打开 API keys 页" FontSize="12.5" Padding="14,7" Margin="10,0,0,0" Cursor="Hand"
                Background="#33FFFFFF" Foreground="#EAF4FF" BorderBrush="#7FB6F0"/>
        <Button x:Name="NBtnPricing" Content="查看模型价格" FontSize="12.5" Padding="14,7" Margin="10,0,0,0" Cursor="Hand"
                Background="#33FFFFFF" Foreground="#EAF4FF" BorderBrush="#7FB6F0"/>
      </StackPanel>

      <StackPanel Grid.Row="3" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,14,0,0">
        <Button x:Name="NBtnExit" Content="退出安装" FontSize="13" Padding="18,9" Margin="0,0,10,0" Cursor="Hand"
                Background="#33FFFFFF" Foreground="#EAF4FF" BorderBrush="#7FB6F0"/>
        <Button x:Name="NBtnOk" Content="我已了解，继续安装" FontSize="13" FontWeight="SemiBold"
                Foreground="#08306B" Background="#FFFFFF" BorderBrush="#BBDDFF" BorderThickness="1" Padding="20,9" Cursor="Hand"/>
      </StackPanel>
    </Grid>
  </Border>
</Window>
'@

function Add-DshUiLog {
    param([string]$Text, [string]$Level = 'info')
    $prefix = ''
    switch ($Level) {
        'error' { $prefix = '✖ ' }
        'warn' { $prefix = '! ' }
        'ok' { $prefix = '✔ ' }
    }
    $script:TxtLog.AppendText($prefix + $Text + "`r`n")
    if ($script:TxtLog.Text.Length -gt 400000) {
        $script:TxtLog.Text = '[日志过长，已截断前面的内容，完整日志见日志文件]' + "`r`n"
    }
    $script:TxtLog.ScrollToEnd()
}

function Update-DshUi {
    if (-not $script:Sync) { return }

    # 1) 排空日志队列
    $q = $script:Sync.Logs
    $guard = 0
    while ($q.Count -gt 0 -and $guard -lt 400) {
        $item = $q.Dequeue()
        $guard++
        if ($item -is [hashtable]) { Add-DshUiLog -Text $item.Text -Level $item.Level }
        else { Add-DshUiLog -Text ([string]$item) }
    }

    # 2) 进度与状态
    $pct = [double]$script:Sync.Percent
    if ($script:Sync.Busy) {
        $script:SpinnerIndex = ($script:SpinnerIndex + 1) % 4
    } else {
        $script:SpinnerIndex = 0
    }
    $script:Bar.Value = [Math]::Max(0, [Math]::Min(100, $pct))
    $script:TxtPercent.Text = ([Math]::Round($pct)).ToString() + '%'

    $status = [string]$script:Sync.Status
    if ($script:Sync.Busy) {
        $spin = @('◐', '◓', '◑', '◒')[$script:SpinnerIndex]
        $status = $spin + ' ' + $status
    }
    $script:TxtStatus.Text = $status

    # 3) 结束状态
    if ($script:Sync.Done -and -not $script:Finished) {
        $script:Finished = $true
        if ($script:Sync.Ok) {
            $script:TxtPercent.Text = '100%'
            $script:Bar.Value = 100
            $script:Bar.Foreground = [Windows.Media.Brushes]::LightGreen
            $script:TxtStatus.Text = '✔ 安装完成！可以点「启动 DSH Web」，或双击桌面「DSH Web」快捷方式。'
            $script:BtnWeb.IsEnabled = $true
            $script:BtnStart.Content = '重新安装'
            $script:BtnStart.IsEnabled = $true
            $script:ShowDoneTip = $true
            try { $script:LauncherPathForTip = [string]$script:Sync.Results.LauncherPath } catch { $script:LauncherPathForTip = '' }
        } else {
            $script:Bar.Foreground = [Windows.Media.Brushes]::OrangeRed
            $script:TxtStatus.Text = ('✖ 安装失败：' + $script:Sync.Error + '（可点「打开安装日志」查看详情）')
            $script:BtnStart.Content = '重试安装'
            $script:BtnStart.IsEnabled = $true
            $script:PwdKey.IsEnabled = $true
        }
    }
}

function Show-DshDoneTip {
    $msg = @(
        '安装完成！',
        '',
        '接下来：点「启动 DSH Web」，或双击桌面上的「DSH Web」快捷方式。',
        '浏览器会自动打开带授权令牌的地址（手输首页地址是打不开的）。',
        '',
        '首次使用请在网页里的「设置 → 模型（Models）」页填入 DeepSeek API 密钥；',
        '如果还没买密钥：访问 platform.deepseek.com，左侧「充值」付款后，',
        '到「API keys」页创建并复制 sk- 开头的密钥。',
        '',
        ('启动器位置：' + $script:LauncherPathForTip)
    ) -join [char]10
    [void][System.Windows.MessageBox]::Show($msg, 'DSH 安装完成',
        [System.Windows.MessageBoxButton]::OK, [System.Windows.MessageBoxImage]::Information)
}

function Show-DshApiNotice {
    $dlg = [Windows.Markup.XamlReader]::Parse($script:XamlNotice)
    $dlg.Owner = $script:Window
    $dlg.Tag = 'continue'
    $script:NoticeDlg = $dlg

    $dlg.FindName('NBtnPlatform').Add_Click({ Start-Process $script:ApiPlatformUrl })
    $dlg.FindName('NBtnKeys').Add_Click({ Start-Process $script:ApiKeysUrl })
    $dlg.FindName('NBtnPricing').Add_Click({ Start-Process $script:ApiPricingUrl })
    $dlg.FindName('NBtnExit').Add_Click({
            $script:NoticeDlg.Tag = 'exit'
            $script:NoticeDlg.Close()
        })
    $dlg.FindName('NBtnOk').Add_Click({ $script:NoticeDlg.Close() })
    $dlg.Add_MouseLeftButtonDown({ try { $script:NoticeDlg.DragMove() } catch { } })

    [void]$dlg.ShowDialog()
    $script:NoticeDlg = $null
    return $dlg.Tag
}

function Start-DshInstall {
    if ($script:Running) { return }

    $mode = 'Msi'
    if ($script:RbZip.IsChecked) { $mode = 'Zip' }

    $port = 3080
    $parsed = 0
    if ([int]::TryParse($script:TxtPort.Text.Trim(), [ref]$parsed) -and $parsed -gt 0 -and $parsed -lt 65536) {
        $port = $parsed
    } else {
        $script:TxtPort.Text = '3080'
    }

    $script:Running = $true
    $script:Finished = $false
    $script:BtnStart.IsEnabled = $false
    $script:BtnWeb.IsEnabled = $false
    $script:PwdKey.IsEnabled = $false
    $script:RbMsi.IsEnabled = $false
    $script:RbZip.IsEnabled = $false
    $script:TxtPort.IsEnabled = $false
    $script:TxtNode.IsEnabled = $false
    $script:ChkMirror.IsEnabled = $false
    $script:ChkSave.IsEnabled = $false
    Add-DshUiLog -Text '===================== 开始安装 =====================' -Level 'info'

    $sync = [hashtable]::Synchronized(@{
            Status  = '启动安装进程…'
            Percent = 0
            Logs    = [System.Collections.Queue]::Synchronized((New-Object System.Collections.Queue))
            Done    = $false
            Ok      = $false
            Error   = ''
            Busy    = $true
            Results = @{}
        })
    $script:Sync = $sync

    $options = @{
        Console      = $false
        LogPath      = $script:LogPath
        WorkRoot     = $script:WorkRoot
        Package      = $script:DshPackage
        Port         = $port
        NodeVersion  = $script:TxtNode.Text.Trim()
        Mode         = $mode
        ApiKey       = $script:PwdKey.Password
        SaveKey      = [bool]$script:ChkSave.IsChecked
        UseNpmMirror = [bool]$script:ChkMirror.IsChecked
        NoShortcuts  = [bool]$script:NoShortcutsFlag
        MinNodeMajor = $script:MinNodeMajor
        FallbackNode = $script:FallbackNode
        ApiPlatformUrl = $script:ApiPlatformUrl
    }

    # 把核心函数定义复制到后台 runspace（脚本块不能跨 runspace，只能复制文本）
    $defs = ''
    foreach ($fn in $script:CoreFunctions) {
        $cmd = Get-Command $fn -ErrorAction SilentlyContinue
        if ($cmd) { $defs += ('function ' + $fn + ' { ' + $cmd.Definition + ' }' + "`n`n") }
    }

    $rs = [System.Management.Automation.Runspaces.RunspaceFactory]::CreateRunspace()
    $rs.ApartmentState = 'MTA'
    $rs.ThreadOptions = 'ReuseThread'
    $rs.Open()
    $rs.SessionStateProxy.SetVariable('DshSync', $sync)
    $rs.SessionStateProxy.SetVariable('DshOptions', $options)

    $ps = [System.Management.Automation.PowerShell]::Create()
    $ps.Runspace = $rs
    [void]$ps.AddScript($defs)
    [void]$ps.AddScript('Start-DshInstallWorker -Sync $DshSync -Options $DshOptions')

    $script:WorkerPowerShell = $ps
    $script:WorkerRunspace = $rs
    $script:WorkerHandle = $ps.BeginInvoke()
}

function Stop-DshWorker {
    if ($script:WorkerPowerShell) {
        try { $script:WorkerPowerShell.Stop() } catch { }
    }
}

function Start-DshWebUi {
    $path = ''
    if ($script:Sync -and $script:Sync.Results -and $script:Sync.Results.LauncherPath) {
        $path = $script:Sync.Results.LauncherPath
    }
    if (-not $path -or -not (Test-Path -LiteralPath $path)) {
        $cand = Join-Path $env:APPDATA 'npm\dsh-web.cmd'
        if (Test-Path -LiteralPath $cand) { $path = $cand }
    }
    if (-not $path -or -not (Test-Path -LiteralPath $path)) {
        [void][System.Windows.MessageBox]::Show('找不到 dsh web 启动器，请先完成安装。', 'DSH', 'OK', 'Warning')
        return
    }
    Start-Process -FilePath $path -WorkingDirectory (Split-Path -Parent $path)
}

function Invoke-DshGui {
    Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Xaml

    $window = [Windows.Markup.XamlReader]::Parse($script:XamlMain)
    $script:Window = $window

    $script:TxtStatus = $window.FindName('TxtStatus')
    $script:TxtPercent = $window.FindName('TxtPercent')
    $script:Bar = $window.FindName('Bar')
    $script:TxtLog = $window.FindName('TxtLog')
    $script:TxtNode = $window.FindName('TxtNode')
    $script:TxtPort = $window.FindName('TxtPort')
    $script:PwdKey = $window.FindName('PwdKey')
    $script:ChkSave = $window.FindName('ChkSave')
    $script:ChkMirror = $window.FindName('ChkMirror')
    $script:RbMsi = $window.FindName('RbMsi')
    $script:RbZip = $window.FindName('RbZip')
    $script:BtnStart = $window.FindName('BtnStart')
    $script:BtnWeb = $window.FindName('BtnWeb')
    $script:BtnPlatform = $window.FindName('BtnPlatform')
    $script:BtnLog = $window.FindName('BtnLog')
    $script:BtnClose = $window.FindName('BtnClose')
    $script:BtnMin = $window.FindName('BtnMin')
    $titleBar = $window.FindName('TitleBar')

    $script:TxtPort.Text = "$Port"
    $script:TxtNode.Text = $NodeVersion
    if ($NodeInstallMode -eq 'Zip') { $script:RbZip.IsChecked = $true } else { $script:RbMsi.IsChecked = $true }
    $script:ChkMirror.IsChecked = [bool]$UseNpmMirror
    $script:ChkSave.IsChecked = $true
    if ($ApiKey) { $script:PwdKey.Password = $ApiKey }
    $script:NoShortcutsFlag = [bool]$NoShortcuts

    $titleBar.Add_MouseLeftButtonDown({ try { $script:Window.DragMove() } catch { } })
    $script:BtnMin.Add_Click({ $script:Window.WindowState = 'Minimized' })
    $script:BtnClose.Add_Click({ $script:Window.Close() })
    $script:BtnPlatform.Add_Click({ Start-Process $script:ApiPlatformUrl })
    $script:BtnLog.Add_Click({
            if (Test-Path -LiteralPath $script:LogPath) { Start-Process notepad.exe -ArgumentList ('"' + $script:LogPath + '"') }
            else { [void][System.Windows.MessageBox]::Show('还没有日志文件。', 'DSH', 'OK', 'Information') }
        })
    $script:BtnStart.Add_Click({ Start-DshInstall })
    $script:BtnWeb.Add_Click({ Start-DshWebUi })

    $window.Add_Closing({
            param($s, $e)
            if ($script:Running -and -not $script:Finished) {
                $r = [System.Windows.MessageBox]::Show('安装正在进行中，确定要退出吗？（会中断安装）', 'DSH',
                    [System.Windows.MessageBoxButton]::YesNo, [System.Windows.MessageBoxImage]::Warning)
                if ($r -ne 'Yes') { $e.Cancel = $true; return }
            }
            Stop-DshWorker
        })

    $window.Add_Loaded({
            $script:Timer.Start()
        })

    # 提示弹窗放在第一次 Tick 里显示，确保主窗口已经画出来
    $script:NeedNotice = -not $SkipApiNotice
    $script:TxtStatus.Text = '准备就绪。点击右下角「开始安装」。'
    Add-DshUiLog -Text ('日志文件：' + $script:LogPath)
    Add-DshUiLog -Text ('工作目录：' + $script:WorkRoot)
    Add-DshUiLog -Text '本脚本只会：下载安装 Node.js → npm install -g @deepseek-ai/dsh → 注入 dsh web 启动器。不会上传任何数据。'
    Add-DshUiLog -Text '提示：如果安装方式为 MSI，接下来可能弹出 UAC 管理员授权窗口，请点「是」。' -Level 'warn'

    $timer = New-Object Windows.Threading.DispatcherTimer
    $timer.Interval = [TimeSpan]::FromMilliseconds(260)
    $script:Timer = $timer
    $timer.Add_Tick({
            if ($script:NeedNotice) {
                $script:NeedNotice = $false
                $answer = Show-DshApiNotice
                if ($answer -eq 'exit') { $script:Window.Close(); return }
                Add-DshUiLog -Text '提示已确认，可以开始安装了。' -Level 'ok'
            }
            Update-DshUi
            if ($script:ShowDoneTip) {
                $script:ShowDoneTip = $false
                Show-DshDoneTip
            }
        })

    [void]$window.ShowDialog()

    Stop-DshWorker
    if ($script:WorkerRunspace) { try { $script:WorkerRunspace.Close() } catch { } }
}

#endregion

#region ─────────────────────────────── 入口 ───────────────────────────────

# 从网络下载（微信/QQ/浏览器）得到的文件带“锁定”标记，这里顺手解除，避免中途被安全策略拦截
try {
    if ($PSScriptRoot) {
        Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -File -ErrorAction SilentlyContinue |
            Unblock-File -ErrorAction SilentlyContinue
    }
} catch { }

if (-not (Test-Path -LiteralPath $WorkRoot)) { New-Item -ItemType Directory -Path $WorkRoot -Force | Out-Null }
$script:WorkRoot = $WorkRoot
$script:LogPath = Join-Path $WorkRoot ('install-' + (Get-Date).ToString('yyyyMMdd-HHmmss') + '.log')
$script:DshPackage = $DshPackage
$script:Running = $false
$script:Finished = $false
$script:Sync = $null
$script:WorkerPowerShell = $null
$script:WorkerRunspace = $null
$script:SpinnerIndex = 0
$script:NeedNotice = $false
$script:ShowDoneTip = $false
$script:NoShortcutsFlag = [bool]$NoShortcuts

if ($Silent) {
    $code = Invoke-DshSilent
    exit $code
}

# 非管理员 + MSI 模式：先提权重启（用户拒绝则自动降级为 ZIP 模式继续）
try {
    $isAdmin = Test-DshAdmin
    if (-not $isAdmin -and $NodeInstallMode -eq 'Msi') {
        $argList = @(
            '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass',
            '-File', ('"' + $PSCommandPath + '"'),
            '-NodeInstallMode', $NodeInstallMode,
            '-DshPackage', $DshPackage,
            '-Port', "$Port",
            '-WorkRoot', ('"' + $WorkRoot + '"')
        )
        if ($NodeVersion) { $argList += @('-NodeVersion', $NodeVersion) }
        if ($UseNpmMirror) { $argList += '-UseNpmMirror' }
        if ($SkipApiNotice) { $argList += '-SkipApiNotice' }
        if ($NoShortcuts) { $argList += '-NoShortcuts' }
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $argList
        exit 0
    }
} catch {
    # 用户取消了 UAC：继续在本进程里跑，并自动使用 ZIP 便携模式
    $NodeInstallMode = 'Zip'
}

try {
    Invoke-DshGui
} catch {
    # 图形界面起不来时的兜底：给出提示，并可改用命令行静默模式继续安装
    # 图形界面起不来时的兜底：
    #   1) 把完整异常打到控制台（这样引导器能抓到并显示出来）
    #   2) 写入日志
    #   3) 不再弹窗询问，直接改用命令行模式继续安装
    #      （原来弹窗只要用户点错一下就彻底失败，太脆）
    $err = $_.Exception.Message
    $det = $_.Exception.ToString()
    $banner = @(
        '',
        '============================================================',
        ' [WARN] GUI failed to start - falling back to console mode.',
        ' 图形界面启动失败，已自动改用命令行模式继续安装。',
        '============================================================',
        ' Message : ' + $err,
        ' LogPath : ' + $script:LogPath,
        '',
        $det,
        '============================================================',
        ''
    )
    foreach ($b in $banner) {
        try { Write-Host $b } catch { }
        Write-Output $b
    }
    try { Add-Content -LiteralPath $script:LogPath -Value ('图形界面启动失败：' + $det) -Encoding UTF8 -ErrorAction SilentlyContinue } catch { }
    $code = Invoke-DshSilent
    try { Start-Process notepad.exe -ArgumentList ('"' + $script:LogPath + '"') } catch { }
    exit $code
}

#endregion
