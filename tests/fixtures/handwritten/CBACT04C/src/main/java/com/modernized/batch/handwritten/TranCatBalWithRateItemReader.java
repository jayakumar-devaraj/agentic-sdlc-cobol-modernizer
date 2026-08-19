package com.modernized.batch.handwritten;

import com.modernized.batch.domain.Account;
import com.modernized.batch.domain.CardXref;
import com.modernized.batch.domain.DisGroup;
import com.modernized.batch.domain.TranCatBal;
import com.modernized.batch.domain.TranCatBalWithRate;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.springframework.batch.infrastructure.item.ItemReader;

/**
 * The driving read plus the three keyed lookups, as one item.
 *
 * <p><b>This is the class ADR-0030 says cannot be rendered from design.json today.</b> The design
 * declares that {@code computeInterest} consumes a {@code TranCatBalWithRate} composed of
 * {@code TranCatBal}, {@code DisGroup}, {@code Account} and {@code CardXref}. It does not say where
 * any of them comes from, which is driving and which is a lookup, or how they join --
 * {@code CompositeType} declares components, never keys. Every one of those facts came from
 * {@code CBACT04C}'s {@code FILE-CONTROL} and its main loop, and each is recorded as a finding in
 * README.md.
 *
 * <p>Two behaviours here are business logic living in wiring, not infrastructure, and that is
 * finding F4: the {@code 'DEFAULT'} disclosure-group fallback ({@code 1200-GET-INTEREST-RATE}) and
 * the abend when a lookup misses. A rendered reader would have to carry both or lose them.
 */
class TranCatBalWithRateItemReader implements ItemReader<TranCatBalWithRate> {

    /** {@code CVTRA01Y}, and the length is finding F1: design.json carries no record length. */
    private static final int TCATBAL_LEN = 50;
    private static final int DISCGRP_LEN = 50;
    private static final int ACCOUNT_LEN = 300;
    private static final int CARDXREF_LEN = 50;

    /** {@code MOVE 'DEFAULT' TO FD-DIS-ACCT-GROUP-ID}, padded to the key's declared X(10). */
    private static final String DEFAULT_GROUP = "DEFAULT   ";

    private final List<String> balances;
    private final Map<String, Account> accountsById;
    private final Map<String, CardXref> xrefsByAccountId;
    private final Map<String, DisGroup> disclosureGroupsByKey;
    private int next;

    TranCatBalWithRateItemReader(Path tcatbalPosted, Path acctdata, Path cardxref, Path discgrp)
            throws Exception {
        this.balances = CobolFixedWidth.fixedRecords(tcatbalPosted, TCATBAL_LEN);
        this.accountsById = new HashMap<>();
        for (String record : CobolFixedWidth.fixedRecords(acctdata, ACCOUNT_LEN)) {
            Account account = account(record);
            accountsById.put(CobolFixedWidth.text(record, 0, 11), account);
        }
        this.xrefsByAccountId = new HashMap<>();
        for (String record : CobolFixedWidth.lineRecords(cardxref, CARDXREF_LEN)) {
            xrefsByAccountId.put(CobolFixedWidth.text(record, 25, 11), cardXref(record));
        }
        this.disclosureGroupsByKey = new HashMap<>();
        for (String record : CobolFixedWidth.lineRecords(discgrp, DISCGRP_LEN)) {
            disclosureGroupsByKey.put(CobolFixedWidth.text(record, 0, 16), disGroup(record));
        }
    }

    @Override
    public TranCatBalWithRate read() {
        if (next >= balances.size()) {
            return null;
        }
        String record = balances.get(next++);
        String accountId = CobolFixedWidth.text(record, 0, 11);
        String typeCd = CobolFixedWidth.text(record, 11, 2);
        String categoryCd = CobolFixedWidth.text(record, 13, 4);

        Account account = require(accountsById.get(accountId), "ACCOUNT NOT FOUND: " + accountId);
        CardXref xref = require(xrefsByAccountId.get(accountId), "XREF NOT FOUND: " + accountId);

        // 1200-GET-INTEREST-RATE: read on the account's own group, and on file status 23 re-read
        // under 'DEFAULT'. A miss on both is an abend in the COBOL, so it is an exception here --
        // silently substituting a zero rate would suppress the transaction rather than fail.
        DisGroup group = disclosureGroupsByKey.get(account.acctGroupId() + typeCd + categoryCd);
        if (group == null) {
            group = disclosureGroupsByKey.get(DEFAULT_GROUP + typeCd + categoryCd);
        }
        require(group, "DISCLOSURE GROUP RECORD MISSING: " + typeCd + "/" + categoryCd);

        return new TranCatBalWithRate(tranCatBal(record), group, account, xref);
    }

    private static <T> T require(T value, String message) {
        if (value == null) {
            throw new IllegalStateException(message);
        }
        return value;
    }

    private static TranCatBal tranCatBal(String record) {
        return new TranCatBal(
                CobolFixedWidth.number(record, 0, 11, 0),
                CobolFixedWidth.text(record, 11, 2),
                CobolFixedWidth.number(record, 13, 4, 0),
                CobolFixedWidth.number(record, 17, 11, 2));
    }

    private static DisGroup disGroup(String record) {
        return new DisGroup(
                CobolFixedWidth.text(record, 0, 10),
                CobolFixedWidth.text(record, 10, 2),
                CobolFixedWidth.number(record, 12, 4, 0),
                CobolFixedWidth.number(record, 16, 6, 2));
    }

    private static CardXref cardXref(String record) {
        return new CardXref(
                CobolFixedWidth.text(record, 0, 16),
                CobolFixedWidth.number(record, 16, 9, 0),
                CobolFixedWidth.number(record, 25, 11, 0));
    }

    private static Account account(String record) {
        return new Account(
                CobolFixedWidth.number(record, 0, 11, 0),
                CobolFixedWidth.text(record, 11, 1),
                CobolFixedWidth.number(record, 12, 12, 2),
                CobolFixedWidth.number(record, 24, 12, 2),
                CobolFixedWidth.number(record, 36, 12, 2),
                CobolFixedWidth.text(record, 48, 10),
                CobolFixedWidth.text(record, 58, 10),
                CobolFixedWidth.text(record, 68, 10),
                CobolFixedWidth.number(record, 78, 12, 2),
                CobolFixedWidth.number(record, 90, 12, 2),
                CobolFixedWidth.text(record, 102, 10),
                CobolFixedWidth.text(record, 112, 10));
    }
}
