<#
Pixel Office - Windows bootstrap (CLOUD.md 7.5). Read it before running it:

  powershell -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((irm https://<host>/hook/install.ps1))) -Server https://<host> -Room <roomId> -Key <writeKey> -Name 'Timur'"

If Python >= 3.8 is available (py -3 or python) and -ForcePowerShell is not given, the Python client is
installed; otherwise the PowerShell client (no Python needed, same privacy guarantee). Both write
%USERPROFILE%\.pixel-office\config.json, merge hooks into %USERPROFILE%\.claude\settings.json (backup:
settings.json.pixel-backup) and send a test character. Pure ASCII on purpose (PowerShell 5.1).
#>
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [string]$Room,
    [string]$Key,
    [Parameter(Mandatory = $true)][string]$Name,
    [int]$Color = -1,
    [string]$SettingsPath,
    [switch]$ForcePowerShell,
    [switch]$NoTest
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

$Server = $Server.TrimEnd('/')
# Phase 16 (SECURITY_AUDIT.md S16-05): https:// only; plain http:// only for a local-mode server on this machine or
# the LAN (loopback, RFC 1918, *.local / *.lan, single-label names), as in install.sh.
$LocalHttp = '^http://(127\.[0-9.]+|localhost|\[::1\]|10\.[0-9.]+|192\.168\.[0-9.]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9.]+|[A-Za-z0-9-]+|[A-Za-z0-9.-]+\.(local|lan))(:[0-9]{1,5})?$'
if (-not ($Server -match '^https://[^/]' -or $Server -match $LocalHttp)) {
    Write-Error 'Server must start with https:// (http:// only for a server on this computer or the local network).'
    exit 2
}
$oh = $env:PIXEL_OFFICE_HOME
if (-not $oh) {
    $h = $env:USERPROFILE
    if (-not $h) { $h = $HOME }
    $oh = Join-Path $h '.pixel-office'
}
if (-not (Test-Path -LiteralPath $oh)) { New-Item -ItemType Directory -Path $oh -Force | Out-Null }

function Get-File([string]$url, [string]$dest) {
    $text = (Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 30).Content
    if ($text -is [byte[]]) { $text = [System.Text.Encoding]::UTF8.GetString($text) }
    # UTF-8 with BOM: PowerShell 5.1 then reads non-ASCII text correctly.
    [System.IO.File]::WriteAllText($dest, $text, (New-Object System.Text.UTF8Encoding($true)))
}

function Find-Python {
    $cands = @(@('py', '-3'), @('python3'), @('python'))
    foreach ($c in $cands) {
        $exe = $c[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $pre = @()
            if ($c.Count -gt 1) { $pre = $c[1..($c.Count - 1)] }
            # The Microsoft Store "python.exe" stub prints nothing useful and fails this check.
            $out = & $exe @pre -c "import sys; print(sys.executable if sys.version_info >= (3, 8) else '')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out -and (Test-Path -LiteralPath ([string]$out).Trim())) { return ([string]$out).Trim() }
        } catch { }
    }
    return $null
}

$py = $null
if (-not $ForcePowerShell) { $py = Find-Python }

if ($py) {
    $script = Join-Path $oh 'pixel_hook.py'
    Get-File "$Server/hook/pixel_hook.py" $script
    try { Get-File "$Server/hook/pixel_agent.py" (Join-Path $oh 'pixel_agent.py') } catch { }
    try { Get-File "$Server/hook/pixel_antigravity.py" (Join-Path $oh 'pixel_antigravity.py') } catch { }
    $a = @($script, 'install', '--server', $Server, '--name', $Name)
    if ($Room) { $a += @('--room', $Room) }
    if ($Key) { $a += @('--key', $Key) }
    if ($Color -ge 0) { $a += @('--color', [string]$Color) }
    if ($SettingsPath) { $a += @('--settings-path', $SettingsPath) }
    if ($NoTest) { $a += '--no-test' }
    & $py @a
    exit $LASTEXITCODE
}

$script = Join-Path $oh 'pixel_hook.ps1'
Get-File "$Server/hook/pixel_hook.ps1" $script
$p = @{ Server = $Server; Name = $Name }
if ($Room) { $p['Room'] = $Room }
if ($Key) { $p['Key'] = $Key }
if ($Color -ge 0) { $p['Color'] = $Color }
if ($SettingsPath) { $p['SettingsPath'] = $SettingsPath }
if ($NoTest) { $p['NoTest'] = $true }
& $script install @p
exit $LASTEXITCODE
