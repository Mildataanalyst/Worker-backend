# V62 — Free-tier repair storage recovery

This release fixes repair-run disk amplification without requiring a larger Railway volume.

## What changed

- Existing V61 repair runs are compacted automatically when the worker starts.
- Reproducible ZIPs, aggregate JSONL/Markdown packets, category packets, and stale temp files are removed from repair folders.
- Full copied source dossiers are converted to lightweight repair deltas.
- Unchanged copied dossiers are removed and read from the original persistent source run instead.
- New repair runs never copy the original `ngo_research_packs` tree.
- Repair checkpoints store only the changed official-site evidence and synthesis overlay.
- Repair exports are built only when downloaded, on ephemeral container storage, and deleted after delivery.
- No new Serper searches are introduced.
- Existing repair progress remains resumable.

## Recovery steps

1. Deploy V62 to the same Railway worker service and keep the existing volume mounted at the same path.
2. Allow the worker to restart. Startup compaction runs automatically.
3. Open the existing repair run and press Resume.
4. After completion, download the repaired export normally. The ZIP is generated on demand without occupying the persistent volume.

## Validation

- 26 automated tests pass.
- Includes tests for V61 run compaction, source-plus-delta merging, and off-volume repair exports.
