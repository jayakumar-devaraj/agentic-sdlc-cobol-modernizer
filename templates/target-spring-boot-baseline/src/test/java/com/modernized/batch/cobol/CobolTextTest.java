package com.modernized.batch.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;

/** {@link CobolText}, against the cases where a short string and a padded one differ on disk. */
class CobolTextTest {

    @Test
    void movingSpacesFillsTheDeclaredWidth() {
        // The defect this class exists for: a real model wrote "" for PIC X(50) fields.
        assertEquals(" ".repeat(50), CobolText.spaces(50));
        assertEquals(50, CobolText.spaces(50).length());
    }

    @Test
    void aShortValueIsPaddedOnTheRight() {
        assertEquals("System    ", CobolText.pad("System", 10));
    }

    @Test
    void aValueOfExactlyTheWidthIsUnchanged() {
        assertEquals("0123456789", CobolText.pad("0123456789", 10));
    }

    @Test
    void anOverlongValueIsTruncatedOnTheRightNotTheLeft() {
        // A numeric MOVE discards high-order digits on the left; an alphanumeric one keeps the
        // leading characters. Getting this backwards silently keeps the wrong half of a card number.
        assertEquals("1234", CobolText.pad("123456", 4));
    }

    @Test
    void nullIsSpacesRatherThanAnEmptyString() {
        // A COBOL PIC X field has no null state -- an uninitialised one contains spaces. Mapping
        // null to "" would reintroduce the exact defect this class exists to prevent.
        assertEquals("     ", CobolText.pad(null, 5));
    }

    @Test
    void aZeroWidthFieldIsEmptyRatherThanAnError() {
        assertEquals("", CobolText.pad("anything", 0));
    }

    @Test
    void aNegativeWidthIsRejected() {
        assertThrows(IllegalArgumentException.class, () -> CobolText.pad("x", -1));
    }
}
