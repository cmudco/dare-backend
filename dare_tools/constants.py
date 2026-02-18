"""
DARE Tools constants.
"""


class ToolCategory:
    """Categories for organizing DARE tools."""
    VISUALIZATION = "visualization"
    ANALYSIS = "analysis"
    UTILITY = "utility"

    @classmethod
    def choices(cls):
        return [
            (cls.VISUALIZATION, "Visualization"),
            (cls.ANALYSIS, "Analysis"),
            (cls.UTILITY, "Utility"),
        ]


class ExecutionStatus:
    """Status of a tool execution."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

    @classmethod
    def choices(cls):
        return [
            (cls.PENDING, "Pending"),
            (cls.RUNNING, "Running"),
            (cls.COMPLETED, "Completed"),
            (cls.FAILED, "Failed"),
        ]
