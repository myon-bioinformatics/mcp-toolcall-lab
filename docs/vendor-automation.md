# Public vendor placement in CI

Push and pull-request CI verify vendored bytes against [`vendor.lock.json`](../vendor.lock.json)
using the shared stdlib tool from
[`myon-bioinformatics/myon-bioinformatics`](https://github.com/myon-bioinformatics/myon-bioinformatics)
at commit `f907560c2b9b3f7a0f78f4434f547f91cb2ef954` (parent `vendor_sync.py`).

The `vendor-locked` job checks the lock, deletes every locked destination, materializes
the exact upstream commits, and checks again. The primary `test` job repeats
`vendor_sync.py check` on the checked-in tree before running the Python and Deno suites.
There is no scheduled or dispatch-driven `promote` / `update` automation in this
repository; lock changes land through normal commits.

`vendor.lock.json` (schema `vendor-lock/1`) is the only provenance record for vendored
files. Each entry's `ref` is a full 40-character commit SHA equal to `commit`.
`tests/test_vendor_lock.py` cross-checks every destination's git blob SHA-1 and SHA-256
against the lock.

ALM agents can reproduce verification in a disposable checkout:

```bash
git clone https://github.com/myon-bioinformatics/myon-bioinformatics.git .vendor-sync-tools
git -C .vendor-sync-tools checkout --detach f907560c2b9b3f7a0f78f4434f547f91cb2ef954
python -S .vendor-sync-tools/vendor_sync.py check --manifest vendor.lock.json
```

Run the existing Python suite with its test-only dependencies after verification.
