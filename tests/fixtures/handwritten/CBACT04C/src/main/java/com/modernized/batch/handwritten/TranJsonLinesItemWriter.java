package com.modernized.batch.handwritten;

import com.modernized.batch.domain.Tran;
import java.io.BufferedWriter;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import org.springframework.batch.infrastructure.item.Chunk;
import org.springframework.batch.infrastructure.item.ItemWriter;

/**
 * Writes each generated {@code Tran} as one JSON object per line.
 *
 * <p><b>Deliberately not a fixed-width COBOL serialiser.</b> ADR-0029 compares fields rather than
 * bytes precisely because building a writer whose only consumer is the assertion about it would be
 * a check written to match whatever it needed to match. This emits the record's own accessor values
 * unchanged -- so a short {@code TRAN-SOURCE} stays short and fails the comparison, which is the
 * property that makes the differential worth running.
 *
 * <p>Nulls are written as JSON null. {@code TRAN-ID} and both timestamps are null by ADR-0026, and
 * the comparison excludes exactly those three.
 */
class TranJsonLinesItemWriter implements ItemWriter<Tran> {

    private final Path output;

    TranJsonLinesItemWriter(Path output) throws IOException {
        this.output = output;
        Files.createDirectories(output.getParent());
        Files.deleteIfExists(output);
    }

    @Override
    public void write(Chunk<? extends Tran> chunk) throws IOException {
        try (BufferedWriter writer =
                Files.newBufferedWriter(
                        output,
                        StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE,
                        StandardOpenOption.APPEND)) {
            for (Tran tran : chunk.getItems()) {
                writer.write(toJson(tran));
                writer.newLine();
            }
        }
    }

    private static String toJson(Tran tran) {
        StringBuilder json = new StringBuilder("{");
        text(json, "tranId", tran.tranId()).append(',');
        text(json, "tranTypeCd", tran.tranTypeCd()).append(',');
        number(json, "tranCatCd", tran.tranCatCd()).append(',');
        text(json, "tranSource", tran.tranSource()).append(',');
        text(json, "tranDesc", tran.tranDesc()).append(',');
        number(json, "tranAmt", tran.tranAmt()).append(',');
        number(json, "tranMerchantId", tran.tranMerchantId()).append(',');
        text(json, "tranMerchantName", tran.tranMerchantName()).append(',');
        text(json, "tranMerchantCity", tran.tranMerchantCity()).append(',');
        text(json, "tranMerchantZip", tran.tranMerchantZip()).append(',');
        text(json, "tranCardNum", tran.tranCardNum()).append(',');
        text(json, "tranOrigTs", tran.tranOrigTs()).append(',');
        text(json, "tranProcTs", tran.tranProcTs());
        return json.append('}').toString();
    }

    private static StringBuilder text(StringBuilder json, String name, String value) {
        json.append('"').append(name).append("\":");
        if (value == null) {
            return json.append("null");
        }
        json.append('"');
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"' -> json.append("\\\"");
                case '\\' -> json.append("\\\\");
                default -> {
                    if (c < 0x20) {
                        json.append(String.format("\\u%04x", (int) c));
                    } else {
                        json.append(c);
                    }
                }
            }
        }
        return json.append('"');
    }

    private static StringBuilder number(StringBuilder json, String name, BigDecimal value) {
        json.append('"').append(name).append("\":");
        return json.append(value == null ? "null" : value.toPlainString());
    }
}
