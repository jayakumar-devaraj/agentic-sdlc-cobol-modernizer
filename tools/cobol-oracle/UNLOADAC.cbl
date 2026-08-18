      *****************************************************************
      * Indexed ACCTFILE -> flat fixed-length, so the posted balances are
      * diffable and reviewable beside the transaction oracle.
      *
      * ORGANIZATION IS SEQUENTIAL on output, deliberately. The first
      * version of this used LINE SEQUENTIAL and GnuCOBOL trimmed trailing
      * spaces on write, so a 300-byte account record came out about 113
      * bytes and every field past the last non-blank was gone. The file
      * looked plausible -- 50 lines, readable text -- which is exactly
      * how that would have reached a comparison unnoticed.
      *****************************************************************
       IDENTIFICATION DIVISION.
       PROGRAM-ID. UNLOADAC.
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT ACC-IN ASSIGN TO ACCTFILE
                  ORGANIZATION IS INDEXED
                  ACCESS MODE IS SEQUENTIAL
                  RECORD KEY IS ACC-KEY
                  FILE STATUS IS ST-IN.
           SELECT ACC-OUT ASSIGN TO ACCOUT
                  ORGANIZATION IS SEQUENTIAL
                  FILE STATUS IS ST-OUT.
       DATA DIVISION.
       FILE SECTION.
       FD  ACC-IN.
       01  ACC-REC.
           05 ACC-KEY   PIC X(11).
           05 ACC-DATA  PIC X(289).
       FD  ACC-OUT.
       01  ACC-O        PIC X(300).
       WORKING-STORAGE SECTION.
       01  ST-IN        PIC XX.
       01  ST-OUT       PIC XX.
       01  WS-EOF       PIC X VALUE "N".
       01  WS-COUNT     PIC 9(06).
       01  WS-DISP      PIC Z(05)9.
       PROCEDURE DIVISION.
       MAIN-PARA.
           OPEN INPUT ACC-IN
           IF ST-IN NOT = "00"
              DISPLAY "ACCTFILE UNLOAD STATUS " ST-IN
              MOVE 16 TO RETURN-CODE
              STOP RUN
           END-IF
           OPEN OUTPUT ACC-OUT
           PERFORM UNTIL WS-EOF = "Y"
              READ ACC-IN
                 AT END MOVE "Y" TO WS-EOF
                 NOT AT END
                    MOVE ACC-REC TO ACC-O
                    WRITE ACC-O
                    ADD 1 TO WS-COUNT
              END-READ
           END-PERFORM
           CLOSE ACC-IN ACC-OUT
           MOVE WS-COUNT TO WS-DISP
           DISPLAY "ACCTFILE unloaded: " WS-DISP
           GOBACK.
