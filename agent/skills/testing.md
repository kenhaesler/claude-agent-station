# Skill: Testing

Apply these practices when writing or evaluating tests.

## Test Strategy
- **Unit tests** for pure logic, calculations, transformations
- **Integration tests** for database queries, API endpoints, service interactions
- **E2E tests** only for critical user flows (login, checkout, data export)
- Prefer fast, isolated unit tests — they catch most bugs cheaply

## Writing Good Tests
- Each test verifies one behavior (one assertion per logical concept)
- Test names describe the scenario: `test_login_fails_with_expired_token`
- Follow Arrange-Act-Assert (or Given-When-Then) structure
- Tests must be deterministic — no random data, no time-dependent logic
- Tests must be independent — no shared mutable state between tests

## Edge Cases to Cover
- **Boundary values**: 0, 1, -1, max int, empty string, empty list
- **Null/None/undefined** inputs at every public interface
- **Error paths**: network failure, invalid input, permission denied, timeout
- **Concurrency**: race conditions if the code is multi-threaded or async
- **Large inputs**: performance doesn't degrade unexpectedly

## Coverage Guidelines
- Target 80%+ line coverage for new code
- 100% coverage on critical paths (auth, payments, data mutations)
- Coverage alone is not quality — meaningless assertions inflate coverage
- Missing test for a bug fix is a review blocker

## Anti-Patterns to Avoid
- Testing implementation details (private methods, internal state)
- Mocking everything — if the mock is complex, use a real integration test
- Flaky tests — fix or delete them; flaky tests erode trust
- Copy-paste test code — use parameterized tests or test fixtures
