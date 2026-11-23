# Requirements

## User-confirmed contracts

- Preserve existing Skill entrypoints and the six controller, capacity, case, firmware, network/EMS and best-practice modules. Case may be empty; it and network/EMS must still run.
- Complete sources produce 25 final sheets and fourteen best-practice checks. Only actual absence of firmware sources may omit the corresponding firmware sheet, with cover/index/coverage evidence. Corruption or parsing failures are not absence.
- Use defaults unless custom thresholds are explicitly requested. Preserve calculations, output conventions and customer naming supplied privately at runtime.
- Preserve complete cluster/SVM/volume identities and exact volume names; same names in different clusters or SVMs are distinct. Unknown values remain N/A, not zero. Numeric changes require source/units and independent arithmetic evidence.
- Check13 is strictly older than 120 days and excludes constituent-only snapshot rows as already implemented. Convert is advisory with existing size, use, growth, time-span and sampling criteria; it never changes storage.
- Do not overwrite original inputs, knowledge files or reports. Keep real data, private profiles and aliases local. Ordinary reporting does not edit code or create Git changes.

## Verification levels

Unit tests, import smoke tests, isolated full reports, numerical checks, rendered quality and production deployment are separate evidence. Success at one level cannot replace another. Historical private evidence may explain behavior but is not a reproducible public fixture or a claim about all configurations.
