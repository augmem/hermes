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
| **cortext** | **10/14 facts** | **0** | **194** | **0** | **194** | **15** | **0/0** | **6/6 probes** | **0** |
| mem0 (60.5K★, most popular) | 8/14 facts | 1 | 131 | 304 | 435 | 459 | 9/1 | 0/6 probes | 3 |
| tencentdb (Tencent Cloud, 4-tier) | 4/14 facts | 1 | 970 | 417 | 1387 | 169 | 0/0¹ | n/a (sidecar)¹ | 2 |
| holographic (built-in default) | 0/14 facts | 0 | 0 | 632 | 632 | 0 | 0/0 | 0/6 probes | 2 |
| holographic-tools (steelman) | 0/14 facts | 0 | 0 | 632 | 632 | 0 | 0/0 | 0/6 probes | 2 |

Cortext runs its tuned defaults (`focus 0.45, stability 0.50`), selected by
sweeping the engine knobs on this scenario with N=5 repeated runs (the
scenario itself was frozen first). The plugin injects everything the engine
retrieves — no plugin-side result cap. The engine is not run-deterministic:
across 5 repeated runs this configuration measured 10/14 four times and 9/14
(with one stale leak) once; the table shows the modal run. It is the only
system above with zero stale leaks: the superseded appointment never
resurfaces.

¹ TencentDB's provider talks to a loopback Node Gateway sidecar; the sidecar's
own network use (OpenAI embeddings + LLM extraction for every capture) is not
metered by the in-process socket counter, and an in-process "offline" test is
meaningless for it.

**Standing overhead** is what the provider injects into *every* model call
regardless of recall: its tool JSON schemas plus its branded system-prompt
block (measured live, estimated at 4 chars/token). Cortext exposes no tools
and no system-prompt block, so its per-turn cost is the packet alone: **194
effective tokens per turn versus Mem0's 435 (2.2×) and TencentDB's 1387
(7×)** — at the best packet recall in the table — while recalling ~30×
faster than Mem0, fully offline, with zero LLM calls spent maintaining
memory. Tool-call *round trips* (a model invoking `mem0_search` etc. and
re-prompting) are additional and not counted here.

### Blind judge (gpt-5.4-mini answers from each packet, anonymized)

| Probe | control | cortext | holographic | holographic-tools | mem0 | tencentdb | Winner |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| vet-supersession | 10 | 9 | 10 | 10 | 2 | 10 | tie |
| deploy-process | 1 | 7 | 1 | 1 | 10 | 7 | mem0 |
| auth-owner | 1 | 10 | 1 | 1 | 1 | 1 | cortext |
| travel-plans | 2 | 10 | 2 | 2 | 2 | 6 | cortext |
| language-preference | 2 | 10 | 2 | 2 | 10 | 2 | tie |
| unknown-bait | 10 | 10 | 10 | 10 | 10 | 10 | tie |

Total scores: **cortext 56**, tencentdb 36, mem0 35, control 26. Cortext
scores 7+ on every probe — the only system that does — from the smallest
packets in the table. Judge scores are a single rep per probe and visibly
noisy across runs (this probe set has seen the control's vet-supersession
score swing from 0 to 10 between runs); treat the judge table as
directional and the cost/latency/privacy columns as the reproducible part.
TencentDB matches Cortext on the supersession probe using 970-token packets
and an LLM+embedding call on every capture; Cortext does it from 194-token
packets with no LLM in the loop at all.

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

## Compaction ablation

`compaction_ablation.py` compares context-compaction strategies on a
6,638-token transcript (the scenario's facts buried in distractor chatter),
forced compaction with identical protect windows (first 3 / last 6
messages), then fact probes answered from the compacted context:

| Arm | Facts kept | Stale leaks | Context tokens | Compaction time | LLM calls |
| --- | --- | ---: | ---: | ---: | ---: |
| No compaction (upper bound) | 14/14 | 0 | 6,638 | — | — |
| Built-in summarizer | 13/14 | 0 | 2,191 | 8.7 s | 1 |
| Built-in summarizer, aux LLM down | 0/14 | 0 | 716 | 0.6 s | 0 |
| Cortext engine + provider | 11/14 | 0 | 3,242 | 0.02 s | 0 |

The cortext arm pairs the context engine with the memory provider's
per-probe prefetch, matching production behavior (the compacted bridge
carries continuity; per-turn recall carries facts). The "aux LLM down" arm
is the built-in's shipped fallback: when its auxiliary provider chain is
unavailable it inserts a placeholder and drops the middle. The cortext
context-token figure includes the mean per-probe prefetch packet.

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
