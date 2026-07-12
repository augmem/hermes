"""Standard-library multimodal normalization for the standalone plugin."""
from __future__ import annotations

import base64
import struct
import wave
import zlib
from array import array
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


@dataclass(slots=True)
class MediaSignal:
  modality: str
  source_id: str
  text: str = ""
  data: bytes = b""
  width: int = 0
  height: int = 0
  channels: int = 0
  pcm: list[float] | None = None
  original: bytes = b""
  mimetype: str = ""

  def is_empty(self) -> bool:
    return not (self.text.strip() if self.modality == "text" else self.data if self.modality == "image" else self.pcm)


def _wav(raw: bytes) -> list[float] | None:
  try:
    with wave.open(BytesIO(raw), "rb") as reader:
      channels, width, rate = reader.getnchannels(), reader.getsampwidth(), reader.getframerate()
      frames = reader.readframes(reader.getnframes())
  except (wave.Error, EOFError): return None
  if width == 1: values = [(item - 128) / 128 for item in frames]
  elif width == 2:
    samples = array("h"); samples.frombytes(frames); values = [item / 32768 for item in samples]
  elif width == 4:
    samples = array("i"); samples.frombytes(frames); values = [item / 2147483648 for item in samples]
  else: return None
  if channels > 1: values = [sum(values[i:i + channels]) / channels for i in range(0, len(values), channels)]
  if not values or rate <= 0: return None
  if rate == 16000: return values
  out_len = max(1, int(len(values) * 16000 / rate))
  return [values[min(len(values) - 1, round(i * (len(values) - 1) / max(1, out_len - 1)))] for i in range(out_len)]


def _png(raw: bytes) -> tuple[bytes, int, int, int] | None:
  """Decode non-interlaced 8-bit RGB/RGBA/greyscale PNG without Pillow."""
  if not raw.startswith(b"\x89PNG\r\n\x1a\n"): return None
  pos, packed, width, height, depth, colour = 8, b"", 0, 0, 0, 0
  try:
    while pos + 8 <= len(raw):
      size = struct.unpack(">I", raw[pos:pos + 4])[0]; kind = raw[pos + 4:pos + 8]; body = raw[pos + 8:pos + 8 + size]; pos += size + 12
      if kind == b"IHDR": width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", body); assert compression == filtering == interlace == 0
      elif kind == b"IDAT": packed += body
      elif kind == b"IEND": break
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[colour]; assert depth == 8 and width > 0 and height > 0
    scan, stride = zlib.decompress(packed), width * channels
    prior, rows = bytearray(stride), []
    pos = 0
    for _ in range(height):
      filter_type, row = scan[pos], bytearray(scan[pos + 1:pos + 1 + stride]); pos += stride + 1
      for i, value in enumerate(row):
        left, up, corner = (row[i - channels] if i >= channels else 0), prior[i], (prior[i - channels] if i >= channels else 0)
        if filter_type == 1: row[i] = (value + left) & 255
        elif filter_type == 2: row[i] = (value + up) & 255
        elif filter_type == 3: row[i] = (value + ((left + up) // 2)) & 255
        elif filter_type == 4:
          p, pa, pb, pc = left + up - corner, abs(up - corner), abs(left - corner), abs(left + up - 2 * corner)
          row[i] = (value + (left if pa <= pb and pa <= pc else up if pb <= pc else corner)) & 255
        elif filter_type != 0: return None
      rows.append(row); prior = row
    decoded = b"".join(rows)
    if colour == 0: decoded = b"".join(bytes((x, x, x)) for x in decoded); channels = 3
    elif colour == 4: decoded = b"".join(bytes((decoded[i], decoded[i], decoded[i], decoded[i + 1])) for i in range(0, len(decoded), 2)); channels = 4
    return decoded, width, height, channels
  except (AssertionError, KeyError, ValueError, struct.error, zlib.error): return None


def _load(ref: str) -> tuple[bytes, str] | None:
  if ref.startswith("data:") and ";base64," in ref:
    header, body = ref.split(",", 1)
    try: return base64.b64decode(body, validate=False), header[5:].split(";", 1)[0]
    except ValueError: return None
  path = Path(unquote(urlparse(ref).path) if ref.startswith("file://") else ref).expanduser()
  if not path.is_file(): return None
  try: return path.read_bytes(), {".png": "image/png", ".wav": "audio/wav"}.get(path.suffix.lower(), "")
  except OSError: return None


def media_signal(ref: str, source_id: str) -> MediaSignal | None:
  loaded = _load(ref)
  if not loaded: return None
  raw, mime = loaded
  if mime == "audio/wav" or raw[:4] == b"RIFF":
    pcm = _wav(raw); return MediaSignal("audio", source_id, pcm=pcm, original=raw, mimetype="audio/wav") if pcm else None
  decoded = _png(raw)
  if decoded:
    data, width, height, channels = decoded; return MediaSignal("image", source_id, data=data, width=width, height=height, channels=channels, original=raw, mimetype="image/png")
  return None


def signals(content: Any, source_id: str) -> list[MediaSignal]:
  """Accept Hermes/OpenAI text, input_audio, image_url, and local/data URLs."""
  if isinstance(content, str):
    media = media_signal(content, source_id + "/media")
    return [media] if media else [MediaSignal("text", source_id, text=content[:4000])] if content.strip() else []
  if isinstance(content, dict): content = [content]
  if not isinstance(content, list): return []
  result: list[MediaSignal] = []; text: list[str] = []
  for index, part in enumerate(content):
    if isinstance(part, str): text.append(part); continue
    if not isinstance(part, dict): continue
    kind = str(part.get("type", ""))
    if kind in {"text", "input_text"}: text.append(str(part.get("text") or part.get("content") or "")); continue
    if kind in {"image", "image_url", "input_image"}:
      ref = part.get("url") or part.get("image") or (part.get("image_url") or {}).get("url", "")
      item = media_signal(str(ref), f"{source_id}/image/{index}")
      if item: result.append(item)
    if kind in {"audio", "input_audio"}:
      audio = part.get("input_audio") or part; raw = audio.get("data") if isinstance(audio, dict) else None
      if raw:
        item = media_signal("data:audio/wav;base64," + str(raw), f"{source_id}/audio/{index}")
        if item: result.append(item)
  if "".join(text).strip(): result.insert(0, MediaSignal("text", source_id, text="\n".join(text)[:4000]))
  return result
