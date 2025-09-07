#!/usr/bin/env python3

import sys
import unittest
import argparse
import logging
from typing import List, Optional

def discover_and_run_tests(test_pattern: str = 'test_*.py', 
                          verbosity: int = 1,
                          failfast: bool = False,
                          specific_tests: Optional[List[str]] = None) -> bool:
    """
    Discovers and runs tests in the tests directory
    
    Args:
        test_pattern: Pattern to match test files
        verbosity: Test output verbosity (0=quiet, 1=normal, 2=verbose)
        failfast: Stop on first failure
        specific_tests: Run only specific test classes/methods
    
    Returns:
        True if all tests passed, False otherwise
    """
    
    # Configure logging to reduce noise during tests
    logging.getLogger().setLevel(logging.WARNING)
    
    # Discover tests
    loader = unittest.TestLoader()
    
    if specific_tests:
        # Load specific tests
        suite = unittest.TestSuite()
        for test_name in specific_tests:
            try:
                # Try to load as module.TestClass.test_method
                suite.addTest(loader.loadTestsFromName(test_name))
            except Exception as e:
                print(f"Warning: Could not load test '{test_name}': {e}")
    else:
        # Discover all tests
        suite = loader.discover('tests', pattern=test_pattern)
    
    # Run tests
    runner = unittest.TextTestRunner(
        verbosity=verbosity,
        failfast=failfast,
        buffer=True  # Capture stdout/stderr during tests
    )
    
    result = runner.run(suite)
    
    # Print summary
    total_tests = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    skipped = len(result.skipped) if hasattr(result, 'skipped') else 0
    
    print(f"\n{'='*60}")
    print(f"TEST SUMMARY")
    print(f"{'='*60}")
    print(f"Total tests run: {total_tests}")
    print(f"Successful: {total_tests - failures - errors}")
    print(f"Failures: {failures}")
    print(f"Errors: {errors}")
    print(f"Skipped: {skipped}")
    print(f"{'='*60}")
    
    return len(result.failures) == 0 and len(result.errors) == 0


def main():
    parser = argparse.ArgumentParser(description='Proxy Load Balancer Test Runner')
    parser.add_argument('-v', '--verbose', action='store_true', 
                       help='Verbose test output')
    parser.add_argument('-f', '--failfast', action='store_true',
                       help='Stop on first failure')
    parser.add_argument('-p', '--pattern', default='test_*.py',
                       help='Test file pattern (default: test_*.py)')
    parser.add_argument('tests', nargs='*',
                       help='Specific tests to run (e.g., tests.test_system_integration.TestSystemIntegration.test_startup_and_balancing)')
    
    args = parser.parse_args()
    
    verbosity = 2 if args.verbose else 1
    
    print("Starting Proxy Load Balancer Test Suite...")
    print(f"Pattern: {args.pattern}")
    if args.tests:
        print(f"Specific tests: {args.tests}")
    print()
    
    success = discover_and_run_tests(
        test_pattern=args.pattern,
        verbosity=verbosity,
        failfast=args.failfast,
        specific_tests=args.tests if args.tests else None
    )
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
