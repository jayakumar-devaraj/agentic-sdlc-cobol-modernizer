"""`rendering/target_api.py` — the generator prompt's view of the target's helper class.

This exists because a hand-copied API list in a prompt is a second source of truth that goes stale
the first time the template changes, and goes stale *silently*: nothing fails, the model just keeps
writing against a class that no longer looks like that. Extracting from the real file makes the two
impossible to disagree; these tests are what make that claim checkable.
"""

from __future__ import annotations

import pytest

from cobol_modernizer.rendering.target_api import (
    COBOL_ARITHMETIC_PATH,
    extract_helper_api,
    render_target_api_facts,
)

#: Every public static helper the real template class exposes today. A method added to or removed
#: from CobolArithmetic must be a deliberate edit here too -- that is the drift check.
EXPECTED = {"truncate", "rounded", "divide", "divideRounded", "requireFits"}


@pytest.fixture(scope="module")
def methods():
    return extract_helper_api(COBOL_ARITHMETIC_PATH.read_text(encoding="utf-8"))


def test_the_real_template_class_is_where_it_is_expected():
    assert COBOL_ARITHMETIC_PATH.is_file(), f"{COBOL_ARITHMETIC_PATH} moved; the prompt is now blind"


def test_every_public_helper_is_extracted_and_no_others(methods):
    found = {m.signature.split("(")[0].split()[-1] for m in methods}
    assert found == EXPECTED


def test_signatures_carry_their_real_parameter_lists(methods):
    by_name = {m.signature.split("(")[0].split()[-1]: m.signature for m in methods}
    assert by_name["truncate"] == "BigDecimal truncate(BigDecimal value, int scale)"
    assert by_name["divide"] == (
        "BigDecimal divide(BigDecimal dividend, BigDecimal divisor, int scale)"
    )
    assert by_name["requireFits"] == (
        "BigDecimal requireFits(BigDecimal value, int precision, int scale)"
    )


# --- The two bugs this module had when first written, pinned so they cannot return --------------


def test_the_first_methods_doc_is_its_own_and_not_the_classs(methods):
    # A lazy `.*?` walks past any `*/` not followed by `public static`, so `truncate`'s doc
    # swallowed the class-level Javadoc, the class declaration and the private constructor. The
    # symptom was a 2,000-character "doc" for a one-sentence method.
    truncate = next(m for m in methods if m.signature.startswith("BigDecimal truncate"))
    assert "public final class" not in truncate.doc
    assert "private CobolArithmetic" not in truncate.doc
    assert len(truncate.doc) < 300
    assert truncate.doc.startswith("Stores a value into a field of the given scale")


def test_multi_word_inline_code_survives_cleaning(methods):
    # `{@code ON SIZE ERROR}` was being reduced to `ERROR`, which inverted the sentence it was in:
    # "a COBOL MOVE without ON SIZE ERROR silently discards" became "without ERROR".
    requires = next(m for m in methods if m.signature.startswith("BigDecimal requireFits"))
    assert "ON SIZE ERROR" in requires.doc


# --- What the prompt section actually says ------------------------------------------------------


def test_the_rendered_section_names_every_method():
    rendered = render_target_api_facts()
    for name in EXPECTED:
        assert name in rendered


def test_the_rendered_section_carries_the_double_rounding_warning():
    # divideRounded's worked counterexample is the single most valuable sentence in the class for a
    # generator: it is the difference between correct money and quietly wrong money, and a bare
    # signature does not carry it.
    rendered = render_target_api_facts()
    assert "double rounding" in rendered
    assert "20099/20000" in rendered


def test_the_rendered_section_tells_the_model_not_to_invent_methods():
    assert "do not" in render_target_api_facts().lower()


def test_rendering_is_deterministic():
    assert render_target_api_facts() == render_target_api_facts()
