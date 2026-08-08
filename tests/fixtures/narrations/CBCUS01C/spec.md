# spec.md — CBCUS01C

## Overview

`CBCUS01C` is a batch report program that reads the CardDemo customer master file (`CUSTFILE`, a KSDS/VSAM indexed file keyed on `FD-CUST-ID`) sequentially from beginning to end and prints every customer record it finds. It has no update, calculation, or output-file behavior: it opens the file, loops until end-of-file, displays each 500-byte customer record, closes the file, and ends. Its only real logic is defensive I/O status handling — any file status other than `'00'` (success) or `'10'` (end of file) is treated as a fatal error, which prints the file status in a normalized four-digit form and then abends the program via `CEE3ABD`. The unnamed mainline of the `PROCEDURE DIVISION` (not itself a named paragraph) displays a start banner, performs `0000-CUSTFILE-OPEN`, then loops `PERFORM UNTIL END-OF-FILE = 'Y'` calling `1000-CUSTFILE-GET-NEXT` and displaying `CUSTOMER-RECORD` when the read did not hit end-of-file, then performs `9000-CUSTFILE-CLOSE`, displays an end banner, and `GOBACK`s.

## Paragraph flow

### 1000-CUSTFILE-GET-NEXT

Reads the next record sequentially: `READ CUSTFILE-FILE INTO CUSTOMER-RECORD`, which moves the 500-byte file record into the `CUSTOMER-RECORD` working-storage layout copied in from `CVCUS01Y`.

It then classifies the result into `APPL-RESULT`:

- If `CUSTFILE-STATUS = '00'` (successful read): `MOVE 0 TO APPL-RESULT` and `DISPLAY CUSTOMER-RECORD`.
- Else if `CUSTFILE-STATUS = '10'` (end of file): `MOVE 16 TO APPL-RESULT`.
- Else (any other status): `MOVE 12 TO APPL-RESULT`.

A second decision block then acts on that code, using the level-88 conditions `APPL-AOK` (value 0) and `APPL-EOF` (value 16):

- If `APPL-AOK`, `CONTINUE` — nothing further happens and control returns to the mainline loop.
- Else if `APPL-EOF`, `MOVE 'Y' TO END-OF-FILE`, which terminates the mainline loop.
- Else, `DISPLAY 'ERROR READING CUSTOMER FILE'`, `MOVE CUSTFILE-STATUS TO IO-STATUS`, then `PERFORM Z-DISPLAY-IO-STATUS` and `PERFORM Z-ABEND-PROGRAM`.

Calls: `Z-DISPLAY-IO-STATUS`, `Z-ABEND-PROGRAM` (error path only).

Note for the Java implementer: on a successful read the record is displayed **here**, and the mainline loop displays `CUSTOMER-RECORD` again immediately afterwards. Each customer record is therefore printed twice per iteration. This looks like a defect in the original, but it is the program's actual observable behavior and must be reproduced if output parity is the requirement.

### 0000-CUSTFILE-OPEN

Opens the customer file for input. It pre-sets a failure code before the operation — `MOVE 8 TO APPL-RESULT` — so that an unclassified outcome does not read as success, then issues `OPEN INPUT CUSTFILE-FILE`.

- If `CUSTFILE-STATUS = '00'`: `MOVE 0 TO APPL-RESULT`.
- Else: `MOVE 12 TO APPL-RESULT`.

Then, if `APPL-AOK`, `CONTINUE`; otherwise `DISPLAY 'ERROR OPENING CUSTFILE'`, `MOVE CUSTFILE-STATUS TO IO-STATUS`, `PERFORM Z-DISPLAY-IO-STATUS`, `PERFORM Z-ABEND-PROGRAM`. Note there is no `APPL-EOF` branch here — any non-`'00'` open status is fatal.

Calls: `Z-DISPLAY-IO-STATUS`, `Z-ABEND-PROGRAM` (error path only).

### 9000-CUSTFILE-CLOSE

Closes the customer file. Structurally identical to the open paragraph, but it manipulates `APPL-RESULT` with arithmetic statements rather than `MOVE`:

- `ADD 8 TO ZERO GIVING APPL-RESULT` — sets `APPL-RESULT` to 8 (the pre-set failure code).
- `CLOSE CUSTFILE-FILE`.
- If `CUSTFILE-STATUS = '00'`: `SUBTRACT APPL-RESULT FROM APPL-RESULT` — subtracts the field from itself, i.e. sets it to 0 (success).
- Else: `ADD 12 TO ZERO GIVING APPL-RESULT` — sets it to 12 (failure).

Then, if `APPL-AOK`, `CONTINUE`; otherwise `DISPLAY 'ERROR CLOSING CUSTOMER FILE'`, `MOVE CUSTFILE-STATUS TO IO-STATUS`, `PERFORM Z-DISPLAY-IO-STATUS`, `PERFORM Z-ABEND-PROGRAM`.

Calls: `Z-DISPLAY-IO-STATUS`, `Z-ABEND-PROGRAM` (error path only).

### Z-ABEND-PROGRAM

The fatal-error terminator. It displays `'ABENDING PROGRAM'`, then `MOVE 0 TO TIMING` and `MOVE 999 TO ABCODE`, and issues `CALL 'CEE3ABD' USING ABCODE, TIMING`. `CEE3ABD` is the Language Environment abend service: abend code 999, timing 0 meaning abend immediately rather than deferring to normal enclave termination. Control does not return from this call — the program terminates abnormally. A Java implementation must treat this as an immediate, non-recoverable failure exit (non-zero exit status / unchecked exception that is not caught), not as a logged warning.

Calls: none (external `CEE3ABD` only).

### Z-DISPLAY-IO-STATUS

Formats and displays the two-character file status as a four-digit value in `IO-STATUS-04`, which is composed of `IO-STATUS-0401` (`PIC 9`) followed by `IO-STATUS-0403` (`PIC 999`). Mainframe file statuses can be non-numeric or use a binary second byte when the first byte is `'9'`, so there are two formatting paths:

- If `IO-STATUS NOT NUMERIC` **or** `IO-STAT1 = '9'`: `MOVE IO-STAT1 TO IO-STATUS-04(1:1)` (first status byte into the leading digit position), `MOVE 0 TO TWO-BYTES-BINARY`, `MOVE IO-STAT2 TO TWO-BYTES-RIGHT` (the second status byte is placed into the low-order byte of a zeroed halfword, reinterpreting it as an unsigned binary number), `MOVE TWO-BYTES-BINARY TO IO-STATUS-0403` (that numeric value becomes the trailing three digits), then `DISPLAY 'FILE STATUS IS: NNNN' IO-STATUS-04`.
- Else (an ordinary numeric status such as `'00'`, `'10'`, `'23'`): `MOVE '0000' TO IO-STATUS-04`, `MOVE IO-STATUS TO IO-STATUS-04(3:2)` (the two status characters into the third and fourth positions), then `DISPLAY 'FILE STATUS IS: NNNN' IO-STATUS-04`.

Calls: none.

The `TWO-BYTES-RIGHT` reference in the first branch depends on the `REDEFINES` overlay of `TWO-BYTES-ALPHA` over `TWO-BYTES-BINARY`; see **Flagged for human review** below. A Java implementation cannot infer this byte-level reinterpretation from the field table alone.

## Business rules

- The customer file is read **sequentially** from the start, in key order on `FD-CUST-ID`; there is no keyed/random access, no filtering, and no restart logic.
- The read loop is driven solely by the `END-OF-FILE` flag (`PIC X(01)`, initialized to `'N'`, set to `'Y'` only on file status `'10'`).
- File status `'00'` means success; `'10'` means normal end of file and is **not** an error; every other status on read, open, or close is fatal.
- On a fatal I/O status: print the operation-specific error message, copy `CUSTFILE-STATUS` into `IO-STATUS`, print the normalized four-digit status, then abend with code 999 and timing 0. No cleanup, no file close, no partial-success exit code.
- The open paragraph pre-sets `APPL-RESULT` to 8 and the close paragraph to 8 before their operations, so an unset result never evaluates as `APPL-AOK`.
- Success/failure is decided only through the level-88 conditions: `APPL-AOK` = 0, `APPL-EOF` = 16. Code 12 (and the pre-set 8) fall through to the abend path.
- Each successfully-read record is displayed **twice** — once inside `1000-CUSTFILE-GET-NEXT` and once in the mainline loop after the read returns without end-of-file. Reproduce this exactly if output must match; flag it as a candidate defect if it must not.
- End-of-file handling: when status `'10'` is returned, `CUSTOMER-RECORD` is **not** displayed for that iteration (the mainline re-checks `END-OF-FILE = 'N'` before displaying) and the loop exits cleanly to `9000-CUSTFILE-CLOSE`.
- Status formatting has two distinct branches, and the non-numeric / `'9'`-prefix branch reinterprets the second status byte as an unsigned binary number rather than as a digit character.
- The program produces no output file and mutates no data; all output is to the job's display/SYSOUT stream.
- No arithmetic is performed on any customer data field. The only arithmetic in the program is on the `APPL-RESULT` return code, and it involves no rounding, scaling, or truncation.

## Field reference

| Field | PIC | Java type | Precision | Scale | Signed |
|---|---|---|---|---|---|
| FD-CUST-ID | 9(09) | BigDecimal | 9 | 0 | False |
| FD-CUST-DATA | X(491) | String | - | - | False |
| CUSTFILE-STAT1 | X | String | - | - | False |
| CUSTFILE-STAT2 | X | String | - | - | False |
| IO-STAT1 | X | String | - | - | False |
| IO-STAT2 | X | String | - | - | False |
| TWO-BYTES-BINARY | 9(4) | BigDecimal | 4 | 0 | False |
| IO-STATUS-0401 | 9 | BigDecimal | 1 | 0 | False |
| IO-STATUS-0403 | 999 | BigDecimal | 3 | 0 | False |
| APPL-RESULT | S9(9) | BigDecimal | 9 | 0 | True |
| END-OF-FILE | X(01) | String | - | - | False |
| ABCODE | S9(9) | BigDecimal | 9 | 0 | True |
| TIMING | S9(9) | BigDecimal | 9 | 0 | True |
| CUST-ID | 9(09) | BigDecimal | 9 | 0 | False |
| CUST-FIRST-NAME | X(25) | String | - | - | False |
| CUST-MIDDLE-NAME | X(25) | String | - | - | False |
| CUST-LAST-NAME | X(25) | String | - | - | False |
| CUST-ADDR-LINE-1 | X(50) | String | - | - | False |
| CUST-ADDR-LINE-2 | X(50) | String | - | - | False |
| CUST-ADDR-LINE-3 | X(50) | String | - | - | False |
| CUST-ADDR-STATE-CD | X(02) | String | - | - | False |
| CUST-ADDR-COUNTRY-CD | X(03) | String | - | - | False |
| CUST-ADDR-ZIP | X(10) | String | - | - | False |
| CUST-PHONE-NUM-1 | X(15) | String | - | - | False |
| CUST-PHONE-NUM-2 | X(15) | String | - | - | False |
| CUST-SSN | 9(09) | BigDecimal | 9 | 0 | False |
| CUST-GOVT-ISSUED-ID | X(20) | String | - | - | False |
| CUST-DOB-YYYY-MM-DD | X(10) | String | - | - | False |
| CUST-EFT-ACCOUNT-ID | X(10) | String | - | - | False |
| CUST-PRI-CARD-HOLDER-IND | X(01) | String | - | - | False |
| CUST-FICO-CREDIT-SCORE | 9(03) | BigDecimal | 3 | 0 | False |
| FILLER | X(168) | String | - | - | False |

## Flagged for human review

- **`TWO-BYTES-LEFT` (CBCUS01C)** — declared under `01 TWO-BYTES-ALPHA REDEFINES TWO-BYTES-BINARY`. `REDEFINES` is an unsupported construct per ADR-0002 (a hand-rolled parser for a deliberately bounded grammar); the parser will not guess a Java shape for a field that shares storage with another field, so this routes to a human gate. A reviewer must decide how the byte-level overlay of a halfword binary field is represented in Java.
- **`TWO-BYTES-RIGHT` (CBCUS01C)** — same `REDEFINES` overlay of `TWO-BYTES-BINARY`. Flagged for the same reason, and it is load-bearing: `Z-DISPLAY-IO-STATUS` writes `IO-STAT2` into `TWO-BYTES-RIGHT` and then reads the result back out of `TWO-BYTES-BINARY` as a number. The non-numeric file-status formatting path cannot be implemented correctly without a human decision on how this aliasing is modeled.