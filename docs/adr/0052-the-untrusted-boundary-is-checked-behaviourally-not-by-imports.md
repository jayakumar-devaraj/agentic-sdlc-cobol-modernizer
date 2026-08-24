# ADR-0052: The untrusted boundary is checked behaviourally, and one node is pinned outside it

## Status

**Accepted** (2026-08-24). Closes what remained of plan step 37 — the guardrail integration itself
has been done since the nodes landed and was mis-tracked as pending.

## Context

`core/guardrails.py` has been tested since it landed: given text, does it wrap, does it raise on
delimiter forgery, does it flag injection phrasings without flagging ordinary COBOL comments. Twenty
tests, all real.

None of them asserted the property the repository actually depends on. The guardrail's value is not
that the function works — it is that **no tenant text reaches a model outside the block the model is
told is inert data**. That is a statement about the four nodes that build prompts, and nothing was
making it.

What stood in for it was a CI step that checked `tests/system/test_guardrails.py` **existed as a
file** if `spec_extractor.py` existed, under a comment still reading *"no node reads COBOL source yet
(Milestone C1)"*. Four nodes read it. The guardrail was real; the check was theatre.

## Decision

**The check is behavioural, over the prompt each node really sends.**

Each node is run through its real entrypoint with only the model call replaced — the same injected
callback its own test module already uses — and the callback raises to hand back the prompt it was
given. The assertion is then: cut every `<untrusted-cobol-source …>` block out of that prompt, and
**no comment line of any source unit the program resolves to may appear in what is left**.

Comment lines rather than the source as a whole, because comments are the surface this module exists
for: a directive-shaped comment is the injection `wrap_untrusted_cobol` contains, and a loose comment
line in a prompt is that containment having failed. `CBACT04C` carries 53 of them and its copybooks
30 more.

### The import-level check was rejected, and the reason is in this repository's own register

Open Issue 3 proposed *"every node module that imports `model_client` also imports `guardrails`"*.
That is one grep, and it has the exact blind spot **G21** cost two closures to learn:
`render_program_field_facts` was written, tested, and **never called** — a string-replacement patch
that silently did not match — and the helper's own unit test passed the entire time it was doing
nothing.

An import check would pass today on every node. It would also have passed on the defect this one
found on its first run, described below, in a module that imports the guardrail *and calls it*.

### Shown failing before it was believed

One line appended to `build_prompt` echoing the source outside the wrapped sections: the check
reports **73 escaped comment lines**, and the two nodes not damaged stay green. A check first
observed passing on the artifact that produced it is not evidence
(`docs/development-environment.md`, trap 6).

## Consequences

### `spec_critic` puts the narration it judges outside the boundary, and is pinned rather than fixed

`build_critique_prompt` wraps every COBOL source unit and then appends `extraction.spec_markdown`
raw. `solution_architect` and `modernization_engineer` both **wrap that same artifact**, on
`solution_architect`'s stated reasoning: a narration is an LLM's account of untrusted text, so
treating it as trusted because this platform produced it launders the input it came from.

The path is short and real — a directive-shaped COBOL comment influences the extractor's narration,
and the narration lands in the critic's prompt outside the block.

It is **recorded, not fixed in the change that found it**, because the fix is not one line: the
prompt at `prompts/registry/spec_critic/v1_0_0.md` names the section it appends and must stay in step
with the payload, and a prompt edit wants a live critic run to say the node still discriminates —
a billed measurement, which this session did not have. So the state is declared under the rule that a
known-unverified item is either probed or accepted-with-consequence: **this one is probed.**
`test_spec_critic_leaves_the_narration_it_judges_outside_the_boundary` asserts the current state and
fails the day someone wraps it, which is the point — the pin makes the fix visible instead of letting
it quietly change what the guardrail covers.

**What would be wrong if this is never fixed**: an injection surviving into `spec_extractor`'s
narration reaches the critic as apparent instructions rather than as data. The critic is the only
independent check the human gate sees (ADR-0001), so the instrument that would catch a manipulated
spec is the one reading it unguarded.

### The CI step costs a second and runs the same module twice

Deliberate. The full suite already covers it; running it again by name means a boundary regression is
a distinctly labelled red rather than one line among eleven hundred.

### What this does not claim

Four nodes, one program, one corpus. The check is structural and would cover a fifth node the day one
exists — it enumerates source units from the resolved program rather than naming files — but nothing
here says a *new* node will be wired to a guardrail at all. That is what the step failing on a real
leak is for, and it is why the check reads the prompt rather than the imports.
