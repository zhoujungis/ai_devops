"""Cross-app services.

These live outside ``apps/`` because they read across several apps at once. Nothing
inside a single app should own the answer to "what does this commit imply" — that
question spans codebase, testing, bugs, requirements and releases.
"""
