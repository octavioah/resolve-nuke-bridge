"""
ReopenInNuke.py
================
Optional companion script for DaVinci Resolve. Run it from
Workspace > Scripts when you want to reopen an existing Nuke
composition for a clip that was already sent to Nuke before -- for
example, if Nuke was closed before sending the result back, or if
you want to continue compositing on a shot already in progress.

Unlike SendToNuke.py, this script does NOT re-render the material
from Resolve. It simply finds the existing job folder for the clip
under the playhead and relaunches Nuke with the .nk that is already
there, without touching anything else.

INSTALLATION:
  Copy this file to the same Resolve Scripts folder as SendToNuke.py.
  It appears as a separate entry in Workspace > Scripts.
"""

import sys
import os

try:
    import resolve_nuke_bridge_common as bridge
except ImportError:
    _scripts_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else ""
    for _d in [_scripts_dir, os.path.expanduser("~/.nuke")]:
        if _d and os.path.isdir(_d) and _d not in sys.path:
            sys.path.insert(0, _d)
    import resolve_nuke_bridge_common as bridge


def main():
    resolve = bridge.get_resolve_app()
    if resolve is None:
        print("Could not connect to Resolve.")
        return

    project_manager = resolve.GetProjectManager()
    project = project_manager.GetCurrentProject() if project_manager else None
    if project is None:
        print("No project is currently open.")
        return

    timeline = project.GetCurrentTimeline()
    if timeline is None:
        print("No active timeline. Open a timeline first.")
        return

    selected_item = timeline.GetCurrentVideoItem()
    if selected_item is None:
        print("No clip under the playhead. Move the playhead over a clip first.")
        return

    clip_name = selected_item.GetName() or "clip"
    media_pool_item = selected_item.GetMediaPoolItem()
    if media_pool_item is None:
        print(f"'{clip_name}' has no associated MediaPoolItem.")
        return

    unique_id = media_pool_item.GetUniqueId()
    existing_job = bridge.find_existing_job_for_clip(unique_id)

    if existing_job is None:
        print(
            f"No Nuke job found for '{clip_name}'.\n"
            f"Use SendToNuke.py to send it to Nuke for the first time."
        )
        return

    job_dir = existing_job["job_dir"]
    nk_path = os.path.join(job_dir, "comp.nk")

    if not os.path.exists(nk_path):
        print(
            f"A job record exists for '{clip_name}' but the .nk file "
            f"is no longer at:\n{nk_path}\n"
            f"It may have been moved or deleted."
        )
        return

    print(f"Reopening Nuke for '{clip_name}': {nk_path}")
    launched = bridge.launch_nuke(nk_path)
    if launched:
        print("Nuke launched successfully.")
    else:
        print(f"Could not launch Nuke automatically. Open it manually:\n{nk_path}")


if __name__ == "__main__":
    main()
else:
    main()
