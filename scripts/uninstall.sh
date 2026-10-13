#!/bin/sh
# udally-link 제거(macOS·Linux): curl -fsSL https://udally.sio2.kr/link/uninstall.sh | sh
# 연결돼 있으면 유달리에서 스스로 끊고(DELETE /self), 컨테이너 또는 uv 도구·자동 시작을 지운다. 받은 모델은 남긴다.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
set -eu
NAME="${UDALLY_NAME:-udally-link}"
VOLUME="${UDALLY_VOLUME:-udally-link}"
DRY="${UDALLY_DRY_RUN:-0}"
say() { printf '%s\n' "$*"; }
run() { if [ "$DRY" = 1 ]; then say "[dry-run] $*"; else "$@"; fi; }
done_any=0

for n in "$NAME" udally-stt; do  # udally-stt = 옛 이름
  command -v docker >/dev/null 2>&1 && docker inspect "$n" >/dev/null 2>&1 || continue
  img=$(docker inspect -f '{{.Config.Image}}' "$n")
  vol=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/cache"}}{{.Name}}{{end}}{{end}}' "$n")
  [ -n "$vol" ] || vol=$VOLUME
  run docker rm -f "$n" >/dev/null
  run docker run --rm -v "$vol:/cache" "$img" unlink || true
  say "컨테이너 $n을 지웠습니다. 받은 모델까지 지우려면: docker volume rm $vol"
  done_any=1
done

PATH="$HOME/.local/bin:$PATH"
for tool in udally-link udally-stt; do  # udally-stt = 옛 이름
  command -v "$tool" >/dev/null 2>&1 || continue
  if [ "$(uname -s)" = Darwin ]; then
    f="$HOME/Library/LaunchAgents/kr.sio2.$tool.plist"
    [ -f "$f" ] && { run launchctl unload "$f" 2>/dev/null || true; run rm -f "$f"; }
  elif [ -f "$HOME/.config/systemd/user/$tool.service" ]; then
    run systemctl --user disable --now "$tool.service" || true
    run rm -f "$HOME/.config/systemd/user/$tool.service"
    run systemctl --user daemon-reload || true
  fi
  run "$tool" unlink || true
  run uv tool uninstall "$tool"
  say "$tool를 지웠습니다. 받은 모델까지 지우려면: rm -rf ~/.cache/huggingface/hub/models--*whisper* (다른 프로그램과 함께 쓰는 폴더이니 확인 후)"
  done_any=1
done
[ "$done_any" = 1 ] || say "설치된 udally-link를 찾지 못했습니다."
