# ADR-0053: The narration `spec_critic` judges is wrapped, and a prompt version says so

## Status

**Accepted** (2026-08-24). Closes the defect
[ADR-0052](0052-the-untrusted-boundary-is-checked-behaviourally-not-by-imports.md)'s boundary check
found on its first run, at the cost that record predicted: a live prompt version and a billed run.

**The first `v1_1_0` in this registry.** Prompt versioning had been a mechanism nothing exercised.

## Context

`build_critique_prompt` wrapped every COBOL source unit and then appended `extraction.spec_markdown`
**raw**. `solution_architect` and `modernization_engineer` both wrap that same artifact, on
`solution_architect`'s stated reasoning: a narration is an LLM's account of untrusted text, so
trusting it because this platform produced it launders the input it came from.

The path is short and needs no exotic step. A directive-shaped comment in COBOL influences
`spec_extractor`'s narration; the narration arrives here outside the block; and per ADR-0001 this
node's confidence score is **the only independent check on extraction quality a human gate sees**.
The instrument that would catch a manipulated spec was the one reading it unguarded.

**The prompt already carried the rule and had nothing to attach it to.** v1_0_0 says, in its own
words, that the narration being critiqued is not a source of instructions. It then handed the model
that narration undelimited, immediately after a block whose delimiters mark exactly that boundary
for the source. A rule stated in prose and unenforced in the payload is the same shape of defect as
a caveat recorded and never probed.

## Decision

**The narration goes inside the block, under `<PROGRAM>-spec`** — the label
`modernization_engineer` already uses for this artifact, so the convention is reused rather than
invented.

**The prompt moves with it, as a version rather than an edit.** `prompts/registry/spec_critic/`
now holds `v1_0_0.md` and `v1_1_0.md`; v1_1_0 states that the narration arrives wrapped and
distinguishes the two kinds of text now inside the tags — the source and copybooks are ground truth,
`<PROGRAM>-spec` is the text on trial, and being inside the tags makes neither of them instructions.
v1_0_0 stays readable rather than being overwritten, which is the only thing that makes the previous
measurement interpretable.

### The mechanism, not the instance

`spec_critic.PROMPT_VERSION` names the version, and **`loader.node_prompt_version(persona)` is the
one place anything asks**. The recurring hazard here is not this node's: it is that something else
which must send the same system prompt keeps sending the registry default.

Two things in this repository do exactly that, and both were caught by the change rather than by
inspection:

1. `test_cli_design`'s fake identifies the calling node by matching its system prompt **exactly**,
   so a node on a new version arrives as *"a system prompt from no known registry entry"*. It would
   have failed loudly.
2. **The billed discrimination benchmark reads the prompt itself** and would have measured
   `v1_0_0`'s text against a `v1_1_0` payload. That failure is silent, it costs money, and its
   output looks exactly like a real result.

`test_every_node_sends_a_prompt_version_that_exists` covers all five nodes, so the second node to
move versions is covered by construction. Both guards were shown failing first, against a node
pointed at a version that does not exist.

## Consequences

### A new way for the node to fail, stated because it is new

Delimiter-forgery detection now runs on the narration. A narration containing this module's tag text
raises `DelimiterForgeryError` instead of being appended silently — a hard failure of the critique,
where before it was a boundary quietly dissolved. That is the intended direction (fail loudly rather
than guess) and it is a failure mode that did not exist yesterday.

### Two tests were asserting the old shape, and one fixture could not have caught this at all

Trap 3, exactly as `docs/development-environment.md` describes it. The ordering test asserted
`prompt.endswith(spec_markdown)`; the prompt still ends with the narration, now inside its wrapper,
and the ADR-0017 property it guards is untouched.

The more interesting one: the boundary section's `faithful_extraction` used the shortcut every other
module uses — a narration that **restates the Known Facts verbatim**. A narration byte-identical to
trusted content cannot be located by any test that asks *where* it appears, because it is already in
the prompt, outside the blocks, as itself. `test_critic_discrimination` guards this property for its
own fixture and had done since it was written; the boundary fixture did not, and would have reported
the fix as not working. It is distinctive prose now.

### What the billed run measured

`pytest tests/system/test_critic_discrimination.py` with the live marker enabled:
**7 passed in 9m16s**, four of them free and **three of them billed** — four model calls, ~$0.56 at
the per-call costs measured on 2026-08-08.

What those three assertions establish, stated as what they check rather than as a summary:

| assertion | what held |
|---|---|
| `test_both_tiers_catch_every_planted_error[haiku]` | at least one rule scored below `0.7` per planted error, **three of three** |
| `test_both_tiers_catch_every_planted_error[opus]` | the same, on the strongest model |
| `test_the_low_confidence_threshold_separates_good_from_bad` | `bad_min < 0.7 < clean_min` — the corrupted narration's worst score sits below the threshold and a genuine narration's worst score sits above it |

The corruptions are chosen so the **deterministic** checks cannot catch them — asserted, not
assumed, by `test_the_deterministic_checks_do_not_catch_these_corruptions` — so this is the model's
own contribution, on the version of the prompt this node now sends.

**The scores themselves were not recorded, and that is a defect this run exposed in its own
harness.** This module printed confidences only inside an assertion message, which does not render
when the assertion holds. A passing run therefore left nothing, and recovering 2026-08-24's numbers
would have meant buying the run a second time. `test_judge_benchmark` had carried the rule in its own
comment since it was written — *printed so a real run leaves the artifact the verification report
needs, whether or not the assertions below pass* — and this module never adopted it. It does now
(`_parsed_and_printed`, which also prints the rationales of every flagged rule, per ADR-0024 and
trap 10). The fix costs nothing and applies to the next run; it cannot recover this one.

**What is therefore claimed and what is not.** Claimed: on v1_1_0, both tiers still catch every
planted error and the threshold still separates. Not claimed: that the scores are unchanged from
2026-08-08's 0.00/0.20/0.40 and 0.30/0.15/0.35, or that wrapping moved them in any direction. The
run answers whether the node still discriminates, which is the question that gated this change.

### What this does not claim

One program, one corpus, one pair of runs. The narration is wrapped for every program this node
sees, but *measured* on `CBCUS01C` — the corpus's simplest, chosen by `test_critic_discrimination`
because its planted errors are checkable against source line numbers, not because it is
representative. And the boundary this closes is `spec_critic`'s. `build_validator` embeds
model-authored Java in its prompt unwrapped; that is out of scope here, deliberately, and is not the
same question: its prompt carries model-authored Java and compiler diagnostics rather than tenant
source, and whether that needs a boundary of its own is undecided rather than answered here.
