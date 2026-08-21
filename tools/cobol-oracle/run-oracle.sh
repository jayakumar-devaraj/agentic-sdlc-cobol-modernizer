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
cobc -x -std=ibm "$CO/SIGNCONV.cbl" -o signconv
cobc -x -std=ibm "$CO/SIGNBACK.cbl" -o signback
cobc -x -std=ibm "$CO/ORACHK.cbl"   -o orachk
cobc -x -std=ibm "$CO/UNLOADAC.cbl" -o unloadac
cobc -x -std=ibm "$CO/UNLOADTC.cbl" -o unloadtc
cobc -x -std=ibm "$CO/UNLOADTR.cbl" -o unloadtr

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
export dd_DALYRAW="$WORK/dailytran.raw"   # DALYCONV frames; SIGNCONV converts signs
export dd_DALYTRAN="$WORK/dailytran.seq"
export dd_TRANFILE="$WORK/idx/tranmaster"   # CBTRN02C's transaction master (OPEN OUTPUT)
export dd_DALYREJS="$OUT/dalyrejs.txt"
export dd_TRANSACT="$WORK/transact.raw"     # CBACT04C's interest transactions -- the oracle,
                                            # in this runtime's signs until SIGNBACK converts it

# --- dialect check, before anything is produced ---------------------------------------------------
# ORACHK compares GnuCOBOL's own COMPUTE against ADR-0021's hand-derived table and returns non-zero
# on any mismatch. It runs first and inside the pipeline deliberately: an oracle produced by a
# compiler that disagrees with the hand-derivation on truncation is worse than no oracle, and a
# check that only a human ever ran is a check that stops being run.
echo "--- dialect check: ORACHK vs ADR-0021 ---"
./orachk

# --- guard: what the corpus actually holds in every sign position ---------------------------------
# The pipeline was built against a measured corpus, and this asserts that measurement every run
# instead of once. ADR-0043 was found by hand and ADR-0047 fixes one field; what makes the *next*
# representation mismatch loud rather than silent is this check, not either of those.
#
# Recorded here rather than reasoned about: three of the four files carry only `{` (+0), the single
# overpunch where the correct reading and this runtime's lossy one agree, so they need no conversion.
# dailytran.txt carries all twenty. If any of that changes -- a different corpus, a regenerated
# fixture -- the run stops and a human decides, rather than the programs computing on wrong digits
# and every stage exiting 0.
expect_signs() {
  # `paste -s -d ''` joins the distinct characters into one line. Deliberately not
  # `tr -d` with an escape: this file is written through tooling that eats backslashes,
  # and a mangled one here silently compares against a string with a newline in it.
  got=$(LC_ALL=C awk -v p="$2" '{ print substr($0, p, 1) }' "$1" | LC_ALL=C sort -u | paste -s -d '' -)
  if [ "$got" != "$3" ]; then
    echo "ABORT: $1 byte $2 holds [$got], expected [$3]" >&2
    exit 1
  fi
}

echo "--- sign-position fingerprint of the corpus ---"
# CVACT01Y: ACCT-CURR-BAL, ACCT-CREDIT-LIMIT, ACCT-CASH-CREDIT-LIMIT, ACCT-CURR-CYC-CREDIT,
# ACCT-CURR-CYC-DEBIT -- five S9(10)V99, sign on the last byte of each.
for off in 24 36 48 90 102; do
  expect_signs "$SRC/data/ASCII/acctdata.txt" "$off" "{"
done
expect_signs "$SRC/data/ASCII/tcatbal.txt"   28  "{"           # CVTRA01Y TRAN-CAT-BAL
expect_signs "$SRC/data/ASCII/discgrp.txt"   22  "{"           # CVTRA02Y DIS-INT-RATE
expect_signs "$SRC/data/ASCII/dailytran.txt" 143 "ABCDEFGHIJKLMNOPQR{}"
echo "sign fingerprint OK"

# --- load: the corpus's text into the record formats the programs expect --------------------------
# Counts are asserted, not just displayed. A truncated input would otherwise convert fewer records,
# post fewer transactions, and still yield a full-looking 50-record oracle with under-posted
# balances -- wrong in a way every stage exits 0 on.
./loadidx | tee load.log
./dalyconv | tee -a load.log
./signconv | tee -a load.log

expect_count() {
  want="$2"
  got=$(grep -E "^$1" load.log | tr -dc '0-9')
  if [ "$got" != "$want" ]; then
    echo "ABORT: $1 processed ${got:-none} records, expected $want" >&2
    exit 1
  fi
}
expect_count_in() {
  got=$(grep -E "^$2" "$1" | tr -dc '0-9')
  if [ "$got" != "$3" ]; then
    echo "ABORT: $2 in $1 reported ${got:-none}, expected $3" >&2
    exit 1
  fi
}

# --- back-conversion: the oracle leaves in the corpus's representation ----------------------------
# SIGNCONV converts on the way in; this converts on the way out, so GnuCOBOL's own `q`-`y` signs
# never leave the container. SIGNBACK.cbl's header has the argument for why that matters -- the
# short version is that two of these files are INPUTS to the generated Java, and the alternative is
# teaching every migrated program this platform ships what a test harness's sign bytes mean.
# Both counts are asserted per call rather than displayed, for the reason every other count in this
# script is: a back-conversion that silently converted nothing would leave a fixture this runtime's
# decoders reject, and one that converted the wrong offset would leave a plausible file of wrong
# numbers. The negative counts are the interesting half -- before ADR-0047 every one of them was 0,
# because the lossy read had destroyed every sign in the corpus.
signback() {
  shape="$1"; src="$2"; dst="$3"; wantr="$4"; wantn="$5"
  case "$shape" in
    ACC) SB_SHAPE=ACC dd_SBACCIN="$src" dd_SBACCOUT="$dst" ./signback > sb.log ;;
    TCB) SB_SHAPE=TCB dd_SBTCBIN="$src" dd_SBTCBOUT="$dst" ./signback > sb.log ;;
    TRN) SB_SHAPE=TRN dd_SBTRNIN="$src" dd_SBTRNOUT="$dst" ./signback > sb.log ;;
    *)   echo "ABORT: signback shape $shape" >&2; exit 1 ;;
  esac
  cat sb.log
  gotr=$(grep "records:" sb.log | tr -dc '0-9')
  gotn=$(grep "negatives:" sb.log | tr -dc '0-9')
  if [ "$gotr" != "$wantr" ] || [ "$gotn" != "$wantn" ]; then
    echo "ABORT: signback $shape gave ${gotr:-none} records / ${gotn:-none} negatives,"          "expected $wantr / $wantn" >&2
    exit 1
  fi
}

expect_count "TCATBALF loaded"    50
expect_count "XREFFILE loaded"    50
expect_count "ACCTFILE loaded"    50
expect_count "DISCGRP  loaded"    51
expect_count "DALYTRAN converted" 300
# Every record is re-signed, and 50 of them are negative -- the same 50 whose amounts the
# previous oracle read as positive with a digit missing. Both numbers are asserted because a
# conversion that silently skipped the negative half would still convert 300 records.
expect_count "DALYTRAN signs converted" 300
expect_count "DALYTRAN signs negative" 50

# --- stage 1: post the daily transactions (CBTRN02C) ---------------------------------------------
# This is what turns tcatbal from the pre-posting state into balances interest can be computed on.
echo "--- stage 1: CBTRN02C ---"
# The exit code is *bounded*, not swallowed and not blindly trusted. The original script used
# `|| echo`, which let any failure through -- a partially-posted run would have produced an oracle
# that looked perfect. Removing it revealed that CBTRN02C exits 4 on this data even though it
# completes normally: 300 processed, 38 rejected, every output written. 4 is a warning-level code and
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

# --- snapshot the account file BETWEEN the stages ---------------------------------------------
# Both programs rewrite ACCTFILE, so the posted file alone cannot say how much of an account's
# balance change was interest. With this snapshot it can: CBACT04C's 1050-UPDATE-ACCOUNT does
# `ADD WS-TOTAL-INT TO ACCT-CURR-BAL` and nothing else in stage 2 touches the balance, so for
# every account
#
#     (stage-2 balance) - (stage-1 balance) == sum of that account's interest transactions
#
# and that identity is two COBOL outputs checking each other rather than a re-derivation in
# Python. It is here because the fixture committed by PR #56 failed it: one transaction record
# carried 900014.55 where four re-runs of this same pipeline over byte-identical inputs write
# 14.55, and the account file agreed with the 14.55. Nothing in the suite could see that, because
# every check asked whether the oracle looked plausible on its own.
export dd_ACCOUT="$WORK/acctdata-stage1.raw"
./unloadac | tee stage1-unload.log
expect_count_in stage1-unload.log "ACCTFILE unloaded" 50
signback ACC "$WORK/acctdata-stage1.raw" "$OUT/acctdata-stage1.dat" 50 53

# --- unload CBTRN02C's own transaction master -------------------------------------------------
# Its other two outputs are captured above and below; this one was written to the work directory
# and never unloaded, so the program's *primary* output existed only inside the container. A
# CBTRN02C comparison had two of its three in-scope targets and no way to check the third.
# (DALYREJS is out of scope for generation by ADR-0038, and is not a comparison target.)
#
# **300 processed minus 38 rejected is 262**, and that is asserted rather than displayed for the
# same reason every other count here is: a truncated or partially-posted master would still look
# like a plausible file of correct records.
#
# It was 257 before ADR-0047. The five extra are transactions this runtime used to reject as
# `0102 OVERLIMIT` on amounts that had lost a digit and their sign -- a negative amount read as a
# large positive one pushes a projected balance over the limit. 262 is also, independently, the
# number the generated Java pipeline produced against the unconverted oracle, which is the
# cross-check that says the new number is a corrected measurement and not a refitted constant.
export dd_TRNOUT="$WORK/transact-stage1.raw"
./unloadtr | tee stage1-tran-unload.log
expect_count_in stage1-tran-unload.log "TRANFILE unloaded" 262
signback TRN "$WORK/transact-stage1.raw" "$OUT/transact-stage1.dat" 262 50

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
export dd_ACCOUT="$WORK/acctdata-posted.raw"
./unloadac | tee stage2-unload.log
expect_count_in stage2-unload.log "ACCTFILE unloaded" 50
signback ACC "$WORK/acctdata-posted.raw" "$OUT/acctdata-posted.dat" 50 4
# CBACT04C wrote its interest transactions straight to a file rather than through an unloader,
# so this is the one back-conversion with no unload step in front of it.
signback TRN "$WORK/transact.raw" "$OUT/transact.dat" 50 0

# --- unload the POSTED tcatbal, which is the comparison's input ------------------------------------
# The balances CBACT04C computed interest from exist only inside this run: the shipped tcatbal.txt is
# the pre-posting state, and stage 1 wrote over it. Without capturing them, anything compared against
# this oracle would have to start from zeros, compute zero interest, and fail for a reason that is
# not about the code under test -- a red result that looks like a translation defect and is really a
# fixture nobody captured.
export dd_TCBOUT="$WORK/tcatbal-posted.raw"
./unloadtc | tee -a load.log
# **100, not 50, and the count assertion is what found that.** CBTRN02C does not only update existing
# balance rows -- it CREATES a row whenever a daily transaction posts to an (account, type, category)
# combination that has none, logging "TCATBAL record not found ... Creating." It creates exactly 50,
# so 50 loaded + 50 created = 100. Asserting the number I assumed would have been true (50) failed
# immediately; asserting nothing would have shipped a fixture missing half its rows, and the
# comparison built on it would have failed against records the candidate was never given.
#
# It was 94 before ADR-0047, from 44 creates. The six extra creates are the six transactions this
# runtime used to reject on a lost digit: each posts to a combination that had no balance row, so
# rejecting it also suppressed the row. 100 is the number the generated Java pipeline already
# produced -- the same cross-check as the 262 above.
expect_count "TCATBALF unloaded" 100
signback TCB "$WORK/tcatbal-posted.raw" "$OUT/tcatbal-posted.dat" 100 50

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
  echo "1. SIGNCONV converts the corpus's IBM sign overpunches into what this runtime reads"
  echo "   as signed (ADR-0047). Without it every posted amount loses its last digit and"
  echo "   its sign -- see the Representation section below."
  echo "2. CBTRN02C posts dailytran into tcatbal (the shipped tcatbal is the PRE-posting state)."
  echo "3. CBACT04C computes interest on the posted balances and writes transact.dat."
  echo "4. SIGNBACK converts every output back, so this directory is in the CORPUS's"
  echo "   representation and nothing outside the container sees GnuCOBOL's own sign bytes."
  echo
  echo "## Representation"
  echo "**The zoned-decimal sign representation is probed, not assumed** (ADR-0043, ADR-0047)."
  echo "This runtime does not recognise IBM trailing overpunches: it reads the byte as digit 0"
  echo "and drops the sign, so 0000005047G (504.77) becomes 504.70. It writes its own negatives"
  echo "as p-y in the final position. Both halves were asked of the compiler directly --"
  echo "OPTEST.cbl for what it reads, SIGNTEST.cbl for what it writes and reads back."
  echo
  echo "So the conversion is bidirectional and this directory carries the corpus's bytes."
  echo "Every signed field here was checked to decode: the sign fingerprint of the corpus is"
  echo "asserted before the run, and both counts of both conversions are asserted during it."
  echo
  echo "**This directory is the run's oracle, not one program's.** It is named for"
  echo "CBACT04C because that is what it was built for; stage 1's own outputs are here"
  echo "too, and renaming the directory would break every reference into it."
  echo "CBTRN02C's comparable outputs are transact-stage1.dat (its transaction master),"
  echo "tcatbal-posted.dat and acctdata-stage1.dat. dalyrejs.txt is produced but is out"
  echo "of scope for generation (ADR-0038) and is not committed."
  echo
  echo "tcatbal-posted.dat is the state *between* the two stages -- the input any"
  echo "candidate implementation must start from to be comparable with transact.dat."
  echo "acctdata-stage1.dat is the account file at the same instant, so the interest"
  echo "CBACT04C posted to each account is measurable as a difference against"
  echo "acctdata-posted.dat and can be checked against the transactions it wrote."
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
  for f in transact.dat transact-stage1.dat acctdata-stage1.dat acctdata-posted.dat \
           tcatbal-posted.dat dalyrejs.txt; do
    [ -f "$OUT/$f" ] && echo "- $f  $(wc -c < "$OUT/$f") bytes  sha256 $(sha256sum "$OUT/$f" | cut -d' ' -f1)"
  done
  echo
  echo "## Known-unverified against IBM Enterprise COBOL"
  echo "GnuCOBOL is not the tenant's compiler. These are NOT corroborated by ADR-0021's"
  echo "hand-derived values and must be read as findings rather than failures if they disagree:"
  echo "FUNCTION CURRENT-DATE formatting; STRING ... DELIMITED BY SIZE padding at the edges;"
  echo "and the sign of zero."
  echo
  echo "**The zoned-decimal sign representation is no longer on that list.** It was, for four"
  echo "revisions, and it came due as seven wrong decisions in CBTRN02C's round trip. It is now"
  echo "probed in both directions and converted in both directions (see Representation above)."
  echo "Its history and the reason it stayed unverified so long are in docs/qa/oracle-caveats.md,"
  echo "which every caveat named here is required to have a row in."
  echo
  echo "The interest arithmetic IS independently corroborated: it must match ADR-0021's"
  echo "hand-computed table, which was derived from the COBOL by a human without running it."
} > "$OUT/PROVENANCE.md"

echo "--- done ---"
