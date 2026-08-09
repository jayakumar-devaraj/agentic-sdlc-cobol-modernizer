package com.modernized.batch.cobol;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatExceptionOfType;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.math.BigDecimal;
import java.math.RoundingMode;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * Every test here pins a rule where the obvious Java translation of COBOL is wrong. None of them
 * is testing {@code BigDecimal}; they are testing that this helper chose the right {@code BigDecimal}
 * behaviour, which is the part a code generator gets wrong.
 */
class CobolArithmeticTest {

    @Test
    @DisplayName("COMPUTE without ROUNDED truncates, it does not round")
    void truncate_discards_rather_than_rounds() {
        assertThat(CobolArithmetic.truncate(new BigDecimal("194.999"), 2))
                .isEqualByComparingTo("194.99");
        assertThat(CobolArithmetic.truncate(new BigDecimal("194.995"), 2))
                .isEqualByComparingTo("194.99");

        // The same input under the rounding a generator reaches for by default, so the two are
        // visibly different rather than merely asserted to be.
        assertThat(new BigDecimal("194.995").setScale(2, RoundingMode.HALF_UP))
                .isEqualByComparingTo("195.00");
    }

    @Test
    @DisplayName("truncation is toward zero, not toward negative infinity")
    void truncate_of_a_negative_goes_toward_zero() {
        // DOWN and FLOOR agree on every positive number, so a suite that only ever tests positive
        // amounts cannot tell a correct implementation from a wrong one. This is that test.
        assertThat(CobolArithmetic.truncate(new BigDecimal("-194.999"), 2))
                .isEqualByComparingTo("-194.99");
        assertThat(new BigDecimal("-194.999").setScale(2, RoundingMode.FLOOR))
                .isEqualByComparingTo("-195.00");
    }

    @Test
    @DisplayName("ROUNDED is nearest-away-from-zero, on both signs")
    void rounded_breaks_ties_away_from_zero() {
        assertThat(CobolArithmetic.rounded(new BigDecimal("194.995"), 2))
                .isEqualByComparingTo("195.00");
        assertThat(CobolArithmetic.rounded(new BigDecimal("-194.995"), 2))
                .isEqualByComparingTo("-195.00");
    }

    @Test
    @DisplayName("division never throws on a non-terminating quotient, the way COBOL never does")
    void divide_handles_a_repeating_decimal() {
        // Plain BigDecimal division on the same numbers fails outright. A generated batch job with
        // this defect compiles, passes a smoke test on well-behaved data, and dies in production on
        // the first rate that does not divide evenly.
        assertThatThrownBy(() -> new BigDecimal("100").divide(new BigDecimal("3")))
                .isInstanceOf(ArithmeticException.class);

        assertThat(CobolArithmetic.divide(new BigDecimal("100"), new BigDecimal("3"), 2))
                .isEqualByComparingTo("33.33");
        assertThat(CobolArithmetic.divide(new BigDecimal("-100"), new BigDecimal("3"), 2))
                .isEqualByComparingTo("-33.33");
    }

    @Test
    @DisplayName("a monthly-interest COMPUTE truncates its result")
    void monthly_interest_shape_truncates() {
        // The shape a COBOL monthly interest calculation takes: an annual percentage rate applied
        // to a balance and divided by 1200, stored without ROUNDED. Chosen because it is the exact
        // expression ADR-0015's benchmark used to catch a model omitting the truncation.
        BigDecimal balance = new BigDecimal("1234.56");
        BigDecimal annualRatePercent = new BigDecimal("13.75");

        BigDecimal monthly = CobolArithmetic.divide(
                balance.multiply(annualRatePercent), new BigDecimal("1200"), 2);

        // 1234.56 * 13.75 = 16975.2000; / 1200 = 14.1460, truncated to 14.14 - not 14.15.
        assertThat(monthly).isEqualByComparingTo("14.14");
    }

    @Test
    @DisplayName("a rounded division rounds exactly once")
    void divide_rounded_does_not_double_round() {
        // 20099 / 20000 is exactly 1.00495.
        assertThat(CobolArithmetic.divideRounded(new BigDecimal("20099"), new BigDecimal("20000"), 2))
                .isEqualByComparingTo("1.00");

        // What an "intermediate precision then narrow" implementation would produce instead.
        BigDecimal doubleRounded = new BigDecimal("20099")
                .divide(new BigDecimal("20000"), 4, RoundingMode.HALF_UP)
                .setScale(2, RoundingMode.HALF_UP);
        assertThat(doubleRounded).isEqualByComparingTo("1.01");
    }

    @Test
    @DisplayName("a value that fits its PIC clause passes through, scaled")
    void require_fits_accepts_the_largest_representable_value() {
        // PIC S9(10)V99 - the shape of every money field in scope.
        assertThat(CobolArithmetic.requireFits(new BigDecimal("9999999999.99"), 12, 2))
                .isEqualByComparingTo("9999999999.99");
        assertThat(CobolArithmetic.requireFits(new BigDecimal("-9999999999.99"), 12, 2))
                .isEqualByComparingTo("-9999999999.99");
        assertThat(CobolArithmetic.requireFits(new BigDecimal("1.005"), 12, 2))
                .isEqualByComparingTo("1.00");
    }

    @Test
    @DisplayName("overflow raises instead of silently losing high-order digits, as COBOL would")
    void require_fits_rejects_a_value_one_digit_too_large() {
        assertThatExceptionOfType(CobolSizeError.class)
                .isThrownBy(() -> CobolArithmetic.requireFits(new BigDecimal("10000000000.00"), 12, 2))
                .withMessageContaining("10000000000.00")
                .withMessageContaining("9999999999.99");

        assertThatExceptionOfType(CobolSizeError.class)
                .isThrownBy(() -> CobolArithmetic.requireFits(new BigDecimal("-10000000000.00"), 12, 2));
    }
}
