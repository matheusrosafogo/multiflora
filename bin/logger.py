"""Creation and configuration of the application logger.

``Logger.create`` prepares a logger that writes simultaneously to the
console (stream handler) and to a timestamped file inside the ``logs``
subdirectory of the given configuration path.
"""

import logging
from datetime import datetime
from pathlib import Path

class Logger:
    """Factory for the application logging instance."""

    @staticmethod
    def create(path):
        """Create the logger and attach its output handlers.

        The ``logs`` directory is created inside ``path`` when missing, and a
        log file named after the current timestamp is opened in append mode.
        Handlers already attached are removed first, so repeated calls do not
        duplicate output lines.

        Args:
            path (str): Base configuration directory that receives ``logs/``.

        Returns:
            logging.Logger: Configured logger with DEBUG level.
        """
        log_dir = Path(path).resolve() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        logger = logging.getLogger(__name__)
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()

        streamhandler = logging.StreamHandler()
        streamhandler.setLevel(logging.DEBUG)

        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        streamhandler.setFormatter(formatter)

        logger.addHandler(streamhandler)

        log_file = log_dir / f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"  # noqa: DTZ005
        filehandler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        filehandler.setLevel(logging.DEBUG)
        filehandler.setFormatter(formatter)
        logger.addHandler(filehandler)

        return logger
