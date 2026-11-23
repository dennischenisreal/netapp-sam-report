# Security and admission checks

Never commit or upload customer identities, addresses, topology, real ASUP, knowledge workbooks, performance data, reports, private rules, local settings, runtime locks, backups, screenshots, raw chat exports or credentials. Private repositories do not waive these rules. Unknown files stay excluded.

Review an exact file inventory: canonical relative names, text encoding, size, symlinks/junctions, SHA256, content patterns and context. Synthetic examples and universal technical constants require explicit narrow justification; do not globally whitelist IPs or client-like strings. Pattern screening alone cannot prove arbitrary undisclosed secrets absent.

Before an approved commit, stage only explicitly reviewed paths and inspect the actual staged diff. Never git add . or git add -A. Scan explicitly selected branch/tag history before a push. Existing private checkpoint objects must not be copied, cleaned, rewritten, mirrored or included in an upload. Ignore rules neither sanitize tracked files nor remove history.

Use safe interactive authentication only after connection approval. Never request passwords/Tokens in chat, embed them in remote URLs or print them. Verify the remote is private and inspect nonempty remote history before changing it. All remote mutations require explicit approval.

Local packages must exactly match reviewed fingerprints and exclude .local and .git. Store detailed scan findings and provenance privately; share only sanitized conclusions. Re-review any byte changes before committing or distributing.
