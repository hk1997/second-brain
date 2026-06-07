# Contributor & Testing Guidelines for OAB

Welcome! This document defines the coding, testing, and evaluation standards for the Obsidian-to-Agent Automation Bridge (OAB). All agents and developers making modifications to this repository must strictly adhere to these practices to ensure security, safety, and reliability.

---

## 1. Coding Standards

* **Language:** Python 3.10+
* **Type Safety:** All Python files must use strict type hints (from the `typing` module) for all function signatures and complex variables.
* **Dependency Rule:** **Zero external package dependencies** for core operations. Use standard library modules (`os`, `sys`, `subprocess`, `re`, `json`, `datetime`, `shutil`, `urllib`) instead of third-party libraries (like `requests`, `pyyaml`, `watchdog`, or `pyobjc`) to maximize portability.
* **Documentation:** Preserve all existing comments and docstrings. Write clear, concise docstrings for all new modules and functions following the Google Style Python Docstrings format.

---

## 2. Testing Architecture

Any new feature or change must be accompanied by comprehensive tests under the `tests/` directory.

```
second-brain/
└── tests/
    ├── unit/            # Isolated unit tests (parsers, utility logic)
    ├── integration/     # Lifecycle end-to-end watcher tests
    ├── security/        # Sandbox and boundary verification tests
    └── evals/           # LLM Router quality evaluations
```

### 2.1 Unit Testing
* **Target:** Test regex command parsers, yaml parsers, configuration readers, and path validation functions.
* **Methodology:** Use Python's standard `unittest` library. Mock all system side-effects (file writes, terminal spawns, iMessage OSAScripts) using `unittest.mock`.
* **Standard:** Keep unit tests 100% deterministic with zero network or shell dependencies.

### 2.2 Integration Testing
* **Target:** Watcher loop lifecycle, state transitions (Pending -> Running -> Completed), and logs replication.
* **Methodology:** Use temporary directories (`tempfile.TemporaryDirectory`) as mocked Obsidian vaults. Write dummy command notes, run the watcher loop for a single iteration, and assert:
  1. The note’s tag mutated correctly.
  2. The expected logs were appended to `logs.md`.
  3. No system files were modified.

### 2.3 Security & Sandbox Verification Tests
Because the agent runs with elevated execution privileges, we must verify that the OS-level sandboxing works.
* **Target:** `sandbox-exec` kernel profiling.
* **Methodology:**
  1. Spawn a test task designed to read `/etc/passwd` or write to `~/Downloads/`.
  2. Run the subprocess inside the sandbox container.
  3. Assert that a `PermissionError` or shell `Operation not permitted` exit code is returned.
  4. Ensure these tests run and pass on any Mac executing the OAB code.

---

## 3. LLM Evaluations (Evals)

To ensure the **Router Subagent** consistently selects the correct complexity and routing parameters, we implement an **Eval Suite** under `tests/evals/run_evals.py`.

### 3.1 The Eval Dataset
We maintain a static dataset of 20 benchmark prompts representing diverse user intents:
```python
EVAL_DATASET = [
    {"prompt": "Make a calendar entry for dentist appointment tomorrow at 10 AM", "expected_complexity": "simple", "expected_server": "google-calendar"},
    {"prompt": "Refactor scripts/agent_sync.py to extract the regex logic", "expected_complexity": "complex", "expected_server": "vault-utilities"}
]
```

### 3.2 Evaluation Metrics
* **Accuracy:** The percentage of prompts where the router's JSON output matches the expected complexity.
* **Recall:** The percentage of correct MCP servers identified.
* **Validation:** Assert that the router's output always conforms to the schema defined in `technical_design.md`.

*Always run the eval suite before pushing changes to the Router prompt to prevent regressions in model selection accuracy.*
