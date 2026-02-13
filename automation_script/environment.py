"""
Environment setup and healing for the automation workflow.

Handles:
- Virtual environment creation
- Dependency installation
- Language/test command detection
- Environment healing for common failures
"""

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .config import (
    DEPENDENCY_FILES,
    EXTENSION_TO_LANGUAGE,
    TEST_COMMANDS,
    HEALING_STRATEGIES,
    VENV_NAME,
    TestResult,
)
from .utils import run_command, get_pip_executable


def detect_language_from_files(changed_files: List[str], logger: logging.Logger) -> Optional[str]:
    """
    Detect language from a list of changed file paths.
    
    This is more accurate for PRs in polyglot repositories because it
    considers only the files that were actually modified.
    
    Args:
        changed_files: List of file paths that were changed
        logger: Logger instance
        
    Returns:
        Detected language or None if undetermined
    """
    if not changed_files:
        return None
    
    extension_counts: Dict[str, int] = {}
    
    # Also check for language-specific files in changed paths
    has_cargo_toml = any("Cargo.toml" in f or "Cargo.lock" in f for f in changed_files)
    has_package_json = any("package.json" in f or "package-lock.json" in f for f in changed_files)
    has_go_mod = any("go.mod" in f or "go.sum" in f for f in changed_files)
    has_pom_xml = any("pom.xml" in f for f in changed_files)
    has_csproj = any(f.endswith(".csproj") or f.endswith(".sln") for f in changed_files)
    has_composer_json = any("composer.json" in f or "composer.lock" in f for f in changed_files)
    has_configure_ac = any("configure.ac" in f or "configure.in" in f for f in changed_files)
    has_makefile_am = any("Makefile.am" in f or "Makefile.in" in f for f in changed_files)
    
    for file_path in changed_files:
        # Get file extension
        path = Path(file_path)
        ext = path.suffix.lower()
        
        if ext in EXTENSION_TO_LANGUAGE:
            lang = EXTENSION_TO_LANGUAGE[ext]
            extension_counts[lang] = extension_counts.get(lang, 0) + 1
    
    # Boost scores based on project files
    if has_cargo_toml:
        extension_counts['rust'] = extension_counts.get('rust', 0) + 10
    if has_package_json:
        # Could be JS or TS - check for .ts files
        ts_count = extension_counts.get('typescript', 0)
        js_count = extension_counts.get('javascript', 0)
        if ts_count > js_count:
            extension_counts['typescript'] = ts_count + 5
        else:
            extension_counts['javascript'] = js_count + 5
    if has_go_mod:
        extension_counts['go'] = extension_counts.get('go', 0) + 10
    if has_pom_xml:
        extension_counts['java'] = extension_counts.get('java', 0) + 10
    if has_csproj:
        extension_counts['csharp'] = extension_counts.get('csharp', 0) + 10
    if has_composer_json:
        extension_counts['php'] = extension_counts.get('php', 0) + 10
    if has_configure_ac or has_makefile_am:
        extension_counts['c'] = extension_counts.get('c', 0) + 10

    if extension_counts:
        most_common = max(extension_counts, key=extension_counts.get)
        total_files = len(changed_files)
        lang_count = extension_counts[most_common]
        # Account for the boost we added
        actual_count = min(lang_count, total_files)
        percentage = (actual_count / total_files) * 100 if total_files > 0 else 0
        logger.info(f"Detected language from changed files: {most_common} ({actual_count}/{total_files} files, {percentage:.1f}%)")
        return most_common
    
    return None


def _uses_pnpm(repo_path: Path) -> bool:
    """
    Return True if the repository uses pnpm (pnpm-lock.yaml, packageManager, or workspace:*).
    Used to choose test command (pnpm test vs npm test).
    """
    if (repo_path / "pnpm-lock.yaml").exists():
        return True
    pkg = repo_path / "package.json"
    if not pkg.exists():
        return False
    try:
        with open(pkg, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "pnpm" in str(data.get("packageManager", "")).lower():
            return True
        for key in ("dependencies", "devDependencies"):
            deps = data.get(key, {}) or {}
            for v in (deps.values() if isinstance(deps, dict) else []):
                if isinstance(v, str) and ("workspace:" in v or v == "workspace:*"):
                    return True
    except Exception:
        pass
    return False


def detect_language(
    repo_path: Path,
    logger: logging.Logger,
    changed_files: Optional[List[str]] = None,
    repo_full_name: Optional[str] = None
) -> str:
    """
    Detect the primary programming language of the repository.

    Detection strategy:
    0. Check for repository-specific configuration (if repo_full_name provided)
    1. If changed_files provided, detect from those first (most accurate for PRs)
    2. Look for language-specific dependency files
    3. Count file extensions and pick the most common

    Args:
        repo_path: Path to repository
        logger: Logger instance
        changed_files: Optional list of files changed in PR (for more accurate detection)
        repo_full_name: Optional full repository name (e.g., "owner/repo") for config lookup

    Returns:
        Language name (e.g., 'python', 'javascript')
    """
    logger.info("Detecting repository language")
    
    # Strategy 0: Check for repo-specific configuration first
    if repo_full_name:
        from .repo_configs import get_repo_config
        repo_config = get_repo_config(repo_full_name, logger)
        if repo_config and repo_config.language:
            logger.info(f"Using configured language for {repo_full_name}: {repo_config.language}")
            return repo_config.language

    # Strategy 0: Detect from changed files (most accurate for PRs)
    if changed_files:
        logger.info(f"Using {len(changed_files)} changed files for language detection")
        lang_from_changes = detect_language_from_files(changed_files, logger)
        if lang_from_changes:
            return lang_from_changes

    # Strategy 1: Check for dependency files
    for lang, files in DEPENDENCY_FILES.items():
        for dep_file in files:
            # Handle glob patterns in dependency files (e.g., "*.csproj")
            if '*' in dep_file:
                if list(repo_path.glob(dep_file)):
                    logger.info(f"Detected language from {dep_file}: {lang}")
                    return lang
            elif (repo_path / dep_file).exists():
                logger.info(f"Detected language from {dep_file}: {lang}")
                return lang

    # Strategy 2: Count file extensions
    extension_counts: Dict[str, int] = {}
    for ext, lang in EXTENSION_TO_LANGUAGE.items():
        # Limit search depth for performance
        count = 0
        for f in repo_path.rglob(f"*{ext}"):
            # Skip hidden dirs, venv, node_modules
            if any(p.startswith(".") or p in ("venv", "node_modules", "__pycache__")
                   for p in f.parts):
                continue
            count += 1
            if count > 100:  # Cap the count
                break
        if count > 0:
            extension_counts[lang] = extension_counts.get(lang, 0) + count

    if extension_counts:
        most_common = max(extension_counts, key=extension_counts.get)
        logger.info(f"Detected language by file count: {most_common} ({extension_counts[most_common]} files)")
        return most_common

    logger.warning("Could not detect language, defaulting to 'python'")
    return "python"


def detect_test_command(
    repo_path: Path,
    language: str,
    logger: logging.Logger,
    repo_full_name: Optional[str] = None,
    changed_files: Optional[List[str]] = None
) -> str:
    """
    Detect the appropriate test command for the repository.

    Checks for configuration files that indicate test setup.
    For multi-module projects, uses changed_files to target specific modules.

    Args:
        repo_path: Path to repository
        language: Detected language
        logger: Logger instance
        repo_full_name: Optional full repository name (e.g., "owner/repo") for config lookup
        changed_files: Optional list of changed files (for module-specific testing)

    Returns:
        Test command string
    """
    logger.info("Detecting test command")
    
    # Check for repo-specific configuration first
    if repo_full_name:
        from .repo_configs import get_repo_config, detect_maven_modules_from_files
        repo_config = get_repo_config(repo_full_name, logger)
        
        # Special handling for aws-sdk-java-v2: detect changed modules
        if repo_config and repo_full_name.lower() == "aws/aws-sdk-java-v2" and changed_files:
            modules = detect_maven_modules_from_files(changed_files, logger)
            if modules:
                # Build test command targeting specific modules
                # REMOVED -am flag to avoid building dependencies with compilation errors
                # Previously: -am (also-make) ensured dependencies were built, but this caused
                # Maven to compile modules like json-utils which have missing Jackson dependencies
                module_list = ",".join(modules)
                test_cmd = f"mvn test -pl {module_list} -Dcheckstyle.skip=true -Dspotbugs.skip=true -Dpmd.skip=true -Dfindbugs.skip=true -Drat.skip=true -Denforcer.skip=true -Dlicense.skip=true -Djacoco.skip=true"
                logger.info(f"Multi-module Maven project - targeting modules: {modules}")
                logger.info(f"Generated test command: {test_cmd}")
                return test_cmd
        
        if repo_config and repo_config.test_command:
            logger.info(f"Using configured test command for {repo_full_name}: {repo_config.test_command}")
            return repo_config.test_command

    if language == "python":
        # Check for pytest configuration
        if (repo_path / "pytest.ini").exists():
            logger.info("Found pytest.ini, using pytest")
            return "pytest"

        if (repo_path / "setup.cfg").exists():
            with open(repo_path / "setup.cfg") as f:
                if "[tool:pytest]" in f.read():
                    logger.info("Found pytest config in setup.cfg")
                    return "pytest"

        if (repo_path / "pyproject.toml").exists():
            with open(repo_path / "pyproject.toml") as f:
                content = f.read()
                if "[tool.pytest" in content:
                    logger.info("Found pytest config in pyproject.toml")
                    return "pytest"

        if (repo_path / "tox.ini").exists():
            logger.info("Found tox.ini, using tox")
            return "tox"

        # Default to pytest
        return "pytest"

    elif language == "ruby":
        # Ruby test command detection
        # Priority: RSpec (spec/) > Minitest/Rake (test/ or Rakefile)

        has_gemfile = (repo_path / "Gemfile").exists()
        has_rakefile = (repo_path / "Rakefile").exists()
        has_spec_dir = (repo_path / "spec").is_dir()
        has_test_dir = (repo_path / "test").is_dir()

        # Check for spec helper (strong RSpec indicator)
        has_spec_helper = (repo_path / "spec" / "spec_helper.rb").exists()
        has_rails_helper = (repo_path / "spec" / "rails_helper.rb").exists()

        # Count *_spec.rb files vs *_test.rb files to determine framework
        spec_files = list(repo_path.glob("spec/**/*_spec.rb")) if has_spec_dir else []
        test_files = list(repo_path.glob("test/**/*_test.rb")) if has_test_dir else []

        prefix = "bundle exec " if has_gemfile else ""

        # RSpec detection: spec/ directory with spec files or spec_helper
        if has_spec_dir and (spec_files or has_spec_helper or has_rails_helper):
            logger.info(f"Found RSpec setup (spec/ with {len(spec_files)} spec files), using rspec")
            return f"{prefix}rspec"

        # Minitest/TestUnit detection: test/ directory with test files
        if has_test_dir and test_files:
            logger.info(f"Found Minitest setup (test/ with {len(test_files)} test files), using rake test")
            if has_rakefile:
                # Use TESTOPTS="-v" to get verbose output with individual test names
                # Minitest verbose format: "TestClass#test_method = X.XX s = ."
                return f'{prefix}rake test TESTOPTS="-v"'
            else:
                # No Rakefile, run tests directly with verbose output
                return f"{prefix}ruby -Itest -e 'Dir.glob(\"test/**/*_test.rb\").each {{|f| require \"./\" + f}}' -- -v"

        # Rakefile with test task (check content if possible)
        if has_rakefile:
            try:
                with open(repo_path / "Rakefile") as f:
                    content = f.read()
                    if "Rake::TestTask" in content or "task :test" in content or "task(:test)" in content:
                        logger.info("Found Rake test task in Rakefile, using rake test")
                        # Use TESTOPTS="-v" for verbose output with individual test names
                        return f'{prefix}rake test TESTOPTS="-v"'
            except Exception:
                pass
            # Default to rake test if Rakefile exists
            logger.info("Found Rakefile, using rake test")
            return f'{prefix}rake test TESTOPTS="-v"'

        # Default to rspec for Ruby projects (most common)
        logger.info("Using default Ruby test command: bundle exec rspec")
        return "bundle exec rspec"

    elif language in ("javascript", "typescript"):
        # Check for Gruntfile (Grunt-based projects)
        if (repo_path / "Gruntfile.js").exists() or (repo_path / "Gruntfile.coffee").exists():
            logger.info("Found Gruntfile, using npx grunt test")
            # Use npx to run locally installed grunt from node_modules/.bin/
            return "npx grunt test"

        # Check for package.json test script
        pkg_json = repo_path / "package.json"
        if pkg_json.exists():
            with open(pkg_json) as f:
                try:
                    pkg = json.load(f)
                    if "scripts" in pkg and "test" in pkg["scripts"]:
                        logger.info("Found test script in package.json")
                        return "pnpm test" if _uses_pnpm(repo_path) else "npm test"
                except json.JSONDecodeError:
                    pass

    elif language == "php":
        # Helper function to check if a file is an executable PHP script
        def _is_php_executable(path: Path) -> bool:
            """Check if a file is an executable PHP script (not just a config file)."""
            if not path.exists() or not path.is_file():
                return False
            try:
                with open(path, 'rb') as f:
                    first_bytes = f.read(100)
                    # Check for PHP shebang or PHP opening tag
                    return b'#!/' in first_bytes or b'<?php' in first_bytes
            except Exception:
                return False
        
        # Helper function to check if project uses Pest instead of PHPUnit
        def _uses_pest(composer_path: Path) -> bool:
            """Check if the project uses Pest testing framework."""
            if not composer_path.exists():
                return False
            try:
                with open(composer_path) as f:
                    composer_data = json.load(f)
                    require_dev = composer_data.get("require-dev", {})
                    require = composer_data.get("require", {})
                    # Pest is typically in require-dev
                    return "pestphp/pest" in require_dev or "pestphp/pest" in require
            except Exception:
                return False
        
        # Detect PHP subdirectory (common in monorepos like OpnForm with api/ folder)
        php_subdir = None
        php_root = repo_path
        composer_path = repo_path / "composer.json"
        
        # First check repo root
        if not composer_path.exists():
            # Check common subdirectories for PHP projects
            for subdir in ["api", "backend", "app", "src", "php"]:
                subdir_path = repo_path / subdir
                if (subdir_path / "composer.json").exists():
                    php_subdir = subdir
                    php_root = subdir_path
                    composer_path = subdir_path / "composer.json"
                    logger.info(f"Detected PHP project in subdirectory: {subdir}")
                    break
            
            # Also check for any directory with composer.json
            if not php_subdir:
                for composer_file in repo_path.glob("*/composer.json"):
                    php_subdir = composer_file.parent.name
                    php_root = composer_file.parent
                    composer_path = composer_file
                    logger.info(f"Found composer.json in subdirectory: {php_subdir}")
                    break
        
        # Check if project uses Pest (a modern PHP testing framework)
        uses_pest = _uses_pest(composer_path)
        if uses_pest:
            logger.info("Project uses Pest testing framework")
            test_binary = "./vendor/bin/pest"
        else:
            test_binary = "./vendor/bin/phpunit --testdox"
        
        # Check for PHPUnit/Pest configuration
        if (php_root / "phpunit.xml").exists() or (php_root / "phpunit.xml.dist").exists():
            logger.info(f"Found phpunit.xml in {'subdirectory ' + php_subdir if php_subdir else 'repo root'}")
            
            # Check for local phpunit/pest script
            local_phpunit = php_root / "phpunit"
            local_pest = php_root / "pest"
            
            if uses_pest and _is_php_executable(local_pest):
                logger.info("Found local pest script, using ./pest")
                if php_subdir:
                    return f"bash -c 'cd {php_subdir} && ./pest'"
                return "./pest"
            
            if _is_php_executable(local_phpunit):
                logger.info("Found local phpunit script, using ./phpunit")
                if php_subdir:
                    return f"bash -c 'cd {php_subdir} && ./phpunit --testdox'"
                return "./phpunit --testdox"
            
            # Use vendor/bin/pest or vendor/bin/phpunit
            if composer_path.exists():
                if php_subdir:
                    return f"bash -c 'cd {php_subdir} && {test_binary}'"
                return test_binary
            
            if php_subdir:
                return f"bash -c 'cd {php_subdir} && phpunit --testdox'"
            return "phpunit --testdox"
        
        # Check for composer.json test script
        if composer_path.exists():
            with open(composer_path) as f:
                try:
                    composer = json.load(f)
                    scripts = composer.get("scripts", {})
                    if "test" in scripts:
                        logger.info("Found test script in composer.json")
                        if php_subdir:
                            return f"bash -c 'cd {php_subdir} && composer test'"
                        return "composer test"
                except json.JSONDecodeError:
                    pass
            
            # Check for local phpunit/pest script
            local_phpunit = php_root / "phpunit"
            local_pest = php_root / "pest"
            
            if uses_pest and _is_php_executable(local_pest):
                if php_subdir:
                    return f"bash -c 'cd {php_subdir} && ./pest'"
                return "./pest"
            
            if _is_php_executable(local_phpunit):
                if php_subdir:
                    return f"bash -c 'cd {php_subdir} && ./phpunit --testdox'"
                return "./phpunit --testdox"
            
            # Default to pest or phpunit via vendor
            if php_subdir:
                return f"bash -c 'cd {php_subdir} && {test_binary}'"
            return test_binary
        
        if php_subdir:
            return f"bash -c 'cd {php_subdir} && phpunit --testdox'"
        return "phpunit --testdox"

    elif language == "csharp":
        # Check for dotnet subdirectory (common in polyglot repos like semantic-kernel)
        has_dotnet_subdir = (repo_path / "dotnet").is_dir()
        
        if has_dotnet_subdir:
            dotnet_path = repo_path / "dotnet"
            
            # For polyglot repos, change to dotnet directory before running tests
            # Also remove global.json to allow using the available SDK
            logger.info("Found dotnet/ subdirectory, will run tests from there")
            
            # Look for solution files (.sln, .slnx, .slnf)
            sln_files = list(dotnet_path.glob("*.sln")) + list(dotnet_path.glob("*.slnx"))
            if sln_files:
                sln_file = sln_files[0].name
                logger.info(f"Found solution file in dotnet/: {sln_file}")
                # Use bash -c to change directory, remove global.json (if exists), and run tests
                return f"bash -c 'cd /repo/dotnet && rm -f global.json && dotnet test {sln_file} --verbosity normal'"
            else:
                # No solution file, try to run tests from directory
                return "bash -c 'cd /repo/dotnet && rm -f global.json && dotnet test --verbosity normal'"
        
        # Check for global.json in root and handle SDK version mismatch
        has_global_json = (repo_path / "global.json").exists()
        test_cmd = "dotnet test --verbosity normal"
        if has_global_json:
            # Remove global.json to use whatever SDK is available
            logger.info("Found global.json, will remove it to use available SDK")
            test_cmd = f"bash -c 'rm -f global.json && {test_cmd}'"
        
        # Check for solution file in root (.sln, .slnx, .slnf)
        sln_files = list(repo_path.glob("*.sln")) + list(repo_path.glob("*.slnx"))
        if sln_files:
            sln_file = sln_files[0].name
            logger.info(f"Found solution file: {sln_file}")
            test_cmd = f"bash -c 'rm -f global.json && dotnet test {sln_file} --verbosity normal'"
            return test_cmd
        
        # Check for test projects
        test_projects = list(repo_path.rglob("*.[Tt]ests.csproj")) + list(repo_path.rglob("*.[Tt]est.csproj"))
        if test_projects:
            logger.info(f"Found {len(test_projects)} test project(s)")
            return test_cmd
        
        return test_cmd

    # Handle Rust projects - check for Cargo.toml location
    if language == "rust":
        # First check if Cargo.toml exists at repo root
        if (repo_path / "Cargo.toml").exists():
            logger.info("Found Cargo.toml at repo root, using cargo test")
            return "cargo test"
        
        # Check for Cargo.toml in common subdirectory patterns
        # This handles monorepo/polyglot projects where Rust is in a subdirectory
        workspace_candidates = []
        
        # Check from changed files first (most accurate for PRs)
        if changed_files:
            for f in changed_files:
                parts = Path(f).parts
                if len(parts) >= 2 and parts[-1] == "Cargo.toml":
                    # Cargo.toml in a subdirectory
                    subdir = str(Path(*parts[:-1]))
                    if subdir not in workspace_candidates:
                        workspace_candidates.append(subdir)
                elif "Cargo.toml" in f or f.endswith(".rs"):
                    # Rust file - extract subdirectory
                    for i, part in enumerate(parts):
                        potential_subdir = Path(*parts[:i+1])
                        if (repo_path / potential_subdir / "Cargo.toml").exists():
                            subdir = str(potential_subdir)
                            if subdir not in workspace_candidates:
                                workspace_candidates.append(subdir)
                            break
        
        # Also scan for Cargo.toml files in common locations (including nested)
        # Search up to 2 levels deep for better coverage
        for cargo_toml in repo_path.glob("*/Cargo.toml"):
            subdir = cargo_toml.parent.name
            if subdir not in workspace_candidates:
                workspace_candidates.append(subdir)
        
        for cargo_toml in repo_path.glob("*/*/Cargo.toml"):
            # Get relative path from repo_path to the directory containing Cargo.toml
            subdir = str(cargo_toml.parent.relative_to(repo_path))
            if subdir not in workspace_candidates:
                workspace_candidates.append(subdir)
        
        # Prioritize candidates that match changed files
        if changed_files and workspace_candidates:
            # Score each candidate by how many changed files it matches
            def score_candidate(subdir):
                return sum(1 for f in changed_files if f.startswith(subdir + "/") or f.startswith(subdir + "\\"))
            
            workspace_candidates.sort(key=score_candidate, reverse=True)
            best_match = workspace_candidates[0]
            match_count = score_candidate(best_match)
            if match_count > 0:
                logger.info(f"Detected Rust workspace in subdirectory: {best_match} ({match_count} changed files match)")
                return f"cargo test --manifest-path {best_match}/Cargo.toml"
        
        # Use the first valid workspace directory found
        if workspace_candidates:
            rust_subdir = workspace_candidates[0]
            logger.info(f"Detected Rust workspace in subdirectory: {rust_subdir}")
            # Use --manifest-path to run cargo test from the subdirectory
            # This works regardless of current directory
            return f"cargo test --manifest-path {rust_subdir}/Cargo.toml"
        
        # Fallback to default cargo test
        logger.warning("No Cargo.toml found - using default cargo test (may fail)")
        logger.warning("TIP: Use --rust-subdir <subdir> to specify the Rust project location")
        return "cargo test"

    # Return first default command for language
    commands = TEST_COMMANDS.get(language, ["pytest"])
    default_cmd = commands[0]
    
    # Special handling for Java: choose Maven vs Gradle
    if language == "java":
        has_gradle = (
            (repo_path / "build.gradle").exists() or 
            (repo_path / "build.gradle.kts").exists() or
            (repo_path / "settings.gradle").exists() or
            (repo_path / "settings.gradle.kts").exists()
        )
        has_maven = (repo_path / "pom.xml").exists()
        has_gradlew = (repo_path / "gradlew").exists()
        
        if has_gradle and not has_maven:
            # Pure Gradle project - prefer gradlew if available
            if has_gradlew:
                default_cmd = "./gradlew test"
                logger.info("Detected Gradle project with wrapper, using ./gradlew test")
            else:
                default_cmd = "gradle test"
                logger.info("Detected Gradle project (no pom.xml), using gradle test")
        elif has_maven and not has_gradle:
            # Pure Maven project - use mvn test (already default)
            logger.info("Detected Maven project (no build.gradle), using mvn test")
        elif has_gradle and has_maven:
            # Hybrid project - prefer Gradle
            if has_gradlew:
                default_cmd = "./gradlew test"
                logger.info("Detected hybrid Maven/Gradle project, preferring ./gradlew test")
            else:
                default_cmd = "gradle test"
                logger.info("Detected hybrid Maven/Gradle project, preferring gradle test")
        # else: neither found, use default (mvn test)

    # Handle C/autoconf projects
    if language == "c":
        # Check for various test targets in Makefile
        has_makefile = (repo_path / "Makefile").exists() or (repo_path / "GNUmakefile").exists()
        has_build_dir = (repo_path / "build").is_dir()

        # ruby/ruby specific detection
        is_ruby_interpreter = repo_full_name and "ruby/ruby" in repo_full_name.lower()
        if is_ruby_interpreter:
            # Ruby interpreter uses 'make test-all' for comprehensive tests
            # Tests must run from the build directory where Makefile is generated
            logger.info("Detected ruby/ruby project, using make test-all from build dir")
            return "bash -c 'cd /app/repo/build && make test-all TESTS=\"-v\"'"

        # Generic C project test detection
        # For autoconf projects, the Makefile is generated in the build directory
        if has_build_dir:
            # Check for Makefile in build directory
            build_makefile = repo_path / "build" / "Makefile"
            if build_makefile.exists():
                try:
                    makefile_content = build_makefile.read_text()
                    if "test-all:" in makefile_content:
                        logger.info("Found test-all target in build/Makefile")
                        return "bash -c 'cd /app/repo/build && make test-all'"
                    elif "check:" in makefile_content:
                        logger.info("Found check target in build/Makefile")
                        return "bash -c 'cd /app/repo/build && make check'"
                    elif "test:" in makefile_content:
                        logger.info("Found test target in build/Makefile")
                        return "bash -c 'cd /app/repo/build && make test'"
                except Exception:
                    pass
            # Default for autoconf projects with build directory
            logger.info("C project with build directory, using make test from build dir")
            return "bash -c 'cd /app/repo/build && make test'"

        # Check for Makefile in repo root
        if has_makefile:
            makefile_path = repo_path / "Makefile"
            if not makefile_path.exists():
                makefile_path = repo_path / "GNUmakefile"

            if makefile_path.exists():
                try:
                    makefile_content = makefile_path.read_text()
                    if "test-all:" in makefile_content:
                        logger.info("Found test-all target in Makefile")
                        return "make test-all"
                    elif "check:" in makefile_content:
                        logger.info("Found check target in Makefile")
                        return "make check"
                    elif "test:" in makefile_content:
                        logger.info("Found test target in Makefile")
                        return "make test"
                except Exception:
                    pass

        logger.info("C project, using default make test")
        return "make test"

    # For large monorepos, use targeted testing if changed files are available
    if changed_files and language == "go":
        from .test_targeting import generate_targeted_test_command
        targeted_cmd = generate_targeted_test_command(
            language=language,
            changed_files=changed_files,
            repo_path=repo_path,
            default_command=default_cmd,
            logger=logger
        )
        return targeted_cmd
    
    logger.info(f"Using default test command: {default_cmd}")
    return default_cmd


def setup_python_venv(
    repo_path: Path,
    logger: logging.Logger
) -> Optional[Path]:
    """
    Create a Python virtual environment and install dependencies.

    Installation order:
    1. Create venv
    2. Upgrade pip, wheel, setuptools
    3. Install from requirements.txt if exists
    4. Install from pyproject.toml/setup.py if exists
    5. Install pytest and reporting tools

    Args:
        repo_path: Path to repository
        logger: Logger instance

    Returns:
        Path to venv, or None if creation failed
    """
    logger.info("Setting up Python virtual environment")

    venv_path = repo_path / VENV_NAME

    # Remove existing venv if corrupted
    if venv_path.exists():
        import shutil
        shutil.rmtree(venv_path, ignore_errors=True)

    # Create venv
    exit_code, _, stderr = run_command(
        [sys.executable, "-m", "venv", str(venv_path)],
        cwd=repo_path,
        logger=logger
    )

    if exit_code != 0:
        logger.error(f"Failed to create venv: {stderr}")
        return None

    pip = get_pip_executable(venv_path)

    # Upgrade pip and friends
    logger.info("Upgrading pip, wheel, setuptools")
    run_command(
        [pip, "install", "--upgrade", "pip", "wheel", "setuptools"],
        cwd=repo_path,
        logger=logger,
        timeout=300
    )

    # Install from requirements.txt
    req_file = repo_path / "requirements.txt"
    if req_file.exists():
        logger.info("Installing from requirements.txt")
        exit_code, _, stderr = run_command(
            [pip, "install", "-r", "requirements.txt"],
            cwd=repo_path,
            logger=logger,
            timeout=600
        )
        if exit_code != 0:
            logger.warning(f"requirements.txt install had issues: {stderr[:200]}")

    # Install from pyproject.toml
    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        logger.info("Installing from pyproject.toml")
        # Try with dev/test extras first
        exit_code, _, _ = run_command(
            [pip, "install", "-e", ".[dev,test]"],
            cwd=repo_path,
            logger=logger,
            timeout=600
        )
        if exit_code != 0:
            # Fall back to basic install
            run_command(
                [pip, "install", "-e", "."],
                cwd=repo_path,
                logger=logger,
                timeout=600
            )

    # Install from setup.py if no pyproject
    elif (repo_path / "setup.py").exists():
        logger.info("Installing from setup.py")
        run_command(
            [pip, "install", "-e", ".[dev,test]"],
            cwd=repo_path,
            logger=logger,
            timeout=600
        )

    # Install test dependencies
    logger.info("Installing pytest and reporting tools")
    run_command(
        [pip, "install", "pytest", "pytest-json-report", "pytest-html", "pytest-timeout"],
        cwd=repo_path,
        logger=logger,
        timeout=120
    )

    logger.info(f"Virtual environment ready: {venv_path}")
    return venv_path


def setup_environment(
    repo_path: Path,
    language: str,
    logger: logging.Logger
) -> Optional[Path]:
    """
    Set up the environment based on detected language.

    Args:
        repo_path: Path to repository
        language: Detected language
        logger: Logger instance

    Returns:
        Path to venv (for Python) or None
    """
    if language == "python":
        return setup_python_venv(repo_path, logger)

    elif language in ("javascript", "typescript"):
        # Install npm dependencies
        if (repo_path / "package.json").exists():
            logger.info("Installing npm dependencies")
            run_command(
                ["npm", "install"],
                cwd=repo_path,
                logger=logger,
                timeout=600
            )
        return None

    elif language == "go":
        logger.info("Downloading Go modules")
        run_command(
            ["go", "mod", "download"],
            cwd=repo_path,
            logger=logger,
            timeout=300
        )
        return None

    elif language == "rust":
        logger.info("Building Rust dependencies")
        run_command(
            ["cargo", "build"],
            cwd=repo_path,
            logger=logger,
            timeout=600
        )
        return None

    else:
        logger.info(f"No specific environment setup for {language}")
        return None


def detect_error_type(stdout: str, stderr: str) -> Optional[str]:
    """
    Analyze test output to detect the type of error.

    Args:
        stdout: Standard output
        stderr: Standard error

    Returns:
        Error type string or None if no environment error detected
    """
    combined = stdout + stderr

    if "ModuleNotFoundError" in combined or "No module named" in combined:
        return "missing_module"

    if "ImportError" in combined:
        return "import_error"

    if "FileNotFoundError" in combined:
        return "file_not_found"

    if "PermissionError" in combined:
        return "permission_error"

    # Check for database connection errors (should not be retried - needs external service)
    if "ActiveRecord::ConnectionNotEstablished" in combined or \
       "PG::ConnectionBad" in combined or \
       ("connection to server at" in combined and "port 5432" in combined):
        return "database_connection_error"

    if "ConnectionError" in combined or "ConnectionRefusedError" in combined:
        return "network_error"

    if "TimeoutError" in combined:
        return "timeout_error"

    if "OSError" in combined:
        return "os_error"

    if "MemoryError" in combined:
        return "memory_error"

    return None


def extract_missing_modules(output: str) -> List[str]:
    """
    Extract names of missing modules from error output.

    Args:
        output: Combined stdout/stderr

    Returns:
        List of missing module names
    """
    patterns = [
        r"No module named '([^']+)'",
        r"ModuleNotFoundError: No module named '([^']+)'",
        r"ImportError: cannot import name '([^']+)'",
    ]

    modules = []
    for pattern in patterns:
        matches = re.findall(pattern, output)
        modules.extend(matches)

    # Clean up module names (take base package)
    cleaned = []
    for m in modules:
        base = m.split(".")[0]
        if base and base not in cleaned:
            cleaned.append(base)

    return cleaned


def heal_environment(
    repo_path: Path,
    venv_path: Optional[Path],
    test_result: TestResult,
    language: str,
    attempt: int,
    logger: logging.Logger
) -> bool:
    """
    Attempt to heal environment issues based on error analysis.

    Cycles through healing strategies on each attempt.

    Args:
        repo_path: Path to repository
        venv_path: Path to virtual environment (for Python)
        test_result: Previous test result with error info
        language: Repository language
        attempt: Current attempt number (0-indexed)
        logger: Logger instance

    Returns:
        True if healing was attempted, False otherwise
    """
    error_type = test_result.error_type or detect_error_type(
        test_result.stdout, test_result.stderr
    )

    strategy = HEALING_STRATEGIES[attempt % len(HEALING_STRATEGIES)]
    logger.info(f"Healing attempt {attempt + 1}: strategy='{strategy}', error_type='{error_type}'")

    if language != "python" or not venv_path:
        logger.info("Healing not supported for this language/environment")
        return False

    pip = get_pip_executable(venv_path)
    combined_output = test_result.stdout + test_result.stderr

    # Strategy: Install missing modules
    if strategy == "install_missing" or error_type == "missing_module":
        missing = extract_missing_modules(combined_output)
        if missing:
            logger.info(f"Installing missing modules: {missing}")
            for module in missing:
                run_command(
                    [pip, "install", module],
                    cwd=repo_path,
                    logger=logger,
                    timeout=120
                )
            return True

    # Strategy: Reinstall all dependencies
    elif strategy == "reinstall_deps":
        if (repo_path / "requirements.txt").exists():
            logger.info("Force reinstalling requirements.txt")
            run_command(
                [pip, "install", "--force-reinstall", "-r", "requirements.txt"],
                cwd=repo_path,
                logger=logger,
                timeout=600
            )
            return True

    # Strategy: Clear cache
    elif strategy == "clear_cache":
        logger.info("Clearing pip cache")
        run_command([pip, "cache", "purge"], cwd=repo_path, logger=logger)
        return True

    # Strategy: Pin versions (no-deps then deps)
    elif strategy == "pin_versions":
        if (repo_path / "requirements.txt").exists():
            logger.info("Reinstalling with version pinning")
            run_command(
                [pip, "install", "--no-deps", "-r", "requirements.txt"],
                cwd=repo_path,
                logger=logger,
                timeout=300
            )
            run_command(
                [pip, "install", "-r", "requirements.txt"],
                cwd=repo_path,
                logger=logger,
                timeout=300
            )
            return True

    # Strategy: Rebuild wheels
    elif strategy == "rebuild_wheels":
        if (repo_path / "requirements.txt").exists():
            logger.info("Rebuilding from source (no binary)")
            run_command(
                [pip, "install", "--no-binary", ":all:", "-r", "requirements.txt"],
                cwd=repo_path,
                logger=logger,
                timeout=900
            )
            return True

    # Strategy: Set environment variables
    elif strategy == "set_env_vars":
        logger.info("Setting environment variables")
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        os.environ["PYTHONUNBUFFERED"] = "1"
        os.environ["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        return True

    return False
