# Launch kit

## One-line positioning

**Cortext for Hermes gives your agent durable local memory in one Git install.**

## GitHub release title

`Cortext for Hermes v0.1.0 — local memory, zero Python dependencies`

## GitHub release body

Hermes plugins are Git clones, not Python environments. Cortext for Hermes
ships the whole local runtime: a standard-library `ctypes` adapter, native
libraries for macOS, Linux, and Windows, plus its required model assets.

```bash
hermes plugins install augmem/hermes --enable
hermes config set memory.provider cortext
```

It stays invisible to the model: no memory tools, no prompt branding, just
useful recalled context. A live cold-start Hermes E2E verified that a fresh
session recalls a fact from SQLite while a no-memory control does not.

## Social post

Hermes plugins don’t run `pip install`. So we made memory installable anyway.

`augmem/hermes` ships Cortext as a self-contained Git plugin: native runtime,
model assets, checksum verification, local SQLite memory, and zero
model-visible tools.

Fresh-session E2E: the no-memory control knew nothing; the Cortext-backed
Hermes session recalled the exact fact from disk.

```bash
hermes plugins install augmem/hermes --enable
hermes config set memory.provider cortext
```

## Launch checklist

- Create the public `augmem/hermes` repository and push `main`.
- Set `assets/cortext-hermes-social-preview.png` as the GitHub social preview.
- Create the `v0.1.0` GitHub release using the release body above.
- Run the `Build and verify Cortext vendor artifacts` workflow and attach its
  target artifacts/checksums to the release.
- Share the social post with the social-preview banner.
