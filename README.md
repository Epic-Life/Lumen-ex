# Lumen-ex

Lumen-ex is an offline, CPU-only, LLM-free healthcare-data privacy firewall project
for the Secret Wards hackathon track. This milestone establishes the submission
foundation and modular contracts only. It does **not** implement a complete firewall,
PII detector, patient matcher, anonymizer, or privacy-scoring engine.

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

The CLI indexes identity evidence across the input before sanitizing records.
Linkage uses matching identifier values or a normalized full name corroborated
by a phone number or date of birth. Records with insufficient evidence stay
unlinked. The release secret keys the pseudonyms written into the sanitized
records and the record index.

The sanitizer also coarsens labeled ages, PIN codes, and village fields with a
known district. It shifts recognized patient dates with a secret-keyed offset
shared across linked records. Unlinked records use a record-specific offset.
Hidden-channel checks cover filenames, schema labels, source metadata, and
supported text attachments. Unknown attachment formats are quarantined.

## Setup

Python 3.10 or later is required. Runtime dependencies are
`indic-transliteration` for offline Indic-script transliteration and `phonenumbers`
for offline phone validation and normalization.

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

`check_submission.py` may show a reminder for the separate 2-page Project Brief PDF;
that deliverable is outside this foundation milestone. Do not treat a format validator
pass or the starter's practice score as evidence that the privacy firewall is complete.

## Structure

```text
main.py                    evaluator entry point
raksha.toml                official submission metadata and command
config/policy.yaml         validated policy settings
lumen_ex/
  cli.py                   arguments, configuration, safe diagnostics
  config.py                validated policy loading
  pipeline.py              injectable minimal stage coordinator
  stages.py                future module interfaces
  contracts/models.py      pipeline data contracts
  contracts/identity.py    compact identity projection contracts
  identity.py              identity extraction and field normalization
  detection/indic_names.py Devanagari-aware candidate name normalization
  linkage.py               cross-file patient matching
  baseline.py              file processing with detection and linkage
starter/                   official starter and policy (preserved)
tests/                     standard-library foundation tests
docs/architecture.md       responsibilities, boundaries, and data flow
docs/contracts.md          internal models and official format compatibility
docs/decisions/            material architecture decisions
validate_output.py         official output format validator (unchanged)
check_dev.py               official practice self-check (unchanged)
check_submission.py        official pre-submit check (unchanged)
```

More detail: [architecture](docs/architecture.md), [contracts](docs/contracts.md), and
[ADR 0001](docs/decisions/0001-project-foundation.md).
