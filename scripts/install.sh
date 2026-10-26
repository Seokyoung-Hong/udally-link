#!/bin/sh
# udally-link 한 줄 설치(macOS·Linux): curl -fsSL https://udally.quanect.kr/link/install.sh | sh
# 다시 실행하면 업데이트(이미 연결돼 있으면 다시 연결하지 않음). 제거: curl -fsSL https://udally.quanect.kr/link/uninstall.sh | sh
# 사람 입력을 받지 않는다(| sh는 표준 입력이 스크립트다). sudo를 쓰지 않는다. 토큰을 출력하지 않는다.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
set -eu

# ---------- 릴리스 값(.github/workflows/release.yml이 채운다) ----------
VERSION="0.1.0"
COMMIT=""
IMAGE="ghcr.io/seokyoung-hong/udally-link"
REPO="https://github.com/Seokyoung-Hong/udally-link"
DIGEST_CPU=""
DIGEST_CUDA=""
DIGEST_OPENVINO=""
DIGEST_ROCM=""
UV_VERSION="0.13.0"
CT2_ROCM_ZIP="https://github.com/OpenNMT/CTranslate2/releases/download/v4.8.2/rocm-python-wheels-Linux.zip"
CT2_ROCM_SHA256="b469e765f74ef85fb97bf4fe2347c8c6296dda00d7f5cf20658f23e95004a925"
ROCM_VERSION="7.13.0"
# ---------- 시험용 덮어쓰기(평소에는 비움) ----------
UDALLY_URL="${UDALLY_URL:-https://udally.quanect.kr}"
NAME="${UDALLY_NAME:-udally-link}"          # 컨테이너 이름
VOLUME="${UDALLY_VOLUME:-udally-link}"      # 모델·토큰 볼륨
LEGACY="udally-stt"                         # 옛 이름(컨테이너·볼륨·uv 도구·자동 시작). 있으면 정리하고 새 이름으로
DRY="${UDALLY_DRY_RUN:-0}"                 # 1이면 바꾸는 명령을 출력만 한다
FORCE="${UDALLY_FORCE:-}"                  # docker | uv
GPU_FORCE="${UDALLY_GPU:-}"                # cuda | rocm | openvino | cpu

say() { printf '%s\n' "$*"; }
run() { if [ "$DRY" = 1 ]; then say "[dry-run] $*"; else "$@"; fi; }
fail() { say "오류: $*" >&2; exit 1; }
case "$UDALLY_URL" in https://*|http://127.0.0.1*|http://localhost*|http://host.docker.internal*) ;; *) fail "UDALLY_URL은 https:// 주소여야 합니다." ;; esac

# ---------- GPU 판별: NVIDIA > AMD > Intel > CPU ----------
gpu_kind() {
  [ -n "$GPU_FORCE" ] && { say "$GPU_FORCE"; return; }
  if [ "$(uname -s)" = Darwin ]; then say cpu; return; fi
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then say cuda; return; fi
  if [ -e /dev/kfd ]; then say rocm; return; fi
  for v in /sys/class/drm/*/device/vendor; do
    [ -r "$v" ] && [ "$(cat "$v")" = 0x8086 ] && { say openvino; return; }
  done
  say cpu
}

# CTranslate2 ROCm 휠 대상 → AMD pip 색인 이름(backends.py GFX_INDEX와 같은 표)
gfx_index() {
  for p in /sys/class/kfd/kfd/topology/nodes/*/properties; do
    v=$(sed -n 's/^gfx_target_version //p' "$p" 2>/dev/null || true)
    [ -n "$v" ] && [ "$v" != 0 ] || continue
    case "$v" in
      1003??) say gfx103X-all; return ;; 1100??) say gfx110X-all; return ;; 110500) say gfx1150; return ;;
      110501) say gfx1151; return ;; 1200??) say gfx120X-all; return ;;
    esac
  done
  return 0  # 대상 아님: 빈 출력(set -e에서 멈추지 않게)
}

digest_of() {
  case "$1" in cuda) say "$DIGEST_CUDA" ;; openvino) say "$DIGEST_OPENVINO" ;; rocm) say "$DIGEST_ROCM" ;; *) say "$DIGEST_CPU" ;; esac
}

# ---------- Docker 경로 ----------
docker_install() {
  kind=$1
  args=""
  case "$kind" in
    cuda) if [ "$DRY" = 1 ] || docker run --rm --gpus all busybox true >/dev/null 2>&1; then args="--gpus all"; else kind=cpu; fi ;;
    rocm) args="--device /dev/kfd --device /dev/dri --group-add $(stat -c %g /dev/kfd)" ;;
    openvino)
      r=""; for f in /dev/dri/renderD*; do [ -e "$f" ] && { r=$f; break; }; done
      if [ -n "$r" ]; then args="--device /dev/dri --group-add $(stat -c %g "$r")"; else kind=cpu; fi ;;
  esac
  ref="${UDALLY_IMAGE:-}"
  if [ -z "$ref" ]; then
    d=$(digest_of "$kind")
    if [ -z "$d" ] && [ "$kind" != cpu ]; then say "이 GPU용 공개 이미지가 없어 CPU 이미지를 씁니다."; kind=cpu; args=""; d=$DIGEST_CPU; fi
    [ -n "$d" ] || fail "릴리스 값(이미지 digest)이 비어 있습니다. 유달리 화면의 설치 명령을 다시 복사해 주세요."
    ref="$IMAGE@$d"
    run docker pull "$ref"
  fi
  say "udally-link $VERSION ($kind, Docker)를 설치합니다."
  # udally-stt 시절 볼륨(토큰·모델)이 있고 새 볼륨이 없으면 그것을 이어 쓴다(다시 연결하지 않게)
  if [ -z "${UDALLY_VOLUME:-}" ] && docker volume inspect "$LEGACY" >/dev/null 2>&1 && ! docker volume inspect "$VOLUME" >/dev/null 2>&1; then
    VOLUME=$LEGACY
  fi
  if [ "$NAME" != "$LEGACY" ] && docker inspect "$LEGACY" >/dev/null 2>&1; then
    say "옛 이름 컨테이너 $LEGACY을 지우고 $NAME으로 바꿉니다."
    run docker rm -f "$LEGACY" >/dev/null 2>&1 || true
  fi
  run docker rm -f "$NAME" >/dev/null 2>&1 || true
  # shellcheck disable=SC2086 # args는 단어로 나뉘어야 한다
  run docker run -d --name "$NAME" --restart unless-stopped -v "$VOLUME:/cache" -e UDALLY_URL="$UDALLY_URL" $args "$ref" connect >/dev/null
  [ "$DRY" = 1 ] && return
  # 이미 연결됐으면(볼륨에 토큰) 바로 끝. 아니면 기기 인증 주소·코드가 보일 때까지 로그를 보여 준다.
  seen=0 i=0
  while [ $i -lt 900 ]; do
    if docker exec "$NAME" test -f /cache/udally-link.json 2>/dev/null; then
      say "연결되었습니다. 이 PC가 켜져 있는 동안 유달리가 전사에 씁니다(컨테이너 $NAME)."
      return
    fi
    n=$(docker logs "$NAME" 2>&1 | wc -l)
    [ "$n" -gt "$seen" ] && docker logs "$NAME" 2>&1 | tail -n $((n - seen)) | grep -v -e ' INFO ' -e ' WARNING ' || true
    seen=$n
    docker ps -q -f "name=^/${NAME}$" -f status=running | grep -q . || fail "컨테이너가 멈췄습니다: docker logs $NAME"
    sleep 2; i=$((i + 2))
  done
  fail "15분 안에 연결되지 않았습니다. 다시 실행해 주세요."
}

# ---------- uv 경로 ----------
uv_install() {
  kind=$1
  if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
    say "uv $UV_VERSION을 사용자 영역에 설치합니다."
    if [ "$DRY" = 1 ]; then say "[dry-run] curl https://astral.sh/uv/$UV_VERSION/install.sh | sh"
    else curl -fsSL --proto '=https' --tlsv1.2 "https://astral.sh/uv/$UV_VERSION/install.sh" | env UV_NO_MODIFY_PATH=1 sh; fi
  fi
  PATH="$HOME/.local/bin:$PATH"; export PATH
  [ -n "$COMMIT" ] || [ -n "${UDALLY_SOURCE:-}" ] || fail "릴리스 값(커밋)이 비어 있습니다."
  src="${UDALLY_SOURCE:-git+$REPO@$COMMIT}"
  extra="" with=""
  case "$kind" in
    cuda) extra="[cuda]" ;;
    openvino) extra="[openvino]" ;;
    rocm)
      idx=$(gfx_index)
      if [ -z "$idx" ]; then say "이 AMD GPU는 CTranslate2 ROCm 휠 대상이 아니라 CPU로 설치합니다."; kind=cpu
      else
        say "AMD GPU는 실험 지원입니다. CTranslate2 ROCm 휠과 ROCm $ROCM_VERSION($idx)을 받습니다(수백 MB)."
        d="$HOME/.cache/udally-link"; run mkdir -p "$d"
        if [ "$DRY" != 1 ]; then
          curl -fsSL --proto '=https' -o "$d/ct2-rocm.zip" "$CT2_ROCM_ZIP"
          echo "$CT2_ROCM_SHA256  $d/ct2-rocm.zip" | sha256sum -c - >/dev/null || fail "CTranslate2 ROCm 휠 검증 실패"
          rm -rf "$d/ct2-rocm" && mkdir -p "$d/ct2-rocm" && (cd "$d/ct2-rocm" && { python3 -m zipfile -e ../ct2-rocm.zip . 2>/dev/null || unzip -q ../ct2-rocm.zip; })
          whl=$(find "$d/ct2-rocm" -name 'ctranslate2-*-cp312-*manylinux*x86_64.whl' | head -n1)
        else whl="$d/ct2-rocm/ctranslate2.whl"; fi
        with="--with $whl --with rocm[libraries]==$ROCM_VERSION --index https://repo.amd.com/rocm/whl/$idx/"
      fi ;;
  esac
  legacy_cleanup
  say "udally-link $VERSION ($kind, uv)를 설치합니다."
  # shellcheck disable=SC2086
  run uv tool install --force --python 3.12 "udally-link$extra @ $src" $with
  run env UDALLY_URL="$UDALLY_URL" udally-link connect --until-linked
  autostart
}

# udally-stt 시절 uv 도구·자동 시작을 지운다. 연결 토큰(~/.config/udally-stt)은 udally-link가 옮겨 쓴다.
legacy_cleanup() {
  [ -x "$HOME/.local/bin/$LEGACY" ] || return 0
  say "옛 이름 $LEGACY을 지우고 udally-link로 바꿉니다(연결 토큰은 그대로 옮겨 씁니다)."
  f="$HOME/Library/LaunchAgents/kr.sio2.$LEGACY.plist"
  if [ -f "$f" ]; then run launchctl unload "$f" 2>/dev/null || true; run rm -f "$f"; fi
  f="$HOME/.config/systemd/user/$LEGACY.service"
  if [ -f "$f" ]; then
    run systemctl --user disable --now "$LEGACY.service" || true
    run rm -f "$f"
    run systemctl --user daemon-reload || true
  fi
  run uv tool uninstall "$LEGACY" || true
}

autostart() {
  bin="$HOME/.local/bin/udally-link"
  if [ "$(uname -s)" = Darwin ]; then
    f="$HOME/Library/LaunchAgents/kr.sio2.udally-link.plist"
    run mkdir -p "$(dirname "$f")"
    if [ "$DRY" = 1 ]; then say "[dry-run] write $f"; else cat > "$f" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>kr.sio2.udally-link</string>
  <key>ProgramArguments</key><array><string>$bin</string><string>connect</string></array>
  <key>EnvironmentVariables</key><dict><key>UDALLY_URL</key><string>$UDALLY_URL</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/udally-link.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/udally-link.log</string>
</dict></plist>
EOF
    fi
    run launchctl unload "$f" 2>/dev/null || true
    run launchctl load -w "$f"
    say "로그인할 때 자동으로 켜집니다(launchd)."
    return
  fi
  if ! systemctl --user show-environment >/dev/null 2>&1; then
    say "systemd 사용자 세션이 없어 자동 시작 등록을 건너뜁니다. 직접 켜기: udally-link connect"
    return
  fi
  f="$HOME/.config/systemd/user/udally-link.service"
  run mkdir -p "$(dirname "$f")"
  if [ "$DRY" = 1 ]; then say "[dry-run] write $f"; else cat > "$f" <<EOF
[Unit]
Description=udally-link (유달리 전사 서버 연결)
After=network-online.target

[Service]
Environment=UDALLY_URL=$UDALLY_URL
ExecStart=$bin connect
Restart=on-failure
RestartSec=10
# 1 거절 · 3 연결 끊김 · 4 업데이트 필요 → 다시 띄우지 않는다
RestartPreventExitStatus=1 3 4

[Install]
WantedBy=default.target
EOF
  fi
  run systemctl --user daemon-reload
  run systemctl --user enable --now udally-link.service
  run systemctl --user restart udally-link.service
  say "로그인할 때 자동으로 켜집니다(systemd --user). 상태: systemctl --user status udally-link"
}

# 이미지 릴리스 전에는 실행 가능한 uv 설치를 사용한다.
if [ -z "$DIGEST_CPU" ] && [ -z "${UDALLY_IMAGE:-}" ] && [ "$FORCE" != docker ]; then
  FORCE=uv
  say "공개 Docker 이미지 릴리스가 없어 uv로 설치합니다."
fi
kind=$(gpu_kind)
if [ "$FORCE" != uv ] && command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 || [ "$FORCE" = docker ]; then
  docker_install "$kind"
else
  [ "$FORCE" = uv ] || say "Docker를 쓸 수 없어(없음 또는 권한 없음) uv로 설치합니다."
  uv_install "$kind"
fi
