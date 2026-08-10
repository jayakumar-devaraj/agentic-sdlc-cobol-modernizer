"""The target template's invariants, enforced from this repo's own suite.

`templates/target-spring-boot-baseline/` is Java, and this repo has no JDK - CI compiles it
(`.github/workflows/ci.yml`, job `template-build`). That leaves a gap worth closing cheaply: the
properties that make the template *correct as a template* are textual, not compilable. A `pom.xml`
that quietly drops back to Java 21, or grows `--enable-preview`, or acquires tenant vocabulary,
still builds green. These tests fail on all three.

They are deliberately not a substitute for the real build. Compiling on the pinned JDK is what
proves the ecosystem supports it (ADR-0019's gate); this file only proves the pin says what it is
supposed to say.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree

import pytest

MAVEN_NS = {"m": "http://maven.apache.org/POM/4.0.0"}

TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "templates" / "target-spring-boot-baseline"
POM_PATH = TEMPLATE_ROOT / "pom.xml"

# ADR-0019, decision 1. Changing this number is a decision, not a refactor: it has to move here and
# in the workflow's `java-version` together, or the build compiles for one release and runs on
# another.
PINNED_JAVA_RELEASE = "25"


def _local_name(tag: object) -> str:
    """`{http://maven.apache.org/POM/4.0.0}java.version` -> `java.version`."""
    return str(tag).rsplit("}", 1)[-1]


def _without_comments(source: str, suffix: str) -> str:
    """Strip comments before scanning for a forbidden literal.

    A check that cannot tell a comment from code is a weak check in both directions: it fires on a
    file that documents why a flag is off (as this template's `pom.xml` does), and it would be
    silenced by anyone who worked around that by deleting the explanation.
    """
    if suffix == ".xml":
        return re.sub(r"<!--.*?-->", "", source, flags=re.DOTALL)
    if suffix == ".java":
        return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL))
    return re.sub(r"(?m)^\s*#.*$", "", source)


@pytest.fixture(scope="module")
def pom_properties() -> dict[str, str]:
    root = ElementTree.parse(POM_PATH).getroot()
    properties = root.find("m:properties", MAVEN_NS)
    assert properties is not None, "the template pom declares no <properties> block"
    return {
        _local_name(child.tag): (child.text or "").strip()
        for child in properties
        if isinstance(child.tag, str)
    }


def test_the_pom_pins_the_java_release_adr_0019_decided(pom_properties: dict[str, str]) -> None:
    """`maven.compiler.release` is what actually constrains the bytecode target."""
    assert pom_properties["maven.compiler.release"] == PINNED_JAVA_RELEASE
    # Set alongside it because the Spring Boot parent reads `java.version` for its own plumbing.
    # If the two ever disagree, which one wins is a detail of a parent pom nobody in this repo
    # controls - so they are asserted equal rather than reasoned about.
    assert pom_properties["java.version"] == PINNED_JAVA_RELEASE


def test_the_ci_workflow_builds_on_the_same_release_the_pom_pins() -> None:
    """A pom targeting 25 built by a JDK 21 runner would prove nothing about JDK 25.

    This is the assertion that keeps the gate honest from the outside; `BaselineStackTest` asserts
    the same thing from the inside, against the JVM that is actually running.
    """
    workflow = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert 'java-version: "25"' in workflow
    assert "working-directory: templates/target-spring-boot-baseline" in workflow


def test_the_maven_wrapper_pins_an_exact_maven_version() -> None:
    """The build tool was the last unpinned thing in a template that pins everything else.

    Before the wrapper, CI ran whatever Maven the runner image happened to ship. That is a
    dependency nobody chose and which can change under an image update -- in a template whose JDK,
    Spring Boot version and Testcontainers version are all exact, and which asserts the JVM's own
    feature version at runtime. Step 42's self-healing loop parses Maven's diagnostics, so *which*
    Maven produced them is not a detail.
    """
    properties = (TEMPLATE_ROOT / ".mvn" / "wrapper" / "maven-wrapper.properties").read_text(
        encoding="utf-8"
    )
    match = re.search(r"apache-maven-(?P<version>[\d.]+)-bin\.zip", properties)
    assert match, f"no pinned Maven distribution in:\n{properties}"
    assert re.fullmatch(r"\d+\.\d+\.\d+", match.group("version")), (
        f"Maven version {match.group('version')!r} is not an exact pin"
    )


def test_the_wrapper_needs_no_committed_binary() -> None:
    """`only-script` mode, so the repo carries three text files and no jar.

    A committed `maven-wrapper.jar` is an opaque binary in source control that nobody reviews and
    every scanner flags. The script-only distribution avoids it entirely.
    """
    properties = (TEMPLATE_ROOT / ".mvn" / "wrapper" / "maven-wrapper.properties").read_text(
        encoding="utf-8"
    )
    assert "distributionType=only-script" in properties
    assert not list((TEMPLATE_ROOT / ".mvn").rglob("*.jar"))


def test_ci_builds_through_the_wrapper_not_a_bare_mvn() -> None:
    """Pinning the version is pointless if the pipeline still calls whatever is on PATH."""
    workflow = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "./mvnw -B -ntp verify" in workflow
    assert "run: mvn -B -ntp verify" not in workflow


def test_the_spring_boot_parent_is_pinned_to_an_exact_version() -> None:
    """No ranges, no properties, no `LATEST`.

    ADR-0019 declines to name a Spring Boot version *in the ADR* precisely so that the pin lives
    somewhere a build verifies it. That only works if the pin is a literal.
    """
    root = ElementTree.parse(POM_PATH).getroot()
    parent = root.find("m:parent", MAVEN_NS)
    assert parent is not None
    assert parent.findtext("m:artifactId", namespaces=MAVEN_NS) == "spring-boot-starter-parent"

    version = parent.findtext("m:version", namespaces=MAVEN_NS)
    assert version is not None
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"parent version {version!r} is not an exact pin"


def test_preview_features_are_not_enabled_anywhere() -> None:
    """ADR-0019 keeps `--enable-preview` off permanently.

    A preview feature changes between releases, so generated code using one compiles today and
    stops compiling on the next JDK - a failure the self-healing loop cannot diagnose, because
    nothing about the source is wrong.
    """
    for path in TEMPLATE_ROOT.rglob("*"):
        if path.is_file() and path.suffix in {".xml", ".java", ".yml", ".yaml"}:
            code = _without_comments(path.read_text(encoding="utf-8"), path.suffix)
            assert "enable-preview" not in code, path


def test_the_template_carries_no_tenant_vocabulary() -> None:
    """The template is domain-agnostic; the generated code seeded into it is not.

    This is the inverse of `core/guardrails.py`'s concern and a mirror of control-plane's own
    domain-vocabulary CI grep: *this repo* is expected to carry COBOL and CardDemo vocabulary, but
    the scaffold that any modernized batch program is seeded into must not, or the next tenant
    inherits this one's program names. `CobolArithmetic` is deliberately not a violation - COBOL's
    arithmetic rules belong to the language, not to a tenant.
    """
    forbidden = ["CardDemo", "CBACT04C", "CBTRN02C", "CBACT01C", "CBCUS01C", "CVACT01Y", "TCATBAL"]
    for path in TEMPLATE_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in {".xml", ".java", ".yml", ".yaml", ".md"}:
            continue
        content = path.read_text(encoding="utf-8")
        for term in forbidden:
            assert term not in content, f"{path} carries tenant vocabulary {term!r}"


def test_the_arithmetic_helper_exists_where_generated_code_will_import_it() -> None:
    """The one piece of real logic in the template, and the reason it is not just a pom.

    ADR-0015's benchmark caught a real model omitting COBOL's truncate-without-ROUNDED semantic, so
    this helper is a response to a measured failure of the generator rather than a precaution.
    """
    helper = TEMPLATE_ROOT / "src/main/java/com/modernized/batch/cobol/CobolArithmetic.java"
    assert helper.is_file()
    source = helper.read_text(encoding="utf-8")
    assert "RoundingMode.DOWN" in source, "truncation must be toward zero, not FLOOR"
    assert "RoundingMode.HALF_UP" in source, "ROUNDED is nearest-away-from-zero"
