"""
resolve_nuke_bridge_common.py
==============================
Module shared by both sides of the Resolve <-> Nuke bridge.

This file must be accessible BOTH from Resolve (placed in the same
folder as SendToNuke.py) AND from Nuke (placed in ~/.nuke/), because
both sides import it.

Key functions:
  - get_resolve_app(): connects to the already-running Resolve
    instance. Works both when run FROM Resolve (where the module is
    already natively available) and FROM Nuke (where the scripting
    API paths must be added to sys.path first).
  - standard cross-platform exchange paths (Windows/Mac).
  - reading/writing the bridge's metadata JSON.
"""

import os
import sys
import json
import platform
import datetime
import shutil
import re


# --- Cross-platform paths for the Resolve scripting API --------------
# Taken from Blackmagic Design's official documentation (Resolve
# Scripting Readme). Needed so a process EXTERNAL to Resolve (such as
# Nuke) can import the DaVinciResolveScript module.

def _resolve_api_paths():
    """
    Returns (resolve_script_api, resolve_script_lib, modules_dir)
    depending on the detected operating system. These paths are the
    ones Blackmagic Design officially documents for each platform.
    """
    system = platform.system()  # "Windows", "Darwin" (Mac), "Linux"

    if system == "Windows":
        programdata = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        api = os.path.join(programdata, "Blackmagic Design", "DaVinci Resolve",
                            "Support", "Developer", "Scripting")
        lib = os.path.join(program_files, "Blackmagic Design", "DaVinci Resolve",
                            "fusionscript.dll")
        modules = os.path.join(api, "Modules")
        return api, lib, modules

    elif system == "Darwin":
        api = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
        lib = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
        modules = os.path.join(api, "Modules")
        return api, lib, modules

    else:
        # Linux, included for completeness even though this course
        # targets Win/Mac.
        api = "/opt/resolve/Developer/Scripting"
        lib = "/opt/resolve/libs/Fusion/fusionscript.so"
        modules = os.path.join(api, "Modules")
        return api, lib, modules


def get_resolve_app():
    """
    Returns the 'resolve' scripting API object, connecting to the
    DaVinci Resolve instance that is already open.

    Works in two different contexts:
    1. Run FROM INSIDE Resolve (a script in Scripts/Edit or
       Scripts/Utility): the DaVinciResolveScript module is already
       available with no extra setup.
    2. Run FROM OUTSIDE Resolve (for example, from Nuke): the
       scripting API's "Modules" folder must be added to sys.path
       before it can be imported.

    Returns None if it could not connect (for example, if Resolve is
    not open in the external use case).
    """
    try:
        import DaVinciResolveScript as dvr_script
    except ImportError:
        _api, _lib, modules_dir = _resolve_api_paths()
        if modules_dir not in sys.path:
            sys.path.append(modules_dir)
        try:
            import DaVinciResolveScript as dvr_script
        except ImportError as e:
            print(f"[resolve_nuke_bridge] Could not import DaVinciResolveScript: {e}")
            print(f"[resolve_nuke_bridge] Tried adding to path: {modules_dir}")
            print("[resolve_nuke_bridge] Check that DaVinci Resolve Studio is installed and running.")
            return None

    try:
        resolve = dvr_script.scriptapp("Resolve")
    except Exception as e:
        print(f"[resolve_nuke_bridge] Error connecting to Resolve: {e}")
        return None

    if resolve is None:
        print("[resolve_nuke_bridge] Could not connect to Resolve. Is it running?")
        return None

    return resolve


# --- Exchange paths (folder where renders + metadata live) -----------

def get_bridge_root():
    """
    Root folder where intermediate renders and generated .nk files
    are stored. Same logical location on Windows and Mac: inside the
    user's home folder, to avoid permission issues in system folders.
    """
    root = os.path.join(os.path.expanduser("~"), "NukeResolveBridge")
    os.makedirs(root, exist_ok=True)
    return root


def get_job_dir(job_name):
    """
    Folder for a specific submission (one clip). Created inside the
    bridge root, with separate subfolders for the input material
    (from Resolve) and the output (from Nuke).
    """
    job_dir = os.path.join(get_bridge_root(), job_name)
    os.makedirs(os.path.join(job_dir, "from_resolve"), exist_ok=True)
    os.makedirs(os.path.join(job_dir, "from_nuke"), exist_ok=True)
    return job_dir


# --- Bridge metadata (JSON) -------------------------------------------

def metadata_path(job_dir):
    return os.path.join(job_dir, "bridge_metadata.json")


def write_metadata(job_dir, data):
    """
    Writes the metadata JSON. 'data' should include at least:
    media_pool_item_id, frame_start, frame_end, fps, width, height,
    source_render_path, job_name, created_at.
    """
    data = dict(data)
    data.setdefault("created_at", datetime.datetime.now().isoformat(timespec="seconds"))
    path = metadata_path(job_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def read_metadata(job_dir):
    path = metadata_path(job_dir)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def update_metadata(job_dir, updates):
    """
    Merges the given updates into the existing metadata JSON for a
    job, without disturbing any fields not mentioned in 'updates'.
    Used by send_back_to_resolve.py to record where the Nuke result
    ended up the first time it is placed on the timeline
    (nuke_result_media_pool_item_id, nuke_result_track_index), so
    later resubmissions of the same clip can find and update it in
    place via ReplaceClip() instead of placing a brand new clip every
    time.

    Returns the merged metadata dict, or None if no metadata exists
    yet for this job_dir (the reason is printed to the console in
    that case).
    """
    existing = read_metadata(job_dir)
    if existing is None:
        print(f"[resolve_nuke_bridge] No existing metadata found at {job_dir} to update.")
        return None
    existing.update(updates)
    write_metadata(job_dir, existing)
    return existing


def find_existing_job_for_clip(media_pool_item_id):
    """
    Looks up, in the centralized job index, whether one already
    exists for the given media_pool_item_id. Returns a dict with at
    least 'job_dir' (the job's real path, which can be in ANY folder
    on disk freely chosen by the user, not just inside
    get_bridge_root()), or None if nothing is registered.

    A centralized index is used (instead of scanning folders) because
    new jobs are saved wherever the user picks via a dialog, so there
    is no single fixed root folder to search them all in.
    """
    index = _read_job_index()
    entry = index.get(media_pool_item_id)
    if not entry:
        return None
    return entry


def register_job_location(media_pool_item_id, job_dir):
    """
    Adds or updates, in the centralized index, the real location of
    the job for a given clip. Must be called every time a NEW job is
    created, so find_existing_job_for_clip() can find it on future
    submissions of the same clip, regardless of which folder on disk
    the user saved it in.
    """
    index = _read_job_index()
    index[media_pool_item_id] = {
        "job_dir": job_dir,
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    _write_job_index(index)


def _job_index_path():
    return os.path.join(get_bridge_root(), "job_index.json")


def _read_job_index():
    path = _job_index_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # Corrupt or unreadable index: treat it as empty instead of
        # breaking the whole bridge flow over a cache file.
        return {}


def _write_job_index(index):
    path = _job_index_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)


# --- Cross-platform Nuke executable detection -------------------------

def find_nuke_executable(preferred_path=None):
    """
    Looks for the Nuke executable installed on the system.

    If 'preferred_path' is provided and exists, it is used directly
    (lets the user pin a manual path if auto-detection fails or
    there are several versions and they want to pick one).

    Otherwise, it searches dynamically in the standard install
    folders. It prioritizes bundles containing "nukex" in the name
    over plain Nuke (on Mac, Foundry ships NukeX as a separate .app
    bundle, not as a command-line flag on the same executable). It
    explicitly excludes "Non-Commercial" variants when found
    alongside a commercial version. If there are several candidate
    versions within the same group (NukeX or plain Nuke), it keeps
    whichever sorts last alphabetically by path, which usually -but
    is not guaranteed to- correspond to the most recent one.

    Returns the path to the executable, or None if nothing was found.
    """
    if preferred_path and os.path.exists(preferred_path):
        return preferred_path

    system = platform.system()
    candidates = []

    if system == "Windows":
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        if os.path.isdir(program_files):
            for entry in os.listdir(program_files):
                if entry.lower().startswith("nuke"):
                    folder = os.path.join(program_files, entry)
                    # The main .exe usually shares the same base name
                    # as the folder, minus the trailing "vN" subversion
                    # suffix.
                    for fname in os.listdir(folder):
                        if fname.lower().endswith(".exe") and "nuke" in fname.lower():
                            priority = "z_nukex_" if "nukex" in fname.lower() else "a_"
                            candidates.append((priority + folder, os.path.join(folder, fname)))

    elif system == "Darwin":
        apps_dir = "/Applications"
        if os.path.isdir(apps_dir):
            # On Mac, Foundry installs inside a CONTAINER folder
            # (e.g. "Nuke17.0v2", without a .app extension) that
            # groups several distinct .app bundles (Nuke, NukeX,
            # NukeAssist, NukeIndie, NukeStudio, Hiero...), including
            # "Non-Commercial" variants. The real executable inside
            # each bundle's Contents/MacOS/ is called "droplet" (it
            # does not contain "nuke" in its name), so it cannot be
            # filtered by filename in there.
            for entry in os.listdir(apps_dir):
                if "nuke" not in entry.lower():
                    continue
                container = os.path.join(apps_dir, entry)
                if not os.path.isdir(container):
                    continue

                # If the entry itself is already a .app bundle (older
                # or different Foundry installs might not use a
                # container folder), handle it directly.
                search_dirs = [container] if container.lower().endswith(".app") else []
                if not search_dirs and os.path.isdir(container):
                    search_dirs = [
                        os.path.join(container, sub) for sub in os.listdir(container)
                        if sub.lower().endswith(".app")
                    ]

                for app_bundle in search_dirs:
                    name_lower = os.path.basename(app_bundle).lower()
                    if "non-commercial" in name_lower:
                        continue
                    macos_dir = os.path.join(app_bundle, "Contents", "MacOS")
                    if not os.path.isdir(macos_dir):
                        continue
                    # NOTE: on Mac, the internal executable of recent
                    # Nuke bundles is called "droplet" and ONLY
                    # accepts files via macOS's drag-and-drop Apple
                    # Events mechanism (the same one Finder uses on
                    # double-click), NOT via traditional command-line
                    # argv (confirmed empirically: passing the .nk
                    # path as a normal argument opens Nuke empty, but
                    # dragging the file onto the icon DOES load the
                    # project correctly). That is why we return the
                    # full .app BUNDLE path here (not the internal
                    # "droplet" executable), so it can later be opened
                    # with the native "open -a" command, which
                    # reproduces that same mechanism.
                    has_executable = any(
                        os.path.isfile(os.path.join(macos_dir, f))
                        and os.access(os.path.join(macos_dir, f), os.X_OK)
                        for f in os.listdir(macos_dir)
                    )
                    if has_executable:
                        priority = "z_nukex_" if "nukex" in name_lower else "a_"
                        candidates.append((priority + app_bundle, app_bundle))

    if not candidates:
        return None

    # candidates is a list of (sort_key, real_path) tuples. We sort by
    # the key (which prefixes "z_nukex_" to prioritize NukeX over
    # plain Nuke, and within each group sorts by path, which tends to
    # favor the most recent version) and take the last one, returning
    # just the path.
    candidates.sort(key=lambda c: c[0])
    return candidates[-1][1]


def launch_nuke(nk_path, preferred_nuke_path=None, nukex=True):
    """
    Launches Nuke with the given .nk file, in a separate process
    (non-blocking: does not wait for Nuke to close).

    On Windows, the executable is invoked directly with the .nk as a
    command-line argument, adding --nukex if requested (it is the
    same executable for Nuke and NukeX on Windows; --nukex only
    raises the license tier).

    On Mac, find_nuke_executable() returns the path to the .app
    BUNDLE (not to the internal "droplet" executable), because that
    executable only accepts files via macOS's drag-and-drop Apple
    Events mechanism, not via traditional command-line argv
    (confirmed empirically). That is why the native "open -a" command
    is used here, which reproduces that exact same mechanism. Since
    the NukeX bundle is already NukeX by itself, no extra flag is
    needed to raise the license tier on Mac.

    Returns True if it could be launched, False if Nuke was not found.
    """
    import subprocess

    nuke_target = find_nuke_executable(preferred_nuke_path)
    if not nuke_target:
        print("[resolve_nuke_bridge] No installed Nuke executable was found.")
        return False

    system = platform.system()

    try:
        if system == "Darwin":
            cmd = ["open", "-a", nuke_target, nk_path]
        else:
            cmd = [nuke_target]
            if nukex:
                cmd.append("--nukex")
            cmd.append(nk_path)

        subprocess.Popen(cmd)
        return True
    except Exception as e:
        print(f"[resolve_nuke_bridge] Error launching Nuke: {e}")
        return False


def find_media_pool_item_by_id(project, unique_id):
    """
    Walks the project's Media Pool looking for the MediaPoolItem
    whose GetUniqueId() matches unique_id. Returns the item or None.

    The Resolve API does not offer a direct lookup by ID, so the
    Media Pool's folders (bins) must be traversed recursively.
    """
    media_pool = project.GetMediaPool()
    if not media_pool:
        return None

    root_folder = media_pool.GetRootFolder()
    if not root_folder:
        return None

    def _search_folder(folder):
        for clip in folder.GetClipList() or []:
            try:
                if clip.GetUniqueId() == unique_id:
                    return clip
            except Exception:
                continue
        for sub in folder.GetSubFolderList() or []:
            found = _search_folder(sub)
            if found:
                return found
        return None

    return _search_folder(root_folder)


# --- Preserving the original clip in the Media Pool -------------------

_SEQUENCE_PATTERN = re.compile(r"^(.*?)\[(\d+)-(\d+)\](.*)$")


def _resolve_source_files(source_path):
    """
    Given the "File Path" string Resolve reports for a MediaPoolItem,
    returns the list of actual file paths on disk that make up that
    clip.

    For a single file, this is just [source_path] (after confirming
    it really exists). For an image sequence, Resolve reports a
    SINGLE STRING following the pattern
    "<prefix>[<first>-<last>]<suffix>" (e.g.
    "Plate01.[000-075].png"), which is NOT a real path on disk by
    itself (confirmed empirically: checking it with os.path.isfile()
    always fails for sequences, since no file is literally named like
    that). In that case, the matching individual frame files are
    listed by combining the prefix and suffix around the numeric
    range.

    Returns an empty list if nothing could be resolved.
    """
    if os.path.isfile(source_path):
        return [source_path]

    folder = os.path.dirname(source_path)
    base_name = os.path.basename(source_path)
    match = _SEQUENCE_PATTERN.match(base_name)
    if not match or not os.path.isdir(folder):
        return []

    prefix, first_str, last_str, suffix = match.groups()
    padding = len(first_str)
    first_frame = int(first_str)
    last_frame = int(last_str)

    files = []
    for frame in range(first_frame, last_frame + 1):
        frame_str = str(frame).zfill(padding)
        candidate = os.path.join(folder, f"{prefix}{frame_str}{suffix}")
        if os.path.isfile(candidate):
            files.append(candidate)

    return files


# --- Preserving the original clip before the Nuke roundtrip -----------

import shutil

_SEQUENCE_PATTERN = re.compile(r"^(.*?)\[(\d+)-(\d+)\](.*)$")
_FRAME_NUMBER_PATTERN = re.compile(r"^(.*?)(\d+)(\.\w+)$")

