# The COBOL runtime ADR-0028 accepts, pinned — and pinned for a reason that is not taste.
#
# `gnucobol4` in the same Ubuntu repository reports `indexed file handler : disabled`, and four of
# CBACT04C's five files are ORGANIZATION IS INDEXED. It cannot run the program at all. `gnucobol3`
# is built against BDB and can, so the version below is a capability requirement rather than a
# preference — checked by `cobc --info`, not assumed.
#
# This image exists to produce a committed fixture once. It is not a CI dependency: ADR-0028's whole
# argument is that ADR-0021 refused a COBOL runtime on objections about running it *continuously*,
# and that none of them apply to running it once. The artifact is the oracle; this is scaffolding
# that produced it, kept so the oracle can be regenerated and its provenance checked.
# Pinned by digest: `ubuntu:24.04` is a moving tag, and an oracle regenerated against a different
# gnucobol build is a different oracle. The BDB assertion below catches a missing handler; only the
# digest catches a changed compiler.
FROM ubuntu@sha256:561618e2c15bf2397621dd04f96926663a3b5616c189cf7e38db7e82f5c538ea

RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends gnucobol3 \
 && rm -rf /var/lib/apt/lists/* \
 # Fail the build rather than the run if a future base image ships a handler-less package. The
 # symptom otherwise is a file-status error deep in CBACT04C that reads like a data problem.
 && cobc --info | grep -q 'indexed file handler *: *BDB'

WORKDIR /work
