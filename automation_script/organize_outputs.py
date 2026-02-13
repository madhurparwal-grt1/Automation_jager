#!/usr/bin/env python3
"""
Output Dataset Organizer

Creates a comprehensive Output_dataset folder with:
- Docker images
- Test results (BASE and PATCHED)
- Metadata and reports
- Analysis and summaries
- FAIL_TO_PASS and PASS_TO_PASS categorization
"""

import json
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List


def create_output_structure(workspace_path: Path, output_root: Path, logger: logging.Logger) -> bool:
    """
    Create comprehensive output dataset structure.
    
    Output_dataset/
    ├── docker_images/
    │   ├── image.tar
    │   └── image_info.json
    ├── results/
    │   ├── base_tests/
    │   │   ├── result.json
    │   │   └── results.jsonl
    │   └── patched_tests/
    │       ├── result.json
    │       └── results.jsonl
    ├── reports/
    │   ├── summary_report.json
    │   ├── test_categorization.json
    │   └── evaluation_report.md
    ├── metadata/
    │   ├── instance.json
    │   ├── state.json
    │   └── pr_info.json
    ├── patches/
    │   ├── pr.patch
    │   └── test.patch
    ├── analysis/
    │   ├── pass_to_pass.json
    │   ├── fail_to_pass.json
    │   └── test_analysis.md
    └── logs/
        ├── part1_build_and_base.log
        └── part2_patch_and_evaluate.log
    """
    logger.info("=" * 80)
    logger.info("CREATING OUTPUT DATASET STRUCTURE")
    logger.info("=" * 80)
    
    # Create output directory
    output_root.mkdir(parents=True, exist_ok=True)
    logger.info(f"Output directory: {output_root}")
    
    # Create subdirectories
    subdirs = [
        "docker_images",
        "results/base_tests",
        "results/patched_tests",
        "reports",
        "metadata",
        "patches",
        "analysis",
        "logs"
    ]
    
    for subdir in subdirs:
        (output_root / subdir).mkdir(parents=True, exist_ok=True)
    
    logger.info("Created directory structure")
    
    # Copy files
    success = True
    
    # 1. Docker images
    logger.info("Copying Docker images...")
    docker_src = workspace_path / "docker_images"
    if docker_src.exists():
        for img_file in docker_src.glob("*.tar"):
            shutil.copy2(img_file, output_root / "docker_images")
            logger.info(f"  Copied: {img_file.name}")
    
    # 2. Results
    logger.info("Copying test results...")
    artifacts_src = workspace_path / "artifacts"
    if artifacts_src.exists():
        # BASE tests
        base_src = artifacts_src / "base"
        if base_src.exists():
            for file in base_src.glob("*"):
                if file.is_file():
                    shutil.copy2(file, output_root / "results" / "base_tests")
                    logger.info(f"  Copied: base_tests/{file.name}")
        
        # PATCHED tests
        pr_src = artifacts_src / "pr"
        if pr_src.exists():
            for file in pr_src.glob("*"):
                if file.is_file():
                    shutil.copy2(file, output_root / "results" / "patched_tests")
                    logger.info(f"  Copied: patched_tests/{file.name}")
    
    # 3. Metadata
    logger.info("Copying metadata...")
    metadata_src = workspace_path / "metadata"
    if metadata_src.exists():
        for file in metadata_src.glob("*.json"):
            shutil.copy2(file, output_root / "metadata")
            logger.info(f"  Copied: metadata/{file.name}")
    
    # State file
    state_file = workspace_path / "state.json"
    if state_file.exists():
        shutil.copy2(state_file, output_root / "metadata")
        logger.info(f"  Copied: metadata/state.json")
    
    # 4. Patches
    logger.info("Copying patches...")
    patches_src = workspace_path / "patches"
    if patches_src.exists():
        for file in patches_src.glob("*"):
            if file.is_file():
                shutil.copy2(file, output_root / "patches")
                logger.info(f"  Copied: patches/{file.name}")
    
    # 5. Logs
    logger.info("Copying logs...")
    logs_src = workspace_path / "logs"
    if logs_src.exists():
        for file in logs_src.glob("*.log"):
            shutil.copy2(file, output_root / "logs")
            logger.info(f"  Copied: logs/{file.name}")
    
    return success


def generate_test_categorization(workspace_path: Path, output_root: Path, logger: logging.Logger) -> Dict[str, Any]:
    """Generate FAIL_TO_PASS and PASS_TO_PASS categorization."""
    logger.info("Generating test categorization...")
    
    # Load test results
    base_result_file = workspace_path / "artifacts" / "base" / "result.json"
    pr_result_file = workspace_path / "artifacts" / "pr" / "result.json"
    
    categorization = {
        "FAIL_TO_PASS": [],
        "PASS_TO_PASS": [],
        "PASS_TO_FAIL": [],
        "FAIL_TO_FAIL": [],
        "summary": {
            "total_tests": 0,
            "tests_fixed": 0,
            "tests_maintained": 0,
            "tests_broken": 0,
            "tests_still_failing": 0
        }
    }
    
    try:
        # Load BASE results
        if base_result_file.exists():
            with open(base_result_file) as f:
                base_result = json.load(f)
        else:
            logger.warning("BASE result file not found")
            base_result = {"tests_passed": [], "tests_failed": []}
        
        # Load PR results
        if pr_result_file.exists():
            with open(pr_result_file) as f:
                pr_result = json.load(f)
        else:
            logger.warning("PR result file not found")
            pr_result = {"tests_passed": [], "tests_failed": []}
        
        # Categorize tests
        base_passed = set(base_result.get("tests_passed", []))
        base_failed = set(base_result.get("tests_failed", []))
        pr_passed = set(pr_result.get("tests_passed", []))
        pr_failed = set(pr_result.get("tests_failed", []))
        
        # FAIL_TO_PASS: Failed in BASE, passed in PR
        fail_to_pass = sorted(list(base_failed & pr_passed))
        categorization["FAIL_TO_PASS"] = fail_to_pass
        
        # PASS_TO_PASS: Passed in both
        pass_to_pass = sorted(list(base_passed & pr_passed))
        categorization["PASS_TO_PASS"] = pass_to_pass
        
        # PASS_TO_FAIL: Passed in BASE, failed in PR (regressions)
        pass_to_fail = sorted(list(base_passed & pr_failed))
        categorization["PASS_TO_FAIL"] = pass_to_fail
        
        # FAIL_TO_FAIL: Failed in both
        fail_to_fail = sorted(list(base_failed & pr_failed))
        categorization["FAIL_TO_FAIL"] = fail_to_fail
        
        # Summary
        categorization["summary"] = {
            "total_tests": len(base_passed | base_failed | pr_passed | pr_failed),
            "tests_fixed": len(fail_to_pass),
            "tests_maintained": len(pass_to_pass),
            "tests_broken": len(pass_to_fail),
            "tests_still_failing": len(fail_to_fail)
        }
        
        logger.info(f"  FAIL_TO_PASS: {len(fail_to_pass)} tests")
        logger.info(f"  PASS_TO_PASS: {len(pass_to_pass)} tests")
        logger.info(f"  PASS_TO_FAIL: {len(pass_to_fail)} tests")
        logger.info(f"  FAIL_TO_FAIL: {len(fail_to_fail)} tests")
        
    except Exception as e:
        logger.error(f"Error categorizing tests: {e}")
    
    # Save categorization
    with open(output_root / "reports" / "test_categorization.json", "w") as f:
        json.dump(categorization, f, indent=2)
    
    return categorization


def generate_analysis_files(categorization: Dict[str, Any], output_root: Path, logger: logging.Logger):
    """Generate detailed analysis files."""
    logger.info("Generating analysis files...")
    
    # PASS_TO_PASS analysis
    pass_to_pass_data = {
        "description": "Tests that passed in both BASE and PATCHED versions",
        "count": len(categorization["PASS_TO_PASS"]),
        "tests": categorization["PASS_TO_PASS"],
        "significance": "These tests verify that existing functionality was not broken by the PR"
    }
    
    with open(output_root / "analysis" / "pass_to_pass.json", "w") as f:
        json.dump(pass_to_pass_data, f, indent=2)
    
    # FAIL_TO_PASS analysis
    fail_to_pass_data = {
        "description": "Tests that failed in BASE but passed in PATCHED version",
        "count": len(categorization["FAIL_TO_PASS"]),
        "tests": categorization["FAIL_TO_PASS"],
        "significance": "These tests demonstrate bugs fixed or features added by the PR"
    }
    
    with open(output_root / "analysis" / "fail_to_pass.json", "w") as f:
        json.dump(fail_to_pass_data, f, indent=2)
    
    # Test analysis markdown
    analysis_md = f"""# Test Analysis Report

## Summary
- **Total Tests**: {categorization["summary"]["total_tests"]}
- **Tests Fixed (FAIL→PASS)**: {categorization["summary"]["tests_fixed"]}
- **Tests Maintained (PASS→PASS)**: {categorization["summary"]["tests_maintained"]}
- **Tests Broken (PASS→FAIL)**: {categorization["summary"]["tests_broken"]}
- **Tests Still Failing (FAIL→FAIL)**: {categorization["summary"]["tests_still_failing"]}

## Test Categorization

### ✅ FAIL_TO_PASS ({len(categorization["FAIL_TO_PASS"])} tests)
Tests that were **fixed** by this PR:
"""
    
    for test in categorization["FAIL_TO_PASS"]:
        analysis_md += f"- `{test}`\n"
    
    if not categorization["FAIL_TO_PASS"]:
        analysis_md += "*No tests fixed*\n"
    
    analysis_md += f"""
### ✓ PASS_TO_PASS ({len(categorization["PASS_TO_PASS"])} tests)
Tests that **remain passing** (no regression):
"""
    
    for test in categorization["PASS_TO_PASS"][:10]:  # Show first 10
        analysis_md += f"- `{test}`\n"
    
    if len(categorization["PASS_TO_PASS"]) > 10:
        analysis_md += f"*...and {len(categorization['PASS_TO_PASS']) - 10} more*\n"
    
    if not categorization["PASS_TO_PASS"]:
        analysis_md += "*No tests passing*\n"
    
    analysis_md += f"""
### ❌ PASS_TO_FAIL ({len(categorization["PASS_TO_FAIL"])} tests)
Tests that **regressed** (new failures):
"""
    
    for test in categorization["PASS_TO_FAIL"]:
        analysis_md += f"- `{test}`\n"
    
    if not categorization["PASS_TO_FAIL"]:
        analysis_md += "*No regressions*\n"
    
    analysis_md += f"""
### ⚠ FAIL_TO_FAIL ({len(categorization["FAIL_TO_FAIL"])} tests)
Tests that **still fail** (pre-existing issues):
"""
    
    for test in categorization["FAIL_TO_FAIL"][:10]:  # Show first 10
        analysis_md += f"- `{test}`\n"
    
    if len(categorization["FAIL_TO_FAIL"]) > 10:
        analysis_md += f"*...and {len(categorization['FAIL_TO_FAIL']) - 10} more*\n"
    
    if not categorization["FAIL_TO_FAIL"]:
        analysis_md += "*No failing tests*\n"
    
    analysis_md += """
## Evaluation Criteria

**PR Quality Score:**
- ✅ Fixes bugs: +1 point per FAIL_TO_PASS test
- ✅ No regressions: 0 PASS_TO_FAIL tests
- ✅ Maintains stability: High PASS_TO_PASS ratio

**Verdict:**
"""
    
    if categorization["summary"]["tests_fixed"] > 0 and categorization["summary"]["tests_broken"] == 0:
        analysis_md += "✅ **EXCELLENT** - Fixes issues without introducing regressions\n"
    elif categorization["summary"]["tests_broken"] == 0 and categorization["summary"]["tests_maintained"] > 0:
        analysis_md += "✅ **GOOD** - No regressions, maintains existing functionality\n"
    elif categorization["summary"]["tests_broken"] > 0:
        analysis_md += "⚠ **NEEDS ATTENTION** - Introduces regressions\n"
    else:
        analysis_md += "ℹ️ **NEUTRAL** - No significant test changes\n"
    
    with open(output_root / "analysis" / "test_analysis.md", "w") as f:
        f.write(analysis_md)
    
    logger.info("Analysis files generated")


def generate_summary_report(workspace_path: Path, categorization: Dict[str, Any], output_root: Path, logger: logging.Logger):
    """Generate comprehensive summary report."""
    logger.info("Generating summary report...")
    
    # Load metadata and state
    try:
        with open(workspace_path / "metadata" / "instance.json") as f:
            metadata = json.load(f)
    except:
        metadata = {}
    
    try:
        with open(workspace_path / "state.json") as f:
            state = json.load(f)
    except:
        state = {}
    
    # Create summary
    summary = {
        "generated_at": datetime.now().isoformat(),
        "pr_information": {
            "pr_url": state.get("pr_url", "N/A"),
            "repository": metadata.get("repo", "N/A"),
            "pr_number": state.get("pr_number", "N/A"),
            "base_commit": metadata.get("base_commit", "N/A"),
            "pr_commit": state.get("pr_commit", "N/A"),
            "language": metadata.get("language", "N/A"),
        },
        "docker_image": {
            "tag": state.get("docker_image", "N/A"),
            "uri": metadata.get("image_storage_uri", "N/A"),
        },
        "test_execution": {
            "test_command": metadata.get("test_command", "N/A"),
            "base_tests": state.get("base_result", {}),
        },
        "test_categorization": categorization,
        "problem_statement": metadata.get("problem_statement", "N/A")[:500] + "..." if len(metadata.get("problem_statement", "")) > 500 else metadata.get("problem_statement", "N/A"),
    }
    
    with open(output_root / "reports" / "summary_report.json", "w") as f:
        json.dump(summary, f, indent=2)
    
    logger.info("Summary report generated")
    
    return summary


def generate_evaluation_report(summary: Dict[str, Any], output_root: Path, logger: logging.Logger):
    """Generate human-readable evaluation report."""
    logger.info("Generating evaluation report...")
    
    report = f"""# PR Evaluation Report
Generated: {summary["generated_at"]}

## Pull Request Information
- **Repository**: {summary["pr_information"]["repository"]}
- **PR Number**: {summary["pr_information"]["pr_number"]}
- **PR URL**: {summary["pr_information"]["pr_url"]}
- **Language**: {summary["pr_information"]["language"]}
- **BASE Commit**: {summary["pr_information"]["base_commit"][:12]}
- **PR Commit**: {summary["pr_information"]["pr_commit"][:12]}

## Docker Image
- **Tag**: `{summary["docker_image"]["tag"]}`
- **URI**: {summary["docker_image"]["uri"]}

## Test Execution
- **Command**: `{summary["test_execution"]["test_command"]}`
- **BASE Tests**:
  - Passed: {summary["test_execution"]["base_tests"].get("passed", 0)}
  - Failed: {summary["test_execution"]["base_tests"].get("failed", 0)}
  - Duration: {summary["test_execution"]["base_tests"].get("duration", 0):.2f}s

## Test Results Summary
- **FAIL_TO_PASS**: {summary["test_categorization"]["summary"]["tests_fixed"]} tests fixed ✅
- **PASS_TO_PASS**: {summary["test_categorization"]["summary"]["tests_maintained"]} tests maintained ✓
- **PASS_TO_FAIL**: {summary["test_categorization"]["summary"]["tests_broken"]} regressions ❌
- **FAIL_TO_FAIL**: {summary["test_categorization"]["summary"]["tests_still_failing"]} still failing ⚠

## Problem Statement
{summary["problem_statement"]}

## Files Included
- `docker_images/` - Compressed Docker images
- `results/` - BASE and PATCHED test results
- `reports/` - Summary and categorization reports
- `metadata/` - Instance metadata and state
- `patches/` - PR patch files
- `analysis/` - Detailed test analysis
- `logs/` - Build and execution logs

---
*For detailed analysis, see `analysis/test_analysis.md`*
"""
    
    with open(output_root / "reports" / "evaluation_report.md", "w") as f:
        f.write(report)
    
    logger.info("Evaluation report generated")


def generate_docker_image_info(workspace_path: Path, output_root: Path, logger: logging.Logger):
    """Generate Docker image information file."""
    logger.info("Generating Docker image info...")
    
    try:
        with open(workspace_path / "state.json") as f:
            state = json.load(f)
        
        docker_info = {
            "image_tag": state.get("docker_image", "N/A"),
            "image_uri": state.get("image_uri", "N/A"),
            "base_commit": state.get("base_commit", "N/A"),
            "language": state.get("language", "N/A"),
            "build_timestamp": state.get("part1_completed", False),
            "usage_instructions": {
                "load_from_archive": f"docker load < {Path(state.get('image_uri', '')).name}",
                "run_container": f"docker run -it {state.get('docker_image', 'IMAGE_TAG')} bash",
                "run_tests": f"docker run --rm {state.get('docker_image', 'IMAGE_TAG')} {state.get('test_command', 'npm test')}"
            }
        }
        
        with open(output_root / "docker_images" / "image_info.json", "w") as f:
            json.dump(docker_info, f, indent=2)
        
        logger.info("Docker image info generated")
    except Exception as e:
        logger.error(f"Error generating Docker image info: {e}")


def main():
    """Main entry point."""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python organize_outputs.py <workspace_path> [output_path]")
        print("\nExample:")
        print("  python organize_outputs.py /tmp/test_workspace ./Output_dataset")
        sys.exit(1)
    
    workspace_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("./Output_dataset")
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger("organize_outputs")
    
    logger.info("=" * 80)
    logger.info("OUTPUT DATASET ORGANIZER")
    logger.info("=" * 80)
    logger.info(f"Workspace: {workspace_path}")
    logger.info(f"Output: {output_path}")
    logger.info("")
    
    # Create output structure
    create_output_structure(workspace_path, output_path, logger)
    
    # Generate test categorization
    categorization = generate_test_categorization(workspace_path, output_path, logger)
    
    # Generate analysis files
    generate_analysis_files(categorization, output_path, logger)
    
    # Generate summary report
    summary = generate_summary_report(workspace_path, categorization, output_path, logger)
    
    # Generate evaluation report
    generate_evaluation_report(summary, output_path, logger)
    
    # Generate Docker image info
    generate_docker_image_info(workspace_path, output_path, logger)
    
    logger.info("")
    logger.info("=" * 80)
    logger.info("OUTPUT DATASET CREATED SUCCESSFULLY")
    logger.info("=" * 80)
    logger.info(f"Location: {output_path.absolute()}")
    logger.info("")
    logger.info("Contents:")
    logger.info(f"  - Docker images: {len(list((output_path / 'docker_images').glob('*')))} files")
    logger.info(f"  - Test results: BASE + PATCHED")
    logger.info(f"  - Reports: 3 files")
    logger.info(f"  - Analysis: PASS_TO_PASS + FAIL_TO_PASS")
    logger.info(f"  - Metadata: {len(list((output_path / 'metadata').glob('*')))} files")
    logger.info("")


if __name__ == "__main__":
    main()
