# Skill: Git Workflow

Follow these conventions for all git operations.

## Commit Messages
- Use conventional format: `type(scope): description`
- Types: `feat`, `fix`, `refactor`, `test`, `docs`, `perf`, `chore`, `style`
- Description is imperative mood, lowercase, no period: `fix(auth): handle expired refresh tokens`
- Keep subject line under 72 characters
- Add body for non-obvious changes explaining **why**, not what

## Branching
- One branch per issue/task — never mix unrelated changes
- Branch from the project's base branch (usually `main`)
- Name format: `feature/<description>`, `fix/<description>`, `chore/<description>`
- Keep branches short-lived — merge within days, not weeks

## Commits
- Each commit should be atomic — one logical change that builds and passes tests
- Never commit half-done work; stash or finish first
- Never commit generated files, build artifacts, or dependencies
- Never commit `.env`, secrets, credentials, or large binaries
- Run linter and tests before committing

## Pull Requests
- PR title matches the commit convention: `feat(scope): description`
- Link the issue in the PR description (`Closes #123`)
- Keep PRs focused — under 400 lines changed when possible
- Include test evidence (test output, screenshots for UI changes)

## Safety Rules
- Never force-push to shared branches
- Never rewrite history on `main` or `develop`
- Always pull before pushing to avoid conflicts
- Review your own diff before creating a PR
