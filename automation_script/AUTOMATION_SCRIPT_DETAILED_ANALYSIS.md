# PR Evaluation Automation (`automation_script/`) — Detailed Analysis

This document explains **what the automation does** and **how it does it**, end-to-end, based on the implementation in `automation_script/`.

The system turns a GitHub Pull Request into **reproducible benchmark artifacts**:

- An **immutable Docker image** built at the PR’s **BASE commit**
- **BASE test results** (run inside that image)
- A **PR patch** (BASE → PR)
- **PATCHED test results** (BASE image + patch applied inside the container)
- Derived **test categories** (**FAIL_TO_PASS**, **PASS_TO_PASS**)
- A benchmark **`instance.json`** (SWE-bench/VeloraHarness compatible)
- Optional **29-field task template exports** (CSV/JSONL) for downstream pipelines

---

## High-level purpose

The goal is to evaluate a PR in a controlled and repeatable environment:

- **Build once** at BASE to create a stable execution environment and dependency snapshot.
- **Run tests twice**:
  - **BASE**: the repository as-of base commit.
  - **PATCHED**: apply PR patch inside the same base image and run the same tests.
- **Compare** results to determine what the PR fixed or changed.

Key design choices:

- **Reproducibility**: the Docker image is built from a generated Dockerfile and saved as a `.tar`.
- **Isolation**: tests run in ephemeral containers with resource limits.
- **No code edits per PR**: language/test command detection can be overridden via CLI flags.
- **Binary-safe patching**: patch generation uses `git diff --binary --full-index`.

---

## What you run (entrypoints)

### Main entrypoint: `main_orchestrator.py`

Run as a module:

```bash
python -m automation_script.main_orchestrator <PR_URL> <OUTPUT_DATASET_ROOT>
```

Execution modes:

- `--part1-only`: build image + run BASE tests + write `state.json`
- `--part2-only`: load `state.json`, patch + evaluate + generate metadata
- default: runs Part 1 then Part 2 sequentially

Override/utility flags (most important):

- `--language <lang>`: force language detection
- `--test-cmd "<cmd>"`: force test command
- `--rust-subdir <dir>`: convenience to build Rust `cargo test --manifest-path <dir>/Cargo.toml`
- `--base-commit <sha>`: manual BASE commit (useful for merged PRs)
- `--shallow-clone`: speed up cloning (with deepening to compute merge-base)
- `--reuse-image <tag>`: skip build and reuse existing local Docker image tag
- `--skip-tests`: skip BASE test execution (still produces state + artifacts placeholders)
- `--keep-repo`: don’t delete the cloned repo during cleanup
- `--cleanup-images`: delete the Docker image after completion

### Part implementations

- `part1_build_and_base.py`: clones repo, finds BASE, builds image, runs BASE tests, writes `state.json`
- `part2_patch_and_evaluate.py`: generates patch, checks patch applies, runs PATCHED tests, categorizes tests, writes metadata, validates, runs cleanup

### Container-side runner

- `container_runner.py`: **runs inside Docker containers** to apply patch (patched mode) and execute tests. Writes `result.json` + `results.jsonl` into mounted workspace.

---

## Workspace layout and naming

Part 1 creates a PR-specific workspace directory under the dataset root:

```
<OUTPUT_DATASET_ROOT>/
  <owner>-<repo>_pr_<number>/
    repo/                 # cloned repo (may be removed by cleanup)
    artifacts/
      base/               # BASE test outputs
      pr/                 # PATCHED test outputs
    docker_images/
      <owner_repo>-<base_commit>.tar
      <owner_repo>-<base_commit>.Dockerfile
    patches/
      pr.patch
    metadata/
      instance.json
      swe-bench-instance.json
    logs/
      part1_build_and_base.log
      part2_patch_and_evaluate.log
    state.json            # handoff from Part 1 → Part 2
    29_fields/            # per-workspace copy (instance_29fields.json) created in Part 2
```

Notes:

- `state.json` is the contract between Part 1 and Part 2.
- The Docker image is tagged like:
  - `pr-eval:pr-<PR_NUMBER>-base-<BASE_SHA12>` when PR number is known
  - otherwise `pr-eval:base-<BASE_SHA12>`

---

## Part 1: Build immutable BASE image + run BASE tests

Implemented by `run_part1()` in `part1_build_and_base.py`.

### Step 1: Parse PR URL

- Uses `git_operations.parse_pr_url()` to extract:
  - host, owner, repo, PR number
  - `clone_url` and API URL

Supports GitHub/GitLab/Bitbucket URL shapes, but most logic assumes GitHub PRs.

### Step 2: Clone the repository

Two clone modes:

- **Full clone (default)**: `git clone <clone_url> repo/`
- **Shallow clone (`--shallow-clone`)**:
  - clones with `--depth 1`, then deepens / fetches PR ref depths as needed
  - used to speed up large repositories, but merge-base/base detection may require deep history

### Step 3: Fetch PR refs and determine commits

Part 1 fetches PR refs and resolves the PR HEAD commit:

- `git_wrappers.fetch_pr_refs()` fetches `refs/pull/<N>/head` into `origin/pr/<N>`
- `git_wrappers.get_pr_head_commit()` resolves the PR commit SHA

Target branch detection:

- `git_operations.detect_target_branch()`:
  - checks `origin/main`, then `origin/master`, then `git remote show origin` (HEAD branch)

BASE commit determination:

- If `--base-commit <sha>` passed, it wins.
- Otherwise `git_operations.get_base_commit()` tries:
  1. **GitHub CLI**: `gh pr view <N> --json baseRefOid,...` (most accurate, works for merged PRs too)
  2. fallback: `git merge-base origin/<target_branch> <pr_sha>`
     - includes special handling if merge-base equals PR SHA (already merged): it walks parents and uses timestamp gap detection.

Validation:

- confirms base commit exists
- confirms base is an ancestor of PR head commit

### Step 4: Checkout BASE commit

`git_operations.checkout_commit()`:

- hard reset + clean (`git reset --hard`, `git clean -fdx`)
- checkout the BASE SHA

### Step 5: Detect language and test command

Detection inputs:

- It computes `changed_files` via:
  - `git diff --name-only <base_commit> <pr_commit>`
- It passes `repo_full_name = "<owner>/<repo>"` to detection to allow repo-specific overrides.

Language detection (`environment.detect_language()`):

- Highest priority: repo-specific config in `repo_configs.py`
- Next: infer from `changed_files` extensions (better for polyglot repos)
- Next: dependency file heuristics (`Cargo.toml`, `go.mod`, `pom.xml`, etc.)
- Fallback: limited extension counting (`rglob` with caps)

Test command detection (`environment.detect_test_command()`):

- Highest priority: repo-specific config (`repo_configs.py`)
- Then language-specific heuristics:
  - Python: pytest/tox config
  - JS/TS: package.json scripts; Grunt detection; pnpm detection
  - Rust: finds Cargo.toml (root or subdir) and uses `--manifest-path` when needed
  - Java: decides between Maven/Gradle; wrapper preference; uses skip flags for heavy static analysis in Maven default
  - C#: handles `dotnet/` subdir and `global.json` SDK constraints
  - PHP: composer + phpunit/pest detection; supports subdir layouts; uses `--testdox` for richer names
  - C: autoconf/build directory handling, plus ruby/ruby special casing
  - Go: can generate targeted package paths via `test_targeting.py` for large repos

CLI overrides always win:

- `--language` and `--test-cmd` take precedence over detection.
- `--rust-subdir` synthesizes a cargo command if language is rust and no explicit test cmd.

### Step 6: Generate Dockerfile + build Docker image (with retries/healing)

Dockerfile generation happens in `docker_builder_new.py`:

- Uses **Ubuntu 24.04** by default (`config.DOCKER_BASE_IMAGE = ubuntu:24.04`)
- Creates harness-compatible filesystem layout:
  - `/app/repo` (source)
  - `/testbed` symlink to `/app/repo` (legacy)
  - `/saved/ENV` and `/saved/venv/ENV` (toolchains + cached deps)
  - `/workspace` (mounted output workspace)
  - `/swe_util/eval_data/...` (expected instance/testbed locations)
  - `/openhands/logs` with permissive perms
- Installs required baseline tools like `git`, `jq`, `bash`, `curl`, `wget`.
- Clones the repo at build time using build args: `REPO_URL` and `BASE_COMMIT`.

Language-specific sections:

- Python: venv in `/saved/venv/ENV`, installs requirements/pyproject/setup with best-effort `|| true` fallbacks, ensures pytest tools exist.
- Rust: rustup installed under `/saved/ENV`, detects toolchain, prefetch/build caching; can switch to NixOS base for special cases.
- Go: downloads a Go toolchain based on `go.mod`, caches modules.
- JS/TS: detects node version (from `.nvmrc` or `package.json` engines), selects npm/yarn/pnpm, may install Chrome for browser tests.
- Java: detects required Java version from build files, caches Maven/Gradle deps.
- C#, Ruby, PHP, C/autoconf: installs toolchains and caches dependencies/builds.

Docker build (`docker_builder_new.build_docker_image()`):

- Runs `docker build -f Dockerfile.pr-eval -t <tag> --build-arg ... .`
- sets `DOCKER_BUILDKIT=1`
- writes `repo/docker_build_error.log` on failure

Retry/healing orchestration in Part 1:

- Docker build is wrapped by `build_docker_image_with_retry()`, which consults `docker_healing.py`:
  - classifies errors (missing system libs, rust nightly needed, network errors, etc.)
  - may patch the generated Dockerfile (e.g., add missing apt packages for pkg-config libraries)
  - can prune builder cache on later attempts

Important limitation to be aware of:

- Some build failures are explicitly treated as **not healable** (e.g., dependencies that require Nix libraries that aren’t apt-installable).

### Step 7: Save Docker image to `.tar` (and copy Dockerfile)

`docker_builder_new.save_and_compress_image()`:

- Runs `docker save -o <docker_images>/<safe_repo>-<base_commit>.tar <tag>`
- Copies `Dockerfile.pr-eval` beside it as `<safe_repo>-<base_commit>.Dockerfile`
- Returns an `image_uri` like `file:///abs/path/to/...tar`

### Step 8: Run BASE tests in a container (with retries/healing)

Container orchestration is in `docker_runner.py`:

- Runs `docker run --rm ... <image_tag> python3 -m automation_script.container_runner --mode base ...`
- Mounts:
  - workspace directory → `/workspace`
  - `automation_script/` itself → `/automation_script:ro`
- Sets `PYTHONPATH=/` so `automation_script` is importable inside container.
- Uses resource limits:
  - `--memory=<config.DOCKER_MEMORY_LIMIT>`
  - `--cpus=<config.DOCKER_CPU_LIMIT>`

Inside the container, `container_runner.py`:

- Builds the final test command (adds helpful flags, e.g., `pytest --json-report`, `go test -json`, Gradle `--info --continue`, Maven `-fae`, Ruby `-v` for Minitest).
- Executes the tests with an internal timeout (commonly 30 minutes).
- Parses stdout/stderr (and in some cases XML/TRX/testdox files) to extract lists:
  - `tests_passed`, `tests_failed`, `tests_skipped`
- Writes:
  - `/workspace/artifacts/base/result.json`
  - `/workspace/artifacts/base/results.jsonl`

Part 1 also has **test retry/healing** logic:

- `run_base_tests_with_retry()` wraps container execution and consults `docker_healing.py`:
  - detects environment error types (missing system libs, rust nightly, timeouts, network errors, etc.)
  - may update Dockerfile and trigger a **Docker image rebuild** before retrying tests
  - may increase timeouts on retry
  - tracks stability across attempts (to avoid “flaky/unstable” outcomes)

### Final Part 1 output: `state.json`

Part 1 writes `state.json` containing at least:

- PR and repo identifiers
- base/pr commit SHAs
- detected language + test command
- docker image tag + image_uri
- paths for repo/workspace/artifacts
- a summary of BASE results (counts)

Part 2 relies on this file.

---

## Part 2: Patch + run PATCHED tests + categorize + metadata

Implemented by `run_part2()` in `part2_patch_and_evaluate.py`.

### Step 1: Load `state.json`

`load_state()` validates required fields and provides the canonical inputs for Part 2:

- base_commit, pr_commit
- language, test_command
- docker_image tag, image_uri
- repo_path, workspace_path, base_artifacts_dir

### Step 2: Generate patch file (`patches/pr.patch`)

`generate_patch_file()` runs:

- `git diff --binary --full-index <base_commit> <pr_commit>`

Why these flags matter:

- `--binary`: embeds binary diffs (base85 blocks)
- `--full-index`: uses full 40-char blob hashes so `git apply` can validate/apply correctly

### Step 3: Verify patch applies in the container

`docker_runner.verify_patch_applies()` runs a dry-run apply inside the image:

- `docker run ... git apply --check /workspace/patches/pr.patch`

This catches patch drift and prevents wasting time running tests if the patch won’t apply.

### Step 4: Run PATCHED tests in the same BASE image

`docker_runner.run_patched_tests()` runs container_runner in patched mode:

- mounts workspace and automation_script
- runs `python3 -m automation_script.container_runner --mode patched --patch /workspace/patches/pr.patch ...`

In patched mode, `container_runner.py`:

- `git reset --hard` + `git clean -fd` (not `-fdx` to avoid deleting ignored caches like `vendor/` or `node_modules/`)
- `git apply --whitespace=fix <patch>`
- special-case: if `composer.json` exists, it runs `composer dump-autoload` best-effort
- runs tests and writes:
  - `/workspace/artifacts/pr/result.json`
  - `/workspace/artifacts/pr/results.jsonl`

### Step 5: Categorize tests: FAIL_TO_PASS and PASS_TO_PASS

`test_results.categorize_tests()` computes:

- **FAIL_TO_PASS**:
  - tests that were failing in BASE and pass after patch
  - plus new tests added by the PR (present in PR passing set but not in BASE sets)
- **PASS_TO_PASS**:
  - tests that passed in both BASE and PR
  - **filtered to PR-relevant tests** using patch analysis (strict heuristic to avoid including an entire suite)

Relevance filtering works by:

- parsing changed files from `diff --git a/... b/...` headers
- extracting “modules” from paths based on language
- for each passing-both test name, applying language-specific matching rules to decide if it relates to changed code

Important nuance:

- The code normalizes test names to strip timing suffixes (important for Jest/Pest/Mocha style output).
- For multi-module JVM builds where the same test can appear multiple times, some deduplication/priority logic exists in parsing.

### Step 6: Generate benchmark metadata (`metadata/instance.json`)

`metadata_generator.generate_metadata()` produces the final instance schema:

- `instance_id`: generated from timestamp + random suffix
- `repo`, `base_commit`, `language`
- `problem_statement`: fetched from GitHub:
  - if PR references issues via “fixes #123”, it prefers **issue descriptions** (bugfix framing)
  - otherwise uses PR title/body (feature framing)
- `FAIL_TO_PASS`, `PASS_TO_PASS`: stored as **JSON-stringified arrays** (string field containing JSON array)
- `test_command` and `test_output_parser`: parser chosen based on command/language
- `image_storage_uri`: converted from `file://...` to path relative to workspace when possible (e.g., `docker_images/<...>.tar`)
- `patch` and `test_patch`:
  - generated by separating changed files into “code files” and “test files”
  - code/test classification uses consistent heuristics to avoid overlap

It writes:

- `metadata/instance.json` (single object)
- `metadata/swe-bench-instance.json` (array-of-one, for harness scripts that expect `.[]`)

### Step 7: Export 29-field task templates (optional but integrated)

`collect_29_fields.integrate_29_fields_collection()`:

- loads `metadata/instance.json` and `state.json`
- optionally fetches GitHub repo + PR metadata via REST
- transforms into a 29-column schema (`TaskTemplate29Fields`)
- writes:
  - global: `<repo_root>/29_fields/task_instances_29fields.csv`
  - global: `<repo_root>/29_fields/task_instances_29fields.jsonl`
  - per-workspace: `<workspace>/29_fields/instance_29fields.json`

### Step 8: Validate outputs and cleanup

Validation (`metadata_generator.validate_artifacts()`):

- checks required `result.json`
- warns on optional files missing (`results.jsonl`, `result_summary.json`, junit xml/html)

Cleanup (`cleanup.cleanup_workspace()`):

- always removes `__pycache__`
- removes `repo/` unless `--keep-repo`
- optionally removes docker image if `--cleanup-images`

---

## Docker image contract (what the harness expects)

The Dockerfile generator is intentionally aligned with VeloraHarness/SWE-style entry scripts:

- Source code is in **`/app/repo`**
- Legacy symlink: **`/testbed -> /app/repo`**
- “Saved env” location: **`/saved/ENV`**
- Python venv location: **`/saved/venv/ENV`**
- Mounted output workspace: **`/workspace`**
- Harness directories exist: **`/swe_util/eval_data/instances`**, **`/swe_util/eval_data/testbeds`**
- Tooling includes **`jq`**, `git`, `bash`, plus language toolchains as needed

Test execution does **not** rely on host repo mounting. The repo is cloned into the image at build time; containers run tests directly against that baked-in repo state.

---

## Retry & “self-healing” model (what it tries to fix automatically)

The healing logic in `docker_healing.py` focuses on issues that are “environmental” rather than code bugs:

- **Missing system libraries**:
  - parses `pkg-config` / “system library was not found” messages
  - maps library/crate → apt packages
  - edits Dockerfile to add packages and triggers rebuild
- **Rust nightly / edition2024 / unstable features**:
  - detects edition/feature errors and can switch toolchain strategy
- **Network / apt / module download**:
  - recognizes transient network failures and retries
- **Timeouts**:
  - increases container test timeout on retry

Explicit non-goals / hard stops:

- Dependencies that require a non-apt package manager (e.g., Nix C libraries) are flagged as **not healable** in this Docker-based approach.

---

## Repo-specific behavior (`repo_configs.py`)

Some repos are known to need custom handling:

- Example: `denoland/deno`:
  - language forced to rust
  - targeted test command to avoid massive integration suite
  - extra packages for bindgen/libclang cases
- Example: `aws/aws-sdk-java-v2`:
  - Maven multi-module targeting derived from changed files (avoids building unrelated modules)

This is the right place to add special-case overrides without editing the main pipeline.

---

## Auxiliary / standalone utilities

These scripts exist but are not required for the standard workflow:

- `organize_outputs.py`: builds an alternative “organized dataset” folder structure + reports (includes markdown report generation).
- `cleanup_workspaces.py`: batch cleanup of old PR workspaces.
- `validate_fix.py`: a self-check script for a specific historical fix.
- `_archived/`: legacy versions of orchestrators/builders kept for reference.

---

## Practical troubleshooting map (where to look)

- Docker build failures:
  - `<workspace>/logs/part1_build_and_base.log`
  - `<workspace>/repo/docker_build_error.log`
  - look for healing steps applied in logs

- Patch application failures:
  - Part 2 log: `<workspace>/logs/part2_patch_and_evaluate.log`
  - `verify_patch_applies()` runs `git apply --check` inside the image

- “No tests ran” / empty test sets:
  - confirm `test_command` detection or override with `--test-cmd`
  - check container runner output in `artifacts/*/result.json` (`stdout`/`stderr`)

- Wrong language detected (polyglot repos):
  - use `--language`
  - or improve detection via `repo_configs.py`

- Merged PR base commit is wrong:
  - use `--base-commit <sha>`
  - or ensure `gh` is available and authenticated so baseRefOid can be fetched reliably

---

## Extension points (how to evolve this system)

- **Add a new language**:
  - update detection in `config.py` and `environment.py`
  - add a Dockerfile generator in `docker_builder_new.py`
  - add parsing and/or output parser mapping in `container_runner.py` and `metadata_generator.py`

- **Improve PASS_TO_PASS relevance filtering**:
  - tune `test_results.is_test_relevant_to_changes()` per language
  - keep it strict to avoid polluting PASS_TO_PASS with unrelated passing tests

- **Repo-specific overrides**:
  - add/adjust `RepoConfig` entries in `repo_configs.py`

