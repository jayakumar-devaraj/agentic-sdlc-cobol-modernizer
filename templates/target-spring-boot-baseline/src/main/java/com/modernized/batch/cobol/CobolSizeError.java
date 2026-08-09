package com.modernized.batch.cobol;

/**
 * Raised when a value does not fit the {@code PIC} clause it is being stored into.
 *
 * <p>Named after COBOL's {@code ON SIZE ERROR} condition, but it is not a translation of it: COBOL
 * without that clause discards high-order digits and carries on. See
 * {@link CobolArithmetic#requireFits} for why this repo fails loudly instead.
 *
 * <p>Extends {@link ArithmeticException} so that a generated {@code ItemProcessor} which already
 * handles arithmetic failure does not have to learn a new type to catch — and so an unhandled one
 * still terminates the step rather than being swallowed by a broad {@code catch (Exception)}.
 */
public class CobolSizeError extends ArithmeticException {

    public CobolSizeError(String message) {
        super(message);
    }
}
