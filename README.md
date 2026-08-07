# agentic-sdlc-cobol-modernizer

COBOL-to-Java modernization specialist for the `agentic-sdlc-*` platform. Invoked as a CLI
subprocess by [`agentic-sdlc-control-plane`](https://github.com/jayakumar-devaraj/agentic-sdlc-control-plane)
— it has no ingress API, no durable checkpointer, and no human-in-the-loop gates of its own; see
[`docs/adr/0001-the-specialist-is-a-subprocess-not-a-second-control-plane.md`](docs/adr/0001-the-specialist-is-a-subprocess-not-a-second-control-plane.md)
for why.

## Tech stack

- **Language**: Python 3.12
- **Internal sub-pipeline**: LangGraph (in-process, in-memory checkpointer — not durable; see
  ADR-0001)
- **State/validation**: Pydantic
- **Testing**: pytest, pytest-cov

## Architecture

Not yet built — the specialist nodes (`spec_extractor`, `spec_critic`, `solution_architect`,
`modernization_engineer`, `build_validator`) land across Milestones C2–C4. Today this repo is a
CLI skeleton (`cobol-modernizer run --program --tenant-repo --output --json`) that honestly
reports `not_implemented` rather than a working pipeline. A real architecture diagram is added
once there's a real architecture to diagram (Milestone C6), not before.

## Quickstart

```bash
py -3.12 -m venv .venv
./.venv/Scripts/pip install -e ".[dev]"
./.venv/Scripts/cobol-modernizer run --program CBACT04C --tenant-repo <path> --output <path> --json
```

Currently returns a `not_implemented` status — this is expected until Milestone C2.

## Local development

No local infrastructure of its own to stand up yet. The knowledge store (Milestone C2) and any
persistent state reuse `agentic-sdlc-control-plane`'s existing Postgres instance rather than a
second database — a docker-compose stack for this repo is deferred until there's a real service
to run (the sandboxed Maven compiler, Milestone C4), rather than shipped empty to check a box.

## Testing

```bash
./.venv/Scripts/python -m pytest --cov=cobol_modernizer --cov-report=term-missing --cov-fail-under=90
```

5 tests passing, 90% coverage as of Milestone C1 — the number a real run produces, not a claim.

## Deployment / CI

`.github/workflows/ci.yml` runs lint + the test suite with the coverage floor above on every push
and pull request. No deployment pipeline yet — this repo has nothing running in production until
control-plane's specialist router (Track P) can invoke it, and Kubernetes/Terraform manifests
(Track P4, in `agentic-sdlc-control-plane`) exist to run it against.
