# ADR-0062: A step must be able to return what it computes

## Status

**Accepted** (2026-09-03). Closes the design half of the defect the first end-to-end run recorded
against `card-service` (audit R2.59).

Extends [ADR-0020](0020-batch-steps-declare-their-types-and-composites-are-declared-not-inferred.md),
which introduced `CompositeType` and whose invariant this widens by exactly one step. Follows
[ADR-0059](0059-a-step-name-is-refused-where-it-is-produced.md)'s shape: state the rule in the
prompt, enforce it where the design is produced. Sits beside
[ADR-0026](0026-job-parameters-reach-a-processor-and-the-per-run-counter-does-not.md)'s `JobParameter`, which is the existing
precedent for a declared design-time fact that no copybook produces.

## Context

The first run of the whole graph put 27 files in `card-service`. Three of the four defects it
exposed were ordinary. The fourth was not a defect in any component:

```java
BigDecimal monthlyInterest = CobolArithmetic.requireFits(
        CobolArithmetic.divide(
                item.categoryBalance().tranCatBal().multiply(interestRate),
                new BigDecimal("1200"), 2),
        11, 2);
// ...
return item;
```

The value is computed and discarded. The javadoc above it — model-authored, and an accurate reading
of the COBOL — claims the method "accumulates it into the account's running month total". Nothing
accumulates. It compiles, because discarding a value is legal Java.

**Every component behaved correctly.** The design typed the step `input_type = output_type =
RatedCategoryBalance`. The renderer emitted a structurally correct
`ItemProcessor<RatedCategoryBalance, RatedCategoryBalance>`. The model, handed a return type with
no field for its result, returned the item unchanged and documented what the COBOL does. The design
language had no way to say *"a `RatedCategoryBalance` plus the interest computed from it"*, so the
architect designed the only expressible thing.

### The COBOL, and why the value cannot be local

```cobol
1300-COMPUTE-INTEREST.
    COMPUTE WS-MONTHLY-INT = ( TRAN-CAT-BAL * DIS-INT-RATE) / 1200
    ADD WS-MONTHLY-INT  TO WS-TOTAL-INT
    PERFORM 1300-B-WRITE-TX.
...
1300-B-WRITE-TX.
    MOVE WS-MONTHLY-INT       TO TRAN-AMT
...
1050-UPDATE-ACCOUNT.
    ADD WS-TOTAL-INT  TO ACCT-CURR-BAL
```

`WS-MONTHLY-INT` has two destinations and `1300-COMPUTE-INTEREST` owns neither: `1300-B-WRITE-TX`
belongs to the writer step and `1050-UPDATE-ACCOUNT` to the account-posting step. The value must
cross a step boundary, and a `RatedCategoryBalance` is where it would have to ride.

### ADR-0020 predicted this failure and predicted it would be loud

> Some COBOL will not fit the chain model, and that is worth finding out. `CBACT04C`'s processors
> happen to enrich a single item; a program whose logic fans out or **accumulates** across records
> will not decompose this way. The failure will be visible — a step whose types cannot be named —
> rather than silent, which is the point.

It was not visible. The types **could** be named: `RatedCategoryBalance` is a perfectly good type
and it resolved. What could not be expressed was a type *carrying a value no record holds*, and the
absence of a vocabulary is not a validation failure. The prediction assumed the design language
would run out of names; it ran out of nouns instead.

### The facts were already computed, and already discarded

This is the sixth sighting of the class `CLAUDE.md` names — *a fact the deterministic layer already
held, dropped one step before its consumer* (G21, G24, G26, G28, G30):

- `pic_mapper` types `WS-MONTHLY-INT` as `BigDecimal`, **precision 11, scale 2**.
  `group_field_mappings_by_source` groups it under the program.
  `build_domain_entities` then discards that whole group at one line, correctly, because a domain
  entity is copybook-sourced by definition (ADR-0010 decision 1):

  ```python
  if source_label == entry.program_name:
      continue  # only copybook-sourced fields become domain entities
  ```

  Skipping was right. *Discarding* was not. The number reached the generated code anyway, as
  `requireFits(..., 11, 2)`, because a model read it off a `PIC` clause in untrusted narration — and
  said plainly that it was inferring and would rather be told.
- `parsing/control_break.py` already recognises `accumulator_field = WS-TOTAL-INT`,
  `accumulated_from_field = WS-MONTHLY-INT`, `landing_field = TRAN-AMT`. `attach_control_breaks`
  hangs that on the step declaring `1050-UPDATE-ACCOUNT` — the *consumer*. The step that
  **produces** the value had no declared obligation to hand it over.

Worth stating precisely, because the neighbouring claim is easy to get wrong: gap **G21** already
closed this for `modernization_engineer` via `render_program_field_facts`, so the *generator* has
had these facts for some time. `solution_architect` never got the same treatment, and that is the
half that decides whether the value can be **expressed** at all.

## Decision

### 1. `UnifiedDesign.computed_values` — the values a program computes, deterministically

`build_computed_values` is the other half of `build_domain_entities`: the same groups, the group the
other one skips. `ComputedValue` carries the program, the COBOL field, `pic_mapper`'s
type/precision/scale/sign, the paragraphs that compute it, the paragraphs that only read it, and the
record field it is moved into.

**Narrowed to values the program's arithmetic actually writes** — `COMPUTE`, `ADD ... TO`,
`SUBTRACT ... FROM`, and the `GIVING` form — rather than to all working storage. Measured against
the corpus that is 3 of `CBACT04C`'s 52 own fields and 1 of `CBTRN02C`'s, with none of the `FD-*`
file aliases, `IO-STATUS` codes or `APPL-RESULT` plumbing. The narrowing is **syntactic**: a filter
on Java type would have admitted all of them, and a filter on `scale > 0` would have worked here by
luck and lied on the first integer quantity.

**It carries no Java name.** What a value should be *called* is judgment; what it *is* is read from
the COBOL.

### 2. `CompositeType.computed_fields` — a composite may carry one

`ComputedComponent` is `CompositeComponent` one level down: `field_name` is the model's judgment,
`cobol_field_name` is a reference that must resolve. ADR-0020's invariant is kept where it matters —
**a composite never invents a field's precision** — and widened only from *"a copybook produced
this"* to *"the deterministic layer produced this"*.

Resolution is **program-scoped**, because `computed_values` is and a composite is not. `WS-TEMP-BAL`
exists in `CBTRN02C`, and nothing stops a second program declaring `WS-MONTHLY-INT` with a different
picture; resolving across programs would hand a currency field the wrong precision, silently, and in
the direction that still compiles.

### 3. The refusal, and its three narrowings

A **processor** step whose paragraphs compute a value that **escapes those paragraphs** must have an
output type that carries it — as a `computed_fields` entry, or as the record the value is moved
into. Refused where the design is produced, through `parse_with_repair`, so a model gets one repair
attempt carrying a message that names the fix.

Each narrowing exists because removing it reports a correct design, and each is verified by removing
it and watching exactly the test that names it fail:

| Narrowing | What it prevents |
|---|---|
| Processors only | `CBACT01C`/`CBCUS01C` open-close tasklets reported for computing `APPL-RESULT`, a status code |
| Escapes its paragraph | `CBTRN02C`'s `WS-TEMP-BAL` (compared against a credit limit in the same paragraph) and `CBACT04C`'s `WS-TRANID-SUFFIX` (built and consumed in `1300-B-WRITE-TX`) |
| Lands in a carried record | A design whose output composite carries `Tran` forced to declare `WS-MONTHLY-INT` redundantly |

Against the four-program corpus the rule fires on exactly `WS-MONTHLY-INT` and `WS-TOTAL-INT` in
`computeMonthlyInterest` — both halves of the real defect — and on nothing else.

### 4. Prompt `v1_2_0` states the rule and hands over the facts

ADR-0059's two halves, unchanged: enforcement without the statement punishes a model for a rule it
was never given. The Known Facts block gains a computed-values table carrying the precision and the
"also read by" column that decides whether a value must survive the step:

```
| CBACT04C | WS-MONTHLY-INT | BigDecimal | 11 | 2 | 1300-COMPUTE-INTEREST | 1300-B-WRITE-TX |
| CBACT04C | WS-TRANID-SUFFIX | BigDecimal | 6 | 0 | 1300-B-WRITE-TX | (nothing -- local to its paragraph) |
```

### 5. `design.json` schema `3.10.0`

Additive with defaults on both new fields, so a design written against `3.9.0` still validates.

## Consequences

**The defect becomes a compile error rather than a silent discard.** This is the part worth having.
Once the output type differs from the input type, `return item;` does not compile — the model must
construct the output record, and its constructor takes the computed value. `CLAUDE.md` asks that the
*n*-th defect of a kind get a mechanism that makes the *n+1*-th impossible or loud; the refusal makes
this one loud at design time, and the type change makes the same mistake impossible at generate time.

**`solution_architect` is asked for more judgment, again.** ADR-0020 raised what this node is trusted
with and noted it had never been scored. This raises it further: the architect must now decide which
computed values a chain carries and where they enter. The LLM-as-judge harness applies to it more
than before.

**The refusal can under-report, and the limits are stated rather than approximated.** Only `COMPUTE`,
`ADD`, `SUBTRACT` and `GIVING` are recognised — `MULTIPLY` and `DIVIDE` appear nowhere in this corpus
and the pattern table is where they go. Only the first receiving field of a multi-target statement is
found. And **only arithmetic inside a paragraph is seen**: `CBACT04C`'s `ADD 1 TO WS-RECORD-COUNT`
sits in the main loop before the first paragraph header and is genuinely not reported. That last one
is the right boundary for the question being asked — a step declares `source_paragraphs`, so a value
computed outside every paragraph belongs to no step — but it is a limit, and absence here does not
mean "not computed".

**A `MOVE` exclusion that turned out not to be load-bearing is documented as such.** Probing showed
adding a `MOVE` pattern changes nothing in this corpus, because the accumulator reset is in the main
loop and already invisible. The rule stands on its meaning rather than on a defect it prevents, and
saying so is cheaper than a future reader re-deriving it.

**Two programs is not four.** Per `CLAUDE.md`'s rule on declaring a capability complete, this is
**closed for `CBACT04C` and `CBTRN02C`** — the two with real business logic. The two logic-free
programs exercise the tasklet narrowing and nothing else.

## Alternatives considered

**Refuse a processor whose `input_type == output_type`.** Mechanical, cheap, and wrong. The very
processor at issue returns `null` when the rate is zero, which is filtering, and filtering is a
legitimate X→X step. It would fire on correct designs, and a test asserts this rule fires on none of
them.

**Fix it in the renderer.** The renderer is already correct: given a step typed `X → X`, an
`ItemProcessor<X, X>` is exactly right. Changing it would mean rendering something the design did not
ask for.

**Let `solution_architect` declare the precision and scale itself.** Rejected for `pic_mapper`'s own
reason: a wrong scale on a currency field looks exactly like a right one. The model demonstrated both
halves of this — it inferred `11, 2` correctly *and* flagged that it should not have had to.

**Attach the obligation to the control break instead.** `ControlBreakDesign` already names
`accumulator_field`, `accumulated_from_field` and `landing_field`, so the facts were there. Rejected
because it hangs on the step declaring the break's `performed_paragraph` — the consumer — and would
have covered only programs that have a control break at all, making a general defect class look like
one idiom's special case.
