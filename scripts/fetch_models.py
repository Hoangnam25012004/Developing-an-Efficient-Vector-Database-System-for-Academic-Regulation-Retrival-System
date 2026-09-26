"""Download the embedding and reranking models at the revisions the paper used.

    python scripts/fetch_models.py

Model names come from config.yaml, revisions from models.lock.json. Each
snapshot is downloaded into the Hugging Face cache (HF_HOME) and the cache's
`main` ref is pointed at it: the application loads models by name, and with
HF_HUB_OFFLINE=1 (set in the Docker image) that name resolves to the pinned
snapshot instead of whatever the Hub serves today. Models already complete
in the cache are not downloaded again. `*.bin` weights are skipped: both
models ship safetensors, which is what the libraries load.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# The one process allowed to reach the Hub: turn offline mode off before
# huggingface_hub reads its settings.
os.environ["HF_HUB_OFFLINE"] = "0"
os.environ.pop("TRANSFORMERS_OFFLINE", None)

import yaml  # noqa: E402
from huggingface_hub import snapshot_download  # noqa: E402
from huggingface_hub.constants import HF_HUB_CACHE  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def configured_models(cfg: dict) -> list[str]:
    names = [cfg["embedding"]["local_model"]]
    rr = cfg.get("reranking") or {}
    if rr.get("enabled"):
        names.append(rr["cross_encoder_model"])
    return names


def main() -> int:
    config_path = Path(os.environ.get("CONFIG_PATH") or ROOT / "config.yaml")
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    pins = json.loads((ROOT / "models.lock.json").read_text(encoding="utf-8"))["models"]
    hub = Path(HF_HUB_CACHE)
    markers = hub.parent / "arrs-models"
    markers.mkdir(parents=True, exist_ok=True)
    for repo in configured_models(cfg):
        rev = pins.get(repo)
        if not rev:
            raise SystemExit(f"{repo} has no pinned revision in models.lock.json")
        folder = "models--" + repo.replace("/", "--")
        marker = markers / f"{folder}@{rev}"
        if marker.exists():
            print(f"{repo}@{rev[:10]}: present")
        else:
            print(f"{repo}@{rev[:10]}: downloading...", flush=True)
            try:
                snapshot_download(repo_id=repo, revision=rev, ignore_patterns=["*.bin"])
            except Exception as exc:
                raise SystemExit(f"cannot download {repo}@{rev}: {exc}")
            marker.write_text("complete\n", encoding="utf-8")
        ref = hub / folder / "refs" / "main"
        ref.parent.mkdir(parents=True, exist_ok=True)
        if not ref.exists() or ref.read_text(encoding="utf-8").strip() != rev:
            ref.write_text(rev, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
