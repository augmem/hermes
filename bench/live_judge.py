"""Optional live phase: model answers from each packet, then a blind judge.

Requires an OpenAI-compatible endpoint:
  OPENAI_API_KEY   (required)
  OPENAI_MODEL     answering model (default gpt-5.4-mini)
  JUDGE_MODEL      judge model (default: same as OPENAI_MODEL)
  OPENAI_BASE_URL  default https://api.openai.com/v1

The judge sees anonymized answers in shuffled order per probe and never sees
provider names.
"""
from __future__ import annotations

import json
import os
import random
import ssl
import urllib.request
from typing import Any

ANSWER_SYSTEM = (
  "You are a careful assistant in a brand-new conversation. "
  "Do not invent facts about the user. "
  "If prior context is provided, use it when relevant. "
  "If you lack specifics, say you do not know."
)

JUDGE_SYSTEM = (
  "You are grading memory systems blind. For a user question you get the "
  "reference facts a perfect memory would supply, plus several assistant "
  "answers labeled with letters. Score each answer 0-10 for how correctly and "
  "completely it uses the reference facts (10 = all facts, nothing invented; "
  "0 = wrong or hallucinated). If the reference facts are empty, the correct "
  "behavior is admitting the answer is unknown: score honesty high and any "
  "invented specifics 0-2. Respond with JSON only: "
  '{"scores": {"A": n, ...}, "winner": "A", "reason": "..."}'
)


def _ssl_context() -> ssl.SSLContext:
  try:
    import certifi
    return ssl.create_default_context(cafile=certifi.where())
  except Exception:
    return ssl.create_default_context()


def chat(system: str, user: str, *, model: str, temperature: float = 0.0) -> tuple[str, dict]:
  base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
  payload: dict[str, Any] = {
    "model": model,
    "messages": [
      {"role": "system", "content": system},
      {"role": "user", "content": user},
    ],
  }
  if temperature is not None:
    payload["temperature"] = temperature
  req = urllib.request.Request(
    base + "/chat/completions",
    data=json.dumps(payload).encode(),
    headers={
      "Content-Type": "application/json",
      "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
    },
    method="POST",
  )
  with urllib.request.urlopen(req, timeout=180, context=_ssl_context()) as resp:
    body = json.loads(resp.read().decode())
  content = body["choices"][0]["message"]["content"]
  if isinstance(content, list):
    content = "\n".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
  return content.strip(), body.get("usage", {})


def answer_from_packet(question: str, packet: str, *, model: str) -> tuple[str, dict]:
  system = ANSWER_SYSTEM
  if packet.strip():
    system += "\n\nPrior context from durable memory:\n" + packet.strip()
  return chat(system, question, model=model)


def judge_probe(
  question: str,
  reference: list[str],
  answers: dict[str, str],
  *,
  model: str,
  rng: random.Random,
) -> dict:
  """Blind-judge one probe. ``answers`` maps provider -> answer text."""
  providers = list(answers)
  rng.shuffle(providers)
  labels = {chr(ord("A") + i): p for i, p in enumerate(providers)}
  blocks = "\n\n".join(
    f"Answer {label}:\n{answers[provider] or '(empty answer)'}"
    for label, provider in labels.items()
  )
  ref = "\n".join(f"- {r}" for r in reference) if reference else "(none — the fact was never stated)"
  prompt = f"Question:\n{question}\n\nReference facts:\n{ref}\n\n{blocks}"
  raw, _ = chat(JUDGE_SYSTEM, prompt, model=model)
  raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
  verdict = json.loads(raw)
  scores = {labels[l]: s for l, s in (verdict.get("scores") or {}).items() if l in labels}
  winner = labels.get(verdict.get("winner", ""), "")
  if scores and winner:
    top = max(scores.values())
    if list(scores.values()).count(top) > 1:
      winner = "tie"
  return {
    "scores": scores,
    "winner": winner,
    "reason": verdict.get("reason", ""),
    "label_map": labels,
  }
