"""Comparison-only CPU allowance; preserve historical executor source bytes."""

from cognition.outcome_worker import IsolatedPython


class ComparisonPython(IsolatedPython):
    def _command(self, work, driver):
        # Historical scientific protocols pin the common executor's exact file.
        # Change only this new benchmark's trusted launch text. Namespace,
        # mounts, file/descriptor limits and the parent's wall bound are reused.
        original = "resource.setrlimit(resource.RLIMIT_CPU,(8,8))\n"
        if driver.count(original) != 1:
            raise ValueError("Unrecognized executor CPU boundary; refuse to launch")
        driver = driver.replace(
            original, "resource.setrlimit(resource.RLIMIT_CPU,(55,55))\n", 1
        )
        return super()._command(work, driver)
