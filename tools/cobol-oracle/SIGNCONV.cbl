      *****************************************************************
      * IBM trailing sign overpunch -> the representation THIS runtime
      * reads as signed. One field, one file: DALYTRAN-AMT, byte 143.
      *
      * ADR-0043 established that GnuCOBOL 3.1.2 does not recognise
      * `{`/`}`/`A`-`R` as signed digits and coerces them to `0`, losing
      * a digit and the sign on every posted amount. ADR-0047 puts the
      * fix here rather than in this repo's decoders: the corpus ships a
      * mainframe text *representation*, and converting it is the same
      * class of step as LOADIDX framing records or DALYCONV stripping
      * terminators. Changing a decoder that agrees with the standard so
      * it agrees with a runtime that does not would fix the measurement
      * into the instrument.
      *
      * BOTH halves of the table are probed, not assumed
      * (tools/cobol-oracle/SIGNTEST.cbl, tools/cobol-oracle/OPTEST.cbl):
      * this runtime WRITES plain digits for positives and `q`-`y` for
      * -1..-9, and it READS `p` as -0 even though it never writes one
      * (arithmetic collapses -0 to +0 before the store). `}` therefore
      * maps to `p` and not to `0`: `0000009190}` is -919.00, and a `0`
      * there would silently make it +919.00.
      *
      * Runs AFTER DALYCONV, on the fixed-length file, deliberately.
      * DALYTRAN-RECORD ends in FILLER X(20) of spaces and GnuCOBOL's
      * LINE SEQUENTIAL trims trailing spaces on output -- the same trap
      * that once turned a 300-byte account record into 113 bytes.
      *
      * An unrecognised byte is a hard failure. There is no defensible
      * guess about a sign: reading it as positive changes which
      * transactions post, and the run would still exit 0.
      *****************************************************************
       IDENTIFICATION DIVISION.
       PROGRAM-ID. SIGNCONV.
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT SGN-IN  ASSIGN TO DALYRAW
                  ORGANIZATION IS SEQUENTIAL
                  FILE STATUS IS ST-IN.
           SELECT SGN-OUT ASSIGN TO DALYTRAN
                  ORGANIZATION IS SEQUENTIAL
                  FILE STATUS IS ST-OUT.

       DATA DIVISION.
       FILE SECTION.
       FD  SGN-IN.
       01  SGN-I-REC              PIC X(350).
       FD  SGN-OUT.
       01  SGN-O-REC              PIC X(350).

       WORKING-STORAGE SECTION.
       01  ST-IN                  PIC XX.
       01  ST-OUT                 PIC XX.
       01  WS-EOF                 PIC X VALUE "N".
       01  WS-COUNT               PIC 9(06) VALUE 0.
       01  WS-NEG                 PIC 9(06) VALUE 0.
       01  WS-DISP                PIC Z(05)9.
       01  WS-NDISP               PIC Z(05)9.
       01  WS-K                   PIC 9(02).
       01  WS-HIT                 PIC 9(02).
       01  WS-BYTE                PIC X.
      * DALYTRAN-AMT is PIC S9(09)V99 at CVTRA06Y offset 133; its sign
      * travels on the eleventh and last of those bytes.
       01  WS-POS                 PIC 9(03) VALUE 143.
       01  WS-IBM                 PIC X(20) VALUE
           "{ABCDEFGHI}JKLMNOPQR".
       01  WS-GNU                 PIC X(20) VALUE
           "0123456789pqrstuvwxy".

       PROCEDURE DIVISION.
       MAIN-PARA.
           OPEN INPUT SGN-IN
           IF ST-IN NOT = "00"
              DISPLAY "SIGNCONV INPUT STATUS " ST-IN
              MOVE 16 TO RETURN-CODE
              STOP RUN
           END-IF
           OPEN OUTPUT SGN-OUT
           IF ST-OUT NOT = "00"
              DISPLAY "SIGNCONV OUTPUT STATUS " ST-OUT
              MOVE 16 TO RETURN-CODE
              STOP RUN
           END-IF
           PERFORM UNTIL WS-EOF = "Y"
              READ SGN-IN
                 AT END MOVE "Y" TO WS-EOF
                 NOT AT END
                    ADD 1 TO WS-COUNT
                    MOVE SGN-I-REC TO SGN-O-REC
                    PERFORM CONVERT-SIGN
                    WRITE SGN-O-REC
              END-READ
           END-PERFORM
           CLOSE SGN-IN SGN-OUT
           MOVE WS-COUNT TO WS-DISP
           MOVE WS-NEG   TO WS-NDISP
           DISPLAY "DALYTRAN signs converted: " WS-DISP
           DISPLAY "DALYTRAN signs negative: " WS-NDISP
           GOBACK.

       CONVERT-SIGN.
           MOVE SGN-O-REC(WS-POS:1) TO WS-BYTE
           MOVE 0 TO WS-HIT
           PERFORM VARYING WS-K FROM 1 BY 1 UNTIL WS-K > 20
              IF WS-IBM(WS-K:1) = WS-BYTE
                 MOVE WS-K TO WS-HIT
                 MOVE 20 TO WS-K
              END-IF
           END-PERFORM
           IF WS-HIT = 0
              DISPLAY "ABORT: SIGNCONV record " WS-COUNT
                      " carries [" WS-BYTE "] in the sign position,"
                      " which is not an IBM overpunch"
              MOVE 16 TO RETURN-CODE
              STOP RUN
           END-IF
           MOVE WS-GNU(WS-HIT:1) TO SGN-O-REC(WS-POS:1)
           IF WS-HIT > 10
              ADD 1 TO WS-NEG
           END-IF.
