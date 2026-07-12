#!/usr/bin/env python3
"""Generate the checked-in vendor manifest after release artifacts are built."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

NAMES = {"darwin-arm64": "libcortext.dylib", "darwin-x64": "libcortext.dylib", "linux-x64": "libcortext.so", "linux-arm64": "libcortext.so", "windows-x64": "cortext.dll"}
MODEL = "models/AIST-87M-GGUF/AIST-87M_q8_0.gguf"
MODEL_SHA256 = "bf4c49954eccc65183f1a97e44606e86c7ee5a4fea500457124b687a3ec97898"
VOCAB = "models/mdbr-leaf-ir/vocab.txt"

def digest(path: Path) -> str:
  result = hashlib.sha256()
  with path.open("rb") as input:
    for block in iter(lambda: input.read(1024 * 1024), b""): result.update(block)
  return result.hexdigest()

def main() -> None:
  parser = argparse.ArgumentParser(); parser.add_argument("--vendor", type=Path, default=Path("vendor")); parser.add_argument("--version", default="1.2.0"); parser.add_argument("--targets", nargs="+", default=["darwin-arm64", "darwin-x64", "linux-x64", "linux-arm64"]); args = parser.parse_args()
  artifacts = []
  for target in args.targets:
    filename = NAMES[target]; path = args.vendor / target / filename
    if not path.is_file(): raise SystemExit(f"missing release artifact: {path}")
    artifacts.append({"target": target, "filename": f"{target}/{filename}", "sha256": digest(path)})
  chunks = sorted((args.vendor / "models" / "AIST-87M-GGUF" / "chunks").glob("AIST-87M_q8_0.gguf.part-*"))
  if not chunks: raise SystemExit("missing required AIST model chunks")
  assets = [{"filename": MODEL, "sha256": MODEL_SHA256, "chunks": [{"filename": str(path.relative_to(args.vendor)), "sha256": digest(path)} for path in chunks]}]
  vocab = args.vendor / VOCAB
  if not vocab.is_file(): raise SystemExit(f"missing required model asset: {vocab}")
  assets.append({"filename": VOCAB, "sha256": digest(vocab)})
  payload = {"schema": 1, "cortext_version": args.version, "generated_by": "tools/generate_manifest.py", "artifacts": artifacts, "assets": assets}
  (args.vendor / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__": main()
