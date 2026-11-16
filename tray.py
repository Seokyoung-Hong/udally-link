# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Windows 알림 영역 아이콘: udally-link connect --tray.
연결 루프(link.Agent)는 뒤 스레드에서 돌고, 아이콘은 상태(연결됨·전사 중·닿지 않음)를 보여 준다.
메뉴: 유달리에서 이 PC 보기 · 로그 보기 · 끝내기. 이미 켜져 있으면 새로 띄우지 않고 유달리 화면만 연다."""

import ctypes
import os
import threading
import time
import webbrowser

COLORS = {"online": (46, 160, 67), "busy": (31, 111, 235), "offline": (140, 140, 140)}
TITLES = {"online": "연결됨", "busy": "작업 중", "offline": "유달리에 닿지 않음"}
EXIT_MSG = {3: "유달리에서 이 PC 연결이 끊겼습니다. 설치 명령을 다시 실행해 연결해 주세요.",
            4: "새 버전이 필요합니다. 설치 명령을 다시 실행해 업데이트해 주세요."}
ERROR_ALREADY_EXISTS = 183


def state(agent) -> str:
    if not agent.online:
        return "offline"
    return "busy" if agent.job or agent.ai else "online"


def image(color):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=color)
    d.text((32, 32), "U", fill="white", font_size=34, anchor="mm")
    return img


def already_running() -> bool:
    # 핸들은 프로세스가 끝날 때까지 쥔다(닫으면 다음 실행이 혼자라고 착각한다)
    already_running.handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\udally-link-tray")
    return ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS


def open_page(link: dict) -> int:
    webbrowser.open(f"{link['url']}/settings/stt")
    return 0


def run(link: dict, agent) -> int:
    import pystray

    log_file = os.environ.get("LINK_LOG_FILE", "")
    result = {"code": 0}

    def open_log(icon, item):
        if log_file and os.path.exists(log_file):
            os.startfile(log_file)

    menu = pystray.Menu(
        pystray.MenuItem("유달리에서 이 PC 보기", lambda i, it: open_page(link), default=True),
        pystray.MenuItem("로그 보기", open_log, enabled=lambda it: bool(log_file)),
        pystray.MenuItem("끝내기", lambda i, it: i.stop()),
    )
    icon = pystray.Icon("udally-link", image(COLORS["offline"]), "유달리 링크", menu)

    def work():
        result["code"] = agent.loop()
        if msg := EXIT_MSG.get(result["code"]):
            icon.notify(msg, "유달리 링크")
            time.sleep(10)
        icon.stop()

    def watch():
        last = None
        while icon.visible or last is None:
            now = state(agent)
            if now != last:
                icon.icon, icon.title, last = image(COLORS[now]), f"유달리 링크 — {TITLES[now]}", now
            time.sleep(2)

    def setup(icon):
        icon.visible = True
        threading.Thread(target=work, daemon=True).start()
        threading.Thread(target=watch, daemon=True).start()

    icon.run(setup)
    return result["code"]
