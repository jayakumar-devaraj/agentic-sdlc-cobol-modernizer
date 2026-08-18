# CBACT04C oracle - provenance

Produced by tools/cobol-oracle/run-oracle.sh inside the pinned image.

- cobc: cobc (GnuCOBOL) 3.1.2.0
- indexed handler: indexed file handler : BDB
- dialect: -std=ibm
- PARM-DATE: 2026-08-12 (fixed; see RUNCB04.cbl)
- generated: 2026-08-18T17:47:15Z

## Input blob hashes (sha256)
- tcatbal.txt  a33eda6c526646e738164fd036ffdf525780466ade7141039bf72d4e25237afd
- cardxref.txt  efec3825ec0d5b791cf54f815bf688abfcc9db832c1600371ed2209df4e97764
- acctdata.txt  c2a97b6a32dc4a87a7aafdf7f72e6712e560412d30b00c5526cca80fc9dfd260
- discgrp.txt  dfdd3832805e3a4bf1d811ea2340ee8f6bf8e9fdf8d040fbd50e3c7b45b0ce2b

## Output
- transact.dat          17500 bytes
- acctdata-posted.txt   5650 bytes

## Known-unverified against IBM Enterprise COBOL
GnuCOBOL is not the tenant's compiler. Three behaviours are NOT corroborated by the
hand-derived oracle in ADR-0021 and must be treated as findings rather than failures
if they disagree: FUNCTION CURRENT-DATE formatting, STRING ... DELIMITED BY SIZE padding
at the edges, and the sign of zero. The interest arithmetic IS corroborated -- it matches
ADR-0021's hand-computed values independently.
