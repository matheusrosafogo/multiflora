"""Creation of the Jinja2 environment used to render device configurations."""

from jinja2 import Environment, FileSystemLoader


class JinjaEnv:
    """Factory for Jinja2 templates loaded from the filesystem."""

    @staticmethod
    def create(template_path, logger):
        """Load the Jinja2 template located at ``template_path``.

        The environment searches for templates in the directory that contains
        the template file. ``trim_blocks`` and ``lstrip_blocks`` are enabled
        so block tags do not leave blank lines in the rendered output.

        Args:
            template_path (pathlib.Path): Path to the ``.j2`` template file.
            logger (logging.Logger): Logger for progress and error reporting.

        Returns:
            jinja2.Template | None: The loaded template, or ``None`` when the
            template cannot be read or parsed (the error is logged).
        """
        try:
            logger.debug("Loading environment settings...")
            env = Environment(
                loader=FileSystemLoader(template_path.parent),
                trim_blocks=True,
                lstrip_blocks=True,
            )
            logger.debug("Returning template...")
            template = env.get_template(template_path.name)
            return template
        except Exception as exc:
            logger.error("Failed to load Jinja2 template '%s': %s", template_path, exc)
            return None
