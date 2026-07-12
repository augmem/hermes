"""Silent, native-backed Cortext memory provider for Hermes."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import queue
import threading
from pathlib import Path
from typing import Any

from cortext_native import Config, Cortext, CortextError, Retention, selected_artifact
from media import MediaSignal, signals

try:
  from agent.memory_provider import MemoryProvider
except ImportError:  # pragma: no cover
  class MemoryProvider: pass

logger = logging.getLogger(__name__)
PROVIDER_NAME, CONFIG_FILENAME = "cortext", "cortext.json"
DEFAULTS: dict[str, Any] = {"db_path": "$HERMES_HOME/cortext.sqlite", "focus": .55, "sensitivity": .50, "stability": .65, "top_k": 6, "seam_user": True, "seam_pre_llm": True, "seam_post_llm": True, "auto_consolidate": True, "ingest_media": True}


def load_config(hermes_home: str | Path | None = None) -> dict[str, Any]:
  cfg = dict(DEFAULTS)
  home = Path(hermes_home or os.environ.get("HERMES_HOME", "")) if hermes_home or os.environ.get("HERMES_HOME") else None
  if not home: return cfg
  try:
    data = json.loads((home / CONFIG_FILENAME).read_text())
    if isinstance(data, dict): cfg.update(data)
  except (OSError, json.JSONDecodeError): pass
  return cfg


def resolve_db_path(db_path: str, hermes_home: str) -> Path:
  value = db_path.replace("$HERMES_HOME", hermes_home).replace("${HERMES_HOME}", hermes_home)
  path = Path(value).expanduser()
  return path if path.is_absolute() else Path(hermes_home) / path


def _memory_text(item: dict[str, Any]) -> str:
  if str(item.get("modality", "text")).lower() != "text": return ""
  if isinstance(item.get("text"), str): return item["text"].strip()
  content = item.get("content")
  if isinstance(content, str): return content.strip()
  if isinstance(content, list):
    result = []
    for part in content:
      if isinstance(part, str): result.append(part)
      elif isinstance(part, dict) and isinstance(part.get("text"), str): result.append(part["text"])
      elif isinstance(part, dict) and isinstance(part.get("base64"), str):
        try: result.append(base64.b64decode(part["base64"]).decode("utf-8", "replace"))
        except ValueError: pass
    return " ".join(result).strip()
  return ""


def _format(items: list[dict[str, Any]], limit: int) -> str:
  result = []
  for item in items[:limit]:
    text = _memory_text(item)
    if text: result.append(f"- {text}")
  return "\n".join(result)


class CortextMemoryProvider(MemoryProvider):
  def __init__(self, config: dict[str, Any] | None = None) -> None:
    self._config = dict(config or DEFAULTS); self._engine: Any = None; self._session_id = "session"; self._turn_number = 0; self._agent_context = "primary"; self._user_id = "user"; self._agent_id = "agent"
    self._lock = threading.Lock(); self._queue: queue.Queue[Any] = queue.Queue(); self._worker: threading.Thread | None = None; self._cache: tuple[str, str] = ("", ""); self._last_user_key = ""

  @property
  def name(self) -> str: return PROVIDER_NAME

  def is_available(self) -> bool:
    try: selected_artifact(); return True
    except CortextError: return False

  def get_config_schema(self) -> list[dict[str, Any]]:
    return [{"key": key, "default": str(self._config[key]), "description": key.replace("_", " ")} for key in ("db_path", "focus", "sensitivity", "stability")]

  def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
    self._config.update({key: value for key, value in values.items() if key in DEFAULTS})
    path = Path(hermes_home) / CONFIG_FILENAME; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(self._config, indent=2) + "\n")

  def initialize(self, session_id: str, **kwargs: Any) -> None:
    home = str(kwargs.get("hermes_home") or os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    self._config = load_config(home); self._session_id = session_id or "session"; self._agent_context = str(kwargs.get("agent_context") or "primary"); self._user_id = _safe(str(kwargs.get("user_id") or "user")); self._agent_id = _safe(str(kwargs.get("agent_id") or kwargs.get("agent_identity") or "agent"))
    db = resolve_db_path(str(self._config["db_path"]), home); db.parent.mkdir(parents=True, exist_ok=True)
    self._engine = Cortext(str(db), config=Config(focus=float(self._config["focus"]), sensitivity=float(self._config["sensitivity"]), stability=float(self._config["stability"])))

  def system_prompt_block(self) -> str: return ""
  def get_tool_schemas(self) -> list[dict[str, Any]]: return []
  def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str: return json.dumps({"error": f"no tools exposed ({tool_name})"})

  def on_turn_start(self, turn_number: int, message: Any, **kwargs: Any) -> None:
    self._turn_number = int(turn_number or 0)
    if self._enabled("seam_user"): self._ingest(signals(message, self._source("user", "turn", str(self._turn_number))))

  def sync_turn(self, user_content: Any, assistant_content: Any, **kwargs: Any) -> None:
    if not self._enabled("seam_post_llm"): return
    user = signals(user_content, self._source("user", "turn", str(self._turn_number))); assistant = signals(assistant_content, self._source("agent", "turn", str(self._turn_number)))
    key = _key(self._session_id, self._turn_number, next((item.text for item in user if item.modality == "text"), ""))
    self._ingest(([item for item in user if key != self._last_user_key] + assistant), user_key=key)

  def prefetch(self, query: str, **kwargs: Any) -> str:
    if not self._enabled("seam_pre_llm") or not self._engine or not query.strip(): return ""
    if self._cache[0] == query: return self._cache[1]
    try:
      with self._lock: packet = self._process_text(query, self._source("agent", "prefetch"), Retention.EPHEMERAL)
      memories = packet.get("retrieved_memory") or []
      block = _format([item for item in memories if isinstance(item, dict)], int(self._config.get("top_k", 6))); self._cache = (query, block); return block
    except Exception as exc: logger.warning("Cortext prefetch failed: %s", exc); return ""

  def queue_prefetch(self, query: str, **kwargs: Any) -> None: self._enqueue(lambda: self.prefetch(query))

  def on_session_end(self, messages: list[dict[str, Any]]) -> None:
    self._drain()
    if self._engine:
      with self._lock:
        if self._enabled("auto_consolidate"): self._engine.consolidate()
        self._engine.flush()

  def on_pre_compress(self, messages: list[dict[str, Any]]) -> str:
    for message in messages[-3:]:
      if isinstance(message, dict) and message.get("role") == "user": self._ingest(signals(message.get("content"), self._source("user", "pre_compress")))
    return ""

  def on_session_switch(self, new_session_id: str, **kwargs: Any) -> None: self._session_id = new_session_id or self._session_id; self._cache = ("", ""); self._last_user_key = ""
  def on_memory_write(self, action: str, target: str, content: str, **kwargs: Any) -> None:
    if action in {"add", "write", "append"}: self._ingest([MediaSignal("text", self._source("user", "builtin", target), text=content)])
  def on_delegation(self, task: str, result: str, **kwargs: Any) -> None: self._ingest(signals(task, self._source("agent", "delegation", "task")) + signals(result, self._source("agent", "delegation", "result")))
  def shutdown(self) -> None:
    self._drain(); engine, self._engine = self._engine, None
    if engine:
      try: engine.flush(); engine.close()
      except Exception: pass

  def _enabled(self, key: str) -> bool: return bool(self._config.get(key, True))
  def _writeable(self) -> bool: return self._agent_context in {"", "primary"}
  def _source(self, role: str, *parts: str) -> str: return "/".join(["hermes", role, _safe(self._user_id if role == "user" else self._agent_id), _safe(self._session_id), *(_safe(part) for part in parts)])
  def _process_text(self, text: str, source: str, retention: Retention) -> dict[str, Any]: return self._engine.process_text(text, source, include_embedding=False, retention=retention)
  def _ingest(self, items: list[MediaSignal], user_key: str = "") -> None:
    if self._engine and self._writeable(): self._enqueue(lambda: self._write(items, user_key))
  def _write(self, items: list[MediaSignal], user_key: str) -> None:
    try:
      with self._lock:
        for item in items:
          if item.is_empty(): continue
          if item.modality == "text": self._process_text(item.text, item.source_id, Retention.DURABLE)
          elif item.modality == "audio" and self._enabled("ingest_media"): self._engine.process_audio(item.pcm or [], item.source_id, retention=Retention.DURABLE, media=item.original, media_mimetype=item.mimetype)
          elif item.modality == "image" and self._enabled("ingest_media"): self._engine.process_image(item.data, item.width, item.height, item.channels, item.source_id, retention=Retention.DURABLE, media=item.original, media_mimetype=item.mimetype)
        self._engine.flush(); self._cache = ("", ""); self._last_user_key = user_key or self._last_user_key
    except Exception as exc: logger.warning("Cortext ingest failed: %s", exc)
  def _enqueue(self, job: Any) -> None:
    if self._worker is None or not self._worker.is_alive():
      self._worker = threading.Thread(target=lambda: _worker(self._queue), daemon=True, name="cortext-ingest"); self._worker.start()
    self._queue.put(job)
  def _drain(self) -> None:
    done = threading.Event(); self._enqueue(done.set); done.wait(10)


def _worker(q: queue.Queue[Any]) -> None:
  while True:
    job = q.get()
    try: job()
    except Exception as exc: logger.warning("Cortext background task failed: %s", exc)
    finally: q.task_done()

def _safe(value: str) -> str: return "".join(char if char.isalnum() or char in "-_.@" else "_" for char in value) or "session"
def _key(session: str, turn: int, text: str) -> str: return f"{session}:{turn}:{hashlib.sha256(text.encode()).hexdigest()[:16]}" if turn and text else ""
def register(ctx: Any) -> None:
  provider = CortextMemoryProvider(load_config(os.environ.get("HERMES_HOME"))); ctx.register_memory_provider(provider)
