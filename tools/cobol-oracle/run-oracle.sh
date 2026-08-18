#!/bin/sh
# Produce CBACT04C's own output, once, so it can be committed as a fixture (ADR-0028).
#
# Runs inside the pinned image (see Dockerfile). Everything here is deliberately explicit rather
# than convenient: the value of the oracle is that a reader can see exactly what produced it.
#
# Expects, mounted read-only:
#   /src   the tenant repo's app/ tree (cbl, cpy, data/ASCII)
# Writes:
#   /out   the oracle and its provenance
set -eu

SRC=/src
OUT=/out
WORK=/work

mkdir -p "$OUT" "$WORK/idx"

# --- compile -----------------------------------------------------------------------------------
# CBACT04C is compiled as a *module* (-m) because it is CALLed by the driver, not run directly.
# -std=ibm because this is IBM Enterprise COBOL source; GnuCOBOL's default dialect is its own, and
# the difference is exactly the kind of thing an oracle must not silently absorb.
cd "$WORK"
cobc -m -std=ibm -I "$SRC/cpy" "$SRC/cbl/CBACT04C.cbl"
cobc -x -std=ibm "$SRC/../tools/cobol-oracle/RUNCB04.cbl" -o runcb04
cobc -x -std=ibm "$SRC/../tools/cobol-oracle/LOADIDX.cbl" -o loadidx

# --- load: flat text -> indexed ------------------------------------------------------------------
# Four of CBACT04C's five files are ORGANIZATION IS INDEXED and the corpus ships flat text, so the
# program cannot be pointed at the data as it ships. See LOADIDX.cbl.
export dd_TCBIN="$SRC/data/ASCII/tcatbal.txt"
export dd_XRFIN="$SRC/data/ASCII/cardxref.txt"
export dd_ACCIN="$SRC/data/ASCII/acctdata.txt"
export dd_DISIN="$SRC/data/ASCII/discgrp.txt"

export dd_TCATBALF="$WORK/idx/tcatbal"
export dd_XREFFILE="$WORK/idx/cardxref"
export dd_ACCTFILE="$WORK/idx/acctdata"
export dd_DISCGRP="$WORK/idx/discgrp"
export dd_TRANSACT="$OUT/transact.dat"

./loadidx

# --- run the unmodified program ------------------------------------------------------------------
# COB_FILE_FORMAT is left at its default on purpose: nothing here should coax the runtime into a
# behaviour the program did not ask for.
./runcb04 || echo "CBACT04C returned non-zero: $?"

# --- capture the account file too ----------------------------------------------------------------
# CBACT04C REWRITEs ACCOUNT-FILE, so the posted balances are an output as much as the transaction
# file is. Unloaded back to flat text so the fixture is diffable and reviewable.
cat > unload.cbl <<'UNLOAD'
       IDENTIFICATION DIVISION.
       PROGRAM-ID. UNLOAD.
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT ACC-IN ASSIGN TO ACCTFILE
                  ORGANIZATION IS INDEXED
                  ACCESS MODE IS SEQUENTIAL
                  RECORD KEY IS ACC-KEY.
           SELECT ACC-OUT ASSIGN TO ACCOUT
                  ORGANIZATION IS LINE SEQUENTIAL.
       DATA DIVISION.
       FILE SECTION.
       FD  ACC-IN.
       01  ACC-REC.
           05 ACC-KEY  PIC X(11).
           05 ACC-DATA PIC X(289).
       FD  ACC-OUT.
       01  ACC-O       PIC X(300).
       WORKING-STORAGE SECTION.
       01  WS-EOF PIC X VALUE "N".
       PROCEDURE DIVISION.
       MAIN-PARA.
           OPEN INPUT ACC-IN
           OPEN OUTPUT ACC-OUT
           PERFORM UNTIL WS-EOF = "Y"
              READ ACC-IN
                 AT END MOVE "Y" TO WS-EOF
                 NOT AT END
                    MOVE ACC-REC TO ACC-O
                    WRITE ACC-O
              END-READ
           END-PERFORM
           CLOSE ACC-IN ACC-OUT
           GOBACK.
UNLOAD
cobc -x -std=ibm unload.cbl -o unload
export dd_ACCOUT="$OUT/acctdata-posted.txt"
./unload

# --- provenance ----------------------------------------------------------------------------------
# ADR-0028 requires the fixture to carry what produced it. Written by the run, not by hand, so it
# cannot drift from the artifact beside it.
{
  echo "# CBACT04C oracle - provenance"
  echo
  echo "Produced by tools/cobol-oracle/run-oracle.sh inside the pinned image."
  echo
  echo "- cobc: $(cobc --version | head -1)"
  echo "- indexed handler: $(cobc --info | grep -i 'indexed file handler' | tr -s ' ')"
  echo "- dialect: -std=ibm"
  echo "- PARM-DATE: 2026-08-12 (fixed; see RUNCB04.cbl)"
  echo "- generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  echo "## Input blob hashes (sha256)"
  for f in tcatbal cardxref acctdata discgrp; do
    echo "- $f.txt  $(sha256sum "$SRC/data/ASCII/$f.txt" | cut -d' ' -f1)"
  done
  echo
  echo "## Output"
  echo "- transact.dat          $(wc -c < "$OUT/transact.dat") bytes"
  echo "- acctdata-posted.txt   $(wc -c < "$OUT/acctdata-posted.txt") bytes"
  echo
  echo "## Known-unverified against IBM Enterprise COBOL"
  echo "GnuCOBOL is not the tenant's compiler. Three behaviours are NOT corroborated by the"
  echo "hand-derived oracle in ADR-0021 and must be treated as findings rather than failures"
  echo "if they disagree: FUNCTION CURRENT-DATE formatting, STRING ... DELIMITED BY SIZE padding"
  echo "at the edges, and the sign of zero. The interest arithmetic IS corroborated -- it matches"
  echo "ADR-0021's hand-computed values independently."
} > "$OUT/PROVENANCE.md"

echo "--- done ---"
cat "$OUT/PROVENANCE.md"
