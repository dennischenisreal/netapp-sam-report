# Roadmap

## Current preparation

Local sanitized source preparation, long-term governance and portable synthetic tests. No Git initialization, commit, remote, production deployment, tag or release is authorized by source preparation.

## Next approval gates

1. Review exact final file hashes and fresh tests. Confirm the independent source directory and Git author identity. Separately approve local Git initialization and the precise initial commit; preserve the private workspace/history.
2. Confirm the private netapp-sam-report repository URL, authentication and remote contents. Remote configuration and first push require explicit approval; no credentials in chat or remote URLs.
3. Validate the approved commit from a clean checkout, evaluate visual limitations and fresh dependency evidence, then seek approval for any stable tag/Release. Do not assume v1.0.0 or relabel version 0.5.0.

Production synchronization is a separate decision with backup, drift checks and regression. It is not a prerequisite for safely preparing local source history and must not happen automatically.
