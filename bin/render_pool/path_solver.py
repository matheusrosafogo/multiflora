"""Resolution of the filesystem paths used by the rendering pipeline."""

import sys
from pathlib import Path

# Keep imports relative to bin/ working when this module is executed directly.
sys.path.append(str(Path(__file__).resolve().parent.parent))


class PathSolver:
    """Static resolver for the template, output and inventory paths."""

    @staticmethod
    def solve(path, template_file, inventory_file, logger):
        """Resolve the project paths and make sure the output directory exists.

        The template path is resolved against the current working directory;
        a ``cfg`` subdirectory is created inside ``path`` to receive the
        rendered files.

        Args:
            path (str): Configuration directory that holds the ``cfg`` folder.
            template_file (str): Path to the Jinja2 template.
            inventory_file (str): Path to the inventory YAML file.
            logger (logging.Logger): Logger for debug messages.

        Returns:
            tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
            ``(template_path, cfg_dir, inventory_path)``, where ``cfg_dir``
            has been created on disk. Note that ``inventory_path`` keeps its
            original (possibly relative) form.
        """
        logger.debug("Resolving template path...")
        template_path = Path(template_file).resolve()
        logger.debug("Resolving cfg path...")
        cfg_dir = Path(path).resolve() / "cfg"
        logger.debug("Creating cfg directory...")
        cfg_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("Resolving inventory path...")
        inventory_path = Path(inventory_file)

        return template_path, cfg_dir, inventory_path
