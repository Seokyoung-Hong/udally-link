# udally-link 한 줄 설치(Windows PowerShell 5.1·7): irm https://udally.quanect.kr/link/install.ps1 | iex
# 다시 실행하면 업데이트(이미 연결돼 있으면 다시 연결하지 않음). 제거: irm https://udally.quanect.kr/link/uninstall.ps1 | iex
# 사람 입력을 받지 않는다. 관리자 권한(UAC)을 요구하지 않는다. 토큰을 출력하지 않는다.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
& {
$ErrorActionPreference = 'Stop'
# docker·uv가 내는 UTF-8 출력을 한국어 Windows 콘솔(CP949)에서도 깨지지 않게 읽는다. 끝나면 되돌린다.
$prevEnc = [Console]::OutputEncoding
$prevPythonIoEncoding = [Environment]::GetEnvironmentVariable('PYTHONIOENCODING', 'Process')
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
try {
# Run이 Python stdout을 파이프로 받으므로 CP949 대신 읽는 쪽과 같은 UTF-8을 쓴다.
$env:PYTHONIOENCODING = 'utf-8'
Set-StrictMode -Version 2

# ---------- 릴리스 값(.github/workflows/release.yml이 채운다) ----------
$Version = '0.1.0'
$Commit = 'b587015b60d2da22594825f45a27e9417d0152cf'
$Image = 'ghcr.io/seokyoung-hong/udally-link'
$Repo = 'https://github.com/Seokyoung-Hong/udally-link'
$Digest = @{ cpu = ''; cuda = ''; openvino = '' }
$UvVersion = '0.13.0'
$Ct2RocmZip = 'https://github.com/OpenNMT/CTranslate2/releases/download/v4.8.2/rocm-python-wheels-Windows.zip'
$Ct2RocmSha256 = '43da4baa5feaee49f77e176277a9647f99c493173c81a0bc60f491cac97532c2'
# CTranslate2 Windows ROCm 휠이 기대하는 AMD 공식 ROCm 휠(_rocm_sdk_core·_rocm_sdk_libraries_custom DLL 폴더)
$RocmWheels = @(
  'https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl',
  'https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl'
)

# ---------- 시험용 덮어쓰기(평소에는 비움) ----------
function GetEnv($name, $default) { $v = [Environment]::GetEnvironmentVariable($name); if ($v) { $v } else { $default } }
$UdallyUrl = GetEnv 'UDALLY_URL' 'https://udally.quanect.kr'
$Name = GetEnv 'UDALLY_NAME' 'udally-link'
$Volume = GetEnv 'UDALLY_VOLUME' 'udally-link'
$LegacyName = 'udally-stt'  # 옛 이름(컨테이너·볼륨·uv 도구·시작프로그램). 있으면 정리하고 새 이름으로
$Dry = (GetEnv 'UDALLY_DRY_RUN' '0') -eq '1'
$Force = GetEnv 'UDALLY_FORCE' ''
$GpuForce = GetEnv 'UDALLY_GPU' ''
if ($UdallyUrl -notmatch '^(https://|http://(127\.0\.0\.1|localhost|host\.docker\.internal))') { throw 'UDALLY_URL은 https:// 주소여야 합니다.' }

# PowerShell 5.1은 TLS 1.2가 기본이 아닐 수 있다
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Say($t) { Write-Host $t }
function Run([string]$exe, [string[]]$argv) {
  if ($Dry) { Say ("[dry-run] $exe " + ($argv -join ' ')); return 0 }
  & $exe @argv | Out-Host
  return $LASTEXITCODE
}
function Quiet([string]$exe, [string[]]$argv) {
  $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { & $exe @argv *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false } finally { $ErrorActionPreference = $old }
}

# ---------- GPU 판별: NVIDIA > AMD > Intel > CPU ----------
function GpuKind {
  if ($GpuForce) { return $GpuForce }
  $ids = @(Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | ForEach-Object { $_.PNPDeviceID })
  if ($ids -match 'VEN_10DE') { return 'cuda' }
  if ($ids -match 'VEN_1002') { return 'rocm' }
  if ($ids -match 'VEN_8086') { return 'openvino' }
  return 'cpu'
}

# ---------- Docker 경로(NVIDIA·Intel·CPU. AMD는 Docker Desktop에 ROCm 경로가 없어 uv) ----------
function DockerInstall($kind) {
  $gpuArgs = @()
  if ($kind -eq 'cuda') {
    if ($Dry -or (Quiet docker @('run', '--rm', '--gpus', 'all', 'busybox', 'true'))) { $gpuArgs = @('--gpus', 'all') } else { $kind = 'cpu' }
  } elseif ($kind -eq 'openvino') {
    # Docker Desktop(WSL2)에서 Intel GPU: OpenVINO 공식 안내
    $gpuArgs = @('--device', '/dev/dxg', '-v', '/usr/lib/wsl:/usr/lib/wsl')
  }
  $ref = GetEnv 'UDALLY_IMAGE' ''
  if (-not $ref) {
    $d = $Digest[$kind]
    if (-not $d -and $kind -ne 'cpu') { Say '이 GPU용 공개 이미지가 없어 CPU 이미지를 씁니다.'; $kind = 'cpu'; $gpuArgs = @(); $d = $Digest['cpu'] }
    if (-not $d) { throw '릴리스 값(이미지 digest)이 비어 있습니다. 유달리 화면의 설치 명령을 다시 복사해 주세요.' }
    $ref = "$Image@$d"
    if ((Run docker @('pull', $ref)) -ne 0) { throw "이미지를 받지 못했습니다: $ref" }
  }
  Say "udally-link $Version ($kind, Docker)를 설치합니다."
  # udally-stt 시절 볼륨(토큰·모델)이 있고 새 볼륨이 없으면 그것을 이어 쓴다(다시 연결하지 않게)
  $vol = $Volume
  if (-not (GetEnv 'UDALLY_VOLUME' '') -and (Quiet docker @('volume', 'inspect', $LegacyName)) -and -not (Quiet docker @('volume', 'inspect', $Volume))) { $vol = $LegacyName }
  if ($Name -ne $LegacyName -and (Quiet docker @('inspect', $LegacyName))) {
    Say "옛 이름 컨테이너 ${LegacyName}을 지우고 ${Name}으로 바꿉니다."
    if ($Dry) { Say "[dry-run] docker rm -f $LegacyName" } else { Quiet docker @('rm', '-f', $LegacyName) | Out-Null }
  }
  if (-not $Dry) { Quiet docker @('rm', '-f', $Name) | Out-Null } else { Say "[dry-run] docker rm -f $Name" }
  $argv = @('run', '-d', '--name', $Name, '--restart', 'unless-stopped', '-v', "${vol}:/cache", '-e', "UDALLY_URL=$UdallyUrl") + $gpuArgs + @($ref, 'connect')
  if ($Dry) { Say ('[dry-run] docker ' + ($argv -join ' ')) }
  else { & docker @argv | Out-Null; if ($LASTEXITCODE -ne 0) { throw '컨테이너를 시작하지 못했습니다.' } }
  if ($Dry) { return }
  # 이미 연결됐으면(볼륨에 토큰) 바로 끝. 아니면 기기 인증 주소·코드가 보일 때까지 로그를 보여 준다.
  $seen = 0
  for ($i = 0; $i -lt 450; $i++) {
    if (Quiet docker @('exec', $Name, 'test', '-f', '/cache/udally-link.json')) {
      Say "연결되었습니다. 이 PC가 켜져 있는 동안 유달리가 전사에 씁니다(컨테이너 $Name)."
      return
    }
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $lines = @(docker logs $Name 2>&1 | ForEach-Object { "$_" })
    $ErrorActionPreference = $old
    if ($lines.Count -gt $seen) { $lines[$seen..($lines.Count - 1)] | Where-Object { $_ -notmatch ' (INFO|WARNING) ' } | ForEach-Object { Say $_ } }
    $seen = $lines.Count
    if (-not (docker ps -q -f "name=^/$Name$" -f status=running)) { throw "컨테이너가 멈췄습니다: docker logs $Name" }
    Start-Sleep -Seconds 2
  }
  throw '15분 안에 연결되지 않았습니다. 다시 실행해 주세요.'
}

# ---------- uv 경로 ----------
function UvInstall($kind) {
  $bin = Join-Path $HOME '.local\bin'
  $env:Path = "$bin;$env:Path"
  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Say "uv ${UvVersion}을 사용자 영역에 설치합니다."
    if ($Dry) { Say "[dry-run] irm https://astral.sh/uv/$UvVersion/install.ps1 | iex" }
    else { $env:UV_NO_MODIFY_PATH = '1'; Invoke-RestMethod "https://astral.sh/uv/$UvVersion/install.ps1" | Invoke-Expression }
  }
  $src = GetEnv 'UDALLY_SOURCE' ''
  if (-not $src) { if (-not $Commit) { throw '릴리스 값(커밋)이 비어 있습니다.' }; $src = "git+$Repo@$Commit" }
  $extra = ''; $with = @()
  if ($kind -eq 'cuda') { $extra = '[cuda]' }
  elseif ($kind -eq 'openvino') { $extra = '[openvino]' }
  elseif ($kind -eq 'rocm') {
    Say 'AMD GPU는 실험 지원입니다. CTranslate2 ROCm 휠과 AMD ROCm 7.2.1 휠을 받습니다(약 1.2GB).'
    $dir = Join-Path $env:LOCALAPPDATA 'udally-link'
    $zip = Join-Path $dir 'ct2-rocm.zip'
    $whlDir = Join-Path $dir 'ct2-rocm'
    if ($Dry) { $whl = Join-Path $whlDir 'ctranslate2.whl'; Say "[dry-run] download $Ct2RocmZip" }
    else {
      New-Item -ItemType Directory -Force $dir | Out-Null
      Invoke-WebRequest $Ct2RocmZip -OutFile $zip -UseBasicParsing
      if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $Ct2RocmSha256) { throw 'CTranslate2 ROCm 휠 검증 실패' }
      if (Test-Path $whlDir) { Remove-Item -Recurse -Force $whlDir }
      Expand-Archive $zip -DestinationPath $whlDir
      $whl = (Get-ChildItem $whlDir -Recurse -Filter 'ctranslate2-*-cp312-*win_amd64.whl' | Select-Object -First 1).FullName
    }
    $with = @('--with', $whl)
    foreach ($w in $RocmWheels) { $with += @('--with', $w) }
  }
  $legacyExe = Join-Path $bin "$LegacyName.exe"
  if (Test-Path $legacyExe) {
    Say "옛 이름 ${LegacyName}을 지우고 udally-link로 바꿉니다(연결 토큰은 그대로 옮겨 씁니다)."
    if ($Dry) { Say "[dry-run] stop $legacyExe, remove $LegacyName.lnk, uv tool uninstall $LegacyName" }
    else {
      Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $legacyExe } | Stop-Process -Force
      Remove-Item -Force (Join-Path ([Environment]::GetFolderPath('Startup')) "$LegacyName.lnk") -ErrorAction SilentlyContinue
      Quiet uv @('tool', 'uninstall', $LegacyName) | Out-Null
    }
  }
  Say "udally-link $Version ($kind, uv)를 설치합니다."
  # 실행 중이면 Windows가 exe를 잠가 uv가 덮어쓰지 못한다(업데이트 실패). 설치 전에 끈다(자식 python까지).
  if (-not $Dry) { Get-Process -Name 'udally-link' -ErrorAction SilentlyContinue | ForEach-Object { & taskkill.exe /PID $_.Id /T /F 2>$null | Out-Null } }
  if ((Run uv (@('tool', 'install', '--force', '--python', '3.12', "udally-link$extra @ $src") + $with)) -ne 0) { throw 'uv 설치에 실패했습니다.' }
  $env:UDALLY_URL = $UdallyUrl
  $exe = Join-Path $bin 'udally-link.exe'
  if ((Run $exe @('connect', '--until-linked')) -ne 0) { throw '연결하지 못했습니다.' }
  # 자동 시작: 시작프로그램 폴더 바로가기. 관리자 권한 불필요.
  # 콘솔 창을 띄우지 않는다: conhost --headless(Windows 10 1809+)로 실행하고 로그는 파일(LINK_LOG_FILE)로 남긴다.
  $lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'udally-link.lnk'
  $conhost = Join-Path $env:SystemRoot 'System32\conhost.exe'
  $logFile = Join-Path $env:LOCALAPPDATA 'udally-link\link.log'
  $runArgs = "--headless `"$exe`" connect"
  if ($Dry) { Say "[dry-run] shortcut $lnk -> $conhost $runArgs (log $logFile)" }
  else {
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $s.TargetPath = $conhost; $s.Arguments = $runArgs; $s.WindowStyle = 7; $s.Save()
    [Environment]::SetEnvironmentVariable('UDALLY_URL', $UdallyUrl, 'User')
    [Environment]::SetEnvironmentVariable('LINK_LOG_FILE', $logFile, 'User'); $env:LINK_LOG_FILE = $logFile
    Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe } | Stop-Process -Force
    Start-Process $conhost -ArgumentList $runArgs -WindowStyle Hidden
  }
  Say "로그는 $logFile 에 남습니다."
  Say '로그인할 때 자동으로 켜집니다(시작프로그램).'
}

$kind = GpuKind
# 이미지 릴리스 전에는 실행 가능한 uv 설치를 사용한다.
if (-not $Digest['cpu'] -and -not (GetEnv 'UDALLY_IMAGE' '') -and $Force -ne 'docker') {
  $Force = 'uv'
  Say '공개 Docker 이미지 릴리스가 없어 uv로 설치합니다.'
}
$hasDocker = ($Force -ne 'uv') -and (Get-Command docker -ErrorAction SilentlyContinue) -and (Quiet docker @('info'))
if (($hasDocker -and $kind -ne 'rocm') -or $Force -eq 'docker') { DockerInstall $kind }
else {
  if ($Force -ne 'uv') { Say 'uv로 설치합니다(Docker 없음 또는 AMD GPU).' }
  UvInstall $kind
}
} finally {
  [Console]::OutputEncoding = $prevEnc
  if ($null -eq $prevPythonIoEncoding) {
    Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
  } else {
    [Environment]::SetEnvironmentVariable('PYTHONIOENCODING', $prevPythonIoEncoding, 'Process')
  }
}
}
