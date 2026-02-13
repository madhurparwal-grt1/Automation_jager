"""
PR Workflow Automation Package - Three-Run Sequence
====================================================

This package provides a Docker-based workflow for analyzing Pull Requests
using the THREE-RUN SEQUENCE for proper F2P/P2P validation.

THREE-RUN ARCHITECTURE:
-----------------------

**Part 1: Docker Image Creation + Run 1 (BASE Testing)**
- Clone repository
- Fetch PR refs and determine BASE/PR commits
- Checkout BASE commit
- Build immutable Docker image
- Run 1: BASE tests (no patches applied) - establish baseline
- Save and compress Docker image
- Generate state.json for Part 2

**Part 2: Patch Application + Evaluation (Runs 2 & 3)**
- Load state from Part 1
- Generate patch files:
  - pr.patch (full patch for Run 3)
  - test.patch (test files only for Run 2)
  - code.patch (code files only for metadata)
- Run 2: TEST_PATCH only - new tests MUST FAIL
- Run 3: FULL PATCH - new tests must PASS, no regressions
- Categorize tests using proper validation:
  - F2P = FAIL in Run 2 AND PASS in Run 3
  - P2P = PASS in Run 1 AND PASS in Run 3 (excluding F2P)
- Generate metadata (instance.json)
- Validate all outputs

USAGE:
------

Run both parts (full workflow):
    python -m automation_script.main_orchestrator \\
        https://github.com/owner/repo/pull/123 \\
        /path/to/workspace

Run Part 1 only:
    python -m automation_script.main_orchestrator \\
        --part1-only \\
        https://github.com/owner/repo/pull/123 \\
        /path/to/workspace

Run Part 2 only (requires Part 1 to have been run):
    python -m automation_script.main_orchestrator \\
        --part2-only \\
        /path/to/workspace

OUTPUT STRUCTURE:
-----------------

workspace/
├── state.json              # State file (Part 1 → Part 2)
├── repo/                   # Cloned repository
├── docker_images/
│   └── <author>_<repo>-<sha>.tar     # Docker image (uncompressed tar)
├── patches/
│   ├── pr.patch           # Full patch (test + code) for Run 3
│   ├── test.patch         # Test files only for Run 2
│   └── code.patch         # Code files only (for reference)
├── artifacts/
│   ├── base/              # Run 1: BASE test results (no patches)
│   │   ├── result.json
│   │   ├── result_results.jsonl
│   │   └── result_summary.json
│   ├── test_patch_only/   # Run 2: TEST_PATCH only results
│   │   ├── result.json
│   │   ├── result_results.jsonl
│   │   └── result_summary.json
│   └── pr/                # Run 3: FULL PATCH results
│       ├── result.json
│       ├── result_results.jsonl
│       └── result_summary.json
├── metadata/
│   └── instance.json      # SWE-bench metadata
└── logs/
    └── workflow.log       # Unified log file

MODULES:
--------

Entry Points:
- main_orchestrator.py     : Main entry point for two-part system
- part1_build_and_base.py  : Part 1 workflow
- part2_patch_and_evaluate.py : Part 2 workflow

Core:
- config.py                : Data classes and configuration
- git_operations.py        : Git operations
- github_api.py            : GitHub API for PR metadata

Docker:
- docker_builder_new.py    : Docker image building
- docker_runner.py         : Container orchestration
- container_runner.py      : In-container execution

Language/Testing:
- environment.py           : Language detection
- language_detection.py    : Language + test command wrapper
- test_results.py          : Test categorization

Metadata:
- metadata_generator.py    : Metadata generation (new)
- metadata.py              : Metadata generation (legacy)

Utilities:
- utils.py                 : General utilities
- artifacts.py             : Artifact handling

ARCHIVED:
---------
Old/obsolete files are in _archived/:
- main.py                  : Old single-script workflow
- main_docker.py           : Docker workflow prototype
- docker_builder.py        : Old Docker builder
- test_runner.py           : Old test runner
"""

__version__ = "2.0.0"
__author__ = "Velora"

# Re-export main entry points
from .part1_build_and_base import run_part1
from .part2_patch_and_evaluate import run_part2

# Re-export key classes
from .config import (
    PRInfo,
    TestResult,
    WorkspaceConfig,
    WorkflowMetadata,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
)

__all__ = [
    # Main functions
    "run_part1",
    "run_part2",
    # Data classes
    "PRInfo",
    "TestResult",
    "WorkspaceConfig",
    "WorkflowMetadata",
    # Constants
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT",
]
