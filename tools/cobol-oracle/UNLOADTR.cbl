      *****************************************************************
      * CBTRN02C's transaction master -> flat fixed-length, as the
      * oracle for a CBTRN02C round trip.
      *
      * **Why this exists.** Stage 1 posts 300 daily transactions and
      * writes the ones that pass validation to TRANFILE, an INDEXED
      * file in the work directory. Every other output of that stage is
      * already captured -- the posted TCATBAL by UNLOADTC, the account
      * file by UNLOADAC -- and this one was not, so CBTRN02C's own
      * primary output existed only inside the container and vanished
      * with it. A comparison for that program had two of its three
      * in-scope targets and no way to check the third.
      *
      * ADR-0038 puts DALYREJS out of scope for generation, so the three
      * comparable outputs are the balance file, the account file, and
      * this one.
      *
      * ORGANIZATION IS SEQUENTIAL on output, like UNLOADAC and UNLOADTC
      * and for the same reason: LINE SEQUENTIAL trims trailing spaces,
      * so a 350-byte record would come out short and every field past
      * the last non-blank would be lost while still looking like
      * readable text.
      *
      * **Read ACCESS MODE IS SEQUENTIAL, so the unload is in key
      * order.** That is a property of the fixture a comparison has to
      * account for: a candidate that appends records as it writes them
      * produces the same records in a different order (ADR-0037's open
      * question).
      *****************************************************************
       IDENTIFICATION DIVISION.
       PROGRAM-ID. UNLOADTR.
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT TR-IN ASSIGN TO TRANFILE
                  ORGANIZATION IS INDEXED
                  ACCESS MODE IS SEQUENTIAL
                  RECORD KEY IS TR-KEY
                  FILE STATUS IS ST-IN.
           SELECT TR-OUT ASSIGN TO TRNOUT
                  ORGANIZATION IS SEQUENTIAL
                  FILE STATUS IS ST-OUT.
       DATA DIVISION.
       FILE SECTION.
       FD  TR-IN.
       01  TR-REC.
           05 TR-KEY    PIC X(16).
           05 TR-DATA   PIC X(334).
       FD  TR-OUT.
       01  TR-O         PIC X(350).
       WORKING-STORAGE SECTION.
       01  ST-IN        PIC XX.
       01  ST-OUT       PIC XX.
       01  WS-EOF       PIC X VALUE "N".
       01  WS-COUNT     PIC 9(06) VALUE 0.
       01  WS-DISP      PIC Z(05)9.
       PROCEDURE DIVISION.
       MAIN-PARA.
           OPEN INPUT TR-IN
           IF ST-IN NOT = "00"
              DISPLAY "TRANFILE UNLOAD STATUS " ST-IN
              MOVE 16 TO RETURN-CODE
              STOP RUN
           END-IF
           OPEN OUTPUT TR-OUT
           IF ST-OUT NOT = "00"
              DISPLAY "TRANFILE OUT STATUS " ST-OUT
              MOVE 16 TO RETURN-CODE
              STOP RUN
           END-IF
           PERFORM UNTIL WS-EOF = "Y"
              READ TR-IN
                 AT END MOVE "Y" TO WS-EOF
                 NOT AT END
                    MOVE TR-REC TO TR-O
                    WRITE TR-O
                    ADD 1 TO WS-COUNT
              END-READ
              IF ST-IN NOT = "00" AND ST-IN NOT = "10"
                 DISPLAY "TRANFILE READ STATUS " ST-IN
                 MOVE 16 TO RETURN-CODE
                 STOP RUN
              END-IF
           END-PERFORM
           CLOSE TR-IN TR-OUT
           MOVE WS-COUNT TO WS-DISP
           DISPLAY "TRANFILE unloaded: " WS-DISP
           GOBACK.
