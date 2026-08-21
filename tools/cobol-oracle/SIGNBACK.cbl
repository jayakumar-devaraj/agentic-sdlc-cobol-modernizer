      *****************************************************************
      * The oracle directory is in the CORPUS's representation. Full
      * stop -- and this is what keeps that true.
      *
      * SIGNCONV converts the corpus into what this runtime reads on the
      * way IN. That leaves the runtime's own signs on everything it
      * writes: GnuCOBOL emits "q"-"y" for -1..-9, a convention no
      * mainframe file uses and no part of this platform outside the
      * container should have to know about.
      *
      * Two consumers make that a real problem rather than a cosmetic
      * one, and only the second is obvious:
      *
      *   1. data_loader.decode_zoned_decimal reads the fixture, and
      *      refuses an unrecognised sign byte by design (ADR-0043).
      *   2. tcatbal-posted.dat and acctdata-stage1.dat are INPUTS to
      *      the generated Java, not just comparison targets. Teaching
      *      CobolRecord.number this runtime's encoding would put a test
      *      harness's representation inside every migrated program this
      *      platform ships -- ADR-0043's "fixing the measurement into
      *      the instrument", in mirror image.
      *
      * So GnuCOBOL's representation exists only inside this container.
      *
      * ONLY the negative half is rewritten: "p"-"y" -> "}JKLMNOPQR".
      * Positives are plain digits in both conventions, which is what
      * the previous oracle already carried and what this repo's own
      * CobolRecord.zoned writes. Every other byte passes through
      * untouched, including a "{" on a row the run never rewrote. That
      * makes this idempotent, and makes the fixture's format identical
      * in kind to the one it replaces -- the only new thing in it is
      * that negative amounts now exist at all.
      *
      * Three record shapes, selected by which env pair the caller sets:
      *   ACC  300  offsets  24 36 48 90 102  (CVACT01Y, five S9(10)V99)
      *   TCB   50  offset   28               (CVTRA01Y TRAN-CAT-BAL)
      *   TRN  350  offset  143               (CVTRA05Y TRAN-AMT)
      *
      * An unconvertible byte is not possible here by construction, so
      * there is nothing to refuse: anything outside "p"-"y" is already
      * in the target representation and is left alone. The assertion
      * that matters is the COUNT, which the caller checks.
      *****************************************************************
       IDENTIFICATION DIVISION.
       PROGRAM-ID. SIGNBACK.
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT ACC-IN  ASSIGN TO SBACCIN
                  ORGANIZATION IS SEQUENTIAL
                  FILE STATUS IS ST-IN.
           SELECT ACC-OUT ASSIGN TO SBACCOUT
                  ORGANIZATION IS SEQUENTIAL
                  FILE STATUS IS ST-OUT.
           SELECT TCB-IN  ASSIGN TO SBTCBIN
                  ORGANIZATION IS SEQUENTIAL
                  FILE STATUS IS ST-IN.
           SELECT TCB-OUT ASSIGN TO SBTCBOUT
                  ORGANIZATION IS SEQUENTIAL
                  FILE STATUS IS ST-OUT.
           SELECT TRN-IN  ASSIGN TO SBTRNIN
                  ORGANIZATION IS SEQUENTIAL
                  FILE STATUS IS ST-IN.
           SELECT TRN-OUT ASSIGN TO SBTRNOUT
                  ORGANIZATION IS SEQUENTIAL
                  FILE STATUS IS ST-OUT.

       DATA DIVISION.
       FILE SECTION.
       FD  ACC-IN.
       01  ACC-I-REC              PIC X(300).
       FD  ACC-OUT.
       01  ACC-O-REC              PIC X(300).
       FD  TCB-IN.
       01  TCB-I-REC              PIC X(50).
       FD  TCB-OUT.
       01  TCB-O-REC              PIC X(50).
       FD  TRN-IN.
       01  TRN-I-REC              PIC X(350).
       FD  TRN-OUT.
       01  TRN-O-REC              PIC X(350).

       WORKING-STORAGE SECTION.
       01  ST-IN                  PIC XX.
       01  ST-OUT                 PIC XX.
       01  WS-EOF                 PIC X VALUE "N".
       01  WS-SHAPE               PIC X(03).
       01  WS-COUNT               PIC 9(06) VALUE 0.
       01  WS-FIXED               PIC 9(06) VALUE 0.
       01  WS-CDISP               PIC Z(05)9.
       01  WS-FDISP               PIC Z(05)9.
       01  WS-REC                 PIC X(350).
       01  WS-K                   PIC 9(02).
       01  WS-J                   PIC 9(02).
       01  WS-HIT                 PIC 9(02).
       01  WS-BYTE                PIC X.
       01  WS-NOFF                PIC 9(01) VALUE 0.
       01  WS-OFFS.
           05  WS-OFF             OCCURS 5 TIMES PIC 9(03).
       01  WS-GNU                 PIC X(10) VALUE "pqrstuvwxy".
       01  WS-IBM                 PIC X(10) VALUE "}JKLMNOPQR".

       PROCEDURE DIVISION.
       MAIN-PARA.
           ACCEPT WS-SHAPE FROM ENVIRONMENT "SB_SHAPE"
           EVALUATE WS-SHAPE
              WHEN "ACC"
                 MOVE 5 TO WS-NOFF
                 MOVE 24  TO WS-OFF(1)
                 MOVE 36  TO WS-OFF(2)
                 MOVE 48  TO WS-OFF(3)
                 MOVE 90  TO WS-OFF(4)
                 MOVE 102 TO WS-OFF(5)
                 PERFORM RUN-ACC
              WHEN "TCB"
                 MOVE 1 TO WS-NOFF
                 MOVE 28 TO WS-OFF(1)
                 PERFORM RUN-TCB
              WHEN "TRN"
                 MOVE 1 TO WS-NOFF
                 MOVE 143 TO WS-OFF(1)
                 PERFORM RUN-TRN
              WHEN OTHER
                 DISPLAY "ABORT: SB_SHAPE is [" WS-SHAPE
                         "], expected ACC, TCB or TRN"
                 MOVE 16 TO RETURN-CODE
                 STOP RUN
           END-EVALUATE
           MOVE WS-COUNT TO WS-CDISP
           MOVE WS-FIXED TO WS-FDISP
           DISPLAY "SIGNBACK " WS-SHAPE " records: " WS-CDISP
           DISPLAY "SIGNBACK " WS-SHAPE " negatives: " WS-FDISP
           GOBACK.

       RUN-ACC.
           OPEN INPUT ACC-IN
           PERFORM CHECK-IN
           OPEN OUTPUT ACC-OUT
           PERFORM CHECK-OUT
           PERFORM UNTIL WS-EOF = "Y"
              READ ACC-IN
                 AT END MOVE "Y" TO WS-EOF
                 NOT AT END
                    ADD 1 TO WS-COUNT
                    MOVE ACC-I-REC TO WS-REC
                    PERFORM CONVERT-ALL
                    MOVE WS-REC(1:300) TO ACC-O-REC
                    WRITE ACC-O-REC
              END-READ
           END-PERFORM
           CLOSE ACC-IN ACC-OUT.

       RUN-TCB.
           OPEN INPUT TCB-IN
           PERFORM CHECK-IN
           OPEN OUTPUT TCB-OUT
           PERFORM CHECK-OUT
           PERFORM UNTIL WS-EOF = "Y"
              READ TCB-IN
                 AT END MOVE "Y" TO WS-EOF
                 NOT AT END
                    ADD 1 TO WS-COUNT
                    MOVE TCB-I-REC TO WS-REC
                    PERFORM CONVERT-ALL
                    MOVE WS-REC(1:50) TO TCB-O-REC
                    WRITE TCB-O-REC
              END-READ
           END-PERFORM
           CLOSE TCB-IN TCB-OUT.

       RUN-TRN.
           OPEN INPUT TRN-IN
           PERFORM CHECK-IN
           OPEN OUTPUT TRN-OUT
           PERFORM CHECK-OUT
           PERFORM UNTIL WS-EOF = "Y"
              READ TRN-IN
                 AT END MOVE "Y" TO WS-EOF
                 NOT AT END
                    ADD 1 TO WS-COUNT
                    MOVE TRN-I-REC TO WS-REC
                    PERFORM CONVERT-ALL
                    MOVE WS-REC(1:350) TO TRN-O-REC
                    WRITE TRN-O-REC
              END-READ
           END-PERFORM
           CLOSE TRN-IN TRN-OUT.

       CONVERT-ALL.
           PERFORM VARYING WS-J FROM 1 BY 1 UNTIL WS-J > WS-NOFF
              MOVE WS-REC(WS-OFF(WS-J):1) TO WS-BYTE
              MOVE 0 TO WS-HIT
              PERFORM VARYING WS-K FROM 1 BY 1 UNTIL WS-K > 10
                 IF WS-GNU(WS-K:1) = WS-BYTE
                    MOVE WS-K TO WS-HIT
                    MOVE 10 TO WS-K
                 END-IF
              END-PERFORM
              IF WS-HIT > 0
                 MOVE WS-IBM(WS-HIT:1) TO WS-REC(WS-OFF(WS-J):1)
                 ADD 1 TO WS-FIXED
              END-IF
           END-PERFORM.

       CHECK-IN.
           IF ST-IN NOT = "00"
              DISPLAY "SIGNBACK INPUT STATUS " ST-IN
              MOVE 16 TO RETURN-CODE
              STOP RUN
           END-IF.

       CHECK-OUT.
           IF ST-OUT NOT = "00"
              DISPLAY "SIGNBACK OUTPUT STATUS " ST-OUT
              MOVE 16 TO RETURN-CODE
              STOP RUN
           END-IF.
