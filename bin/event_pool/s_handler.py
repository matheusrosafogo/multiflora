"""Asynchronous device connection and configuration deployment.

Uses ``scrapli``/``asyncssh`` to connect to the network devices listed in the
inventory and apply the rendered configuration files, reporting every step
through the logger provided by the caller.
"""

import asyncio
import logging
import re
import warnings
from pathlib import Path

import yaml
from scrapli.driver import AsyncNetworkDriver
from scrapli.driver.core import AsyncEOSDriver
from scrapli.exceptions import (
    ScrapliAuthenticationFailed,
    ScrapliCommandFailure,
    ScrapliConnectionError,
    ScrapliConnectionNotOpened,
    ScrapliTimeout,
)
from scrapli_community.nokia.srlinux.async_driver import (
    default_async_on_close,
    default_async_on_open,
)
from scrapli_community.nokia.srlinux.nokia_srlinux import DEFAULT_PRIVILEGE_LEVELS

# Scrapli emits its own deprecation warnings; keep the console output clean.
warnings.filterwarnings("ignore", module="scrapli")

# SR Linux asks for a terminal size right after login; scrapli opens the SSH
# session without one, which leaves the CLI waiting until the operation times
# out. Requesting an explicit size fixes the hang.
PLATFORM_TRANSPORT_OPTIONS = {
    "nokia_srlinux": {"asyncssh": {"term_size": (200, 50)}},
}

# Platforms that keep configuration changes in a candidate datastore: the
# staged changes are only applied after an explicit commit and must be
# discarded when anything fails.
CANDIDATE_PLATFORMS = {"nokia_srlinux"}

# Confirmation shown when the SR Linux candidate already holds uncommitted
# changes (for example, left behind by a session that was aborted).
SRLINUX_CANDIDATE_CONFIRMATION = "Are you sure?"

# Candidate-mode banner of the SR Linux prompt.
SRLINUX_CANDIDATE_BANNER = re.compile(r"--\{[^}]*\bcandidate\b[^}]*\}--")

# Marker printed by SR Linux when "commit now" fails.
SRLINUX_COMMIT_FAILURE = "Error: Commit failed"

# Marker printed when another session still holds the exclusive candidate.
SRLINUX_EXCLUSIVE_IN_PROGRESS = "Cannot start an exclusive configuration session"


class AsyncSRLinuxDriver(AsyncNetworkDriver):
    """Asynchronous Scrapli driver for Nokia SR Linux.

    Reuses the privilege-level definitions shipped with
    ``scrapli_community.nokia.srlinux`` (``exec``/``configuration``) and adds
    the candidate-datastore handling that SR Linux requires: the deployment
    pipeline enters the candidate explicitly (:meth:`enter_candidate`), and
    then either applies the staged changes (:meth:`commit`) or throws them
    away (:meth:`discard`).
    """

    def __init__(self, **kwargs):
        """Initialize the driver with the Nokia SR Linux platform defaults.

        Args:
            **kwargs: Keyword arguments forwarded to ``AsyncNetworkDriver``.
                Platform defaults are only applied when the caller does not
                provide its own value.
        """
        kwargs.setdefault("privilege_levels", DEFAULT_PRIVILEGE_LEVELS)
        kwargs.setdefault("default_desired_privilege_level", "exec")
        kwargs.setdefault("on_open", default_async_on_open)
        kwargs.setdefault("on_close", default_async_on_close)
        kwargs.setdefault(
            "failed_when_contains",
            ["MINOR:", "MAJOR:", "CRITICAL:", "Error:", "Parsing error"],
        )
        super().__init__(**kwargs)

    async def _send_enter_candidate(self) -> tuple[bytes, bytes]:
        """Send ``enter candidate exclusive`` and return the raw/processed output.

        Returns:
            tuple[bytes, bytes]: Raw output (prompt included) and processed
            output (prompt stripped) read from the channel.
        """
        return await self.channel.send_input_and_read(
            channel_input="enter candidate exclusive",
            expected_outputs=[SRLINUX_CANDIDATE_CONFIRMATION],
            read_duration=10,
        )

    @staticmethod
    def _first_error_line(output: str) -> str:
        """Return the first ``Error`` line of a channel output.

        Args:
            output (str): Decoded channel output.

        Returns:
            str: The first line starting with ``Error``, or the stripped
            output itself when no error line is present.
        """
        return next(
            (line.strip() for line in output.splitlines() if line.strip().startswith("Error")),
            output.strip() or "no output",
        )

    async def enter_candidate(self) -> None:
        """Enter the exclusive candidate configuration with a clean datastore.

        When a previous session left uncommitted changes behind, SR Linux
        warns that the candidate is not empty and asks for confirmation before
        handing the exclusive session over. The confirmation is answered
        automatically and the leftover changes are discarded, so that only the
        configuration applied by this connection is ever committed.

        Raises:
            ScrapliCommandFailure: When the candidate cannot be entered (for
                example: another session still holds the exclusive candidate).
        """
        raw, processed = await self._send_enter_candidate()
        output = raw.decode(errors="replace")

        if SRLINUX_CANDIDATE_CONFIRMATION in output:
            self.logger.warning(
                "candidate was not empty: taking the session over and discarding the"
                " uncommitted changes"
            )
            _, answer = await self.channel.send_input(channel_input="y")
            answer_output = answer.decode(errors="replace")
            if SRLINUX_EXCLUSIVE_IN_PROGRESS in answer_output:
                raise ScrapliCommandFailure(
                    "could not enter candidate mode: "
                    f"{self._first_error_line(answer_output)}"
                )
            # the exclusive session is ours now: drop the stale changes and
            # enter the candidate again, with an empty datastore
            await self.channel.send_input(channel_input="discard now")
            raw, processed = await self._send_enter_candidate()
            output = raw.decode(errors="replace")

        if SRLINUX_CANDIDATE_BANNER.search(output):
            return

        raise ScrapliCommandFailure(
            f"could not enter candidate mode: {self._first_error_line(processed.decode(errors='replace'))}"
        )

    async def commit(self) -> None:
        """Commit the candidate configuration and return to the running state.

        ``commit now`` applies the staged changes and leaves candidate mode by
        itself, so plain ``send_command`` cannot be used here (it would try to
        deescalate first, discarding the configuration). On failure the staged
        changes are discarded through ``acquire_priv("exec")`` before raising.

        Raises:
            ScrapliCommandFailure: When the device reports
                ``Error: Commit failed``; the message carries the device output
                with the per-path errors.
        """
        _, processed = await self.channel.send_input(channel_input="commit now")
        output = processed.decode(errors="replace")
        await self.acquire_priv(desired_priv="exec")
        if SRLINUX_COMMIT_FAILURE in output:
            raise ScrapliCommandFailure(f"commit now: {output.strip()}")

    async def discard(self) -> None:
        """Discard the staged candidate changes, returning to the running state."""
        await self.acquire_priv(desired_priv="exec")


# Supported platform identifiers and their asynchronous Scrapli drivers.
DRIVER_MAP = {
    "arista_eos": AsyncEOSDriver,
    "arista_ceos": AsyncEOSDriver,
    "nokia_srlinux": AsyncSRLinuxDriver,
}

class SHandler:
    """Inventory validation and batch configuration deployment.

    Validates the host entries, builds the Scrapli connection parameters and
    applies configuration files to network devices concurrently.
    """

    def __init__(self, logger=None):
        """Initialize the handler.

        Args:
            logger (logging.Logger, optional): Custom logger instance. When
                omitted, the module logger is used.
        """
        self.logger = logger or logging.getLogger(__name__)

    def validate_inventory_hosts(self, inventory_hosts):
        """Validate the inventory hosts for duplicates and driver errors.

        Args:
            inventory_hosts (dict): Mapping with the configuration of every
                host.

        Raises:
            ValueError: When duplicate hostnames/config files are found or a
                host declares an unsupported platform.
        """
        self.logger.info("Validating inventory hosts for unique hostnames and config_file values...")
        if not inventory_hosts:
            return

        hostname_map = {}
        config_map = {}
        duplicates = []
        validation_errors = []

        self.logger.debug("Checking for duplicate hostnames and config files...")
        for name, params in inventory_hosts.items():
            device_params = dict(params)
            platform = str(device_params.get("device_type")).strip()
            hostname = str(device_params.get("hostname")).strip()
            config_file = str(
                device_params.get("config_file") or hostname
            ).strip()
            if not config_file.lower().endswith(".cfg"):
                config_file = f"{config_file}.cfg"

            self.logger.debug("Validating host '%s' with platform '%s', hostname '%s', and config_file '%s'", name, platform, hostname, config_file)
            try:
                self._get_driver(platform)
            except ValueError as exc:
                validation_errors.append(
                    f"Host '{name}' has invalid driver settings: {exc}"
                )

            if hostname:
                existing_name = hostname_map.get(hostname)
                if existing_name and existing_name != name:
                    duplicates.append(
                        f"Duplicate inventory hostname '{hostname}' for hosts '{existing_name}' and '{name}'"
                    )
                hostname_map[hostname] = name

            if config_file:
                existing_name = config_map.get(config_file)
                if existing_name and existing_name != name:
                    duplicates.append(
                        f"Duplicate config_file '{config_file}' for hosts '{existing_name}' and '{name}'"
                    )
                config_map[config_file] = name

        if duplicates:
            raise ValueError(
                "Duplicate inventory identifiers detected: " + "; ".join(duplicates)
            )

        if validation_errors:
            raise ValueError(
                "Invalid inventory driver settings: " + "; ".join(validation_errors)
            )

        self.logger.info("Inventory validation passed: unique hostnames and config_file values")

    def _build_connection_params(self, device_params, platform):
        """Build the keyword arguments used to instantiate the Scrapli driver.

        Falls back to the primary password when no secondary password is
        provided and disables strict host key checking.

        Args:
            device_params (dict): Raw parameters read from the inventory.
            platform (str): Device platform name (used for logging).

        Returns:
            dict: Parameters for the asynchronous driver constructor.
        """
        self.logger.info("Building connection parameters for platform '%s'", platform)
        connection_params = {
            "host": device_params["host"],
            "port": device_params.get("port", 22),
            "auth_username": device_params["username"],
            "auth_password": device_params["password"],
            "auth_secondary": device_params.get("auth_secondary"),
            "auth_strict_key": False,
            "transport": "asyncssh",
        }
        self.logger.debug("Connection parameters initialized")

        transport_options = PLATFORM_TRANSPORT_OPTIONS.get(platform)
        if transport_options:
            self.logger.debug("Applying extra transport options for platform '%s'", platform)
            connection_params["transport_options"] = transport_options

        if not connection_params.get("auth_secondary"):
            self.logger.warning("No secondary password found, using primary password...")
            connection_params["auth_secondary"] = connection_params.get("auth_password")

        return connection_params

    @staticmethod
    def _failure_detail(response) -> str:
        """Extract structured failure details from a Scrapli response.

        Args:
            response: Response object produced by sending commands through
                Scrapli.

        Returns:
            str: The first failed command together with the first non-empty
            output line, or a generic message when no detail is available.
        """
        failed_responses = [item for item in response.data if item.failed]
        if not failed_responses:
            return "response rejected by equipments"

        item = failed_responses[0]
        detail = next(
            (line.strip() for line in item.result.splitlines() if line.strip()),
            "response without details",
        )
        return f"{item.channel_input.strip()}: {detail}"

    def _get_driver(self, platform: str):
        """Return the Scrapli driver class matching a platform name.

        Args:
            platform (str): Device platform identifier.

        Returns:
            type: The asynchronous Scrapli driver class.

        Raises:
            ValueError: When the platform is not present in ``DRIVER_MAP``.
        """
        self.logger.debug("Retrieving driver for platform '%s'", platform)
        driver = DRIVER_MAP.get(platform)

        if driver is not None:
            return driver

        message = (
            f"Platform '{platform}' is not supported. "
            f"Available platforms: {list(DRIVER_MAP.keys())}"
        )
        self.logger.error(message)
        raise ValueError(message)

    async def _apply_configs_to_device(self, device_name: str, device_params: dict, config_files: list[str]):
        """Connect to one device and apply the given configuration files.

        Empty lines and comment lines starting with ``!`` or ``#`` are
        skipped; the remaining lines are sent with the ``configuration``
        privilege level. On candidate-based platforms the candidate is entered
        before the file is sent and committed afterwards; any failure discards
        the staged changes before the device is reported as failed.

        Args:
            device_name (str): Device name from the inventory.
            device_params (dict): Device parameters and credentials.
            config_files (list[str]): Paths of the configuration files.

        Returns:
            bool: ``True`` when every configuration was applied, ``False`` on
            any error.
        """
        self.logger.debug("Applying configurations to device '%s' with config_files: %s", device_name, config_files)
        platform = device_params.get("device_type")
        candidate_based = platform in CANDIDATE_PLATFORMS
        try:
            driver_cls = self._get_driver(platform)
            connection_params = self._build_connection_params(device_params, platform)
        except (ValueError, KeyError) as exc:
            self.logger.error("[%s] Invalid connection parameters: %s", device_name, exc)
            return False
        self.logger.debug("Retrieved driver for platform '%s'", platform)

        self.logger.info("[%s] Connecting to %s...", device_name, device_name)

        try:
            async with driver_cls(**connection_params) as conn:
                for cfg_file in config_files:
                    filename = Path(cfg_file).name
                    self.logger.info("[%s] Applying %s...", device_name, filename)
                    
                    try:
                        with open(cfg_file, "r", encoding="utf-8") as file:
                            configs = [
                                line.strip()
                                for line in file
                                if line.strip() and not line.lstrip().startswith(("!", "#"))
                            ]
                    except FileNotFoundError as exc:
                        self.logger.error("[%s] Config file not found: %s", device_name, exc)
                        return False

                    if candidate_based:
                        await conn.enter_candidate()

                    response = await conn.send_configs(
                        configs, privilege_level="configuration"
                    )
                    if response.failed:
                        detail = self._failure_detail(response)
                        self.logger.error(
                            "[%s] Failed to apply %s: %s", device_name, filename, detail
                        )
                        if candidate_based:
                            await conn.discard()
                        return False

                    if candidate_based:
                        await conn.commit()

                    self.logger.info("[%s] %s applied successfully", device_name, filename)

        except ScrapliCommandFailure as exc:
            self.logger.error("[%s] Device rejected the configuration: %s", device_name, exc)
            return False
        except ScrapliConnectionNotOpened as exc:
            self.logger.error(
                "[%s] Could not establish an interactive session on the device (%s). "
                "This usually means the SSH login succeeded but scrapli never recognized "
                "the device prompt; check the 'privilege_levels' patterns for platform '%s'.",
                device_name,
                exc,
                platform,
            )
            return False
        except ScrapliAuthenticationFailed as exc:
            self.logger.error("[%s] Authentication failed: %s", device_name, exc)
            return False
        except ScrapliTimeout as exc:
            self.logger.error("[%s] Connection timed out: %s", device_name, exc)
            return False
        except ScrapliConnectionError as exc:
            self.logger.error("[%s] Connection error: %s", device_name, exc)
            return False
        except Exception as exc:
            self.logger.error("[%s] Unexpected error during connection: %s", device_name, exc)
            return False

        self.logger.info("[%s] Connection closed.", device_name)
        return True

    async def connect(self, inventory_path: str, cfg_dir: str, inventory_hosts=None):
        """Connect to every inventory host and apply its configuration file.

        One task is created per host and all of them run concurrently. When
        ``inventory_hosts`` is not given, the inventory is read from
        ``inventory_path`` first.

        Args:
            inventory_path (str): YAML file containing the inventory.
            cfg_dir (str): Base directory holding the configuration files.
            inventory_hosts (dict, optional): Pre-loaded hosts mapping.

        Returns:
            bool: ``True`` when every host succeeded, ``False`` otherwise.
        """
        self.logger.debug("Connecting to devices...")
        if inventory_hosts is None:
            try:
                with open(inventory_path, "r", encoding="utf-8") as file:
                    inventory_data = yaml.safe_load(file) or {}
                inventory_hosts = inventory_data.get("all", {}).get("hosts", {})
            except FileNotFoundError as exc:
                self.logger.error("Inventory file not found: %s", exc)
                return False
            except yaml.YAMLError as exc:
                self.logger.error("Error parsing YAML inventory: %s", exc)
                return False

        self.logger.debug("Inventory hosts initialized")
        tasks = []

        for name, params in inventory_hosts.items():
            device_params = dict(params)
            config_file_name = str(
                device_params.get("config_file") or device_params.get("hostname") or name
            ).strip()
            if not config_file_name.lower().endswith(".cfg"):
                config_file_name = f"{config_file_name}.cfg"

            config_file = Path(cfg_dir) / config_file_name
            self.logger.debug("[%s] Loading config file %s", name, config_file)
            tasks.append(
                self._apply_configs_to_device(
                    name,
                    device_params,
                    [str(config_file)]
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=False)

        all_succeeded = True
        for (name, _), result in zip(inventory_hosts.items(), results):
            if isinstance(result, Exception):
                self.logger.error("[%s] Error: %s", name, result, exc_info=result)
                all_succeeded = False
            elif result is False:
                all_succeeded = False

        return all_succeeded