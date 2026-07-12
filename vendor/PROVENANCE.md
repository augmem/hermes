# Vendor artifact provenance

The native libraries in this directory are Cortext v1.2.0 release artifacts,
from `augmem_cortext-1.2.0-py3-none-any.whl` published with the upstream
`v1.2.0` release. They were extracted without installing or importing the
`augmem.cortext` Python package. Their target, file name, and SHA-256 are
declared in `manifest.json`; runtime loading verifies that manifest.

The AIST model and tokenizer are the v1.2.0 release assets with their upstream
published SHA-256 values. The 135 MB model is stored as ordinary Git chunks to
avoid Git LFS and GitHub's 100 MB per-file limit; the adapter verifies every
chunk and the reassembled model before the C API can use it. They are data
required by the C API, not runtime-downloaded code.
