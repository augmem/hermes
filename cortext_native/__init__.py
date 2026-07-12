"""Dependency-free bridge to the bundled Cortext C API.

Only files committed below ``vendor/`` are considered.  In particular this
module never falls back to a system library or downloads a binary.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import sys
import tempfile
from array import array
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any, Iterable

__all__ = ["Config", "Cortext", "CortextError", "Retention", "load_library", "platform_tag"]


class CortextError(RuntimeError):
  """A bundled library could not be selected, verified, or called."""


class Retention(IntEnum):
  NATURAL = 0
  DURABLE = 1
  BOUNDARY = 2
  EPHEMERAL = 3


@dataclass(slots=True)
class Config:
  focus: float = 0.5
  sensitivity: float = 0.5
  stability: float = 0.5
  affect_interrupt: bool = True
  affect_retrieval: bool = True
  reinforcement_enabled: bool = True
  procedural_enabled: bool = True
  sequential_edges_enabled: bool = True
  signal_filter_audio_enabled: bool = True
  signal_filter_image_enabled: bool = True
  signal_filter_text_enabled: bool = False


class _NativeConfig(ctypes.Structure):
  _fields_ = [
    ("struct_size", ctypes.c_size_t), ("focus", ctypes.c_double),
    ("sensitivity", ctypes.c_double), ("stability", ctypes.c_double),
    ("affect_interrupt", ctypes.c_int), ("affect_retrieval", ctypes.c_int),
    ("reinforcement_enabled", ctypes.c_int), ("procedural_enabled", ctypes.c_int),
    ("sequential_edges_enabled", ctypes.c_int),
    ("signal_filter_audio_enabled", ctypes.c_int),
    ("signal_filter_image_enabled", ctypes.c_int),
    ("signal_filter_text_enabled", ctypes.c_int),
  ]


class _ProcessOptions(ctypes.Structure):
  _fields_ = [
    ("struct_size", ctypes.c_size_t), ("include_embedding", ctypes.c_int),
    ("retention", ctypes.c_int), ("reserved", ctypes.c_int),
  ]


class _Media(ctypes.Structure):
  _fields_ = [
    ("data", ctypes.POINTER(ctypes.c_uint8)), ("size", ctypes.c_size_t),
    ("mimetype", ctypes.c_char_p),
  ]


def platform_tag(system: str | None = None, machine: str | None = None) -> str:
  """Return the canonical vendor target without making a load attempt."""
  system = (system or sys.platform).lower()
  os_tag = {"darwin": "darwin", "linux": "linux", "win32": "windows", "cygwin": "windows"}.get(system, system)
  machine = (machine or platform.machine()).lower()
  arch = {"arm64": "arm64", "aarch64": "arm64", "amd64": "x64", "x86_64": "x64"}.get(machine, machine)
  return f"{os_tag}-{arch}"


def _root() -> Path:
  return Path(__file__).resolve().parent.parent


def _sha256(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
      digest.update(chunk)
  return digest.hexdigest()


def selected_artifact(tag: str | None = None, root: Path | None = None) -> Path:
  """Find and checksum the exact artifact declared for *tag*.

  The manifest is deliberately a required generated release input, not a
  fallback catalogue.  A missing entry is an unsupported platform error.
  """
  root = root or _root()
  tag = tag or platform_tag()
  manifest_path = root / "vendor" / "manifest.json"
  try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError) as exc:
    raise CortextError(f"Cortext vendor manifest is unreadable: {exc}") from exc
  entries = manifest.get("artifacts") if isinstance(manifest, dict) else None
  entry = next((item for item in entries or [] if item.get("target") == tag), None)
  supported = ", ".join(sorted(str(item.get("target")) for item in entries or []))
  if not isinstance(entry, dict):
    raise CortextError(f"Cortext is unsupported on {tag}; bundled targets: {supported or 'none'}")
  filename, expected = entry.get("filename"), entry.get("sha256")
  if not isinstance(filename, str) or not isinstance(expected, str):
    raise CortextError(f"Cortext manifest entry for {tag} is invalid")
  candidate = root / "vendor" / filename
  if not candidate.is_file():
    raise CortextError(f"Cortext artifact for {tag} is missing: {candidate.name}")
  actual = _sha256(candidate)
  if actual != expected:
    raise CortextError(f"Cortext artifact checksum mismatch for {tag}; refusing to load it")
  return candidate


def _required_asset(filename: str) -> Path:
  root = _root()
  try:
    manifest = json.loads((root / "vendor" / "manifest.json").read_text(encoding="utf-8"))
    entry = next(item for item in manifest.get("assets", []) if item.get("filename") == filename)
  except (OSError, json.JSONDecodeError, StopIteration) as exc:
    raise CortextError(f"Cortext required asset is not declared: {filename}") from exc
  path = root / "vendor" / filename
  if not path.is_file() or _sha256(path) != entry.get("sha256"):
    raise CortextError(f"Cortext required asset is missing or has an invalid checksum: {filename}")
  return path


def _materialize_model(db_path: str) -> Path:
  """Reassemble the checked-in model chunks into Hermes's local data cache.

  GitHub rejects single files over 100 MB, so the model is versioned as
  ordinary Git chunks instead of relying on Git LFS.  This is local file I/O,
  never a network download; every input and the assembled output are checked.
  """
  root = _root()
  try:
    manifest = json.loads((root / "vendor" / "manifest.json").read_text(encoding="utf-8"))
    asset = next(item for item in manifest.get("assets", []) if item.get("filename") == "models/AIST-87M-GGUF/AIST-87M_q8_0.gguf")
    chunks = asset["chunks"]
  except (OSError, json.JSONDecodeError, StopIteration, KeyError, TypeError) as exc:
    raise CortextError("Cortext model chunks are not declared correctly") from exc
  cache = Path(db_path).expanduser().parent / ".cortext-assets"
  model = cache / "AIST-87M-GGUF" / "AIST-87M_q8_0.gguf"
  expected = asset.get("sha256")
  if model.is_file() and _sha256(model) == expected:
    return model
  model.parent.mkdir(parents=True, exist_ok=True)
  cache.mkdir(parents=True, exist_ok=True)
  vocab_source = _required_asset("models/mdbr-leaf-ir/vocab.txt")
  vocab = cache / "mdbr-leaf-ir" / "vocab.txt"
  vocab.parent.mkdir(parents=True, exist_ok=True)
  if not vocab.is_file() or _sha256(vocab) != _sha256(vocab_source):
    vocab.write_bytes(vocab_source.read_bytes())
  handle = tempfile.NamedTemporaryFile(prefix=".aist-", suffix=".tmp", dir=model.parent, delete=False)
  temp = Path(handle.name)
  try:
    with handle:
      for chunk in chunks:
        filename, checksum = chunk.get("filename"), chunk.get("sha256")
        path = root / "vendor" / str(filename)
        if not path.is_file() or _sha256(path) != checksum:
          raise CortextError(f"Cortext model chunk is missing or has an invalid checksum: {filename}")
        with path.open("rb") as source:
          for block in iter(lambda: source.read(1024 * 1024), b""): handle.write(block)
    if _sha256(temp) != expected:
      raise CortextError("Cortext reassembled model checksum mismatch")
    temp.replace(model)
  finally:
    temp.unlink(missing_ok=True)
  return model


def _configure(lib: ctypes.CDLL) -> ctypes.CDLL:
  lib.cortext_config_init.argtypes = [ctypes.POINTER(_NativeConfig)]
  lib.cortext_create_with_config.argtypes = [ctypes.POINTER(_NativeConfig), ctypes.c_char_p]
  lib.cortext_create_with_config.restype = ctypes.c_void_p
  lib.cortext_free.argtypes = [ctypes.c_void_p]
  lib.cortext_flush.argtypes = [ctypes.c_void_p]; lib.cortext_flush.restype = ctypes.c_int
  lib.cortext_consolidate_json.argtypes = [ctypes.c_void_p]; lib.cortext_consolidate_json.restype = ctypes.c_void_p
  lib.cortext_last_error.argtypes = []; lib.cortext_last_error.restype = ctypes.c_char_p
  lib.cortext_string_free.argtypes = [ctypes.c_void_p]
  lib.cortext_process_text_json_with_options.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(_ProcessOptions)]
  lib.cortext_process_text_json_with_options.restype = ctypes.c_void_p
  lib.cortext_process_audio_with_media_json_with_options.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.c_char_p, ctypes.POINTER(_Media), ctypes.POINTER(_ProcessOptions)]
  lib.cortext_process_audio_with_media_json_with_options.restype = ctypes.c_void_p
  lib.cortext_process_image_with_media_json_with_options.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint8), ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(_Media), ctypes.POINTER(_ProcessOptions)]
  lib.cortext_process_image_with_media_json_with_options.restype = ctypes.c_void_p
  return lib


def load_library() -> ctypes.CDLL:
  path = selected_artifact()
  try:
    if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
      os.add_dll_directory(str(path.parent))  # type: ignore[name-defined]
    return _configure(ctypes.CDLL(str(path)))
  except OSError as exc:
    raise CortextError(f"Unable to load bundled Cortext library for {platform_tag()}: {exc}") from exc


class Cortext:
  """Small C API-shaped engine facade used by the Hermes provider."""

  def __init__(self, db_path: str, *, config: Config | None = None) -> None:
    self._lib = load_library()
    native = _NativeConfig()
    self._lib.cortext_config_init(ctypes.byref(native))
    if config is not None:
      for key in Config.__dataclass_fields__:
        value = getattr(config, key)
        setattr(native, key, int(value) if isinstance(value, bool) else value)
    # The C++ encoder resolves its model via this environment variable.  The
    # plugin verifies both model and tokenizer before making that resolution.
    model = _materialize_model(db_path)
    previous = os.environ.get("CORTEXT_AIST_MODEL_PATH")
    os.environ["CORTEXT_AIST_MODEL_PATH"] = str(model)
    try:
      self._handle = self._lib.cortext_create_with_config(ctypes.byref(native), db_path.encode())
    finally:
      if previous is None: os.environ.pop("CORTEXT_AIST_MODEL_PATH", None)
      else: os.environ["CORTEXT_AIST_MODEL_PATH"] = previous
    if not self._handle:
      raise CortextError(self._last_error("Cortext initialization failed"))

  def _last_error(self, fallback: str) -> str:
    value = self._lib.cortext_last_error()
    return f"{fallback}: {value.decode(errors='replace')}" if value else fallback

  def _options(self, include_embedding: bool, retention: Retention | int | None) -> _ProcessOptions:
    return _ProcessOptions(ctypes.sizeof(_ProcessOptions), int(include_embedding), int(Retention.NATURAL if retention is None else retention), 0)

  def _json(self, function: Any, *args: Any) -> dict[str, Any]:
    result = function(self._handle, *args)
    if not result:
      raise CortextError(self._last_error("Cortext processing failed"))
    try:
      return json.loads(ctypes.string_at(result).decode("utf-8"))
    finally:
      self._lib.cortext_string_free(result)

  def process_text(self, text: str, source_id: str, include_embedding: bool = False, retention: Retention | int | None = None) -> dict[str, Any]:
    return self._json(self._lib.cortext_process_text_json_with_options, text.encode(), source_id.encode(), ctypes.byref(self._options(include_embedding, retention)))

  def process_audio(self, pcm: Iterable[float], source_id: str, include_embedding: bool = False, retention: Retention | int | None = None, media: bytes | None = None, media_mimetype: str | None = None) -> dict[str, Any]:
    samples = array("f", pcm)
    raw = (ctypes.c_float * len(samples)).from_buffer(samples)
    media_value, keepalive = _media(media, media_mimetype)
    _ = keepalive
    return self._json(self._lib.cortext_process_audio_with_media_json_with_options, raw, len(samples), source_id.encode(), ctypes.byref(media_value), ctypes.byref(self._options(include_embedding, retention)))

  def process_image(self, data: bytes, width: int, height: int, channels: int, source_id: str, include_embedding: bool = False, retention: Retention | int | None = None, media: bytes | None = None, media_mimetype: str | None = None) -> dict[str, Any]:
    raw = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
    media_value, keepalive = _media(media, media_mimetype)
    _ = keepalive
    return self._json(self._lib.cortext_process_image_with_media_json_with_options, raw, width, height, channels, source_id.encode(), ctypes.byref(media_value), ctypes.byref(self._options(include_embedding, retention)))

  def flush(self) -> None:
    if self._lib.cortext_flush(self._handle): raise CortextError(self._last_error("Cortext flush failed"))

  def consolidate(self) -> dict[str, Any]: return self._json(self._lib.cortext_consolidate_json)

  def close(self) -> None:
    if self._handle:
      self._lib.cortext_free(self._handle); self._handle = None


def _media(data: bytes | None, mimetype: str | None) -> tuple[_Media, Any]:
  if not data: return _Media(None, 0, None), None
  if not mimetype: raise ValueError("media_mimetype is required with media")
  raw = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
  mime = mimetype.encode()
  return _Media(raw, len(data), mime), (raw, mime)
