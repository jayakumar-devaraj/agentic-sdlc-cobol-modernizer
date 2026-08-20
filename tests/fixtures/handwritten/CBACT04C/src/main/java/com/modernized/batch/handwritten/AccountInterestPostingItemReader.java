package com.modernized.batch.handwritten;

import com.modernized.batch.cobol.CobolText;
import com.modernized.batch.domain.Account;
import com.modernized.batch.domain.AccountInterestPosting;
import com.modernized.batch.domain.Tran;
import com.modernized.batch.domain.TranWithContext;
import com.modernized.batch.job.TranWithContextStaging;
import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import org.springframework.batch.infrastructure.item.ItemReader;

/**
 * The aggregation ADR-0027 moved out of the processor and into the reader.
 *
 * <p>`WS-TOTAL-INT` accumulates `WS-MONTHLY-INT` across every `TRAN-CAT-BAL` row of one account and
 * `1050-UPDATE-ACCOUNT` posts it when the account key breaks. A stateless `ItemProcessor` cannot
 * hold that; ADR-0027's answer is to make the item an account whose interest is <em>already</em>
 * summed, so the generated body is COBOL's two statements and the summation is infrastructure. This
 * is that summation — and the live model run reached the same conclusion unprompted, saying the
 * total "has to be supplied by whatever step implements the account-break/update logic".
 *
 * <p><b>The equality is provable, not asserted.</b> Every `WS-MONTHLY-INT` is added to
 * `WS-TOTAL-INT` and written to `TRAN-AMT` inside `1300-COMPUTE-INTEREST` under the same guard, so
 * the sum of an account's interest transactions <em>is</em> its `WS-TOTAL-INT`.
 *
 * <p><b>It reads from the previous step's staged output, in account-key order.</b> That staging
 * bean is now rendered (ADR-0032); this reader is what remains hand-written, because the design
 * carries no grouping key, summed field or ordering for the aggregate it builds -- finding F6. That order is
 * what makes the emitted records line up with an account file the COBOL wrote sequentially by key.
 * Accounts with no interest row never reach this reader, exactly as they never reach
 * `1050-UPDATE-ACCOUNT`.
 *
 * <p><b>What it does not reproduce is a defect, and that is deliberate.</b> `CBACT04C`'s loop is
 * `PERFORM UNTIL END-OF-FILE = 'Y'` with the account-break post in the `ELSE` of
 * `IF END-OF-FILE = 'N'`, so the branch never runs and **COBOL never credits its last account**.
 * This reader emits a posting for every account, including the last. The differential therefore
 * disagrees on exactly one record, by exactly that account's interest, and
 * `test_the_account_half_differs_only_where_cobol_never_posts` pins that shape rather than hiding
 * it — reproducing the defect here would have been the cheaper option and would have made the
 * comparison green by encoding a bug.
 */
class AccountInterestPostingItemReader implements ItemReader<AccountInterestPosting> {

    private final TranWithContextStaging staging;
    private List<AccountInterestPosting> postings;
    private int next;

    AccountInterestPostingItemReader(TranWithContextStaging staging) {
        this.staging = staging;
    }

    @Override
    public AccountInterestPosting read() {
        if (postings == null) {
            postings = aggregate();
        }
        return next < postings.size() ? postings.get(next++) : null;
    }

    private List<AccountInterestPosting> aggregate() {
        // TreeMap keyed by the account id, so the output is in the same key order the COBOL read
        // the account file in -- a comparison against a sequentially-unloaded file is positional.
        Map<BigDecimal, BigDecimal> totals = new TreeMap<>();
        Map<BigDecimal, Account> accounts = new TreeMap<>();
        for (TranWithContext item : staging.items()) {
            BigDecimal id = item.account().acctId();
            totals.merge(id, item.tran().tranAmt(), BigDecimal::add);
            accounts.putIfAbsent(id, item.account());
        }

        List<AccountInterestPosting> aggregated = new ArrayList<>(totals.size());
        for (Map.Entry<BigDecimal, BigDecimal> entry : totals.entrySet()) {
            aggregated.add(
                    new AccountInterestPosting(
                            accounts.get(entry.getKey()), interestCarrier(entry.getValue())));
        }
        return aggregated;
    }

    /**
     * The summed interest, travelling inside a `Tran` because a composite carries existing entities
     * only (ADR-0020). Every other component is filled at its declared PIC width rather than left
     * null: the body is expected to read `tranAmt` and nothing else, and a null would turn a body
     * that reads one more field into a `NullPointerException` instead of a comparison failure that
     * says which field.
     */
    private static Tran interestCarrier(BigDecimal total) {
        return new Tran(
                CobolText.spaces(16),
                CobolText.spaces(2),
                BigDecimal.ZERO,
                CobolText.spaces(10),
                CobolText.spaces(100),
                total,
                BigDecimal.ZERO,
                CobolText.spaces(50),
                CobolText.spaces(50),
                CobolText.spaces(10),
                CobolText.spaces(16),
                CobolText.spaces(26),
                CobolText.spaces(26));
    }
}
