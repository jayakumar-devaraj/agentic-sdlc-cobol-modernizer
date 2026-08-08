# Real narration samples

Actual `spec.md` output from a live `cobol-modernizer design` run, checked in verbatim.

**These are not golden fixtures and must never be treated as one.** The only hand-verified golden
fixture in this repo is `tests/fixtures/golden/CBACT04C/spec.md`, whose Overview, Paragraph flow,
and Business rules a human read and checked paragraph-by-paragraph against real source. The files
here are unreviewed model output — useful precisely *because* they are what the pipeline really
produces, not because anyone has certified them correct.

They exist for one reason: **`spec_critic` cannot be exercised honestly without a real narration.**

The `faithful_narrate` technique the rest of the suite uses — feeding the Known Facts block back as
the narration — validates the deterministic fidelity machinery correctly, but produces a prompt a
real critic model rejects outright. Asked to critique one, a live model replied:

> "I don't see the `spec.md` narration file in your message… I need: ✗ The `spec.md` file content"

It is right to. That prompt contains the same block twice, once labelled Known Facts and once
labelled narration. Every test using the technique also injects a *fake* critic, so nothing ever
exercised the combination and nothing caught it.

## Provenance

| File | Source |
|---|---|
| `CBCUS01C/spec.md` | Live run `live-all-four-001`, 2026-08-08, `claude-opus-5` via the `claude` CLI backend |

Regenerate by running the real `design` subcommand and copying the output; there is no script,
because a stale regeneration script that nobody runs is worse than an honestly-dated file.
