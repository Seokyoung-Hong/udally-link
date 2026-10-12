# udally-stt

유달리(Discord 회의 녹음)가 부르는 **로컬 음성 전사 서버**입니다. 조직이 자기 PC·서버에 띄우면 회의 음성이 외부로 나가지 않습니다.
OpenAI 호환 `POST /v1/audio/transcriptions`에 더해, 유달리 설정 화면이 쓰는 **모델 목록·받기·장비 적합 정보**를 제공합니다.

- 전사 엔진: [faster-whisper](https://github.com/SYSTRAN/faster-whisper)(CTranslate2, MIT). 디코딩·VAD·정밀도·GPU 병렬은 이 라이브러리를 그대로 씁니다.
- 모델 받기·캐시: [huggingface_hub](https://github.com/huggingface/huggingface_hub).
- 이 저장소가 직접 짠 것: `server.py` 하나(HTTP 규약, 모델 상태·받기, 장비 감지·메모리 계획, OOM 강등, 취소, 속도 집계).

## 1. 띄우기

### Docker(권장)

```bash
cp .env.example .env          # STT_API_KEY 등 값 고치기
docker compose up -d --build                                   # CPU
docker compose -f compose.yaml -f compose.cuda.yaml up -d --build   # NVIDIA GPU
```

- 이미지 크기(이 PC 실측): CPU 849MB, CUDA 4.6GB(cuBLAS·cuDNN 포함). 기반은 `python:3.12-slim`입니다.
- GPU는 NVIDIA 드라이버와 NVIDIA Container Toolkit(Windows는 Docker Desktop WSL2)이 필요합니다.
- 받은 모델과 CUDA 첫 실행 캐시는 `models` 볼륨(`/cache`)에 남습니다. 컨테이너를 지워도 다시 받지 않습니다.
- **기동할 때 모델을 받지 않습니다.** 유달리 설정 화면의 [적용]이 받기를 시작합니다(4절).

### uv(한 줄)

```bash
STT_API_KEY=... STT_HOST=0.0.0.0 STT_PORT=8000 uv run server.py
```

- 의존성은 `server.py` 머리(PEP 723)에 있어 uv가 알아서 설치합니다. 모델 캐시는 `HF_HOME`(기본 `~/.cache/huggingface`)입니다.
- uv 실행에서 GPU를 쓰려면 CUDA 12용 cuBLAS와 cuDNN 9가 시스템에 있어야 합니다([faster-whisper 요구사항](https://github.com/SYSTRAN/faster-whisper#gpu)).
  없으면 첫 적재에서 실패를 감지하고 CPU로 바꿉니다(이 PC에서 `cublas64_12.dll` 없음 → CPU int8 전환 확인).

### 환경변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `STT_API_KEY` | (없음) | 정하면 모든 경로에 `Authorization: Bearer <키>`가 필요합니다. 비우면 무인증입니다. |
| `STT_MODELS` | 아래 프리셋 4개 | `/v1/models`에 보일 모델 ID(쉼표 구분). 여기에 없는 ID는 404입니다. |
| `STT_DEVICE` | `auto` | `auto`(GPU가 있으면 GPU) · `cuda` · `cpu` |
| `STT_GPUS` | 전부 | 쓸 GPU 번호(nvidia-smi 번호). 예 `0,1`. `CUDA_VISIBLE_DEVICES`와 함께 쓰지 마세요(번호가 어긋납니다). |
| `STT_CPU_FALLBACK` | `1` | GPU 메모리가 모자라거나 GPU를 쓸 수 없을 때 CPU로 내립니다. `0`이면 거절합니다. |
| `STT_COMPUTE_TYPE` | 자동 | 정하면 정밀도를 고정합니다(자동 강등 안 함). |
| `STT_CONCURRENCY` | `3` | 동시 처리 수. 유달리는 3요청을 동시에 보냅니다. |
| `STT_HOST` / `STT_PORT` | `127.0.0.1` / `8000` | uv 실행 때 여는 주소. Docker는 `STT_BIND` / `STT_PORT`(호스트 쪽)를 씁니다. |

## 2. 모델 프리셋

| 모델 ID(`stt_model` 값) | 표시 이름 | 파일 | 권장 장비 | 필요 메모리 추정(동시 3) GPU float16 / GPU int8_float16 / CPU int8 | 속도 근거 |
|---|---|---|---|---|---|
| `Systran/faster-whisper-small` | OpenAI Whisper small | 0.49GB | **CPU 기본**, 약한 GPU | 1.3GB / 1.0GB / 1.5GB | 원본 대비 약 4배 빠름¹. 이 PC CPU(uv)에서 6.3초 음성 1.2~2.5초 |
| `Systran/faster-whisper-medium` | OpenAI Whisper medium | 1.53GB | 여유 있는 CPU, 4GB GPU | 2.6GB / 1.7GB / 2.2GB | 약 2배¹ |
| `deepdml/faster-whisper-large-v3-turbo-ct2` | OpenAI Whisper large-v3-turbo | 1.62GB | **GPU 기본** | 2.7GB / 1.7GB / 2.2GB | 약 8배¹. 이 PC GPU 2장에서 6.3초 음성 3동시 0.4~0.8초 |
| `Systran/faster-whisper-large-v3` | OpenAI Whisper large-v3 | 3.09GB | 6GB 이상 GPU(최고 품질) | 4.4GB / 2.6GB / 3.1GB | 1배(기준)¹ |
| `nvidia/nemotron-3.5-asr-streaming-0.6b` | NVIDIA Nemotron 3.5 ASR 0.6B | 2.55GB(float32) | NVIDIA GPU, **`udally-stt:nemotron` 이미지에서만 보임** | 2.4GB / (없음) / CPU float32 3.9GB | NEMOTRON_RESULT |

### 라이선스(상업적 사용)

모두 상업적 사용이 가능한 것만 프리셋에 넣었습니다(2026-10-12 확인). 배포할 때는 각 라이선스 고지를 함께 둡니다.

| 모델 ID | 가중치(원본) | 변환본 | 상업 사용 |
|---|---|---|---|
| `Systran/faster-whisper-small` | MIT — [openai/whisper README "License"](https://github.com/openai/whisper#license)("code and model weights are released under the MIT License"). HF [openai/whisper-small](https://huggingface.co/openai/whisper-small) 표기는 Apache-2.0 | MIT — [HF 카드](https://huggingface.co/Systran/faster-whisper-small) | 가능 |
| `Systran/faster-whisper-medium` | 위와 같음([openai/whisper-medium](https://huggingface.co/openai/whisper-medium) 표기 Apache-2.0) | MIT — [HF 카드](https://huggingface.co/Systran/faster-whisper-medium) | 가능 |
| `deepdml/faster-whisper-large-v3-turbo-ct2` | MIT — [openai/whisper-large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) | MIT — [HF 카드](https://huggingface.co/deepdml/faster-whisper-large-v3-turbo-ct2) | 가능 |
| `Systran/faster-whisper-large-v3` | 위 MIT([openai/whisper-large-v3](https://huggingface.co/openai/whisper-large-v3) 표기 Apache-2.0) | MIT — [HF 카드](https://huggingface.co/Systran/faster-whisper-large-v3) | 가능 |
| `nvidia/nemotron-3.5-asr-streaming-0.6b` | [OpenMDW-1.1](https://openmdw.ai/license/1-1/) — "deal in the Model Materials without restriction", 배포 시 라이선스 사본·고지 유지, 특허 소송 시 종료. 출력물에는 제한 없음 | (원본 그대로 사용) | 가능 |

- 원본 Whisper는 GitHub(MIT)과 HF(Apache-2.0) 표기가 다르지만 둘 다 상업 사용을 허용합니다.
- 실행 라이브러리: faster-whisper·CTranslate2 MIT, PyTorch BSD-3, Transformers Apache-2.0.

- ¹ [openai/whisper README "Available models"](https://github.com/openai/whisper#available-models-and-languages)의 상대 속도(원본 PyTorch 기준. 같은 표의 VRAM 1~10GB는 PyTorch fp32 수치라 이 서버보다 큽니다).
- 필요 메모리 추정식(`server.py`의 `need_mb`): `가중치 × 1.25 + 150MB × 동시 수 + 300MB(CPU는 800MB)`. 가중치는 model.bin 크기(float16 저장), int8 계열은 그 절반입니다. 근거:
  - 이 PC 실측(RTX 5060 Ti): large-v3-turbo float16 적재 +2.0GB, 3동시 전사 +0.35GB / int8_float16 적재 +1.05GB, 3동시 +0.22GB. `num_workers=3`이어도 가중치는 한 벌만 올라갑니다(1과 3이 같은 2.0GB).
  - [faster-whisper 벤치마크](https://github.com/SYSTRAN/faster-whisper#benchmark): large-v2 GPU fp16 4,525MB · int8 2,926MB, small CPU int8 1,477MB.
- 모든 프리셋은 CTranslate2 변환본이고 원본 저장소는 `base_model`(예 `openai/whisper-large-v3-turbo`)에 나옵니다. 라이선스: Systran 변환본·원본 Whisper MIT, deepdml 변환본 MIT(HF 카드 기준).
- 한국어 품질 수치(모델별 한국어 WER/CER)는 공개 출처에서 확인하지 못했습니다(확인 불가). 이 PC의 한국어 TTS 문장(Windows Heami 음성) 시험에서는 small·large-v3-turbo 모두 문장을 정확히 옮겼습니다(turbo는 "다음주"로 붙여 씀).

## 3. 장비별 동작

모델을 올릴 때(첫 전사 또는 [적용] 직후) `nvidia-smi`로 GPU별 여유 VRAM, `psutil`로 여유 RAM을 읽어 배치를 정합니다.

| 경우 | 동작 | 이 PC 실측 |
|---|---|---|
| ① GPU 여러 개 | 필요 메모리가 들어가는 GPU마다 모델을 하나씩 올리고, 진행 중 요청이 가장 적은 GPU로 보냅니다(GPU당 동시 수 = ⌈3/GPU 수⌉). | RTX 5060 Ti + RTX 3060: `cuda:0,1` float16, 3동시 4묶음 모두 200 |
| ② GPU 1개·넉넉 | float16 | `STT_GPUS=1`: `cuda:1` float16 |
| ③ GPU 1개·부족 | float16 → int8_float16 → int8 순으로 낮추고, 그래도 안 되면 CPU 폴백(`STT_CPU_FALLBACK=0`이면 `fits:false`로 [적용] 거절) | 3060 여유 2.3GB로 만든 뒤: turbo `int8_float16`, large-v3 `cpu int8`(폴백 끄면 `fits:false`, POST 409) |
| ④ GPU 없음 | CPU int8, 여유 RAM보다 크면 `fits:false` | CPU 컨테이너: 모든 프리셋 `cpu int8`, 3동시 200 |

- **여러 GPU를 CTranslate2 한 모델로 묶지 않은 이유**: `device_index=[0,1]`은 계산 능력이 다른 GPU에서 `Cannot use multiple GPUs with different Compute Capabilities`로 실패했습니다(이 PC 실측). 그래서 GPU마다 모델을 따로 올리고, GPU마다 전용 스레드를 둡니다. 한 스레드가 두 GPU 모델을 번갈아 부르면 `CUDA failed with error invalid argument`가 났습니다(실측).
- **OOM**: 적재 중 OOM은 프로세스가 죽지 않고 `RuntimeError`로 잡히는 것을 확인했습니다(3060에 turbo 7번째 적재에서 OOM, 이후 같은 프로세스에서 전사 정상). 적재 중 OOM이면 한 단계 낮춰 바로 다시 올리고, 전사 중 OOM이면 그 요청만 503으로 실패시키고 다음 요청은 낮은 정밀도로 다시 올립니다. 전사 중 OOM은 재현하지 못했습니다(여유 57MB에서도 CTranslate2가 미리 잡아 둔 메모리로 정상 처리, 확인 불가). CPU에서 RAM이 바닥나면 운영체제가 프로세스를 죽일 수 있어 막지 못합니다.
- GPU 실행이 OOM이 아닌 이유(라이브러리 누락 등)로 실패하면 이후 CPU만 씁니다(`STT_CPU_FALLBACK=1`일 때).
- 한 번에 한 모델만 메모리에 둡니다. 다른 모델로 바꾸면 이전 모델을 내리고 새로 올립니다.

## 4. API 규약

모든 경로는 `STT_API_KEY`를 정했으면 `Authorization: Bearer <키>`가 필요합니다(틀리면 401).

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
| `id` | 전사 요청의 `model` 값이자 받을 Hugging Face 저장소 |
| `owned_by` / `family` / `display_name` / `base_model` / `source` / `format` | 사람이 읽는 계열 정보. `owned_by`는 원본 제작자(Whisper는 `openai`), `source`는 실제로 받는 변환본 저장소 |
| `status` | `ready` · `downloading` · `not_downloaded` |
| `progress` | `downloading`일 때 0~0.99. 파일 단위로 갱신되어 몇 초씩 멈췄다 뛸 수 있습니다. 전체 크기를 아직 모르면 없습니다. |
| `error` | `not_downloaded`이고 마지막 받기가 실패했을 때 이유 |
| `fits` | 이 장비에서 돌 수 있는지. 크기를 아직 모르면(기동 직후 수 초) `null` |
| `device` / `compute_type` | 올렸거나 올릴 배치. 예 `"cuda:0,1"` · `"cuda:1"` · `"cpu"`, `"float16"` · `"int8_float16"` · `"int8"` |
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
| `meeting.stt_api_key` | `.env`의 `STT_API_KEY`와 같은 값 |

## 6. 유달리 LXC 밖에 둘 때(네트워크·키)

- 기본 바인드는 `127.0.0.1`이라 같은 PC에서만 열립니다. 다른 머신에서 부르려면 `STT_BIND=0.0.0.0`(uv는 `STT_HOST=0.0.0.0`)으로 열고 **반드시 `STT_API_KEY`를 정합니다.** 키 없이 열면 같은 네트워크의 누구나 전사·모델 받기를 할 수 있습니다.
- 평문 HTTP입니다. 회의 음성과 키가 그대로 지나가므로 같은 사설망(집·사무실 LAN, VPN) 안에서만 쓰고, 인터넷에 직접 열지 마세요. 밖에서 써야 하면 TLS 리버스 프록시나 VPN을 앞에 둡니다.
- 방화벽은 유달리 서버(디스코드 게이트웨이와 웹이 도는 곳)의 IP에서 오는 포트만 허용하는 것이 좋습니다.
- Windows는 Hyper-V 예약 포트 범위가 있어 일부 포트를 열 수 없습니다(이 PC에서 18431 거부). 다른 포트를 고르세요.

## 7. 시험

```bash
uv run --no-project --with fastapi,psutil,python-multipart,numpy python test_plan.py   # 장비 4경우 배치 계산
curl -H "Authorization: Bearer $KEY" http://127.0.0.1:8000/v1/models
curl -H "Authorization: Bearer $KEY" -X POST http://127.0.0.1:8000/v1/models/Systran%2Ffaster-whisper-small
curl -H "Authorization: Bearer $KEY" -F file=@samples/ko.webm -F model=Systran/faster-whisper-small -F language=ko \
     http://127.0.0.1:8000/v1/audio/transcriptions
```

`samples/`는 Windows 한국어 TTS로 만든 문장(webm/Opus 48kHz, wav 16kHz mono)과 2초 440Hz 톤입니다. 공개 데이터셋은 쓰지 않았습니다.

## 8. 검토했으나 제외한 것

| 대상 | 판단 | 근거 |
|---|---|---|
| [speaches](https://github.com/speaches-ai/speaches)(옛 faster-whisper-server, MIT) | 전사·`prompt`·Bearer 키는 맞지만 제외 | `GET /v1/models`가 **받은 모델만** 돌려주고 상태 필드가 없음, `POST /v1/models/{id}`가 받기가 끝날 때까지 응답을 막음(유달리 5초 제한), 장비 적합·속도 정보 없음. `ghcr.io/speaches-ai/speaches:latest-cpu`(압축 0.85GB)를 실제로 띄워 보니 `PRELOAD_MODELS`가 동작하지 않았고(이미지가 master보다 오래됨) 키를 정하면 `/health`도 403이었습니다. 필요한 부분을 앞단에서 메우면 경로 대부분을 다시 짜야 해서, 같은 엔진(faster-whisper)을 직접 쓰는 편이 짧았습니다. |
| NVIDIA [Nemotron 3.5 ASR Streaming 0.6B](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b) | **한국어 지원·라이선스 문제없음, 이번 프리셋에서는 제외(후속 후보)** | 40개 언어, ko-KR "Transcription-ready", FLEURS 한국어 CER 7.12%(1.12초 청크), 라이선스 OpenMDW-1.1, 600M. 다만 (1) 공식 실행은 NeMo `main` 브랜치·Linux·NVIDIA GPU 전제이고 최소 VRAM은 카드에 없음(확인 불가), (2) [Speech NIM](https://docs.nvidia.com/nim/speech/latest/asr/deploy-asr-models/nemotron-asr-streaming.html)은 스트리밍 전용(오프라인 HTTP 전사 미지원)이고 NGC 키가 필요, [지원표](https://docs.nvidia.com/nim/speech/latest/reference/support-matrix/asr.html)상 계산 능력 8.0 이상·16GB VRAM, (3) speaches의 NeMo 실행기(onnx-asr)는 `istupakov/parakeet-tdt` 계열만 받고 [onnx-asr](https://github.com/istupakov/onnx-asr)도 Nemotron을 지원 목록에 두지 않음. 별도 NeMo 백엔드를 만들고 검증하려면 PyTorch+NeMo 의존성 수 GB를 받아야 해 승인 없이 진행하지 않았습니다. |
| NVIDIA [Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3), [Canary 1B v2](https://huggingface.co/nvidia/canary-1b-v2), [Canary 1B Flash](https://huggingface.co/nvidia/canary-1b-flash) | 제외 | HF 카드 언어 목록에 한국어 없음(유럽 언어 위주), CC-BY-4.0 |
| 한국어 미세조정 Whisper | 제외 | Hugging Face에서 CTranslate2 형식 한국어 미세조정본은 주소 도메인 모델 1개(내려받기 7회)뿐이라 신뢰할 근거가 없음 |
| [whisper.cpp server](https://github.com/ggerganov/whisper.cpp) | 조사하지 않음 | faster-whisper로 요구가 충족되어 더 보지 않았습니다. |

## 9. 알려진 한계

- 여유 RAM은 컨테이너 메모리 제한이 아니라 호스트(또는 WSL VM) 기준입니다. `mem_limit`을 걸었다면 `fits`가 실제보다 낙관적일 수 있습니다.
- 크기 정보는 기동 직후 Hugging Face API로 한 번 읽습니다. 오프라인이면 받지 않은 모델의 `fits`가 `null`로 남습니다.
- 첫 GPU 적재는 CUDA 첫 실행 컴파일 때문에 오래 걸릴 수 있습니다(이 PC RTX 5060 Ti: 최대 약 30초, 캐시가 생긴 뒤 5~16초). 그래서 [적용]·받기 완료 때 미리 올립니다. 시험 중 첫 기동 1회에서 전사 요청이 응답 없이 끊긴 일이 있었으나 재현되지 않았습니다(원인 확인 불가).
- Windows에서 uv로 띄우고 출력을 파일로 돌리면 로그가 CP949로 저장됩니다.
