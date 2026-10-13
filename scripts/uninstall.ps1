# udally-link 제거(Windows): irm https://udally.sio2.kr/link/uninstall.ps1 | iex
# 연결돼 있으면 유달리에서 스스로 끊고(DELETE /self), 컨테이너 또는 uv 도구·시작프로그램 바로가기를 지운다. 받은 모델은 남긴다.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
& {
$ErrorActionPreference = 'Stop'
# docker·uv가 내는 UTF-8 출력을 한국어 Windows 콘솔(CP949)에서도 깨지지 않게 읽는다. 끝나면 되돌린다.
$prevEnc = [Console]::OutputEncoding
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
try {
function GetEnv($name, $default) { $v = [Environment]::GetEnvironmentVariable($name); if ($v) { $v } else { $default } }
$Name = GetEnv 'UDALLY_NAME' 'udally-link'
$Volume = GetEnv 'UDALLY_VOLUME' 'udally-link'
$Dry = (GetEnv 'UDALLY_DRY_RUN' '0') -eq '1'
function Say($t) { Write-Host $t }
function Run([string]$exe, [string[]]$argv) {
  if ($Dry) { Say ("[dry-run] $exe " + ($argv -join ' ')); return }
  $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { & $exe @argv | Out-Host } finally { $ErrorActionPreference = $old }
}
$found = $false

if (Get-Command docker -ErrorAction SilentlyContinue) {
  foreach ($n in @($Name, 'udally-stt') | Select-Object -Unique) {  # udally-stt = 옛 이름
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $img = docker inspect -f '{{.Config.Image}}' $n 2>$null
    $vol = docker inspect -f '{{range .Mounts}}{{if eq .Destination "/cache"}}{{.Name}}{{end}}{{end}}' $n 2>$null
    $ErrorActionPreference = $old
    if ($img) {
      if (-not $vol) { $vol = $Volume }
      Run docker @('rm', '-f', $n)
      Run docker @('run', '--rm', '-v', "${vol}:/cache", $img, 'unlink')
      Say "컨테이너 ${n}을 지웠습니다. 받은 모델까지 지우려면: docker volume rm $vol"
      $found = $true
    }
  }
}

foreach ($tool in @('udally-link', 'udally-stt')) {  # udally-stt = 옛 이름
  $exe = Join-Path $HOME ".local\bin\$tool.exe"
  if (-not (Test-Path $exe)) { continue }
  $lnk = Join-Path ([Environment]::GetFolderPath('Startup')) "$tool.lnk"
  if ($Dry) { Say "[dry-run] remove $lnk, stop $exe" }
  else {
    Remove-Item -Force $lnk -ErrorAction SilentlyContinue
    Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe } | Stop-Process -Force
  }
  Run $exe @('unlink')
  if (-not $Dry) { [Environment]::SetEnvironmentVariable('UDALLY_URL', $null, 'User') }
  $env:Path = (Join-Path $HOME '.local\bin') + ";$env:Path"
  Run uv @('tool', 'uninstall', $tool)
  Say "${tool}를 지웠습니다. 받은 모델은 %USERPROFILE%\.cache\huggingface\hub에 남아 있습니다(다른 프로그램과 함께 쓰는 폴더이니 확인 후 지워 주세요)."
  $found = $true
}
if (-not $found) { Say '설치된 udally-link를 찾지 못했습니다.' }
} finally { [Console]::OutputEncoding = $prevEnc }
}
