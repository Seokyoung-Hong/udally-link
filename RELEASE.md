# 릴리스 메모(관리자용)

## 공개 push
- 공개 저장소에는 **`git push origin master:main`만** 씁니다(로컬 브랜치 이름이 master). `--all`·`--tags`·`--mirror`는 쓰지 않습니다.
- 로컬 전용(올리지 않음): `backup-local/*` 브랜치. 이력 재작성 전 상태(옛 이메일·TTS 표본·사설 IP 예시)가 들어 있습니다.
- 릴리스 태그는 `git tag -s v0.x.y && git push origin v0.x.y`로 그 태그 하나만 올립니다. 그러면 `.github/workflows/release.yml`이 이미지를 빌드·푸시하고 GCC 런타임 소스(SRPM)를 릴리스에 첨부합니다.

## 공개 전 확인
- 라이선스 체크리스트: `test_decode.py`를 각 이미지에서 실행하고, `/app/licenses/PACKAGES.txt`를 확인합니다(GPL 패키지가 있으면 빌드가 실패합니다).
- nemotron 태그는 공개 대상이 아닙니다(README 1절).
