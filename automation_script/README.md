# PR Evaluation Automation Script

Automated system for generating Docker images and test artifacts from GitHub Pull Requests.

> **Navigation:** This module is part of the [Automation_jager](../README.md) project. See the [main README](../README.md) for installation, prerequisites, and the multi-architecture Docker image builder. For multi-build utilities, see [automation_script_build_multi](../automation_script_build_multi/README.md).

## Quick Start

```bash
# Full workflow (Part 1 + Part 2)
python -m automation_script.main_orchestrator \
  https://github.com/owner/repo/pull/NUMBER \
  ./Output_dataset
```

## Commands

### Full Workflow

```bash
python -m automation_script.main_orchestrator <PR_URL> <OUTPUT_DIR>
```

### Part 1 Only (Build + BASE Tests)

```bash
python -m automation_script.main_orchestrator --part1-only <PR_URL> <OUTPUT_DIR>
```

### Part 2 Only (Patch + Evaluate)

```bash
python -m automation_script.main_orchestrator --part2-only <WORKSPACE_PATH>
```

## Options

| Option | Description |
|--------|-------------|
| `--language <lang>` | Override language detection (python, go, rust, java, javascript, typescript, csharp, ruby) |
| `--test-cmd <cmd>` | Override test command (e.g., "cargo test", "go test ./...") |
| `--rust-subdir <dir>` | Subdirectory containing Cargo.toml for Rust projects |
| `--base-commit <sha>` | Override auto-detected base commit (required for merged PRs) |
| `--shallow-clone` | Use shallow git clone (faster for large repos) |
| `--reuse-image <tag>` | Reuse existing Docker image (skip rebuild) |
| `--skip-tests` | Skip test execution (only build artifacts) |
| `--keep-repo` | Keep cloned repository after completion |
| `--cleanup-images` | Remove Docker images after completion |
| `--part1-only` | Run only Part 1 |
| `--part2-only` | Run only Part 2 |

## Examples

### Basic Usage

```bash
# Kubernetes PR
python -m automation_script.main_orchestrator \
  https://github.com/kubernetes/kubernetes/pull/134345 \
  ./Output_dataset

# Rust project with subdirectory
python -m automation_script.main_orchestrator \
  --language rust \
  --rust-subdir engine \
  https://github.com/owner/repo/pull/123 \
  ./Output_dataset

# Fast mode for large repos
python -m automation_script.main_orchestrator \
  --shallow-clone --skip-tests \
  https://github.com/owner/repo/pull/123 \
  ./Output_dataset

# Reuse existing Docker image
python -m automation_script.main_orchestrator \
  --reuse-image pr-eval:pr-134345-base-abc123def \
  https://github.com/owner/repo/pull/123 \
  ./Output_dataset
```

### Override Detection

```bash
# Force Go language
python -m automation_script.main_orchestrator \
  --language go \
  https://github.com/owner/repo/pull/123 \
  ./Output_dataset

# Custom test command
python -m automation_script.main_orchestrator \
  --test-cmd "make test" \
  https://github.com/owner/repo/pull/123 \
  ./Output_dataset
```

## Output Structure

```
Output_dataset/
└── owner-repo_pr_NUMBER/
    ├── artifacts/
    │   ├── base/           # BASE test results
    │   └── pr/             # PR test results
    ├── docker_images/
    │   ├── owner_repo-COMMIT.tar        # Docker image
    │   └── owner_repo-COMMIT.Dockerfile # Generated Dockerfile
    ├── logs/
    │   ├── part1_build_and_base.log
    │   └── part2_patch_and_evaluate.log
    ├── metadata/
    │   └── task_instance.json  # Final metadata
    ├── patches/
    │   └── pr.patch            # PR patch file
    └── state.json              # State between Part 1 and Part 2
```

## Two-Phase Pipeline

### Part 1: Build + BASE Testing

1. Clone repository
2. Fetch PR refs
3. Determine BASE commit (merge-base)
4. Checkout BASE commit
5. Detect language and test command
6. Generate Dockerfile
7. Build Docker image
8. Run BASE tests
9. Save Docker image to tar
10. Generate state.json

### Part 2: Patch + Evaluate

1. Load state from Part 1
2. Generate PR patch
3. Apply patch in container
4. Run PATCHED tests
5. Compare results:
   - **FAIL_TO_PASS**: Tests that failed in BASE but pass after patch
   - **PASS_TO_PASS**: Tests that passed in both
6. Generate metadata
7. Validate outputs

## Supported Languages

| Language | Toolchain | Test Command |
|----------|-----------|--------------|
| Python | venv @ /saved/venv/ENV | pytest |
| Go | /usr/local/go | go test ./... |
| Rust | rustup @ /saved/ENV | cargo test |
| Java | OpenJDK 17 + Maven | mvn test |
| JavaScript | Node.js | npm test |
| TypeScript | Node.js | npm test |
| C# | .NET 8 | dotnet test |
| Ruby | Ruby + Bundler | bundle exec rspec |

## Docker Image Structure

The generated Docker images are fully compatible with the VeloraHarness evaluation system:

```
/app/repo           # Repository source code (primary location)
/testbed            # Symlink to /app/repo (legacy compatibility)
/saved/ENV          # Language toolchain and dependencies
/saved/venv/ENV     # Python virtual environment (Python only)
/workspace          # Evaluation workspace
/swe_util/          # Harness utilities directory
  └── eval_data/
      ├── instances/    # Instance data location
      └── testbeds/     # Testbed copies
/openhands/logs     # OpenHands log directory (chmod 777)
```

### Installed Tools

All images include these tools required by the harness entry scripts:
- `bash` - Shell
- `git` - Version control (configured for patch operations)
- `jq` - JSON parsing (required for instance_swe_entry.sh)
- `curl`, `wget` - Network utilities

## Generated Dockerfile Features

- **Self-contained**: All dependencies pre-installed
- **Offline execution**: No network required during tests
- **Git-ready**: Configured for patch operations
- **Reproducible**: Pinned versions, deterministic builds
- **UID 1000 reserved**: No user conflicts
- **Harness compatible**: Includes /testbed symlink, /swe_util, jq

## Instance Metadata Format

The generated `instance.json` is compatible with SWE-bench and VeloraHarness:

```json
{
  "instance_id": "1770139103324345",
  "repo": "owner/repo",
  "base_commit": "abc123...",
  "environment_setup_commit": "abc123...",
  "problem_statement": "PR title and description...",
  "hints_text": "",
  "FAIL_TO_PASS": "[\"test1\", \"test2\"]",
  "PASS_TO_PASS": "[\"test3\", \"test4\"]",
  "language": "java",
  "version": "1.0",
  "test_command": "./gradlew test",
  "test_output_parser": "java/parse_log_junit",
  "image_storage_uri": "docker_images/owner_repo-abc123.tar",
  "patch": "diff --git a/...",
  "test_patch": "diff --git a/..."
}
```

### FAIL_TO_PASS / PASS_TO_PASS

- **FAIL_TO_PASS**: Tests that the PR fixes (failed in BASE, pass in PR) + new tests
- **PASS_TO_PASS**: Tests relevant to the PR that pass in both BASE and PR
- Both fields are JSON-stringified arrays for harness compatibility

### Test Identifier Format

Tests are formatted to match what the evaluation harness log parsers expect:

| Language | Parser | Format | Example |
|----------|--------|--------|---------|
| Python | `parse_log_pytest_v3` | `path/to/file.py::Class::method` | `tests/test_api.py::TestAPI::test_get` |
| Java | `parse_log_junit` | `"package.Class.method"` (quoted) | `"com.example.TestClass.testMethod"` |
| Go | `parse_log_gotest_json` | `package/path/TestName` | `internal/pkg/TestFoo` |
| Rust | `parse_log_cargo_test` | `module::submodule::test_name` | `peer::media::test_cancel` |
| JavaScript | `parse_log_jest` | Test name string | `should handle errors` |

#### Python Example
```json
"FAIL_TO_PASS": "[\"src/test/test_dp_base.py::MyTestCase::test_roundtrip\", \"tests/test_basic_api.py::test_lexer_classes[AntlrPythonLexer]\"]"
```

#### Java Example
```json
"FAIL_TO_PASS": "[\"\\\"com.github.tomakehurst.wiremock.WireMockServerTests.testGetBaseUrl\\\"\", \"\\\"org.wiremock.url.BaseUrlTests.testParsing\\\"\"]"
```

#### Go Example
```json
"FAIL_TO_PASS": "[\"internal/controller/TestReconcile\", \"pkg/util/TestHelper\"]"
```

#### Rust Example
```json
"FAIL_TO_PASS": "[\"peer::media::transitable_state::test::cancel_transition\"]"
```

## Running with VeloraHarness

The generated Docker image is compatible with `instance_swe_entrypoint.sh`. To run:

### 1. Prepare Instance Data

Convert `instance.json` to the array format expected by the entry script:

```bash
# Create array from single instance
INSTANCE_FILE=/path/to/metadata/instance.json
jq -s '.' $INSTANCE_FILE > /tmp/swe-bench-instance.json
```

### 2. Run Container

```bash
docker run -it \
  -e SWE_INSTANCE_ID="<instance_id_from_json>" \
  -v /tmp/swe-bench-instance.json:/swe_util/eval_data/instances/swe-bench-instance.json:ro \
  pr-eval:pr-3311-base-abc123 \
  /bin/bash -c "source /path/to/instance_swe_entrypoint.sh"
```

### Entry Script Expectations

The `instance_swe_entrypoint.sh` expects:
- `SWE_INSTANCE_ID` environment variable
- Instance data at `/swe_util/eval_data/instances/swe-bench-instance.json`
- Fields: `instance_id`, `repo`, `version`
- `/testbed` directory (symlinked to `/app/repo`)

### WORKSPACE_NAME Format

The entry script creates: `WORKSPACE_NAME = repo__version` (slashes → underscores)

Example: `wiremock/wiremock` with version `efe4df93f792` → `wiremock__wiremock__efe4df93f792`

## Troubleshooting

### Language Detection Fails

```bash
# Override with explicit language
python -m automation_script.main_orchestrator \
  --language rust \
  <PR_URL> <OUTPUT_DIR>
```

### Docker Build Fails

1. Check logs: `<workspace>/logs/part1_build_and_base.log`
2. Check Docker error: `<workspace>/repo/docker_build_error.log`
3. Try with `--no-cache` (edit state.json and rerun)

### Shallow Clone Issues

```bash
# If merge-base calculation fails, use full clone
python -m automation_script.main_orchestrator \
  <PR_URL> <OUTPUT_DIR>  # without --shallow-clone
```

### Rerun Part 2 Only

```bash
python -m automation_script.main_orchestrator \
  --part2-only ./Output_dataset/owner-repo_pr_123
```

## Module Structure

```
automation_script/
├── main_orchestrator.py       # Entry point - coordinates Part 1 and Part 2
├── part1_build_and_base.py    # Part 1: Docker build and baseline testing
├── part2_patch_and_evaluate.py # Part 2: Patch application and evaluation
│
├── docker_builder_new.py      # Dockerfile generation engine
├── docker_runner.py           # Container test execution
├── docker_healing.py          # Self-healing for build failures
├── container_runner.py        # Advanced container operations
│
├── config.py                  # Configuration constants and timeouts
├── environment.py             # Environment detection (Python, Node, Rust, Java, etc.)
│
├── git_operations.py          # Git utilities (clone, fetch, patch, merge-base)
├── git_wrappers.py            # High-level Git command wrappers
├── github_api.py              # GitHub API client (PR metadata, commits)
│
├── language_detection.py      # Language/framework auto-detection
├── test_results.py            # Test output parsing and categorization
├── test_targeting.py          # Test file and command identification
│
├── metadata.py                # Metadata structure definitions (dataclasses)
├── metadata_generator.py      # SWE-bench compatible metadata creation
├── collect_29_fields.py       # Extended metadata collection (29 fields)
│
├── artifacts.py               # Artifact management (test outputs, images, logs)
├── organize_outputs.py        # Final output organization and file arrangement
├── repo_configs.py            # Repository-specific configurations and overrides
├── validate_fix.py            # Patch and metadata validation
│
├── utils.py                   # Common utility functions (command execution, etc.)
├── cleanup.py                 # Workspace cleanup utilities
├── cleanup_workspaces.py      # Bulk workspace cleanup
│
└── _archived/                 # Legacy/deprecated code
    ├── main.py
    ├── main_docker.py
    ├── docker_builder.py
    └── runner_legacy.py
```

## Requirements

- Python 3.10+
- Docker 20.10+
- Git 2.25+
- ~10GB disk space per PR (varies by project size)
