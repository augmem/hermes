from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import cortext_native


class ArtifactSelectionTests(unittest.TestCase):
  def _root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temp = tempfile.TemporaryDirectory(); root = Path(temp.name); (root / "vendor" / "linux-x64").mkdir(parents=True)
    artifact = root / "vendor" / "linux-x64" / "libcortext.so"; artifact.write_bytes(b"test library")
    (root / "vendor" / "manifest.json").write_text(json.dumps({"artifacts": [{"target": "linux-x64", "filename": "linux-x64/libcortext.so", "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}]}))
    return temp, root

  def test_normalizes_supported_platform_names(self) -> None:
    self.assertEqual(cortext_native.platform_tag("darwin", "aarch64"), "darwin-arm64")
    self.assertEqual(cortext_native.platform_tag("linux", "AMD64"), "linux-x64")
    self.assertEqual(cortext_native.platform_tag("win32", "x86_64"), "windows-x64")

  def test_selects_and_verifies_manifest_artifact(self) -> None:
    temp, root = self._root()
    with temp: self.assertEqual(cortext_native.selected_artifact("linux-x64", root).read_bytes(), b"test library")

  def test_rejects_tampered_artifact(self) -> None:
    temp, root = self._root()
    with temp:
      (root / "vendor" / "linux-x64" / "libcortext.so").write_bytes(b"tampered")
      with self.assertRaisesRegex(cortext_native.CortextError, "checksum mismatch"): cortext_native.selected_artifact("linux-x64", root)

  def test_rejects_unknown_platform_without_fallback(self) -> None:
    temp, root = self._root()
    with temp:
      with self.assertRaisesRegex(cortext_native.CortextError, "unsupported"): cortext_native.selected_artifact("plan9-x64", root)

  def test_checked_in_manifest_verifies_every_shipped_library(self) -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "vendor" / "manifest.json").read_text())
    self.assertEqual({entry["target"] for entry in manifest["artifacts"]}, {"darwin-arm64", "darwin-x64", "linux-x64", "linux-arm64", "windows-x64"})
    for entry in manifest["artifacts"]:
      self.assertEqual(cortext_native.selected_artifact(entry["target"], root), root / "vendor" / entry["filename"])
