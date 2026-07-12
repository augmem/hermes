# Cortext for Hermes

![Cortext memory flowing into Hermes](assets/cortext-hermes-social-preview.png)

**Your Hermes agent forgets everything the moment a session ends. Two commands
fix that — permanently, locally, invisibly.**

```bash
hermes plugins install augmem/hermes --enable
hermes config set memory.provider cortext
```

`augmem/hermes` gives [Hermes Agent](https://hermes-agent.nousresearch.com/)
durable memory backed by [Cortext](https://github.com/augmem/cortext), a local
neuromorphic memory engine. Everything your agent learns lands in a SQLite
file on your machine. Nothing is sent to a memory API. No LLM summarizes your
conversations. And the model never sees a memory tool — recalled facts simply
arrive as prior context, as if the agent just… remembered.

## What changes

Tell Hermes something once. Close the session. Days later, in a brand-new
session, it still knows.

```text
── Monday ────────────────────────────────────────────────
you    › We deploy from the `release` branch, never `main`.
         Staging is at 10.0.4.7 and the on-call runbook is in ops/RUNBOOK.md.
hermes › Got it.
         (session ends — nothing pinned, no notes file, no tool calls)

── Friday, fresh session ─────────────────────────────────
you    › Ship the fix.
hermes › Cutting the release from `release` — you deploy from there,
         not `main`. I'll verify against staging at 10.0.4.7 first.
```

This is what that unlocks in practice:

- **A coding agent that knows your project.** Conventions, gotchas, "we tried
  that and it broke prod" — retained across sessions without you re-explaining
  or maintaining a notes file.
- **A personal assistant that actually knows you.** Allergies, appointments,
  preferences, the names that matter. Because memory is a local file, this is
  finally private enough for the personal stuff.
- **An ops agent with institutional memory.** Which host is flaky, what the
  last incident looked like, which dashboard is the real one.
- **Corrections that stick.** Cortext supersedes stale facts instead of
  hoarding contradictions — tell it the appointment moved, and the old time
  stops being recalled.
- **Compaction that doesn't lobotomize.** The plugin ships a full Hermes
  context engine: compaction becomes a 20-millisecond local memory
  operation instead of an auxiliary-LLM summarization call. Nothing is
  irreversibly lost — every turn is already in the durable store — and it
  keeps working when cloud LLMs are down (which is exactly when the
  built-in summarizer silently drops your history).

Memory management is automatic. There is no "save this" command, no memory
tool for the model to call (or forget to call), and no LLM in the loop
deciding what to keep. Cortext's write gate, decay, and consolidation decide —
deterministically, on your machine.

## Benchmarked against the providers people actually use

Same scripted 4-session transcript through every provider's real Hermes
seams, cold-start probes, blind LLM judging. Full method, caveats, and
reproduction steps in [bench/README.md](bench/README.md); raw packets and
verdicts in [bench/results/](bench/results/).

| | **cortext** | mem0 (most popular, 60.5K★) | tencentdb (Tencent Cloud) | holographic (built-in default) |
| --- | --- | --- | --- | --- |
| Facts recalled (packet) | **10/14** | 8/14 | 4/14 | 0/14 |
| Superseded facts leaked | **0** | 1 | 1 | 0 |
| Effective tokens per turn | **194** | 435 | 1,387 | 632 |
| Median recall latency | **15 ms** | 459 ms | 169 ms | — |
| Blind-judge answer score | **56** | 35 | 36 | 26 (= no memory at all) |
| Works offline | **yes** | no | no (LLM extraction) | yes |
| LLM calls to maintain memory | **0** | every write | every write | 0 |
| Model-visible tools | **0** | 3 | 2 | 2 |

### Compaction ablation

When context fills up, Hermes's built-in compactor summarizes history with
an auxiliary LLM. The Cortext context engine replaces that with a local
memory operation. Forced compaction of a 6,638-token transcript, then fact
probes against the compacted context:

| | Facts kept | Compaction time | LLM calls |
| --- | --- | ---: | ---: |
| No compaction (upper bound) | 14/14 | — | — |
| Built-in summarizer | 13/14 | 8.7 s | 1 |
| Built-in summarizer, **aux LLM down** | **0/14** | 0.6 s | 0 |
| **Cortext engine** | **11/14** | **0.02 s** | **0** |

The built-in keeps one more fact — when its cloud LLM chain is healthy.
When it isn't, its shipped fallback silently drops your history. Cortext's
compaction is ~400× faster, free, offline, and has no failure mode that
costs you your memory. Reproduce: `python -m bench.compaction_ablation`.

### Cold-start recall, verified live

Not a demo script — a live control/treatment test against Hermes 0.15.2 with
`gpt-5.4-mini`:

1. A first Hermes session stored a unique medical fact, then shut down.
2. A new **control** session, with memory disabled and no prior chat history,
   did not know the fact.
3. A second new session, with only Cortext's reopened SQLite database,
   recalled the secret identifier, treatment, and appointment details —
   without using the word "Cortext."

That is durable retrieval from disk, not conversation-history leakage.

## Private by architecture, not by policy

- Memories live in **one SQLite file** (default:
  `$HERMES_HOME/cortext.sqlite`). Back it up, inspect it, delete it — it's
  yours.
- **Zero network calls.** No memory SaaS, no embedding API, no runtime
  downloads. The local AIST encoder ships in the plugin and is
  checksum-verified before use.
- Works **fully offline** immediately after Git installation.

## Invisible to the model

This provider is intentionally silent:

- no `cortext_*` tools for the model;
- no system-prompt branding;
- recalled facts arrive as plain prior context.

Text, WAV audio, and non-interlaced 8-bit PNG images work with no Python
dependencies. Other image containers are skipped rather than silently adding
or downloading a decoder.

## Install

```bash
hermes plugins install augmem/hermes --enable
hermes config set memory.provider cortext
```

Restart Hermes (or its gateway) after installing. `--enable` enables the
plugin; it does **not** select Hermes's active memory provider, so the second
command remains required.

The clone is intentionally large (~135 MB after model reassembly) — that is
the price of a plugin that is ready to run offline the moment the clone
finishes. See [How it ships](#how-it-ships) for why.

## Configuration

Optional. Drop a `cortext.json` in your Hermes home
(`$HERMES_HOME/cortext.json`) to tune behavior; every key has a sensible
default:

```json
{
  "db_path": "$HERMES_HOME/cortext.sqlite",
  "focus": 0.45,
  "sensitivity": 0.50,
  "stability": 0.50,
  "auto_consolidate": true,
  "ingest_media": true
}
```

`focus`, `sensitivity`, and `stability` are Cortext's three homeostatic
control knobs — retrieval selectivity, responsiveness to surprising input,
and preference for durable context. The engine decides how much to recall;
everything it returns (long-term retrieval plus active working memory) is
injected as-is.

## How it ships

Hermes installs Git plugins by cloning their files; it does not resolve
Python dependencies. This repository therefore ships the complete local
runtime:

- A standard-library-only `ctypes` adapter over the Cortext C API.
- Checked-in Cortext libraries for macOS arm64/x64, Linux x64/arm64, and
  Windows x64.
- The required AIST model and tokenizer, verified before use. The model is
  Git-native chunked and reassembled locally on first use, avoiding a Git LFS
  requirement and GitHub's 100 MB per-file limit.
- A generated SHA-256 manifest. The adapter refuses missing, unsupported, or
  tampered artifacts and never falls back to a system library or downloads
  code at runtime.

## Supported platforms

<!-- Supported platforms derived from vendor/manifest.json -->

| Platform | Architecture | Bundled library |
| --- | --- | --- |
| macOS | Apple Silicon | `libcortext.dylib` |
| macOS | Intel | `libcortext.dylib` |
| Linux | x64 | `libcortext.so` |
| Linux | arm64 | `libcortext.so` |
| Windows | x64 | `cortext.dll` |

The exact version, target names, paths, and SHA-256 values are in
[vendor/manifest.json](vendor/manifest.json). Artifact provenance is recorded
in [vendor/PROVENANCE.md](vendor/PROVENANCE.md).

## Under the hood

Cortext is a C++20 memory engine with a graph-native long-term store,
working memory, homeostatic control, and multimodal (text/audio/image)
embeddings in a single retrieval space — no LLM in the memory loop. In blind
LLM-judged benchmarks it matched or beat chat+RAG while using ~50× fewer
context tokens per turn. Architecture, benchmarks, and the research paper
live in the [Cortext repository](https://github.com/augmem/cortext).

## Verify or release

```bash
python -m unittest discover -s tests -v
python tools/generate_manifest.py --version 1.2.0 \
  --targets darwin-arm64 darwin-x64 linux-x64 linux-arm64 windows-x64
```

The release workflow builds target libraries using Zig 0.15.2, validates the
manifest, and runs the test suite. The maintained Python/PyPI integration
continues to live in the Cortext source repository; this repo is the
dependency-free Git-install route.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
