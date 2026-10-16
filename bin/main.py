"""Multiflora backend entry point.

Coordinates the command-line arguments, logging, inventory loading, Jinja2
rendering and the deployment flow. The same routine is reused by the GUI
worker process.
"""

import asyncio
import yaml
from arg_parser import ArgParser
from execution_timer import ExecutionTimer
from logger import Logger
from event_pool.s_handler import SHandler
from render_pool.jinja_env import JinjaEnv
from render_pool.path_solver import PathSolver
from render_pool.renderer import Renderer


async def main():
    """Run the backend orchestration.

    Sets up the timer, logger and device handler; resolves the project paths;
    loads and validates the inventory; renders the configuration files; and
    finishes according to the flags received: stop after rendering
    (``--dry-run``), deploy the generated files to the devices (``--deploy``)
    or do nothing else (no flag). Failures are logged, and the total elapsed
    time is always reported at the end.
    """
    
    timer = ExecutionTimer().start()
    logger = None

    try:
        args = ArgParser.parse()
        logger = Logger.create(args.path)
        handler = SHandler(logger)

        logger.info("Backend initiated")
        logger.info(f"Initiating queue {args.name} configuration...")
        logger.info("Argument parser and logging object initiated")

        logger.info("Initiating path solver...")
        template_path, cfg_dir, inventory_path = PathSolver.solve(
            args.path, args.template, args.inventory, logger
        )
        logger.info("Solved path successfully")

        try:
            with open(inventory_path, "r", encoding="utf-8") as file:
                inventory_data = yaml.safe_load(file) or {}
        except FileNotFoundError as exc:
            logger.error(f"Inventory file not found: {exc}")
            return
        except yaml.YAMLError as exc:
            logger.error(f"Error parsing YAML inventory: {exc}")
            return

        try:
            inventory_hosts = inventory_data.get("all", {}).get("hosts", {})
            handler.validate_inventory_hosts(inventory_hosts)
        except ValueError as exc:
            logger.error(f"Inventory validation failed: {exc}")
            return

        logger.info("Creating Jinja env...")
        template = JinjaEnv.create(template_path, logger)
        if template is None:
            logger.error("Failed to create Jinja env")
            return
        logger.info("Jinja env created successfully")

        logger.info("Initiating renderer...")
        if not Renderer.render(template, args.data_file, cfg_dir, logger, inventory_hosts):
            logger.error("Rendering failed")
            return
        logger.info("Rendering was successful")

        if args.dry_run:
            logger.info("Initiating dry-run...")
            logger.info("Dry-run occurred successfully")
        else:
            if args.deploy:
                logger.info("Initiating deploy...")
                deployment_succeeded = await handler.connect(
                    inventory_path, cfg_dir, inventory_hosts
                )
                if deployment_succeeded:
                    logger.info("Deploy occurred successfully")
                else:
                    logger.error("Deploy failed")

    except Exception as exc:
        if logger:
            logger.error(f"An unexpected error occurred: {exc}")
        print(f"An error occurred: {exc}")
    finally:
        elapsed_time = timer.stop()
        message = f"Total elapsed time: {timer.format_elapsed()} ({elapsed_time:.2f}s)"
        if logger is not None:
            logger.info(message)
        else:
            print(message)


def run():
    """Run the asynchronous backend from a synchronous entry point.

    Falls back to a manually managed event loop when an event loop is already
    active and ``asyncio.run`` cannot be used.
    """

    try:
        asyncio.run(main())
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())


if __name__ == "__main__":
    run()
