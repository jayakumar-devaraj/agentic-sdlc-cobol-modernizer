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

    @Test
    void writesAPositiveValueAsPlainZeroPaddedDigits() {
        // What the reference run's COBOL actually wrote: no overpunch on positives.
        assertEquals("00000019400", CobolRecord.zoned(new BigDecimal("194.00"), 11, 2));
    }

    @Test
    void writesANegativeValueWithAnOverpunchOnItsLastDigit() {
        // A '-' has nowhere to live in a field whose width is its digit count.
        assertEquals("0000001941J", CobolRecord.zoned(new BigDecimal("-194.11"), 11, 2));
    }

    @Test
    void roundTripsThroughItsOwnReader() {
        String stored = CobolRecord.zoned(new BigDecimal("-19.41"), 11, 2);
        assertEquals(new BigDecimal("-19.41"), CobolRecord.number(stored, 0, 11, 2));
    }

    @Test
    void truncatesExtraScaleTowardZeroRatherThanRounding() {
        // COBOL stores into a fixed scale by truncation; rounding here would invent a cent.
        assertEquals("00000000194", CobolRecord.zoned(new BigDecimal("1.9499"), 11, 2));
    }

    @Test
    void aValueTooLargeForTheFieldThrows() {
        // Writing it truncated would produce a smaller number that looks entirely valid.
        assertThrows(
                IllegalArgumentException.class,
                () -> CobolRecord.zoned(new BigDecimal("12345.67"), 4, 2));
    }

    /**
     * The twenty overpunch characters, against values derived by hand from the standard.
     *
     * <p>ADR-0043. Every one of these is a real {@code DALYTRAN-AMT} from the CardDemo corpus, and
     * the expected values are written out rather than computed -- deriving them with this decoder
     * and then asserting this decoder would compare two renderings of one interpretation.
     *
     * <p>It matters here rather than only in the Python suite because this class ships inside every
     * generated project: it is what a migrated program reads its own input with. GnuCOBOL, given
     * the same bytes, returns 504.70 for the first of these -- the digit the overpunch carries is
     * dropped -- which is why the oracle and a generated run disagree about which transactions
     * clear a credit limit.
     */
    @Test
    void readsEveryTrailingSignOverpunchTheStandardDefines() {
        assertEquals(new BigDecimal("325.00"), CobolRecord.number("0000003250{", 0, 11, 2));
        assertEquals(new BigDecimal("416.11"), CobolRecord.number("0000004161A", 0, 11, 2));
        assertEquals(new BigDecimal("250.22"), CobolRecord.number("0000002502B", 0, 11, 2));
        assertEquals(new BigDecimal("94.33"), CobolRecord.number("0000000943C", 0, 11, 2));
        assertEquals(new BigDecimal("29.44"), CobolRecord.number("0000000294D", 0, 11, 2));
        assertEquals(new BigDecimal("829.55"), CobolRecord.number("0000008295E", 0, 11, 2));
        assertEquals(new BigDecimal("454.66"), CobolRecord.number("0000004546F", 0, 11, 2));
        assertEquals(new BigDecimal("504.77"), CobolRecord.number("0000005047G", 0, 11, 2));
        assertEquals(new BigDecimal("67.88"), CobolRecord.number("0000000678H", 0, 11, 2));
        assertEquals(new BigDecimal("849.99"), CobolRecord.number("0000008499I", 0, 11, 2));
        assertEquals(new BigDecimal("-919.00"), CobolRecord.number("0000009190}", 0, 11, 2));
        assertEquals(new BigDecimal("-835.11"), CobolRecord.number("0000008351J", 0, 11, 2));
        assertEquals(new BigDecimal("-75.22"), CobolRecord.number("0000000752K", 0, 11, 2));
        assertEquals(new BigDecimal("-215.33"), CobolRecord.number("0000002153L", 0, 11, 2));
        assertEquals(new BigDecimal("-358.44"), CobolRecord.number("0000003584M", 0, 11, 2));
        assertEquals(new BigDecimal("-445.55"), CobolRecord.number("0000004455N", 0, 11, 2));
        assertEquals(new BigDecimal("-945.66"), CobolRecord.number("0000009456O", 0, 11, 2));
        assertEquals(new BigDecimal("-56.77"), CobolRecord.number("0000000567P", 0, 11, 2));
        assertEquals(new BigDecimal("-535.88"), CobolRecord.number("0000005358Q", 0, 11, 2));
        assertEquals(new BigDecimal("-70.99"), CobolRecord.number("0000000709R", 0, 11, 2));
    }
}
