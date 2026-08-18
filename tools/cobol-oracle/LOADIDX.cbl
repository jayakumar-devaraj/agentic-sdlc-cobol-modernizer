      *****************************************************************
      * Flat text -> VSAM-style INDEXED, so CBACT04C runs UNMODIFIED.
      *
      * Four of CBACT04C's five files are ORGANIZATION IS INDEXED and the
      * shipped corpus is flat text, so the program cannot be pointed at
      * the data as it ships. Editing its SELECT clauses to SEQUENTIAL was
      * the cheap alternative and was rejected: the oracle's whole value
      * is that it is COBOL's own answer, and an answer from a modified
      * program is a weaker claim that would be carried forever.
      *
      * LINE SEQUENTIAL on input is deliberate. tcatbal.txt carries 49 CR
      * against 50 LF (audit G16) -- mixed terminators inside one file --
      * and LINE SEQUENTIAL strips both, where a fixed-length RECORD would
      * drag the terminator into the record and shift every field.
      *
      * Every ASSIGN is an environment name, never a path: CLAUDE.md
      * forbids machine paths in committed files.
      *****************************************************************
       IDENTIFICATION DIVISION.
       PROGRAM-ID. LOADIDX.
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT TCB-IN  ASSIGN TO TCBIN
                  ORGANIZATION IS LINE SEQUENTIAL
                  FILE STATUS IS ST-IN.
           SELECT TCB-OUT ASSIGN TO TCATBALF
                  ORGANIZATION IS INDEXED
                  ACCESS MODE  IS SEQUENTIAL
                  RECORD KEY   IS TCB-O-KEY
                  FILE STATUS  IS ST-OUT.

           SELECT XRF-IN  ASSIGN TO XRFIN
                  ORGANIZATION IS LINE SEQUENTIAL
                  FILE STATUS IS ST-IN.
           SELECT XRF-OUT ASSIGN TO XREFFILE
                  ORGANIZATION IS INDEXED
                  ACCESS MODE  IS SEQUENTIAL
                  RECORD KEY   IS XRF-O-CARD
                  ALTERNATE RECORD KEY IS XRF-O-ACCT
                  FILE STATUS  IS ST-OUT.

           SELECT ACC-IN  ASSIGN TO ACCIN
                  ORGANIZATION IS LINE SEQUENTIAL
                  FILE STATUS IS ST-IN.
           SELECT ACC-OUT ASSIGN TO ACCTFILE
                  ORGANIZATION IS INDEXED
                  ACCESS MODE  IS SEQUENTIAL
                  RECORD KEY   IS ACC-O-KEY
                  FILE STATUS  IS ST-OUT.

           SELECT DIS-IN  ASSIGN TO DISIN
                  ORGANIZATION IS LINE SEQUENTIAL
                  FILE STATUS IS ST-IN.
           SELECT DIS-OUT ASSIGN TO DISCGRP
                  ORGANIZATION IS INDEXED
                  ACCESS MODE  IS SEQUENTIAL
                  RECORD KEY   IS DIS-O-KEY
                  FILE STATUS  IS ST-OUT.

       DATA DIVISION.
       FILE SECTION.
       FD  TCB-IN.
       01  TCB-I-REC              PIC X(50).
       FD  TCB-OUT.
       01  TCB-O-REC.
           05 TCB-O-KEY           PIC X(17).
           05 TCB-O-DATA          PIC X(33).

      * Input is 36 bytes against CVACT03Y's declared RECLN 50 -- the
      * trailing FILLER X(14) is simply absent from the file (audit G16).
      * The indexed record is the copybook's 50, so the load pads. Reading
      * 50 from a 36-byte file misaligns every record after the first.
       FD  XRF-IN.
       01  XRF-I-REC              PIC X(36).
       FD  XRF-OUT.
       01  XRF-O-REC.
           05 XRF-O-CARD          PIC X(16).
           05 XRF-O-CUST          PIC X(09).
           05 XRF-O-ACCT          PIC X(11).
           05 XRF-O-FILLER        PIC X(14).

       FD  ACC-IN.
       01  ACC-I-REC              PIC X(300).
       FD  ACC-OUT.
       01  ACC-O-REC.
           05 ACC-O-KEY           PIC X(11).
           05 ACC-O-DATA          PIC X(289).

       FD  DIS-IN.
       01  DIS-I-REC              PIC X(50).
       FD  DIS-OUT.
       01  DIS-O-REC.
           05 DIS-O-KEY           PIC X(16).
           05 DIS-O-DATA          PIC X(34).

       WORKING-STORAGE SECTION.
       01  ST-IN                  PIC XX.
       01  ST-OUT                 PIC XX.
       01  WS-EOF                 PIC X VALUE "N".
       01  WS-COUNT               PIC 9(06).
       01  WS-DISP                PIC Z(05)9.

       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM LOAD-TCB
           PERFORM LOAD-XRF
           PERFORM LOAD-ACC
           PERFORM LOAD-DIS
           GOBACK.

       LOAD-TCB.
           MOVE 0 TO WS-COUNT
           MOVE "N" TO WS-EOF
           OPEN INPUT TCB-IN
           PERFORM CHECK-IN
           OPEN OUTPUT TCB-OUT
           PERFORM CHECK-OUT
           PERFORM UNTIL WS-EOF = "Y"
              READ TCB-IN INTO TCB-O-REC
                 AT END MOVE "Y" TO WS-EOF
                 NOT AT END
                    WRITE TCB-O-REC
                    PERFORM CHECK-OUT
                    ADD 1 TO WS-COUNT
              END-READ
              PERFORM CHECK-IN
           END-PERFORM
           CLOSE TCB-IN TCB-OUT
           MOVE WS-COUNT TO WS-DISP
           DISPLAY "TCATBALF loaded: " WS-DISP.

       LOAD-XRF.
           MOVE 0 TO WS-COUNT
           MOVE "N" TO WS-EOF
           OPEN INPUT XRF-IN
           PERFORM CHECK-IN
           OPEN OUTPUT XRF-OUT
           PERFORM CHECK-OUT
           PERFORM UNTIL WS-EOF = "Y"
              READ XRF-IN
                 AT END MOVE "Y" TO WS-EOF
                 NOT AT END
                    MOVE SPACES TO XRF-O-REC
                    MOVE XRF-I-REC(1:16)  TO XRF-O-CARD
                    MOVE XRF-I-REC(17:9)  TO XRF-O-CUST
                    MOVE XRF-I-REC(26:11) TO XRF-O-ACCT
                    WRITE XRF-O-REC
                    PERFORM CHECK-OUT
                    ADD 1 TO WS-COUNT
              END-READ
              PERFORM CHECK-IN
           END-PERFORM
           CLOSE XRF-IN XRF-OUT
           MOVE WS-COUNT TO WS-DISP
           DISPLAY "XREFFILE loaded: " WS-DISP.

       LOAD-ACC.
           MOVE 0 TO WS-COUNT
           MOVE "N" TO WS-EOF
           OPEN INPUT ACC-IN
           PERFORM CHECK-IN
           OPEN OUTPUT ACC-OUT
           PERFORM CHECK-OUT
           PERFORM UNTIL WS-EOF = "Y"
              READ ACC-IN INTO ACC-O-REC
                 AT END MOVE "Y" TO WS-EOF
                 NOT AT END
                    WRITE ACC-O-REC
                    PERFORM CHECK-OUT
                    ADD 1 TO WS-COUNT
              END-READ
              PERFORM CHECK-IN
           END-PERFORM
           CLOSE ACC-IN ACC-OUT
           MOVE WS-COUNT TO WS-DISP
           DISPLAY "ACCTFILE loaded: " WS-DISP.

       LOAD-DIS.
           MOVE 0 TO WS-COUNT
           MOVE "N" TO WS-EOF
           OPEN INPUT DIS-IN
           PERFORM CHECK-IN
           OPEN OUTPUT DIS-OUT
           PERFORM CHECK-OUT
           PERFORM UNTIL WS-EOF = "Y"
              READ DIS-IN INTO DIS-O-REC
                 AT END MOVE "Y" TO WS-EOF
                 NOT AT END
                    WRITE DIS-O-REC
                    PERFORM CHECK-OUT
                    ADD 1 TO WS-COUNT
              END-READ
              PERFORM CHECK-IN
           END-PERFORM
           CLOSE DIS-IN DIS-OUT
           MOVE WS-COUNT TO WS-DISP
           DISPLAY "DISCGRP  loaded: " WS-DISP.

      * A load that half-worked would produce an oracle nobody could
      * trust, so every status is checked and any failure stops the run --
      * including inside the READ loops. Without that, a status that is
      * neither 00 nor 10 runs neither the AT END nor the NOT AT END
      * branch, WS-EOF is never set, and the PERFORM UNTIL spins forever:
      * the container hangs where it should have failed loudly.
       CHECK-IN.
           IF ST-IN NOT = "00" AND ST-IN NOT = "10"
              DISPLAY "INPUT FILE STATUS " ST-IN
              MOVE 16 TO RETURN-CODE
              STOP RUN
           END-IF.

       CHECK-OUT.
           IF ST-OUT NOT = "00"
              DISPLAY "OUTPUT FILE STATUS " ST-OUT
              MOVE 16 TO RETURN-CODE
              STOP RUN
           END-IF.
