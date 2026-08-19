package com.modernized.batch.handwritten;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * Reads CardDemo's fixed-width records. Hand-written wiring (ADR-0030) -- not generated, not part
 * of the baseline template.
 *
 * <p><b>Every offset here is a fact design.json does not carry.</b> The design gives each field a
 * width (a {@code PIC X(n)} length, or a numeric precision) and an order, but no offsets, no record
 * length, and no FILLER -- so this layout is derivable from it only under the assumption that
 * fields are contiguous from byte zero, which happens to hold for these four copybooks because
 * every FILLER in them is trailing. See README.md; this is finding F1.
 *
 * <p>The zoned-decimal decoder is not a design fact either. It is the same overpunch convention the
 * data loader already implements, restated here because nothing renders it into a target project.
 */
final class CobolFixedWidth {

    private CobolFixedWidth() {}

    /** {@code {} = +0, A-I = +1..9, } = -0, J-R = -1..9} -- the sign rides the final digit. */
    private static final String POSITIVE = "{ABCDEFGHI";
    private static final String NEGATIVE = "}JKLMNOPQR";

    static String text(String record, int offset, int width) {
        return record.substring(offset, offset + width);
    }

    /**
     * A zoned-decimal DISPLAY field. {@code 00000001940{} is +194.00, not 19400 -- reading it as
     * plain digits is wrong by a factor of ten and loses the sign.
     */
    static BigDecimal number(String record, int offset, int width, int scale) {
        String raw = record.substring(offset, offset + width);
        char last = raw.charAt(raw.length() - 1);
        String digits = raw.substring(0, raw.length() - 1);
        int positive = POSITIVE.indexOf(last);
        int negative = NEGATIVE.indexOf(last);
        boolean signed = negative >= 0;
        if (positive >= 0) {
            digits += positive;
        } else if (negative >= 0) {
            digits += negative;
        } else {
            digits += last;
        }
        BigDecimal value = new BigDecimal(digits).movePointLeft(scale);
        return signed ? value.negate() : value;
    }

    /** Fixed-length records with no terminator, as the oracle's unloaded files are written. */
    static List<String> fixedRecords(Path path, int length) throws Exception {
        byte[] bytes = Files.readAllBytes(path);
        if (bytes.length % length != 0) {
            throw new IllegalStateException(
                    path + " is " + bytes.length + " bytes, not a whole number of " + length);
        }
        String all = new String(bytes, StandardCharsets.ISO_8859_1);
        List<String> records = new ArrayList<>(all.length() / length);
        for (int i = 0; i < all.length(); i += length) {
            records.add(all.substring(i, i + length));
        }
        return records;
    }

    /**
     * Line-terminated records, as the shipped corpus files are. Padded to {@code width} because
     * the corpus is not uniform: {@code cardxref.txt} lines are 36 characters where its copybook
     * declares 50, and line endings differ between files (audit G16).
     */
    static List<String> lineRecords(Path path, int width) throws Exception {
        List<String> records = new ArrayList<>();
        for (String line : Files.readAllLines(path, StandardCharsets.ISO_8859_1)) {
            if (line.isEmpty()) {
                continue;
            }
            String trimmed = line.endsWith("\r") ? line.substring(0, line.length() - 1) : line;
            records.add(trimmed.length() >= width ? trimmed : padRight(trimmed, width));
        }
        return records;
    }

    private static String padRight(String value, int width) {
        StringBuilder builder = new StringBuilder(value);
        while (builder.length() < width) {
            builder.append(' ');
        }
        return builder.toString();
    }
}
