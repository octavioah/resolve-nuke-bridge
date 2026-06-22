RESOLVE <-> NUKE BRIDGE
========================
Personal tool by Octavio Alonso.

Sends a clip from a DaVinci Resolve timeline to Nuke, lets you
composite at your own pace, and sends the result back to Resolve
automatically -- no manual importing, no track shuffling, no lost
work between iterations.

Requires DaVinci Resolve Studio (the free version does not expose
the scripting API this tool needs).


FILES
-----
resolve_nuke_bridge_common.py  -- shared module (goes in BOTH places)
SendToNuke.py                  -- Resolve side
ReopenInNuke.py                -- Resolve side (optional, see below)
send_back_to_resolve.py        -- Nuke side


INSTALLATION
------------

1. RESOLVE SIDE
   Copy these three files to your Resolve Scripts/Edit folder:
     - resolve_nuke_bridge_common.py
     - SendToNuke.py
     - ReopenInNuke.py

   Paths:
     Mac:     /Library/Application Support/Blackmagic Design/
                DaVinci Resolve/Fusion/Scripts/Edit/
     Windows: C:\ProgramData\Blackmagic Design\DaVinci Resolve\
                Fusion\Scripts\Edit\

   Restart Resolve. Both scripts appear under Workspace > Scripts.

2. NUKE SIDE
   Copy these two files to your ~/.nuke/ folder
   (the same folder where menu.py lives):
     - resolve_nuke_bridge_common.py
     - send_back_to_resolve.py

   Then add this line to ~/.nuke/menu.py:
     import send_back_to_resolve

   Restart Nuke (or source the menu if it is already running).
   A new menu item appears: Render > Send back to Resolve
   Keyboard shortcut: Ctrl+Alt+R


HOW TO USE
----------

SENDING TO NUKE
  1. In Resolve, on the Edit or Cut page, move the PLAYHEAD over
     the clip you want to send to Nuke. (The Resolve API has no
     way to detect which clip is visually selected, so the clip
     under the playhead is used -- the same approach as the native
     Send to Fusion feature.)

  2. Run Workspace > Scripts > SendToNuke.

     First time with this clip: you are asked to choose a folder
     where the Nuke project will live. Resolve then renders the
     full source material to EXR (preserving the original frame
     numbering of the clip, e.g. 1001-1100), opens Nuke with a
     Read and Write node already wired up, and imports the render
     into the Media Pool as a reference copy.

     Subsequent times with the same clip: no folder prompt. The
     existing .nk is reused untouched, preserving any compositing
     work you already did. Only the source material is re-rendered
     fresh from Resolve.

COMPOSITING IN NUKE
  Work normally. The Read node points at the EXR render from
  Resolve. The Write node (ResolveBridge_Write1) is pre-configured;
  do not rename or delete it.

  Note: the Read and Write are set to colorspace "sRGB". This was
  confirmed empirically to match a standard Rec.709 Resolve project.
  If your project uses a different color space (Log, ACES, Linear),
  adjust the colorspace knob on BOTH the Read and Write nodes before
  compositing, or the roundtrip will look wrong. A sticky note in the
  .nk reminds you of this when you first open it.

SENDING BACK TO RESOLVE
  When done, run Render > Send back to Resolve (or Ctrl+Alt+R).
  Nuke renders and sends the result back automatically. Resolve
  updates the original clip in place via ReplaceClip() -- no new
  tracks are created, nothing shifts around.

  The from_resolve/ EXR render stays in the job folder as your
  pre-Nuke reference. Import it manually from the Media Pool (it
  was added there automatically on first send) if you need to
  compare before/after.

REOPENING NUKE FOR A SHOT ALREADY IN PROGRESS
  If you closed Nuke before sending the result back, or want to
  continue working on a shot, place the playhead over the clip in
  Resolve and run Workspace > Scripts > ReopenInNuke. This relaunches
  Nuke with the existing .nk without re-rendering anything.


FOLDER STRUCTURE
----------------
Each clip gets its own job folder inside the directory you chose:

  YourChosenFolder/
    ClipName_YYYYMMDD_HHMMSS/
      from_resolve/        <- EXR render from Resolve (source for Nuke,
                              also serves as pre-Nuke reference)
      from_nuke/           <- EXR render from Nuke (sent back to Resolve)
      comp.nk              <- your Nuke composition
      bridge_metadata.json <- internal bookkeeping (do not edit)

A small index at ~/NukeResolveBridge/job_index.json maps each Resolve
clip to its job folder so the script can find it on subsequent sends.
Deleting this file makes the script forget all registered jobs (the
job folders themselves are not deleted). If you move a job folder
manually, the script detects that its .nk is gone and creates a new
job, asking you again for a folder.


IMPORTANT NOTES
---------------
- ReplaceClip() updates the MediaPoolItem for the original clip in
  place. If the same source material appears in more than one place
  in your project, all instances update simultaneously. Undo (Ctrl+Z)
  in Resolve reverts it if needed.

- The script creates a temporary timeline internally during the render
  phase to isolate exactly the clip you sent (so neighboring content
  on the timeline does not bleed into the render). You may briefly
  see the timeline switch; this is expected and it returns to your
  original timeline automatically.

- Nuke version detection: if you have multiple Nuke versions
  installed, the script picks the one that sorts last alphabetically,
  which is usually the most recent. NukeX is preferred over plain
  Nuke when both are found.

- Tested end-to-end on macOS. Windows paths and Nuke detection follow
  Foundry's standard conventions and should work, but have not been
  verified on a real Windows machine.
