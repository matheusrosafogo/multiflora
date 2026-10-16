"""Rendering of the per-device configuration files."""

import os

import yaml


class Renderer:
    """Static renderer that turns template + data into ``.cfg`` files."""

    @staticmethod
    def render(template, data_file, cfg_dir, logger, inventory_hosts=None):
        """Render one configuration file per entry of the data file.

        Each top-level key of the data file is treated as a device name. The
        inventory is searched (case-insensitively, by host name or by its
        ``hostname`` field) to find the matching entry and, from it, the
        ``config_file`` name to use; the name receives a ``.cfg`` extension
        when missing. The files are written into ``cfg_dir``.

        Args:
            template (jinja2.Template): Template applied to every device.
            data_file (str): YAML file with the per-device variables.
            cfg_dir (str): Directory that receives the rendered files.
            logger (logging.Logger): Logger for progress and error reporting.
            inventory_hosts (dict, optional): Pre-loaded ``inventory.yaml``
                ``all.hosts`` mapping used to resolve ``config_file`` names.

        Returns:
            bool: ``True`` when every device was rendered; ``False`` when the
            data file could not be loaded or at least one device failed.
        """
        logger.debug("Opening data file...")
        try:
            with open(data_file, "r", encoding="utf-8") as file:
                logger.debug("Loading data file...")
                content = yaml.safe_load(file) or {}
                logger.debug("Successfully opened data file")
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            logger.error("Failed to load data file '%s': %s", data_file, exc)
            return False

        inventory_hosts = inventory_hosts or {}
        all_rendered = True

        for key, device_data in content.items():
            device_name = str(key).strip()
            inventory_entry = inventory_hosts.get(device_name.lower()) or next(
                (
                    params
                    for name, params in inventory_hosts.items()
                    if str(name).strip().lower() == device_name.lower()
                    or str(params.get("hostname", "")).strip().lower()
                    == device_name.lower()
                ),
                None,
            )

            config_file_name = (
                str(inventory_entry.get("config_file", "")).strip()
                if inventory_entry
                else device_name
            )
            if not config_file_name.lower().endswith(".cfg"):
                config_file_name = f"{config_file_name}.cfg"

            logger.debug(f"Solving {config_file_name}...")
            file_destination = os.path.join(cfg_dir, config_file_name)

            try:
                logger.debug(f"Creating {config_file_name}...")
                with open(file_destination, "w", encoding="utf-8") as output_file:
                    output_file.write(template.render(device_data))
                    logger.debug(f"Successfully created {config_file_name}")
            except Exception as exc:
                logger.error("Failed to render %s: %s", config_file_name, exc)
                all_rendered = False

        return all_rendered
