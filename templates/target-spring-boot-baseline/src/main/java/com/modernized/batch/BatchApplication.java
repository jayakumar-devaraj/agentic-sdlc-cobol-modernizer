package com.modernized.batch;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Entry point for the modernized batch application.
 *
 * <p>Deliberately carries no {@code @EnableBatchProcessing}: Spring Boot's own auto-configuration
 * builds the {@code JobRepository} and {@code JobLauncher} against the configured DataSource, and
 * adding the annotation switches that auto-configuration <em>off</em> — a mistake that is easy for
 * a code generator to make, because every pre-Boot-3 example on the internet has it.
 *
 * <p>There are no {@code Job} beans here. Generated code contributes them in sub-packages, one per
 * migrated COBOL program, so nothing in this file is ever rewritten when the template is seeded.
 */
@SpringBootApplication
public class BatchApplication {

    public static void main(String[] args) {
        SpringApplication.run(BatchApplication.class, args);
    }
}
