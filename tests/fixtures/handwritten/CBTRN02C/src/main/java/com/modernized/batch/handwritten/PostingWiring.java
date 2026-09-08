package com.modernized.batch.handwritten;

import com.modernized.batch.domain.PostingInput;
import com.modernized.batch.domain.PostingResult;
import com.modernized.batch.reader.PostTransactionItemReader;
import com.modernized.batch.state.PostTransactionWorkingSet;
import com.modernized.batch.writer.PostTransactionItemWriter;
import java.nio.file.Path;
import org.springframework.batch.core.configuration.JobRegistry;
import org.springframework.batch.core.configuration.support.MapJobRegistry;
import org.springframework.batch.core.launch.JobOperator;
import org.springframework.batch.core.launch.support.TaskExecutorJobOperator;
import org.springframework.batch.core.repository.JobRepository;
import org.springframework.batch.core.repository.support.ResourcelessJobRepository;
import org.springframework.batch.infrastructure.support.transaction.ResourcelessTransactionManager;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.batch.infrastructure.item.ItemReader;
import org.springframework.batch.infrastructure.item.ItemWriter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;

/**
 * What the renderers refuse to produce for CBTRN02C -- <b>and nothing else</b>.
 *
 * <p>The job, its infrastructure beans, the step, the reader, the writer and the working set are
 * all rendered from design.json. What is not rendered is the same one thing as for CBACT04C: the
 * COBOL says {@code ASSIGN TO ACCTFILE}, an environment name, and nothing anywhere says what it
 * resolves to. Binding a name to a location is deployment (ADR-0030).
 *
 * <p><b>The working set is a bean because two rendered classes share it.</b> The reader takes its
 * ACCOUNT and TCATBAL lookups from it and the writer puts back into it, so one instance has to
 * reach both -- that is the whole mechanism ADR-0041 exists for, and it is what makes an item's
 * decision see the items before it.
 *
 * <p>Gated behind the rendered configuration's profile because {@code BatchApplication}
 * component-scans {@code com.modernized.batch}: without it this wiring would join the context of
 * every Spring Boot test in the generated project.
 */
@Configuration
@Profile(PostingWiring.PROFILE)
public class PostingWiring {

    /** The profile both this and the rendered configuration are registered under. */
    public static final String PROFILE = "handwritten-wiring";

    /** Where the harness stages the corpus and collects the candidate output. */
    static final Path INPUT = Path.of("roundtrip", "input");

    static final Path OUTPUT = Path.of("roundtrip", "output", "transact.dat");

    /**
     * The two files CBTRN02C reads by key and writes back, held for the length of the step.
     *
     * <p>These paths are both input and output: the store seeds itself from them and flushes back
     * to them, which is COBOL's {@code OPEN I-O} on the same file. The harness compares them
     * afterwards against what the real program left behind.
     */
    @Bean
    PostTransactionWorkingSet postTransactionWorkingSet() throws Exception {
        return new PostTransactionWorkingSet(
                INPUT.resolve("acctdata.dat"), INPUT.resolve("tcatbal.dat"));
    }

    /**
     * The rendered reader, bound to files and to the store.
     *
     * <p>Two paths only. ACCOUNT and TCATBAL are absent on purpose: a reader holding its own copy
     * of a file the writer is changing would answer every item from the state the job started in.
     */
    @Bean
    ItemReader<PostingInput> postingInputItemReader(PostTransactionWorkingSet state)
            throws Exception {
        return new PostTransactionItemReader(
                state, INPUT.resolve("dailytran.dat"), INPUT.resolve("cardxref.dat"));
    }

    /** The rendered writer: the transaction master by path, the other two through the store. */
    @Bean
    ItemWriter<PostingResult> postingResultItemWriter(PostTransactionWorkingSet state)
            throws Exception {
        return new PostTransactionItemWriter(state, OUTPUT);
    }

    /**
     * The batch infrastructure this context needs, which the rendered configuration deliberately
     * no longer carries (ADR-0080).
     *
     * <p>In the delivered application Spring Boot builds these against the configured
     * {@code DataSource}. This context is a plain {@code AnnotationConfigApplicationContext} with
     * neither auto-configuration nor a database, so the wiring that stands in for the generated
     * remainder supplies resourceless equivalents -- which is what this class is for.
     */
    @Bean
    JobRepository jobRepository() {
        return new ResourcelessJobRepository();
    }

    @Bean
    PlatformTransactionManager transactionManager() {
        return new ResourcelessTransactionManager();
    }

    @Bean
    JobRegistry jobRegistry() {
        return new MapJobRegistry();
    }

    @Bean
    JobOperator jobOperator(JobRepository jobRepository, JobRegistry jobRegistry) throws Exception {
        TaskExecutorJobOperator operator = new TaskExecutorJobOperator();
        operator.setJobRepository(jobRepository);
        operator.setJobRegistry(jobRegistry);
        operator.afterPropertiesSet();
        return operator;
    }
}
