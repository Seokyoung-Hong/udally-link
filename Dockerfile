FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app
COPY server.py .
# 의존성은 server.py 머리(PEP 723) 한 곳에만 적는다. GPU=1이면 CUDA 12 cuBLAS·cuDNN 9를 함께 넣는다.
ARG GPU=0
# server.py가 바뀌면 이 단계가 다시 돌지만 uv 캐시 마운트 덕에 내려받기는 다시 하지 않는다.
RUN --mount=type=cache,target=/root/.cache/uv uv export --script server.py --no-hashes -o /tmp/req.txt \
 && if [ "$GPU" = 1 ]; then echo "nvidia-cublas-cu12" >> /tmp/req.txt && echo "nvidia-cudnn-cu12==9.*" >> /tmp/req.txt; fi \
 && uv pip install --system --link-mode=copy -r /tmp/req.txt && rm /tmp/req.txt \
 && useradd -m app && mkdir /cache && chown app /cache
# pip로 넣은 CUDA 라이브러리 위치(GPU=0이면 없는 경로라 무해)
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib \
    HF_HOME=/cache CUDA_CACHE_PATH=/cache/cuda-jit STT_HOST=0.0.0.0 STT_PORT=8000 PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,os;urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/v1/models',headers={'Authorization':'Bearer '+os.environ.get('STT_API_KEY','')}),timeout=4)"
CMD ["python", "server.py"]
