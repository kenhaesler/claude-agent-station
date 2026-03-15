---
name: Debugging
description: Systematic bug diagnosis — reproduce, isolate, root-cause, fix, prevent
role: employee
---

# Skill: Debugging

Follow this systematic approach to diagnose and fix bugs.

## Step 1: Reproduce
- Get the exact steps, input, and environment that triggers the bug
- Reproduce it yourself before attempting any fix
- If it's intermittent, identify conditions that increase likelihood
- Write a failing test that captures the bug (test-first fixing)

## Step 2: Isolate
- Narrow the scope: which component, file, or function is responsible?
- Use binary search: comment out half the code path, check if bug persists
- Check recent changes: `git log --oneline -20` and `git diff HEAD~5`
- Read error messages and stack traces carefully — the answer is often there
- Check logs at the point of failure, not just the final error

## Step 3: Understand Root Cause
- Don't fix symptoms — find the actual cause
- Ask "why does this happen?" repeatedly until you reach the real issue
- Common root causes:
  - Off-by-one errors and boundary conditions
  - Null/undefined values reaching code that assumes non-null
  - Race conditions in async or concurrent code
  - Stale state or cache invalidation failures
  - Type coercion or implicit conversion surprises
  - Environment differences (dev vs prod config, versions)

## Step 4: Fix
- Make the smallest change that fixes the root cause
- Don't refactor while fixing — keep the fix isolated
- Verify the failing test now passes
- Run the full test suite to check for regressions
- Document non-obvious fixes with a code comment explaining why

## Step 5: Prevent
- Add the reproduction test case to the test suite permanently
- If the bug class is common, add a linter rule or type constraint
- Update documentation if the bug stemmed from unclear behavior
