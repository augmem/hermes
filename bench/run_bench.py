"""Run the Hermes memory-provider benchmark.

Usage (from the repo root, in a venv with hermes-agent installed):

  python -m bench.run_bench --providers cortext,holographic,mem0 --out bench/results

Requires per provider:
  cortext      nothing (vendored runtime in this repo)
  holographic  nothing (ships inside hermes-agent)
  mem0         MEM0_API_KEY (read from .env) + `pip install mem0ai`

Optional live phase (runs automatically when OPENAI_API_KEY is set):
  answers each probe from each provider's packet with OPENAI_MODEL and
  blind-judges the anonymized answers with JUDGE_MODEL.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
  sys.path.insert(0, str(REPO_ROOT))

from bench import harness, scenario
from bench.harness import FACTORIES, SETTLE, ProviderResult


def run_live_phase(results: list[ProviderResult], out_dir: Path) -> dict | None:
  if not os.environ.get("OPENAI_API_KEY"):
    return None
  from bench import live_judge

  answer_model = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
  judge_model = os.environ.get("JUDGE_MODEL", answer_model)
  rng = random.Random(20260712)
  by_provider = {r.provider: r for r in results if r.ok}

  judgments: dict[str, dict] = {}
  for p_index, probe in enumerate(scenario.PROBES):
    answers: dict[str, str] = {}
    # No-memory control shares the same answering model and empty packet.
    answers["control"], _ = live_judge.answer_from_packet(
      probe["question"], "", model=answer_model)
    for name, res in by_provider.items():
      pr = res.probes[p_index]
      pr.answer, _ = live_judge.answer_from_packet(
        probe["question"], pr.packet, model=answer_model)
      pr.answer_matched, pr.answer_missed = harness.match_groups(
        pr.answer, probe.get("expected", []))
      answers[name] = pr.answer
    reference = ["/".join(g) for g in probe.get("expected", [])]
    judgments[probe["id"]] = live_judge.judge_probe(
      probe["question"], reference, answers, model=judge_model, rng=rng)
    print(f"  judged {probe['id']}: winner={judgments[probe['id']]['winner']}")

  (out_dir / "judgments.json").write_text(json.dumps(judgments, indent=2))
  return judgments


def markdown_report(results: list[ProviderResult], judgments: dict | None) -> str:
  lines = ["# Hermes memory provider benchmark", ""]
  lines.append(f"Scenario: {len(scenario.SESSIONS)} sessions, "
               f"{sum(len(s) for s in scenario.SESSIONS)} turns, "
               f"{len(scenario.PROBES)} cold-start probes. "
               "Identical scripted transcript per provider; full provider "
               "shutdown between sessions (cold-start recall only).")
  lines.append("")
  lines.append("| Provider | Packet recall | Stale leaks | Median packet tokens | Standing overhead tok/turn | Effective tok/turn | Median recall ms | Net calls (ingest/recall) | Offline recall | Model-visible tools |")
  lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |")
  for r in results:
    if not r.ok:
      lines.append(f"| {r.provider} | FAILED: {r.error} | | | | | | | | |")
      continue
    scored = [p for p in r.probes if not _probe(p.probe_id).get("expect_unknown")]
    total = sum(len(p.matched_groups) + len(p.missed_groups) for p in scored)
    hit = sum(len(p.matched_groups) for p in scored)
    stale = sum(len(p.stale_groups) for p in r.probes)
    tokens = sorted(p.packet_tokens for p in r.probes)
    lat = sorted(p.recall_latency_ms for p in r.probes)
    offline = sum(1 for p in r.probes if p.offline_ok)
    overhead = harness.est_tokens(" " * (r.tool_schema_chars + r.system_prompt_chars))
    median_tokens = tokens[len(tokens) // 2]
    offline_cell = ("n/a (sidecar)" if r.provider in harness.OFFLINE_NOT_MEASURABLE
                    else f"{offline}/{len(r.probes)} probes")
    lines.append(
      f"| {r.provider} | {hit}/{total} facts | {stale} | "
      f"{median_tokens} | {overhead} | {median_tokens + overhead} | "
      f"{lat[len(lat) // 2]:.0f} | "
      f"{r.ingest_network_connections}/{r.recall_network_connections} | "
      f"{offline_cell} | {r.tool_schema_count} |")
  if judgments:
    lines += ["", "## Blind judge (LLM answers from each packet)", ""]
    lines.append("| Probe | " + " | ".join(sorted({p for j in judgments.values() for p in j['scores']})) + " | Winner |")
    providers = sorted({p for j in judgments.values() for p in j["scores"]})
    lines[-1] = "| Probe | " + " | ".join(providers) + " | Winner |"
    lines.append("| --- | " + " | ".join("---:" for _ in providers) + " | --- |")
    wins: dict[str, int] = {}
    for pid, j in judgments.items():
      row = " | ".join(str(j["scores"].get(p, "—")) for p in providers)
      lines.append(f"| {pid} | {row} | {j['winner']} |")
      wins[j["winner"]] = wins.get(j["winner"], 0) + 1
    lines += ["", "Wins: " + ", ".join(f"{k}: {v}" for k, v in sorted(wins.items(), key=lambda kv: -kv[1]))]
  return "\n".join(lines) + "\n"


def _probe(probe_id: str) -> dict:
  return next(p for p in scenario.PROBES if p["id"] == probe_id)


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--providers", default="cortext,holographic,mem0")
  parser.add_argument("--out", default="bench/results")
  parser.add_argument("--homes", default="", help="base dir for provider homes (default: temp dir)")
  parser.add_argument("--no-offline", action="store_true", help="skip the sockets-disabled recall phase")
  args = parser.parse_args()

  harness.load_dotenv()
  # Unique per-run identity so cloud-backed providers (Mem0) can't carry
  # memories from a previous benchmark run into this one.
  scenario.USER_ID = f"bench-user-{uuid.uuid4().hex[:10]}"
  print(f"run identity: {scenario.USER_ID}")
  out_dir = REPO_ROOT / args.out if not Path(args.out).is_absolute() else Path(args.out)
  homes_base = Path(args.homes) if args.homes else Path(tempfile.mkdtemp(prefix="hermes-membench-"))

  results: list[ProviderResult] = []
  for name in [p.strip() for p in args.providers.split(",") if p.strip()]:
    if name not in FACTORIES:
      print(f"unknown provider: {name} (known: {', '.join(FACTORIES)})")
      return 2
    print(f"[{name}] replaying {len(scenario.SESSIONS)} sessions...")
    started = time.perf_counter()
    result = harness.run_provider(
      name,
      FACTORIES[name],
      homes_base / name,
      offline_check=not args.no_offline and name not in harness.OFFLINE_NOT_MEASURABLE,
      **SETTLE.get(name, {}),
    )
    results.append(result)
    status = "ok" if result.ok else f"FAILED: {result.error}"
    print(f"[{name}] {status} in {time.perf_counter() - started:.1f}s")

  path = harness.save_results(results, out_dir)
  judgments = None
  if any(r.ok for r in results):
    if os.environ.get("OPENAI_API_KEY"):
      print("live phase: answering + blind judging...")
      judgments = run_live_phase(results, out_dir)
      harness.save_results(results, out_dir)  # persist answers
    else:
      print("live phase skipped (set OPENAI_API_KEY to enable)")

  report = markdown_report(results, judgments)
  (out_dir / "REPORT.md").write_text(report)
  print(f"\nresults: {path}\nreport: {out_dir / 'REPORT.md'}\n")
  print(report)
  return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
  raise SystemExit(main())
