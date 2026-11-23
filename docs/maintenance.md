# Simple maintenance commands

Read [maintenance-boundary.md](maintenance-boundary.md) before choosing an editing directory. The private workspace remains the sole active business source; this repository holds the reviewed Git baseline and approved governance work. Installed Skills are not editing sources. If task scope and authority disagree, stop before business edits and propose a handover or promotion plan.

| User request | Default workflow |
|---|---|
| 查看项目状态 | Read-only Git, branch/tag, deployment, requirements, decisions and pending-task checks. |
| 继续开发 / 继续 | Continue already approved unfinished work; otherwise propose the next bounded task and obtain approval. |
| 新增需求 / 修复问题 | Record sanitized requirements, assess impact/risk/tests, propose feature/* or fix/* and seek implementation/branch approval. |
| 运行测试 | Run available local tests and record actual scope, time, result and limitations. No publishing or deployment. |
| 准备发布 / 发布新版本 | Evaluate release/security/quality/rollback gates; seek separate tag/Release and remote approvals. |
| 同步GitHub | Inspect authentication, private remote, exact files and explicit ref histories; present exact operations and wait for push approval. |
| 回滚版本 | Protect current work, identify a verified target, propose restore/validation and wait for approval. |
| 生成 SAM Report | Use the separately validated production Skill and private configuration; no source edits, branch switching, commit, deployment or upload. |

Every development cycle reads AGENTS.md and the current project documents, checks state, explains scope/risk/tests, implements only approved changes, tests and updates CHANGELOG.md. Generate a clear proposed commit message, but do not commit without approval. Local validation output belongs in .local and must never be staged.

After approved document edits, refresh only affected package-manifest.json fingerprints and revalidate the complete inventory. Do not alter runtime source-manifest.json or production locks to hide drift. First-push approval is not approval of a later commit or push; pending governance changes stay local until separately approved.
