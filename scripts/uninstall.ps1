# udally-stt 제거(Windows): irm https://udally.sio2.kr/stt/uninstall.ps1 | iex
# 연결돼 있으면 유달리에서 스스로 끊고(DELETE /self), 컨테이너 또는 uv 도구·시작프로그램 바로가기를 지운다. 받은 모델은 남긴다.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
& {
$ErrorActionPreference = 'Stop'
# docker·uv가 내는 UTF-8 출력을 한국어 Windows 콘솔(CP949)에서도 깨지지 않게 읽는다. 끝나면 되돌린다.
$prevEnc = [Console]::OutputEncoding
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
try {
function GetEnv($name, $default) { $v = [Environment]::GetEnvironmentVariable($name); if ($v) { $v } else { $default } }
$Name = GetEnv 'UDALLY_NAME' 'udally-stt'
$Volume = GetEnv 'UDALLY_VOLUME' 'udally-stt'
$Dry = (GetEnv 'UDALLY_DRY_RUN' '0') -eq '1'
function Say($t) { Write-Host $t }
function Run([string]$exe, [string[]]$argv) {
  if ($Dry) { Say ("[dry-run] $exe " + ($argv -join ' ')); return }
  $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { & $exe @argv | Out-Host } finally { $ErrorActionPreference = $old }
}
$found = $false

if (Get-Command docker -ErrorAction SilentlyContinue) {
  $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  $img = docker inspect -f '{{.Config.Image}}' $Name 2>$null
  $ErrorActionPreference = $old
  if ($LASTEXITCODE -eq 0 -and $img) {
    Run docker @('rm', '-f', $Name)
    Run docker @('run', '--rm', '-v', "${Volume}:/cache", $img, 'unlink')
    Say "컨테이너 ${Name}을 지웠습니다. 받은 모델까지 지우려면: docker volume rm $Volume"
    $found = $true
  }
}

$exe = Join-Path $HOME '.local\bin\udally-stt.exe'
if (Test-Path $exe) {
  $lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'udally-stt.lnk'
  if ($Dry) { Say "[dry-run] remove $lnk, stop $exe" }
  else {
    Remove-Item -Force $lnk -ErrorAction SilentlyContinue
    Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe } | Stop-Process -Force
  }
  Run $exe @('unlink')
  if (-not $Dry) { [Environment]::SetEnvironmentVariable('UDALLY_URL', $null, 'User') }
  $env:Path = (Join-Path $HOME '.local\bin') + ";$env:Path"
  Run uv @('tool', 'uninstall', 'udally-stt')
  Say 'udally-stt를 지웠습니다. 받은 모델은 %USERPROFILE%\.cache\huggingface\hub에 남아 있습니다(다른 프로그램과 함께 쓰는 폴더이니 확인 후 지워 주세요).'
  $found = $true
}
if (-not $found) { Say '설치된 udally-stt를 찾지 못했습니다.' }
} finally { [Console]::OutputEncoding = $prevEnc }
}
