package com.modernized.batch.handwritten;

import com.modernized.batch.domain.Account;
import com.modernized.batch.domain.Tran;
import com.modernized.batch.domain.TranCatBalWithRate;
import com.modernized.batch.job.InterestJobConfiguration;
import com.modernized.batch.reader.ComputeInterestItemReader;
import com.modernized.batch.writer.CompleteTransactionItemWriter;
import com.modernized.batch.writer.PostAccountInterestItemWriter;
import java.nio.file.Path;
import org.springframework.batch.infrastructure.item.ItemReader;
import org.springframework.batch.infrastructure.item.ItemWriter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;

/**
 * What the renderers refuse to produce for this program -- <b>and nothing else</b>.
 *
 * <p>The job, its infrastructure beans, the staging, all three step beans, the reader, the writers
 * and the control-break aggregation are rendered from design.json. <b>One thing is not</b>, and it
 * is not a gap in the design:
 *
 * <ul>
 *   <li><b>File paths.</b> A rendered reader takes {@code Path} arguments and a rendered writer
 *       takes one, because the COBOL says {@code ASSIGN TO TCATBALF} -- an environment name -- and
 *       nothing anywhere says what that resolves to. Binding them to locations is deployment, and
 *       arguably never belongs in a design at all.
 * </ul>
 *
 * <p>Gated behind the rendered configuration's profile because {@code BatchApplication}
 * component-scans {@code com.modernized.batch}: without it this wiring would join the context of
 * every Spring Boot test in the generated project.
 */
@Configuration
@Profile(HandWrittenRemainder.PROFILE)
public class HandWrittenRemainder {

    /** The profile both this and the rendered configuration are registered under. */
    public static final String PROFILE = "handwritten-wiring";

    /** Where the harness stages the oracle's inputs and collects the candidate output. */
    static final Path INPUT = Path.of("roundtrip", "input");

    static final Path OUTPUT = Path.of("roundtrip", "output", "transact.dat");

    /**
     * The rendered reader, bound to files.
     *
     * <p>The arguments are the program's own {@code ASSIGN TO} names in declaration order, so what
     * this supplies is paths -- not layout, not keys, not joins.
     */
    @Bean
    ItemReader<TranCatBalWithRate> tranCatBalWithRateItemReader() throws Exception {
        return new ComputeInterestItemReader(
                INPUT.resolve("tcatbal-posted.dat"),
                INPUT.resolve("acctdata-stage1.dat"),
                INPUT.resolve("cardxref.dat"),
                INPUT.resolve("discgrp.dat"));
    }

    /** The rendered transaction writer, bound to a file. */
    @Bean
    ItemWriter<Tran> tranItemWriter() throws Exception {
        return new CompleteTransactionItemWriter(OUTPUT);
    }

    /**
     * The rendered account writer, bound to the file it updates in place -- so the file this job
     * read its accounts from is the file it leaves behind, and the comparison reads that.
     */
    @Bean
    ItemWriter<Account> accountItemWriter() throws Exception {
        return new PostAccountInterestItemWriter(INPUT.resolve("acctdata-stage1.dat"));
    }
}
