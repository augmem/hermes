from __future__ import annotations

import unittest
import tempfile
from typing import Any

from cortext_native import Retention
from provider import CortextMemoryProvider


class FakeEngine:
  def __init__(self) -> None: self.calls: list[tuple[str, Any]] = []; self.responses: list[dict[str, Any]] = []; self.flushed = 0; self.closed = 0
  def process_text(self, text: str, source: str, **kwargs: Any) -> dict[str, Any]: self.calls.append((text, kwargs.get("retention"))); return self.responses.pop(0) if self.responses else {"retrieved_memory": []}
  def process_audio(self, *args: Any, **kwargs: Any) -> dict[str, Any]: self.calls.append(("audio", kwargs.get("retention"))); return {}
  def process_image(self, *args: Any, **kwargs: Any) -> dict[str, Any]: self.calls.append(("image", kwargs.get("retention"))); return {}
  def flush(self) -> None: self.flushed += 1
  def consolidate(self) -> dict[str, Any]: return {}
  def close(self) -> None: self.closed += 1


class ProviderTests(unittest.TestCase):
  def setUp(self) -> None:
    self.provider = CortextMemoryProvider(); self.engine = FakeEngine(); self.provider._engine = self.engine

  def test_is_silent_to_the_model(self) -> None:
    self.assertEqual(self.provider.get_tool_schemas(), []); self.assertEqual(self.provider.system_prompt_block(), "")

  def test_prefetch_returns_unbranded_context(self) -> None:
    self.engine.responses = [{"retrieved_memory": [{"text": "vet is July 14"}]}]
    block = self.provider.prefetch("when is the vet?")
    self.assertIn("vet is July 14", block); self.assertNotIn("cortext", block.lower())

  def test_turn_ingest_is_durable_and_shutdown_closes_engine(self) -> None:
    self.provider.on_turn_start(1, "remember this")
    self.provider._drain(); self.assertIn(("remember this", Retention.DURABLE), self.engine.calls)
    self.provider.shutdown(); self.assertEqual(self.engine.closed, 1)

  def test_config_round_trip_and_source_provenance(self) -> None:
    with tempfile.TemporaryDirectory() as home:
      self.provider.save_config({"focus": .7, "db_path": "$HERMES_HOME/custom.sqlite"}, home)
      self.assertEqual(self.provider._config["focus"], .7)
    self.provider._session_id, self.provider._user_id, self.provider._agent_id = "s/1", "alice smith", "hermes"
    self.assertEqual(self.provider._source("user", "turn", "2"), "hermes/user/alice_smith/s_1/turn/2")

  def test_working_memory_backfills_only_after_compression(self) -> None:
    wm_packet = {"retrieved_memory": [{"text": "durable fact"}], "working_memory": [{"text": "recent detail"}]}
    self.engine.responses = [dict(wm_packet)]
    self.assertNotIn("recent detail", self.provider.prefetch("q1"))
    self.provider.on_pre_compress([{"role": "user", "content": "latest"}])
    self.engine.responses = [dict(wm_packet)]
    block = self.provider.prefetch("q2")
    self.assertIn("durable fact", block); self.assertIn("recent detail", block)
    self.engine.responses = [dict(wm_packet)]
    self.assertNotIn("recent detail", self.provider.prefetch("q3"))

  def test_tool_results_are_ingested_durable_and_truncated(self) -> None:
    self.provider._config["tool_result_max_chars"] = 200
    self.provider.on_post_tool_call(tool_name="terminal", args={"command": "backup.sh"}, result="Restore took 42 minutes. " + "x" * 500, session_id="s")
    self.provider._drain()
    text, retention = self.engine.calls[0]
    self.assertIn("Restore took 42 minutes", text); self.assertLessEqual(len(text), 200); self.assertEqual(retention, Retention.DURABLE)
    self.provider._config["seam_tool_results"] = False
    self.provider.on_post_tool_call(tool_name="terminal", args={}, result="ignored")
    self.provider._drain(); self.assertEqual(len(self.engine.calls), 1)

  def test_user_turn_not_double_ingested(self) -> None:
    self.provider._session_id = "s"
    self.provider.on_turn_start(1, "remember this fact")
    self.provider.sync_turn("remember this fact", "stored")
    self.provider._drain()
    texts = [call[0] for call in self.engine.calls]
    self.assertEqual(texts.count("remember this fact"), 1); self.assertIn("stored", texts)

  def test_registers_memory_provider_and_tool_result_hook(self) -> None:
    import provider
    entries: list[Any] = []; hooks: list[str] = []
    class Context:
      def register_memory_provider(self, item: Any) -> None: entries.append(item)
      def register_hook(self, name: str, callback: Any) -> None: hooks.append(name)
    provider.register(Context())
    self.assertEqual(entries[0].name, "cortext"); self.assertEqual(hooks, ["post_tool_call"])
