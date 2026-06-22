"""
SendToNuke.py
=============
Script for DaVinci Resolve Studio. Exports the selected
TimelineItem to an EXR sequence, generates a Nuke project with a
Read/Write already set up, and automatically opens Nuke with that
project.

INSTALLATION:
  Copy this file and resolve_nuke_bridge_common.py together to
  Resolve's utility scripts folder:

  Windows: %APPDATA%\\Blackmagic Design\\DaVinci Resolve\\Fusion\\Scripts\\Edit\\
  Mac:     ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Edit/

  After copying, it will appear in Resolve under:
  Workspace > Scripts > SendToNuke

USAGE:
  1. On Resolve's Edit or Cut page, move the PLAYHEAD over the
     timeline clip you want to send to Nuke (by clicking its
     position in the timeline). The Resolve API offers no way to
     detect a visual clip "selection", so the clip under the
     playhead is used instead, the same way native "Send to Fusion"
     does.
  2. Run this script from Workspace > Scripts > SendToNuke. The FULL
     source file is sent to Nuke (every frame of the underlying
     material on disk), not just the range used by the specific
     piece under the playhead -- if that same material has been cut
     into several pieces on the timeline, all of them are detected
     automatically, so the result can later be placed back
     reproducing the same cuts (see send_back_to_resolve.py).
     - If this is the FIRST TIME you send THIS SPECIFIC clip to
       Nuke, you will be asked to choose a folder to save the Nuke
       project in (via a native dialog). Resolve will render the
       full material to EXR and automatically open Nuke with a Read
       and a Write already set up, in that folder.
     - If you had ALREADY sent this same clip to Nuke before (it is
       identified by the clip itself, not by its name, so this works
       even if you rename it), the existing Nuke project is
       REUSED automatically, with no prompt: the material is
       re-rendered fresh from Resolve, but the .nk itself is left
       untouched, so any compositing work you already did on it
       (blur, grain, whatever) is preserved. This lets you go back
       and forth between Resolve and Nuke on the same shot without
       losing work.
  3. Compose normally in Nuke. When done, use the Render > Send back
     to Resolve menu item (or the ctrl+alt+r shortcut).
"""

import os
import sys
import re
import time

# IMPORTANT: __file__ CANNOT be used here. When Resolve runs a script
# from its Workspace > Scripts menu, it loads it internally (not as
# an independent Python process), and __file__ is not defined in that
# context, which causes a NameError if used (confirmed empirically).
# Instead, we first try importing the common module directly
# (Resolve might already have the script's folder in its own
# sys.path), and only if that fails do we try the standard install
# locations documented in the README, without relying on knowing
# "where am I" myself.
try:
    import resolve_nuke_bridge_common as bridge
except ImportError:
    _candidate_dirs = [
        os.path.expanduser(
            "~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Edit"
        ),
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Edit",
        os.path.expandvars(
            r"%APPDATA%\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Edit"
        ),
    ]
    for _d in _candidate_dirs:
        if os.path.isdir(_d) and _d not in sys.path:
            sys.path.append(_d)
    import resolve_nuke_bridge_common as bridge


def _sanitize_job_name(name):
    """Turns the clip name into something safe to use as a folder/file name."""
    safe = re.sub(r"[^A-Za-z0-9_\-]+", "_", name).strip("_")
    return safe or "clip"


def main():
    resolve = bridge.get_resolve_app()
    if resolve is None:
        print("Could not connect to Resolve. This script must be run from inside Resolve.")
        return

    project_manager = resolve.GetProjectManager()
    project = project_manager.GetCurrentProject() if project_manager else None
    if project is None:
        print("No Resolve project is open.")
        return

    timeline = project.GetCurrentTimeline()
    if timeline is None:
        print("No timeline is active. Open a timeline on the Edit page first.")
        return

    # Read fps here (before any _render_from_resolve calls) so it can
    # be passed as a parameter for correct timecode calculation.
    fps = float(timeline.GetSetting("timelineFrameRate") or 24.0)

    # IMPORTANT NOTE: the Resolve API offers no method to know which
    # clip is visually "selected" in the timeline (this is a
    # confirmed and widely requested limitation, unresolved by
    # Blackmagic Design as of today). What it DOES offer is
    # GetCurrentVideoItem(), which returns the clip under the
    # PLAYHEAD (the playback cursor). That's why the usage flow is:
    # place the playhead over the clip you want to send to Nuke (by
    # clicking on it in the timeline, which also moves the playhead
    # there) and run the script. This is, in fact, the same behavior
    # native "Send to Fusion" uses in Resolve.
    selected_item = timeline.GetCurrentVideoItem()

    if selected_item is None:
        print("No clip was detected under the playhead.")
        print("Move the playhead over the clip you want to send to Nuke (by clicking its position in the timeline) and try again.")
        return

    clip_name = selected_item.GetName() or "clip"

    media_pool_item = selected_item.GetMediaPoolItem()
    if media_pool_item is None:
        print(f"Clip '{clip_name}' has no associated MediaPoolItem (is it a generator or title?). Cannot send to Nuke.")
        return

    unique_id = media_pool_item.GetUniqueId()

    # IMPORTANT: the FULL source material is sent to Nuke (every
    # frame of the underlying file), not just the range used by the
    # specific piece under the playhead. When the result comes back,
    # ReplaceClip() updates this same MediaPoolItem directly, which
    # automatically reflects everywhere this material is used in the
    # project regardless of how many cuts/instances there are.
    #
    # The actual frame range is read from the MediaPoolItem itself
    # (GetClipProperty "Start"/"End") rather than assuming the
    # material always starts at frame 0. Production sequences
    # routinely start at 1001, 1000, 101 or any other number, and
    # passing startFrame=0 to AppendToTimeline when the material
    # actually starts at 1001 makes Resolve freeze on the first
    # valid frame for every output frame -- which is exactly the
    # "all frames identical" symptom. Using the real Start/End
    # values avoids this entirely.
    try:
        props = media_pool_item.GetClipProperty()
        source_start = int(props["Start"])
        source_end = int(props["End"])
        total_frames = source_end - source_start + 1
    except (TypeError, ValueError, KeyError) as e:
        print(f"Could not determine the frame range for '{clip_name}': {e}. Cannot send to Nuke.")
        return
    if total_frames <= 0:
        print(f"'{clip_name}' appears to have no frames (Start={source_start}, End={source_end}). Cannot send to Nuke.")
        return

    # --- Does a Nuke project already exist for this clip? ---
    # Identified by the clip's ID in the Media Pool (stable even if
    # renamed), not by its name. If one exists, it is reused with NO
    # PROMPT (including the existing .nk, so prior compositing work
    # is not lost), exactly as decided: a save location is only
    # asked for when the clip is genuinely new.
    existing_job = bridge.find_existing_job_for_clip(unique_id)

    if existing_job is not None:
        job_dir = existing_job["job_dir"]
        nk_path = os.path.join(job_dir, "comp.nk")
        if os.path.exists(nk_path):
            print(f"A Nuke project already exists for '{clip_name}'. Reusing: {nk_path}")
            # The material is re-rendered fresh from Resolve (in case
            # the content or frame range changed since the previous
            # submission), but the EXISTING .nk is not touched or
            # regenerated, to preserve any compositing work already
            # done on it (blur, grain, whatever).
            render_dir = os.path.join(job_dir, "from_resolve")
            _render_from_resolve(project, render_dir, media_pool_item, source_start, source_end, fps, clip_name)
            launched = bridge.launch_nuke(nk_path)
            if launched:
                print(f"Nuke launched with the existing project: {nk_path}")
            else:
                print(f"Could not launch Nuke automatically. Open it manually: {nk_path}")
            return
        else:
            # Metadata was found but the .nk no longer exists on disk
            # (it was deleted or moved by hand). Treat it as a new clip.
            print(f"Found a previous job for '{clip_name}' but its .nk no longer exists on disk. Creating a new one.")
            existing_job = None

    # --- NEW CLIP: ask where to save the Nuke project ---
    fusion = resolve.Fusion()
    chosen_dir = None
    if fusion is not None:
        chosen_dir = fusion.RequestDir(os.path.expanduser("~"))
    if not chosen_dir:
        print("No folder was selected. Operation cancelled.")
        return

    job_name = _sanitize_job_name(clip_name) + "_" + time.strftime("%Y%m%d_%H%M%S")
    job_dir = os.path.join(chosen_dir, job_name)
    os.makedirs(os.path.join(job_dir, "from_resolve"), exist_ok=True)
    os.makedirs(os.path.join(job_dir, "from_nuke"), exist_ok=True)
    render_dir = os.path.join(job_dir, "from_resolve")

    media_pool = project.GetMediaPool()

    rendered_count = _render_from_resolve(project, render_dir, media_pool_item, source_start, source_end, fps, clip_name)
    if rendered_count is None:
        return

    # The exact filename Resolve generates follows the pattern
    # <CustomName>.<padded frame>.exr. We look up the first frame to
    # build the sequence-syntax path for Nuke.
    rendered_files = sorted(
        f for f in os.listdir(render_dir) if f.lower().endswith(".exr")
    )
    if not rendered_files:
        print(f"No EXR files were found in {render_dir}. The render may have failed.")
        return

    first_file = rendered_files[0]
    # Expected pattern: render.0001.exr -> we extract the numeric padding
    match = re.match(r"^(.*?)(\d+)(\.\w+)$", first_file)
    if not match:
        print(f"Could not parse the numbering pattern of '{first_file}'.")
        return

    prefix, first_frame_str, ext = match.groups()
    padding = len(first_frame_str)
    frame_start = int(first_frame_str)
    frame_end = frame_start + len(rendered_files) - 1
    nuke_read_pattern = os.path.join(render_dir, f"{prefix}%0{padding}d{ext}").replace("\\", "/")

    # Import the from_resolve EXRs into the Media Pool as an
    # independent clip. This serves as the "original backup": once
    # ReplaceClip() later updates the MediaPoolItem to point at the
    # Nuke result (from_nuke), the from_resolve entry in the Media
    # Pool still points at the pre-Nuke render, so the original is
    # always recoverable without needing a separate backup folder.
    # This is non-fatal: if the import fails, the script continues
    # and the EXR files remain on disk in from_resolve anyway.
    try:
        seq_path = os.path.join(render_dir, f"{prefix}%0{padding}d{ext}")
        media_pool.ImportMedia([{
            "FilePath": seq_path,
            "StartIndex": frame_start,
            "EndIndex": frame_end,
        }])
        print(f"Imported from_resolve render into Media Pool as a reference copy.")
    except Exception as e:
        print(f"[resolve_nuke_bridge] Could not import from_resolve into Media Pool (non-fatal): {e}")

    # --- Prepare bridge metadata ---
    width = project.GetSetting("timelineResolutionWidth")
    height = project.GetSetting("timelineResolutionHeight")

    bridge.write_metadata(job_dir, {
        "job_name": job_name,
        "clip_name": clip_name,
        "media_pool_item_id": unique_id,
        "frame_start": frame_start,
        "frame_end": frame_end,
        "fps": fps,
        "width": width,
        "height": height,
        "source_render_path": nuke_read_pattern,
        "nuke_output_dir": os.path.join(job_dir, "from_nuke").replace("\\", "/"),
    })

    # Register where this job ended up in the centralized index, so
    # it can be found automatically the next time this same clip is
    # sent to Nuke (regardless of which different chosen_dir is
    # picked in future submissions of OTHER clips).
    bridge.register_job_location(unique_id, job_dir)

    # --- Generate the .nk ---
    nk_path = _write_nuke_script(job_dir, nuke_read_pattern, frame_start, frame_end, width, height, fps)

    # --- Launch Nuke ---
    launched = bridge.launch_nuke(nk_path)
    if launched:
        print(f"Nuke launched with the project: {nk_path}")
    else:
        print(f"Could not launch Nuke automatically. Open it manually: {nk_path}")


def _render_from_resolve(project, render_dir, media_pool_item, source_start, source_end, fps, clip_name):
    """
    Renders ONLY the indicated material (between source_start and
    source_end WITHIN its own source media) to render_dir, regardless
    of what other clips surround it in the user's original timeline.

    IMPORTANT - WHY MarkIn/MarkOut ON THE ORIGINAL TIMELINE IS NOT
    USED: project.SetRenderSettings() with "MarkIn"/"MarkOut" renders
    a TIME RANGE of the WHOLE COMPOSITED TIMELINE, not the content of
    a specific clip. This was confirmed empirically: if there is
    another clip (or image) right after the clip you want to send to
    Nuke, that neighboring material also shows up in the render, even
    though the playhead was only over the desired clip. Instead, a
    TEMPORARY TIMELINE is created containing ONLY this material
    (trimmed to its own internal source range), that isolated
    timeline is rendered, and it is deleted afterwards. This
    guarantees the render contains exactly the expected content,
    regardless of what surrounds it in the user's real timeline.

    FRAME NUMBERING: the temporary timeline's start timecode is set
    to the timecode equivalent of source_start at the given fps, so
    that the rendered output files are numbered to match the source
    material's own frame range (e.g. source_start=1001 at 24fps
    produces render.00001001.exr, render.00001002.exr, ...). This
    keeps three things independent, as they should be:
      1. The Resolve timeline's own start timecode (irrelevant here)
      2. The clip's position within that timeline (also irrelevant)
      3. The source material's frame range on disk (this is what we
         replicate in the rendered output)

    Returns True if the render completed successfully, or None if
    something failed (the reason is already printed to the console in
    that case).
    """
    media_pool = project.GetMediaPool()
    original_timeline = project.GetCurrentTimeline()

    temp_timeline_name = "_NukeBridge_temp_" + time.strftime("%Y%m%d_%H%M%S")
    temp_timeline = media_pool.CreateEmptyTimeline(temp_timeline_name)
    if temp_timeline is None:
        print("Could not create a temporary timeline for an isolated render.")
        return None

    try:
        project.SetCurrentTimeline(temp_timeline)

        # Set the temporary timeline's start timecode to match the
        # source material's first frame number, so rendered output
        # files inherit that numbering (render.00001001.exr etc.)
        # rather than always starting at 0.
        # Formula: convert source_start frames to HH:MM:SS:FF at fps.
        fps_int = int(round(float(fps)))
        total_seconds = source_start // fps_int
        ff = source_start % fps_int
        hh = total_seconds // 3600
        mm = (total_seconds % 3600) // 60
        ss = total_seconds % 60
        start_tc = f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"
        temp_timeline.SetStartTimecode(start_tc)

        # WARNING: AppendToTimeline() ALWAYS expects a LIST of
        # dictionaries, even for a single clip. Passing a bare
        # dictionary (without the wrapping list) is a known API bug
        # that can FREEZE Resolve entirely, not just fail with an
        # error. Never remove the outer brackets here.
        #
        # NOTE: endFrame here is EXCLUSIVE for the temporary timeline
        # context (confirmed empirically: endFrame=1300 produces frames
        # up to 1299 only, missing the last frame; endFrame=source_end+1
        # produces the full range including source_end).
        appended = media_pool.AppendToTimeline([{
            "mediaPoolItem": media_pool_item,
            "startFrame": source_start,
            "endFrame": source_end + 1,
        }])
        if not appended:
            print(f"Could not place '{clip_name}' on the temporary timeline.")
            return None

        os.makedirs(render_dir, exist_ok=True)

        project.SetCurrentRenderFormatAndCodec("exr", _pick_exr_codec(project))
        project.SetCurrentRenderMode(1)  # 1 = Single clip
        project.SetRenderSettings({
            "SelectAllFrames": True,
            "TargetDir": render_dir,
            "CustomName": "render",
            "ExportVideo": True,
            "ExportAudio": False,
        })

        job_id = project.AddRenderJob()
        if not job_id:
            print("Could not create the render job in Resolve.")
            return None

        project.StartRendering([job_id])
        print(f"Rendering '{clip_name}' to {render_dir} ...")

        last_pct = -1
        while project.IsRenderingInProgress():
            time.sleep(0.5)
            try:
                status = project.GetRenderJobStatus(job_id)
                pct = int(status.get("CompletionPercentage", 0))
                if pct != last_pct:
                    print(f"  Render progress: {pct}%")
                    last_pct = pct
            except Exception:
                pass  # Progress feedback is best-effort; don't abort on failure

        print("Render complete.")
        return True

    finally:
        # Always go back to the user's original timeline and delete
        # the temporary one, even if something failed above, so the
        # user isn't left "stranded" on an empty timeline and no
        # temporary timelines pile up in their project.
        if original_timeline is not None:
            project.SetCurrentTimeline(original_timeline)
        media_pool.DeleteTimelines([temp_timeline])


def _pick_exr_codec(project):
    """
    Picks an available EXR codec by dynamically querying Resolve,
    instead of assuming a fixed name (it varies between versions).
    Prioritizes lossless compression if available.
    """
    codecs = project.GetRenderCodecs("exr") or {}
    if not codecs:
        return ""  # let Resolve use its default codec

    preference = ["RGBHalfZIP", "RGBHalfPIZ", "RGBFloatZIP"]
    for pref in preference:
        if pref in codecs.values():
            return pref
    # If none of the preferred ones are available, use the first one there is.
    return list(codecs.values())[0]


def _write_nuke_script(job_dir, read_pattern, frame_start, frame_end, width, height, fps):
    """
    Generates a minimal .nk file with a Read pointing at Resolve's
    render and a Write pointing at the output folder back to Resolve.

    IMPORTANT - ROUNDTRIP COLORSPACE:
    The Read and Write are configured with colorspace "sRGB"
    explicitly, instead of being left at "default" (which Nuke
    interprets as linear). This was confirmed EMPIRICALLY (not just
    theoretically): even though the Resolve project has its "Output
    color space" set to "Rec.709 (Scene)" (Project Settings > Color
    Management), trying "rec709" in Nuke's Read gave a result MORE
    washed out than in Resolve, while "sRGB" DID match visually. This
    suggests that the actual curve Resolve is rendering the output
    EXR with is closer to sRGB than to classic "display" Rec709 in
    this particular project, even though the theoretical difference
    between the two curves is small. If you decide to verify this
    more rigorously (comparing numeric pixel values instead of by
    eye), or if you change the project's color configuration, update
    the value here accordingly.

    If you change the project's "Output color space" in Resolve in
    the future (for example to Linear, or to an ACES workflow), you
    must check again (by eye or with numeric values) which Nuke
    colorspace actually matches, and update the value here on BOTH
    ends (Read AND Write), or the roundtrip will be off again.
    """
    nk_path = os.path.join(job_dir, "comp.nk")
    output_dir = os.path.join(job_dir, "from_nuke").replace("\\", "/")
    output_pattern = os.path.join(output_dir, "render.%04d.exr").replace("\\", "/")

    width = int(width) if width else 1920
    height = int(height) if height else 1080
    fps = float(fps) if fps else 24.0

    nk_content = f"""#! Nuke bridge script (auto-generated)
Root {{
 inputs 0
 format "{width} {height} 0 0 {width} {height} 1 ResolveBridgeFormat"
 fps {fps}
 first_frame {frame_start}
 last_frame {frame_end}
}}
Read {{
 file "{read_pattern}"
 first {frame_start}
 last {frame_end}
 origfirst {frame_start}
 origlast {frame_end}
 origset true
 colorspace sRGB
 name ResolveBridge_Read1
}}
Write {{
 file "{output_pattern}"
 file_type exr
 first {frame_start}
 last {frame_end}
 colorspace sRGB
 name ResolveBridge_Write1
}}
StickyNote {{
 inputs 0
 label "RESOLVE BRIDGE -- auto-generated script\n\nCOLORSPACE: Read and Write are set to sRGB.\nThis matches a standard Rec.709 Resolve project.\nIf your project uses a different color space\n(Log, ACES, Linear...) adjust the colorspace\nknob on BOTH nodes before compositing.\n\nDo NOT move or rename ResolveBridge_Write1."
 note_font_size 12
 xpos 200
 ypos -100
}}
"""
    with open(nk_path, "w", encoding="utf-8") as f:
        f.write(nk_content)

    return nk_path


if __name__ == "__main__":
    main()
