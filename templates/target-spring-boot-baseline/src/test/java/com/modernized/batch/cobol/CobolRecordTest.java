package com.modernized.batch.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.io.IOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * The two rules that produce plausible, wrong output when a translation takes them literally:
 * records are bytes rather than lines, and a signed zoned field carries its sign in its last digit.
 */
class CobolRecordTest {

    @TempDir Path directory;

    @Test
    void splitsAFileIntoFixedLengthRecordsWithoutLookingForNewlines() throws IOException {
        Path file = directory.resolve("records.dat");
        Files.write(file, "AAAAABBBBBCCCCC".getBytes(StandardCharsets.ISO_8859_1));

        assertEquals(List.of("AAAAA", "BBBBB", "CCCCC"), CobolRecord.fixedRecords(file, 5));
    }

    @Test
    void aByteThatLooksLikeANewlineIsJustData() throws IOException {
        // The defect this exists to prevent: a 0x0A inside a record is data, and splitting on it
        // would misalign every record after it while still producing readable-looking output.
        Path file = directory.resolve("embedded.dat");
        Files.write(file, "AA\nAABB\nBB".getBytes(StandardCharsets.ISO_8859_1));

        assertEquals(List.of("AA\nAA", "BB\nBB"), CobolRecord.fixedRecords(file, 5));
    }

    @Test
    void aFileThatIsNotAWholeNumberOfRecordsThrows() throws IOException {
        Path file = directory.resolve("ragged.dat");
        Files.write(file, "AAAAABBB".getBytes(StandardCharsets.ISO_8859_1));

        IllegalStateException thrown =
                assertThrows(IllegalStateException.class, () -> CobolRecord.fixedRecords(file, 5));
        assertEquals(true, thrown.getMessage().contains("whole number"));
    }

    @Test
    void shortLinesArePaddedToTheRecordLength() throws IOException {
        // cardxref.txt is exactly this: 36-character lines for a copybook that declares 50.
        Path file = directory.resolve("lines.txt");
        Files.write(file, "ABC\r\nDEF\n".getBytes(StandardCharsets.ISO_8859_1));

        assertEquals(List.of("ABC  ", "DEF  "), CobolRecord.lineRecords(file, 5));
    }

    @Test
    void aLineLongerThanTheRecordThrowsRatherThanTruncating() throws IOException {
        Path file = directory.resolve("long.txt");
        Files.write(file, "ABCDEFGH\n".getBytes(StandardCharsets.ISO_8859_1));

        assertThrows(IllegalStateException.class, () -> CobolRecord.lineRecords(file, 5));
    }

    @Test
    void readsAPositiveOverpunchAsItsSignAndFinalDigit() {
        // 00000001940{ is +194.00. Read as digits it is 19400 -- ten times too large.
        assertEquals(new BigDecimal("194.00"), CobolRecord.number("00000001940{", 0, 12, 2));
    }

    @Test
    void readsANegativeOverpunch() {
        assertEquals(new BigDecimal("-19.41"), CobolRecord.number("0000000194J", 0, 11, 2));
    }

    @Test
    void readsPlainDigitsWhenTheFieldIsUnsigned() {
        assertEquals(new BigDecimal("00000000001"), CobolRecord.number("00000000001", 0, 11, 0));
    }

    @Test
    void readsAFieldFromTheMiddleOfARecord() {
        String record = "00000000001010001000001164700000000000000000000000";
        assertEquals(new BigDecimal("1164.70"), CobolRecord.number(record, 17, 11, 2));
        assertEquals("01", CobolRecord.text(record, 11, 2));
    }

    @Test
    void keepsPaddingOnAlphanumericFields() {
        // A PIC X(10) field holding "System" is ten characters, and the comparison against COBOL's
        // own output depends on that staying true.
        assertEquals("System    ", CobolRecord.text("xxSystem    yy", 2, 10));
    }
}
