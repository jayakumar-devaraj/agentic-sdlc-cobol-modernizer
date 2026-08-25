package com.modernized.batch.cobol;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * Reading COBOL's own record format — the sibling of {@link CobolArithmetic} and {@link CobolText}.
 *
 * <p>Two rules live here that do not survive a literal translation, and both are the kind that
 * produce output which looks right:
 *
 * <ol>
 *   <li><b>A record is a fixed number of bytes, not a line.</b> A file written by a COBOL
 *       {@code WRITE} has no terminators, so splitting it on newlines yields records that are
 *       plausible and wrong — and wrong differently for every record after the first byte that
 *       happens to be {@code 0x0A}.
 *   <li><b>A signed zoned-decimal field carries its sign in its last digit.</b> {@code 00000001940{}
 *       is {@code +194.00}; read as plain digits it is {@code 19400} — out by a factor of ten, with
 *       the sign lost. Real corpus data uses all twenty overpunch forms.
 * </ol>
 *
 * <p>Encoded here rather than emitted into each generated reader, for the reason
 * {@link CobolArithmetic} gives: a defect in the semantics should be one fix in one file rather
 * than a sweep through everything ever generated.
 */
public final class CobolRecord {

    /** {@code {} = +0, A-I = +1..9, } = -0, J-R = -1..9}. */
    private static final String POSITIVE_OVERPUNCH = "{ABCDEFGHI";

    private static final String NEGATIVE_OVERPUNCH = "}JKLMNOPQR";

    private CobolRecord() {
    }

    /**
     * Splits a file into fixed-length records.
     *
     * <p>ISO-8859-1 rather than UTF-8, deliberately: the corpus is bytes, and a multi-byte decoding
     * would make character offsets stop matching byte offsets — which is the same class of silent
     * misalignment as splitting on newlines.
     *
     * @throws IllegalStateException if the file is not a whole number of records, which means the
     *     length is wrong and every record after the first is misaligned.
     */
    public static List<String> fixedRecords(Path path, int recordLength) throws java.io.IOException {
        if (recordLength <= 0) {
            throw new IllegalArgumentException("record length must be positive, got " + recordLength);
        }
        byte[] bytes = Files.readAllBytes(path);
        if (bytes.length % recordLength != 0) {
            throw new IllegalStateException(
                    path
                            + " is "
                            + bytes.length
                            + " bytes, which is not a whole number of "
                            + recordLength
                            + "-byte records");
        }
        String all = new String(bytes, StandardCharsets.ISO_8859_1);
        List<String> records = new ArrayList<>(all.length() / recordLength);
        for (int i = 0; i < all.length(); i += recordLength) {
            records.add(all.substring(i, i + recordLength));
        }
        return records;
    }

    /**
     * Splits a line-terminated file into records, padding short lines to {@code recordLength}.
     *
     * <p>The shipped corpus is not uniform: {@code cardxref.txt} lines are 36 characters where its
     * copybook declares 50, and line endings differ between files. Padding rather than failing is
     * correct here — the trailing {@code FILLER} of a record simply was not written out — while a
     * line <em>longer</em> than the record is a different file than the one expected and throws.
     */
    public static List<String> lineRecords(Path path, int recordLength) throws java.io.IOException {
        List<String> records = new ArrayList<>();
        for (String line : Files.readAllLines(path, StandardCharsets.ISO_8859_1)) {
            if (line.isEmpty()) {
                continue;
            }
            String trimmed = line.endsWith("\r") ? line.substring(0, line.length() - 1) : line;
            if (trimmed.length() > recordLength) {
                throw new IllegalStateException(
                        path + " has a " + trimmed.length() + "-character line for a "
                                + recordLength + "-byte record");
            }
            records.add(CobolText.pad(trimmed, recordLength));
        }
        return records;
    }

    /**
     * Stores a number into a zoned-decimal {@code DISPLAY} field: {@code width} characters, the
     * decimal point implied at {@code scale}.
     *
     * <p><b>Positive values are written as plain digits</b>, which is what the reference run's
     * COBOL produced — its own output files carry no overpunch on positive amounts. Negative values
     * take the standard overpunch on the final digit, because a {@code -} sign has nowhere to live
     * in a field whose width is its digit count.
     *
     * <p>The positive representation is compiler-dependent and is on the oracle's own
     * known-unverified list, which is exactly why the differential compares field <em>values</em>
     * rather than bytes: a run whose COBOL writes {@code 194.00} as {@code 0000001940{} and one
     * that writes {@code 00000019400} agree on the number and differ on disk.
     *
     * @throws IllegalArgumentException if the value does not fit, rather than writing a truncated
     *     number that looks like a smaller one.
     */
    public static String zoned(BigDecimal value, int width, int scale) {
        BigDecimal scaled = value.setScale(scale, java.math.RoundingMode.DOWN);
        String digits = scaled.abs().unscaledValue().toString();
        if (digits.length() > width) {
            throw new IllegalArgumentException(
                    "value " + value + " needs " + digits.length() + " digits for a " + width
                            + "-character field");
        }
        StringBuilder padded = new StringBuilder();
        for (int i = digits.length(); i < width; i++) {
            padded.append('0');
        }
        padded.append(digits);

        if (scaled.signum() < 0) {
            int last = padded.length() - 1;
            int finalDigit = padded.charAt(last) - '0';
            padded.setCharAt(last, NEGATIVE_OVERPUNCH.charAt(finalDigit));
        }
        return padded.toString();
    }

    /** One {@code PIC X(width)} field, exactly as stored — padding included. */
    public static String text(String record, int offset, int width) {
        return record.substring(offset, offset + width);
    }

    /**
     * One zoned-decimal {@code DISPLAY} field.
     *
     * <p>{@code scale} is the number of digits after the implied decimal point: {@code V} occupies
     * no byte, so a {@code PIC S9(9)V99} field is eleven characters and the point is positional.
     */
    public static BigDecimal number(String record, int offset, int width, int scale) {
        String raw = record.substring(offset, offset + width);
        char last = raw.charAt(raw.length() - 1);
        String digits = raw.substring(0, raw.length() - 1);

        int positive = POSITIVE_OVERPUNCH.indexOf(last);
        int negative = NEGATIVE_OVERPUNCH.indexOf(last);
        if (positive >= 0) {
            digits += positive;
        } else if (negative >= 0) {
            digits += negative;
        } else {
            digits += last;
        }

        BigDecimal value = new BigDecimal(digits.trim().isEmpty() ? "0" : digits).movePointLeft(scale);
        return negative >= 0 ? value.negate() : value;
    }
}
