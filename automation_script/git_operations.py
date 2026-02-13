"""
Git operations for the automation workflow.

Handles:
- PR URL parsing (GitHub, GitLab, Bitbucket)
- Repository cloning
- PR ref fetching
- Commit checkout
- Base commit detection via merge-base
- Patch generation
"""

import json
import logging
import shutil
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

from .config import PRInfo
from .utils import run_command


def parse_pr_url(pr_url: str, logger: logging.Logger) -> PRInfo:
    """
    Parse a PR URL and extract repository information.

    Supported formats:
    - GitHub:    https://github.com/owner/repo/pull/123
    - GitLab:    https://gitlab.com/owner/repo/-/merge_requests/123
    - Bitbucket: https://bitbucket.org/owner/repo/pull-requests/123

    Args:
        pr_url: Full URL to the pull request
        logger: Logger instance

    Returns:
        PRInfo with parsed details

    Raises:
        ValueError: If URL format is not recognized
    """
    logger.info(f"Parsing PR URL: {pr_url}")

    parsed = urlparse(pr_url)
    host = parsed.netloc.lower()
    path_parts = [p for p in parsed.path.split("/") if p]

    # GitHub
    if "github" in host:
        # Format: /owner/repo/pull/123
        if len(path_parts) < 4 or path_parts[2] != "pull":
            raise ValueError(f"Invalid GitHub PR URL: {pr_url}")

        owner, repo = path_parts[0], path_parts[1]
        pr_number = int(path_parts[3])
        clone_url = f"https://github.com/{owner}/{repo}.git"
        api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"

    # GitLab
    elif "gitlab" in host:
        # Format: /owner/repo/-/merge_requests/123
        if "merge_requests" not in path_parts:
            raise ValueError(f"Invalid GitLab MR URL: {pr_url}")

        mr_idx = path_parts.index("merge_requests")
        # Handle nested groups: owner can be "group/subgroup"
        owner = "/".join(path_parts[:mr_idx - 1]) if path_parts[mr_idx - 1] == "-" else "/".join(path_parts[:mr_idx])
        repo = path_parts[mr_idx - 2] if path_parts[mr_idx - 1] == "-" else path_parts[mr_idx - 1]
        pr_number = int(path_parts[mr_idx + 1])
        clone_url = f"https://gitlab.com/{owner}/{repo}.git"
        api_url = f"https://gitlab.com/api/v4/projects/{owner}%2F{repo}/merge_requests/{pr_number}"

    # Bitbucket
    elif "bitbucket" in host:
        # Format: /owner/repo/pull-requests/123
        if len(path_parts) < 4 or path_parts[2] != "pull-requests":
            raise ValueError(f"Invalid Bitbucket PR URL: {pr_url}")

        owner, repo = path_parts[0], path_parts[1]
        pr_number = int(path_parts[3])
        clone_url = f"https://bitbucket.org/{owner}/{repo}.git"
        api_url = f"https://api.bitbucket.org/2.0/repositories/{owner}/{repo}/pullrequests/{pr_number}"

    else:
        raise ValueError(f"Unsupported git platform: {host}")

    pr_info = PRInfo(
        host=host,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        clone_url=clone_url,
        api_url=api_url,
    )

    logger.info(f"Parsed: {owner}/{repo} PR#{pr_number}")
    return pr_info


def clone_repo(
    clone_url: str,
    dest_path: Path,
    logger: logging.Logger
) -> bool:
    """
    Clone a repository to the specified path.

    Clones with full history (not shallow) to enable merge-base calculations.

    Args:
        clone_url: Git clone URL
        dest_path: Destination directory
        logger: Logger instance

    Returns:
        True if successful, False otherwise
    """
    logger.info(f"Cloning repository: {clone_url}")
    logger.info(f"Destination: {dest_path}")

    # Remove existing directory if present
    if dest_path.exists():
        logger.warning(f"Removing existing directory: {dest_path}")
        shutil.rmtree(dest_path)

    # Clone with full history
    cmd = ["git", "clone", clone_url, str(dest_path)]
    exit_code, stdout, stderr = run_command(cmd, logger=logger, timeout=600)

    if exit_code != 0:
        logger.error(f"Clone failed: {stderr}")
        return False

    logger.info("Repository cloned successfully")
    return True


def fetch_pr_refs(
    repo_path: Path,
    pr_info: PRInfo,
    logger: logging.Logger
) -> Optional[str]:
    """
    Fetch PR refs and return the PR head commit SHA.

    Each platform uses different ref names:
    - GitHub:    refs/pull/{pr}/head
    - GitLab:    refs/merge-requests/{mr}/head
    - Bitbucket: refs/pull-requests/{pr}/from

    Args:
        repo_path: Path to cloned repository
        pr_info: Parsed PR information
        logger: Logger instance

    Returns:
        PR head commit SHA, or None if failed
    """
    logger.info(f"Fetching PR #{pr_info.pr_number} refs")

    # Determine refspec based on platform
    if "github" in pr_info.host:
        refspec = f"+refs/pull/{pr_info.pr_number}/head:refs/remotes/origin/pr/{pr_info.pr_number}"
        ref_name = f"origin/pr/{pr_info.pr_number}"

    elif "gitlab" in pr_info.host:
        refspec = f"+refs/merge-requests/{pr_info.pr_number}/head:refs/remotes/origin/mr/{pr_info.pr_number}"
        ref_name = f"origin/mr/{pr_info.pr_number}"

    else:  # Bitbucket
        refspec = f"+refs/pull-requests/{pr_info.pr_number}/from:refs/remotes/origin/pr/{pr_info.pr_number}"
        ref_name = f"origin/pr/{pr_info.pr_number}"

    # Fetch the PR ref
    exit_code, _, stderr = run_command(
        ["git", "fetch", "origin", refspec],
        cwd=repo_path,
        logger=logger
    )

    if exit_code != 0:
        logger.error(f"Failed to fetch PR refs: {stderr}")
        return None

    # Get the commit SHA
    exit_code, stdout, stderr = run_command(
        ["git", "rev-parse", ref_name],
        cwd=repo_path,
        logger=logger
    )

    if exit_code != 0:
        logger.error(f"Failed to get PR commit SHA: {stderr}")
        return None

    pr_sha = stdout.strip()
    logger.info(f"PR head commit: {pr_sha}")
    return pr_sha


def detect_target_branch(
    repo_path: Path,
    logger: logging.Logger
) -> str:
    """
    Detect the target/default branch of the repository.

    Checks for 'main' first, then 'master', then queries remote.

    Args:
        repo_path: Path to cloned repository
        logger: Logger instance

    Returns:
        Branch name (defaults to 'main' if detection fails)
    """
    logger.info("Detecting target branch")

    # Check for 'main'
    exit_code, _, _ = run_command(
        ["git", "rev-parse", "--verify", "origin/main"],
        cwd=repo_path,
        logger=logger
    )
    if exit_code == 0:
        logger.info("Target branch: main")
        return "main"

    # Check for 'master'
    exit_code, _, _ = run_command(
        ["git", "rev-parse", "--verify", "origin/master"],
        cwd=repo_path,
        logger=logger
    )
    if exit_code == 0:
        logger.info("Target branch: master")
        return "master"

    # Query remote for default branch
    exit_code, stdout, _ = run_command(
        ["git", "remote", "show", "origin"],
        cwd=repo_path,
        logger=logger
    )
    if exit_code == 0:
        for line in stdout.splitlines():
            if "HEAD branch:" in line:
                branch = line.split(":")[-1].strip()
                logger.info(f"Target branch (from remote): {branch}")
                return branch

    logger.warning("Could not detect target branch, defaulting to 'main'")
    return "main"


def _is_gh_cli_available() -> bool:
    """
    Check if GitHub CLI (gh) is installed and available in PATH.

    Returns:
        True if gh command is available, False otherwise
    """
    return shutil.which("gh") is not None


def get_base_commit(
    repo_path: Path,
    target_branch: str,
    pr_sha: str,
    pr_info: PRInfo,
    logger: logging.Logger
) -> Optional[str]:
    """
    Compute the true base commit using GitHub CLI (preferred) or git merge-base (fallback).

    Strategy:
    1. For GitHub PRs: Try to get base commit from GitHub API using gh CLI
       - Uses `gh pr view` command to get the accurate baseRefOid
       - This works for both open and merged PRs
    2. If gh CLI fails or not available: Fall back to git merge-base
    3. For GitLab/Bitbucket PRs: Use git merge-base directly

    Args:
        repo_path: Path to cloned repository
        target_branch: Name of target branch (e.g., 'main')
        pr_sha: PR head commit SHA
        pr_info: PRInfo object with owner, repo, pr_number (required for GitHub API)
        logger: Logger instance

    Returns:
        Base commit SHA, or None if failed
    """
    logger.info(f"Computing base commit for PR {pr_sha[:12]}")

    base_sha = None

    # PRIMARY METHOD: Try GitHub CLI for GitHub PRs
    if "github" in pr_info.host and _is_gh_cli_available():
        logger.info("Attempting to get base commit using GitHub CLI (gh command)...")
        base_sha = _get_base_commit_from_github_api(
            pr_info.owner,
            pr_info.repo,
            pr_info.pr_number,
            repo_path,
            logger
        )
        
        if base_sha:
            logger.info(f"✓ Successfully obtained base commit from GitHub API: {base_sha[:12]}")
        else:
            logger.warning("GitHub CLI method failed, falling back to git merge-base")
    else:
        # Log why we're not using GitHub CLI
        if "github" not in pr_info.host:
            logger.info(f"Not a GitHub PR (host: {pr_info.host}), using git merge-base method")
        else:
            logger.warning("GitHub CLI (gh) not available, using git merge-base method")

    # FALLBACK METHOD: Use git merge-base if GitHub CLI didn't work
    if not base_sha:
        logger.info("Using git merge-base method to compute base commit")
        base_sha = _get_base_commit_merge_base(
            repo_path,
            target_branch,
            pr_sha,
            logger
        )

    if not base_sha:
        logger.error("Failed to compute base commit using all available methods")
        return None

    logger.info(f"Base commit computed: {base_sha[:12]}")

    # VALIDATION: Ensure base commit exists in repository
    exit_code, _, _ = run_command(
        ["git", "cat-file", "-e", base_sha],
        cwd=repo_path,
        logger=logger
    )
    if exit_code != 0:
        logger.error(f"Validation failed: Base commit does not exist in repository: {base_sha}")
        return None

    logger.info(f"✓ Base commit exists in repository")

    # VALIDATION: Ensure base is an ancestor of PR commit
    exit_code, _, _ = run_command(
        ["git", "merge-base", "--is-ancestor", base_sha, pr_sha],
        cwd=repo_path,
        logger=logger
    )
    
    if exit_code != 0:
        # Get more debugging info
        exit_code_mb, merge_base_result, _ = run_command(
            ["git", "merge-base", base_sha, pr_sha],
            cwd=repo_path,
            logger=logger
        )
        
        logger.error("Validation failed: Base commit is not an ancestor of PR commit")
        logger.error(f"  Base SHA: {base_sha}")
        logger.error(f"  PR SHA:   {pr_sha}")
        if exit_code_mb == 0:
            logger.error(f"  Actual merge-base: {merge_base_result.strip()}")
        return None

    logger.info(f"✓ Base commit validated: {base_sha[:12]}")
    logger.info("✓ Confirmed: base is ancestor of PR")
    return base_sha


def _get_base_commit_from_github_api(
    owner: str,
    repo: str,
    pr_number: int,
    repo_path: Path,
    logger: logging.Logger
) -> Optional[str]:
    """
    Get base commit from GitHub API using gh CLI.

    Uses the `gh pr view` command to query the PR and extract the baseRefOid,
    which represents the true base commit that the PR was created from.
    This method works for both open and merged PRs.

    Args:
        owner: Repository owner
        repo: Repository name
        pr_number: PR number
        repo_path: Path to cloned repository (for running commands)
        logger: Logger instance

    Returns:
        Base commit SHA (baseRefOid) from GitHub API, or None if failed
    """
    logger.info(f"Querying GitHub API for PR #{pr_number} base commit")
    logger.info(f"Repository: {owner}/{repo}")

    # Execute gh CLI command to get PR information
    # Request baseRefOid (the actual base commit SHA), baseRefName (branch name),
    # and headRefOid (PR commit SHA) for verification
    exit_code, stdout, stderr = run_command(
        ["gh", "pr", "view", str(pr_number), 
         "--repo", f"{owner}/{repo}", 
         "--json", "baseRefOid,baseRefName,headRefOid"],
        cwd=repo_path,
        logger=logger
    )

    # Handle command execution errors
    if exit_code != 0:
        logger.warning(f"gh CLI command failed with exit code {exit_code}")
        if "gh: command not found" in stderr or "not found" in stderr.lower():
            logger.warning("gh CLI is not installed or not in PATH")
        elif "authentication" in stderr.lower() or "auth" in stderr.lower():
            logger.warning("gh CLI authentication required - run 'gh auth login'")
        elif "not found" in stderr.lower() or "could not resolve" in stderr.lower():
            logger.warning(f"PR #{pr_number} not found in {owner}/{repo}")
        else:
            logger.warning(f"gh CLI error: {stderr[:200]}")
        return None

    # Parse JSON response from gh CLI
    try:
        data = json.loads(stdout.strip())
        base_ref_oid = data.get("baseRefOid")
        base_ref_name = data.get("baseRefName")
        head_ref_oid = data.get("headRefOid")
        
        if not base_ref_oid:
            logger.warning("GitHub API response missing 'baseRefOid' field")
            return None
        
        # Log success with details
        logger.info(f"✓ GitHub API response:")
        logger.info(f"  Base branch: {base_ref_name}")
        logger.info(f"  Base commit (baseRefOid): {base_ref_oid[:12]}")
        logger.info(f"  Head commit (headRefOid): {head_ref_oid[:12]}")
        
        return base_ref_oid
        
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse GitHub API JSON response: {e}")
        logger.warning(f"Response was: {stdout[:200]}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error parsing GitHub API response: {e}")
        return None


def _get_base_commit_merge_base(
    repo_path: Path,
    target_branch: str,
    pr_sha: str,
    logger: logging.Logger
) -> Optional[str]:
    """
    Compute base commit using git merge-base (fallback method).

    For merged PRs: If pr_sha is a merge commit, use its first parent as BASE.
    For open PRs: Use merge-base to find common ancestor.
    For already-merged PRs: Use timestamp gap detection.

    Args:
        repo_path: Path to cloned repository
        target_branch: Name of target branch (e.g., 'main')
        pr_sha: PR head commit SHA
        logger: Logger instance

    Returns:
        Base commit SHA, or None if failed
    """
    # Ensure target branch is fetched
    run_command(
        ["git", "fetch", "origin", target_branch],
        cwd=repo_path,
        logger=logger
    )

    # Check if pr_sha is a merge commit
    exit_code, stdout, _ = run_command(
        ["git", "rev-list", "--parents", "-n", "1", pr_sha],
        cwd=repo_path,
        logger=logger
    )

    if exit_code == 0:
        parts = stdout.strip().split()
        if len(parts) > 2:  # Merge commit has 2+ parents
            # This is a merge commit - use first parent as BASE
            base_sha = parts[1]
            logger.info(f"Detected merge commit - using first parent as BASE")
            logger.info(f"Merge commit: {pr_sha[:12]}")
            logger.info(f"Base commit (first parent): {base_sha[:12]}")
            return base_sha

    # Not a merge commit - use merge-base
    logger.info(f"Using merge-base: origin/{target_branch} {pr_sha[:12]}")
    exit_code, stdout, stderr = run_command(
        ["git", "merge-base", f"origin/{target_branch}", pr_sha],
        cwd=repo_path,
        logger=logger
    )

    if exit_code != 0:
        logger.error(f"Failed to compute merge-base: {stderr}")
        return None

    base_sha = stdout.strip()

    # CRITICAL FIX: If merge-base returns the PR commit itself, the PR is already merged
    # Use commit timestamp analysis to find where PR diverged from base
    if base_sha == pr_sha:
        logger.warning(f"PR {pr_sha[:12]} is already merged into {target_branch}")
        logger.info("Finding original base using timestamp gap detection...")
        
        # Strategy: Walk backwards from pr_sha checking committer timestamps
        # PR commits are typically committed around the same time (during PR creation/rebase)
        # Find a gap in committer timestamps - that indicates the boundary
        
        current = pr_sha
        max_iterations = 50
        TIME_GAP_THRESHOLD = 86400  # 1 day in seconds
        
        for i in range(max_iterations):
            # Get current commit's committer timestamp
            exit_code, time_stdout, _ = run_command(
                ["git", "log", "-1", "--format=%ct", current],
                cwd=repo_path,
                logger=logger
            )
            
            if exit_code != 0:
                logger.warning(f"Failed to get timestamp for {current[:12]}")
                break
            
            current_time = int(time_stdout.strip())
            
            # Get parent commit
            exit_code, parent_stdout, _ = run_command(
                ["git", "rev-parse", f"{current}^"],
                cwd=repo_path,
                logger=logger
            )
            
            if exit_code != 0:
                logger.warning(f"No parent found for {current[:12]}")
                break
            
            parent = parent_stdout.strip()
            
            # Get parent's committer timestamp
            exit_code, parent_time_stdout, _ = run_command(
                ["git", "log", "-1", "--format=%ct", parent],
                cwd=repo_path,
                logger=logger
            )
            
            if exit_code != 0:
                logger.warning(f"Failed to get timestamp for parent {parent[:12]}")
                break
            
            parent_time = int(parent_time_stdout.strip())
            time_gap = abs(current_time - parent_time)
            
            # If there's a significant time gap between commits, parent is likely the base
            if time_gap > TIME_GAP_THRESHOLD:
                base_sha = parent
                logger.info(f"Found BASE at iteration {i}: {base_sha[:12]}")
                logger.info(f"  Time gap: {time_gap / 3600:.1f} hours between commits")
                logger.info(f"  Current commit: {current[:12]} at {current_time}")
                logger.info(f"  Base commit: {parent[:12]} at {parent_time}")
                break
            
            # Move to parent for next iteration
            current = parent
        else:
            # Fallback: use last checked commit as base
            logger.warning(f"No significant time gap found in {max_iterations} commits")
            logger.warning(f"Using commit at iteration {max_iterations-1} as BASE: {current[:12]}")
            base_sha = current

    logger.info(f"merge-base computed: {base_sha[:12]}")
    return base_sha


def checkout_commit(
    repo_path: Path,
    commit: str,
    logger: logging.Logger,
    clean: bool = True
) -> bool:
    """
    Checkout a specific commit.

    Optionally cleans the working directory first to ensure
    a pristine state.

    Args:
        repo_path: Path to cloned repository
        commit: Commit SHA or ref to checkout
        logger: Logger instance
        clean: Whether to clean working directory first

    Returns:
        True if successful, False otherwise
    """
    logger.info(f"Checking out commit: {commit[:12] if len(commit) > 12 else commit}")

    if clean:
        # Reset any staged changes
        run_command(["git", "reset", "--hard"], cwd=repo_path, logger=logger)
        # Remove untracked files and directories
        run_command(["git", "clean", "-fdx"], cwd=repo_path, logger=logger)

    # Checkout the commit
    exit_code, _, stderr = run_command(
        ["git", "checkout", commit],
        cwd=repo_path,
        logger=logger
    )

    if exit_code != 0:
        logger.error(f"Checkout failed: {stderr}")
        return False

    # Verify checkout
    exit_code, stdout, _ = run_command(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        logger=logger
    )

    current = stdout.strip()
    if not commit.startswith(current[:len(commit)]) and not current.startswith(commit[:len(current)]):
        logger.warning(f"Checkout verification: expected {commit[:12]}, got {current[:12]}")

    logger.info("Checkout successful")
    return True


def generate_patch_file(
    repo_path: Path,
    base_commit: str,
    pr_commit: str,
    output_path: Path,
    logger: logging.Logger
) -> bool:
    """
    Generate and save patch file from git diff.

    Uses format: git diff base_commit..pr_commit

    Args:
        repo_path: Path to cloned repository
        base_commit: Base commit SHA
        pr_commit: PR commit SHA
        output_path: Where to save the patch file
        logger: Logger instance

    Returns:
        True if successful
    """
    logger.info(f"Generating patch: {base_commit[:12]}..{pr_commit[:12]}")

    # Generate diff with --binary and --full-index for binary patch support
    # --binary: includes binary file content as base85-encoded data
    # --full-index: ensures full 40-char SHA hashes are used, required for 'git apply' on binary files
    exit_code, patch_content, stderr = run_command(
        ["git", "diff", "--binary", "--full-index", f"{base_commit}..{pr_commit}"],
        cwd=repo_path,
        logger=logger
    )

    if exit_code != 0:
        logger.error(f"Failed to generate patch: {stderr}")
        return False

    if not patch_content.strip():
        logger.warning("Patch is empty - no changes between commits")
        patch_content = ""

    # Save to file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(patch_content)

    logger.info(f"Patch saved: {len(patch_content)} bytes → {output_path}")
    return True


def is_test_file(filepath: str) -> bool:
    """
    Determine if a file is a test file based on its path.
    
    Uses consistent logic for classifying files to ensure proper separation
    between code patches and test patches.
    
    Args:
        filepath: File path relative to repository root
        
    Returns:
        True if the file is a test file, False otherwise
    """
    import re
    
    filepath_lower = filepath.lower()
    
    # Directory-based patterns (most reliable)
    dir_patterns = [
        r'^tests?/',           # Starts with test/ or tests/
        r'/tests?/',           # Contains /test/ or /tests/
        r'^spec/',             # Starts with spec/
        r'/spec/',             # Contains /spec/
        r'testdata/',          # Test data directories
        r'test_data/',         # Test data directories
        r'testing/',           # Testing directories
        r'fixtures/',          # Test fixtures
        r'__tests__/',         # Jest-style test directories
        r'__mocks__/',         # Jest-style mock directories
        r'/mocks?/',           # Mock directories
    ]
    
    # Filename-based patterns (language-specific)
    file_patterns = [
        r'_test\.[^/]+$',      # Go: *_test.go, Python: *_test.py
        r'_spec\.[^/]+$',      # Ruby/JS: *_spec.rb, *_spec.js
        r'\.test\.[^/]+$',     # JS/TS: *.test.js, *.test.ts
        r'\.spec\.[^/]+$',     # JS/TS: *.spec.js, *.spec.ts
        r'test_[^/]+\.[^/]+$', # Python: test_*.py
        r'Tests?\.java$',      # Java: *Test.java, *Tests.java
        r'IT\.java$',          # Java integration tests: *IT.java
        r'[^/]*Test\.java$',   # Java: SomeClassTest.java
        r'[^/]*Tests\.java$',  # Java: SomeClassTests.java
        r'[^/]*Test\.kt$',     # Kotlin: *Test.kt
        r'[^/]*Test\.scala$',  # Scala: *Test.scala
        r'[^/]*Spec\.scala$',  # Scala: *Spec.scala
    ]
    
    # Check directory patterns
    for pattern in dir_patterns:
        if re.search(pattern, filepath_lower):
            return True
    
    # Check filename patterns
    for pattern in file_patterns:
        if re.search(pattern, filepath, re.IGNORECASE):
            return True
    
    return False


def get_patches(
    repo_path: Path,
    base_commit: str,
    pr_commit: str,
    logger: logging.Logger
) -> Tuple[str, str]:
    """
    Generate code_patch and test_patch from git diff (for metadata).
    
    Uses consistent file classification to ensure:
    - patch: Contains ONLY code changes (no test files)
    - test_patch: Contains ONLY test file changes
    - No overlap between the two patches
    - All changed files are in exactly one patch

    Args:
        repo_path: Path to cloned repository
        base_commit: Base commit SHA
        pr_commit: PR commit SHA
        logger: Logger instance

    Returns:
        Tuple of (code_patch, test_patch) - properly separated patches
    """
    logger.info("Generating separated patches for metadata")

    # Step 1: Get list of all changed files
    exit_code, stdout, _ = run_command(
        ["git", "diff", "--name-only", f"{base_commit}..{pr_commit}"],
        cwd=repo_path,
        logger=logger
    )
    
    if exit_code != 0:
        logger.warning("Failed to get list of changed files")
        return "", ""
    
    all_files = [f.strip() for f in stdout.strip().split('\n') if f.strip()]
    
    if not all_files:
        logger.info("No changed files found")
        return "", ""
    
    # Step 2: Classify files using consistent logic
    code_files = []
    test_files = []
    
    for filepath in all_files:
        if is_test_file(filepath):
            test_files.append(filepath)
        else:
            code_files.append(filepath)
    
    logger.info(f"Classified {len(all_files)} files: {len(code_files)} code, {len(test_files)} test")
    
    # Step 3: Generate code-only patch
    code_patch = ""
    if code_files:
        cmd = ["git", "diff", "--binary", "--full-index", f"{base_commit}..{pr_commit}", "--"]
        cmd.extend(code_files)
        exit_code, code_patch, _ = run_command(cmd, cwd=repo_path, logger=logger)
        if exit_code != 0:
            code_patch = ""
    
    # Step 4: Generate test-only patch
    test_patch = ""
    if test_files:
        cmd = ["git", "diff", "--binary", "--full-index", f"{base_commit}..{pr_commit}", "--"]
        cmd.extend(test_files)
        exit_code, test_patch, _ = run_command(cmd, cwd=repo_path, logger=logger)
        if exit_code != 0:
            test_patch = ""

    logger.info(f"Generated patches: code={len(code_patch)} bytes, test={len(test_patch)} bytes")
    return code_patch, test_patch

