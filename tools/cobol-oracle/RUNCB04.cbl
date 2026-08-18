      *****************************************************************
      * Driver: supplies the JCL PARM CBACT04C expects, and nothing else.
      *
      * CBACT04C is `PROCEDURE DIVISION USING EXTERNAL-PARMS`, so it is a
      * called program, not a command. On the mainframe JCL supplies the
      * parameter; here this driver does, and it is the ONLY thing standing
      * between the shell and the unmodified program.
      *
      * PARM-DATE feeds TRAN-ID via `STRING PARM-DATE, WS-TRANID-SUFFIX`.
      * ADR-0029 excludes TRAN-ID from the comparison, so the value below
      * does not affect any asserted field -- but it is fixed rather than
      * derived from a clock, because an oracle regenerated tomorrow must
      * be byte-identical to this one or it is not a fixture.
      *****************************************************************
       IDENTIFICATION DIVISION.
       PROGRAM-ID. RUNCB04.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  EXTERNAL-PARMS.
           05  PARM-LENGTH         PIC S9(04) COMP VALUE 10.
           05  PARM-DATE           PIC X(10)  VALUE "2026-08-12".

       PROCEDURE DIVISION.
       MAIN-PARA.
           DISPLAY "RUNCB04: calling CBACT04C with PARM-DATE "
                   PARM-DATE
           CALL "CBACT04C" USING EXTERNAL-PARMS
           DISPLAY "RUNCB04: CBACT04C returned " RETURN-CODE
           GOBACK.
