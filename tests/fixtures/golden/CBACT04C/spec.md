# spec.md — CBACT04C (hand-verified golden fixture)

This is the hand-verified golden `spec.md` for `CBACT04C`, per
`tests/fixtures/golden/CBACT04C/` (plan step 32). It follows the exact five-section format
`prompts/registry/spec_extractor/v1_0_0.md` specifies. The Overview, Paragraph flow, and Business
rules sections were written and verified by hand, paragraph by paragraph, against the real
`CBACT04C.cbl` source (`tests/fixtures/tenant_repo_sample/app/cbl/CBACT04C.cbl`) and its five real
copybooks. The Field reference and Flagged for human review sections are generated verbatim by
`render_known_facts` against that same real fixture, not retyped by hand — see
`tests/system/test_golden_fixture.py`, which asserts this file is fidelity-clean
(`compute_fidelity_issues(...) == []`) against a live `extract_field_mappings`/`extract_paragraphs`
run every time the test suite runs, so this fixture cannot silently drift from the real pipeline's
own deterministic output.

## Overview

CBACT04C is a batch COBOL program that calculates and posts monthly interest for CardDemo
transaction category balances. It reads every record from the transaction category balance file
(`TCATBAL-FILE`) in sequence. Whenever the account id on the current record differs from the
previous record's account id, the program first posts the previous account's accrued interest (if
any), then looks up the new account's master record and card cross-reference record. For every
transaction category balance record, it resolves an interest rate from the disclosure group file
-- falling back to a hard-coded `DEFAULT` group if no group-specific rate is configured -- and, if
that rate is non-zero, computes interest on the category balance, accumulates it for the current
account, and writes a system-generated interest transaction to the transaction output file
(`TRANSACT-FILE`). The last account's accrued interest is posted when the input file is
exhausted. Fee computation (`1400-COMPUTE-FEES`) is present in the control flow but not yet
implemented in this version of the program.

## Paragraph flow

### 0000-TCATBALF-OPEN

Opens `TCATBAL-FILE` (the transaction category balance file) for input. If `TCATBALF-STATUS` is
`'00'`, sets `APPL-RESULT` to 0 (success); otherwise sets it to 12 (error). On any non-success
result, displays an error message, moves the file status into `IO-STATUS`, performs
`9910-DISPLAY-IO-STATUS`, then performs `9999-ABEND-PROGRAM` to abend.

### 0100-XREFFILE-OPEN

Opens `XREF-FILE` (the card cross-reference file) for input, using the same status-check and
error-handling pattern as `0000-TCATBALF-OPEN`.

### 0200-DISCGRP-OPEN

Opens `DISCGRP-FILE` (the disclosure group / interest rate file) for input, using the same
status-check and error-handling pattern.

### 0300-ACCTFILE-OPEN

Opens `ACCOUNT-FILE` for I-O (both input and output, since account balances are rewritten later
by `1050-UPDATE-ACCOUNT`), using the same status-check and error-handling pattern.

### 0400-TRANFILE-OPEN

Opens `TRANSACT-FILE` (the transaction output file this program writes new interest-posting
transactions to) for output, using the same status-check and error-handling pattern.

### 1000-TCATBALF-GET-NEXT

Reads the next record from `TCATBAL-FILE` into `TRAN-CAT-BAL-RECORD`. If the file status is
`'00'`, sets `APPL-RESULT` to 0. If the status is `'10'` (end of file), sets `END-OF-FILE` to
`'Y'` rather than treating it as an error. Any other status is a genuine read error and abends the
program via `9910-DISPLAY-IO-STATUS`/`9999-ABEND-PROGRAM`.

### 1050-UPDATE-ACCOUNT

Adds the accumulated `WS-TOTAL-INT` to `ACCT-CURR-BAL`, resets `ACCT-CURR-CYC-CREDIT` and
`ACCT-CURR-CYC-DEBIT` to 0, then rewrites the account record to `ACCOUNT-FILE`. Called from the
main processing loop both when the account id on the current transaction category balance record
changes (to post the previous account's accrued interest before moving on) and again at
end-of-file (to post the last account's accrued interest).

### 1100-GET-ACCT-DATA

Reads `ACCOUNT-FILE` keyed by `FD-ACCT-ID` into `ACCOUNT-RECORD`; on `INVALID KEY`, displays an
"ACCOUNT NOT FOUND" message. Uses the same status-check and error-handling pattern as the file-open
paragraphs. Called only when the account id changes, from the main processing loop.

### 1110-GET-XREF-DATA

Reads `XREF-FILE` by the `FD-XREF-ACCT-ID` alternate key into `CARD-XREF-RECORD`; on
`INVALID KEY`, displays an "ACCOUNT NOT FOUND" message for the cross-reference lookup. Same
status-check and error-handling pattern. Called only when the account id changes, immediately
after `1100-GET-ACCT-DATA`.

### 1200-GET-INTEREST-RATE

Reads `DISCGRP-FILE` keyed by the current transaction's (account group id, transaction type code,
transaction category code) into `DIS-GROUP-RECORD`; treats status `'00'` or `'23'`
(record-not-found) as non-error. If `DISCGRP-STATUS` is `'23'` (no group-specific disclosure
record exists for this combination), moves the literal `'DEFAULT'` into `FD-DIS-ACCT-GROUP-ID` and
performs `1200-A-GET-DEFAULT-INT-RATE` to re-read the file with the default group id instead.

### 1200-A-GET-DEFAULT-INT-RATE

Re-reads `DISCGRP-FILE` (now keyed with `'DEFAULT'` as the account group id, set by the caller)
into `DIS-GROUP-RECORD`. Standard status-check and error-handling pattern -- any status other than
`'00'` here is treated as a fatal error, unlike the `'23'` fallback in the caller.

### 1300-COMPUTE-INTEREST

Computes `COMPUTE WS-MONTHLY-INT = ( TRAN-CAT-BAL * DIS-INT-RATE) / 1200` -- monthly interest is
the transaction category balance times the resolved annual interest rate, divided by 1200. Adds
`WS-MONTHLY-INT` to the running `WS-TOTAL-INT` accumulator for the current account, then performs
`1300-B-WRITE-TX` to write an interest transaction record. Only called when `DIS-INT-RATE` is
non-zero.

### 1300-B-WRITE-TX

Builds a new transaction record and writes it to `TRANSACT-FILE`: increments
`WS-TRANID-SUFFIX` and builds `TRAN-ID` by concatenating the run's `PARM-DATE` with that suffix;
sets `TRAN-TYPE-CD` to `'01'` and `TRAN-CAT-CD` to `'05'` (fixed codes identifying this as a
system-generated interest posting); sets `TRAN-SOURCE` to `'System'`; builds `TRAN-DESC` as
`"Int. for a/c "` followed by the account id; moves `WS-MONTHLY-INT` into `TRAN-AMT`; zeroes or
blanks every merchant field (`TRAN-MERCHANT-ID`/`NAME`/`CITY`/`ZIP`), since this is a
system-generated transaction with no merchant; copies the card number from the cross-reference
lookup into `TRAN-CARD-NUM`; performs `Z-GET-DB2-FORMAT-TIMESTAMP` and stamps both
`TRAN-ORIG-TS` and `TRAN-PROC-TS` with the resulting DB2-format timestamp. Standard status-check
and error-handling pattern on the `WRITE`.

### 1400-COMPUTE-FEES

Currently an unimplemented stub -- the paragraph body is only the comment `* To be implemented`
followed by `EXIT`. It performs no fee computation despite being called unconditionally alongside
`1300-COMPUTE-INTEREST` whenever a non-zero interest rate was found.

### 9000-TCATBALF-CLOSE

Closes `TCATBAL-FILE`, using the same status-check and error-handling pattern as the file-open
paragraphs.

### 9100-XREFFILE-CLOSE

Closes `XREF-FILE`, same status-check and error-handling pattern.

### 9200-DISCGRP-CLOSE

Closes `DISCGRP-FILE`, same status-check and error-handling pattern.

### 9300-ACCTFILE-CLOSE

Closes `ACCOUNT-FILE`, same status-check and error-handling pattern.

### 9400-TRANFILE-CLOSE

Closes `TRANSACT-FILE`, same status-check and error-handling pattern.

### Z-GET-DB2-FORMAT-TIMESTAMP

Moves `FUNCTION CURRENT-DATE` into `COBOL-TS`, then copies each component (year, month, day,
hour, minute, second, hundredths) into the corresponding `DB2-FORMAT-TS` fields (via the
`FILLER REDEFINES DB2-FORMAT-TS` alias), inserts literal `'-'` separators between the date
components and `'.'` separators between the time components, and sets a fixed `'0000'` suffix --
producing a DB2-style timestamp string. Called by `1300-B-WRITE-TX` for every interest
transaction it writes.

### 9999-ABEND-PROGRAM

Displays `'ABENDING PROGRAM'`, sets `TIMING` to 0 and `ABCODE` to 999, then calls the `CEE3ABD`
system service to abnormally terminate the program with that abend code. Performed from every
file operation's error branch as the final step after `9910-DISPLAY-IO-STATUS`.

### 9910-DISPLAY-IO-STATUS

Formats and displays the current file status for diagnostics. If `IO-STATUS` is not numeric, or
its first byte is `'9'` (a raw device/VSAM status rather than a simple two-digit file status),
converts the second status byte to a numeric value (via the `TWO-BYTES-ALPHA REDEFINES
TWO-BYTES-BINARY` alias) and displays it as part of a 4-digit `"FILE STATUS IS: NNNN"` message;
otherwise formats the plain two-digit status into the same 4-digit display format. Performed from
every file operation's error branch, immediately before `9999-ABEND-PROGRAM`.

## Business rules

- Monthly interest is computed as `COMPUTE WS-MONTHLY-INT = ( TRAN-CAT-BAL * DIS-INT-RATE) / 1200`
  -- the transaction category balance times the (annual, percentage) interest rate, divided by
  1200 to convert an annual percentage rate into a monthly decimal amount in one step.
- Account data and the card cross-reference record are only re-fetched when the account id on the
  current transaction category balance record differs from the previous record's account id --
  not on every transaction category balance record.
- If no disclosure-group record exists for an account's own group/transaction-type/category
  combination (`DISCGRP-STATUS = '23'`), the program falls back to a hard-coded `'DEFAULT'` group
  id and re-reads the disclosure file, rather than skipping interest calculation for that account.
- Interest is computed at all only if the resolved `DIS-INT-RATE` is non-zero
  (`IF DIS-INT-RATE NOT = 0`) -- a zero-rate group produces no interest transaction and no fee
  computation for that transaction category balance record.
- Interest accrued across every transaction category balance record for the same account
  (`WS-TOTAL-INT`) is posted to the account's `ACCT-CURR-BAL` only when the program moves on to a
  different account, or reaches end-of-file -- not per transaction category balance record.
- Posting an account's accrued interest also resets that account's `ACCT-CURR-CYC-CREDIT` and
  `ACCT-CURR-CYC-DEBIT` cycle counters to zero.
- Every generated interest transaction uses fixed codes `TRAN-TYPE-CD = '01'`,
  `TRAN-CAT-CD = '05'`, and `TRAN-SOURCE = 'System'`, with no merchant information (merchant id
  zeroed, merchant name/city/zip blank) -- distinguishing system-generated interest postings from
  customer-initiated transactions.
- Fee computation (`1400-COMPUTE-FEES`) is invoked whenever an interest rate was found and
  interest was computed, but its body is an unimplemented stub (`* To be implemented`) -- no fee
  logic currently executes.
- Any non-EOF, non-zero file status on any file operation (open, read, rewrite, write, close) is
  treated as a fatal error: the program displays the failing status via `9910-DISPLAY-IO-STATUS`
  and abends via `CEE3ABD` (`9999-ABEND-PROGRAM`) rather than continuing.

## Field reference

| Field | PIC | Java type | Precision | Scale | Signed |
|---|---|---|---|---|---|
| FD-TRANCAT-ACCT-ID | 9(11) | BigDecimal | 11 | 0 | False |
| FD-TRANCAT-TYPE-CD | X(02) | String | - | - | False |
| FD-TRANCAT-CD | 9(04) | BigDecimal | 4 | 0 | False |
| FD-FD-TRAN-CAT-DATA | X(33) | String | - | - | False |
| FD-XREF-CARD-NUM | X(16) | String | - | - | False |
| FD-XREF-CUST-NUM | 9(09) | BigDecimal | 9 | 0 | False |
| FD-XREF-ACCT-ID | 9(11) | BigDecimal | 11 | 0 | False |
| FD-XREF-FILLER | X(14) | String | - | - | False |
| FD-DIS-ACCT-GROUP-ID | X(10) | String | - | - | False |
| FD-DIS-TRAN-TYPE-CD | X(02) | String | - | - | False |
| FD-DIS-TRAN-CAT-CD | 9(04) | BigDecimal | 4 | 0 | False |
| FD-DISCGRP-DATA | X(34) | String | - | - | False |
| FD-ACCT-ID | 9(11) | BigDecimal | 11 | 0 | False |
| FD-ACCT-DATA | X(289) | String | - | - | False |
| FD-TRANS-ID | X(16) | String | - | - | False |
| FD-ACCT-DATA | X(334) | String | - | - | False |
| TCATBALF-STAT1 | X | String | - | - | False |
| TCATBALF-STAT2 | X | String | - | - | False |
| XREFFILE-STAT1 | X | String | - | - | False |
| XREFFILE-STAT2 | X | String | - | - | False |
| DISCGRP-STAT1 | X | String | - | - | False |
| DISCGRP-STAT2 | X | String | - | - | False |
| ACCTFILE-STAT1 | X | String | - | - | False |
| ACCTFILE-STAT2 | X | String | - | - | False |
| TRANFILE-STAT1 | X | String | - | - | False |
| TRANFILE-STAT2 | X | String | - | - | False |
| IO-STAT1 | X | String | - | - | False |
| IO-STAT2 | X | String | - | - | False |
| TWO-BYTES-BINARY | 9(4) | BigDecimal | 4 | 0 | False |
| IO-STATUS-0401 | 9 | BigDecimal | 1 | 0 | False |
| IO-STATUS-0403 | 999 | BigDecimal | 3 | 0 | False |
| APPL-RESULT | S9(9) | BigDecimal | 9 | 0 | True |
| END-OF-FILE | X(01) | String | - | - | False |
| ABCODE | S9(9) | BigDecimal | 9 | 0 | True |
| TIMING | S9(9) | BigDecimal | 9 | 0 | True |
| COB-YYYY | X(04) | String | - | - | False |
| COB-MM | X(02) | String | - | - | False |
| COB-DD | X(02) | String | - | - | False |
| COB-HH | X(02) | String | - | - | False |
| COB-MIN | X(02) | String | - | - | False |
| COB-SS | X(02) | String | - | - | False |
| COB-MIL | X(02) | String | - | - | False |
| COB-REST | X(05) | String | - | - | False |
| DB2-FORMAT-TS | X(26) | String | - | - | False |
| WS-LAST-ACCT-NUM | X(11) | String | - | - | False |
| WS-MONTHLY-INT | S9(09)V99 | BigDecimal | 11 | 2 | True |
| WS-TOTAL-INT | S9(09)V99 | BigDecimal | 11 | 2 | True |
| WS-FIRST-TIME | X(01) | String | - | - | False |
| WS-RECORD-COUNT | 9(09) | BigDecimal | 9 | 0 | False |
| WS-TRANID-SUFFIX | 9(06) | BigDecimal | 6 | 0 | False |
| PARM-LENGTH | S9(04) | BigDecimal | 4 | 0 | True |
| PARM-DATE | X(10) | String | - | - | False |
| TRANCAT-ACCT-ID | 9(11) | BigDecimal | 11 | 0 | False |
| TRANCAT-TYPE-CD | X(02) | String | - | - | False |
| TRANCAT-CD | 9(04) | BigDecimal | 4 | 0 | False |
| TRAN-CAT-BAL | S9(09)V99 | BigDecimal | 11 | 2 | True |
| FILLER | X(22) | String | - | - | False |
| XREF-CARD-NUM | X(16) | String | - | - | False |
| XREF-CUST-ID | 9(09) | BigDecimal | 9 | 0 | False |
| XREF-ACCT-ID | 9(11) | BigDecimal | 11 | 0 | False |
| FILLER | X(14) | String | - | - | False |
| DIS-ACCT-GROUP-ID | X(10) | String | - | - | False |
| DIS-TRAN-TYPE-CD | X(02) | String | - | - | False |
| DIS-TRAN-CAT-CD | 9(04) | BigDecimal | 4 | 0 | False |
| DIS-INT-RATE | S9(04)V99 | BigDecimal | 6 | 2 | True |
| FILLER | X(28) | String | - | - | False |
| ACCT-ID | 9(11) | BigDecimal | 11 | 0 | False |
| ACCT-ACTIVE-STATUS | X(01) | String | - | - | False |
| ACCT-CURR-BAL | S9(10)V99 | BigDecimal | 12 | 2 | True |
| ACCT-CREDIT-LIMIT | S9(10)V99 | BigDecimal | 12 | 2 | True |
| ACCT-CASH-CREDIT-LIMIT | S9(10)V99 | BigDecimal | 12 | 2 | True |
| ACCT-OPEN-DATE | X(10) | String | - | - | False |
| ACCT-EXPIRAION-DATE | X(10) | String | - | - | False |
| ACCT-REISSUE-DATE | X(10) | String | - | - | False |
| ACCT-CURR-CYC-CREDIT | S9(10)V99 | BigDecimal | 12 | 2 | True |
| ACCT-CURR-CYC-DEBIT | S9(10)V99 | BigDecimal | 12 | 2 | True |
| ACCT-ADDR-ZIP | X(10) | String | - | - | False |
| ACCT-GROUP-ID | X(10) | String | - | - | False |
| FILLER | X(178) | String | - | - | False |
| TRAN-ID | X(16) | String | - | - | False |
| TRAN-TYPE-CD | X(02) | String | - | - | False |
| TRAN-CAT-CD | 9(04) | BigDecimal | 4 | 0 | False |
| TRAN-SOURCE | X(10) | String | - | - | False |
| TRAN-DESC | X(100) | String | - | - | False |
| TRAN-AMT | S9(09)V99 | BigDecimal | 11 | 2 | True |
| TRAN-MERCHANT-ID | 9(09) | BigDecimal | 9 | 0 | False |
| TRAN-MERCHANT-NAME | X(50) | String | - | - | False |
| TRAN-MERCHANT-CITY | X(50) | String | - | - | False |
| TRAN-MERCHANT-ZIP | X(10) | String | - | - | False |
| TRAN-CARD-NUM | X(16) | String | - | - | False |
| TRAN-ORIG-TS | X(26) | String | - | - | False |
| TRAN-PROC-TS | X(26) | String | - | - | False |
| FILLER | X(20) | String | - | - | False |

## Flagged for human review

- `TWO-BYTES-LEFT` in CBACT04C: Unsupported construct 'REDEFINES' detected in field declaration; per ADR-0002 this must route to a human gate, not be parsed: '           05  TWO-BYTES-LEFT      PIC X.                                       \n       01  TWO-BYTES-ALPHA         REDEFINES TWO-BYTES-BINARY.                  \n           05  TWO-BYTES-RIGHT     PIC X.                                       '
- `TWO-BYTES-RIGHT` in CBACT04C: Unsupported construct 'REDEFINES' detected in field declaration; per ADR-0002 this must route to a human gate, not be parsed: '           05  TWO-BYTES-RIGHT     PIC X.                                       \n       01  TWO-BYTES-ALPHA         REDEFINES TWO-BYTES-BINARY.                  \n           05  TWO-BYTES-LEFT      PIC X.                                       '
- `DB2-YYYY` in CBACT04C: Unsupported construct 'REDEFINES' detected in field declaration; per ADR-0002 this must route to a human gate, not be parsed: '           06 DB2-YYYY                  PIC X(004).                      E      \n           06 DB2-STREEP-1              PIC X.                           -      \n           06 DB2-MM                    PIC X(002).                      M      \n           06 DB2-STREEP-2              PIC X.                           -      \n           06 DB2-DD                    PIC X(002).                      D      \n           06 DB2-STREEP-3              PIC X.                           -      \n           06 DB2-HH                    PIC X(002).                      U      \n           06 DB2-DOT-1                 PIC X.                                  \n       01  FILLER REDEFINES DB2-FORMAT-TS.                                      \n           06 DB2-MIN                   PIC X(002).                             \n           06 DB2-DOT-2                 PIC X.                                  \n           06 DB2-SS                    PIC X(002).                             \n           06 DB2-DOT-3                 PIC X.                                  \n           06 DB2-MIL                   PIC 9(002).                             \n           06 DB2-REST                  PIC X(04).                              '
- `DB2-MIN` in CBACT04C: Unsupported construct 'REDEFINES' detected in field declaration; per ADR-0002 this must route to a human gate, not be parsed: '           06 DB2-MIN                   PIC X(002).                             \n       01  FILLER REDEFINES DB2-FORMAT-TS.                                      \n           06 DB2-YYYY                  PIC X(004).                      E      \n           06 DB2-STREEP-1              PIC X.                           -      \n           06 DB2-MM                    PIC X(002).                      M      \n           06 DB2-STREEP-2              PIC X.                           -      \n           06 DB2-DD                    PIC X(002).                      D      \n           06 DB2-STREEP-3              PIC X.                           -      \n           06 DB2-HH                    PIC X(002).                      U      \n           06 DB2-DOT-1                 PIC X.                                  \n           06 DB2-DOT-2                 PIC X.                                  \n           06 DB2-SS                    PIC X(002).                             \n           06 DB2-DOT-3                 PIC X.                                  \n           06 DB2-MIL                   PIC 9(002).                             \n           06 DB2-REST                  PIC X(04).                              '
- `DB2-DOT-2` in CBACT04C: Unsupported construct 'REDEFINES' detected in field declaration; per ADR-0002 this must route to a human gate, not be parsed: '           06 DB2-DOT-2                 PIC X.                                  \n       01  FILLER REDEFINES DB2-FORMAT-TS.                                      \n           06 DB2-YYYY                  PIC X(004).                      E      \n           06 DB2-STREEP-1              PIC X.                           -      \n           06 DB2-MM                    PIC X(002).                      M      \n           06 DB2-STREEP-2              PIC X.                           -      \n           06 DB2-DD                    PIC X(002).                      D      \n           06 DB2-STREEP-3              PIC X.                           -      \n           06 DB2-HH                    PIC X(002).                      U      \n           06 DB2-DOT-1                 PIC X.                                  \n           06 DB2-MIN                   PIC X(002).                             \n           06 DB2-SS                    PIC X(002).                             \n           06 DB2-DOT-3                 PIC X.                                  \n           06 DB2-MIL                   PIC 9(002).                             \n           06 DB2-REST                  PIC X(04).                              '
- `DB2-SS` in CBACT04C: Unsupported construct 'REDEFINES' detected in field declaration; per ADR-0002 this must route to a human gate, not be parsed: '           06 DB2-SS                    PIC X(002).                             \n       01  FILLER REDEFINES DB2-FORMAT-TS.                                      \n           06 DB2-YYYY                  PIC X(004).                      E      \n           06 DB2-STREEP-1              PIC X.                           -      \n           06 DB2-MM                    PIC X(002).                      M      \n           06 DB2-STREEP-2              PIC X.                           -      \n           06 DB2-DD                    PIC X(002).                      D      \n           06 DB2-STREEP-3              PIC X.                           -      \n           06 DB2-HH                    PIC X(002).                      U      \n           06 DB2-DOT-1                 PIC X.                                  \n           06 DB2-MIN                   PIC X(002).                             \n           06 DB2-DOT-2                 PIC X.                                  \n           06 DB2-DOT-3                 PIC X.                                  \n           06 DB2-MIL                   PIC 9(002).                             \n           06 DB2-REST                  PIC X(04).                              '
- `DB2-DOT-3` in CBACT04C: Unsupported construct 'REDEFINES' detected in field declaration; per ADR-0002 this must route to a human gate, not be parsed: '           06 DB2-DOT-3                 PIC X.                                  \n       01  FILLER REDEFINES DB2-FORMAT-TS.                                      \n           06 DB2-YYYY                  PIC X(004).                      E      \n           06 DB2-STREEP-1              PIC X.                           -      \n           06 DB2-MM                    PIC X(002).                      M      \n           06 DB2-STREEP-2              PIC X.                           -      \n           06 DB2-DD                    PIC X(002).                      D      \n           06 DB2-STREEP-3              PIC X.                           -      \n           06 DB2-HH                    PIC X(002).                      U      \n           06 DB2-DOT-1                 PIC X.                                  \n           06 DB2-MIN                   PIC X(002).                             \n           06 DB2-DOT-2                 PIC X.                                  \n           06 DB2-SS                    PIC X(002).                             \n           06 DB2-MIL                   PIC 9(002).                             \n           06 DB2-REST                  PIC X(04).                              '
- `DB2-MIL` in CBACT04C: Unsupported construct 'REDEFINES' detected in field declaration; per ADR-0002 this must route to a human gate, not be parsed: '           06 DB2-MIL                   PIC 9(002).                             \n       01  FILLER REDEFINES DB2-FORMAT-TS.                                      \n           06 DB2-YYYY                  PIC X(004).                      E      \n           06 DB2-STREEP-1              PIC X.                           -      \n           06 DB2-MM                    PIC X(002).                      M      \n           06 DB2-STREEP-2              PIC X.                           -      \n           06 DB2-DD                    PIC X(002).                      D      \n           06 DB2-STREEP-3              PIC X.                           -      \n           06 DB2-HH                    PIC X(002).                      U      \n           06 DB2-DOT-1                 PIC X.                                  \n           06 DB2-MIN                   PIC X(002).                             \n           06 DB2-DOT-2                 PIC X.                                  \n           06 DB2-SS                    PIC X(002).                             \n           06 DB2-DOT-3                 PIC X.                                  \n           06 DB2-REST                  PIC X(04).                              '
- `DB2-REST` in CBACT04C: Unsupported construct 'REDEFINES' detected in field declaration; per ADR-0002 this must route to a human gate, not be parsed: '           06 DB2-REST                  PIC X(04).                              \n       01  FILLER REDEFINES DB2-FORMAT-TS.                                      \n           06 DB2-YYYY                  PIC X(004).                      E      \n           06 DB2-STREEP-1              PIC X.                           -      \n           06 DB2-MM                    PIC X(002).                      M      \n           06 DB2-STREEP-2              PIC X.                           -      \n           06 DB2-DD                    PIC X(002).                      D      \n           06 DB2-STREEP-3              PIC X.                           -      \n           06 DB2-HH                    PIC X(002).                      U      \n           06 DB2-DOT-1                 PIC X.                                  \n           06 DB2-MIN                   PIC X(002).                             \n           06 DB2-DOT-2                 PIC X.                                  \n           06 DB2-SS                    PIC X(002).                             \n           06 DB2-DOT-3                 PIC X.                                  \n           06 DB2-MIL                   PIC 9(002).                             '
