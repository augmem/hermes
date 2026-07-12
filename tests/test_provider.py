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

  def test_action_gate_uses_natural_and_only_blocks_interrupt(self) -> None:
    self.engine.responses = [{"should_interrupt": True, "retrieved_memory": [{"text": "do not delete production"}]}]
    result = self.provider.pre_tool_call("terminal", {"command": "rm -rf prod"}, "task")
    self.assertEqual(result and result["action"], "block"); self.assertIn("do not delete production", result["message"] if result else "")
    self.assertEqual(self.engine.calls[0][1], Retention.NATURAL)
    self.engine.responses = [{"should_interrupt": False, "retrieved_memory": [{"text": "ignored"}]}]
    self.assertIsNone(self.provider.pre_tool_call("terminal", {"command": "pwd"}))

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

  def test_registers_memory_provider_and_only_action_hook(self) -> None:
    import provider
    entries: list[Any] = []; hooks: list[str] = []
    class Context:
      def register_memory_provider(self, item: Any) -> None: entries.append(item)
      def register_hook(self, name: str, callback: Any) -> None: hooks.append(name)
    provider.register(Context())
    self.assertEqual(entries[0].name, "cortext"); self.assertEqual(hooks, ["pre_tool_call"])
