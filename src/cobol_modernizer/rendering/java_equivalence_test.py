"""Render the JUnit equivalence test that checks generated interest math against COBOL's answers.

**The test must run against generated code, or it is not an equivalence test.** That constraint
decides this module's existence. A hand-written test in the target template cannot name the
processor class, the composite it consumes, or the record it returns -- all three come from
`design.json` and differ per design. A test that instead exercised `CobolArithmetic` would pass no
matter what a model wrote in the method body, which is the one thing step 45 exists to check.

So the test is rendered beside the processor, from the same design, and compiled and run by the
same Maven the heal loop already drives.

**Nothing here computes an expected value.** The numbers come from
`tests/fixtures/golden/CBACT04C/interest-oracle.json`, where they are literals derived by hand from
the COBOL (ADR-0021). This module transcribes them into a `@CsvSource` and does no arithmetic at
all -- deliberately, because a renderer that recomputed them would reintroduce exactly the
Python-derived oracle that ADR-0021 refused.

**The binding is declared, not inferred.** Which record component carries the balance, which
carries the rate, and which field of the output receives the interest are read from the oracle's
`java_binding` block rather than guessed from field names. That is ADR-0020's rule applied again:
when the target needs a fact the source does not supply, declare it. Guessing that `tranAmt` is
"probably the amount" is the hallucination surface this repo removes everywhere else.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from cobol_modernizer.core.contracts import CompositeType, DomainEntity
from cobol_modernizer.rendering.java_names import require_java_identifier

logger = logging.getLogger(__name__)

#: Placeholder constructor arguments for record components the oracle does not bind. They exist to
#: satisfy the constructor, never to be read: the computation under test reads the bound fields
#: only. `BigDecimal.ZERO` rather than `null` so an implementation that touches an unbound field
#: fails on a wrong *number* rather than on a `NullPointerException`, which is easier to diagnose
#: and cannot be mistaken for a missing-data bug.
_PLACEHOLDER = {"BigDecimal": "BigDecimal.ZERO", "String": '""'}


class UnrenderableOracleError(Exception):
    """The oracle cannot be rendered into a test against this design.

    Raised rather than papered over, per this repo's standing rule. Every case here means the
    oracle and the design disagree about the shape of the thing under test -- a binding naming a
    field no record has, or a composite missing the component the balance lives in -- and rendering
    a test anyway would produce one that either does not compile or silently checks the wrong
    column.
    """


def _entity(entities: Sequence[DomainEntity], name: str) -> DomainEntity:
    for entity in entities:
        if entity.name == name:
            return entity
    raise UnrenderableOracleError(
        f"the oracle's java_binding names entity {name!r}, which this design does not declare; "
        f"declared entities are {sorted(e.name for e in entities)}"
    )


def _field(entity: DomainEntity, field_name: str) -> None:
    if not any(f.java_field_name == field_name for f in entity.fields):
        raise UnrenderableOracleError(
            f"the oracle's java_binding names field {field_name!r} on {entity.name!r}, which has "
            f"no such component; components are "
            f"{sorted(f.java_field_name for f in entity.fields)}"
        )


def _require_reachable(composite: CompositeType, entity_name: str) -> None:
    """Check the composite carries exactly one component of `entity_name`, and raise if not.

    Called for the refusal, not for a value: the constructor arguments below are built from the
    composite's own component order. What this catches is a design whose step cannot reach a value
    the computation needs -- PR #28's model hit exactly that and threw rather than inventing, and
    failing here gives the same answer at render time with a better diagnostic than javac's.
    """
    matches = [c.field_name for c in composite.components if c.entity_name == entity_name]
    if not matches:
        raise UnrenderableOracleError(
            f"the step consumes {composite.name!r}, which has no {entity_name!r} component, so the "
            f"body has no way to reach it; components are "
            f"{sorted(c.entity_name for c in composite.components)}"
        )
    if len(matches) > 1:
        raise UnrenderableOracleError(
            f"{composite.name!r} has {len(matches)} components of type {entity_name!r} "
            f"({sorted(matches)}); which one carries the value is ambiguous and must be declared"
        )


def _construct(entity: DomainEntity, bound_field: str | None, bound_expression: str) -> str:
    """A `new Entity(...)` call with every component supplied positionally, in declaration order.

    Positional and in order because these are Java records: a component list that drifts from the
    record's own order compiles whenever the types happen to line up, and then puts the balance in
    the category-code column. Rendering from `entity.fields` is what keeps the two in step.

    `bound_field=None` builds the record entirely from placeholders — for a composite component the
    oracle does not bind, which is a real case since G26 widened the composite beyond what the
    interest arithmetic reads.
    """
    arguments = []
    for field in entity.fields:
        if field.java_field_name == bound_field:
            arguments.append(bound_expression)
            continue
        placeholder = _PLACEHOLDER.get(field.java_type)
        if placeholder is None:
            raise UnrenderableOracleError(
                f"{entity.name}.{field.java_field_name} has type {field.java_type!r}, which this "
                f"renderer has no placeholder for; add one rather than emitting `null`"
            )
        arguments.append(placeholder)
    return f"new {entity.name}({', '.join(arguments)})"


def render_equivalence_test(
    oracle: Mapping[str, Any],
    *,
    package: str,
    test_class_name: str,
    processor_class: str,
    composite: CompositeType,
    entities: Sequence[DomainEntity],
    output_entity: str,
    domain_package: str,
) -> str:
    """Render the equivalence test for one interest-computing step.

    `oracle` is the parsed `interest-oracle.json`. Its `rows` become parameterised cases and its
    single `not_computed` entry becomes a separate assertion, because that case is about control
    flow rather than about a number: a zero rate means COBOL writes no transaction at all.
    """
    require_java_identifier(test_class_name, source_name=test_class_name, kind="Test class name")

    binding = oracle.get("java_binding")
    if binding is None:
        raise UnrenderableOracleError(
            "the oracle has no `java_binding` block, so which record component carries the balance, "
            "the rate and the result is undeclared; this renderer will not guess it"
        )

    balance_entity = _entity(entities, binding["balance_field"]["entity"])
    rate_entity = _entity(entities, binding["rate_field"]["entity"])
    result_entity = _entity(entities, output_entity)
    _field(balance_entity, binding["balance_field"]["field"])
    _field(rate_entity, binding["rate_field"]["field"])
    _field(result_entity, binding["result_field"]["field"])

    _require_reachable(composite, balance_entity.name)
    _require_reachable(composite, rate_entity.name)
    result_field = binding["result_field"]["field"]

    # Component order is the composite's own, so the constructor call matches the rendered record.
    by_entity = {
        balance_entity.name: _construct(
            balance_entity, binding["balance_field"]["field"], "new BigDecimal(balance)"
        ),
        rate_entity.name: _construct(rate_entity, binding["rate_field"]["field"], "new BigDecimal(rate)"),
    }
    # A component the oracle does not bind is constructed from placeholders rather than refused.
    # This used to raise, which was right while the composite was exactly balance-plus-rate and
    # anything else meant a mismatch. It stopped being right when the composite gained `Account`
    # and `CardXref` (G26): those exist so the step can populate the `Tran` it returns, and the
    # interest *amount* -- the only thing this oracle has expected values for -- does not read
    # them. Refusing them would force the test's composite to differ from the design's, which is
    # the one thing a test rendered from the design must never do.
    composite_arguments = []
    for component in composite.components:
        if component.entity_name in by_entity:
            composite_arguments.append(by_entity[component.entity_name])
            continue
        unbound = _entity(entities, component.entity_name)
        composite_arguments.append(_construct(unbound, bound_field=None, bound_expression=""))

    rows = oracle["rows"]
    csv_lines = ",\n".join(
        f'        "{row["id"]}, {row["balance"]}, {row["rate"]}, {row["expected"]}"' for row in rows
    )
    (absent,) = oracle["not_computed"]

    statement = oracle["source"]["statement"]
    paragraph = oracle["source"]["paragraph"]
    program = oracle["source"]["program"]

    # The test sits in the processor's package and the types it builds live in the domain package,
    # so they are imported. Sorted and de-duplicated so one design renders one byte-identical file.
    #
    # **Every composite component, not just the bound ones.** The construction below instantiates
    # each component by simple name, so an import set built from the bound entities alone renders a
    # file that does not compile the moment a composite carries anything else -- which is exactly
    # what happened when G26 added `Account` and `CardXref`: `cannot find symbol`, twice.
    imported = {composite.name, output_entity, balance_entity.name, rate_entity.name}
    imported.update(component.entity_name for component in composite.components)
    domain_imports = "\n".join(f"import {domain_package}.{name};" for name in sorted(imported))

    rendered = f"""\
package {package};

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;

import java.math.BigDecimal;
{domain_imports}
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

/**
 * Equivalence test for {processor_class}, against COBOL's own answers.
 *
 * <p>Checks the generated implementation of {program}'s {paragraph}:
 *
 * <pre>{statement}</pre>
 *
 * <p><b>Every expected value below is a literal derived by hand from that COBOL</b>, not computed
 * here and not computed by the generator. See interest-oracle.md and ADR-0021 for the derivations,
 * what they are evidence of, and what they are not: this covers one COMPUTE, so a green run means
 * the interest arithmetic matches and no more.
 *
 * <p>This file is rendered from design.json and the oracle. It is not model-authored.
 */
class {test_class_name} {{

    private final {processor_class} processor = new {processor_class}();

    private static {composite.name} item(String balance, String rate) {{
        return new {composite.name}({", ".join(composite_arguments)});
    }}

    @ParameterizedTest(name = "{{0}}: balance {{1}} at rate {{2}} -> {{3}}")
    @CsvSource({{
{csv_lines}
    }})
    void matchesTheHandComputedTable(String id, String balance, String rate, String expected)
            throws Exception {{
        {output_entity} result = processor.process(item(balance, rate));

        assertNotNull(result, () -> id + ": expected a transaction, got none");
        BigDecimal actual = result.{result_field}();
        // compareTo, not equals: BigDecimal.equals is false for 2.4 against 2.40, and a scale
        // difference is not a wrong answer. A wrong *value* is what this test is for.
        assertEquals(
                0,
                new BigDecimal(expected).compareTo(actual),
                () -> id + ": expected " + expected + " but was " + actual);
    }}

    @Test
    void writesNoTransactionWhenTheRateIsZero() throws Exception {{
        // {absent["id"]}. {absent["behaviour"]}.
        //
        // This is the row that cannot be written as a number. An implementation returning a
        // zero-amount transaction agrees with every arithmetic case above and is still wrong: it
        // emits a record the COBOL never writes. An ItemProcessor returning null filters the item,
        // which is the faithful translation of a paragraph that is never performed.
        assertNull(
                processor.process(item("{absent["balance"]}", "{absent["rate"]}")),
                "{absent["id"]}: a zero rate must produce no transaction at all");
    }}
}}
"""

    logger.debug(
        "rendered equivalence test %s for %s (%d parameterised case(s) + the zero-rate case)",
        test_class_name,
        processor_class,
        len(rows),
    )
    return rendered
