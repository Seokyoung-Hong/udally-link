#!/bin/sh
# udally-stt 제거(macOS·Linux): curl -fsSL https://udally.sio2.kr/stt/uninstall.sh | sh
# 연결돼 있으면 유달리에서 스스로 끊고(DELETE /self), 컨테이너 또는 uv 도구·자동 시작을 지운다. 받은 모델은 남긴다.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
set -eu
NAME="${UDALLY_NAME:-udally-stt}"
VOLUME="${UDALLY_VOLUME:-udally-stt}"
DRY="${UDALLY_DRY_RUN:-0}"
say() { printf '%s\n' "$*"; }
run() { if [ "$DRY" = 1 ]; then say "[dry-run] $*"; else "$@"; fi; }
done_any=0

if command -v docker >/dev/null 2>&1 && docker inspect "$NAME" >/dev/null 2>&1; then
  img=$(docker inspect -f '{{.Config.Image}}' "$NAME")
  run docker rm -f "$NAME" >/dev/null
  run docker run --rm -v "$VOLUME:/cache" "$img" unlink || true
  say "컨테이너 $NAME을 지웠습니다. 받은 모델까지 지우려면: docker volume rm $VOLUME"
  done_any=1
fi

PATH="$HOME/.local/bin:$PATH"
if command -v udally-stt >/dev/null 2>&1; then
  if [ "$(uname -s)" = Darwin ]; then
    f="$HOME/Library/LaunchAgents/kr.sio2.udally-stt.plist"
    [ -f "$f" ] && { run launchctl unload "$f" 2>/dev/null || true; run rm -f "$f"; }
  elif [ -f "$HOME/.config/systemd/user/udally-stt.service" ]; then
    run systemctl --user disable --now udally-stt.service || true
    run rm -f "$HOME/.config/systemd/user/udally-stt.service"
    run systemctl --user daemon-reload || true
  fi
  run udally-stt unlink || true
  run uv tool uninstall udally-stt
  say "udally-stt를 지웠습니다. 받은 모델까지 지우려면: rm -rf ~/.cache/huggingface/hub/models--*whisper* (다른 프로그램과 함께 쓰는 폴더이니 확인 후)"
  done_any=1
fi
[ "$done_any" = 1 ] || say "설치된 udally-stt를 찾지 못했습니다."
