# syntax=docker/dockerfile:1
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# 변형(VARIANT): cpu(기본) · cuda(NVIDIA) · nemotron(NVIDIA + Nemotron) · openvino(Intel) · rocm(AMD, 실험)
# PyAV는 PyPI 휠 대신 GPL 부품 없는(LGPL, 디코드 전용) FFmpeg로 소스 빌드한다. PyPI 휠의 FFmpeg는 libx264·libx265(GPL)를 링크한다.
ARG PY=python:3.12-slim

# ---------- 1. FFmpeg(LGPL-2.1+, 디코드 전용) + 의존성 설치 ----------
FROM ${PY} AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential pkg-config curl xz-utils ca-certificates unzip \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
ARG FFMPEG=8.0.3
ARG FFMPEG_SHA256=6136812ea6d4e68bdba27e33c2a94382711cdf4f8602ffef056ff792bd6f9818
# 유달리 업로드 허용 형식(mp3·m4a·wav·ogg·opus·webm)과 Discord 트랙(ogg/opus)의 오디오 디코더·분리기만 넣는다.
# --enable-gpl·--enable-nonfree·외부 코덱 라이브러리는 쓰지 않는다(--disable-autodetect).
RUN curl -fsSLo /tmp/ffmpeg.tar.xz https://ffmpeg.org/releases/ffmpeg-${FFMPEG}.tar.xz \
 && echo "${FFMPEG_SHA256}  /tmp/ffmpeg.tar.xz" | sha256sum -c - \
 && mkdir -p /opt/ffmpeg/src && cp /tmp/ffmpeg.tar.xz /opt/ffmpeg/src/ \
 && tar xJf /tmp/ffmpeg.tar.xz -C /tmp && cd /tmp/ffmpeg-${FFMPEG} \
 && ./configure --prefix=/opt/ffmpeg --enable-shared --disable-static --disable-programs --disable-doc --disable-network \
      --disable-autodetect --disable-x86asm --disable-everything \
      --enable-decoder=aac,aac_latm,alac,mp3,mp3float,opus,vorbis,flac,pcm_s16le,pcm_s16be,pcm_s24le,pcm_s32le,pcm_f32le,pcm_f64le,pcm_u8,pcm_alaw,pcm_mulaw,adpcm_ms,adpcm_ima_wav \
      --enable-demuxer=mov,mp3,wav,ogg,matroska,flac,aac \
      --enable-parser=aac,mpegaudio,opus,vorbis,flac \
      --enable-protocol=file,pipe \
      --enable-filter=abuffer,abuffersink,aformat,aresample,anull,asetnsamples \
 | tee /opt/ffmpeg/src/configure.log | grep -E '^License: LGPL' \
 && make -j"$(nproc)" install && rm -rf /tmp/ffmpeg* /opt/ffmpeg/share \
 && echo /opt/ffmpeg/lib > /etc/ld.so.conf.d/ffmpeg.conf && ldconfig
WORKDIR /src
COPY pyproject.toml .
ARG VARIANT=cpu
# nemotron에서 빼는 것: triton(ptxas 등 NVIDIA 개발 도구 동봉 — 재배포 근거 없음, eager 실행이라 불필요).
# NCCL·cuFile·NVSHMEM은 libtorch_cuda.so가 NEEDED로 가져 빼면 import torch가 실패한다(실측) → 남기고,
# nemotron 이미지는 공개 배포하지 않는다(로컬 빌드 전용, README·THIRD_PARTY_NOTICES).
ARG NEMOTRON_DROP="triton"
# AMD: CTranslate2 ROCm 휠(MIT, GitHub 릴리스 zip, PyPI 없음). ROCm 런타임은 넣지 않는다(첫 실행 때 사용자 PC가 AMD 색인에서 받는다).
ARG CT2_ROCM_ZIP=https://github.com/OpenNMT/CTranslate2/releases/download/v4.8.2/rocm-python-wheels-Linux.zip
ARG CT2_ROCM_SHA256=b469e765f74ef85fb97bf4fe2347c8c6296dda00d7f5cf20658f23e95004a925
# PKG_CONFIG_PATH: av를 위 FFmpeg로 빌드. nemotron은 torch를 cu128 색인에서 먼저 고정한다
# (PyPI와 섞어 unsafe-best-match로 고르면 PyPI의 더 높은 버전 = cu130 빌드가 들어온다: 2.14.1+cu130 실측).
RUN --mount=type=cache,target=/root/.cache/uv export PKG_CONFIG_PATH=/opt/ffmpeg/lib/pkgconfig \
 && case "$VARIANT" in cpu) X="";; cuda) X="--extra cuda";; nemotron) X="--extra cuda --extra nemotron";; \
      openvino) X="--extra openvino";; rocm) X="";; *) echo "알 수 없는 VARIANT $VARIANT"; exit 1;; esac \
 && if [ "$VARIANT" = nemotron ]; then uv pip install --system --index-url https://download.pytorch.org/whl/cu128 "torch==2.11.0"; fi \
 && if [ "$VARIANT" = rocm ]; then curl -fsSLo /tmp/ct2.zip "$CT2_ROCM_ZIP" && echo "$CT2_ROCM_SHA256  /tmp/ct2.zip" | sha256sum -c - \
      && unzip -q /tmp/ct2.zip -d /tmp/ct2 && W=$(find /tmp/ct2 -name 'ctranslate2-*-cp312-*manylinux*x86_64.whl') \
      && echo "ctranslate2 @ file://$W" > /tmp/over.txt; else : > /tmp/over.txt; fi \
 && uv pip install --system --no-binary av --override /tmp/over.txt $X -r pyproject.toml \
 && if [ "$VARIANT" = nemotron ]; then uv pip uninstall --system $NEMOTRON_DROP; fi \
 && rm -rf /tmp/ct2*
# Intel GPU(openvino): OpenVINO GPU 플러그인이 쓰는 Intel compute runtime(MIT). Debian 13(trixie)에는 intel-opencl-icd가 없어
# Intel 공식 GitHub 릴리스 .deb를 sha256으로 고정해 받는다(compute-runtime 26.35.39758.10, IGC 2.41.5, gmmlib 22.10.0).
ARG INTEL_DEBS="\
https://github.com/intel/intel-graphics-compiler/releases/download/v2.41.5/intel-igc-core-2_2.41.5+22716_amd64.deb 0a6e64a663ae65a0fa02d6912ae3b6b37cf85b90c21cc423fd9fef70aaf4f628 \
https://github.com/intel/intel-graphics-compiler/releases/download/v2.41.5/intel-igc-opencl-2_2.41.5+22716_amd64.deb 779e1b9e88098eb25711e9a8f67c2752665bad22f134aa40ed5649f6e1b87058 \
https://github.com/intel/compute-runtime/releases/download/26.35.39758.10/intel-opencl-icd_26.35.39758.10-0_amd64.deb 61712caaddeba3d38e4f79e2a0fb23fea25596ca2d72c3144c6eea2331ec4301 \
https://github.com/intel/compute-runtime/releases/download/26.35.39758.10/libigdgmm12_22.10.0_amd64.deb 6031a63d6e8a12ce61c14efc15f2c8e727061286e3820b8594e6d00615e04d54"
RUN mkdir /debs && if [ "$VARIANT" = openvino ]; then set -- $INTEL_DEBS; while [ $# -gt 0 ]; do \
      f="/debs/$(basename "$1")"; curl -fsSLo "$f" "$1" && echo "$2  $f" | sha256sum -c - || exit 1; shift 2; done; fi

# ---------- 2. 실행 이미지 ----------
FROM ${PY}
COPY --from=build /opt/ffmpeg/lib /opt/ffmpeg/lib
COPY --from=build /opt/ffmpeg/src /opt/ffmpeg/src
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /debs /tmp/debs
ARG VARIANT=cpu
# openvino: OpenCL ICD 로더(Debian ocl-icd-libopencl1, BSD-2-Clause) + 위 Intel compute runtime
RUN if [ "$VARIANT" = openvino ]; then apt-get update && apt-get install -y --no-install-recommends ocl-icd-libopencl1 /tmp/debs/*.deb \
      && rm -rf /var/lib/apt/lists/*; fi && rm -rf /tmp/debs \
 && echo /opt/ffmpeg/lib > /etc/ld.so.conf.d/ffmpeg.conf && ldconfig \
 && useradd -m app && mkdir /cache && chown app /cache
WORKDIR /app
COPY server.py link.py backends.py LICENSE THIRD_PARTY_NOTICES ./
COPY licenses ./licenses
# 이 이미지에 실제로 든 패키지·네이티브 라이브러리 목록(GPL 패키지가 있으면 빌드 실패)
COPY scripts/third_party.py /tmp/third_party.py
RUN python /tmp/third_party.py > /app/licenses/PACKAGES.txt && rm /tmp/third_party.py
ARG REVISION=""
LABEL org.opencontainers.image.title="udally-stt"       org.opencontainers.image.description="유달리용 로컬 전사 서버(OpenAI 호환 + 내 PC 연결). 비상업 라이선스."       org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0"       org.opencontainers.image.source="https://github.com/Seokyoung-Hong/udally-stt"       org.opencontainers.image.url="https://github.com/Seokyoung-Hong/udally-stt"       org.opencontainers.image.documentation="https://github.com/Seokyoung-Hong/udally-stt#readme"       org.opencontainers.image.vendor="Seokyoung-Hong"       org.opencontainers.image.version="0.1.0-${VARIANT}"       org.opencontainers.image.revision="${REVISION}"
# pip로 넣은 CUDA 라이브러리 위치(cuda 아니면 없는 경로라 무해)
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib \
    HF_HOME=/cache CUDA_CACHE_PATH=/cache/cuda-jit STT_LINK_FILE=/cache/udally-link.json STT_HOST=0.0.0.0 STT_PORT=8000 \
    PYTHONUNBUFFERED=1 STT_ROCM_INSTALL=1 STT_ALIVE_FILE=/tmp/udally-alive
USER app
EXPOSE 8000
# connect 모드는 포트를 열지 않는다: 마지막 heartbeat 성공 시각(STT_ALIVE_FILE)이 2분 안이면 정상. HTTP 모드는 /v1/models.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import os,sys,time,urllib.request as u;f=os.environ['STT_ALIVE_FILE'];sys.exit(time.time()-os.path.getmtime(f)>120) if os.path.exists(f) else u.urlopen(u.Request('http://127.0.0.1:8000/v1/models',headers={'Authorization':'Bearer '+os.environ.get('STT_API_KEY','')}),timeout=4)"
ENTRYPOINT ["python", "server.py"]
