Identify performance bottlenecks in: $ARGUMENTS

Run `uv run radon cc $ARGUMENTS -mi B` for cyclomatic complexity (grade B+ = CC >= 6).
Read the module; identify: nested loops on large data, repeated computations, unnecessary copies, blocking I/O in async, missing caching.
Suggest specific optimizations with code examples.
