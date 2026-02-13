#!/usr/bin/env python3
"""
29-Field Collection Module

This module collects and transforms instance metadata to the full 29-field format.
It integrates with the automation workflow to generate comprehensive task templates.

Field Categories:
1. Direct Copy (8 fields): instance_id, repo, base_commit, problem_statement, 
   FAIL_TO_PASS, PASS_TO_PASS, language, test_patch
2. Simple Rename (3 fields): hints_text→hints, image_storage_uri→docker_image_url, 
   patch→functional_patch
3. Derive/Transform (6 fields): repo_path_or_url, run_script, parsing_script, 
   version, selected_test_files_to_run
4. Generate Per-Language (3 fields): docker_file, entrypoint_script, before_repo_set_cmd
5. Configuration Defaults (3 fields): container_mem, container_memswap, 
   container_network_needed
6. Classification (2 fields): task_category, repo_category
+ Additional metadata (4 fields): problem_statement_variants, artifacts, status, 
   owner, notes
"""

import csv
import json
import logging
import os
import re
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Any, List, Optional

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class TaskTemplate29Fields:
    """Complete 29-field task template structure."""
    
    # Category 1: Core Identification (4 columns)
    instance_id: str = ""
    repo: str = ""
    repo_path_or_url: str = ""
    version: str = ""
    
    # Category 2: Problem Description (3 columns)
    problem_statement: str = ""
    hints: str = ""
    problem_statement_variants: str = ""
    
    # Category 3: Test Configuration (5 columns)
    FAIL_TO_PASS: str = ""
    PASS_TO_PASS: str = ""
    test_patch: str = ""
    selected_test_files_to_run: str = ""
    parsing_script: str = ""
    
    # Category 4: Execution Configuration (6 columns)
    language: str = ""
    run_script: str = ""
    entrypoint_script: str = ""
    before_repo_set_cmd: str = ""
    base_commit: str = ""
    functional_patch: str = ""
    
    # Category 5: Docker Configuration (5 columns)
    docker_image_url: str = ""
    docker_file: str = ""
    container_mem: str = "4g"
    container_memswap: str = "4g"
    container_network_needed: str = "false"
    
    # Category 6: Classification & Metadata (6 columns)
    task_category: str = ""
    repo_category: str = ""
    artifacts: str = "[]"
    status: str = "complete"
    owner: str = ""
    notes: str = ""


# Path to dockerfiles directory
DOCKERFILES_DIR = Path(__file__).parent.parent / "task_template_generator" / "dockerfiles"


# Language-specific Dockerfile templates (fallback if no pre-built one exists)
DOCKERFILE_TEMPLATES = {
    "rust": """# Rust project Docker image
FROM rust:1.83-slim-bookworm

WORKDIR /repo

RUN apt-get update && apt-get install -y --no-install-recommends \\
    git python3 python3-pip pkg-config libssl-dev \\
    cmake build-essential protobuf-compiler libprotobuf-dev curl \\
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir --break-system-packages uv pipx pytest 2>/dev/null || true

COPY . /repo/

RUN cargo build --release 2>/dev/null || cargo build 2>/dev/null || true

WORKDIR /repo

CMD ["cargo", "test"]
""",
    
    "python": """# Python project Docker image
FROM python:3.10-slim

WORKDIR /repo

RUN apt-get update && apt-get install -y --no-install-recommends \\
    gcc g++ make git libffi-dev libssl-dev \\
    && rm -rf /var/lib/apt/lists/*

COPY . /repo/

RUN pip install --no-cache-dir -e . 2>/dev/null || \\
    pip install --no-cache-dir -r requirements.txt 2>/dev/null || true

RUN pip install --no-cache-dir pytest pytest-json-report pytest-html || true

WORKDIR /repo

CMD ["pytest", "-v"]
""",
    
    "go": """# Go project Docker image
FROM golang:1.21-alpine AS builder

WORKDIR /build
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN go build -v ./... || true

FROM golang:1.21-alpine
WORKDIR /repo
RUN apk add --no-cache git python3 py3-pip bash
COPY --from=builder /go/pkg /go/pkg
COPY . /repo/
WORKDIR /repo

CMD ["go", "test", "./..."]
""",
    
    "java": """# Java/Maven project Docker image
FROM maven:3.9-eclipse-temurin-17

WORKDIR /repo

RUN apt-get update && apt-get install -y --no-install-recommends \\
    git python3 python3-pip \\
    && rm -rf /var/lib/apt/lists/*

COPY pom.xml ./
RUN mvn dependency:go-offline -B || true

COPY . /repo/
RUN mvn clean install -DskipTests \\
    -Dmaven.javadoc.skip=true \\
    -Dcheckstyle.skip=true \\
    -Dspotbugs.skip=true \\
    -Dpmd.skip=true \\
    -B || true

WORKDIR /repo
CMD ["mvn", "test"]
""",
    
    "kotlin": """# Kotlin/Gradle project Docker image
FROM gradle:8-jdk17

WORKDIR /repo

RUN apt-get update && apt-get install -y --no-install-recommends \\
    git python3 python3-pip \\
    && rm -rf /var/lib/apt/lists/*

COPY build.gradle* settings.gradle* gradlew* gradle/ ./
RUN chmod +x ./gradlew || true
RUN ./gradlew build -x test || true

COPY . /repo/
WORKDIR /repo

CMD ["./gradlew", "test", "--no-daemon"]
""",
    
    "javascript": """# Node.js project Docker image
FROM node:18-bullseye-slim

WORKDIR /repo

RUN apt-get update && apt-get install -y --no-install-recommends \\
    git python3 python3-pip build-essential \\
    && rm -rf /var/lib/apt/lists/*

COPY package*.json yarn.lock* pnpm-lock.yaml* ./
RUN npm install || yarn install || pnpm install || true

COPY . /repo/
ENV PATH=/repo/node_modules/.bin:$PATH
WORKDIR /repo

CMD ["npm", "test"]
""",
    
    "typescript": """# TypeScript project Docker image
FROM node:18-bullseye-slim

WORKDIR /repo

RUN apt-get update && apt-get install -y --no-install-recommends \\
    git python3 python3-pip build-essential \\
    && rm -rf /var/lib/apt/lists/*

COPY package*.json yarn.lock* pnpm-lock.yaml* tsconfig*.json ./
RUN npm install || yarn install || pnpm install || true

COPY . /repo/
ENV PATH=/repo/node_modules/.bin:$PATH
WORKDIR /repo

CMD ["npm", "test"]
""",
    
    "csharp": """# .NET project Docker image
FROM mcr.microsoft.com/dotnet/sdk:9.0

WORKDIR /repo

RUN apt-get update && apt-get install -y --no-install-recommends \\
    git python3 python3-pip \\
    && rm -rf /var/lib/apt/lists/*

COPY *.sln *.csproj ./
RUN dotnet restore || true

COPY . /repo/
WORKDIR /repo

CMD ["dotnet", "test", "--verbosity", "normal"]
""",
    
    "ruby": """# Ruby project Docker image
FROM ruby:3.2-slim

WORKDIR /repo

RUN apt-get update && apt-get install -y --no-install-recommends \\
    git python3 python3-pip build-essential \\
    && rm -rf /var/lib/apt/lists/*

COPY Gemfile Gemfile.lock* ./
RUN bundle install --jobs=4 --retry=3 || true

COPY . /repo/
WORKDIR /repo

CMD ["bundle", "exec", "rspec"]
""",
    
    "cpp": """# C++ project Docker image
FROM gcc:13-bookworm

WORKDIR /repo

RUN apt-get update && apt-get install -y --no-install-recommends \\
    git python3 python3-pip cmake build-essential pkg-config \\
    && rm -rf /var/lib/apt/lists/*

COPY . /repo/
RUN cmake -B build . 2>/dev/null || mkdir -p build
RUN cmake --build build 2>/dev/null || make 2>/dev/null || true

WORKDIR /repo
CMD ["ctest", "--test-dir", "build"]
""",
    
    "php": """# PHP project Docker image
FROM php:8.1-cli

WORKDIR /repo

RUN apt-get update && apt-get install -y --no-install-recommends \\
    git unzip libzip-dev libpng-dev libicu-dev \\
    && docker-php-ext-install zip intl \\
    && rm -rf /var/lib/apt/lists/*

# Install Composer
COPY --from=composer:latest /usr/bin/composer /usr/bin/composer

COPY composer.json composer.lock* ./
RUN composer install --no-scripts --no-autoloader || true

COPY . /repo/
RUN composer dump-autoload --optimize || true

WORKDIR /repo
CMD ["./vendor/bin/phpunit", "--testdox"]
""",
}


# Language-specific entrypoint script templates
ENTRYPOINT_TEMPLATES = {
    "rust": """#!/bin/bash
set -e
cd /repo
{test_command}
""",
    "python": """#!/bin/bash
set -e
cd /repo
{test_command}
""",
    "go": """#!/bin/bash
set -e
cd /repo
{test_command}
""",
    "java": """#!/bin/bash
set -e
cd /repo
{test_command}
""",
    "kotlin": """#!/bin/bash
set -e
cd /repo
{test_command}
""",
    "javascript": """#!/bin/bash
set -e
cd /repo
{test_command}
""",
    "typescript": """#!/bin/bash
set -e
cd /repo
{test_command}
""",
    "csharp": """#!/bin/bash
set -e
cd /repo
{test_command}
""",
    "ruby": """#!/bin/bash
set -e
cd /repo
{test_command}
""",
    "cpp": """#!/bin/bash
set -e
cd /repo
{test_command}
""",
    "php": """#!/bin/bash
set -e
cd /repo
{test_command}
""",
}


# Language-specific before_repo_set_cmd templates
BEFORE_REPO_SET_CMD_TEMPLATES = {
    "rust": "apt-get update && apt-get install -y pkg-config libssl-dev",
    "python": "pip install --upgrade pip setuptools wheel",
    "go": "go mod download",
    "java": "",
    "kotlin": "",
    "javascript": "npm install -g pnpm yarn || true",
    "typescript": "npm install -g pnpm yarn typescript || true",
    "csharp": "dotnet restore || true",
    "ruby": "gem update bundler || true",
    "cpp": "apt-get update && apt-get install -y cmake build-essential",
    "php": "composer install || true",
}


def load_accurate_dockerfile(repo: str, base_commit: str = "") -> Optional[str]:
    """Load pre-built accurate Dockerfile for a specific repo and commit."""
    if not DOCKERFILES_DIR.exists():
        return None
    
    safe_name = repo.replace("/", "_")
    
    # Try per-commit Dockerfile first
    if base_commit:
        commit_short = base_commit[:12]
        dockerfile_path = DOCKERFILES_DIR / f"Dockerfile.{safe_name}_{commit_short}"
        if dockerfile_path.exists():
            return dockerfile_path.read_text()
    
    # Fall back to repo-level Dockerfile
    dockerfile_path = DOCKERFILES_DIR / f"Dockerfile.{safe_name}"
    if dockerfile_path.exists():
        return dockerfile_path.read_text()
    
    return None


def fetch_github_repo_metadata(repo: str) -> Optional[Dict[str, Any]]:
    """
    Fetch repository metadata from GitHub API.
    
    Args:
        repo: Repository in format "owner/repo"
        
    Returns:
        Dictionary with metadata or None if failed
    """
    try:
        url = f"https://api.github.com/repos/{repo}"
        req = urllib.request.Request(url)
        req.add_header('Accept', 'application/vnd.github.v3+json')
        req.add_header('User-Agent', 'PR-Evaluation-Tool/1.0')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        logger.warning(f"Failed to fetch GitHub metadata for {repo}: {e}")
        return None


def fetch_pr_metadata(repo: str, pr_number: int) -> Optional[Dict[str, Any]]:
    """
    Fetch PR metadata from GitHub API.
    
    Args:
        repo: Repository in format "owner/repo"
        pr_number: PR number
        
    Returns:
        Dictionary with PR metadata or None if failed
    """
    try:
        url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
        req = urllib.request.Request(url)
        req.add_header('Accept', 'application/vnd.github.v3+json')
        req.add_header('User-Agent', 'PR-Evaluation-Tool/1.0')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        logger.warning(f"Failed to fetch PR metadata for {repo}#{pr_number}: {e}")
        return None


def classify_task_category(problem_statement: str, pr_metadata: Optional[Dict] = None) -> str:
    """
    Classify task category based on problem statement and PR metadata.
    
    Returns: bug, feature, refactor, docs, test, or other
    """
    # Try to get labels from PR metadata first
    if pr_metadata:
        labels = [l.get('name', '').lower() for l in pr_metadata.get('labels', [])]
        if any('bug' in l or 'fix' in l for l in labels):
            return 'bug'
        if any('feature' in l or 'enhancement' in l for l in labels):
            return 'feature'
        if any('refactor' in l for l in labels):
            return 'refactor'
        if any('doc' in l for l in labels):
            return 'docs'
        if any('test' in l for l in labels):
            return 'test'
    
    # Fall back to keyword analysis
    text_lower = problem_statement.lower()
    
    # Bug indicators
    bug_keywords = ['fix', 'bug', 'error', 'crash', 'issue', 'broken', 'fail', 
                    'exception', 'incorrect', 'wrong', 'null', 'undefined']
    if any(word in text_lower for word in bug_keywords):
        return 'bug'
    
    # Feature indicators
    feature_keywords = ['add', 'implement', 'new', 'feature', 'support', 
                       'introduce', 'enable', 'create']
    if any(word in text_lower for word in feature_keywords):
        return 'feature'
    
    # Refactor indicators
    refactor_keywords = ['refactor', 'cleanup', 'improve', 'optimize', 
                        'performance', 'speed', 'memory', 'simplify']
    if any(word in text_lower for word in refactor_keywords):
        return 'refactor'
    
    # Documentation indicators
    docs_keywords = ['doc', 'readme', 'comment', 'documentation', 'typo', 'spelling']
    if any(word in text_lower for word in docs_keywords):
        return 'docs'
    
    return 'other'


def classify_repo_category(repo_metadata: Optional[Dict]) -> str:
    """
    Classify repository category based on GitHub metadata.
    
    Returns: web, cli, library, framework, or other
    """
    if not repo_metadata:
        return 'other'
    
    topics = repo_metadata.get('topics', [])
    description = repo_metadata.get('description', '') or ''
    combined = ' '.join(topics).lower() + ' ' + description.lower()
    
    # Web indicators
    if any(word in combined for word in ['web', 'http', 'api', 'server', 'rest', 'graphql']):
        return 'web'
    
    # CLI indicators
    if any(word in combined for word in ['cli', 'command', 'terminal', 'shell']):
        return 'cli'
    
    # Framework indicators
    if any(word in combined for word in ['framework', 'toolkit', 'platform']):
        return 'framework'
    
    # Library indicators
    if any(word in combined for word in ['library', 'package', 'sdk', 'module', 'crate']):
        return 'library'
    
    return 'other'


def extract_version_from_pr(pr_metadata: Optional[Dict], repo_metadata: Optional[Dict]) -> str:
    """Extract version information from PR or repo metadata."""
    # Try to get target branch from PR
    if pr_metadata:
        base_ref = pr_metadata.get('base', {}).get('ref', '')
        if base_ref:
            return base_ref
    
    # Fall back to repo default branch
    if repo_metadata:
        return repo_metadata.get('default_branch', 'main')
    
    return ""


def extract_test_files(test_names_json: str, language: str) -> str:
    """
    Extract unique test file paths from test names.
    
    Args:
        test_names_json: JSON string array of test names
        language: Programming language
        
    Returns:
        JSON string array of unique test file paths
    """
    try:
        test_names = json.loads(test_names_json)
    except:
        return "[]"
    
    if not test_names:
        return "[]"
    
    files = set()
    
    for test_name in test_names:
        if language.lower() == 'python':
            # Python: tests/test_module.py::TestClass::test_method
            if '::' in test_name:
                file_path = test_name.split('::')[0]
                if file_path.endswith('.py'):
                    files.add(file_path)
            elif test_name.endswith('.py'):
                files.add(test_name)
                
        elif language.lower() in ('javascript', 'typescript'):
            # JS/TS: src/__tests__/module.test.js or test/module.spec.ts
            if '.test.' in test_name or '.spec.' in test_name:
                files.add(test_name)
                
        elif language.lower() == 'rust':
            # Rust: module::submodule::test_function
            if '::' in test_name:
                module_path = test_name.rsplit('::', 1)[0]
                files.add(f"{module_path.replace('::', '/')}")
                
        elif language.lower() == 'go':
            # Go: package/path.TestName
            if '.' in test_name:
                pkg_path = test_name.rsplit('.', 1)[0]
                files.add(pkg_path)
                
        elif language.lower() in ('java', 'kotlin'):
            # Java: com.example.TestClass#testMethod or package.ClassName.methodName
            if '#' in test_name:
                class_path = test_name.split('#')[0]
                files.add(class_path.replace('.', '/') + '.java')
            elif '.' in test_name:
                # Take all but last component as package/class path
                parts = test_name.rsplit('.', 1)
                if len(parts) == 2:
                    files.add(parts[0].replace('.', '/'))
                    
        elif language.lower() == 'csharp':
            # C#: Namespace.ClassName.TestMethod
            if '.' in test_name:
                parts = test_name.rsplit('.', 1)
                if len(parts) == 2:
                    files.add(parts[0].replace('.', '/') + '.cs')
                    
        elif language.lower() == 'php':
            # PHP: Namespace\ClassName::testMethodName or Namespace\ClassName::testMethodWithDataSet"dataSetName"
            # Example: League\Csv\AbstractCsv::testStreamFilterModeWithDataSet"readerWithStreamCapability"
            if '::' in test_name:
                # Extract class path before ::
                class_path = test_name.split('::')[0]
                # Convert namespace to file path
                # League\Csv\AbstractCsv -> League/Csv/AbstractCsv
                file_path = class_path.replace('\\', '/')
                # PHPUnit tests typically end with Test.php
                if not file_path.endswith('Test'):
                    file_path = file_path + 'Test'
                files.add(file_path + '.php')
            elif '\\' in test_name:
                # Just a class name without method
                file_path = test_name.replace('\\', '/') + '.php'
                files.add(file_path)
    
    return json.dumps(sorted(list(files)))


def transform_to_29_fields(
    source: Dict[str, Any],
    state: Optional[Dict[str, Any]] = None,
    repo_metadata: Optional[Dict[str, Any]] = None,
    pr_metadata: Optional[Dict[str, Any]] = None
) -> TaskTemplate29Fields:
    """
    Transform source instance metadata to the full 29-field format.
    
    Args:
        source: Original instance metadata (from instance.json)
        state: Workflow state (from state.json)
        repo_metadata: Optional GitHub repo metadata
        pr_metadata: Optional GitHub PR metadata
        
    Returns:
        TaskTemplate29Fields with all 29 fields populated
    """
    template = TaskTemplate29Fields()
    
    # ============================================
    # Category 1: Direct Copy (8 fields)
    # ============================================
    template.instance_id = source.get('instance_id', '')
    template.repo = source.get('repo', '')
    template.base_commit = source.get('base_commit', '')
    template.problem_statement = source.get('problem_statement', '')
    template.FAIL_TO_PASS = source.get('FAIL_TO_PASS', '[]')
    template.PASS_TO_PASS = source.get('PASS_TO_PASS', '[]')
    template.language = source.get('language', '')
    template.test_patch = source.get('test_patch', '')
    
    # ============================================
    # Category 2: Simple Rename (3 fields)
    # ============================================
    template.hints = source.get('hints_text', '')
    template.docker_image_url = source.get('image_storage_uri', '')
    template.functional_patch = source.get('patch', '')
    
    # ============================================
    # Category 3: Derive/Transform (6 fields)
    # ============================================
    # repo_path_or_url: From repo
    template.repo_path_or_url = f"https://github.com/{template.repo}" if template.repo else ""
    
    # run_script: From test_command
    template.run_script = source.get('test_command', '')
    
    # parsing_script: From test_output_parser
    template.parsing_script = source.get('test_output_parser', '')
    
    # version: From PR metadata or state
    template.version = extract_version_from_pr(pr_metadata, repo_metadata)
    if not template.version and state:
        template.version = state.get('target_branch', '')
    
    # selected_test_files_to_run: Parse from test names
    all_tests_json = template.FAIL_TO_PASS
    try:
        f2p = json.loads(template.FAIL_TO_PASS) if template.FAIL_TO_PASS else []
        p2p = json.loads(template.PASS_TO_PASS) if template.PASS_TO_PASS else []
        all_tests_json = json.dumps(f2p + p2p)
    except:
        pass
    template.selected_test_files_to_run = extract_test_files(all_tests_json, template.language)
    
    # ============================================
    # Category 4: Generate Per-Language (3 fields)
    # ============================================
    lang = template.language.lower()
    
    # docker_file: Try to load accurate one first, fallback to template
    accurate_dockerfile = load_accurate_dockerfile(template.repo, template.base_commit)
    if accurate_dockerfile:
        template.docker_file = accurate_dockerfile
    else:
        template.docker_file = DOCKERFILE_TEMPLATES.get(lang, DOCKERFILE_TEMPLATES.get('python', ''))
    
    # entrypoint_script: Language-specific entry
    entrypoint_template = ENTRYPOINT_TEMPLATES.get(lang, ENTRYPOINT_TEMPLATES.get('python', ''))
    template.entrypoint_script = entrypoint_template.format(
        test_command=template.run_script or 'echo "No test command configured"'
    )
    
    # before_repo_set_cmd: Dependency setup
    template.before_repo_set_cmd = BEFORE_REPO_SET_CMD_TEMPLATES.get(lang, '')
    
    # ============================================
    # Category 5: Configuration Defaults (3 fields)
    # ============================================
    template.container_mem = "4g"
    template.container_memswap = "4g"
    template.container_network_needed = "false"
    
    # ============================================
    # Category 6: Classification (2 fields)
    # ============================================
    template.task_category = classify_task_category(template.problem_statement, pr_metadata)
    template.repo_category = classify_repo_category(repo_metadata)
    
    # ============================================
    # Additional metadata fields
    # ============================================
    template.problem_statement_variants = ""
    template.artifacts = "[]"
    template.status = "complete"
    template.owner = ""
    template.notes = ""
    
    return template


def collect_29_fields(
    workspace_path: Path,
    logger: logging.Logger,
    fetch_github_metadata: bool = True
) -> Optional[TaskTemplate29Fields]:
    """
    Collect all 29 fields from workspace metadata and save to 29_fields folder.
    
    Args:
        workspace_path: Path to the PR workspace directory
        logger: Logger instance
        fetch_github_metadata: Whether to fetch GitHub API metadata
        
    Returns:
        TaskTemplate29Fields object or None if failed
    """
    logger.info("Collecting 29-field task template data")
    
    # Load instance.json (source metadata)
    instance_file = workspace_path / "metadata" / "instance.json"
    if not instance_file.exists():
        logger.error(f"Instance file not found: {instance_file}")
        return None
    
    try:
        with open(instance_file, 'r') as f:
            source = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load instance.json: {e}")
        return None
    
    # Load state.json (additional workflow data)
    state_file = workspace_path / "state.json"
    state = None
    if state_file.exists():
        try:
            with open(state_file, 'r') as f:
                state = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load state.json: {e}")
    
    # Fetch GitHub metadata if enabled
    repo_metadata = None
    pr_metadata = None
    
    if fetch_github_metadata:
        repo = source.get('repo', '')
        if repo:
            logger.info(f"Fetching GitHub metadata for {repo}")
            repo_metadata = fetch_github_repo_metadata(repo)
            
            # Get PR number from state
            pr_number = None
            if state:
                pr_number = state.get('pr_number')
            
            if pr_number:
                pr_metadata = fetch_pr_metadata(repo, pr_number)
    
    # Transform to 29-field format
    template = transform_to_29_fields(source, state, repo_metadata, pr_metadata)
    
    logger.info("29-field template generated successfully")
    logger.info(f"  instance_id: {template.instance_id}")
    logger.info(f"  repo: {template.repo}")
    logger.info(f"  language: {template.language}")
    logger.info(f"  task_category: {template.task_category}")
    logger.info(f"  repo_category: {template.repo_category}")
    
    return template


def save_29_fields_csv(
    template: TaskTemplate29Fields,
    output_dir: Path,
    logger: logging.Logger,
    append: bool = True
) -> Path:
    """
    Save 29-field template to CSV file.
    
    Args:
        template: TaskTemplate29Fields object
        output_dir: Directory to save CSV (29_fields folder)
        logger: Logger instance
        append: If True, append to existing CSV; otherwise create new
        
    Returns:
        Path to CSV file
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "task_instances_29fields.csv"
    
    # Get field names from dataclass
    field_names = list(asdict(template).keys())
    
    # Check if file exists and has content
    file_exists = csv_path.exists() and csv_path.stat().st_size > 0
    
    # Write mode
    mode = 'a' if append and file_exists else 'w'
    
    with open(csv_path, mode, newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=field_names)
        
        # Write header if new file
        if mode == 'w':
            writer.writeheader()
        
        # Write row
        writer.writerow(asdict(template))
    
    logger.info(f"29-field data saved to {csv_path}")
    return csv_path


def save_29_fields_jsonl(
    template: TaskTemplate29Fields,
    output_dir: Path,
    logger: logging.Logger,
    append: bool = True
) -> Path:
    """
    Save 29-field template to JSONL file.
    
    Args:
        template: TaskTemplate29Fields object
        output_dir: Directory to save JSONL (29_fields folder)
        logger: Logger instance
        append: If True, append to existing JSONL; otherwise create new
        
    Returns:
        Path to JSONL file
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "task_instances_29fields.jsonl"
    
    # Write mode
    mode = 'a' if append else 'w'
    
    with open(jsonl_path, mode, encoding='utf-8') as f:
        f.write(json.dumps(asdict(template)) + '\n')
    
    logger.info(f"29-field data appended to {jsonl_path}")
    return jsonl_path


def integrate_29_fields_collection(
    workspace_path: Path,
    logger: logging.Logger,
    output_dir: Optional[Path] = None,
    fetch_github_metadata: bool = True
) -> bool:
    """
    Full integration function to collect and save 29-field data.
    
    This function is called at the end of Part 2 workflow to generate
    the complete 29-field task template.
    
    Args:
        workspace_path: Path to the PR workspace directory
        logger: Logger instance
        output_dir: Optional custom output directory (default: {repo_root}/29_fields)
        fetch_github_metadata: Whether to fetch GitHub API metadata
        
    Returns:
        True if successful, False otherwise
    """
    logger.info("=" * 40)
    logger.info("29-FIELD COLLECTION")
    logger.info("=" * 40)
    
    try:
        # Collect 29 fields
        template = collect_29_fields(
            workspace_path, 
            logger, 
            fetch_github_metadata=fetch_github_metadata
        )
        
        if not template:
            logger.error("Failed to collect 29-field data")
            return False
        
        # Determine output directory
        if output_dir is None:
            # Default to {repo_root}/29_fields
            output_dir = workspace_path.parent.parent / "29_fields"
        
        # Save to both CSV and JSONL
        save_29_fields_csv(template, output_dir, logger, append=True)
        save_29_fields_jsonl(template, output_dir, logger, append=True)
        
        # Also save a copy in workspace for reference
        workspace_29fields_dir = workspace_path / "29_fields"
        workspace_29fields_dir.mkdir(parents=True, exist_ok=True)
        
        # Save individual instance file
        instance_file = workspace_29fields_dir / "instance_29fields.json"
        with open(instance_file, 'w') as f:
            json.dump(asdict(template), f, indent=2)
        logger.info(f"Individual 29-field instance saved to {instance_file}")
        
        logger.info("=" * 40)
        logger.info("29-FIELD COLLECTION COMPLETE")
        logger.info("=" * 40)
        
        return True
        
    except Exception as e:
        logger.exception(f"29-field collection failed: {e}")
        return False


# CLI entry point for standalone usage
def main():
    """Main entry point for standalone 29-field collection."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Collect 29-field task template data from workspace'
    )
    parser.add_argument(
        'workspace',
        help='Workspace directory containing instance.json'
    )
    parser.add_argument(
        '--output', '-o',
        type=str,
        default=None,
        help='Output directory (default: {repo_root}/29_fields)'
    )
    parser.add_argument(
        '--no-github',
        action='store_true',
        help='Skip GitHub API calls'
    )
    
    args = parser.parse_args()
    
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    
    workspace_path = Path(args.workspace).resolve()
    output_dir = Path(args.output).resolve() if args.output else None
    
    success = integrate_29_fields_collection(
        workspace_path,
        logger,
        output_dir=output_dir,
        fetch_github_metadata=not args.no_github
    )
    
    return 0 if success else 1


if __name__ == '__main__':
    import sys
    sys.exit(main())
