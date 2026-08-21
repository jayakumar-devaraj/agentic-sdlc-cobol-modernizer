      *****************************************************************
      * dailytran.txt (LF-terminated text) -> fixed 350-byte SEQUENTIAL.
      *
      * CBTRN02C declares DALYTRAN-FILE as ORGANIZATION IS SEQUENTIAL,
      * which in GnuCOBOL means fixed-length records with NO line
      * terminator. The shipped file is 105,300 bytes: 300 records of 350
      * plus 300 single-byte terminators. Pointed at it directly, every
      * record after the first is shifted by one byte and every field in
      * the program reads the wrong columns -- silently, because a
      * shifted record is still a readable record.
      *
      * So this strips the terminators, and does not touch anything else.
      * It is the same class of step as LOADIDX: the corpus ships a text
      * *representation*, and the program expects the record format the
      * mainframe gave it.
      *
      * "Does not touch anything else" is kept literally true: the sign
      * conversion ADR-0047 adds is a separate program, SIGNCONV, run
      * after this one. Framing and content are different claims and a
      * pipeline listing should show both. This writes DALYRAW; SIGNCONV
      * reads it and writes DALYTRAN.
      *****************************************************************
       IDENTIFICATION DIVISION.
       PROGRAM-ID. DALYCONV.
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT DLY-IN  ASSIGN TO DLYIN
                  ORGANIZATION IS LINE SEQUENTIAL
                  FILE STATUS IS ST-IN.
           SELECT DLY-OUT ASSIGN TO DALYRAW
                  ORGANIZATION IS SEQUENTIAL
                  FILE STATUS IS ST-OUT.

       DATA DIVISION.
       FILE SECTION.
       FD  DLY-IN.
       01  DLY-I-REC              PIC X(350).
       FD  DLY-OUT.
       01  DLY-O-REC              PIC X(350).

       WORKING-STORAGE SECTION.
       01  ST-IN                  PIC XX.
       01  ST-OUT                 PIC XX.
       01  WS-EOF                 PIC X VALUE "N".
       01  WS-COUNT               PIC 9(06) VALUE 0.
       01  WS-DISP                PIC Z(05)9.

       PROCEDURE DIVISION.
       MAIN-PARA.
           OPEN INPUT DLY-IN
           IF ST-IN NOT = "00"
              DISPLAY "DALYTRAN INPUT STATUS " ST-IN
              MOVE 16 TO RETURN-CODE
              STOP RUN
           END-IF
           OPEN OUTPUT DLY-OUT
           PERFORM UNTIL WS-EOF = "Y"
              READ DLY-IN
                 AT END MOVE "Y" TO WS-EOF
                 NOT AT END
                    MOVE DLY-I-REC TO DLY-O-REC
                    WRITE DLY-O-REC
                    ADD 1 TO WS-COUNT
              END-READ
           END-PERFORM
           CLOSE DLY-IN DLY-OUT
           MOVE WS-COUNT TO WS-DISP
           DISPLAY "DALYTRAN converted: " WS-DISP
           GOBACK.
