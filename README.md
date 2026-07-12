# Cortext for Hermes

![Cortext memory flowing into Hermes](assets/cortext-hermes-social-preview.png)

**Give Hermes durable, local memory in one Git install — no `pip install`, no
runtime downloads, and no model-visible memory tools.**

`augmem/hermes` is a standalone [Hermes Agent](https://hermes-agent.nousresearch.com/)
memory-provider plugin. It recalls useful prior context before each model call,
stores interactions quietly in a local SQLite database, and can block a tool
action only when Cortext's existing interrupt signal has relevant context.

## Install

<!-- Git install and provider selection commands derived from plugin.yaml and Hermes provider name -->

```bash
hermes plugins install augmem/hermes --enable
hermes config set memory.provider cortext
```

Restart Hermes (or its gateway) after installing. `--enable` enables the
plugin; it does **not** select Hermes's active memory provider, so the second
command remains required.

## Why this exists

Hermes installs Git plugins by cloning their files; it does not resolve Python
dependencies. This repository therefore ships the complete local runtime:

- A standard-library-only `ctypes` adapter over the Cortext C API.
- Checked-in Cortext libraries for macOS arm64/x64, Linux x64/arm64, and
  Windows x64.
- The required AIST model and tokenizer, verified before use. The model is
  Git-native chunked and reassembled locally on first use, avoiding a Git LFS
  requirement and GitHub's 100 MB per-file limit.
- A generated SHA-256 manifest. The adapter refuses missing, unsupported, or
  tampered artifacts and never falls back to a system library or downloads
  code at runtime.

The clone is intentionally large (the local AIST model is about 135 MB after
reassembly). That is the tradeoff for a plugin that is ready to run offline
immediately after Git installation.

## What Hermes sees

Nothing product-branded. This provider is intentionally silent:

- no `cortext_*` tools for the model;
- no system-prompt branding;
- recalled facts arrive as plain prior context;
- action gating processes tool intent with `Retention.NATURAL` and returns
  Hermes's block directive only when Cortext says to interrupt and retrieved
  context can explain why.

Text, WAV audio, and non-interlaced 8-bit PNG images work with no Python
dependencies. Other image containers are skipped rather than silently adding
or downloading a decoder.

## Proven cold-start recall

<!-- Live E2E result derived from the verified Hermes 0.15.2 cold-start test -->

The plugin was tested in an isolated Hermes 0.15.2 environment with a live
`gpt-5.4-mini` control/treatment check:

1. A first Hermes session stored a unique medical fact, then shut down.
2. A new control session, with memory disabled and no prior chat history, did
   not know the fact.
3. A second new session, with only Cortext's reopened SQLite database, recalled
   the secret identifier, treatment, and appointment details — without using
   the word “Cortext.”

That is durable retrieval, not conversation-history leakage.

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
