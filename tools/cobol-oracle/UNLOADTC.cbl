      *****************************************************************
      * Posted TCATBALF -> flat fixed-length, as the generated job's INPUT.
      *
      * **Why this is an output of the oracle run and an input to the
      * comparison.** The shipped tcatbal.txt is the PRE-posting state
      * (audit R2.9): CBTRN02C writes it with ADD DALYTRAN-AMT TO
      * TRAN-CAT-BAL, and CBACT04C then computes interest on the result.
      * So the balances the oracle's amounts were derived from exist only
      * inside the run -- and without them the generated Java would start
      * from zeros, compute zero interest, and mismatch the oracle for a
      * reason that has nothing to do with the Java.
      *
      * That failure would be the worst kind available here: a red
      * comparison that looks like a translation defect and is actually a
      * fixture that was never captured.
      *
      * ORGANIZATION IS SEQUENTIAL on output, like UNLOADAC and for the
      * same reason: LINE SEQUENTIAL trims trailing spaces, so a 50-byte
      * record would come out short and every field past the last
      * non-blank would be lost while still looking like readable text.
      *****************************************************************
       IDENTIFICATION DIVISION.
       PROGRAM-ID. UNLOADTC.
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT TCB-IN ASSIGN TO TCATBALF
                  ORGANIZATION IS INDEXED
                  ACCESS MODE IS SEQUENTIAL
                  RECORD KEY IS TCB-KEY
                  FILE STATUS IS ST-IN.
           SELECT TCB-OUT ASSIGN TO TCBOUT
                  ORGANIZATION IS SEQUENTIAL
                  FILE STATUS IS ST-OUT.
       DATA DIVISION.
       FILE SECTION.
       FD  TCB-IN.
       01  TCB-REC.
           05 TCB-KEY   PIC X(17).
           05 TCB-DATA  PIC X(33).
       FD  TCB-OUT.
       01  TCB-O        PIC X(50).
       WORKING-STORAGE SECTION.
       01  ST-IN        PIC XX.
       01  ST-OUT       PIC XX.
       01  WS-EOF       PIC X VALUE "N".
       01  WS-COUNT     PIC 9(06) VALUE 0.
       01  WS-DISP      PIC Z(05)9.
       PROCEDURE DIVISION.
       MAIN-PARA.
           OPEN INPUT TCB-IN
           IF ST-IN NOT = "00"
              DISPLAY "TCATBALF UNLOAD STATUS " ST-IN
              MOVE 16 TO RETURN-CODE
              STOP RUN
           END-IF
           OPEN OUTPUT TCB-OUT
           IF ST-OUT NOT = "00"
              DISPLAY "TCATBAL OUT STATUS " ST-OUT
              MOVE 16 TO RETURN-CODE
              STOP RUN
           END-IF
           PERFORM UNTIL WS-EOF = "Y"
              READ TCB-IN
                 AT END MOVE "Y" TO WS-EOF
                 NOT AT END
                    MOVE TCB-REC TO TCB-O
                    WRITE TCB-O
                    ADD 1 TO WS-COUNT
              END-READ
              IF ST-IN NOT = "00" AND ST-IN NOT = "10"
                 DISPLAY "TCATBALF READ STATUS " ST-IN
                 MOVE 16 TO RETURN-CODE
                 STOP RUN
              END-IF
           END-PERFORM
           CLOSE TCB-IN TCB-OUT
           MOVE WS-COUNT TO WS-DISP
           DISPLAY "TCATBALF unloaded: " WS-DISP
           GOBACK.
