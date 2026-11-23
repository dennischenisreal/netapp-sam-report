# Acceptance evidence and limits

## 本轮中文管理文档验证（2026-11-23）

中文说明及维护边界更新后，候选 doctor、38 项测试、release-check 通过；两次打包逐字节一致，89 文件清单安全筛查无发现。四个合成场景共 188 项断言通过，quality 对象与上轮完全一致，未发现外部数据访问。52 个运行源未修改。不代表新机器、跨 Agent 安装或完整视觉验收通过；精确包指纹及运行收据保留在本地，不上传测试产物。

## Verified initial Git baseline

On 2026-11-23, commit 54c9acad9752ced4aeb99e3548099bec6455a5be was independently cloned locally and all 87 file hashes matched the reviewed inventory. Installation, doctor, release-check and 38 portable tests passed. Four real-entry synthetic scenarios passed 57, 58, 3 and 70 assertions (188 total), with no unexpected external access. Baseline archive SHA256: 68a74b78ec567650d9a77e40141808bbc915f33a70fb7816d58345b3b0df2d51. The first private main push matched local commit/tree; 38 tests and doctor were rerun before that push.

These results cover the exact baseline in its tested environment, not later uncommitted governance changes or a new machine. Documentation/package refreshes need fresh byte/security/link and packaging checks; a changed archive has a new digest. Keep detailed local evidence private and preserve earlier receipts.

## Historical scope and remaining gates

Historical evidence before management-only packaging: 302 private maintenance tests, 141 compatibility naming inputs, eight import smoke tests and 188 independent assertions across four synthetic report scenarios. Private regression is not the same suite as the portable tests in this package.

The complete synthetic case validates six real module subprocesses, 25 sheets, fourteen check sections/audits and advisory Convert evidence. Missing-switch omits only that firmware sheet. Broken-switch must reject corrupt source and deliver no report. Identity-gap must retain N/A and needs_review rather than substituting zero or hiding distinct object identities.

The independent numerical oracle covers node size/use, tier arithmetic and overprovisioning, volume and bucket identities, layer values, logical usage, snapshot age boundaries, policy scope, historical growth, case matching and network/firmware data. It is fixed synthetic coverage, not exhaustive validation of every customer configuration or every best-practice branch.

Repeat each scenario from the packaged validator using a reviewed archive and a fresh .local output directory. Store quality.json, access log and summary with the archive SHA256. The evaluation time and historical samples are intentionally fixed for reproducibility, not the current time used by production reports. Success in a negative scenario is not a delivered complete report.

Full visual release acceptance remains unconfirmed: seven key sheets were sampled, with clipped long cover descriptions and some headers noted; the renderer wrote images but exited nonzero. Full pixel/printing, fresh dependency installation, production synchronization and a stable GitHub Release remain unverified. The clean local Git checkout result above does not close those gates.
