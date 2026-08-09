package com.modernized.batch;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import jakarta.persistence.EntityManagerFactory;
import javax.sql.DataSource;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.batch.core.repository.JobRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.TestPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

/**
 * The gate ADR-0019 put on step 38: prove the ecosystem's bytecode tooling actually works on the
 * pinned JDK <em>by running it</em>, not by reading release notes.
 *
 * <p>The risk Java 25 carries is not the language — a model that does not know Java 25 writes Java
 * 17 idioms, which compile. It is Hibernate/ByteBuddy, Mockito's instrumentation agent, and
 * Testcontainers, all of which historically lag a new JDK and all of which fail at <em>runtime</em>,
 * where the self-healing compile loop (step 42) cannot help because the compile succeeded. Each is
 * exercised below.
 *
 * <p>Nothing here skips. A test that quietly skips when Docker is unavailable would turn this gate
 * into decoration — the exact failure this repo already caught once, when its pgvector tests could
 * have skipped in CI forever.
 */
@SpringBootTest
@Testcontainers
@TestPropertySource(properties = {
        // Batch's metadata tables are created for this test only; production defers schema
        // ownership to migration tooling (see application.yml).
        "spring.batch.jdbc.initialize-schema=always"
})
class BaselineStackTest {

    @Container
    @ServiceConnection
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:16-alpine");

    @Autowired
    private JobRepository jobRepository;

    @Autowired
    private EntityManagerFactory entityManagerFactory;

    @Autowired
    private DataSource dataSource;

    @Test
    @DisplayName("the JVM running these tests really is the pinned release")
    void the_runtime_is_java_25() {
        // Without this, a workflow that silently resolved a different JDK would still go green and
        // the whole point of the gate would be lost. maven.compiler.release only constrains the
        // bytecode target; it says nothing about what actually ran.
        assertThat(Runtime.version().feature()).isEqualTo(25);
    }

    @Test
    @DisplayName("Hibernate bootstraps on this JDK, against a real PostgreSQL")
    void hibernate_starts() {
        // Building the EntityManagerFactory is what drives ByteBuddy, and ByteBuddy is the library
        // that historically breaks first on a new class-file version.
        assertThat(entityManagerFactory.isOpen()).isTrue();
        assertThat(entityManagerFactory.createEntityManager()).isNotNull();
    }

    @Test
    @DisplayName("Spring Batch's JobRepository is wired to the real database, not an embedded one")
    void the_job_repository_uses_postgres() {
        assertThat(jobRepository).isNotNull();

        String product = new JdbcTemplate(dataSource)
                .queryForObject("select version()", String.class);
        assertThat(product).contains("PostgreSQL");

        Integer batchTables = new JdbcTemplate(dataSource).queryForObject(
                "select count(*) from information_schema.tables where table_name like 'batch\\_%'",
                Integer.class);
        assertThat(batchTables).isGreaterThan(0);
    }

    @Test
    @DisplayName("Mockito can instrument a final class on this JDK")
    void mockito_instruments_a_final_class() {
        // A *final* class, deliberately. An interface would be proxied with plain JDK reflection
        // and would still pass with the instrumentation agent completely broken; a final class can
        // only be mocked through the inline mock maker's bytecode agent, which is the piece that
        // actually depends on the JDK's class-file version.
        FinalCollaborator mocked = mock(FinalCollaborator.class);
        when(mocked.value()).thenReturn(42);

        assertThat(mocked.value()).isEqualTo(42);
    }

    static final class FinalCollaborator {
        int value() {
            return 0;
        }
    }

    @Test
    @DisplayName("NUMERIC(p,s) enforces the COBOL PIC clause at the schema level")
    void numeric_precision_and_scale_are_enforced_by_the_database() {
        // ADR-0019's fourth reason for choosing PostgreSQL, checked rather than asserted: a value
        // too large for the declared precision is rejected by the database, not silently truncated
        // the way a COBOL MOVE would.
        JdbcTemplate jdbc = new JdbcTemplate(dataSource);
        jdbc.execute("create table pic_probe (amount numeric(12,2))");
        try {
            jdbc.update("insert into pic_probe (amount) values (?)", new java.math.BigDecimal("9999999999.99"));
            assertThat(jdbc.queryForObject("select amount from pic_probe", java.math.BigDecimal.class))
                    .isEqualByComparingTo("9999999999.99");

            org.assertj.core.api.Assertions.assertThatThrownBy(() -> jdbc.update(
                            "insert into pic_probe (amount) values (?)",
                            new java.math.BigDecimal("10000000000.00")))
                    .hasMessageContaining("numeric field overflow");
        } finally {
            jdbc.execute("drop table pic_probe");
        }
    }
}
