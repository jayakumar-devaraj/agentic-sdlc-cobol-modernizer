#!/bin/sh
# Produce CBACT04C's own output, once, so it can be committed as a fixture (ADR-0028).
#
# **Two stages, and the first is not optional.** The shipped tcatbal.txt is the *pre-posting* state:
# CBTRN02C writes it with `ADD DALYTRAN-AMT TO TRAN-CAT-BAL` (audit R2.9). Run CBACT04C against it
# directly and every TRAN-CAT-BAL is zero, so every interest amount is zero and the oracle would pass
# for any implementation returning zero -- measured, not predicted: the first run of this script did
# exactly that. So stage 1 posts the daily transactions, and stage 2 computes interest on the result.
#
# Both programs run UNMODIFIED. Everything here is conversion of the corpus's *text representation*
# into the record formats the programs were written against, never a change to a program.
#
# Expects, mounted read-only:  /src   the tenant repo's app/ tree (cbl, cpy, data/ASCII)
#                              /co    this directory
# Writes:                      /out   the oracle and its provenance
set -eu

SRC=/src
OUT=/out
CO=/co
WORK=/work

mkdir -p "$OUT" "$WORK/idx"
cd "$WORK"

# --- compile ------------------------------------------------------------------------------------
# The two tenant programs are compiled as modules (-m) because CBACT04C is CALLed by a driver.
# -std=ibm because this is IBM Enterprise COBOL source; GnuCOBOL's own dialect is the default, and
# the difference is exactly what an oracle must not silently absorb.
cobc -m -std=ibm -I "$SRC/cpy" "$SRC/cbl/CBACT04C.cbl"
cobc -x -std=ibm -I "$SRC/cpy" "$SRC/cbl/CBTRN02C.cbl" -o cbtrn02c
cobc -x -std=ibm "$CO/RUNCB04.cbl"  -o runcb04
cobc -x -std=ibm "$CO/LOADIDX.cbl"  -o loadidx
cobc -x -std=ibm "$CO/DALYCONV.cbl" -o dalyconv
cobc -x -std=ibm "$CO/ORACHK.cbl"   -o orachk

# --- file mapping -------------------------------------------------------------------------------
# Every ASSIGN is an environment name; CLAUDE.md forbids machine paths in committed files.
export dd_TCBIN="$SRC/data/ASCII/tcatbal.txt"
export dd_XRFIN="$SRC/data/ASCII/cardxref.txt"
export dd_ACCIN="$SRC/data/ASCII/acctdata.txt"
export dd_DISIN="$SRC/data/ASCII/discgrp.txt"
export dd_DLYIN="$SRC/data/ASCII/dailytran.txt"

export dd_TCATBALF="$WORK/idx/tcatbal"      # shared: CBTRN02C writes it, CBACT04C reads it
export dd_XREFFILE="$WORK/idx/cardxref"
export dd_ACCTFILE="$WORK/idx/acctdata"     # shared: both programs rewrite it
export dd_DISCGRP="$WORK/idx/discgrp"
export dd_DALYTRAN="$WORK/dailytran.seq"
export dd_TRANFILE="$WORK/idx/tranmaster"   # CBTRN02C's transaction master (OPEN OUTPUT)
export dd_DALYREJS="$OUT/dalyrejs.txt"
export dd_TRANSACT="$OUT/transact.dat"      # CBACT04C's interest transactions -- the oracle

# --- dialect check, before anything is produced ---------------------------------------------------
# ORACHK compares GnuCOBOL's own COMPUTE against ADR-0021's hand-derived table and returns non-zero
# on any mismatch. It runs first and inside the pipeline deliberately: an oracle produced by a
# compiler that disagrees with the hand-derivation on truncation is worse than no oracle, and a
# check that only a human ever ran is a check that stops being run.
echo "--- dialect check: ORACHK vs ADR-0021 ---"
./orachk

# --- load: the corpus's text into the record formats the programs expect --------------------------
# Counts are asserted, not just displayed. A truncated input would otherwise convert fewer records,
# post fewer transactions, and still yield a full-looking 50-record oracle with under-posted
# balances -- wrong in a way every stage exits 0 on.
./loadidx | tee load.log
./dalyconv | tee -a load.log

expect_count() {
  want="$2"
  got=$(grep -E "^$1" load.log | tr -dc '0-9')
  if [ "$got" != "$want" ]; then
    echo "ABORT: $1 processed ${got:-none} records, expected $want" >&2
    exit 1
  fi
}
expect_count "TCATBALF loaded"    50
expect_count "XREFFILE loaded"    50
expect_count "ACCTFILE loaded"    50
expect_count "DISCGRP  loaded"    51
expect_count "DALYTRAN converted" 300

# --- stage 1: post the daily transactions (CBTRN02C) ---------------------------------------------
# This is what turns tcatbal from the pre-posting state into balances interest can be computed on.
echo "--- stage 1: CBTRN02C ---"
# The exit code is *bounded*, not swallowed and not blindly trusted. The original script used
# `|| echo`, which let any failure through -- a partially-posted run would have produced an oracle
# that looked perfect. Removing it revealed that CBTRN02C exits 4 on this data even though it
# completes normally: 300 processed, 43 rejected, every output written. 4 is a warning-level code and
# its precise meaning here is NOT established, so it is allowed explicitly and anything else aborts.
#
# The real check is the line below it: the program reports how many transactions it processed, and
# that is asserted. A count is evidence about the work done; an exit code is evidence about how the
# runtime felt about it.
set +e
./cbtrn02c | tee stage1.log
rc=${PIPESTATUS:-$?}
set -e
case "$rc" in
  0|4) ;;
  *) echo "ABORT: CBTRN02C returned $rc (only 0 and 4 are known-good)" >&2; exit 1 ;;
esac
grep -q "TRANSACTIONS PROCESSED :000000300" stage1.log || {
  echo "ABORT: CBTRN02C did not process all 300 daily transactions" >&2; exit 1; }

# --- stage 2: compute interest (CBACT04C), unmodified --------------------------------------------
echo "--- stage 2: CBACT04C ---"
./runcb04 | tee stage2.log
grep -q "END OF EXECUTION OF PROGRAM CBACT04C" stage2.log || {
  echo "ABORT: CBACT04C did not reach normal end" >&2; exit 1; }

# --- unload the rewritten account file ------------------------------------------------------------
# CBACT04C REWRITEs ACCOUNT-FILE, so posted balances are an output as much as the transactions are.
# Written as fixed-length SEQUENTIAL, not LINE SEQUENTIAL: the first version of this script used
# LINE SEQUENTIAL and GnuCOBOL trimmed trailing spaces, so a 300-byte record came out ~113 bytes and
# every field after the last non-blank was lost.
cobc -x -std=ibm "$CO/UNLOADAC.cbl" -o unloadac
export dd_ACCOUT="$OUT/acctdata-posted.dat"
./unloadac

# --- provenance ------------------------------------------------------------------------------------
# ADR-0028 requires the fixture to carry what produced it. Written by the run, not by hand, so it
# cannot drift from the artifact beside it.
{
  echo "# CBACT04C oracle - provenance"
  echo
  echo "Produced by tools/cobol-oracle/run-oracle.sh inside the pinned image (see Dockerfile)."
  echo "**Both tenant programs run unmodified.** Only the corpus's text representation is converted."
  echo
  echo "- cobc: $(cobc --version | head -1)"
  echo "- indexed handler: $(cobc --info | grep -i 'indexed file handler' | tr -s ' ')"
  echo "- dialect: -std=ibm"
  echo "- PARM-DATE: 2026-08-12 (fixed; see RUNCB04.cbl)"
  echo "- generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  echo "## Pipeline"
  echo "1. CBTRN02C posts dailytran into tcatbal (the shipped tcatbal is the PRE-posting state)."
  echo "2. CBACT04C computes interest on the posted balances and writes transact.dat."
  echo
  echo "## Input blob hashes (sha256)"
  for f in tcatbal cardxref acctdata discgrp dailytran; do
    echo "- $f.txt  $(sha256sum "$SRC/data/ASCII/$f.txt" | cut -d' ' -f1)"
  done
  echo
  echo "### Producing programs"
  echo "The COBOL is as much an input as the data. Without these, an edit to a tenant program"
  echo "leaves this fixture stale with no signal, and a later mismatch reads as a Java defect."
  for f in CBACT04C CBTRN02C; do
    echo "- $f.cbl  $(sha256sum "$SRC/cbl/$f.cbl" | cut -d' ' -f1)"
  done
  echo "- ORACHK: $(./orachk | tail -1)"
  echo "- CBTRN02C exit code: $rc (0 and 4 accepted; see run-oracle.sh)"
  echo "- CBTRN02C: $(grep -h TRANSACTIONS stage1.log | tr -s ' ' | paste -sd'; ' -)"
  echo
  echo "## Output"
  for f in transact.dat acctdata-posted.dat dalyrejs.txt; do
    [ -f "$OUT/$f" ] && echo "- $f  $(wc -c < "$OUT/$f") bytes  sha256 $(sha256sum "$OUT/$f" | cut -d' ' -f1)"
  done
  echo
  echo "## Known-unverified against IBM Enterprise COBOL"
  echo "GnuCOBOL is not the tenant's compiler. These are NOT corroborated by ADR-0021's"
  echo "hand-derived values and must be read as findings rather than failures if they disagree:"
  echo "FUNCTION CURRENT-DATE formatting; STRING ... DELIMITED BY SIZE padding at the edges;"
  echo "the sign of zero; and the zoned-decimal sign representation on REWRITE -- the first run"
  echo "turned an input overpunch of 940{ into 9400, identical in value and different in bytes,"
  echo "which is why ADR-0029 compares fields rather than bytes."
  echo
  echo "The interest arithmetic IS independently corroborated: it must match ADR-0021's"
  echo "hand-computed table, which was derived from the COBOL by a human without running it."
} > "$OUT/PROVENANCE.md"

echo "--- done ---"
