# Release process

No release exists merely because the project has version 0.5.0. Keep changes under Unreleased until separately approved. Do not auto-create tags, Releases or repositories.

Before release, require:

1. Approved, exact reviewed commit and file/history security checks; clean working state or documented unrelated changes protected.
2. Relevant unit tests and four isolated synthetic scenarios, actual numerical/coverage checks, source fingerprints and recorded archive/commit identity. Test failures block release.
3. Reviewed report quality and explicit known limitations. Document new-machine dependency and clean-checkout evidence separately; missing evidence must not be described as passing.
4. Confirmed private remote, authentication, explicit refs and approvals for commit, push, merge, tag and Release as applicable. Never force push.
5. A tested rollback plan and preserved previous artifacts. Release notes include version, date, features/fixes, limitations, test results and rollback steps.

Do not modify user Skills or production configuration to make release checks green. Source admission, production deployment and GitHub publishing are independent gates.
