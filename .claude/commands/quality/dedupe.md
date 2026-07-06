Find and merge duplicate logic across the repository.

Read all source files; identify functions/patterns appearing in multiple places.
For each: create canonical version in a shared module, update all call sites, remove duplicates.
