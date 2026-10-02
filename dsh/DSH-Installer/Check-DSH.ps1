# ============================================================================
#  DSH 环境自检脚本（只读检查，不做任何安装 / 修改）
#  用法：右键“使用 PowerShell 运行”，或
#        powershell -NoProfile -ExecutionPolicy Bypass -File .\Check-DSH.ps1
# ============================================================================
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'

function Line { param([string]$Text, [string]$Color = 'Gray') Write-Host $Text -ForegroundColor $Color }
function Ok { param([string]$Text) Write-Host ('  [OK]   ' + $Text) -ForegroundColor Green }
function Bad { param([string]$Text) Write-Host ('  [FAIL] ' + $Text) -ForegroundColor Red }
function Warn { param([string]$Text) Write-Host ('  [WARN] ' + $Text) -ForegroundColor Yellow }
function Info { param([string]$Text) Write-Host ('  [INFO] ' + $Text) -ForegroundColor Cyan }

Write-Host ''
Line '================================================================' Cyan
Line '  DSH（DeepSeek Harness）环境自检' Cyan
Line ('  时间：' + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) Cyan
Line '================================================================' Cyan

# ── 1. 系统 ─────────────────────────────────────────────────────────────────
Line ''
Line '[1] 系统信息'
Info ([System.Environment]::OSVersion.VersionString)
Info ('64 位系统：' + [System.Environment]::Is64BitOperatingSystem + '    CPU 架构：' + $env:PROCESSOR_ARCHITECTURE)
try {
    $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $admin = (New-Object System.Security.Principal.WindowsPrincipal($id)).IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
    Info ('管理员权限：' + $admin)
} catch { }

# ── 2. Node.js ──────────────────────────────────────────────────────────────
Line ''
Line '[2] Node.js / npm'
$node = (Get-Command node.exe -ErrorAction SilentlyContinue | Select-Object -First 1)
if ($node) {
    Ok ('node.exe -> ' + $node.Source)
    try {
        $v = & $node.Source -v 2>$null
        Ok ('node 版本：' + $v)
        if ($v -match '^v(\d+)' -and [int]$Matches[1] -lt 20) { Warn 'Node.js 版本偏旧，DSH 建议 v20 以上（推荐最新 LTS）。' }
    } catch { Warn '无法执行 node -v' }
} else {
    Bad '未找到 node.exe（还没安装 Node.js，或安装后没有重开命令行窗口）'
}

$npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue | Select-Object -First 1)
if ($npm) {
    Ok ('npm.cmd -> ' + $npm.Source)
    try { Ok ('npm 版本：' + (& $npm.Source -v 2>$null)) } catch { Warn '无法执行 npm -v' }
} else {
    Bad '未找到 npm.cmd'
}

# ── 3. dsh ──────────────────────────────────────────────────────────────────
Line ''
Line '[3] DSH 安装情况'
$dsh = (Get-Command dsh.cmd -ErrorAction SilentlyContinue | Select-Object -First 1)
if ($dsh) { Ok ('dsh.cmd -> ' + $dsh.Source) } else { Bad 'PATH 中没有 dsh.cmd' }

if ($npm) {
    try {
        $prefix = (& $npm.Source prefix -g 2>$null | Select-Object -Last 1)
        if ($prefix) { $prefix = $prefix.Trim() }
        Info ('npm 全局目录（prefix -g）：' + $prefix)
        $pkg = Join-Path $prefix 'node_modules\@deepseek-ai\dsh\package.json'
        if (Test-Path -LiteralPath $pkg) {
            $meta = Get-Content -LiteralPath $pkg -Raw -Encoding UTF8 | ConvertFrom-Json
            Ok ('已安装 ' + $meta.name + ' ' + $meta.version)
        } else {
            Bad ('未在 ' + $pkg + ' 找到已安装的 dsh 包')
        }
        $launcher = Join-Path $prefix 'dsh-web.cmd'
        if (Test-Path -LiteralPath $launcher) { Ok ('Web 启动器已就位：' + $launcher) } else { Warn '尚未注入 dsh-web.cmd 启动器（运行 Install-DSH.cmd 即可）' }
        $inPkg = Join-Path $prefix 'node_modules\@deepseek-ai\dsh\dsh-web.cmd'
        if (Test-Path -LiteralPath $inPkg) { Ok ('包内启动器已就位：' + $inPkg) } else { Warn '包内还没有 dsh-web.cmd' }
    } catch { Warn ('查询 npm 全局目录失败：' + $_.Exception.Message) }
}

# ── 4. API 密钥 ─────────────────────────────────────────────────────────────
Line ''
Line '[4] DeepSeek API 密钥'
$envKey = [System.Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', 'User')
if (-not $envKey) { $envKey = $env:DEEPSEEK_API_KEY }
if ($envKey) {
    $mask = $envKey.Substring(0, [Math]::Min(6, $envKey.Length)) + '****'
    Ok ('已设置 DEEPSEEK_API_KEY（' + $mask + '，长度 ' + $envKey.Length + '）')
} else {
    Warn '未设置 DEEPSEEK_API_KEY —— 可在 DSH 网页「设置 → 模型」里填写，或设置用户环境变量'
    Info '购买 / 申请：https://platform.deepseek.com/  （左侧「充值」→「API keys」→「创建 API key」）'
}

# ── 5. 网络 ─────────────────────────────────────────────────────────────────
Line ''
Line '[5] 网络连通性'
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
foreach ($u in @('https://nodejs.org/dist/index.json', 'https://registry.npmjs.org/', 'https://platform.deepseek.com/')) {
    try {
        $req = [System.Net.HttpWebRequest]::Create($u)
        $req.Method = 'HEAD'
        $req.Timeout = 12000
        $req.UserAgent = 'DSH-Check'
        $resp = $req.GetResponse()
        $code = [int]$resp.StatusCode
        $resp.Close()
        Ok ($u + '  ->  HTTP ' + $code)
    } catch {
        Warn ($u + '  ->  ' + $_.Exception.Message)
    }
}

# ── 6. 快捷方式 ─────────────────────────────────────────────────────────────
Line ''
Line '[6] 快捷方式'
$desktop = [System.Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop 'DSH Web.lnk'
if (Test-Path -LiteralPath $lnk) { Ok ('桌面快捷方式：' + $lnk) } else { Warn '桌面没有「DSH Web」快捷方式' }

Write-Host ''
Line '================================================================' Cyan
Line '  自检结束。以上 [FAIL] 项需要处理，[WARN] 项按提示可选处理。' Cyan
Line '================================================================' Cyan
Write-Host ''
