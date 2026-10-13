# udally-link

유달리(Discord 회의 녹음)가 쓰는 **로컬 음성 전사 서버**입니다. 회의 음성을 내 PC·조직 서버에서 전사하므로 외부 전사 서비스로 보내지 않습니다.

- **내 PC 연결(기본)**: 명령 한 줄로 설치하면 기기 인증(주소+코드)으로 유달리 계정에 묶이고, PC가 켜져 있는 동안 유달리가 이 PC로 전사합니다. PC → 유달리로 나가는 연결만 쓰므로 포트포워딩이 필요 없습니다(0절).
- **서버 주소 직접 입력(고급)**: OpenAI 호환 `POST /v1/audio/transcriptions`와 모델 목록·받기·장비 적합 정보를 HTTP로 엽니다(1절~).
- 전사 엔진: [faster-whisper](https://github.com/SYSTRAN/faster-whisper)(CTranslate2, MIT) — NVIDIA CUDA·AMD ROCm(실험)·CPU. Intel GPU는 [OpenVINO GenAI](https://github.com/openvinotoolkit/openvino.genai)(Apache-2.0), NVIDIA Nemotron은 Transformers. 장치별 실행부는 `backends.py` 한 곳에 모았습니다.
- 이 저장소가 직접 짠 것: `server.py`(HTTP 규약, 모델 상태·받기, 메모리 계획, OOM 강등, 취소), `link.py`(유달리 연결), `backends.py`(장치별 실행부), `scripts/`(설치·제거).
- **라이선스: PolyForm Noncommercial 1.0.0(비상업, 소스 공개)** — 10절. 의존성·NVIDIA 구성요소·모델은 각자 라이선스입니다.

> **이름이 바뀌었습니다: udally-stt → udally-link** (전사에 더해 AI 자동완성 거쳐 보내기까지 맡습니다). 옛 설치는 0절 "옛 이름(udally-stt)에서 옮기기"를 보세요.

## 0. 내 PC를 유달리 전사 서버로 연결하기

| OS | 설치·업데이트(같은 명령) | 제거 |
|---|---|---|
| Windows (PowerShell 5.1·7) | `irm https://udally.sio2.kr/link/install.ps1 \| iex` | `irm https://udally.sio2.kr/link/uninstall.ps1 \| iex` |
| macOS · Linux | `curl -fsSL https://udally.sio2.kr/link/install.sh \| sh` | `curl -fsSL https://udally.sio2.kr/link/uninstall.sh \| sh` |

- 실행 전에 내용을 보고 싶으면 위 주소를 브라우저로 여세요(유달리가 이 저장소의 고정 커밋 `scripts/` 원본으로 보냅니다).
- 스크립트는 관리자 권한(sudo·UAC)을 요구하지 않고, 사람 입력을 받지 않으며, 토큰을 출력하지 않습니다.
- 화면에 나온 주소(`https://udally.sio2.kr/link`)를 직접 열고 **PC 화면의 코드를 손으로 입력**한 뒤 [허용]합니다. 코드가 든 링크를 남에게 받았다면 누르지 마세요.
- **Docker가 있으면** GHCR 이미지(digest 고정)를 받아 `udally-link` 컨테이너(`--restart unless-stopped`, 볼륨 `udally-link:/cache`)로 띄웁니다. 토큰은 볼륨에 남으므로 다시 실행 = 업데이트이고 다시 연결하지 않습니다.
- **Docker가 없으면(또는 AMD GPU면)** [uv](https://docs.astral.sh/uv/)(버전 고정)로 사용자 영역에 설치하고 로그인 때 자동으로 켭니다(Windows 시작프로그램(창 없이 `conhost --headless`, 로그는 `%LOCALAPPDATA%Dally-linkink.log`), Linux `systemd --user`, macOS LaunchAgent).
- GPU 판별: NVIDIA > AMD > Intel > CPU(Windows `Win32_VideoController` VEN_10DE/1002/8086, Linux `nvidia-smi`·`/dev/kfd`·`/sys/class/drm/*/device/vendor`). 판별·준비가 실패하면 CPU로 내립니다.

| GPU | Docker | uv |
|---|---|---|
| NVIDIA | 이미지 `-cuda` + `--gpus all` | `udally-link[cuda]`(cuBLAS·cuDNN 휠) |
| AMD (**실험**) | Linux만 `-rocm` + `/dev/kfd`·`/dev/dri`. Windows Docker에는 ROCm 경로가 없어 uv | CTranslate2 ROCm 휠(OpenNMT 공식 릴리스 v4.8.2, sha256 고정) + AMD 공식 색인의 ROCm 런타임 |
| Intel | 이미지 `-openvino` + Linux `/dev/dri`, Windows Docker Desktop `--device /dev/dxg -v /usr/lib/wsl:/usr/lib/wsl` | `udally-link[openvino]` |
| 없음 / macOS | 이미지 `-cpu` | 기본 |

직접 명령(고급, `<버전>`·`<커밋>`은 릴리스 노트 참고):

```bash
# uv
uv tool install --force --python 3.12 "udally-link[cuda] @ git+https://github.com/Seokyoung-Hong/udally-link@<커밋>"
udally-link connect            # 연결 후 계속 실행(Ctrl+C로 끔). --until-linked: 연결만 하고 끝냄
udally-link unlink             # 스스로 끊기(토큰 폐기)
# Docker
docker run -d --name udally-link --restart unless-stopped -v udally-link:/cache ghcr.io/seokyoung-hong/udally-link:<버전>-cpu connect
docker logs -f udally-link     # 기기 인증 주소·코드
# compose
docker compose -f compose.yaml -f compose.link.yaml up -d   # GPU면 compose.cuda.yaml 등을 함께
```

- 유달리 주소는 `UDALLY_URL`(기본 `https://udally.sio2.kr`). 토큰 파일은 `LINK_FILE`(Docker `/cache/udally-link.json`, Windows `%APPDATA%\udally-link\link.json`, 그 밖 `~/.config/udally-link/link.json`, POSIX 0600). 로그에는 토큰 앞 12자만 남깁니다.
- 동작: 5초마다 heartbeat로 모델 목록을 보내고 [모델 적용]·작업을 받습니다. 작업은 트랙·녹음을 내려받아 PC 안에서 구간을 잘라 전사하고 구간별 결과만 올립니다(이름 목록은 받지 않음). 받은 오디오는 작업이 끝나면 지웁니다.
- 끊김: 네트워크 오류·5xx는 2·4·8…60초 백오프, 401(유달리에서 끊음)은 토큰 파일을 지우고 종료 코드 3, 426(업데이트 필요)은 4.
- 연결 모드는 포트를 열지 않습니다. Docker HEALTHCHECK는 마지막 heartbeat 성공 시각(2분 안)으로 봅니다.
- AI 자동완성 거쳐 보내기: 유달리 설정의 개인 AI 주소가 PC 안이면 유달리가 heartbeat로 메시지를 보내고, 이 PC가 그 주소의 `/chat/completions`를 불러 결과만 올립니다(전사와 따로, 한 번에 하나). 주소 예: Ollama `http://localhost:11434/v1`, LM Studio `http://localhost:1234/v1`, vLLM `http://localhost:8000/v1`.
  - 부를 수 있는 주소는 루프백(`localhost`·`127.0.0.1`·`::1`)과 `host.docker.internal`뿐입니다. 같은 LAN의 다른 기기는 `UDALLY_AI_HOSTS=192.168.0.5,gpu.lan`처럼 직접 적은 호스트만 허용합니다. 리다이렉트는 따르지 않고 프록시를 쓰지 않습니다.
  - Docker 안에서는 `localhost`·`127.0.0.1`을 `host.docker.internal`로 바꿔 부릅니다(`compose.link.yaml`의 `extra_hosts`). `docker run`이면 `--add-host host.docker.internal:host-gateway`를 붙여 주세요.
  - 키·메시지·결과는 로그에 남기지 않습니다.
- **Codex(내 구독)**: 유달리 설정에서 개인 AI 종류를 "Codex(내 구독, 내 PC)"로 고르면, 이 PC에 설치·로그인된 [Codex CLI](https://github.com/openai/codex)를 비대화로 실행합니다(`codex exec --sandbox read-only --skip-git-repo-check --ephemeral --ignore-user-config --json`, 승인 없음, 빈 임시 폴더, 마지막 메시지만 결과). 명령 실행·웹 검색·브라우저·플러그인 등 도구 기능은 끄고(`--disable`), 그래도 도구 호출 사건이 보이면 바로 프로세스를 끄고 결과를 버립니다. Codex에는 실행·로그인 위치·프록시에 필요한 환경변수만 넘깁니다. 구독 토큰은 읽지도 유달리로 보내지도 않습니다. 시간 상한·취소 때 프로세스를 끕니다. Codex가 없으면 `codex_missing`, 로그인이 안 됐으면 `codex_auth`로 실패해 다음 AI로 넘어갑니다. 실행 파일 위치는 `LINK_CODEX_BIN`, 쓸 Codex 계정 폴더는 `LINK_CODEX_HOME`(지정하면 udally-link만 그 계정을 쓰고 평소 codex 기본 계정은 그대로)으로 바꿀 수 있습니다. 쓰는 만큼 내 Codex 사용량이 줄어듭니다. 명령 실행·웹 검색·브라우저·플러그인 등 기능은 허용 목록 방식으로 끕니다(`codex features list`의 기능 중 `link.py` `CODEX_KEEP`(로그인 저장·전송·응답 형식 6개) 밖은 전부 `--disable` — 새 버전에 새로 생긴 기능도 자동으로 꺼짐, 기능 목록을 못 읽으면 Codex 작업을 받지 않음). 어떤 Codex 버전이 실제로 도구 없이 도는지는 `scripts/codex-matrix/run.py`(Docker, 버전당 Codex 1회 사용)로 미리 시험하고, 통과한 버전 목록을 유달리가 heartbeat 응답(`codex_versions`)으로 내려 줍니다. 이 PC의 Codex 버전이 목록에 정확히 있을 때만 Codex 작업을 받습니다(목록 밖이면 로그 한 줄). Codex를 업데이트했는데 목록에 없으면 유달리가 그 버전을 시험해 목록에 넣을 때까지 Codex 작업을 받지 않습니다. **Docker로 설치한 udally-link에서는 Codex를 쓸 수 없습니다** — 컨테이너에 Codex CLI·로그인이 없으므로 Codex를 쓰려면 uv로 설치하세요(`UDALLY_FORCE=uv`).

### 옛 이름(udally-stt)에서 옮기기

- **설치 스크립트를 다시 실행하면 됩니다.** 옛 컨테이너 `udally-stt`·uv 도구 `udally-stt`·시작프로그램 `udally-stt.lnk`·`systemd --user` `udally-stt.service`·LaunchAgent `kr.sio2.udally-stt`가 있으면 지우고 새 이름으로 다시 만듭니다.
- 연결 토큰은 그대로 씁니다(다시 연결하지 않음). Docker는 옛 볼륨 `udally-stt`가 있고 새 볼륨 `udally-link`가 없으면 옛 볼륨을 그대로 이어 씁니다. uv는 `%APPDATA%\udally-stt\link.json`·`~/.config/udally-stt/link.json`을 새 위치로 옮깁니다.
- 환경변수는 새 이름 `LINK_*`(토큰 파일은 `LINK_FILE`)가 우선이고, 옛 `STT_*`(`STT_LINK_FILE`)도 계속 읽습니다. `.env`를 고치지 않아도 됩니다.
- compose는 프로젝트 이름을 `udally-link`로 고정해 모델 볼륨이 `udally-link_models`가 됩니다. 옛 `udally-stt_models`(받은 모델)를 이어 쓰려면 한 번 복사합니다:

```bash
docker compose -p udally-stt down        # 옛 compose 컨테이너를 멈춤(볼륨은 남음)
docker volume create udally-link_models
docker run --rm -v udally-stt_models:/from:ro -v udally-link_models:/to alpine cp -a /from/. /to/
docker compose up -d --build             # 확인 뒤 옛 볼륨 삭제: docker volume rm udally-stt_models
```

## 1. 띄우기

### Docker(권장)

```bash
cp .env.example .env          # LINK_API_KEY 등 값 고치기
docker compose up -d --build                                   # CPU
docker compose -f compose.yaml -f compose.cuda.yaml up -d --build   # NVIDIA GPU
docker compose -f compose.yaml -f compose.openvino.yaml up -d --build   # Intel GPU(Linux)
docker compose -f compose.yaml -f compose.rocm.yaml up -d --build   # AMD GPU(Linux, 실험)
docker compose -f compose.yaml -f compose.cuda.yaml -f compose.nemotron.yaml up -d --build   # GPU + NVIDIA Nemotron(로컬 빌드 전용)
```

- 변형은 Dockerfile `VARIANT`(cpu · cuda · nemotron · openvino · rocm) 하나로 고릅니다. 기반은 `python:3.12-slim`입니다.
- 이미지 크기(이 PC 실측, 2026-10-13): cpu 650MB(이전 PyPI PyAV 824MB) · cuda 4.4GB(이전 4.6GB) · openvino 1.75GB(Intel GPU compute runtime 포함) · rocm 0.9GB(ROCm 런타임 제외, 첫 실행 때 받음) · nemotron 11.2GB(이전 12.4GB, torch 2.11.0+cu128·triton 제거).
- PyAV는 PyPI 휠 대신 **GPL 부품 없는 디코드 전용 FFmpeg 8.0.3(LGPL-2.1+)**로 소스 빌드합니다(10절). 이 FFmpeg는 유달리 업로드 형식(mp3·m4a·wav·ogg·opus·webm)과 Discord 트랙(ogg/opus)의 오디오 디코더만 담습니다. `test_decode.py`가 이미지 안에서 6형식과 영상이 섞인 webm·m4a 디코드, libx264·libx265 부재를 확인합니다.
- Nemotron 프리셋은 PyTorch와 Transformers가 있을 때만 목록에 나타납니다. **nemotron 이미지는 공개 배포하지 않습니다(로컬 빌드 전용)**: torch가 링크하는 NVIDIA NVSHMEM의 재배포 근거가 불명확해서입니다. torch는 `2.11.0+cu128`로 고정합니다(전에는 PyPI와 섞어 고르다 PyPI의 cu130 빌드가 들어왔습니다).
- NVIDIA GPU는 NVIDIA 드라이버와 NVIDIA Container Toolkit(Windows는 Docker Desktop WSL2)이 필요합니다.
- 받은 모델과 CUDA 첫 실행 캐시는 `models` 볼륨(`/cache`)에 남습니다. 컨테이너를 지워도 다시 받지 않습니다.
- **기동할 때 모델을 받지 않습니다.** 유달리 설정 화면의 [적용]이 받기를 시작합니다(4절).

### uv(한 줄)

```bash
LINK_API_KEY=... LINK_HOST=0.0.0.0 LINK_PORT=8000 uv run server.py
```

- 의존성은 `server.py` 머리(PEP 723)와 `pyproject.toml`(같은 목록, `test_plan.py`가 검사)에 있어 uv가 알아서 설치합니다. 모델 캐시는 `HF_HOME`(기본 `~/.cache/huggingface`)입니다.
- uv(네이티브) 경로에서는 사용자 PC가 PyPI의 PyAV 휠을 직접 받습니다. 이 휠의 FFmpeg는 libx264·libx265(GPL, 인코더 전용 — 전사에는 쓰지 않음)를 링크합니다. 이 저장소는 그 바이너리를 배포하지 않으며, 사용자 PC에서 사용자가 직접 설치·조합하는 것입니다. 우리가 배포하는 바이너리(이미지)에는 GPL 바이너리를 넣지 않습니다.
- uv 실행에서 GPU를 쓰려면 CUDA 12용 cuBLAS와 cuDNN 9가 시스템에 있어야 합니다([faster-whisper 요구사항](https://github.com/SYSTRAN/faster-whisper#gpu)).
  없으면 첫 적재에서 실패를 감지하고 CPU로 바꿉니다(이 PC에서 `cublas64_12.dll` 없음 → CPU int8 전환 확인).

### 환경변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `LINK_API_KEY` | (없음) | 정하면 모든 경로에 `Authorization: Bearer <키>`가 필요합니다. 비우면 무인증입니다. |
| `LINK_MODELS` | 아래 프리셋 5개(Nemotron은 해당 이미지에서만) | `/v1/models`에 보일 모델 ID(쉼표 구분). 여기에 없는 ID는 404입니다. |
| `LINK_DEVICE` | `auto` | `auto`(NVIDIA > AMD > Intel > CPU) · `cuda` · `rocm` · `openvino` · `cpu` |
| `LINK_GPUS` | 전부 | 쓸 GPU 번호(nvidia-smi·HIP·OpenVINO `GPU.n` 번호). 예 `0,1`. `CUDA_VISIBLE_DEVICES`와 함께 쓰지 마세요(번호가 어긋납니다). |
| `LINK_CPU_FALLBACK` | `1` | GPU 메모리가 모자라거나 GPU를 쓸 수 없을 때 CPU로 내립니다. `0`이면 거절합니다. |
| `LINK_COMPUTE_TYPE` | 자동 | 정하면 정밀도를 고정합니다(자동 강등 안 함). |
| `LINK_CONCURRENCY` | `3` | 동시 처리 수. 유달리는 3요청을 동시에 보냅니다. |
| `LINK_HOST` / `LINK_PORT` | `127.0.0.1` / `8000` | uv 실행 때 여는 주소. Docker는 `LINK_BIND` / `LINK_PORT`(호스트 쪽)를 씁니다. |
| `UDALLY_URL` | `https://udally.sio2.kr` | 연결 모드가 부를 유달리 주소 |
| `UDALLY_AI_HOSTS` | (비움) | AI 거쳐 보내기로 부를 수 있는 LAN 호스트(쉼표). 루프백·`host.docker.internal`은 늘 허용 |
| `LINK_FILE` | 0절 | 연결 토큰 파일 위치 |
| `LINK_NAME` | 컴퓨터 이름 | 연결할 때 보낼 기기 이름(50자, 유달리 확인 화면에서 고칠 수 있음) |
| `LINK_ROCM_VERSION` | `7.13.0` | AMD(Linux) ROCm 런타임 버전. rocm 이미지는 첫 실행 때 `/cache/rocm-<버전>`에 AMD 공식 색인(`repo.amd.com/rocm/whl/<GPU 계열>/`)에서 받습니다. |

## 2. 모델 프리셋

| 모델 ID(`stt_model` 값) | 표시 이름 | 파일 | 권장 장비 | 필요 메모리 추정(동시 3) GPU float16 / GPU int8_float16 / CPU int8 | 속도 근거 |
|---|---|---|---|---|---|
| `Systran/faster-whisper-small` | OpenAI Whisper small | 0.49GB | **CPU 기본**, 약한 GPU | 1.3GB / 1.0GB / 1.5GB | 원본 대비 약 4배 빠름¹. 이 PC CPU(uv)에서 6.3초 음성 1.2~2.5초 |
| `Systran/faster-whisper-medium` | OpenAI Whisper medium | 1.53GB | 여유 있는 CPU, 4GB GPU | 2.6GB / 1.7GB / 2.2GB | 약 2배¹ |
| `deepdml/faster-whisper-large-v3-turbo-ct2` | OpenAI Whisper large-v3-turbo | 1.62GB | **GPU 기본** | 2.7GB / 1.7GB / 2.2GB | 약 8배¹. 이 PC GPU 2장에서 6.3초 음성 3동시 0.4~0.8초 |
| `Systran/faster-whisper-large-v3` | OpenAI Whisper large-v3 | 3.09GB | 6GB 이상 GPU(최고 품질) | 4.4GB / 2.6GB / 3.1GB | 1배(기준)¹ |
| `nvidia/nemotron-3.5-asr-streaming-0.6b` | NVIDIA Nemotron 3.5 ASR 0.6B | 2.55GB(float32) | NVIDIA GPU, **`udally-link:nemotron` 이미지에서만 보임** | 2.1GB(GPU당, 실측 약 1.4GB) / (없음) / CPU float32 3.9GB | 이 PC GPU 2장(float16)에서 6.3초 음성 단건 0.4~0.7초, 3동시 0.7~1.0초(rtf 0.16~0.23). 첫 적재 약 14초 |

### 모델 라이선스

모델 가중치는 이 저장소·이미지에 들어 있지 않고, 사용자가 [모델 적용]으로 Hugging Face에서 직접 받습니다. 각 모델의 원 라이선스를 따릅니다(2026-10-12 확인, 모두 상업 사용 가능 — 이 저장소 코드의 비상업 라이선스와 별개).

| 모델 ID | 가중치(원본) | 변환본 | 상업 사용 |
|---|---|---|---|
| `Systran/faster-whisper-small` | MIT — [openai/whisper README "License"](https://github.com/openai/whisper#license)("code and model weights are released under the MIT License"). HF [openai/whisper-small](https://huggingface.co/openai/whisper-small) 표기는 Apache-2.0 | MIT — [HF 카드](https://huggingface.co/Systran/faster-whisper-small) | 가능 |
| `Systran/faster-whisper-medium` | 위와 같음([openai/whisper-medium](https://huggingface.co/openai/whisper-medium) 표기 Apache-2.0) | MIT — [HF 카드](https://huggingface.co/Systran/faster-whisper-medium) | 가능 |
| `deepdml/faster-whisper-large-v3-turbo-ct2` | MIT — [openai/whisper-large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) | MIT — [HF 카드](https://huggingface.co/deepdml/faster-whisper-large-v3-turbo-ct2) | 가능 |
| `Systran/faster-whisper-large-v3` | 위 MIT([openai/whisper-large-v3](https://huggingface.co/openai/whisper-large-v3) 표기 Apache-2.0) | MIT — [HF 카드](https://huggingface.co/Systran/faster-whisper-large-v3) | 가능 |
| `nvidia/nemotron-3.5-asr-streaming-0.6b` | [OpenMDW-1.1](https://openmdw.ai/license/1-1/) — "deal in the Model Materials without restriction", 배포 시 라이선스 사본·고지 유지, 특허 소송 시 종료. 출력물에는 제한 없음 | (원본 그대로 사용) | 가능 |

- 원본 Whisper는 GitHub(MIT)과 HF(Apache-2.0) 표기가 다르지만 둘 다 상업 사용을 허용합니다.
- 실행 라이브러리: faster-whisper·CTranslate2 MIT, PyTorch BSD-3, Transformers Apache-2.0, OpenVINO Apache-2.0.
- `deepdml/faster-whisper-large-v3-turbo-ct2`는 원본 `openai/whisper-large-v3-turbo`(MIT)의 변환본입니다. Intel용 OpenVINO 공식 변환본은 `OpenVINO/whisper-{small,medium,large-v3}-*-ov` Apache-2.0, `OpenVINO/whisper-large-v3-turbo-*-ov` MIT입니다(HF API).

- ¹ [openai/whisper README "Available models"](https://github.com/openai/whisper#available-models-and-languages)의 상대 속도(원본 PyTorch 기준. 같은 표의 VRAM 1~10GB는 PyTorch fp32 수치라 이 서버보다 큽니다).
- 필요 메모리 추정식(`server.py`의 `need_mb`): `가중치 × 1.25 + 150MB × 동시 수 + 300MB(CPU는 800MB)`. 가중치는 model.bin 크기(float16 저장), int8 계열은 그 절반입니다. 근거:
  - 이 PC 실측(RTX 5060 Ti): large-v3-turbo float16 적재 +2.0GB, 3동시 전사 +0.35GB / int8_float16 적재 +1.05GB, 3동시 +0.22GB. `num_workers=3`이어도 가중치는 한 벌만 올라갑니다(1과 3이 같은 2.0GB).
  - [faster-whisper 벤치마크](https://github.com/SYSTRAN/faster-whisper#benchmark): large-v2 GPU fp16 4,525MB · int8 2,926MB, small CPU int8 1,477MB.
- 모든 프리셋은 CTranslate2 변환본이고 원본 저장소는 `base_model`(예 `openai/whisper-large-v3-turbo`)에 나옵니다. 라이선스: Systran 변환본·원본 Whisper MIT, deepdml 변환본 MIT(HF 카드 기준).
- 한국어 품질 수치(모델별 한국어 WER/CER)는 공개 출처에서 확인하지 못했습니다(확인 불가). 이 PC의 한국어 합성 음성 문장 시험(저장소에 두지 않음)에서는 small·large-v3-turbo 모두 문장을 정확히 옮겼습니다(turbo는 "다음주"로 붙여 씀).

### Nemotron 3.5 ASR 메모

- 실행 경로는 모델 카드의 [Transformers "Offline transcription"](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b) 예시(`AutoModelForRNNT`, `transformers>=5.13`)입니다. NIM·NeMo 없이 파일 전사가 됩니다.
- `language=ko`를 그대로 넘깁니다(언어를 안 주면 `auto`). `prompt`(용어집)는 넣을 자리가 없어 무시합니다. VAD 없이 구간 전체를 한 번에 넣습니다.
- `generate()`가 호출마다 모델 객체에 상태를 써서 동시에 부르면 `AttributeError`가 납니다(실측). 그래서 GPU당 한 번에 하나씩 처리하고, GPU가 여러 개면 GPU 사이에서 나눕니다.
- 정밀도는 GPU float16, CPU float32뿐입니다(int8 단계 없음). CPU 전사 속도는 재지 않았습니다.
- INT4 변환본 [onnx-community/...-onnx-int4](https://huggingface.co/onnx-community/nemotron-3.5-asr-streaming-0.6b-onnx-int4)는 라이선스 표기가 불명확하고(MIT와 NVIDIA Open Model License가 함께 적혀 있고 원본 OpenMDW 승계 표기가 없음) 실행 경로가 onnxruntime-genai뿐이라 넣지 않았습니다.

## 3. 장비별 동작

모델을 올릴 때(첫 전사 또는 [적용] 직후) GPU별 여유 VRAM(NVIDIA `nvidia-smi`, AMD HIP `hipMemGetInfo` 또는 Linux sysfs, Intel OpenVINO `GPU_DEVICE_TOTAL_MEM_SIZE`)과 `psutil` 여유 RAM을 읽어 배치를 정합니다.

| 실행부(`backends.py`) | device 값 | 정밀도 강등 | 비고 |
|---|---|---|---|
| CT2 · NVIDIA | `cuda:0,1` | float16 → int8_float16 → int8 → CPU int8 | 이 PC 실측 |
| CT2 · AMD ROCm | `rocm:0` | 같음 | **실험**. CTranslate2 해제·종료 멈춤 버그(#2038·#2085) 때문에 모델을 바꿀 때 내리지 않고 프로세스를 다시 띄웁니다. 대상 GPU: gfx1030·1100~1102·1150·1151·1200·1201. 실기 시험 없음 |
| OpenVINO · Intel | `openvino:GPU.0` | float16 → int8 → int4 → OpenVINO CPU | 정밀도마다 공식 변환본 저장소가 따로라(`OpenVINO/whisper-*-{fp16,int8,int4}-ov`) [적용] 때 장비에 맞는 정밀도를 받고, 받은 정밀도 안에서 낮춥니다. 모델 id·표시 이름은 그대로, 실제 저장소는 `source`. 30초 이하 구간은 faster-whisper VAD로 만듭니다. 외장 GPU 여유는 전체의 90%, 내장 GPU는 RAM 여유로 봅니다(OpenVINO가 남은 VRAM을 주지 않음). GPU 실기 시험 없음(CPU 장치로 경로 시험) |
| CT2 · CPU | `cpu` | int8 | |
| Torch · Nemotron | `cuda:0` | float16 → CPU float32 | NVIDIA 전용 |

| 경우 | 동작 | 이 PC 실측 |
|---|---|---|
| ① GPU 여러 개 | 필요 메모리가 들어가는 GPU마다 모델을 하나씩 올리고, 진행 중 요청이 가장 적은 GPU로 보냅니다(GPU당 동시 수 = ⌈3/GPU 수⌉). | RTX 5060 Ti + RTX 3060: `cuda:0,1` float16, 3동시 4묶음 모두 200 |
| ② GPU 1개·넉넉 | float16 | `LINK_GPUS=1`: `cuda:1` float16 |
| ③ GPU 1개·부족 | float16 → int8_float16 → int8 순으로 낮추고, 그래도 안 되면 CPU 폴백(`LINK_CPU_FALLBACK=0`이면 `fits:false`로 [적용] 거절) | 3060 여유 2.3GB로 만든 뒤: turbo `int8_float16`, large-v3 `cpu int8`(폴백 끄면 `fits:false`, POST 409) |
| ④ GPU 없음 | CPU int8, 여유 RAM보다 크면 `fits:false` | CPU 컨테이너: 모든 프리셋 `cpu int8`, 3동시 200 |

- **여러 GPU를 CTranslate2 한 모델로 묶지 않은 이유**: `device_index=[0,1]`은 계산 능력이 다른 GPU에서 `Cannot use multiple GPUs with different Compute Capabilities`로 실패했습니다(이 PC 실측). 그래서 GPU마다 모델을 따로 올리고, GPU마다 전용 스레드를 둡니다. 한 스레드가 두 GPU 모델을 번갈아 부르면 `CUDA failed with error invalid argument`가 났습니다(실측).
- **OOM**: 적재 중 OOM은 프로세스가 죽지 않고 `RuntimeError`로 잡히는 것을 확인했습니다(3060에 turbo 7번째 적재에서 OOM, 이후 같은 프로세스에서 전사 정상). 적재 중 OOM이면 한 단계 낮춰 바로 다시 올리고, 전사 중 OOM이면 그 요청만 503으로 실패시키고 다음 요청은 낮은 정밀도로 다시 올립니다. 전사 중 OOM은 재현하지 못했습니다(여유 57MB에서도 CTranslate2가 미리 잡아 둔 메모리로 정상 처리, 확인 불가). CPU에서 RAM이 바닥나면 운영체제가 프로세스를 죽일 수 있어 막지 못합니다.
- GPU 실행이 OOM이 아닌 이유(라이브러리 누락 등)로 실패하면 이후 CPU만 씁니다(`LINK_CPU_FALLBACK=1`일 때).
- 한 번에 한 모델만 메모리에 둡니다. 다른 모델로 바꾸면 이전 모델을 내리고 새로 올립니다.

## 4. API 규약

모든 경로는 `LINK_API_KEY`를 정했으면 `Authorization: Bearer <키>`가 필요합니다(틀리면 401).

### `GET /v1/models` — 프리셋 전체(받지 않은 것 포함)

```json
{"object": "list", "data": [
  {"id": "deepdml/faster-whisper-large-v3-turbo-ct2", "object": "model",
   "owned_by": "openai", "family": "Whisper", "display_name": "OpenAI Whisper large-v3-turbo",
   "base_model": "openai/whisper-large-v3-turbo", "source": "deepdml/faster-whisper-large-v3-turbo-ct2", "format": "CTranslate2",
   "status": "ready", "fits": true, "device": "cuda:0,1", "compute_type": "float16", "rtf": 0.123},
  {"id": "Systran/faster-whisper-medium", "object": "model", "owned_by": "openai", "family": "Whisper",
   "display_name": "OpenAI Whisper medium", "base_model": "openai/whisper-medium", "source": "Systran/faster-whisper-medium", "format": "CTranslate2",
   "status": "downloading", "progress": 0.42, "fits": true, "device": "cuda:0,1", "compute_type": "float16"}
]}
```

| 필드 | 뜻 |
|---|---|
| `id` | 전사 요청의 `model` 값(프리셋 id). CT2 실행부는 이 저장소를 받고, OpenVINO 실행부는 `source`의 변환본을 받습니다 |
| `owned_by` / `family` / `display_name` / `base_model` / `source` / `format` | 사람이 읽는 계열 정보. `owned_by`는 원본 제작자(Whisper는 `openai`), `source`는 실제로 받는 변환본 저장소 |
| `status` | `ready` · `downloading` · `not_downloaded` |
| `progress` | `downloading`일 때 0~0.99. 파일 단위로 갱신되어 몇 초씩 멈췄다 뛸 수 있습니다. 전체 크기를 아직 모르면 없습니다. |
| `error` | `not_downloaded`이고 마지막 받기가 실패했을 때 이유 |
| `fits` | 이 장비에서 돌 수 있는지. 크기를 아직 모르면(기동 직후 수 초) `null` |
| `device` / `compute_type` | 올렸거나 올릴 배치. 예 `"cuda:0,1"` · `"rocm:0"` · `"openvino:GPU.0"` · `"cpu"`, `"float16"` · `"int8_float16"` · `"int8"` · `"int4"`(OpenVINO) |
| `rtf` | `ready`일 때만. 이 장비 실측 처리시간 ÷ 오디오 길이의 이동평균(최근 값 30%). 실측 전이나 다시 올린 뒤에는 `null`. 동시 요청 중이면 커집니다. |

`GET /v1/models/{id}`는 같은 항목 하나를 돌려줍니다.

### `POST /v1/models/{id}` — 받기 시작([적용])

`{id}`는 `%2F`로 인코딩해도(유달리 방식, 확인) `/`를 그대로 둬도 됩니다. 응답은 5초 안에 옵니다(받기는 백그라운드).

| 상황 | 코드 | 본문 |
|---|---|---|
| 받기 시작 또는 이미 받는 중 | 202 | 위 항목(`status: "downloading"`) |
| 이미 받음 | 200 | 위 항목(`status: "ready"`). 이 모델을 백그라운드로 미리 올립니다. |
| 이 장비에서 못 돎 | 409 | 위 항목(`fits: false`) + `"detail"` |
| 프리셋에 없음 | 404 | `{"detail": "프리셋에 없는 모델입니다: ..."}` |

받기가 끝나면 그 모델을 바로 미리 올려 첫 전사 요청이 적재 시간을 치르지 않게 합니다.

### `POST /v1/audio/transcriptions` — 전사

multipart: `file`(webm/Opus, wav 등 PyAV가 읽는 형식), `model`, `language`(예 `ko`), `prompt`(선택, 용어집. 빈 값은 없음으로 봄), `response_format`(`json` | `text`).

| 상황 | 코드 | 본문 |
|---|---|---|
| 성공 | 200 | `{"text": "..."}` (무음·톤만이면 `{"text": ""}`) |
| 받지 않음 / 받는 중 | 409 | `{"detail": "모델이 준비되지 않았습니다(받지 않음): <id>. POST /v1/models/{id}로 받은 뒤 다시 요청해 주세요."}` |
| 프리셋에 없음 | 404 | `{"detail": "프리셋에 없는 모델입니다: <id>"}` |
| `response_format`이 json·text 아님 | 400 | `{"detail": "response_format은 json 또는 text만 지원합니다."}` |
| 메모리가 모자라 올리지 못함 / 전사 중 OOM | 503 | `{"detail": "..."}` |
| 키 틀림 | 401 | `{"detail": "API 키가 맞지 않습니다..."}` |

- `prompt`는 항상 받으므로 "prompt" 문구가 든 400은 내지 않습니다.
- 받지 않은 모델은 **자동으로 받지 않습니다.** 409는 유달리가 재시도하지 않고 실패로 기록합니다(유달리 `transcribe.mjs`는 5xx만 재시도·엔진 전환 대상으로 봅니다).
- 503(메모리)은 유달리가 10초 뒤 한 번 다시 보내므로, 강등된 정밀도로 성공할 수 있습니다.

### 받기 방식 판단: "[적용]이 받기" vs "첫 요청이 받기 + 503"

[적용]이 받는 방식을 택했습니다. 첫 전사 요청이 받기를 시작하고 503을 주는 방식은 (1) 유달리가 503을 연결 실패로 보고 10초 뒤 재시도, 두 번 실패하면 남은 구간을 OpenAI로 넘기므로 1.6~3GB 받기 동안 회의 하나가 통째로 외부 엔진이나 실패로 가고, (2) 전사 타임아웃(`meeting.stt_timeout_s` 기본 120초, 구간별 `min(120, 30+3×초)`) 안에 받기가 끝나지 않습니다. 관리자가 설정 화면에서 받기를 끝내 두면 회의 중에는 이런 일이 없습니다.

### 취소

클라이언트가 연결을 끊으면 서버는 0.5초마다 확인해 **다음 구간 경계에서** 전사를 멈춥니다. faster-whisper는 30초 창 단위로 결과를 내므로, 창 하나를 처리하는 도중에는 멈추지 못합니다.
- 유달리가 보내는 짧은 구간(대부분 30초 미만)은 창이 하나라 끊어도 끝까지 처리됩니다. GPU에서는 1초 안팎이라 영향이 작습니다.
- 실측(이 PC, CPU small, 124초 음성, 2초 뒤 끊음): 55초 뒤 첫 창을 마치고 중단 로그를 남겼습니다. 같은 문장을 반복한 시험 음성이라 온도 폴백 재시도로 창 처리가 유난히 길었습니다.
- 동시 처리 칸이 다 찬 상태에서 끊긴 요청은 차례가 오면 바로 버립니다.

## 5. 유달리 설정 값

| 설정 | 값 |
|---|---|
| `meeting.stt_url` | `http://<이 서버 IP>:<포트>` (예 `http://192.0.2.10:8000`). 끝에 `/v1`을 붙이지 않습니다. 루프백·내부 이름은 유달리가 막습니다. |
| `meeting.stt_model` | 설정 화면 목록에서 고릅니다(위 표의 모델 ID). |
| `meeting.stt_api_key` | `.env`의 `LINK_API_KEY`와 같은 값 |

## 6. 유달리 LXC 밖에 둘 때(네트워크·키)

- 기본 바인드는 `127.0.0.1`이라 같은 PC에서만 열립니다. 다른 머신에서 부르려면 `LINK_BIND=0.0.0.0`(uv는 `LINK_HOST=0.0.0.0`)으로 열고 **반드시 `LINK_API_KEY`를 정합니다.** 키 없이 열면 같은 네트워크의 누구나 전사·모델 받기를 할 수 있습니다.
- 평문 HTTP입니다. 회의 음성과 키가 그대로 지나가므로 같은 사설망(집·사무실 LAN, VPN) 안에서만 쓰고, 인터넷에 직접 열지 마세요. 밖에서 써야 하면 TLS 리버스 프록시나 VPN을 앞에 둡니다.
- 방화벽은 유달리 서버(디스코드 게이트웨이와 웹이 도는 곳)의 IP에서 오는 포트만 허용하는 것이 좋습니다.
- Windows는 Hyper-V 예약 포트 범위가 있어 일부 포트를 열 수 없습니다(이 PC에서 18431 거부). 다른 포트를 고르세요.

## 7. 시험

```bash
uv run --no-project --python 3.12 --with fastapi,psutil,python-multipart,numpy python test_plan.py   # 배치 계산·장치 판별(가짜 장치)·의존성 목록 일치
uv run --no-project --with "faster-whisper>=1.1,<2" --with "av<17" --with fastapi,psutil,python-multipart python test_link.py   # 연결(가짜 core)
docker run --rm -v ./samples:/app/samples:ro -v ./test_decode.py:/app/test_decode.py:ro --entrypoint python udally-link:cpu test_decode.py   # 이미지 디코드·GPL 부재
sh -n scripts/install.sh && UDALLY_DRY_RUN=1 UDALLY_FORCE=uv UDALLY_SOURCE=. sh scripts/install.sh   # 설치 스크립트 드라이런
curl -H "Authorization: Bearer $KEY" http://127.0.0.1:8000/v1/models
curl -H "Authorization: Bearer $KEY" -X POST http://127.0.0.1:8000/v1/models/Systran%2Ffaster-whisper-small
curl -H "Authorization: Bearer $KEY" -F file=@samples/tone.webm -F model=Systran/faster-whisper-small -F language=ko \
     http://127.0.0.1:8000/v1/audio/transcriptions
```

`samples/`는 합성 440Hz 톤(2초)을 6형식(mp3·m4a·wav·ogg·opus·webm)과 영상이 섞인 webm·m4a로 만든 것입니다. 사람 목소리·TTS 음성은 저장소에 두지 않습니다(한국어 전사 품질 시험은 각자 녹음으로).

## 8. 검토했으나 제외한 것

| 대상 | 판단 | 근거 |
|---|---|---|
| [speaches](https://github.com/speaches-ai/speaches)(옛 faster-whisper-server, MIT) | 전사·`prompt`·Bearer 키는 맞지만 제외 | `GET /v1/models`가 **받은 모델만** 돌려주고 상태 필드가 없음, `POST /v1/models/{id}`가 받기가 끝날 때까지 응답을 막음(유달리 5초 제한), 장비 적합·속도 정보 없음. `ghcr.io/speaches-ai/speaches:latest-cpu`(압축 0.85GB)를 실제로 띄워 보니 `PRELOAD_MODELS`가 동작하지 않았고(이미지가 master보다 오래됨) 키를 정하면 `/health`도 403이었습니다. 필요한 부분을 앞단에서 메우면 경로 대부분을 다시 짜야 해서, 같은 엔진(faster-whisper)을 직접 쓰는 편이 짧았습니다. |
| NVIDIA [Nemotron 3.5 ASR Streaming 0.6B](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)를 NIM·NeMo로 실행 | **프리셋에는 Transformers 경로로 넣었고, NIM·NeMo 실행만 제외** | 40개 언어, ko-KR "Transcription-ready", FLEURS 한국어 CER 7.12%(1.12초 청크), 라이선스 OpenMDW-1.1, 600M. 다만 (1) 공식 실행은 NeMo `main` 브랜치·Linux·NVIDIA GPU 전제이고 최소 VRAM은 카드에 없음(확인 불가), (2) [Speech NIM](https://docs.nvidia.com/nim/speech/latest/asr/deploy-asr-models/nemotron-asr-streaming.html)은 스트리밍 전용(오프라인 HTTP 전사 미지원)이고 NGC 키가 필요, [지원표](https://docs.nvidia.com/nim/speech/latest/reference/support-matrix/asr.html)상 계산 능력 8.0 이상·16GB VRAM, (3) speaches의 NeMo 실행기(onnx-asr)는 `istupakov/parakeet-tdt` 계열만 받고 [onnx-asr](https://github.com/istupakov/onnx-asr)도 Nemotron을 지원 목록에 두지 않음. 별도 NeMo 백엔드를 만들고 검증하려면 PyTorch+NeMo 의존성 수 GB를 받아야 해 승인 없이 진행하지 않았습니다. |
| NVIDIA [Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3), [Canary 1B v2](https://huggingface.co/nvidia/canary-1b-v2), [Canary 1B Flash](https://huggingface.co/nvidia/canary-1b-flash) | 제외 | HF 카드 언어 목록에 한국어 없음(유럽 언어 위주), CC-BY-4.0 |
| 한국어 미세조정 Whisper | 제외 | Hugging Face에서 CTranslate2 형식 한국어 미세조정본은 주소 도메인 모델 1개(내려받기 7회)뿐이라 신뢰할 근거가 없음 |
| [whisper.cpp server](https://github.com/ggerganov/whisper.cpp) | 조사하지 않음 | faster-whisper로 요구가 충족되어 더 보지 않았습니다. |

## 9. 알려진 한계

- 여유 RAM은 컨테이너 메모리 제한이 아니라 호스트(또는 WSL VM) 기준입니다. `mem_limit`을 걸었다면 `fits`가 실제보다 낙관적일 수 있습니다.
- 크기 정보는 기동 직후 Hugging Face API로 한 번 읽습니다. 오프라인이면 받지 않은 모델의 `fits`가 `null`로 남습니다.
- 첫 GPU 적재는 CUDA 첫 실행 컴파일 때문에 오래 걸릴 수 있습니다(이 PC RTX 5060 Ti: 최대 약 30초, 캐시가 생긴 뒤 5~16초). 그래서 [적용]·받기 완료 때 미리 올립니다. 시험 중 첫 기동 1회에서 전사 요청이 응답 없이 끊긴 일이 있었으나 재현되지 않았습니다(원인 확인 불가).
- Windows에서 uv로 띄우고 출력을 파일로 돌리면 로그가 CP949로 저장됩니다.

## 10. 라이선스

**이 저장소의 코드·스크립트·문서는 [PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0)입니다(`LICENSE`).** 소스는 공개하지만 상업적 사용은 허용하지 않습니다. OSI 승인 오픈소스 라이선스가 아니며, GitHub 저장소 정보에는 "Other"로 보입니다. SPDX: `PolyForm-Noncommercial-1.0.0`.

| 쓸 수 있음(비상업 목적) | 쓸 수 없음(별도 허락 필요) |
|---|---|
| 개인의 연구·학습·취미·개인 용도("without any anticipated commercial application") | 영리 회사·단체의 업무용(수익 유무 무관, 사내 사용 포함) |
| 자선 단체, 교육 기관, 공공 연구 기관, 공공 안전·보건 기관, 환경 보호 단체, 정부 기관(재원과 무관) | 이 서버를 사업용 서비스(SaaS 등)의 백엔드로 쓰기 |
| 위 목적 안에서 수정·재배포(라이선스 사본과 `Required Notice` 줄 유지) | 위 목적 밖의 재배포 |

- 상업 이용 문의: 이 저장소의 GitHub 이슈로 남겨 주세요.
- 저작권자(Seokyoung-Hong)가 운영하는 유달리 서비스의 사용은 저작권자 본인의 사용이라 이 라이선스의 대상이 아닙니다.
- **외부 기여(PR)는 받지 않습니다.** 기여분의 라이선스가 저작권자의 다른 사용과 충돌하지 않게 하려는 것입니다(CLA를 두지 않음). 버그·제안은 이슈로 남겨 주세요.
- 이 라이선스는 이 저장소 저작물에만 적용됩니다. 의존성·NVIDIA 구성요소·Intel 구성요소·LGPL 구성요소·모델 가중치는 각자의 라이선스를 따르며, PolyForm Noncommercial이 그 권리를 제한하지 않습니다. 전체 목록과 고지는 `THIRD_PARTY_NOTICES`, 라이선스 전문은 `licenses/`, 이미지별 패키지 목록은 이미지 안 `/app/licenses/PACKAGES.txt`입니다.
- 이미지의 FFmpeg는 GPL 부품(`--enable-gpl`, libx264·libx265 등) 없이 직접 빌드한 LGPL-2.1+ 공유 라이브러리이고, 그 소스(`/opt/ffmpeg/src`)를 이미지에 함께 둡니다. LGPL 구성요소의 수정·재링크·디버깅 목적 역공학은 이 저장소 라이선스로 제한하지 않습니다.
- NVIDIA 라이브러리(cuBLAS·cuDNN 등)는 NVIDIA 약관에 따라 이 애플리케이션과 함께 쓰도록만 들어 있습니다. 이미지에서 꺼내 다른 용도로 쓰거나 재배포하지 마세요.
- AMD ROCm 런타임은 이미지에 넣지 않고 사용자 PC가 AMD 공식 색인에서 직접 받습니다.
- Intel OpenVINO 이미지에는 `openvino-telemetry` 패키지가 함께 설치될 수 있습니다(OpenVINO의 사용 통계 수집 모듈, 동의 기반). 이 서버는 이를 켜거나 부르지 않습니다.
