from __future__ import annotations


class LiveExecutionDisabled(RuntimeError):
    pass


class LiveExecutor:
    """Placeholder for real venue order submission.

    Live trading must remain disabled until API verification is complete and
    config explicitly opts in. This class exists so the execution boundary is
    clear from v1 instead of being mixed into strategy code.
    """

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def execute(self, *_args: object, **_kwargs: object) -> None:
        if not self.enabled:
            raise LiveExecutionDisabled("live execution is disabled by default")
        raise NotImplementedError("live venue order submission is not implemented yet")
