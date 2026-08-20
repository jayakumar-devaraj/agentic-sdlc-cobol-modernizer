package com.modernized.batch.handwritten;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.batch.core.job.Job;
import org.springframework.batch.core.job.JobExecution;
import org.springframework.batch.core.job.parameters.JobParametersBuilder;
import org.springframework.batch.core.launch.JobOperator;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;

/**
 * Runs the hand-written job. The assertions here are about the <em>run</em>, never about the
 * values: whether the generated logic matches COBOL is decided by the Python differential against
 * {@code transact.dat} (ADR-0029), and duplicating that judgement here would give it a second place
 * to disagree with itself.
 *
 * <p>A plain {@code AnnotationConfigApplicationContext} rather than {@code @SpringBootTest}: Boot's
 * autoconfiguration would want a {@code DataSource} for JPA and a container to put it in, neither of
 * which is what is being measured.
 */
class InterestJobRunTest {

    @Test
    void runsTheInterestJobAndWritesOneRecordPerNonZeroRate() throws Exception {
        JobExecution execution;
        try (AnnotationConfigApplicationContext context = new AnnotationConfigApplicationContext()) {
            // The profile the wiring hides behind, so it stays out of every other context.
            context.getEnvironment().setActiveProfiles(HandWrittenRemainder.PROFILE);
            // The rendered configuration carries the job, its infrastructure, the staging and
            // two of the three steps; the hand-written one carries the third step, the
            // aggregation behind it, and the file paths. Registering both is what the split
            // looks like from the outside.
            context.register(
                    com.modernized.batch.job.InterestJobConfiguration.class,
                    HandWrittenRemainder.class);
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
        assertTrue(Files.exists(HandWrittenRemainder.OUTPUT), "no candidate output written");

        List<String> lines = Files.readAllLines(HandWrittenRemainder.OUTPUT);
        // 94 balance rows in, and the guard `IF DIS-INT-RATE NOT = 0` is what makes it fewer out.
        // Asserted rather than left to the differential so that "the job ran and wrote nothing"
        // cannot present as a comparison failure.
        assertTrue(!lines.isEmpty() && lines.size() < 94, "wrote " + lines.size() + " records");
    }
}
