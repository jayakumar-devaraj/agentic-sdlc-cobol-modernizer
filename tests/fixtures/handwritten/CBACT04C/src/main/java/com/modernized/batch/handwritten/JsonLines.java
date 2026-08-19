package com.modernized.batch.handwritten;

import java.io.BufferedWriter;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.List;

/**
 * Emits generated records as one JSON object per line, for the Python differential to read.
 *
 * <p><b>Deliberately not a fixed-width COBOL serialiser.</b> ADR-0029 compares fields rather than
 * bytes precisely because building a writer whose only consumer is the assertion about it would be
 * a check written to match whatever it needed to match. These helpers emit each record's own
 * accessor values unchanged — so a short {@code TRAN-SOURCE} stays short and fails the comparison,
 * which is the property that makes the differential worth running.
 *
 * <p>Nulls are written as JSON null. Whatever a generated body leaves unset arrives at the
 * comparison as unset rather than as a plausible substitute.
 */
final class JsonLines {

    private JsonLines() {}

    static void prepare(Path output) throws IOException {
        Files.createDirectories(output.getParent());
        Files.deleteIfExists(output);
    }

    static void append(Path output, List<String> objects) throws IOException {
        try (BufferedWriter writer =
                Files.newBufferedWriter(
                        output,
                        StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE,
                        StandardOpenOption.APPEND)) {
            for (String object : objects) {
                writer.write(object);
                writer.newLine();
            }
        }
    }

    static StringBuilder text(StringBuilder json, String name, String value) {
        separate(json).append('"').append(name).append("\":");
        if (value == null) {
            return json.append("null");
        }
        json.append('"');
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (c == '"' || c == '\\') {
                json.append('\\').append(c);
            } else if (c < 0x20) {
                json.append(String.format("\\u%04x", (int) c));
            } else {
                json.append(c);
            }
        }
        return json.append('"');
    }

    static StringBuilder number(StringBuilder json, String name, BigDecimal value) {
        separate(json).append('"').append(name).append("\":");
        return json.append(value == null ? "null" : value.toPlainString());
    }

    /** Commas by position rather than by hand: a trailing or missing one is invalid JSON. */
    private static StringBuilder separate(StringBuilder json) {
        if (json.length() > 1) {
            json.append(',');
        }
        return json;
    }
}
