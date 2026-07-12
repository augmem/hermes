from __future__ import annotations

import base64
import struct
import unittest
import zlib
from io import BytesIO
import wave

from media import media_signal, signals


def png_rgb() -> bytes:
  # 1×1 RGB non-interlaced PNG, assembled to exercise the stdlib decoder.
  def chunk(kind: bytes, value: bytes) -> bytes: return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xffffffff)
  return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0")) + chunk(b"IEND", b"")


class MediaTests(unittest.TestCase):
  def test_png_data_url_decodes_without_pillow(self) -> None:
    encoded = base64.b64encode(png_rgb()).decode(); item = media_signal("data:image/png;base64," + encoded, "image")
    self.assertIsNotNone(item); self.assertEqual((item.width, item.height, item.channels, item.data), (1, 1, 3, b"\xff\0\0"))

  def test_wav_data_url_decodes_and_resamples(self) -> None:
    output = BytesIO()
    with wave.open(output, "wb") as wav:
      wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000); wav.writeframes(struct.pack("<hh", 0, 32767))
    encoded = base64.b64encode(output.getvalue()).decode(); item = media_signal("data:audio/wav;base64," + encoded, "audio")
    self.assertIsNotNone(item); self.assertEqual(len(item.pcm or []), 4)

  def test_hermes_input_audio_part_is_normalized(self) -> None:
    item = signals([{"type": "input_audio", "input_audio": {"data": base64.b64encode(b"not wav").decode()}}], "source")
    self.assertEqual(item, [])
