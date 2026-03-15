---
name: Code Review
description: Clean code checklist, naming, SOLID principles, and code smells
role: employee
---

# Skill: Code Review

Apply this checklist when reviewing or writing code.

## Clean Code
- Functions do one thing and are under 40 lines
- No deeply nested logic (max 3 levels); extract early returns or helpers
- No magic numbers or strings — use named constants
- No commented-out code — delete it (git remembers)
- No code duplication — extract shared logic into helpers

## Naming
- Variables describe what they hold, not their type (`userCount` not `n`)
- Booleans read as questions (`isReady`, `hasPermission`, `canRetry`)
- Functions describe their action (`fetchUser`, `validateInput`, `calculateTotal`)
- Consistent casing per language convention (camelCase, snake_case, PascalCase)

## SOLID Principles
- **Single Responsibility**: each module/class has one reason to change
- **Open/Closed**: extend via new code, not by modifying stable code
- **Dependency Inversion**: depend on abstractions, not concretions

## Error Handling
- Handle errors at the appropriate level — don't swallow silently
- Use specific error types over generic catches
- Validate inputs at boundaries (API endpoints, CLI args, config loaders)
- Fail fast with clear error messages

## Code Smells to Flag
- God objects (classes doing everything)
- Long parameter lists (>4 params → use an options/config object)
- Feature envy (method uses another class's data more than its own)
- Premature abstraction (don't generalize until you have 3+ concrete cases)
