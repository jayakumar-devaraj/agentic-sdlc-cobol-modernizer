      *****************************************************************
      * OPTEST asked what this runtime READS an IBM overpunch as.
      * This asks the complementary question SIGNCONV has to target:
      * what representation does this runtime use for a sign of its own?
      *
      * Two halves, because they are not symmetric:
      *
      *  (a) what it WRITES, for +0..+9 and -0..-9. Answer: plain digits
      *      for positives, `q`-`y` for -1..-9, and plain `0` for -0 --
      *      COMPUTE collapses negative zero to positive zero before the
      *      store, so `p` is never produced.
      *
      *  (b) what it READS, for `p`-`y` and `0`-`9` in the sign position.
      *      Answer: `p` IS accepted as -0. So the corpus's `}` (also -0)
      *      maps to `p`, which round-trips in value even though nothing
      *      in half (a) would ever emit it.
      *
      * Half (b) exists because half (a) alone would have argued for
      * mapping `}` to `0` -- which reads back as +919.00 where the
      * corpus means -919.00.
      *
      * Nothing in the pipeline depends on this program. It is committed
      * so the table in SIGNCONV is reproducible rather than asserted,
      * the same way OPTEST.cbl is (ADR-0043, ADR-0047).
      *****************************************************************
       IDENTIFICATION DIVISION.
       PROGRAM-ID. SIGNTEST.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-NUM           PIC S9(09)V99.
       01  WS-RAW REDEFINES WS-NUM  PIC X(11).
       01  WS-IN            PIC X(11).
       01  WS-INNUM REDEFINES WS-IN PIC S9(09)V99.
       01  WS-LAST          PIC X.
       01  WS-ORD           PIC 9(03).
       01  WS-I             PIC 9(02).
       01  WS-SHOW          PIC -(9)9.99.
       01  WS-CAND          PIC X(20) VALUE "pqrstuvwxy0123456789".

       PROCEDURE DIVISION.
       MAIN-PARA.
           DISPLAY "(a) what this runtime WRITES -- positive"
           PERFORM VARYING WS-I FROM 0 BY 1 UNTIL WS-I > 9
              COMPUTE WS-NUM = WS-I / 100
              PERFORM SHOW-WRITTEN
           END-PERFORM
           DISPLAY "(a) what this runtime WRITES -- negative"
           PERFORM VARYING WS-I FROM 0 BY 1 UNTIL WS-I > 9
              COMPUTE WS-NUM = 0 - (WS-I / 100)
              PERFORM SHOW-WRITTEN
           END-PERFORM

           DISPLAY "(b) what this runtime READS, on 0000009190 + byte"
           PERFORM VARYING WS-I FROM 1 BY 1 UNTIL WS-I > 20
              MOVE "0000009190" TO WS-IN(1:10)
              MOVE WS-CAND(WS-I:1) TO WS-IN(11:1)
              MOVE WS-INNUM TO WS-SHOW
              DISPLAY "    last=[" WS-CAND(WS-I:1) "] -> " WS-SHOW
           END-PERFORM
           GOBACK.

       SHOW-WRITTEN.
           MOVE WS-RAW(11:1) TO WS-LAST
           COMPUTE WS-ORD = FUNCTION ORD(WS-LAST) - 1
           MOVE WS-NUM TO WS-SHOW
           DISPLAY "    " WS-I " raw=[" WS-RAW "] last=[" WS-LAST
                   "] ord=" WS-ORD " val=" WS-SHOW.
