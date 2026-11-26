# ADR 0001: Keep the submission contract and starter baseline

- **Status:** Accepted
- **Decision:** Add a small standard-library `lumen_ex` package and root launcher while
  retaining the official starter implementation and its existing contract. Keep
  `raksha.toml` at repository root and use its required `main.py --input ... --output ...`
  interface.
- **Context:** The official kit already provides a working baseline, practice dataset,
  output validator, and checks. Replacing the starter or inventing a new output format
  would add risk without advancing the requested foundation milestone.
- **Alternatives considered:** Rebuild the starter immediately as a modular engine, or
  introduce a packaging framework and third-party validation/configuration dependencies.
  Both options expand this milestone and risk changing baseline output behavior.
- **Consequences:** Later milestones can replace the adapter with implementations of
  the typed stage interfaces. The starter baseline still leaks most PII and links no
  records; that known behavior is preserved rather than misrepresented as a firewall.
