"""한국어 파인튜닝 Whisper를 CTranslate2 float16으로 변환해 Hugging Face에 올린다(관리자가 한 번 실행).
원본은 transformers 형식뿐이라 udally-link가 바로 받을 수 없다. 토크나이저 파일이 빠진 원본은 openai/whisper-large-v3 것으로 채운다.

사용(HF_TOKEN에 쓰기 권한):
  uv run --with "transformers<5" --with torch python scripts/convert_ct2.py 작업폴더 [--upload]
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backends import KOREAN

FILL = ["tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt", "special_tokens_map.json",
        "added_tokens.json", "normalizer.json", "preprocessor_config.json", "generation_config.json"]
LICENSE = {"BuzzASR/korean": "mit", "gcasey2/whisper-large-v3-ko-en-v2": "apache-2.0"}
CARD = """---
license: {license}
language: [ko, en]
base_model: {base}
pipeline_tag: automatic-speech-recognition
tags: [ctranslate2, faster-whisper, whisper]
---
# {name} — CTranslate2 float16

[{base}](https://huggingface.co/{base})를 [faster-whisper](https://github.com/SYSTRAN/faster-whisper)용
CTranslate2 float16으로 변환한 사본입니다. 가중치는 바꾸지 않았고, 원본 라이선스({license})를 따릅니다.
udally-link(유달리 내 PC 전사)가 받습니다.

변환: `ct2-transformers-converter --model {base} --quantization float16 --copy_files tokenizer.json preprocessor_config.json`
"""


def convert(base: str, out: Path) -> None:
    if (out / "model.bin").exists():
        return
    from ctranslate2.converters import TransformersConverter
    from huggingface_hub import hf_hub_download, snapshot_download
    src = Path(snapshot_download(base, allow_patterns=["*.json", "*.txt", "model*.safetensors"]))
    for f in FILL:
        if not (src / f).exists():
            shutil.copy(hf_hub_download("openai/whisper-large-v3", f), src / f)
    TransformersConverter(str(src), copy_files=["tokenizer.json", "preprocessor_config.json"]).convert(
        str(out), quantization="float16")


def main() -> None:
    root, upload = Path(sys.argv[1]), "--upload" in sys.argv
    for repo, (_, name, base) in KOREAN.items():
        out = root / repo.replace("/", "__")
        convert(base, out)
        (out / "README.md").write_text(CARD.format(license=LICENSE[base], base=base, name=name), encoding="utf-8")
        if upload:
            from huggingface_hub import HfApi
            api = HfApi()
            api.create_repo(repo, exist_ok=True)
            api.upload_folder(repo_id=repo, folder_path=str(out), commit_message=f"CTranslate2 float16 of {base}")
        print(repo, "올림" if upload else "변환", out, flush=True)


if __name__ == "__main__":
    main()
