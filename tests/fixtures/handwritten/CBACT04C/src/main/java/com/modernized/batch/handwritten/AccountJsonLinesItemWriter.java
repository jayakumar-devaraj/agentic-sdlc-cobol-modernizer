package com.modernized.batch.handwritten;

import com.modernized.batch.domain.Account;
import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import org.springframework.batch.infrastructure.item.Chunk;
import org.springframework.batch.infrastructure.item.ItemWriter;

/**
 * The candidate for `acctdata-posted.dat`: every posted {@code Account}, one JSON object per line.
 *
 * <p>`CBACT04C` writes two files — the interest transactions and the rewritten account master — and
 * comparing only the first would leave half the program's observable output unmeasured, which is
 * exactly what the transaction-only round trip did.
 */
class AccountJsonLinesItemWriter implements ItemWriter<Account> {

    private final Path output;

    AccountJsonLinesItemWriter(Path output) throws IOException {
        this.output = output;
        JsonLines.prepare(output);
    }

    @Override
    public void write(Chunk<? extends Account> chunk) throws IOException {
        List<String> objects = new ArrayList<>(chunk.size());
        for (Account account : chunk.getItems()) {
            objects.add(toJson(account));
        }
        JsonLines.append(output, objects);
    }

    /** `CVACT01Y`'s fields, in the copybook's own order. */
    private static String toJson(Account account) {
        StringBuilder json = new StringBuilder("{");
        JsonLines.number(json, "acctId", account.acctId());
        JsonLines.text(json, "acctActiveStatus", account.acctActiveStatus());
        JsonLines.number(json, "acctCurrBal", account.acctCurrBal());
        JsonLines.number(json, "acctCreditLimit", account.acctCreditLimit());
        JsonLines.number(json, "acctCashCreditLimit", account.acctCashCreditLimit());
        JsonLines.text(json, "acctOpenDate", account.acctOpenDate());
        JsonLines.text(json, "acctExpiraionDate", account.acctExpiraionDate());
        JsonLines.text(json, "acctReissueDate", account.acctReissueDate());
        JsonLines.number(json, "acctCurrCycCredit", account.acctCurrCycCredit());
        JsonLines.number(json, "acctCurrCycDebit", account.acctCurrCycDebit());
        JsonLines.text(json, "acctAddrZip", account.acctAddrZip());
        JsonLines.text(json, "acctGroupId", account.acctGroupId());
        return json.append('}').toString();
    }
}
