package com.modernized.batch.cobol;

import java.math.BigDecimal;
import java.math.RoundingMode;

/**
 * COBOL arithmetic semantics, in one place.
 *
 * <p>This class exists because three COBOL rules do not survive a literal translation into Java,
 * and each one produces a wrong number that looks right:
 *
 * <ol>
 *   <li><b>{@code COMPUTE} without {@code ROUNDED} truncates.</b> It does not round. A generated
 *       {@code setScale(2, RoundingMode.HALF_UP)} is a defect, not a style choice — and it is a
 *       defect no compiler and no test of the "does it compile" kind will ever catch. ADR-0015's
 *       four-model benchmark caught Haiku 4.5 missing exactly this semantic while narrating the
 *       interest calculation, so this is a demonstrated failure mode of the generator, not a
 *       hypothetical one.
 *   <li><b>COBOL truncates toward zero, not toward negative infinity.</b> {@code -194.999} stored
 *       into a {@code PIC S9(9)V99} field is {@code -194.99}, not {@code -195.00}. That is
 *       {@link RoundingMode#DOWN}, not {@link RoundingMode#FLOOR}, and the two agree on every
 *       positive number — so a test suite using only positive amounts cannot tell them apart.
 *   <li><b>{@code BigDecimal.divide} throws on a non-terminating quotient.</b> COBOL divides
 *       happily and truncates to the receiving field. Every generated division must therefore
 *       carry a scale and a rounding mode, or the batch job dies at runtime on the first awkward
 *       rate.
 * </ol>
 *
 * <p>Encoding these once here, rather than leaving {@code setScale} calls scattered through
 * generated code, means a defect in the semantics is one fix in one file rather than a sweep
 * through everything ever generated.
 *
 * <p>The class is deliberately free of any tenant vocabulary: it is COBOL's arithmetic, not any
 * particular program's.
 */
public final class CobolArithmetic {

    private CobolArithmetic() {
    }

    /**
     * Stores a value into a field of the given scale the way {@code COMPUTE} without
     * {@code ROUNDED} does: excess digits are discarded, toward zero.
     */
    public static BigDecimal truncate(BigDecimal value, int scale) {
        return value.setScale(scale, RoundingMode.DOWN);
    }

    /**
     * Stores a value the way {@code COMPUTE ... ROUNDED} does. COBOL's default rounding mode is
     * {@code NEAREST-AWAY-FROM-ZERO}, which is Java's {@link RoundingMode#HALF_UP} — {@code HALF_UP}
     * rounds away from zero on a tie, so {@code -0.005} becomes {@code -0.01}, matching COBOL.
     * (Java's {@code CEILING}/{@code FLOOR} and COBOL's {@code TOWARD-GREATER}/{@code TOWARD-LESSER}
     * are a different pair and are not what {@code ROUNDED} means on its own.)
     */
    public static BigDecimal rounded(BigDecimal value, int scale) {
        return value.setScale(scale, RoundingMode.HALF_UP);
    }

    /**
     * Divides and stores the result truncated, the {@code COMPUTE} default.
     *
     * <p>The quotient is produced directly at the target scale rather than at some wide
     * intermediate scale and then narrowed. For truncation the two are identical — truncating
     * toward zero twice equals truncating once — but see {@link #divideRounded} for why that does
     * not generalise.
     */
    public static BigDecimal divide(BigDecimal dividend, BigDecimal divisor, int scale) {
        return dividend.divide(divisor, scale, RoundingMode.DOWN);
    }

    /**
     * Divides and stores the result rounded, {@code COMPUTE ... ROUNDED}.
     *
     * <p><b>Rounds exactly once.</b> Computing a wide intermediate quotient and rounding it again
     * to the target scale is double rounding, and it gives a different answer: {@code 20099/20000}
     * is {@code 1.00495}, which rounds to {@code 1.00} at scale 2 but to {@code 1.0050} at scale 4
     * and thence to {@code 1.01}. A generator that reaches for an "intermediate precision" constant
     * will produce the second answer.
     */
    public static BigDecimal divideRounded(BigDecimal dividend, BigDecimal divisor, int scale) {
        return dividend.divide(divisor, scale, RoundingMode.HALF_UP);
    }

    /**
     * Checks that a value fits the {@code PIC} clause it is about to be stored into, and throws if
     * it does not.
     *
     * <p><b>This deliberately diverges from COBOL.</b> A COBOL {@code MOVE} or {@code COMPUTE}
     * without {@code ON SIZE ERROR} silently discards <em>high-order</em> digits, so a balance of
     * 12,345,678,901.00 stored into {@code PIC S9(10)V99} becomes 2,345,678,901.00 and the program
     * continues. Reproducing that faithfully would mean generating code whose defined behaviour is
     * to lose an order of magnitude of money in silence. This repo's standing rule is to fail
     * loudly on an unambiguous case rather than guess past it, so overflow raises here and reaches
     * an operator instead of a ledger.
     *
     * @param precision total digit positions, i.e. the {@code 9} count in the {@code PIC} clause
     * @param scale digit positions after the implied decimal point
     * @return the value, scaled to {@code scale}, so this can be used inline
     */
    public static BigDecimal requireFits(BigDecimal value, int precision, int scale) {
        if (scale < 0 || scale > precision) {
            throw new IllegalArgumentException(
                    "scale " + scale + " is not valid for precision " + precision);
        }
        BigDecimal stored = truncate(value, scale);
        BigDecimal exclusiveLimit = BigDecimal.TEN.pow(precision - scale);
        if (stored.abs().compareTo(exclusiveLimit) >= 0) {
            throw new CobolSizeError(
                    "value " + stored.toPlainString() + " does not fit PIC S9("
                            + (precision - scale) + ")V9(" + scale + "); the largest value that fits is "
                            + exclusiveLimit.subtract(BigDecimal.ONE.movePointLeft(scale)).toPlainString());
        }
        return stored;
    }
}
