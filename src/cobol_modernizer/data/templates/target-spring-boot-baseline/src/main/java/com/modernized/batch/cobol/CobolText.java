package com.modernized.batch.cobol;

/**
 * COBOL alphanumeric semantics, in one place — the sibling of {@link CobolArithmetic}.
 *
 * <p>This class exists for one rule that does not survive a literal translation into Java: a
 * {@code PIC X(n)} field is <b>always exactly n characters</b>. There is no such thing as a short
 * value in one. {@code MOVE SPACES} into a {@code PIC X(50)} field writes fifty spaces, and a
 * generated {@code ""} writes none — the same value, a different record, and no compiler or unit
 * test that compares values will ever notice.
 *
 * <p>The failure mode is correct-looking output that differs from the original only on disk. A
 * real model generating a batch processor wrote {@code ""} for three such fields and flagged it in
 * its own notes, saying an empty string and fifty spaces are not the same record. It was right,
 * and it had not been told the widths.
 *
 * <p>Encoded here rather than left as {@code " ".repeat(n)} scattered through generated bodies for
 * the reason {@link CobolArithmetic} gives: a defect in the semantics should be one fix in one
 * file, not a sweep through everything ever generated.
 */
public final class CobolText {

    private CobolText() {
    }

    /**
     * Stores a value into a {@code PIC X(width)} field: space-padded on the right, or truncated if
     * it is too long.
     *
     * <p>Truncation is on the <b>right</b>, which is what a COBOL {@code MOVE} to a shorter
     * alphanumeric field does — the opposite end from a numeric {@code MOVE}, which discards
     * high-order digits on the left. Getting that backwards silently keeps the wrong half of a
     * card number.
     *
     * <p>A {@code null} value is treated as spaces, because a COBOL field has no null state: an
     * uninitialised {@code PIC X} field contains spaces, and mapping null to an empty string would
     * reintroduce exactly the defect this class exists for.
     */
    public static String pad(String value, int width) {
        if (width < 0) {
            throw new IllegalArgumentException("PIC X width cannot be negative: " + width);
        }
        if (value == null) {
            return " ".repeat(width);
        }
        if (value.length() >= width) {
            return value.substring(0, width);
        }
        return value + " ".repeat(width - value.length());
    }

    /**
     * A {@code PIC X(width)} field holding {@code SPACES}. Equivalent to {@code pad(null, width)},
     * and named so that a generated body translating {@code MOVE SPACES} reads like the COBOL it
     * came from.
     */
    public static String spaces(int width) {
        return pad(null, width);
    }
}
