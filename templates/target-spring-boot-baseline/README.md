# target-spring-boot-baseline

Generic Maven scaffold `modernization_engineer` seeds generated code into — Java 25 toolchain,
Spring Boot 3.x/4.x starters, Jakarta Validation, JUnit 5. Domain-agnostic: no CardDemo or any
other tenant specifics belong here, only what any modernized batch program needs regardless of
which one it is.

Not yet built — lands in Milestone C4 alongside `modernization_engineer` and the sandboxed
compiler, once there's generated code to actually compile against it. Maven itself is pinned to
whatever `mvn -version` reports as latest stable at that point, not a version guessed here ahead
of time.
