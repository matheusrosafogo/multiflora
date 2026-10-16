"""Main graphical interface of the Multiflora application.

Manages execution queues, the device status tree in the side bar and the
communication with the background worker processes. Also owns the project
file lifecycle: creating, loading, saving and caching of ``.json`` project
files under the persistent ``config/`` directory.
"""

import pathlib
import os
import tkinter as tk
from tkinter import filedialog, ttk, simpledialog, messagebox
import tkinter.scrolledtext as st
import logging
import subprocess
import shutil
import sys
import threading
import re
import json


# COMPILATION-AWARE SECTION
# Detects whether the code runs from a compiled binary (Nuitka/PyInstaller)
# and resolves where the executable and the persistent "config/" folder live.
_compiled_info = globals().get("__compiled__")
_COMPILED = (
    _compiled_info is not None
    or getattr(sys, "frozen", False)
    or hasattr(sys, "_MEIPASS")
)
# In Nuitka's onefile mode the real process runs from a temporary directory
# (sys.executable itself points there). The original binary directory is
# exposed in __compiled__.containing_dir / NUITKA_ONEFILE_DIRECTORY.
_ONEFILE = bool(_compiled_info is not None and getattr(_compiled_info, "onefile", False))

current_project_filepath = None


def _app_base_dir():
    """Return the application directory holding the executable and ``config/``.

    In onefile mode the extracted executable lives in a temporary directory
    that disappears on exit, so the original binary directory is used instead
    to keep projects and configuration persistent.

    Returns:
        str: Absolute path to the application base directory.
    """
    if _ONEFILE:
        containing = getattr(_compiled_info, "containing_dir", None)
        if containing:
            return os.path.abspath(containing)
        onefile_dir = os.environ.get("NUITKA_ONEFILE_DIRECTORY")
        if onefile_dir:
            return os.path.abspath(onefile_dir)
    if _COMPILED:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _app_executable():
    """Return the executable path used to relaunch the app or run workers.

    Returns ``None`` when the app runs from source (not compiled); in that
    case the worker must be started with the current Python interpreter.
    """
    if _ONEFILE:
        # sys.argv[0] points to the original onefile binary (not the temporary one).
        executable = os.path.abspath(sys.argv[0])
        if os.path.isfile(executable):
            return executable
        containing = getattr(_compiled_info, "containing_dir", None)
        if containing:
            return os.path.join(os.path.abspath(containing), os.path.basename(sys.argv[0]))
    if _COMPILED:
        return os.path.abspath(sys.executable)
    return None


def _run_as_worker():
    """Reconfigure the console output and hand execution to the backend (main).

    Line buffering keeps the logs flowing synchronously so the GUI captures
    them while the worker is running.
    """
    import main
    sys.argv = sys.argv[:1] + sys.argv[2:]
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True, errors="replace")
        except Exception:
            pass
    main.run()


# Re-execution of the compiled binary as a backend worker (triggered by the GUI).
if len(sys.argv) > 1 and sys.argv[1] == "--worker":
    _run_as_worker()
    sys.exit(0)

_BASE_DIR = _app_base_dir()

main_script = os.path.join(_BASE_DIR, "main.py")

if not os.path.isfile(main_script):
    main_script = os.path.join(_BASE_DIR, "bin", "main.py")

_CONFIG_DIR = os.path.abspath(os.path.join(_BASE_DIR, "config"))
_CACHE_FILE = os.path.join(_CONFIG_DIR, ".last_project.json")
_AUTOSAVE_FILE = os.path.join(_CONFIG_DIR, ".autosave_project.json")

def _desktop_quote(argument):
    """Cita um argumento conforme as regras de string do formato .desktop."""
    escaped = (
        argument.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
    )
    return f'"{escaped}"'


def create_desktop_entry():
    """Register an application shortcut in the system menu.

    The ``.desktop`` file needs an "Exec" entry pointing to a program that is
    really executable. In compiled mode the binary itself is used; during
    development the current Python interpreter plus ``gui.py`` is used (the
    script alone is not executable and the desktop environment would discard
    the entry).

    The icon is copied to a persistent location because, in onefile mode, the
    packaged PNG exists only inside the temporary extraction directory, which
    is deleted when the application closes.
    """
    try:
        # Use XDG_DATA_HOME when defined (that is exactly where system menus
        # look for entries); otherwise fall back to the user default location.
        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        if xdg_data_home:
            data_dir = pathlib.Path(xdg_data_home)
        else:
            data_dir = pathlib.Path.home() / ".local" / "share"
        desktop_dir = data_dir / "applications"
        desktop_dir.mkdir(parents=True, exist_ok=True)
        desktop_file = desktop_dir / "multiflora.desktop"

        executable = _app_executable()
        if _COMPILED and executable:
            exec_args = [executable]
        else:
            exec_args = [sys.executable, os.path.abspath(__file__)]
        exec_value = " ".join(_desktop_quote(arg) for arg in exec_args)

        icon_value = ""
        icon_source = pathlib.Path(__file__).resolve().parent / "assets" / "multiflora.png"
        if not icon_source.is_file():
            icon_source = pathlib.Path(_BASE_DIR) / "assets" / "multiflora.png"
        if icon_source.is_file():
            icon_dir = data_dir / "multiflora"
            icon_dir.mkdir(parents=True, exist_ok=True)
            icon_dest = icon_dir / "multiflora.png"
            try:
                shutil.copy2(icon_source, icon_dest)
                icon_value = str(icon_dest)
            except Exception:
                # In onefile the source path lives in the temporary extraction
                # directory and stops existing when the app closes; in that
                # case it is better to omit the icon than to point to a
                # volatile file.
                icon_value = "" if _ONEFILE else str(icon_source)

        desktop_content = (
            "[Desktop Entry]\n"
            "Version=1.0\n"
            "Type=Application\n"
            "Name=Multiflora\n"
            "Comment=Platform for Automation and Deployment of Networks\n"
            f"Exec={exec_value}\n"
        )
        if icon_value:
            desktop_content += f"Icon={icon_value}\n"
        desktop_content += "Terminal=false\nCategories=Utility;\n"

        # Rewrite only when the content changed, fixing old or invalid entries
        # instead of keeping them forever.
        if desktop_file.is_file():
            try:
                if desktop_file.read_text(encoding="utf-8") == desktop_content:
                    return
            except Exception:
                pass

        desktop_file.write_text(desktop_content, encoding="utf-8")
        desktop_file.chmod(0o644)

        updater = shutil.which("update-desktop-database")
        if updater:
            subprocess.run(
                [updater, str(desktop_dir)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception as exc:
        # Failures here must not go unnoticed: console logging is not set up
        # yet at this point, so write straight to stderr to make the error
        # visible in any terminal execution.
        message = f"Could not create desktop entry: {exc}"
        logging.warning(message)
        try:
            print(f"Multiflora: {message}", file=sys.stderr)
        except Exception:
            pass


# The shortcut is registered before the interface is built so it is still
# created even when the window fails to open (e.g.: no display available).
create_desktop_entry()

if not os.path.exists(_CONFIG_DIR):
    os.makedirs(_CONFIG_DIR)


_execution_queues = []
_device_status     = {}
_queue_base_names  = {}
_tree_queue_items  = {}
_tree_device_items = {}

_terminal_maximized = False
_saved_top_height = None
_saved_bottom_height = None

_STATUS_LABELS = {
    "detected":  "Detected",
    "connecting": "Connecting",
    "applying":  "Applying",
    "success":    "Success",
    "rendered":     "Rendered",
    "failure":      "Failure",
}

_RE_VALIDATING = re.compile(r"Validating host '(?P<host>[^']+)' with platform '(?P<platform>[^']+)'")

# Output keywords that drive the device tree status updates.
SUCCESS_WORDS = (
    "applied successfully",
    "successfully created",
    "rendering was successful",
    "dry-run occurred successfully",
)

_FAIL_WORDS = ("fail", "error", "timeout", "refused", "exception", "unreachable")

def _is_cache_path(path):
    """Check whether a path points to one of the internal cache files.

    The internal files are the session cache (``.last_project.json``) and the
    continuous auto-save cache (``.autosave_project.json``). They must never
    be treated as the user's project file.

    Args:
        path (str): Path to test.

    Returns:
        bool: ``True`` when the path matches either internal cache file.
    """
    return _same_path(path, _CACHE_FILE) or _same_path(path, _AUTOSAVE_FILE)

def _same_path(path_a, path_b):
    """Check whether two paths point to the same physical file or location.

    When both paths exist the comparison uses ``os.path.samefile`` (which
    resolves symlinks); otherwise the paths are normalized, including case
    folding on case-insensitive filesystems.

    Args:
        path_a (str): First file path.
        path_b (str): Second file path.

    Returns:
        bool: ``True`` when both paths represent the same destination.
    """
    if not path_a or not path_b:
        return False
    try:
        path_a = os.path.abspath(path_a)
        path_b = os.path.abspath(path_b)
    except Exception:
        return False

    try:
        if os.path.exists(path_a) and os.path.exists(path_b):
            return os.path.samefile(path_a, path_b)
    except Exception:
        pass

    return os.path.normcase(os.path.normpath(path_a)) == os.path.normcase(os.path.normpath(path_b))

def _sync_caches():
    """Mirror the in-memory state into both internal caches.

    Returns:
        bool: ``True`` when both the session cache and the auto-save cache
        were written successfully.
    """
    ok_cache = save_project_state(_CACHE_FILE)
    ok_auto = save_project_state(_AUTOSAVE_FILE)
    return ok_cache and ok_auto

def _overwrite_caches_with_project():
    """Overwrite both caches with the exact project file content on disk.

    This effectively discards the pending changes: they do not come back when
    the application is reopened.

    Returns:
        bool: ``True`` when both cache files were rewritten.
    """
    try:
        with open(current_project_filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = get_current_project_state()
    ok = True
    for target in (_CACHE_FILE, _AUTOSAVE_FILE):
        try:
            with open(target, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception:
            ok = False
    return ok

def _check_path_consistency(path, field_type, queue_dict, entry_widget):
    """Run the consistency checks on a path that is about to be accepted.

    The checks cover: files of the same queue spread across different
    subdirectories, a probable inventory/data swap (judged by file name), the
    same file used twice in one queue (hard block) and a file already in use
    by another queue (warning). Applies both to dialog selections and to
    manually typed paths.

    Args:
        path (str): Path to validate.
        field_type (str): One of ``inventory``, ``data`` or ``template``.
        queue_dict (dict): Queue widgets and state the path belongs to.
        entry_widget (tk.Entry): Entry being validated (excluded from the
            duplicate search, since it is the field receiving the path).

    Returns:
        bool: ``True`` when the path is accepted (with or without warnings).
    """
    resolved = _resolve_config_path(path)

    # 1) Files of the same queue pointing to different subdirectories
    try:
        current_subdir = os.path.dirname(os.path.relpath(resolved, _CONFIG_DIR))
        existing_subdirs = []
        for k in ["inventory", "data", "template"]:
            if k in queue_dict and queue_dict[k] != entry_widget:
                other_raw = queue_dict[k].get().strip()
                other_path = _resolve_config_path(other_raw)
                if other_raw and os.path.exists(other_path):
                    other_sub = os.path.dirname(os.path.relpath(other_path, _CONFIG_DIR))
                    if other_sub:
                        existing_subdirs.append((k, other_sub))
        if current_subdir and existing_subdirs:
            unique_existing = set(sub for _, sub in existing_subdirs)
            if current_subdir not in unique_existing:
                names_field = ", ".join([f"{c} ({s})" for c, s in existing_subdirs])
                msg = (f"The file is in '{current_subdir}', but other files in this queue "
                       f"are in different subdirectories:\n\n{names_field}\n\n"
                       f"Do you want to continue anyway?")
                if not messagebox.askyesno("Warning: Different Subdirectories", msg):
                    return False
    except Exception as e:
        logging.warning(f"Error in subdirectory validation: {e}")

    # 2) Probable inventory x data swap (judged by file name)
    file_name = os.path.basename(resolved).lower()
    inversion = False
    msg = ""
    if field_type == "inventory" and "data" in file_name:
        inversion = True
        msg = f"Selected a file with 'data' in the name for the Inventory field.\n\nFile: {file_name}\n\nAre you sure?"
    elif field_type == "data" and ("inv" in file_name or "inventory" in file_name):
        inversion = True
        msg = f"Selected a file with 'inventory' (or inv) in the name for the Data field.\n\nFile: {file_name}\n\nAre you sure?"
    if inversion and not messagebox.askyesno("Warning: Possible Inversion", msg):
        return False

    # 3) Same file used twice in the same queue (hard block)
    for k in ["inventory", "data", "template"]:
        if k in queue_dict and queue_dict[k] != entry_widget:
            other_raw = queue_dict[k].get().strip()
            if other_raw and _resolve_config_path(other_raw) == resolved:
                messagebox.showerror("Error: Duplicate File",
                    "You cannot use the same file in different fields of the same queue.")
                return False

    # 4) File already used by another queue (warning)
    used_in_other_queues = []
    for q in _execution_queues:
        if q["id"] != queue_dict["id"]:
            for k in ["inventory", "data", "template"]:
                if k in q and _resolve_config_path(q[k].get().strip()) == resolved:
                    used_in_other_queues.append(q["title_label"].cget("text"))
                    break
    if used_in_other_queues:
        queue_names = "\n- ".join(used_in_other_queues)
        if not messagebox.askyesno("Warning: File in Use",
            f"This file is already being used in the following queues:\n\n- {queue_names}\n\nDo you want to use it anyway?"):
            return False

    return True

def _on_entry_focusout(entry_widget, field_type, queue_dict):
    """Validate manually typed paths when the field loses focus.

    Applies the same consistency warnings used by dialog selections.

    Args:
        entry_widget (tk.Entry): Entry that lost focus.
        field_type (str): One of ``inventory``, ``data`` or ``template``.
        queue_dict (dict): Queue widgets and state the entry belongs to.
    """
    raw = entry_widget.get().strip()
    if not raw:
        return
    resolved = _resolve_config_path(raw)
    key = (field_type, os.path.normpath(resolved))
    done = queue_dict.setdefault("_validated_paths", set())
    if key in done:
        return
    if not os.path.isfile(resolved):
        return  # missing files are blocked later by get_queue_errors
    accepted = _check_path_consistency(resolved, field_type, queue_dict, entry_widget)
    done.add(key)
    if not accepted:
        entry_widget.select_range(0, tk.END)   # keep the text selected for correction
        entry_widget.icursor(tk.END)

def _has_unsaved_project_changes():
    """Compare the in-memory state with the project file content on disk.

    Returns:
        bool: ``True`` when there are changes not yet persisted to the
        official project file.
    """
    if not current_project_filepath or _is_cache_path(current_project_filepath):
        return False
    if not os.path.isfile(current_project_filepath):
        return has_unsaved_changes()
    try:
        with open(current_project_filepath, "r", encoding="utf-8") as f:
            disk_state = json.load(f)
    except Exception:
        return True
    return disk_state != get_current_project_state()

def _resolve_config_path(path):
    """Normalize user-typed paths, accepting relative entries.

    Absolute paths are only normalized; relative paths are resolved first
    against ``config/`` and, as a fallback, against the application base
    directory.

    Args:
        path (str): Raw path (absolute or relative).

    Returns:
        str: Resolved absolute path (or the ``config/`` candidate when the
        file does not exist in either location).
    """
    if not path:
        return path
    p = os.path.expanduser(path.strip())
    if os.path.isabs(p):
        return os.path.normpath(p)
    candidate = os.path.normpath(os.path.join(_CONFIG_DIR, p))
    if os.path.exists(candidate):
        return candidate
    candidate_base = os.path.normpath(os.path.join(_BASE_DIR, p))
    if os.path.exists(candidate_base):
        return candidate_base
    return candidate

def _update_queue_node(qid):
    """Refresh the title and device count of a queue node in the device tree.

    Args:
        qid (int): Execution queue identifier.
    """
    iid = _tree_queue_items.get(qid)
    if not iid:
        return
    statuses = [d["status"] for d in _device_status.get(qid, {}).values()]
    name = _queue_base_names.get(qid, f"Queue {qid}")
    device_tree.item(iid, text=f"{name}".strip(), values=(f"{len(statuses)} devices",))

def sidebar_ensure_queue(queue_dict):
    """Make sure a queue exists in the device side bar.

    Creates the queue node when it is not registered in the tree yet.

    Args:
        queue_dict (dict): Queue data and widgets.
    """
    qid = queue_dict["id"]
    if qid in _tree_queue_items:
        return
    name = queue_dict["title_label"].cget("text")
    _queue_base_names[qid] = name
    _device_status.setdefault(qid, {})
    _tree_queue_items[qid] = device_tree.insert("", "end", text=name, open=True, values=("0 devices",))

def sidebar_rename_queue(queue_dict):
    """Rename the label shown on the queue node inside the side bar.

    Args:
        queue_dict (dict): Queue data and widgets.
    """
    qid = queue_dict["id"]
    _queue_base_names[qid] = queue_dict["title_label"].cget("text")
    _update_queue_node(qid)

def sidebar_remove_queue(queue_dict):
    """Remove a queue and all its devices from the device tree.

    The associated worker process is killed here to prevent background
    routines from keeping on consuming resources and writing logs into a tab
    that no longer exists.

    Args:
        queue_dict (dict): Queue data and widgets being removed.
    """
    qid = queue_dict["id"]
    for key in [k for k in _tree_device_items if k[0] == qid]:
        device_tree.delete(_tree_device_items.pop(key))
    if qid in _tree_queue_items:
        device_tree.delete(_tree_queue_items.pop(qid))
    _device_status.pop(qid, None)
    _queue_base_names.pop(qid, None)
    proc = queue_dict.get("process")
    if proc and proc.poll() is None:
        proc.kill()

def sidebar_clear_devices(qid):
    """Delete every device item under a given queue, keeping the queue itself.

    Useful to reset the visuals before a new execution.

    Args:
        qid (int): Target queue identifier.
    """
    for key in [k for k in _tree_device_items if k[0] == qid]:
        device_tree.delete(_tree_device_items.pop(key))
    _device_status[qid] = {}
    _update_queue_node(qid)

def sidebar_add_device(qid, host, platform=None, status="detected"):
    """Insert and register a device as a child of the given queue.

    When the device is already registered, only its status is updated.

    Args:
        qid (int): Queue identifier in the tree.
        host (str): Device name or address.
        platform (str, optional): Device platform name.
        status (str, optional): Initial status key (see ``_STATUS_LABELS``).
    """
    devices = _device_status.setdefault(qid, {})
    if host in devices:
        sidebar_set_device_status(qid, host, status)
        return
    devices[host] = {"platform": platform, "status": status}
    q_iid = _tree_queue_items.get(qid)
    if q_iid is None:
        return
    iid = device_tree.insert(q_iid, "end", text=host,
                             values=(_STATUS_LABELS[status],), tags=(status,))
    _tree_device_items[(qid, host)] = iid
    _update_queue_node(qid)

def sidebar_set_device_status(qid, host, status):
    """Update colors and label of one device according to its new status.

    Args:
        qid (int): Queue identifier.
        host (str): Device name.
        status (str): New status key (see ``_STATUS_LABELS``).
    """
    devices = _device_status.setdefault(qid, {})
    if host not in devices:
        return
    devices[host]["status"] = status
    iid = _tree_device_items.get((qid, host))
    if iid:
        device_tree.item(iid, values=(_STATUS_LABELS[status],), tags=(status,))
    _update_queue_node(qid)

def sidebar_finalize_queue(qid, mode):
    """Apply the terminal statuses to the devices when the queue finishes.

    Devices already marked as success/rendered become ``rendered`` in dry-run
    mode or ``success`` otherwise; every other device becomes ``failure``.

    Args:
        qid (int): Numeric identifier of the queue.
        mode (str): Execution mode (``dry-run`` or ``deploy``).
    """
    devices = _device_status.get(qid, {})
    is_dry = mode in ("dry-run", "dry_run")
    for host, info in devices.items():
        current = info["status"]
        if current in ("success", "rendered"):
            info["status"] = "rendered" if is_dry else "success"
        else:
            info["status"] = "failure"
        iid = _tree_device_items.get((qid, host))
        if iid:
            device_tree.item(iid, values=(_STATUS_LABELS[info["status"]],), tags=(info["status"],))
    _update_queue_node(qid)

def parse_device_line(queue_dict, line):
    """Parse one worker output line read from the console tab.

    Two patterns are recognized: the inventory validation line (which creates
    the device node with its platform name) and status messages mentioning a
    registered host, classified through keyword lists (``SUCCESS_WORDS``,
    ``_FAIL_WORDS``, applying/creating/solving and connecting). Tree updates
    are scheduled on the Tk main thread through ``root.after``.

    Args:
        queue_dict (dict): Queue the line belongs to.
        line (str): Raw line read from the worker output.
    """
    qid = queue_dict["id"]
    m = _RE_VALIDATING.search(line)
    if m:
        h, p = m.group("host"), m.group("platform")
        root.after(0, lambda h=h, p=p: sidebar_add_device(qid, h, p, "detected"))
        return
    registered_hosts = _device_status.get(qid, {})

    if not registered_hosts:
        return

    line_lower = line.lower()
    matched_host = None

    for host in registered_hosts:
        if re.search(rf"\b{re.escape(host.lower())}\b", line_lower):
            matched_host = host
            break

    if not matched_host:
        return

    if any(w in line_lower for w in SUCCESS_WORDS):
        status = "success"
    elif any(w in line_lower for w in _FAIL_WORDS):
        status = "failure"
    elif any(w in line_lower for w in ("applying", "creating", "solving")):
        status = "applying"
    elif "connecting" in line_lower:
        status = "connecting"
    else:
        return

    root.after(0, lambda h=matched_host, s=status: sidebar_set_device_status(qid, h, s))

class TextHandler(logging.Handler):
    """Logging handler that writes records into a Tkinter text widget."""
    def __init__(self, text_widget):
        """Bind the handler to its destination text widget.

        Args:
            text_widget (tk.Widget): Text area that receives the messages.
        """
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record):
        """Format and schedule the message delivery on the GUI thread."""
        msg = self.format(record)
        self.text_widget.after(0, self.append_text, msg)

    def append_text(self, msg):
        """Temporarily enable the text widget, append the message and scroll down."""
        self.text_widget.config(state=tk.NORMAL)
        self.text_widget.insert(tk.END, msg + '\n')
        self.text_widget.see(tk.END)
        self.text_widget.config(state=tk.DISABLED)

def select_file(entry_widget, title, filetypes, field_type, queue_dict):
    """Open a file dialog until the user picks an accepted path (or cancels).

    Every selected path goes through ``_check_path_consistency``; rejected
    paths reopen the dialog. The accepted path is written into
    ``entry_widget`` and its directory is remembered as the queue's last used
    directory.

    Args:
        entry_widget (tk.Entry): Entry that receives the chosen path.
        title (str): Dialog title.
        filetypes (list): File type filters for the dialog.
        field_type (str): One of ``inventory``, ``data`` or ``template``.
        queue_dict (dict): Queue the entry belongs to.
    """
    while True:
        start_dir = queue_dict.get("last_used_directory", _CONFIG_DIR)
        if not start_dir or not os.path.isdir(start_dir):
            start_dir = _CONFIG_DIR
        path = filedialog.askopenfilename(title=title, filetypes=filetypes, initialdir=start_dir)
        if not path:
            return
        if not _check_path_consistency(path, field_type, queue_dict, entry_widget):
            continue
        queue_dict["last_used_directory"] = os.path.dirname(path)
        queue_dict.setdefault("_validated_paths", set()).add((field_type, os.path.normpath(path)))
        entry_widget.delete(0, tk.END)
        entry_widget.insert(0, path)
        break

def create_file_row(parent, row_index, label_text, dialog_title, filetypes, field_type, queue_dict):
    """Build a standardized form row with a label, an entry and a Search button.

    Args:
        parent (tk.Widget): Parent widget of the row.
        row_index (int): Grid row index.
        label_text (str): Label shown on the left.
        dialog_title (str): Title of the file dialog opened by the button.
        filetypes (list): File type filters for the dialog.
        field_type (str): Field name (``inventory``, ``data`` or ``template``).
        queue_dict (dict): Queue the row belongs to.

    Returns:
        tk.Entry: The created entry widget.
    """
    label = tk.Label(parent, text=label_text)
    label.grid(row=row_index, column=0, sticky="e", pady=2, padx=(0, 5))

    entry_width = 50
    entry = tk.Entry(parent, width=entry_width)
    entry.grid(row=row_index, column=1, sticky="ew", pady=2, padx=5)
    entry.bind("<FocusOut>", lambda e, ew=entry, ft=field_type, qd=queue_dict: _on_entry_focusout(ew, ft, qd))

    parent.columnconfigure(1, weight=1)

    search_button = tk.Button(parent, text="Search", command=lambda: select_file(entry, dialog_title, filetypes, field_type, queue_dict))
    search_button.grid(row=row_index, column=2, sticky="w", pady=2)

    return entry

def remove_queue(queue_dict):
    """Destroy the frame and the tree nodes belonging to a queue.

    When any path field is filled, a confirmation dialog is shown first to
    prevent accidental loss of typing.

    Args:
        queue_dict (dict): Queue data and widgets being removed.
    """
    inv_val = queue_dict["inventory"].get().strip()
    data_val = queue_dict["data"].get().strip()
    tpl_val = queue_dict["template"].get().strip()
    if not inv_val and not data_val and not tpl_val:
        confirmation = True
    else:
        current_name = queue_dict["title_label"].cget("text")
        confirmation = messagebox.askyesno(
            "Confirm removal", 
            f"Are you sure you want to remove '{current_name}'?\nThe filled paths and logs of this tab will be lost."
        )
    
    if confirmation:
        sidebar_remove_queue(queue_dict)
        queue_dict["frame"].destroy()
        log_notebook.forget(queue_dict["tab"])
        _execution_queues.remove(queue_dict)
        queues_container.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))
        if queues_container.winfo_reqheight() <= canvas.winfo_height():
            canvas.yview_moveto(0.0)

def rename_queue(queue_dict):
    """Interactively edit a queue title.

    The new name is applied to the frame label, the log tab and the side bar,
    and the project is auto-saved. When the project file exists and the queue
    already executed successfully, the app offers a restart so the change
    reaches the execution logs.

    Args:
        queue_dict (dict): Queue whose title is being edited.
    """
    current_name = queue_dict["title_label"].cget("text")
    new_name = simpledialog.askstring("Rename Queue", "Enter the new name for this queue:", initialvalue=current_name)
    if new_name and new_name.strip() and new_name.strip() != current_name:
        new_name = new_name.strip()
        qid = queue_dict["id"]
        requires_restart = False
        if current_project_filepath:
            devices = _device_status.get(qid, {})
            has_success = any(info.get("status") in ("success", "rendered") for info in devices.values())
            if has_success:
                requires_restart = True
        queue_dict["title_label"].config(text=new_name)
        log_notebook.tab(queue_dict["tab"], text=new_name)
        sidebar_rename_queue(queue_dict)
        if requires_restart:
            _queue_base_names[qid] = f"{new_name} *"
            _update_queue_node(qid)
        auto_save_current_project()
        if requires_restart:
            if messagebox.askyesno(
                "Restart",
                "Because the project is saved and this queue has already been executed successfully before, it will be necessary to restart the application to confirm the name change of the queue in the execution logs.\n\nDo you want to restart the application now?"
            ):
                restart_app()

def get_queue_errors(queue_dict):
    """Collect the problems found in the path fields of a queue.

    Checks for empty fields, files missing on disk and two fields pointing to
    the same file.

    Args:
        queue_dict (dict): Queue whose fields are inspected.

    Returns:
        list[str]: Human-readable error descriptions (empty when the queue is
        ready to run).
    """
    errors = []
    fields = {
        "Inventory path": queue_dict["inventory"].get().strip(),
        "Data path": queue_dict["data"].get().strip(),
        "Jinja Template path": queue_dict["template"].get().strip(),
    }
    for name_field, raw in fields.items():
        if not raw:
            errors.append(f"{name_field}: Empty")
        elif not os.path.isfile(_resolve_config_path(raw)):
            errors.append(f"{name_field}: File not found on disk")
    pairs = [(name, _resolve_config_path(raw)) for name, raw in fields.items() if raw]
    for i in range(len(pairs)):
        for j in range(i + 1, len(pairs)):
            if pairs[i][1] == pairs[j][1]:
                errors.append(f"{pairs[i][0]} and {pairs[j][0]} point to the same file")
    return errors

def execute_queue_logic(queue_dict, mode):
    """Run one queue in a separate worker process and relay its events.

    The inventory must live inside a subdirectory of ``config/``; that
    subdirectory becomes the worker's configuration path. The worker (the
    compiled binary relaunched with ``--worker`` or the current interpreter
    running ``main.py``) is spawned as a subprocess, and every output line is
    forwarded to the queue logger and parsed to update the device tree.

    Args:
        queue_dict (dict): Queue data and widgets to execute.
        mode (str): Execution mode (``dry-run`` or ``deploy``).
    """
    log = queue_dict["logger"]
    qid = queue_dict["id"]
    current_name = queue_dict["title_label"].cget("text")
    inv_path  = _resolve_config_path(queue_dict["inventory"].get().strip())
    data_path = _resolve_config_path(queue_dict["data"].get().strip())
    tpl_path  = _resolve_config_path(queue_dict["template"].get().strip())

    try:
        rel_inv = os.path.relpath(inv_path, _CONFIG_DIR)
        father_dir = os.path.dirname(rel_inv)
        if not father_dir or father_dir == ".":
            log.error(f"File must be inside a subfolder of config/. Path: {inv_path}")
            return
    except ValueError as e:
        log.error(f"Path outside config/ directory: {e}")
        return

    config_subdir = os.path.join(_CONFIG_DIR, father_dir)
    root.after(0, lambda: sidebar_clear_devices(qid))

    # Build the worker command for a separate process:
    #  - compiled: relaunch the application executable with the "--worker" flag;
    #  - development: run main.py directly with the current interpreter.
    if _COMPILED:
        exe_path = _app_executable()
        if not exe_path or not os.path.isfile(exe_path):
            log.error(f"Could not locate the application executable to run {current_name}.")
            root.after(0, lambda: sidebar_finalize_queue(qid, mode))
            return
        cmd = [exe_path, "--worker", config_subdir, inv_path, data_path, tpl_path, current_name, f"--{mode}"]
    else:
        cmd = [sys.executable, "-u", main_script, config_subdir, inv_path, data_path, tpl_path, current_name, f"--{mode}"]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
            cwd=_BASE_DIR
        )
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log.info(line)
                parse_device_line(queue_dict, line)
        proc.wait()
    except Exception as e:
        log.error(f"Unexpected error while executing {current_name}: {e}")

    root.after(0, lambda: sidebar_finalize_queue(qid, mode))

def run_single_queue(queue_dict, mode="dry-run"):
    """Validate and submit a single queue for background execution.

    On validation errors an alert is shown and nothing runs. Otherwise the
    project is auto-saved and ``execute_queue_logic`` starts in a daemon
    thread, so the GUI never blocks.

    Args:
        queue_dict (dict): Queue to execute.
        mode (str, optional): ``dry-run`` (default) or ``deploy``.
    """
    current_name = queue_dict["title_label"].cget("text")
    errors = get_queue_errors(queue_dict)
    
    if errors:
        msg_errors = "\n".join([f"- {err}" for err in errors])
        messagebox.showerror(
            "Validation Error",
            f"The '{current_name}' has issues:\n\n{msg_errors}\n\nPlease fix the paths before executing."
        )
        return
    
    auto_save_current_project()
    threading.Thread(
        target=execute_queue_logic,
        args=(queue_dict, mode),
        daemon=True,
        name=f"Queue-{queue_dict['id']}-{mode}"
    ).start()

def run_all_queues(mode="dry-run"):
    """Validate every queue and execute all of them in background threads.

    When any queue has validation problems nothing runs and a single error
    dialog lists every issue. Otherwise the project is auto-saved and one
    daemon thread per queue is started.

    Args:
        mode (str, optional): ``dry-run`` (default) or ``deploy``.
    """
    if not _execution_queues:
        messagebox.showwarning("Warning", "There are no queues to execute.")
        return
    
    invalid_queues_msgs = []
    for queue in _execution_queues:
        current_name = queue["title_label"].cget("text")
        errors = get_queue_errors(queue)
        if errors:
            msg_fila = f"• {current_name}:"
            for err in errors:
                msg_fila += f"\n    - {err}"
            invalid_queues_msgs.append(msg_fila)
    
    if invalid_queues_msgs:
        formatted_messages = "\n\n".join(invalid_queues_msgs)
        messagebox.showerror(
            "Validation Error in Batch",
            f"Could not start the {mode.upper()}.\nProblems found:\n\n{formatted_messages}\n\nPlease fix all fields before executing."
        )
        return
    auto_save_current_project()
    for queue in _execution_queues:
        threading.Thread(
            target=execute_queue_logic,
            args=(queue, mode),
            daemon=True,
            name=f"Queue-{queue['id']}-{mode}"
        ).start()

def get_current_project_state():
    """Build a serializable dictionary with the whole workspace state.

    Returns:
        dict: The project metadata (official file path plus one entry per
        queue). The path is stored as ``None`` while the state comes from an
        internal cache.
    """
    project_path = current_project_filepath
    if _is_cache_path(project_path):
        project_path = None
    return {
        "filepath": project_path,
        "queues": [
            {
                "name": q["title_label"].cget("text"),
                "inventory": q["inventory"].get().strip(),
                "data": q["data"].get().strip(),
                "template": q["template"].get().strip(),
                "last_used_directory": q.get("last_used_directory", _CONFIG_DIR),
            }
            for q in _execution_queues
        ],
    }

def save_project_state(filepath):
    """Write the in-memory state to ``filepath`` as JSON.

    Args:
        filepath (str): Destination file on the filesystem.

    Returns:
        bool: ``True`` on success, ``False`` after showing an error dialog
        (serialization or I/O failure).
    """
    state = get_current_project_state()
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4)
        return True
    except TypeError as e:
        messagebox.showerror(
            "Serialization Error",
            f"Failed to encode project data to JSON format:\n{e}"
        )
        return False
    except Exception as e:
        messagebox.showerror(
            "Error in Save",
            f"Error occurred while saving the project:\n{e}"
        )
        return False

def auto_save_current_project():
    """Silently save the current state into the auto-save cache only.

    Returns:
        bool: ``True`` when the auto-save cache was written.
    """
    return save_project_state(_AUTOSAVE_FILE)

def save_project():
    """Save the current project.

    When there is no official project file yet (or only a cache path),
    ``save_project_as`` is used to ask for a destination. Otherwise the file
    and the session cache are updated and a confirmation is shown.

    Returns:
        bool: ``True`` when the project was written to disk.
    """
    global current_project_filepath
    if _is_cache_path(current_project_filepath):
        current_project_filepath = None
    if not current_project_filepath or not os.path.isfile(current_project_filepath):
        return save_project_as()
    if save_project_state(current_project_filepath):
        save_project_state(_CACHE_FILE)
        messagebox.showinfo("Success", "Project saved successfully!")
        return True
    return False

def save_project_as():
    """Ask for a destination file and save the project there.

    Refuses paths that correspond to internal cache files. On success both the
    chosen ``.json`` file and the session cache are written.

    Returns:
        bool: ``True`` when the project was saved under the chosen name.
    """
    global current_project_filepath
    filepath = filedialog.asksaveasfilename(
        defaultextension=".json",
        filetypes=[("JSON files", "*.json")],
        initialdir=_CONFIG_DIR,
    )
    if not filepath:
        return False
    if _is_cache_path(filepath):
        messagebox.showerror(
            "Error",
            "You cannot save the project directly into the internal cache file.\n"
            "Please choose another location or name."
        )
        return False
    current_project_filepath = filepath
    if save_project_state(filepath):
        save_project_state(_CACHE_FILE)
        messagebox.showinfo("Success", "Project saved successfully!")
        return True
    return False

def load_project_state(filepath):
    """Load a project JSON and rebuild the whole interface from it.

    Existing queues are torn down and one frame plus one log tab are created
    per entry of the file. Both the current dict format (with a "queues" key)
    and a legacy bare list are accepted. When the file is one of the internal
    caches, the official project path stored inside it is restored (if any).

    Args:
        filepath (str): Path of the JSON file to load.

    Returns:
        bool: ``True`` when the interface was rebuilt.
    """
    global current_project_filepath
    if not os.path.exists(filepath):
        return False
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        is_cache = _is_cache_path(filepath)
        if isinstance(data, dict):
            queues_data = data.get("queues", data.get("queues ", []))
            if is_cache:
                loaded_path = data.get("filepath", data.get("filepath "))
                if (
                    loaded_path
                    and not _is_cache_path(loaded_path)
                    and os.path.isfile(loaded_path)
                ):
                    current_project_filepath = loaded_path
                else:
                    current_project_filepath = None
            else:
                current_project_filepath = None if _is_cache_path(filepath) else filepath
        elif isinstance(data, list):
            queues_data = data
            current_project_filepath = None if is_cache else filepath
        else:
            queues_data = []
            current_project_filepath = None
            
        for q in list(_execution_queues):
            queue_dict = q
            sidebar_remove_queue(queue_dict)
            queue_dict["frame"].destroy()
            log_notebook.forget(queue_dict["tab"])
            _execution_queues.remove(queue_dict)
            
        for idx, q_data in enumerate(queues_data):
            next_id = idx + 1
            name = q_data.get("name", f"Queue {next_id}")
            queue_frame = tk.LabelFrame(queues_container, padx=10, pady=10)
            queue_frame.pack(fill="x", padx=10, pady=5)
            queue_frame.columnconfigure(1, weight=1)
            title_label = tk.Label(queue_frame, text=name, cursor="hand2", fg="#0284c7", font=("Arial", 9, "bold"))
            queue_frame.config(labelwidget=title_label)
            queue_dict = {
                "id": next_id,
                "frame": queue_frame,
                "title_label": title_label,
                "last_used_directory": q_data.get("last_used_directory", _CONFIG_DIR)
            }
            inv_entry = create_file_row(queue_frame, 0, "Inventory path:", "Select an inventory", [("YAML", "*.yml *.yaml"), ("All", "*.*")], "inventory", queue_dict)
            data_entry = create_file_row(queue_frame, 1, "Data path:", "Select a data file", [("YAML", "*.yml *.yaml"), ("All", "*.*")], "data", queue_dict)
            tpl_entry = create_file_row(queue_frame, 2, "Jinja Template path:", "Select a template", [("Jinja", "*.j2"), ("All", "*.*")], "template", queue_dict)
            inv_entry.insert(0, q_data.get("inventory", ""))
            data_entry.insert(0, q_data.get("data", ""))
            tpl_entry.insert(0, q_data.get("template", ""))
            queue_dict["inventory"] = inv_entry
            queue_dict["data"] = data_entry
            queue_dict["template"] = tpl_entry
            tab_frame = tk.Frame(log_notebook)
            log_notebook.add(tab_frame, text=name)
            queue_dict["tab"] = tab_frame
            terminal_text = st.ScrolledText(tab_frame, state=tk.DISABLED, bg="#1e1e1e", fg="#00ff00", font=("Consolas", 10), height=10)
            terminal_text.pack(fill="both", expand=True, padx=2, pady=2)
            logger = logging.getLogger(f"Fila_{next_id}")
            logger.setLevel(logging.DEBUG)
            logger.handlers.clear()
            handler = TextHandler(terminal_text)
            handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S'))
            logger.addHandler(handler)
            queue_dict["logger"] = logger
            title_label.bind("<Button-1>", lambda e, q=queue_dict: rename_queue(q))
            actions_frame = tk.Frame(queue_frame)
            actions_frame.grid(row=3, column=0, columnspan=3, sticky="e", pady=(5, 0))
            btn_dryrun = tk.Button(actions_frame, text="Dry-run", command=lambda q=queue_dict: run_single_queue(q, mode="dry-run"))
            btn_dryrun.pack(side=tk.LEFT, padx=(0, 5))
            btn_deploy = tk.Button(actions_frame, text="Deploy", command=lambda q=queue_dict: run_single_queue(q, mode="deploy"))
            btn_deploy.pack(side=tk.LEFT, padx=(0, 5))
            btn_remove = tk.Button(actions_frame, text="Remove", fg="red", command=lambda q=queue_dict: remove_queue(q))
            btn_remove.pack(side=tk.LEFT)
            _execution_queues.append(queue_dict)
            sidebar_ensure_queue(queue_dict)
            
        queues_container.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))
        return True
    
    except json.JSONDecodeError as e:
        messagebox.showerror("Format Error", f"Failed to parse project file structure:\n{e}")
        return False
    except Exception as e:
        messagebox.showerror("Error loading", f"Could not load the project:\n{e}")
        return False

def load_project():
    """Offer to save pending changes, then load a project chosen by the user."""
    if current_project_filepath:
        if _has_unsaved_project_changes():
            response = messagebox.askyesnocancel(
                "Save",
                "There are unsaved changes in the current project.\n"
                "Do you want to save them before loading another project?"
            )
            if response is None:
                return
            if response and not save_project_state(current_project_filepath):
                return
    elif has_unsaved_changes():
        response = messagebox.askyesnocancel("Save", "Do you want to save the current project before loading a new one?")
        if response is True:
            if not save_project_as():
                return
        elif response is None:
            return
    filepath = filedialog.askopenfilename(initialdir=_CONFIG_DIR, filetypes=[("JSON files", "*.json")])
    if filepath:
        if load_project_state(filepath):
            save_project_state(_CACHE_FILE)
            messagebox.showinfo("Success", "Project loaded successfully!")

def new_project():
    """Reset the workspace, closing every queue and starting a single empty one."""
    global current_project_filepath
    if current_project_filepath:
        if _has_unsaved_project_changes():
            response = messagebox.askyesnocancel(
                "Save",
                "There are unsaved changes in the current project.\n"
                "Do you want to save them before creating a new one?"
            )
            if response is None:
                return
            if response and not save_project_state(current_project_filepath):
                return
    elif has_unsaved_changes():
        response = messagebox.askyesnocancel("Save", "Do you want to save the current project before creating a new one?")
        if response is True:
            if not save_project_as():
                return
        elif response is None:
            return
    for q in list(_execution_queues):
        queue_dict = q
        sidebar_remove_queue(queue_dict)
        queue_dict["frame"].destroy()
        log_notebook.forget(queue_dict["tab"])
        _execution_queues.remove(queue_dict)
    current_project_filepath = None
    add_queue()
    save_project_state(_CACHE_FILE)

def has_unsaved_changes():
    """Light check for any text typed in the queue path fields.

    Used when there is no official project file to compare against.

    Returns:
        bool: ``True`` when at least one path field is filled.
    """
    for queue in _execution_queues:
        inv = queue["inventory"].get().strip()
        data = queue["data"].get().strip()
        tpl = queue["template"].get().strip()
        if inv or data or tpl:
            return True 
    return False

def restart_app():
    """Restart the application by replacing the current process.

    Pending state is saved into the caches first; the executable is then
    re-executed (the binary itself when compiled, the current interpreter
    otherwise) so logging and environment are reloaded from scratch.
    """
    if current_project_filepath:
        auto_save_current_project()
    else:
        save_project_state(_CACHE_FILE)
    _sync_caches()
    root.destroy()
    if _COMPILED:
        exe_path = _app_executable()
        if exe_path:
            os.execv(exe_path, [exe_path] + sys.argv[1:])
            return
    os.execv(sys.executable, [sys.executable] + sys.argv)

def on_closing():
    """Handle the window close event.

    With an official project open: "Yes" writes to the official file and syncs
    the caches; "No" overwrites the caches with the project content
    (effectively discarding the pending changes); "Cancel" aborts the exit.
    Without an official project, the work in progress is kept in the caches.

    The Tk image reference is cleared before teardown to avoid errors during
    interpreter shutdown.
    """
    global icon  # Bring the image reference into scope
    icon = None  # Drop the image before Python shuts down
    if current_project_filepath and not _is_cache_path(current_project_filepath):
        if _has_unsaved_project_changes():
            response = messagebox.askyesnocancel(
                "Exit",
                "There are unsaved changes in the current project.\n"
                "Do you want to save them before exiting?"
            )
            if response is None:
                return
            if response:
                if not save_project_state(current_project_filepath):
                    return
                _sync_caches()
            else:
                _overwrite_caches_with_project()
        else:
            _sync_caches()
        root.quit()
        root.destroy()
    elif has_unsaved_changes():
        response = messagebox.askyesnocancel("Exit", "Do you want to save the current project before exiting?")
        if response is True:
            if save_project_as():
                _sync_caches()
                root.quit()
                root.destroy()
        elif response is False:
            _sync_caches()   # no official project: the draft survives for the next session
            root.destroy()
        else:
            return
    else:
        _sync_caches()
        root.quit()
        root.destroy()

def init_app():
    """Restore the session when the application opens.

    Loads the session cache when present; if the load fails, a fresh queue is
    created. After a successful load, ``_offer_session_recovery`` checks
    whether the previous session ended abruptly and offers to recover the
    pending work.
    """
    if os.path.exists(_CACHE_FILE):
        if not load_project_state(_CACHE_FILE):
            add_queue()
        else:
            _offer_session_recovery()
    else:
        add_queue()

def _offer_session_recovery():
    """Offer recovery when the previous session did not end cleanly.

    An unclean exit (crash, kill, power loss) is detected by comparing the
    continuous auto-save cache with the session cache: when they differ there
    were pending changes and the user decides whether to recover them.
    """
    if not os.path.exists(_AUTOSAVE_FILE):
        return
    try:
        with open(_AUTOSAVE_FILE, "r", encoding="utf-8") as f:
            autosave_state = json.load(f)
    except Exception:
        return
    if autosave_state == get_current_project_state():
        return
    response = messagebox.askyesno(
        "Session Recovery",
        "Unsaved changes from the previous session were detected.\n"
        "Do you want to recover them?"
    )
    if response:
        load_project_state(_AUTOSAVE_FILE) 
    else:
        save_project_state(_AUTOSAVE_FILE)

def add_queue():
    """Create a new queue frame with its log tab, logger and action buttons.

    The queue receives the smallest free numeric id and is registered in the
    scrolling panel and in the device tree.
    """
    existing_ids = [q["id"] for q in _execution_queues]
    next_id = 1
    while next_id in existing_ids:
        next_id += 1
    default_name = f"Queue {next_id}"
    queue_frame = tk.LabelFrame(queues_container, padx=10, pady=10)
    queue_frame.pack(fill="x", padx=10, pady=5)
    queue_frame.columnconfigure(1, weight=1)
    title_label = tk.Label(
        queue_frame, 
        text=default_name, 
        cursor="hand2", 
        fg="#0284c7", 
        font=("Arial", 9, "bold")
    )
    queue_frame.config(labelwidget=title_label)
    queue_data = {
        "id": next_id,
        "frame": queue_frame,
        "title_label": title_label,
        "last_used_directory": _CONFIG_DIR
    }
    
    inv_entry = create_file_row(queue_frame, 0, "Inventory path:", "Select an inventory", [("YAML", "*.yml *.yaml"), ("All", "*.*")], "inventory", queue_data)
    data_entry = create_file_row(queue_frame, 1, "Data path:", "Select a data file", [("YAML", "*.yml *.yaml"), ("All", "*.*")], "data", queue_data)
    tpl_entry = create_file_row(queue_frame, 2, "Jinja Template path:", "Select a template", [("Jinja", "*.j2"), ("All", "*.*")], "template", queue_data)
    queue_data["inventory"] = inv_entry
    queue_data["data"] = data_entry
    queue_data["template"] = tpl_entry
    
    tab_frame = tk.Frame(log_notebook)
    log_notebook.add(tab_frame, text=default_name)
    queue_data["tab"] = tab_frame
    
    terminal_text = st.ScrolledText(tab_frame, state=tk.DISABLED, bg="#1e1e1e", fg="#00ff00", font=("Consolas", 10), height=10)
    terminal_text.pack(fill="both", expand=True, padx=2, pady=2)
    
    logger = logging.getLogger(f"Fila_{next_id}")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear() 
    
    handler = TextHandler(terminal_text)
    handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S'))
    logger.addHandler(handler)
    
    queue_data["logger"] = logger
    
    title_label.bind("<Button-1>", lambda e, q=queue_data: rename_queue(q))
    
    actions_frame = tk.Frame(queue_frame)
    actions_frame.grid(row=3, column=0, columnspan=3, sticky="e", pady=(5, 0))
    
    btn_dryrun = tk.Button(actions_frame, text="Dry-run", command=lambda q=queue_data: run_single_queue(q, mode="dry-run"))
    btn_dryrun.pack(side=tk.LEFT, padx=(0, 5))
    
    btn_deploy = tk.Button(actions_frame, text="Deploy", command=lambda q=queue_data: run_single_queue(q, mode="deploy"))
    btn_deploy.pack(side=tk.LEFT, padx=(0, 5))
    
    btn_remove = tk.Button(actions_frame, text="Remove", fg="red", command=lambda q=queue_data: remove_queue(q))
    btn_remove.pack(side=tk.LEFT)
    
    _execution_queues.append(queue_data)
    queues_container.update_idletasks()
    canvas.configure(scrollregion=canvas.bbox("all"))
    if queues_container.winfo_reqheight() > canvas.winfo_height():
        canvas.yview_moveto(1.0)
    sidebar_ensure_queue(queue_data)

def _set_paned_split(top_height):
    """Apply explicit, complementary heights to both panes of ``right_paned``.

    Because the two heights always add up to the available height of the
    PanedWindow, no orphan space (the gray band) is left after programmatic
    changes.

    Args:
        top_height (int): Requested height of the queue pane; clamped to keep
            at least 100 px on the top and 150 px on the terminal pane.
    """
    right_paned.update_idletasks()
    total = right_paned.winfo_height()
    if total <= 1:
        return
    top_height = max(100, min(int(top_height), total - 150))
    right_paned.paneconfigure(top_frame, stretch="always")
    right_paned.paneconfigure(bottom_container, stretch="never")
    right_paned.paneconfigure(top_frame, height=top_height)
    right_paned.paneconfigure(bottom_container, height=total - top_height)

def _apply_default_paned_ratio():
    """Apply the initial split: compact terminal (~300 px), queues with the rest.

    Without this adjustment Tk hands the leftover space of the PanedWindow to
    its last pane (the terminal), which would fill almost the whole window at
    startup.
    """
    root.update_idletasks()
    total = right_paned.winfo_height()
    if total <= 1:
        return
    terminal_height = max(180, min(300, total - 250))
    _set_paned_split(total - terminal_height)

def toggle_terminal():
    """Maximize or restore the terminal pane, preserving the previous layout.

    Pane heights are stored before maximizing and re-applied when the terminal
    is collapsed back.
    """
    global _terminal_maximized, _saved_top_height, _saved_bottom_height
    if _terminal_maximized:
        # --- Restore: give the panes back the heights saved before maximizing ---
        right_paned.add(top_frame, minsize=200, before=bottom_container)
        if _saved_top_height:
            root.after(50, lambda h=_saved_top_height: _set_paned_split(h))
        else:
            root.after(50, _apply_default_paned_ratio)
        btn_toggle.config(text="Maximize Terminal")
        _terminal_maximized = False
    else:
        # --- Maximize: store the current heights BEFORE removing the top pane ---
        _saved_top_height = top_frame.winfo_height()
        _saved_bottom_height = bottom_container.winfo_height()
        right_paned.forget(top_frame)
        right_paned.update_idletasks()
        right_paned.paneconfigure(bottom_container, stretch="always")
        right_paned.paneconfigure(bottom_container, height=right_paned.winfo_height())
        btn_toggle.config(text="Restore Terminal")
        _terminal_maximized = True

root = tk.Tk(className='Multiflora')
root.protocol("WM_DELETE_WINDOW", on_closing)
icon = tk.PhotoImage(file = pathlib.Path(__file__).parent / "assets" / "multiflora.png")
root.iconphoto(False, icon)
root.title("Multiflora")
root.geometry("1350x750")
root.minsize(600, 400)

def open_about():
    """Open the panel with information about the application."""
    about_win = tk.Toplevel(root)
    about_win.title("About Multiflora")
    about_win.geometry("600x300")
    about_win.resizable(False, False)
    about_win.grab_set() 
    tk.Label(about_win, text="Multiflora", font=("Arial", 16, "bold"), fg="#0284c7").pack(pady=(20, 5))
    tk.Label(about_win, text="Version 0.0.1").pack(pady=2)
    tk.Label(about_win, text="Platform for Automation and Deployment of Networks").pack(pady=10)
    tk.Label(about_win, text="Developed for the orchestration of configurations,\nvalidation and control of devices.", justify="center").pack(pady=5)
    tk.Label(about_win, text="Made by: Matheus Rosa-Fogo, 2026.\nc@rosafogo.com", justify="center").pack(pady=5)
    tk.Label(about_win, text="ATTENTION: THIS VERSION IS ONLY A PROTOTYPE AND A PROOF OF CONCEPT.").pack(pady=5)

menu_bar = tk.Menu(root)
root.config(menu=menu_bar)
file_menu = tk.Menu(menu_bar, tearoff=0)
menu_bar.add_cascade(label="File", menu=file_menu)
file_menu.add_command(label="New Project", command=new_project)
file_menu.add_command(label="Open Project...", command=load_project)
file_menu.add_command(label="Save", command=save_project)
file_menu.add_command(label="Save Project As...", command=save_project_as)
file_menu.add_separator()
file_menu.add_command(label="Exit", command=on_closing)
help_menu = tk.Menu(menu_bar, tearoff=0)
menu_bar.add_cascade(label="Help", menu=help_menu)
help_menu.add_command(label="About", command=open_about)

main_paned = tk.PanedWindow(root, orient=tk.HORIZONTAL, sashrelief=tk.RAISED, sashwidth=4, bg="#d9d9d9")
main_paned.pack(fill="both", expand=True)

sidebar_width = 300
sidebar = tk.Frame(main_paned, width=sidebar_width, bg="#f5f5f5")
main_paned.add(sidebar, minsize=200)
sidebar.pack_propagate(False)

tk.Label(sidebar, text="Devices", font=("Arial", 10, "bold"), bg="#f5f5f5", anchor="w").pack(fill="x", padx=8, pady=(8, 4))
tree_container = tk.Frame(sidebar, bg="#f5f5f5")
tree_container.pack(fill="both", expand=True, padx=6, pady=(0, 6))
device_tree = ttk.Treeview(tree_container, columns=("status",), show="tree headings", selectmode="none")
tree_scroll = ttk.Scrollbar(tree_container, orient="vertical", command=device_tree.yview)
device_tree.configure(yscrollcommand=tree_scroll.set)
device_tree.pack(side="left", fill="both", expand=True)
tree_scroll.pack(side="right", fill="y")
device_tree.heading("#0", text="Queue / Device", anchor="w")
device_tree.heading("status", text="Status", anchor="w")
col_1_width = 100
col_2_width = 90
device_tree.column("#0", width=col_1_width, minwidth=100)
device_tree.column("status", width=col_2_width, minwidth=80, stretch=False)
device_tree.tag_configure("success",    foreground="#008000")
device_tree.tag_configure("rendered",   foreground="#008000")
device_tree.tag_configure("failure",    foreground="#cc0000")
device_tree.tag_configure("connecting", foreground="#b26a00")
device_tree.tag_configure("applying",   foreground="#b26a00")
device_tree.tag_configure("detected",   foreground="#555555")

def _on_sidebar_mousewheel(event):
    """Scroll the device tree according to the mouse wheel direction."""
    if event.num == 4 or event.delta > 0:
        device_tree.yview_scroll(-1, "units")
    elif event.num == 5 or event.delta < 0:
        device_tree.yview_scroll(1, "units") 

device_tree.bind("<MouseWheel>", _on_sidebar_mousewheel)
device_tree.bind("<Button-4>", _on_sidebar_mousewheel)
device_tree.bind("<Button-5>", _on_sidebar_mousewheel)

right_side = tk.Frame(main_paned)
main_paned.add(right_side, minsize=400)

right_paned = tk.PanedWindow(right_side, orient=tk.VERTICAL, sashrelief=tk.RAISED, sashwidth=4, bg="#d9d9d9")
right_paned.pack(fill="both", expand=True)

top_frame = tk.Frame(right_paned)
right_paned.add(top_frame, minsize=200)

canvas = tk.Canvas(top_frame, highlightthickness=0)
scrollbar = tk.Scrollbar(top_frame, orient="vertical", command=canvas.yview)
queues_container = tk.Frame(canvas)
canvas.configure(yscrollcommand=scrollbar.set)
canvas_window = canvas.create_window((0, 0), window=queues_container, anchor="nw")

def on_canvas_configure(event):
    """Keep the inner frame as wide as the canvas."""
    canvas.itemconfig(canvas_window, width=event.width)
    if queues_container.winfo_reqheight() <= event.height:
        canvas.yview_moveto(0)

def on_frame_configure(event):
    """Update the canvas scroll region when the inner frame changes size."""
    canvas.configure(scrollregion=canvas.bbox("all"))
    if queues_container.winfo_reqheight() <= canvas.winfo_height():
        canvas.yview_moveto(0)

canvas.bind('<Configure>', on_canvas_configure)
queues_container.bind('<Configure>', on_frame_configure)
canvas.pack(side="left", fill="both", expand=True)
scrollbar.pack(side="right", fill="y")

def _on_mousewheel(event):
    """Scroll the queue panel with the mouse wheel when it overflows the canvas."""
    if queues_container.winfo_reqheight() > canvas.winfo_height():
        if event.num == 4 or event.delta > 0:
            canvas.yview_scroll(-1, "units")
        elif event.num == 5 or event.delta < 0:
            canvas.yview_scroll(1, "units")

root.bind_all("<MouseWheel>", _on_mousewheel)
root.bind_all("<Button-4>", _on_mousewheel)
root.bind_all("<Button-5>", _on_mousewheel)

bottom_container = tk.Frame(right_paned)
right_paned.add(bottom_container, minsize=150)
right_paned.paneconfigure(top_frame, stretch="always")
right_paned.paneconfigure(bottom_container, stretch="never")

controls_frame = tk.Frame(bottom_container, padx=20, pady=10)
controls_frame.pack(fill="x", side="top")

btn_add = tk.Button(controls_frame, text="+ Add Queue", command=add_queue, font=("Arial", 10, "bold"))
btn_add.pack(side=tk.LEFT)

btn_toggle = tk.Button(controls_frame, text="Maximize Terminal", command=toggle_terminal, font=("Arial", 10, "bold"))
btn_toggle.pack(side=tk.LEFT, padx=15)

btn_deploy_all = tk.Button(controls_frame, text="Deploy all", command=lambda: run_all_queues("deploy"), font=("Arial", 10, "bold"))
btn_deploy_all.pack(side=tk.RIGHT)

btn_dryrun_all = tk.Button(controls_frame, text="Dry-Run all", command=lambda: run_all_queues("dry-run"), font=("Arial", 10, "bold"))
btn_dryrun_all.pack(side=tk.RIGHT, padx=(0, 10))

terminal_container = tk.LabelFrame(bottom_container, text="Terminal in execution")
terminal_container.pack(fill="both", expand=True, padx=10, pady=(0, 10))

log_notebook = ttk.Notebook(terminal_container)
log_notebook.pack(fill="both", expand=True, padx=5, pady=5)

def _autosave_loop():
    """Keep the recovery cache fresh even without user actions.

    Runs ``auto_save_current_project`` and reschedules itself every 30
    seconds.
    """
    try:
        auto_save_current_project()
    except Exception:
        pass
    root.after(30000, _autosave_loop)

init_app()
root.after(300, _apply_default_paned_ratio)
root.after(30000, _autosave_loop)
root.mainloop()