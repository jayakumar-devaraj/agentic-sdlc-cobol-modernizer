---
name: run-demo
description: Walk the full Track C demonstration — trigger event through control-plane, specialist invocation, a real HITL pause/approve, self-healing recovery, to a release-gate decision — using docs/demo-playbook.md.
---

Run `scripts/demo_kind_up.sh` (or the current local-dev bring-up script, once one exists) to start
the stack, then follow `docs/demo-playbook.md` step by step — do not improvise a shortcut path
through it live. The playbook exists precisely so the walkthrough is rehearsed and reproducible,
not assembled from memory each time.

Confirm before starting: control-plane's Postgres and Kafka broker (from `agentic-sdlc-eventbus`)
are reachable, and the batch-drift trigger publishes to a topic control-plane's consumer is
actually subscribed to (pattern-subscription auto-discovers new topics, but a typo in the topic
name still means nothing arrives — verify the message is consumed, not just published).

If any step deviates from the playbook's documented outcome, stop and fix the playbook or the
system — never keep going and describe what "would" happen instead of what did.
