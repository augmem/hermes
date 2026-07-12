# Hermes memory-provider benchmark

Head-to-head comparison of Hermes Agent memory providers, driven through the
**same `MemoryProvider` seams Hermes itself calls** (`prefetch` →
`on_turn_start` → `sync_turn` → `queue_prefetch` → `on_session_end` →
`shutdown`), with a full provider shutdown between sessions so recall must
come from durable storage — never from process state or chat history.

## Results (2026-07-12, Hermes 0.15.2 provider ABI, clean run)

Scenario: 4 sessions, 20 turns, 6 cold-start probes (including one
correction/supersession test and one hallucination-bait probe that was never
answered in the transcript). Fresh state for every provider, unique per-run
user identity so cloud-backed providers cannot carry memories across runs.

| Provider | Packet recall | Stale leaks | Packet tokens (median) | Standing overhead tok/turn | Effective tok/turn | Median recall ms | Net connections (ingest/recall) | Offline recall | Model-visible tools |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| **cortext** | **8/14 facts** | 1 | **97** | **0** | **97** | **15** | **0/0** | **6/6 probes** | **0** |
| mem0 (60.5K★, most popular) | 8/14 facts | 1 | 131 | 304 | 435 | 459 | 9/1 | 0/6 probes | 3 |
| tencentdb (Tencent Cloud, 4-tier) | 4/14 facts | 1 | 970 | 417 | 1387 | 169 | 0/0¹ | n/a (sidecar)¹ | 2 |
| holographic (built-in default) | 0/14 facts | 0 | 0 | 632 | 632 | 0 | 0/0 | 0/6 probes | 2 |
| holographic-tools (steelman) | 0/14 facts | 0 | 0 | 632 | 632 | 0 | 0/0 | 0/6 probes | 2 |

¹ TencentDB's provider talks to a loopback Node Gateway sidecar; the sidecar's
own network use (OpenAI embeddings + LLM extraction for every capture) is not
metered by the in-process socket counter, and an in-process "offline" test is
meaningless for it.

**Standing overhead** is what the provider injects into *every* model call
regardless of recall: its tool JSON schemas plus its branded system-prompt
block (measured live, estimated at 4 chars/token). Cortext exposes no tools
and no system-prompt block, so its per-turn cost is the packet alone: **97
effective tokens per turn versus Mem0's 435 (4.5×) and TencentDB's 1387
(14×)** — at equal-or-better packet recall — while recalling ~30× faster
than Mem0, fully offline, with zero LLM calls spent maintaining memory.
Cortext, Mem0, and TencentDB each leaked one superseded fact fragment on the
correction probe. Tool-call *round trips* (a model invoking `mem0_search`
etc. and re-prompting) are additional and not counted here.

### Blind judge (gpt-5.4-mini answers from each packet, anonymized)

| Probe | control | cortext | holographic | holographic-tools | mem0 | tencentdb | Winner |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| vet-supersession | 9 | 9 | 9 | 8 | 2 | 10 | tencentdb |
| deploy-process | 1 | 10 | 1 | 1 | 10 | 8 | tie |
| auth-owner | 1 | 10 | 1 | 1 | 1 | 1 | cortext |
| travel-plans | 2 | 0 | 2 | 2 | 2 | 8 | tencentdb |
| language-preference | 1 | 1 | 1 | 1 | 10 | 1 | mem0 |
| unknown-bait | 10 | 10 | 10 | 10 | 10 | 10 | tie |

Total scores: cortext 40, tencentdb 38, mem0 35, control 24. Answer quality
across the three real memory systems is competitive on this small scenario
(single judge rep — noisy; scores for the same probe visibly vary between
runs). The stable, structural differences are the cost columns above:
TencentDB buys its wins with 970-token packets and an LLM+embedding call on
every capture; Cortext delivers competitive answers from 97-token packets
with no LLM in the loop at all.

## Method

- Every provider replays the **identical scripted transcript**
  ([scenario.py](scenario.py)) — fixed user *and* assistant turns, so the only
  variable is the memory backend (standard replay methodology).
- Probes run in **fresh cold-start provider instances** on the same durable
  store, with natural-language questions passed to `prefetch()` — exactly
  what Hermes passes (the user's message).
- **Packet recall** counts expected fact groups present in the returned
  context packet. **Stale leaks** counts superseded facts (the moved vet
  appointment) still present.
- **Offline recall** repeats every probe with outbound sockets disabled.
- **Net connections** counts outbound TCP connections (keep-alive reuse means
  requests ≥ connections).
- An optional live phase (set `OPENAI_API_KEY`) has a model answer each probe
  from each packet, plus a no-memory control, then blind-judges anonymized
  answers with a separate judge prompt.

## Caveats — read before quoting

- **Holographic** stores facts primarily via model-invoked `fact_store`
  tools; a seam-level replay has no model, so it captures only its
  `auto_extract` regexes. The `holographic-tools` steelman simulates a
  perfectly diligent model storing **every** user turn via its tool — it
  still scored 0/14 because its stage-1 retrieval is FTS5 with implicit AND:
  natural-sentence prefetch queries match nothing (single keywords do). In
  real use, quality depends on the model distilling good keyword queries.
- **Mem0** extracts facts server-side (their hosted platform); the harness
  waits 10s between sessions and 5s after the final one for eventual
  consistency. Slower settle could improve its recall slightly. Each run uses
  a unique user id so prior runs' cloud memories cannot leak in (an early run
  without this measured 13/14 for Mem0 — that number was contamination).
- **TencentDB** ran with `pipeline.everyNConversations: 2` (default 5) so
  L1 extraction fires within our short sessions, `bm25.language: en`
  (default is Chinese jieba), and OpenAI `text-embedding-3-small` +
  `gpt-5.4-mini` as its embedding/extraction backends. Its extraction spends
  an LLM call per pipeline pass — that cost is real but not metered here.
- The blind judge is a single rep per probe with anonymized, shuffled
  answers; scores for the same probe visibly vary between runs. Treat the
  judge table as directional; the cost/latency/privacy columns are the
  reproducible part.
- One scenario, one run, small N. This measures the automatic memory path
  under identical treatment; it is not a claim about every workload. The
  scenario was written before any provider was run and was not tuned
  afterward.

## Reproduce

```bash
python3 -m venv .venv && .venv/bin/pip install hermes-agent mem0ai certifi
echo 'MEM0_API_KEY=...' >> .env                 # only needed for mem0
echo 'OPENAI_API_KEY=...' >> .env               # judge phase + tencentdb backends
.venv/bin/python -m bench.run_bench --providers cortext,holographic,holographic-tools,mem0
```

To include TencentDB-Agent-Memory, start its Gateway sidecar first:

```bash
git clone https://github.com/TencentCloud/TencentDB-Agent-Memory tdai && cd tdai
npm install --omit=dev
cat > tdai-gateway.json <<'EOF'
{"llm": {"baseUrl": "https://api.openai.com/v1", "apiKey": "<KEY>", "model": "gpt-5.4-mini"},
 "memory": {"bm25": {"enabled": true, "language": "en"},
            "embedding": {"enabled": true, "provider": "openai", "baseUrl": "https://api.openai.com/v1",
                          "apiKey": "<KEY>", "model": "text-embedding-3-small", "dimensions": 1024},
            "pipeline": {"everyNConversations": 2}}}
EOF
TDAI_GATEWAY_CONFIG=$PWD/tdai-gateway.json TDAI_DATA_DIR=$PWD/bench-data npx tsx src/gateway/server.ts &
cd - && TDAI_CHECKOUT=/path/to/tdai OPENAI_MODEL=gpt-5.4-mini \
  .venv/bin/python -m bench.run_bench --providers cortext,holographic,holographic-tools,mem0,tencentdb
```

Outputs land in `bench/results/`: raw per-probe packets in `results.json`,
summary in `REPORT.md`, blind-judge verdicts in `judgments.json` (live phase).
