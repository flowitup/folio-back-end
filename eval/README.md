# AI eval harness

The plan's M0 acceptance bar for the assistant pipeline: S1 (invoice extraction) and A1
(material identification) accuracy against a small, hand-labelled gold set.

```
uv run python -m scripts.ai_eval.run_eval --invoices eval/invoices --materials eval/materials [--limit N]
```

Needs a real `DEEPSEEK_API_KEY` in the environment (exits 2 with a clear message
otherwise) — this makes real, billed network calls, so it is never run in CI, only by
hand before a release or after touching the S1/A1 prompts.

## Building your own eval set

This repo ships **no real invoices or material photos** (they would either be fake test
data — useless for measuring real-world accuracy — or real documents, which cannot be
committed). To run the harness for real:

1. Drop `.pdf`/`.jpg`/`.jpeg`/`.png` files into `eval/invoices/` and `eval/materials/`
   (both directories are gitignored except this README and the two `gold.example.json`
   files below — your real files and gold labels never get committed).
2. Copy `eval/invoices/gold.example.json` to `eval/invoices/gold.json` and
   `eval/materials/gold.example.json` to `eval/materials/gold.json`, then fill in the
   real values for each file you added, keyed by filename.
3. Run the command above.

## Gold format — `eval/invoices/gold.json`

```json
{
  "receipt-01.jpg": {
    "total_ttc": 79.54,
    "invoice_number": "F-2026-0001",
    "date": "2026-09-10",
    "merchant": "Leroy Merlin"
  }
}
```

Scoring per field:

- `total_ttc` — exact match within ±0.005.
- `invoice_number` — normalised (spaces stripped, case-insensitive) exact match.
- `date` — exact `YYYY-MM-DD` match.
- `merchant` — case-insensitive "gold value is a substring of the extracted value".

## Gold format — `eval/materials/gold.json`

```json
{
  "material-01.jpg": {
    "brand": "Bosch",
    "reference": "GSB18V",
    "name_contains": "perceuse"
  }
}
```

Scoring per field: `brand`/`reference` case-insensitive exact match, `name_contains`
case-insensitive substring match against the identified name.

## Acceptance bar (plan §7, M0)

The harness exits `1` when either invoice-set threshold is not met (scaled to however
many files are present — a 10-file set needs the same 95%/85% ratios, not the raw counts):

- `total_ttc` ≥ 19/20 (95%)
- `invoice_number` ≥ 17/20 (85%)

If not met: the plan's own next steps are to add auto-crop/deskew before S1, and if still
not met, flag it for a model change at S1 — do not ship blind.
