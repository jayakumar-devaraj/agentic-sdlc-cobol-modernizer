# CBACT04C oracle - provenance

Produced by tools/cobol-oracle/run-oracle.sh inside the pinned image (see Dockerfile).
**Both tenant programs run unmodified.** Only the corpus's text representation is converted.

- cobc: cobc (GnuCOBOL) 3.1.2.0
- indexed handler: indexed file handler : BDB
- dialect: -std=ibm
- PARM-DATE: 2026-08-12 (fixed; see RUNCB04.cbl)
- generated: 2026-08-21T11:59:05Z

## Pipeline
1. CBTRN02C posts dailytran into tcatbal (the shipped tcatbal is the PRE-posting state).
2. CBACT04C computes interest on the posted balances and writes transact.dat.

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
- CBTRN02C: TRANSACTIONS PROCESSED :000000300;TRANSACTIONS REJECTED :000000043

## Output
- transact.dat  17500 bytes  sha256 b4427c750af5c805d7fd2485c2ef2ada478a3d1d9f5060e6540e982d13ef969e
- transact-stage1.dat  89950 bytes  sha256 0f02a5a935ac4b3d209ffb2db0bbae54a492fadbd4ced624ea7e98fd8fda6fb8
- acctdata-stage1.dat  15000 bytes  sha256 5dfe79a147bc8c6b0a1e6e5c2b3a3df05367241da1c32622b8187eccba68195d
- acctdata-posted.dat  15000 bytes  sha256 2156833ada1d4f9f2df8820794e7a1647a3fa95f7a4021d129cbde007d04fb25
- tcatbal-posted.dat  4700 bytes  sha256 4b0a2389413ee5de0059bf0c40e1e52935e0aa265d7ac34ed7515d0e0aec1376
- dalyrejs.txt  18490 bytes  sha256 86f3b3418f44226b0df45b68b164b9d81c7d1121a5c4d98b187245cc59080cc6

## Known-unverified against IBM Enterprise COBOL
GnuCOBOL is not the tenant's compiler. These are NOT corroborated by ADR-0021's
hand-derived values and must be read as findings rather than failures if they disagree:
FUNCTION CURRENT-DATE formatting; STRING ... DELIMITED BY SIZE padding at the edges;
the sign of zero; and the zoned-decimal sign representation on REWRITE -- the first run
turned an input overpunch of 940{ into 9400, identical in value and different in bytes,
which is why ADR-0029 compares fields rather than bytes.

The interest arithmetic IS independently corroborated: it must match ADR-0021's
hand-computed table, which was derived from the COBOL by a human without running it.
