"""Expected failures with safe messages that never include source record contents."""


class PipelineError(Exception):
    """An expected pipeline failure suitable for a run report."""


class SourceError(PipelineError):
    """The source could not be fetched reliably."""


class ContractError(PipelineError):
    """The source does not satisfy the required data contract."""


class QualityError(PipelineError):
    """The transformed dataset failed one or more quality checks."""
