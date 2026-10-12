"""이미지 안 Python 패키지·동봉 네이티브 라이브러리 목록(THIRD_PARTY_NOTICES 부록). Docker 빌드가 /app/licenses/PACKAGES.txt로 만든다.
GPL(LGPL·예외 붙은 것 제외) 메타데이터를 가진 패키지가 있으면 실패한다(이미지에 GPL 구성요소를 넣지 않는 원칙).
실행: python scripts/third_party.py > PACKAGES.txt"""
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import re
import sys
from importlib import metadata
from pathlib import Path


def license_of(d) -> str:
    m = d.metadata
    expr = m.get("License-Expression")
    if expr:
        return expr
    lic = (m.get("License") or "").strip().splitlines()
    if lic and len(lic[0]) < 80:
        return lic[0]
    cls = [c.split("::")[-1].strip() for c in m.get_all("Classifier") or [] if c.startswith("License ::")]
    return "; ".join(cls) or "(메타데이터 없음 — THIRD_PARTY_NOTICES 보충 고지 참고)"


rows, bad = [], []
for d in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
    name, lic = d.metadata["Name"], license_of(d)
    files = [str(f) for f in d.files or [] if re.search(r"(LICEN[CS]E|COPYING|NOTICE|License\.txt)", str(f), re.I)]
    rows.append(f"{name}=={d.version}\t{lic}\t{', '.join(files[:4]) or '-'}")
    if re.search(r"\bGPL\b|General Public License", lic) and not re.search(r"LGPL|Lesser|Library|exception|Apache|MIT|BSD", lic, re.I):
        bad.append(f"{name}: {lic}")

site = Path(metadata.distribution("numpy").locate_file(""))
libs = sorted(f"{p.parent.name}/{p.name}" for p in site.glob("*.libs/*"))
print("# Python 패키지(이름==버전, 라이선스 메타데이터, 패키지 안 라이선스 파일)")
print("\n".join(rows))
print("\n# 휠에 동봉된 네이티브 라이브러리(*.libs)")
print("\n".join(libs))
print("\n# FFmpeg(LGPL-2.1+, 이 이미지에서 빌드): /opt/ffmpeg/lib, 소스·configure: /opt/ffmpeg/src")
print("\n".join(sorted(p.name for p in Path("/opt/ffmpeg/lib").glob("lib*.so.*") if not p.is_symlink())) if Path("/opt/ffmpeg/lib").exists() else "-")
import shutil  # noqa: E402
import subprocess  # noqa: E402

if shutil.which("dpkg-query"):  # 이미지에 apt로 더한 실행 구성요소(openvino: OpenCL·Intel GPU runtime)
    extra = subprocess.run(["dpkg-query", "-W", "-f", "${Package}=${Version}\t/usr/share/doc/${Package}/copyright\n",
                            "ocl-icd-libopencl1", "intel-opencl-icd", "intel-igc-core-2", "intel-igc-opencl-2", "libigdgmm12"],
                           capture_output=True, text=True, check=False).stdout.strip()
    if extra:
        print("\n# apt·Intel 릴리스 .deb로 더한 구성요소(고지 위치는 THIRD_PARTY_NOTICES 4절)")
        print(extra)
if bad:
    sys.exit("GPL 패키지가 들어 있습니다: " + "; ".join(bad))
