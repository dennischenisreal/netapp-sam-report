# Rollback

There is no approved Git tag or deployed candidate to roll back yet. Do not invent a target commit. Preserve the original private workspace, production Skills, configuration, locks and backups.

For a future authorized rollback: identify the exact known-good commit/tag and fingerprints, inspect uncommitted changes, back up source/configuration and runtime state, then present the restore and validation plan for approval. Prefer an isolated checkout for verification; no destructive reset/clean or history rewrite by default. A repository revert does not automatically deploy production.

After a separately approved deployment rollback, verify installed fingerprints, doctor results, relevant tests and a non-overwriting report comparison in a private validation directory. Record the old/new identities, evidence, remaining issues and recovery path. Never overwrite customer originals or upload rollback data.
