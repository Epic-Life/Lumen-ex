# Lumen-ex

Lumen-ex is an offline, CPU-only, LLM-free healthcare-data privacy firewall project
for the Secret Wards hackathon track. The repository includes its submission
foundation, file-ingestion API, identity helpers, a deterministic PII detector, a
conservative patient-linkage stage, and a modular cohort-aware privacy transformation
stage. The submission CLI retains the starter adapter, now guarded by an offline
pre-write leak scanner for sanitized files, record metadata, reports, and filenames.
The modular cohort-aware privacy stage is not yet integrated with submission output.

The official challenge kit is included in this repository. Read [START_HERE.md](START_HERE.md)
and the official [problem statement](docs/PROBLEM_STATEMENT.md),
[output specification](docs/SPEC.md), [scoring](docs/SCORING.md),
[dataset guide](docs/DATASET.md), [attack guide](docs/ATTACKS.md),
[sandbox rules](docs/SANDBOX.md), and [FAQ](docs/FAQ.md) before later milestones.
`dev/truth/truth.json` is practice evaluation data only: production processing must not
read it, and this foundation does not import it.

## Challenge restrictions

- Runtime must work offline on CPU and must not use LLMs, generative models, or
  pretrained transformers above the permitted parameter limit.
- Do not access the network during submission execution.
- Keep processing deterministic for identical input and secret.
- Keep secrets out of files and output; do not modify input data.
- Quarantine unsupported or unsafe files; never silently pass them through as clean.
- Preserve clinical values according to the official specification.

These are project requirements, not claims that the current baseline achieves them.
The shipped submission CLI still uses the starter's basic regex transformation, which
has incomplete handling of hidden PII channels. The M07 guard uses the existing
detector to quarantine detected leaks before writing each artifact, but cannot guarantee
that all PII is detected. The modular privacy stage requires detector and linkage
results plus a complete cohort; it does not replace the official output writer.

## Setup

Python 3.10 or later is required. Runtime dependencies are
`indic-transliteration` for offline Indic-script transliteration and `phonenumbers`
for offline phone validation and normalization, `ijson` for bounded JSON parsing, and
`cryptography` for authenticated encryption and keyed indexes.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The submission contract is the root [`raksha.toml`](raksha.toml). The evaluator runs
`python main.py --input {input} --output {output}` and supplies `RAKSHA_SECRET` through
the environment. For local practice in PowerShell:

```powershell
$env:RAKSHA_SECRET = "practice"
python main.py --input dev\input --output my_output
python validate_output.py --input dev\input --output my_output
python check_dev.py --output my_output
```

The final two commands are official kit checks; `check_dev.py` consults the practice
truth file for evaluation and is not part of runtime processing.

## Development

```powershell
python -m unittest discover -s tests -v
python check_submission.py --repo . --input dev\input --run
```

`check_submission.py` may show a reminder for the separate 2-page Project Brief PDF.
Do not treat a format validator pass or the starter's practice score as evidence that
the privacy firewall is complete.

## Structure

```text
main.py                    evaluator entry point
raksha.toml                official submission metadata and command
config/policy.yaml         validated policy settings
lumen_ex/
  cli.py                   arguments, configuration, safe diagnostics
  config.py                validated policy loading
  pipeline.py              injectable stage coordinator and ingestion entry point
  stages.py                future module interfaces
  contracts/models.py      pipeline data contracts
  contracts/identity.py    compact identity projection contracts
  privacy/                 keyed pseudonyms, dates, generalization, risk, and transforms
  value_normalization.py   low-level age/date normalizers shared without package cycles
  ingestion.py             deterministic discovery, quarantine outcomes, and readers
  identity.py              identity extraction and field normalization
  registry.py              encrypted per-run registry and guarded cluster aliases
  matching.py              bounded candidate retrieval and conservative linkage
  detection/indic_names.py Devanagari-aware candidate name normalization
  detection/pii.py       deterministic multilingual PII detection
  security/                 pre-write leak scanning and artifact checks
  baseline.py              compatibility adapter for the shipped starter
starter/                   starter implementation with M07 pre-write guard
tests/                     foundation, ingestion, identity, detection, linkage, and privacy tests
docs/architecture.md       responsibilities, boundaries, and data flow
docs/contracts.md          internal models and official format compatibility
docs/security.md           key handling, scanning coverage, and limitations
docs/decisions/            material architecture decisions
validate_output.py         official output format validator (unchanged)
check_dev.py               official practice self-check (unchanged)
check_submission.py        official pre-submit check (unchanged)
```

More detail: [architecture](docs/architecture.md), [contracts](docs/contracts.md), and
[ADR 0001](docs/decisions/0001-project-foundation.md).
