# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""이미지 안 디코드·라이선스 검사. 실행(이미지): docker run --rm -v ./samples:/app/samples:ro --entrypoint python <이미지> test_decode.py
유달리 업로드 허용 6형식(mp3·m4a·wav·ogg·opus·webm)과 영상이 섞인 webm·m4a를 faster-whisper decode_audio로 읽고,
FFmpeg가 LGPL 구성(GPL·nonfree·x264·x265 없음)인지, 어떤 .so도 libx264·libx265를 갖지 않는지 본다."""

import ctypes
import glob
import os
import re
import sys

try:
    from faster_whisper.audio import decode_audio
except ImportError:  # rocm 이미지: ROCm 런타임(첫 실행 때 받음) 없이는 ctranslate2를 못 읽는다 → 같은 audio.py만 읽는다
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "fw_audio", os.path.join(os.path.dirname(importlib.util.find_spec("faster_whisper").origin), "audio.py"))
    fw_audio = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fw_audio)
    decode_audio = fw_audio.decode_audio

for f in sorted(glob.glob("samples/tone*.*")):
    a = decode_audio(f, sampling_rate=16000)
    assert abs(len(a) / 16000 - 2) < 0.06 and a.std() > 0.05, (f, len(a), a.std())
    print("decode ok", f, round(len(a) / 16000, 3))
assert len(glob.glob("samples/tone*.*")) >= 6

lib = ctypes.CDLL(glob.glob("/opt/ffmpeg/lib/libavcodec.so.*")[0])
lib.avcodec_license.restype = lib.avcodec_configuration.restype = ctypes.c_char_p
lic, conf = lib.avcodec_license().decode(), lib.avcodec_configuration().decode()
bad = [k for k in ("--enable-gpl", "--enable-nonfree", "--enable-version3", "--enable-libx264", "--enable-libx265") if k in conf]
assert lic.startswith("LGPL version 2.1") and not bad, (lic, bad)
print("ffmpeg", lic)

site = next(p for p in sys.path if p.endswith("site-packages"))
hits = []
for root in (site, "/opt/ffmpeg/lib", "/usr/lib", "/usr/local/lib"):
    for dp, _, fs in os.walk(root):
        for n in fs:
            p = os.path.join(dp, n)
            if ".so" in n and not os.path.islink(p):
                if re.search(r"x26[45]", n) or re.search(rb"libx26[45]\.so", open(p, "rb").read()):
                    hits.append(p)
assert not hits, hits
print("ok")
