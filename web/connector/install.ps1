# Pixel Office bağlayıcı — Windows kurulumu (V3.md §5.4, §12.3). Çalıştırmadan önce okuyun:
#
#   irm https://<host>/connector/install.ps1 -OutFile install.ps1
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Server https://<host> -Room <oda> -Key <anahtar> -Name 'Ad'
#
# 1. Python 3.8+ bulur (py -3, python)
# 2. pixel-connector.pyz ve SHA256SUMS indirir, SHA-256'yı doğrular
# 3. %USERPROFILE%\.pixel-office\connector\ içine kurar, %USERPROFILE%\.pixel-office\bin\pixel-connector.cmd yazar
# 4. `pixel-connector setup …`, ardından `install-service` (Görev Zamanlayıcı, yönetici izni gerekmez).
#    -NoService servis adımını atlar. Bağlayıcı kendini asla buluttan güncellemez.
param(
  [Parameter(Mandatory = $true)][string]$Server,
  [Parameter(Mandatory = $true)][string]$Room,
  [Parameter(Mandatory = $true)][string]$Key,
  [Parameter(Mandatory = $true)][string]$Name,
  [string]$DeviceName = "",
  [switch]$NoService
)
$ErrorActionPreference = "Stop"
$Server = $Server.TrimEnd("/")

function Find-Python {
  foreach ($c in @(@("py", "-3"), @("python3"), @("python"))) {
    $exe = Get-Command $c[0] -ErrorAction SilentlyContinue
    if ($null -eq $exe) { continue }
    $args = @()
    if ($c.Count -gt 1) { $args += $c[1] }
    $out = & $exe.Source @args -c "import sys; print(sys.executable if sys.version_info >= (3, 8) else '')" 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) { return $out.Trim() }
  }
  return $null
}

$py = Find-Python
if (-not $py) {
  Write-Error "Python 3.8 ya da daha yenisi gerekli: https://www.python.org/downloads/ (kurulumda 'Add to PATH' seçin)."
  exit 1
}

$office = if ($env:PIXEL_OFFICE_HOME) { $env:PIXEL_OFFICE_HOME } else { Join-Path $env:USERPROFILE ".pixel-office" }
$conn = Join-Path $office "connector"
$bin = Join-Path $office "bin"
New-Item -ItemType Directory -Force -Path $conn, $bin | Out-Null

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("pixel-connector-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
  $pyz = Join-Path $tmp "pixel-connector.pyz"
  $sums = Join-Path $tmp "SHA256SUMS"
  Invoke-WebRequest -UseBasicParsing -Uri "$Server/connector/pixel-connector.pyz" -OutFile $pyz
  Invoke-WebRequest -UseBasicParsing -Uri "$Server/connector/SHA256SUMS" -OutFile $sums
  $expected = (Get-Content $sums | Where-Object { $_ -match " pixel-connector\.pyz$" } | ForEach-Object { ($_ -split " ")[0] })
  $actual = (Get-FileHash -Algorithm SHA256 $pyz).Hash.ToLower()
  if (-not $expected -or $expected -ne $actual) {
    Write-Error "SHA-256 doğrulanamadı; dosya bozuk ya da değiştirilmiş. Kurulum durduruldu. (beklenen: $expected, inen: $actual)"
    exit 1
  }
  Move-Item -Force $pyz (Join-Path $conn "pixel-connector.pyz")
} finally {
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

$target = Join-Path $conn "pixel-connector.pyz"
Set-Content -Encoding ASCII -Path (Join-Path $bin "pixel-connector.cmd") -Value "@`"$py`" `"$target`" %*"
Write-Host "pixel-connector.pyz SHA-256: $actual (README'deki sürüm özetiyle karşılaştırın)"

$setup = @($target, "setup", "--server", $Server, "--room", $Room, "--key", $Key, "--name", $Name)
if ($DeviceName) { $setup += @("--device-name", $DeviceName) }
& $py @setup
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $NoService) {
  & $py $target install-service
  if ($LASTEXITCODE -ne 0) { Write-Warning "Servis kurulamadı; elle çalıştırın: pixel-connector run" }
}

Write-Host ""
Write-Host "Kurulum tamam. Sıradaki adım: $bin\pixel-connector.cmd pair"
