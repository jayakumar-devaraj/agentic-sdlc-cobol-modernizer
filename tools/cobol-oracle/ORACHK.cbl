       IDENTIFICATION DIVISION.
       PROGRAM-ID. ORACHK.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  W-BAL   PIC S9(09)V99.
       01  W-RATE  PIC S9(04)V99.
       01  W-INT   PIC S9(09)V99.
       01  W-OUT   PIC -(9)9.99.
       PROCEDURE DIVISION.
           MOVE 194.00 TO W-BAL
           MOVE 15.00 TO W-RATE
           COMPUTE W-INT = ( W-BAL * W-RATE) / 1200
           MOVE W-INT TO W-OUT
           DISPLAY "R1 expected=2.42 got=" W-OUT
           MOVE -194.00 TO W-BAL
           MOVE 15.00 TO W-RATE
           COMPUTE W-INT = ( W-BAL * W-RATE) / 1200
           MOVE W-INT TO W-OUT
           DISPLAY "R2 expected=-2.42 got=" W-OUT
           MOVE 100.00 TO W-BAL
           MOVE 25.00 TO W-RATE
           COMPUTE W-INT = ( W-BAL * W-RATE) / 1200
           MOVE W-INT TO W-OUT
           DISPLAY "R3 expected=2.08 got=" W-OUT
           MOVE -100.00 TO W-BAL
           MOVE 25.00 TO W-RATE
           COMPUTE W-INT = ( W-BAL * W-RATE) / 1200
           MOVE W-INT TO W-OUT
           DISPLAY "R4 expected=-2.08 got=" W-OUT
           MOVE 0.50 TO W-BAL
           MOVE 15.00 TO W-RATE
           COMPUTE W-INT = ( W-BAL * W-RATE) / 1200
           MOVE W-INT TO W-OUT
           DISPLAY "R5 expected=0.00 got=" W-OUT
           MOVE -0.50 TO W-BAL
           MOVE 15.00 TO W-RATE
           COMPUTE W-INT = ( W-BAL * W-RATE) / 1200
           MOVE W-INT TO W-OUT
           DISPLAY "R6 expected=0.00 got=" W-OUT
           MOVE 999.77 TO W-BAL
           MOVE 25.00 TO W-RATE
           COMPUTE W-INT = ( W-BAL * W-RATE) / 1200
           MOVE W-INT TO W-OUT
           DISPLAY "R7 expected=20.82 got=" W-OUT
           MOVE -998.33 TO W-BAL
           MOVE 15.00 TO W-RATE
           COMPUTE W-INT = ( W-BAL * W-RATE) / 1200
           MOVE W-INT TO W-OUT
           DISPLAY "R8 expected=-12.47 got=" W-OUT
           MOVE 0.00 TO W-BAL
           MOVE 15.00 TO W-RATE
           COMPUTE W-INT = ( W-BAL * W-RATE) / 1200
           MOVE W-INT TO W-OUT
           DISPLAY "R9 expected=0.00 got=" W-OUT
           GOBACK.
