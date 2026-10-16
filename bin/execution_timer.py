"""Stopwatch used to measure the duration of a block of code."""

from time import perf_counter

class ExecutionTimer:
    """Timer based on ``time.perf_counter`` with start/stop semantics."""

    def __init__(self):
        """Initialize the timer in the stopped state."""
        self._start_time = None
        self._end_time = None

    def start(self):
        """Start (or restart) the measurement.

        Returns:
            ExecutionTimer: ``self``, so calls can be chained as
            ``ExecutionTimer().start()``.
        """
        self._start_time = perf_counter()
        self._end_time = None
        return self

    def stop(self):
        """Stop the measurement and return the elapsed time.

        Returns:
            float: Seconds elapsed since ``start()``, or ``0.0`` when the
            timer was never started.
        """
        if self._start_time is None:
            return 0.0

        self._end_time = perf_counter()
        return self.elapsed

    @property
    def elapsed(self):
        """float: Seconds elapsed since ``start()``.

        While the timer is running the value is computed against the current
        instant; after ``stop()`` it is frozen.
        """
        if self._start_time is None:
            return 0.0

        end_time = self._end_time if self._end_time is not None else perf_counter()
        return end_time - self._start_time

    def format_elapsed(self):
        """Format the elapsed time as ``HH:MM:SS.ss``.

        Returns:
            str: Human-readable duration string.
        """
        seconds = self.elapsed
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{int(hours):02d}:{int(minutes):02d}:{seconds:05.2f}"
