      *****************************************************************
      * What does this runtime make of an IBM trailing sign overpunch?
      * Reads the same eleven bytes as S9(09)V99 DISPLAY and shows the
      * value it computes. Nothing here is part of the repo; it exists
      * to settle one question about the compiler.
      *****************************************************************
       IDENTIFICATION DIVISION.
       PROGRAM-ID. OPTEST.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-RAW           PIC X(11).
       01  WS-NUM REDEFINES WS-RAW  PIC S9(09)V99.
       01  WS-SHOW          PIC -(9)9.99.
       PROCEDURE DIVISION.
       MAIN-PARA.
           MOVE "0000005047G" TO WS-RAW
           MOVE WS-NUM TO WS-SHOW
           DISPLAY "0000005047G (G = +7) -> " WS-SHOW

           MOVE "0000000567P" TO WS-RAW
           MOVE WS-NUM TO WS-SHOW
           DISPLAY "0000000567P (P = -7) -> " WS-SHOW

           MOVE "0000000294D" TO WS-RAW
           MOVE WS-NUM TO WS-SHOW
           DISPLAY "0000000294D (D = +4) -> " WS-SHOW

           MOVE "0000009190}" TO WS-RAW
           MOVE WS-NUM TO WS-SHOW
           DISPLAY "0000009190} (} = -0) -> " WS-SHOW

           MOVE "0000003250{" TO WS-RAW
           MOVE WS-NUM TO WS-SHOW
           DISPLAY "0000003250{ ({ = +0) -> " WS-SHOW
           GOBACK.
