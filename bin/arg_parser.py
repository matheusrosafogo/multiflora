"""Command-line argument parsing for the Multiflora backend.

Defines the positional configuration paths and the mutually exclusive
execution-mode flags (``--dry-run``, ``--simulate``, ``--deploy``) consumed
by ``main.py`` and by the GUI worker process.
"""

import argparse

class ArgParser:
    """Static wrapper around the ``argparse`` definition for the backend CLI."""

    @staticmethod
    def parse():
        """Read and process the arguments passed by the user on the command line.

        Returns:
            argparse.Namespace: Parsed arguments with the attributes ``path``,
            ``inventory``, ``data_file``, ``template``, ``name``, ``dry_run``,
            ``simulate`` and ``deploy``.
        """

        argument_parser = argparse.ArgumentParser(
            description="Render and deploy network device configurations."
        )

        argument_parser.add_argument(
            "path", help="Path to the configuration directory (config/)"
        )

        argument_parser.add_argument(
            "inventory", help="Inventory file (inventory.yaml)"
        )

        argument_parser.add_argument(
            "data_file", help="File with the template variables (data.yaml)"
        )

        argument_parser.add_argument("template", help="Jinja2 template")

        argument_parser.add_argument("name", help="queue name")

        group = argument_parser.add_mutually_exclusive_group()
        group.add_argument(
            "--dry-run",
            action="store_true",
            help="Render the configurations without deploying them",
        )
        group.add_argument(
            "--simulate",
            action="store_true",
            help="Reserved simulation mode (currently ignored by the backend)",
        )
        group.add_argument(
            "--deploy",
            action="store_true",
            help="Render and deploy the configurations to the devices",
        )

        args = argument_parser.parse_args()

        return args
