package com.modernized.batch.handwritten;

import com.modernized.batch.domain.Tran;
import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import org.springframework.batch.infrastructure.item.Chunk;
import org.springframework.batch.infrastructure.item.ItemWriter;

/** The candidate for `transact.dat`: every generated {@code Tran}, one JSON object per line. */
class TranJsonLinesItemWriter implements ItemWriter<Tran> {

    private final Path output;

    TranJsonLinesItemWriter(Path output) throws IOException {
        this.output = output;
        JsonLines.prepare(output);
    }

    @Override
    public void write(Chunk<? extends Tran> chunk) throws IOException {
        List<String> objects = new ArrayList<>(chunk.size());
        for (Tran tran : chunk.getItems()) {
            objects.add(toJson(tran));
        }
        JsonLines.append(output, objects);
    }

    /** `CVTRA05Y`'s fields, in the copybook's own order. */
    private static String toJson(Tran tran) {
        StringBuilder json = new StringBuilder("{");
        JsonLines.text(json, "tranId", tran.tranId());
        JsonLines.text(json, "tranTypeCd", tran.tranTypeCd());
        JsonLines.number(json, "tranCatCd", tran.tranCatCd());
        JsonLines.text(json, "tranSource", tran.tranSource());
        JsonLines.text(json, "tranDesc", tran.tranDesc());
        JsonLines.number(json, "tranAmt", tran.tranAmt());
        JsonLines.number(json, "tranMerchantId", tran.tranMerchantId());
        JsonLines.text(json, "tranMerchantName", tran.tranMerchantName());
        JsonLines.text(json, "tranMerchantCity", tran.tranMerchantCity());
        JsonLines.text(json, "tranMerchantZip", tran.tranMerchantZip());
        JsonLines.text(json, "tranCardNum", tran.tranCardNum());
        JsonLines.text(json, "tranOrigTs", tran.tranOrigTs());
        JsonLines.text(json, "tranProcTs", tran.tranProcTs());
        return json.append('}').toString();
    }
}
