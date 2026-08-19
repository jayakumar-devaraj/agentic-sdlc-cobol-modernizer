package com.modernized.batch.handwritten;

import com.modernized.batch.domain.Account;
import com.modernized.batch.domain.AccountInterestPosting;
import com.modernized.batch.domain.Tran;
import com.modernized.batch.domain.TranCatBalWithRate;
import com.modernized.batch.domain.TranWithContext;
import com.modernized.batch.processor.CompleteTransactionProcessor;
import com.modernized.batch.processor.ComputeInterestProcessor;
import com.modernized.batch.processor.PostAccountInterestProcessor;
import com.modernized.batch.reader.ComputeInterestItemReader;
import com.modernized.batch.processor.PostAccountInterestProcessor;
import java.nio.file.Path;
import org.springframework.batch.core.job.Job;
import org.springframework.batch.core.configuration.JobRegistry;
import org.springframework.batch.core.configuration.support.MapJobRegistry;
import org.springframework.batch.core.job.builder.JobBuilder;
import org.springframework.batch.core.launch.JobOperator;
import org.springframework.batch.core.launch.support.TaskExecutorJobOperator;
import org.springframework.batch.core.repository.JobRepository;
import org.springframework.batch.core.repository.support.ResourcelessJobRepository;
import org.springframework.batch.core.step.Step;
import org.springframework.batch.core.step.builder.StepBuilder;
import org.springframework.batch.infrastructure.item.ItemReader;
import org.springframework.batch.infrastructure.support.transaction.ResourcelessTransactionManager;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;
import org.springframework.transaction.PlatformTransactionManager;

/**
 * The job, its steps, its reader and its writer -- <b>hand-written, for one program</b> (ADR-0030).
 *
 * <p>{@code generate} renders domain records, {@code ItemProcessor}s and an equivalence test. It
 * renders no reader, no writer, no step and no job, which is why a generated project compiles and
 * cannot run (gap G31). This file is the stopgap that decision chose, and it is bounded three ways:
 * it lives under {@code tests/fixtures/}, never in {@code templates/target-spring-boot-baseline/};
 * every result measured through it is reported as <em>generated logic inside hand-written
 * wiring</em>; and it is written against design.json, with every fact the design lacked recorded in
 * README.md beside it.
 *
 * <p><b>The step chain is the design's, not this file's invention.</b> design.json declares
 * {@code computeInterest} ({@code TranCatBalWithRate} to {@code TranWithContext}) followed by
 * {@code completeTransaction} ({@code TranWithContext} to {@code Tran}), so this is two chunk steps
 * in that order. The processors are constructed directly rather than component-scanned: they are
 * generated classes with no-argument constructors, and scanning would make which class runs a
 * property of the classpath rather than of the design.
 *
 * <p>No {@code DataSource}. A {@code ResourcelessJobRepository} keeps the run to what is being
 * measured -- the generated logic -- rather than to Spring Batch's schema. That is a divergence
 * from ADR-0019's PostgreSQL target and is finding F2.
 */
@Configuration
@Profile(InterestJobConfiguration.PROFILE)
public class InterestJobConfiguration {

    /**
     * Off unless a run asks for it, and that is not tidiness.
     *
     * <p>{@code BatchApplication} component-scans {@code com.modernized.batch}, so without this the
     * hand-written wiring joins the context of every Spring Boot test in every generated project --
     * which is ADR-0030's first bound arriving through a side door: the stopgap would be part of the
     * artifact rather than beside it. The baseline's own {@code BaselineStackTest} failed to load its
     * context on the first run for exactly that reason.
     */
    static final String PROFILE = "handwritten-wiring";

    /** Where the Python harness stages the oracle's inputs and collects the candidate output. */
    static final Path INPUT = Path.of("roundtrip", "input");

    static final Path OUTPUT = Path.of("roundtrip", "output", "candidate.jsonl");

    /** `CBACT04C` writes two files, so a round trip that measures one of them measures half. */
    static final Path ACCOUNT_OUTPUT = Path.of("roundtrip", "output", "candidate-accounts.jsonl");

    @Bean
    JobRepository jobRepository() {
        return new ResourcelessJobRepository();
    }

    @Bean
    PlatformTransactionManager transactionManager() {
        return new ResourcelessTransactionManager();
    }

    /**
     * <b>Rendered, not hand-written</b> (G31). `ComputeInterestItemReader` is generated from
     * design.json's access paths and record layouts -- which file drives, which are keyed lookups,
     * what fills each key, and where every field sits. The hand-written reader this replaced was
     * ADR-0030's stopgap, and the only thing left hand-written here is the job and step wiring.
     *
     * <p>The file arguments are the program's own `ASSIGN TO` names in declaration order, so what
     * this configuration supplies is paths -- not layout, not keys, not joins.
     */
    @Bean
    ItemReader<TranCatBalWithRate> tranCatBalWithRateItemReader() throws Exception {
        return new ComputeInterestItemReader(
                INPUT.resolve("tcatbal-posted.dat"),
                INPUT.resolve("acctdata-stage1.dat"),
                INPUT.resolve("cardxref.dat"),
                INPUT.resolve("discgrp.dat"));
    }

    @Bean
    TranWithContextStaging tranWithContextStaging() {
        return new TranWithContextStaging();
    }

    @Bean
    TranJsonLinesItemWriter tranJsonLinesItemWriter() throws Exception {
        return new TranJsonLinesItemWriter(OUTPUT);
    }

    @Bean
    AccountInterestPostingItemReader accountInterestPostingItemReader(
            TranWithContextStaging staging) {
        return new AccountInterestPostingItemReader(staging);
    }

    @Bean
    AccountJsonLinesItemWriter accountJsonLinesItemWriter() throws Exception {
        return new AccountJsonLinesItemWriter(ACCOUNT_OUTPUT);
    }

    @Bean
    Step computeInterestStep(
            JobRepository jobRepository,
            PlatformTransactionManager transactionManager,
            ItemReader<TranCatBalWithRate> reader,
            TranWithContextStaging staging) {
        return new StepBuilder("computeInterest", jobRepository)
                .<TranCatBalWithRate, TranWithContext>chunk(10)
                .reader(reader)
                .processor(new ComputeInterestProcessor())
                .writer(staging)
                .transactionManager(transactionManager)
                .build();
    }

    @Bean
    Step completeTransactionStep(
            JobRepository jobRepository,
            PlatformTransactionManager transactionManager,
            TranWithContextStaging staging,
            TranJsonLinesItemWriter writer) {
        return new StepBuilder("completeTransaction", jobRepository)
                .<TranWithContext, Tran>chunk(10)
                .reader(staging)
                .processor(new CompleteTransactionProcessor())
                .writer(writer)
                .transactionManager(transactionManager)
                .build();
    }

    /**
     * `1050-UPDATE-ACCOUNT`, over items whose interest is already summed (ADR-0027).
     *
     * <p>Third rather than parallel: it consumes what step 1 staged, so it cannot run until every
     * balance row has been through the interest calculation -- which is the same reason COBOL posts
     * on the account break rather than per row.
     */
    @Bean
    Step postAccountInterestStep(
            JobRepository jobRepository,
            PlatformTransactionManager transactionManager,
            AccountInterestPostingItemReader reader,
            AccountJsonLinesItemWriter writer) {
        return new StepBuilder("postAccountInterest", jobRepository)
                .<AccountInterestPosting, Account>chunk(10)
                .reader(reader)
                .processor(new PostAccountInterestProcessor())
                .writer(writer)
                .transactionManager(transactionManager)
                .build();
    }

    @Bean
    Job interestJob(
            JobRepository jobRepository,
            Step computeInterestStep,
            Step completeTransactionStep,
            Step postAccountInterestStep) {
        return new JobBuilder("interestJob", jobRepository)
                .start(computeInterestStep)
                .next(completeTransactionStep)
                .next(postAccountInterestStep)
                .build();
    }

    /**
     * A registry the operator resolves job names through. Required rather than optional in Spring
     * Batch 6: {@code TaskExecutorJobOperator.afterPropertiesSet} refuses without one.
     *
     * <p>It registers every {@code Job} bean itself, in {@code afterSingletonsInstantiated}. An
     * explicit {@code register(interestJob)} here therefore threw {@code DuplicateJobException} --
     * which is worth leaving on the record, because "register the thing you built" is the obvious
     * shape and it is wrong.
     */
    @Bean
    JobRegistry jobRegistry() {
        return new MapJobRegistry();
    }

    /** {@code JobLauncher} is deprecated for removal in Spring Batch 6; this is its replacement. */
    @Bean
    JobOperator jobOperator(JobRepository jobRepository, JobRegistry jobRegistry) throws Exception {
        TaskExecutorJobOperator operator = new TaskExecutorJobOperator();
        operator.setJobRepository(jobRepository);
        operator.setJobRegistry(jobRegistry);
        operator.afterPropertiesSet();
        return operator;
    }
}
