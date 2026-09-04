# CBACT04C oracle - provenance

Produced by tools/cobol-oracle/run-oracle.sh inside the pinned image (see Dockerfile).
**Both tenant programs run unmodified.** Only the corpus's text representation is converted.

- cobc: cobc (GnuCOBOL) 3.1.2.0
- indexed handler: indexed file handler : BDB
- dialect: -std=ibm
- PARM-DATE: 2026-08-12 (fixed; see RUNCB04.cbl)
- generated: 2026-08-21T20:11:43Z

## Pipeline
1. SIGNCONV converts the corpus's IBM sign overpunches into what this runtime reads
   as signed (ADR-0047). Without it every posted amount loses its last digit and
   its sign -- see the Representation section below.
2. CBTRN02C posts dailytran into tcatbal (the shipped tcatbal is the PRE-posting state).
3. CBACT04C computes interest on the posted balances and writes transact.dat.
4. SIGNBACK converts every output back, so this directory is in the CORPUS's
   representation and nothing outside the container sees GnuCOBOL's own sign bytes.

## Representation
**The zoned-decimal sign representation is probed, not assumed** (ADR-0043, ADR-0047).
This runtime does not recognise IBM trailing overpunches: it reads the byte as digit 0
and drops the sign, so 0000005047G (504.77) becomes 504.70. It writes its own negatives
as p-y in the final position. Both halves were asked of the compiler directly --
OPTEST.cbl for what it reads, SIGNTEST.cbl for what it writes and reads back.

So the conversion is bidirectional and this directory carries the corpus's bytes.
Every signed field here was checked to decode: the sign fingerprint of the corpus is
asserted before the run, and both counts of both conversions are asserted during it.

**This directory is the run's oracle, not one program's.** It is named for
CBACT04C because that is what it was built for; stage 1's own outputs are here
too, and renaming the directory would break every reference into it.
CBTRN02C's comparable outputs are transact-stage1.dat (its transaction master),
tcatbal-posted.dat and acctdata-stage1.dat. dalyrejs.txt is produced but is out
of scope for generation (ADR-0038) and is not committed.

tcatbal-posted.dat is the state *between* the two stages -- the input any
candidate implementation must start from to be comparable with transact.dat.
acctdata-stage1.dat is the account file at the same instant, so the interest
CBACT04C posted to each account is measurable as a difference against
acctdata-posted.dat and can be checked against the transactions it wrote.

## Input blob hashes (sha256)
- tcatbal.txt  a33eda6c526646e738164fd036ffdf525780466ade7141039bf72d4e25237afd
- cardxref.txt  efec3825ec0d5b791cf54f815bf688abfcc9db832c1600371ed2209df4e97764
- acctdata.txt  c2a97b6a32dc4a87a7aafdf7f72e6712e560412d30b00c5526cca80fc9dfd260
- discgrp.txt  dfdd3832805e3a4bf1d811ea2340ee8f6bf8e9fdf8d040fbd50e3c7b45b0ce2b
- dailytran.txt  1605206de7009cba771a921bf13f4dfcd1673fc13f1b844150355e9a95fa8da3

### Producing programs
The COBOL is as much an input as the data. Without these, an edit to a tenant program
leaves this fixture stale with no signal, and a later mismatch reads as a Java defect.
- CBACT04C.cbl  5084bb8b0c9a0f0199f737487ae1863f12e43cabbdc459a62b6b67bedc683cc4
- CBTRN02C.cbl  708f3cadc555acab63f11e2f3238f5372ac7180e6b01197bf960d96bf0d2e83f
- ORACHK: ORACHK: 009 row(s) checked, 000 mismatch(es)
- CBTRN02C exit code: 0 (0 and 4 accepted; see run-oracle.sh)
- CBTRN02C: TRANSACTIONS PROCESSED :000000300;TRANSACTIONS REJECTED :000000038

## Output
- transact.dat  17500 bytes  sha256 86994f5f80b2b329ab20316914a61fd5dac8186c2e98336309789ffe3121bec9
- transact-stage1.dat  91700 bytes  sha256 1b3016c78e7020d79dcc232c63c613d2a035f1dd4ba0b7b80f4cbd4f311b5f12
- acctdata-stage1.dat  15000 bytes  sha256 54fd60aee8aafb17c654d5eb49f8db641613f576f97595addfcd81dab0c31a6b
- acctdata-posted.dat  15000 bytes  sha256 080656286a16d037079a4d9fe8c750354125144a7ab768a63aa412bb8ffc348c
- tcatbal-posted.dat  5000 bytes  sha256 588ad9be3b7396badb8c396b87d79e16232f9327bb1a83a7e83a3f36a9e73d88
- dalyrejs.txt  16340 bytes  sha256 42c8df20262c281549ee1c461c2554ae0ddfc88e1736a5edc07ff7b22b449379

## Known-unverified against IBM Enterprise COBOL
GnuCOBOL is not the tenant's compiler. These are NOT corroborated by ADR-0021's
hand-derived values and must be read as findings rather than failures if they disagree:
FUNCTION CURRENT-DATE formatting; STRING ... DELIMITED BY SIZE padding at the edges;
and the sign of zero.

**The zoned-decimal sign representation is no longer on that list.** It was, for four
revisions, and it came due as seven wrong decisions in CBTRN02C's round trip. It is now
probed in both directions and converted in both directions (see Representation above).
Its history and the reason it stayed unverified so long are in docs/qa/oracle-caveats.md,
which every caveat named here is required to have a row in.

The interest arithmetic IS independently corroborated: it must match ADR-0021's
hand-computed table, which was derived from the COBOL by a human without running it.
