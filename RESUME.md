# 재개 지점 (2026-10-12, PC 재부팅으로 중단)

## 한 것
- `server.py`에 Nemotron 백엔드를 넣었습니다(`class Nemotron`: 모델 카드의 Transformers "Offline transcription" 예시를 감쌈). 메타 정보는 owned_by `nvidia`, family `Nemotron`, display_name "NVIDIA Nemotron 3.5 ASR 0.6B"이고, 받는 파일은 `NEMOTRON_FILES`(model.safetensors 2.55GB, float32)입니다. GPU는 float16만, CPU는 float32로 돕니다.
  - torch와 transformers가 설치된 경우에만 프리셋에 나타납니다(`HAS_TORCH`).
- Dockerfile에 `ARG NEMOTRON`을 넣고 `compose.nemotron.yaml`(이미지 `udally-stt:nemotron`)을 만들었습니다. torch(cu128 인덱스)와 `transformers>=5.13`을 더하며, 기본 이미지에는 영향이 없습니다.
- `test_plan.py`는 통과했습니다.
- README에 라이선스 표와 Nemotron 행을 넣었습니다. 속도 칸은 아직 `NEMOTRON_RESULT` 자리표시입니다.
- 모델 볼륨 `udally-stt-test_models`에 Nemotron과 small을 미리 받아 두었습니다.

## 남은 일
1. 이미지를 빌드합니다: `docker compose -p udally-stt-test -f compose.yaml -f compose.cuda.yaml -f compose.nemotron.yaml build`
   - 빌드 도중 중단되어 아직 한 번도 성공하지 않았습니다. 이전 빌드는 Dockerfile 패치가 실패한 상태라 무효입니다.
2. 기동한 뒤 `/v1/models`에 Nemotron이 보이는지, `samples/ko.webm`을 한국어로 전사(`language=ko`)할 수 있는지 GPU에서 확인합니다.
   - 확인할 점: processor가 `ko`를 받는지, `dtype=` 인자가 맞는지, 3동시 스레드에서 안전한지
3. README에서 `NEMOTRON_RESULT`를 실측값으로 채우고, 필요 메모리 추정과 이미지 크기를 적습니다.
4. 이 파일을 지우고 정식 커밋합니다.

## 라이선스 확인 결과(확인된 것만)
- 원본 Whisper 가중치: MIT(github.com/openai/whisper README "License"). HF openai/whisper-small·medium·large-v3는 Apache-2.0, large-v3-turbo는 MIT로 표기되어 있습니다. 모두 상업 사용이 가능합니다.
- 변환본 Systran/faster-whisper-small·medium·large-v3와 deepdml/faster-whisper-large-v3-turbo-ct2: HF 카드에 MIT로 표기되어 있습니다. 상업 사용이 가능합니다.
- nvidia/nemotron-3.5-asr-streaming-0.6b: OpenMDW-1.1(https://openmdw.ai/license/1-1/)로, "without restriction"이며 고지 유지와 특허 소송 시 종료 조건이 있습니다. 상업 사용이 가능합니다.
- onnx-community/nemotron-3.5-asr-streaming-0.6b-onnx-int4: **제외합니다.**
  - 카드에 `license: mit`와 `license_name: nvidia-open-model-license`가 함께 적혀 있어 서로 맞지 않습니다. 원본 라이선스(OpenMDW)도 승계 표기되지 않았고 LICENSE 파일이 없습니다.
  - 실행 경로가 onnxruntime-genai뿐이고, onnx-asr README에는 Nemotron이 없습니다.
