"""
GitHub API utilities for fetching PR information.
"""

import logging
import subprocess
import json
import urllib.request
import urllib.error
from typing import Optional, Dict, Any
from pathlib import Path


def fetch_pr_description_via_gh(
    repo: str,
    pr_number: int,
    logger: logging.Logger
) -> Optional[str]:
    """
    Fetch PR description using GitHub CLI (gh).

    Args:
        repo: Repository in format "owner/repo"
        pr_number: PR number
        logger: Logger instance

    Returns:
        PR description/body text, or None if failed
    """
    try:
        logger.info(f"Fetching PR #{pr_number} description from GitHub")

        # Use gh CLI to get PR details
        cmd = [
            "gh", "pr", "view", str(pr_number),
            "--repo", repo,
            "--json", "body"
        ]

        logger.debug(f"Running: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            logger.warning(f"gh CLI failed: {result.stderr}")
            logger.warning("Install gh CLI or set GITHUB_TOKEN for API access")
            return None

        data = json.loads(result.stdout)
        body = data.get("body", "")

        if body:
            logger.info(f"Fetched PR description ({len(body)} characters)")
            return body
        else:
            logger.warning("PR description is empty")
            return None

    except subprocess.TimeoutExpired:
        logger.error("GitHub API request timed out")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse GitHub API response: {e}")
        return None
    except FileNotFoundError:
        logger.warning("gh CLI not found - install with: brew install gh")
        return None
    except Exception as e:
        logger.error(f"Failed to fetch PR description: {e}")
        return None


def fetch_pr_metadata_via_api(
    repo: str,
    pr_number: int,
    logger: logging.Logger
) -> Optional[Dict[str, Any]]:
    """
    Fetch PR metadata using GitHub REST API (no authentication required for public repos).

    Args:
        repo: Repository in format "owner/repo"
        pr_number: PR number
        logger: Logger instance

    Returns:
        Dictionary with PR metadata, or None if failed
    """
    try:
        url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
        logger.info(f"Fetching PR #{pr_number} from GitHub API")
        logger.debug(f"URL: {url}")

        req = urllib.request.Request(url)
        req.add_header('Accept', 'application/vnd.github.v3+json')
        req.add_header('User-Agent', 'PR-Evaluation-Tool/1.0')

        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())

        # Extract relevant fields
        metadata = {
            "title": data.get("title", ""),
            "body": data.get("body", ""),
            "author": {"login": data.get("user", {}).get("login", "")},
            "createdAt": data.get("created_at", ""),
            "mergedAt": data.get("merged_at", ""),
            "baseRefName": data.get("base", {}).get("ref", ""),
            "headRefName": data.get("head", {}).get("ref", ""),
            "url": data.get("html_url", "")
        }

        logger.info(f"Fetched PR metadata via REST API: {metadata.get('title', 'N/A')}")
        return metadata

    except urllib.error.HTTPError as e:
        logger.error(f"GitHub API HTTP error: {e.code} {e.reason}")
        return None
    except urllib.error.URLError as e:
        logger.error(f"GitHub API URL error: {e.reason}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse GitHub API response: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to fetch PR metadata via API: {e}")
        return None


def fetch_pr_metadata(
    repo: str,
    pr_number: int,
    logger: logging.Logger
) -> Optional[Dict[str, Any]]:
    """
    Fetch comprehensive PR metadata.

    Tries GitHub CLI first, falls back to REST API.

    Args:
        repo: Repository in format "owner/repo"
        pr_number: PR number
        logger: Logger instance

    Returns:
        Dictionary with PR metadata, or None if failed
    """
    # Try gh CLI first
    try:
        logger.debug("Trying GitHub CLI (gh)...")

        cmd = [
            "gh", "pr", "view", str(pr_number),
            "--repo", repo,
            "--json", "title,body,author,createdAt,mergedAt,baseRefName,headRefName,url"
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)
            logger.info(f"Fetched PR metadata via gh CLI: {data.get('title', 'N/A')}")
            return data
        else:
            logger.debug(f"gh CLI failed: {result.stderr}")

    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError, Exception) as e:
        logger.debug(f"gh CLI not available: {e}")

    # Fall back to REST API
    logger.debug("Falling back to GitHub REST API...")
    return fetch_pr_metadata_via_api(repo, pr_number, logger)


def extract_linked_issue_numbers(pr_body: str) -> list:
    """
    Extract linked issue numbers from PR body.
    
    Looks for patterns like:
    - fixes #123
    - closes #123
    - resolves #123
    - fix #123
    - close #123
    - resolve #123
    - fixes #123, #456, #789 (comma-separated after keyword)
    - fixes #123 and #456
    - fixes #1, #2, and #3
    
    Args:
        pr_body: PR body text
        
    Returns:
        List of unique issue numbers found (preserves order)
    """
    import re
    
    if not pr_body:
        return []
    
    all_issues = []
    
    # Pattern 1: keyword followed by issue number(s) with various separators
    # Captures the entire list after the keyword including comma-and combinations
    # Matches: "fixes #123", "closes #123, #456", "fix #1, #2, and #3"
    keyword_pattern = r'\b(?:fix(?:es)?|close[sd]?|resolve[sd]?):?\s*(#\d+(?:\s*(?:[,&]|,?\s*and)\s*#\d+)*)'
    keyword_matches = re.findall(keyword_pattern, pr_body, re.IGNORECASE)
    
    for match in keyword_matches:
        # Extract all issue numbers from the matched group
        issue_numbers = re.findall(r'#(\d+)', match)
        all_issues.extend([int(n) for n in issue_numbers])
    
    # Pattern 2: Direct "fixes #N" pattern (simpler, catches stragglers)
    simple_pattern = r'\b(?:fix(?:es)?|close[sd]?|resolve[sd]?):?\s*#(\d+)'
    simple_matches = re.findall(simple_pattern, pr_body, re.IGNORECASE)
    all_issues.extend([int(m) for m in simple_matches])
    
    # Remove duplicates while preserving order
    seen = set()
    unique_issues = []
    for issue in all_issues:
        if issue not in seen:
            seen.add(issue)
            unique_issues.append(issue)
    
    return unique_issues


def fetch_issue_description(
    repo: str,
    issue_number: int,
    logger: logging.Logger
) -> Optional[str]:
    """
    Fetch issue description from GitHub API.
    
    Args:
        repo: Repository in format "owner/repo"
        issue_number: Issue number
        logger: Logger instance
        
    Returns:
        Raw issue text (prefer body-only exact copy) or None if failed
    """
    try:
        url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
        logger.info(f"Fetching issue #{issue_number} from GitHub API")
        
        req = urllib.request.Request(url)
        req.add_header('Accept', 'application/vnd.github.v3+json')
        req.add_header('User-Agent', 'PR-Evaluation-Tool/1.0')
        
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())
        
        title = data.get("title", "")
        body = data.get("body", "")
        
        if body:
            description = body
        elif title:
            description = title
        else:
            return None
        
        logger.info(f"Fetched issue #{issue_number}: {title[:50]}...")
        return description
        
    except urllib.error.HTTPError as e:
        logger.warning(f"GitHub API HTTP error for issue #{issue_number}: {e.code}")
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch issue #{issue_number}: {e}")
        return None


def is_bug_fix_pr(pr_title: str, pr_body: str, linked_issues: list) -> bool:
    """
    Determine if a PR is a bug fix based on title, body, and linked issues.
    
    A PR is considered a bug fix if:
    1. It has linked issues (fixes #, closes #, etc.)
    2. OR the title/body contains bug-related keywords
    
    Args:
        pr_title: PR title
        pr_body: PR body text
        linked_issues: List of linked issue numbers
        
    Returns:
        True if PR is a bug fix, False if it's a feature
    """
    # If there are linked issues, it's likely a bug fix
    if linked_issues:
        return True
    
    # Check for bug keywords in title
    if pr_title:
        title_lower = pr_title.lower()
        bug_keywords = ['fix', 'bug', 'issue', 'error', 'crash', 'broken', 'repair', 'patch']
        for keyword in bug_keywords:
            if keyword in title_lower:
                return True
    
    return False


def get_problem_statement(
    repo: str,
    pr_number: int,
    pr_url: str,
    logger: logging.Logger
) -> str:
    """
    Get problem statement for metadata.
    
    Logic:
    - For BUG FIX PRs (have linked issues): Use ALL linked ISSUE descriptions as raw text
    - For FEATURE PRs (no linked issues): Use PR body as raw text
    
    This ensures:
    - Bug fixes get the issue description(s) (what's broken from user perspective)
    - Features get the PR description (what should be implemented)

    Args:
        repo: Repository in format "owner/repo"
        pr_number: PR number
        pr_url: Full PR URL
        logger: Logger instance

    Returns:
        Problem statement string
    """
    # Try to fetch PR metadata
    pr_metadata = fetch_pr_metadata(repo, pr_number, logger)

    if not pr_metadata:
        logger.warning("Could not fetch PR metadata from GitHub")
        logger.warning("Using fallback problem statement")
        return f"PR #{pr_number} from {pr_url}"
    
    title = pr_metadata.get("title", "")
    body = pr_metadata.get("body", "")
    
    # Check for linked issues (bug fix indicators)
    linked_issues = extract_linked_issue_numbers(body)
    
    if linked_issues:
        logger.info(f"PR references issues: {linked_issues} - treating as BUG FIX")
        
        # Fetch ALL linked issue descriptions without adding synthetic formatting.
        issue_descriptions = []
        for issue_number in linked_issues:
            issue_description = fetch_issue_description(repo, issue_number, logger)
            if issue_description:
                issue_descriptions.append(issue_description)
                logger.info(f"Fetched issue #{issue_number} description")
            else:
                logger.warning(f"Could not fetch issue #{issue_number}")
        
        if issue_descriptions:
            # Keep text as close to source as possible; no markdown wrappers/separators.
            combined_description = "\n\n".join(issue_descriptions)
            logger.info(f"Using {len(issue_descriptions)} issue description(s) as problem statement")
            return combined_description
        else:
            logger.warning("Could not fetch any linked issues, falling back to PR description")
    else:
        logger.info("No linked issues found - treating as FEATURE PR")
    
    # Use PR body as primary source to preserve exact copy/paste text.
    if body:
        problem_statement = body
    elif title:
        problem_statement = title
    else:
        problem_statement = f"PR #{pr_number} from {pr_url}"

    logger.info(f"Problem statement: {len(problem_statement)} characters")
    return problem_statement
