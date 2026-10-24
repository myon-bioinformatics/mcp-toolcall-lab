# JSONL log archive

`src/mcp_toolcall_lab/jsonl_log_archive.py` (standard library only) packs a JSONL log into one file:
128-line LZMA blocks with CRC32, a literal 2–3-gram → block index, an ID table
(PR numbers, 40-char SHAs, standalone 7-char SHAs, `bc-` ids), a plain-text summary,
an optional 128-bit similarity section (`build --sim`), and a SHA-256 footer.

```
mcp-jsonl-log-archive build log.jsonl -o log.jlogarc [--sim]
mcp-jsonl-log-archive search log.jlogarc 'decision=merge'   # same lines as grep -nF
mcp-jsonl-log-archive get log.jlogarc --id '#160'           # also 160, PR#160, pull/160, SHA prefix (7+ hex)
mcp-jsonl-log-archive similar log.jlogarc '日本語' -k 5      # needs build --sim
mcp-jsonl-log-archive summary log.jlogarc
```

`get --id` exits 1 when nothing matches; a corrupted archive exits 2.

## Size

The archive stores the compressed text **plus** the index, ID table and summary, so its
size depends on the content:

- Repetitive, text-heavy agent transcripts: about 11% of the source (7,478 lines, 1,995,956 → 222,722 bytes).
- High-entropy content (random SHAs, ids, hashes on every line): the index can make the archive
  **larger than the source**; a 3,000-line synthetic log with random SHAs gave 493,673 → 533,397 bytes (1.08×).

Measure with `build`, which prints `ratio`, before relying on a figure.
