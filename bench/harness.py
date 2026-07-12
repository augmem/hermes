"""Provider-agnostic benchmark driver.

Drives each memory backend through the real Hermes ``MemoryProvider`` seams in
the order Hermes calls them (prefetch -> on_turn_start -> sync_turn ->
queue_prefetch -> on_session_end -> shutdown), with a full provider shutdown
between sessions so cross-session recall must come from durable storage.

Metrics captured per provider:
  - per-probe cold-start context packet (text, chars, est. tokens, latency)
  - packet recall: expected-fact groups present in the packet
  - stale leakage: superseded facts still present in the packet
  - outbound network connections during ingest and recall
  - offline recall: does the probe still work with sockets disabled
  - model-visible surface: tool schema count, system prompt block size
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
  sys.path.insert(0, str(REPO_ROOT))

from bench import scenario


def load_dotenv(path: Path | None = None) -> None:
  """Minimal .env loader (KEY=VALUE lines, no quoting rules)."""
  env_path = path or REPO_ROOT / ".env"
  try:
    for line in env_path.read_text().splitlines():
      line = line.strip()
      if not line or line.startswith("#") or "=" not in line:
        continue
      key, _, value = line.partition("=")
      os.environ.setdefault(key.strip(), value.strip())
  except OSError:
    pass


def est_tokens(text: str) -> int:
  return max(0, round(len(text) / 4))


class NetworkMeter:
  """Counts outbound socket connections; can also block them entirely."""

  def __init__(self) -> None:
    self.connections: list[str] = []
    self.blocked = False
    self._orig_connect = None

  def __enter__(self) -> "NetworkMeter":
    meter = self
    self._orig_connect = socket.socket.connect

    def counting_connect(sock, address, _orig=self._orig_connect):
      host = address[0] if isinstance(address, tuple) else str(address)
      if not str(host).startswith(("127.", "::1", "/")):
        if meter.blocked:
          raise OSError(f"network disabled by benchmark (attempted {host})")
        meter.connections.append(str(host))
      return _orig(sock, address)

    socket.socket.connect = counting_connect
    return self

  def __exit__(self, *exc: Any) -> None:
    if self._orig_connect is not None:
      socket.socket.connect = self._orig_connect


@dataclass
class ProbeResult:
  probe_id: str
  packet: str = ""
  packet_chars: int = 0
  packet_tokens: int = 0
  recall_latency_ms: float = 0.0
  matched_groups: list[str] = field(default_factory=list)
  missed_groups: list[str] = field(default_factory=list)
  stale_groups: list[str] = field(default_factory=list)
  offline_ok: bool | None = None
  answer: str = ""
  answer_matched: list[str] = field(default_factory=list)
  answer_missed: list[str] = field(default_factory=list)


@dataclass
class ProviderResult:
  provider: str
  ok: bool = True
  error: str = ""
  ingest_seconds: float = 0.0
  ingest_network_connections: int = 0
  recall_network_connections: int = 0
  tool_schema_count: int = 0
  tool_schema_chars: int = 0
  system_prompt_chars: int = 0
  probes: list[ProbeResult] = field(default_factory=list)

  def to_dict(self) -> dict:
    return {
      "provider": self.provider,
      "ok": self.ok,
      "error": self.error,
      "ingest_seconds": round(self.ingest_seconds, 2),
      "ingest_network_connections": self.ingest_network_connections,
      "recall_network_connections": self.recall_network_connections,
      "tool_schema_count": self.tool_schema_count,
      "tool_schema_chars": self.tool_schema_chars,
      "system_prompt_chars": self.system_prompt_chars,
      "standing_overhead_tokens": est_tokens(" " * (self.tool_schema_chars + self.system_prompt_chars)),
      "probes": [vars(p) for p in self.probes],
    }


def match_groups(text: str, groups: list[list[str]]) -> tuple[list[str], list[str]]:
  lower = text.lower()
  matched, missed = [], []
  for group in groups:
    (matched if any(t.lower() in lower for t in group) else missed).append("|".join(group))
  return matched, missed


def _wait_background(provider: Any, settle_seconds: float) -> None:
  join = getattr(provider, "_join_background", None)
  if callable(join):
    try:
      join(timeout=120.0)
    except Exception:
      pass
  for attr in ("_sync_thread", "_prefetch_thread"):
    thread = getattr(provider, attr, None)
    if thread is not None and getattr(thread, "is_alive", lambda: False)():
      thread.join(timeout=60.0)
  if settle_seconds > 0:
    time.sleep(settle_seconds)


def _init(provider: Any, session_id: str, home: Path) -> None:
  provider.initialize(
    session_id,
    hermes_home=str(home),
    platform="cli",
    agent_context="primary",
    user_id=scenario.USER_ID,
    agent_identity=scenario.AGENT_ID,
  )


def run_provider(
  name: str,
  factory: Callable[[], Any],
  home: Path,
  *,
  settle_seconds: float = 0.0,
  session_settle_seconds: float = 0.0,
  offline_check: bool = True,
  log: Callable[[str], None] = print,
) -> ProviderResult:
  """Replay the scenario and run cold-start probes for one provider."""
  result = ProviderResult(provider=name)
  home.mkdir(parents=True, exist_ok=True)
  os.environ["HERMES_HOME"] = str(home)

  try:
    # ---------------- Ingest: replay every session, shutdown between ------
    started = time.perf_counter()
    with NetworkMeter() as ingest_net:
      for s_index, session in enumerate(scenario.SESSIONS, start=1):
        provider = factory()
        if not provider.is_available():
          raise RuntimeError(f"{name}: provider not available (config/deps)")
        _init(provider, f"bench-session-{s_index}", home)
        messages: list[dict] = []
        for turn, (user, assistant) in enumerate(session, start=1):
          provider.prefetch(user, session_id=f"bench-session-{s_index}")
          try:
            provider.on_turn_start(turn, user)
          except TypeError:
            provider.on_turn_start(turn, user, session_id=f"bench-session-{s_index}")
          provider.sync_turn(user, assistant, session_id=f"bench-session-{s_index}")
          provider.queue_prefetch(user, session_id=f"bench-session-{s_index}")
          messages += [{"role": "user", "content": user},
                       {"role": "assistant", "content": assistant}]
        _wait_background(provider, settle_seconds)
        provider.on_session_end(messages)
        _wait_background(provider, 0.0)
        provider.shutdown()
        log(f"  [{name}] session {s_index}/{len(scenario.SESSIONS)} ingested")
        if session_settle_seconds > 0:
          time.sleep(session_settle_seconds)
    result.ingest_seconds = time.perf_counter() - started
    result.ingest_network_connections = len(ingest_net.connections)

    # ---------------- Recall: fresh cold-start provider per probe run -----
    with NetworkMeter() as recall_net:
      provider = factory()
      _init(provider, "bench-probe-session", home)
      schemas = provider.get_tool_schemas() or []
      result.tool_schema_count = len(schemas)
      result.tool_schema_chars = len(json.dumps(schemas)) if schemas else 0
      result.system_prompt_chars = len(provider.system_prompt_block() or "")

      for probe in scenario.PROBES:
        pr = ProbeResult(probe_id=probe["id"])
        t0 = time.perf_counter()
        provider.queue_prefetch(probe["question"], session_id="bench-probe-session")
        packet = provider.prefetch(probe["question"], session_id="bench-probe-session") or ""
        if not packet:  # providers that fill the cache on queue_prefetch
          _wait_background(provider, 0.0)
          packet = provider.prefetch(probe["question"], session_id="bench-probe-session") or ""
        pr.recall_latency_ms = (time.perf_counter() - t0) * 1000.0
        pr.packet = packet
        pr.packet_chars = len(packet)
        pr.packet_tokens = est_tokens(packet)
        pr.matched_groups, pr.missed_groups = match_groups(packet, probe.get("expected", []))
        stale_matched, _ = match_groups(packet, probe.get("stale", []))
        pr.stale_groups = stale_matched
        result.probes.append(pr)
      provider.shutdown()
    result.recall_network_connections = len(recall_net.connections)

    # ---------------- Offline: same recall with sockets blocked -----------
    if offline_check:
      with NetworkMeter() as offline_net:
        offline_net.blocked = True
        try:
          provider = factory()
          _init(provider, "bench-offline-session", home)
          for probe, pr in zip(scenario.PROBES, result.probes):
            try:
              provider.queue_prefetch(probe["question"], session_id="bench-offline-session")
              packet = provider.prefetch(probe["question"], session_id="bench-offline-session") or ""
              if not packet:
                _wait_background(provider, 0.0)
                packet = provider.prefetch(probe["question"], session_id="bench-offline-session") or ""
              matched, _ = match_groups(packet, probe.get("expected", []))
              pr.offline_ok = bool(packet) and len(matched) == len(pr.matched_groups)
            except Exception:
              pr.offline_ok = False
          provider.shutdown()
        except Exception:
          for pr in result.probes:
            if pr.offline_ok is None:
              pr.offline_ok = False
  except Exception as exc:  # keep other providers running
    result.ok = False
    result.error = f"{type(exc).__name__}: {exc}"
  return result


# ---------------------------------------------------------------------------
# Provider factories
# ---------------------------------------------------------------------------

def make_cortext() -> Any:
  from provider import CortextMemoryProvider
  return CortextMemoryProvider()


def make_holographic() -> Any:
  from plugins.memory.holographic import HolographicMemoryProvider
  # sync_turn is a no-op for Holographic; auto_extract is its only
  # conversation-driven write path when no model is invoking its tools.
  return HolographicMemoryProvider(config={"auto_extract": True})


class _HolographicToolDriver:
  """Steelman wrapper: simulates a perfectly diligent model that stores every
  user turn via Holographic's ``fact_store`` tool. Upper bound on what its
  model-invoked write path can capture."""

  def __init__(self) -> None:
    from plugins.memory.holographic import HolographicMemoryProvider
    self._inner = HolographicMemoryProvider(config={"auto_extract": True})

  def sync_turn(self, user_content: str, assistant_content: str, **kwargs: Any) -> None:
    self._inner.sync_turn(user_content, assistant_content, **kwargs)
    try:
      self._inner.handle_tool_call(
        "fact_store", {"action": "add", "content": str(user_content)[:400]})
    except Exception:
      pass

  def __getattr__(self, name: str) -> Any:
    return getattr(self._inner, name)


def make_holographic_tools() -> Any:
  return _HolographicToolDriver()


def make_mem0() -> Any:
  from plugins.memory.mem0 import Mem0MemoryProvider
  return Mem0MemoryProvider()


# Path to a TencentDB-Agent-Memory checkout with node deps installed and its
# Gateway sidecar already running on 127.0.0.1:8420 (see bench/README.md).
TDAI_CHECKOUT = os.environ.get("TDAI_CHECKOUT", "")


def make_tencentdb() -> Any:
  provider_dir = str(Path(TDAI_CHECKOUT) / "hermes-plugin" / "memory")
  if provider_dir not in sys.path:
    sys.path.insert(0, provider_dir)
  os.environ.setdefault("MEMORY_TENCENTDB_GATEWAY_PORT", "8420")
  from memory_tencentdb import MemoryTencentdbProvider
  return MemoryTencentdbProvider()


FACTORIES: dict[str, Callable[[], Any]] = {
  "cortext": make_cortext,
  "holographic": make_holographic,
  "holographic-tools": make_holographic_tools,
  "mem0": make_mem0,
  "tencentdb": make_tencentdb,
}

# Mem0 extracts facts server-side after client.add() returns; give it time to
# settle before cold-start recall so eventual consistency doesn't punish it.
# TencentDB's Gateway runs its L1 extraction pipeline asynchronously after
# /capture; /session/end flushes it, but LLM extraction still needs wall time.
SETTLE = {
  "mem0": {"settle_seconds": 5.0, "session_settle_seconds": 10.0},
  "tencentdb": {"settle_seconds": 10.0, "session_settle_seconds": 15.0},
}

# Providers that delegate to a loopback sidecar process: the in-process socket
# blocker can't reach the sidecar's own network use, so an "offline" result
# would be meaningless. Skip the phase and report n/a.
OFFLINE_NOT_MEASURABLE = {"tencentdb"}


def save_results(results: list[ProviderResult], out_dir: Path) -> Path:
  out_dir.mkdir(parents=True, exist_ok=True)
  path = out_dir / "results.json"
  path.write_text(json.dumps([r.to_dict() for r in results], indent=2))
  return path
