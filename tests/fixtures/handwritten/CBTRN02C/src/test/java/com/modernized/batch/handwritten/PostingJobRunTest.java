package com.modernized.batch.handwritten;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import org.junit.jupiter.api.Test;
import org.springframework.batch.core.job.Job;
import org.springframework.batch.core.job.JobExecution;
import org.springframework.batch.core.job.parameters.JobParametersBuilder;
import org.springframework.batch.core.launch.JobOperator;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;

/**
 * Runs the rendered posting job. The assertions here are about the <em>run</em>, never about the
 * values: whether the generated logic matches COBOL is decided by the Python differential against
 * the oracle (ADR-0029), and duplicating that judgement here would give it a second place to
 * disagree with itself.
 *
 * <p>What is asserted is the one thing the differential cannot see if it goes wrong: that the job
 * completed and wrote something. A run that abends on the first created balance row would otherwise
 * present as a comparison failure about missing records.
 */
class PostingJobRunTest {

    @Test
    void runsThePostingJobAndWritesTheTransactionsItAccepted() throws Exception {
        JobExecution execution;
        try (AnnotationConfigApplicationContext context = new AnnotationConfigApplicationContext()) {
            context.getEnvironment().setActiveProfiles(PostingWiring.PROFILE);
            // The rendered configuration carries the job, its infrastructure and the step; the
            // hand-written one carries the file paths and the working set they bind. Registering
            // both is what the split looks like from the outside.
            context.register(
                    com.modernized.batch.job.PostingJobConfiguration.class, PostingWiring.class);
            context.refresh();

            Job job = context.getBean(Job.class);
            JobOperator operator = context.getBean(JobOperator.class);
            execution =
                    operator.start(
                            job,
                            new JobParametersBuilder()
                                    .addString("source", "handwritten-wiring")
                                    .toJobParameters());
        }

        assertEquals(
                "COMPLETED",
                execution.getExitStatus().getExitCode(),
                execution.getAllFailureExceptions().toString());
        assertTrue(Files.exists(PostingWiring.OUTPUT), "no candidate output written");

        long written = Files.size(PostingWiring.OUTPUT) / 350;
        // 300 daily transactions in, and the credit-limit check is what makes it fewer out.
        // Asserted rather than left to the differential so that "the job ran and wrote nothing"
        // cannot present as a comparison failure.
        assertTrue(written > 0 && written < 300, "wrote " + written + " records");
    }
}
