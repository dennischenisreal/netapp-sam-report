# Changelog

## 0.2.0 - 2026-11-27

- Separate actual port purpose from ASUP's deprecated physical port Role.
  Trace current LIF roles/services through explicit IFGRP members and recorded
  VLAN bases so intercluster LIFs remain visible on their backing PA ports.
- Mark shared business/intercluster ports without splitting or duplicating PA
  traffic. Keep raw physical Role, snapshot identity/date and private LIF evidence.
- Version the ASUP cache and port-purpose metric contract; preserve numerical
  performance metrics, nine sheets and all-observed-down filtering.
- Add synthetic tests for dedicated/shared ports, VLANs, current placement,
  node isolation, missing configuration and contradictory IFGRP membership.

## 0.1.1 - 2026-11-24

- Selected the existing Report Skills repository, with independent PA ownership,
  versioning, source installation package and a future Trend data contract plan.
- Added exact-inventory source packaging, ZIP/hash/privacy verification and a
  non-overwriting suite staging helper; no customer data or runtime dependencies ship.
- Kept the reviewed report format and metric meaning unchanged. SAM and existing
  installed Skills are not moved or replaced. No stable tag/Release is implied.

## 0.1.0 - 2026-11-23

- Established the initial reviewed nine-sheet resource-first PA report contract.
- Added the managed local `netapp-pa-report` Skill and source-linked installation.
- Added a focused end-to-end entry, runtime checks, independent run directories,
  source/code fingerprints and verified, non-overwriting batch publication.
- Removed fixed November naming from the approved compact preparation step.
- Kept customer data/configuration local; added source privacy preflight,
  synthetic CI and an explicit GitHub synchronization plan.
- Monthly/quarterly/yearly trend reporting and AIQUM calibration remain future work.
