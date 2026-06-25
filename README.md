# Resolve ↔ Nuke Bridge

A Python tool that connects DaVinci Resolve and Nuke, letting you send any clip from your Resolve timeline to Nuke, composite it, and send the result back — automatically, without any manual importing or exporting.

---

## What it does

When you are editing in Resolve and need to do VFX or compositing work on a specific clip in Nuke, the normal workflow involves manually exporting the clip, importing it into Nuke, doing the work, rendering, importing the result back into Resolve, and reconnecting everything. Every iteration repeats the whole process.

This bridge automates that roundtrip:

1. **In Resolve**, place the playhead over the clip you want to work on and run **SendToNuke** from the Scripts menu. Resolve renders the full source material to EXR (preserving the original frame numbering), opens Nuke automatically with a Read and Write node already wired up, and imports the render into the Media Pool as a reference copy of the pre-Nuke original.

2. **In Nuke**, composite normally. The Read node already points at the footage from Resolve. When done, press **Ctrl+Alt+R** (or go to Render > Send back to Resolve).

3. **Back in Resolve**, the original clip updates automatically in place. No new tracks, nothing shifts around in your edit.

If you send the same clip to Nuke again later, the existing Nuke project is reused with all your compositing work intact — only the source material is re-rendered fresh from Resolve.

---

## Features

- Works with image sequences (EXR, PNG, DPX...) and video files (MOV, MP4, MXF...)
- Preserves the original frame numbering of the clip (e.g. 1001–1300, not 0–299)
- Handles clips cut into multiple pieces on the timeline — all instances update at once when the result comes back
- Keeps a copy of the pre-Nuke render in the Media Pool as a reference
- If you close Nuke before sending the result back, **ReopenInNuke** reopens the existing project without re-rendering anything
- All intermediate files stay in a self-contained job folder that you choose

---

## Requirements

- DaVinci Resolve **Studio** (the free version does not expose the scripting API)
- Nuke or NukeX (any recent version)
- Python 3 installed from [python.org](https://www.python.org/downloads/)
- External scripting enabled in Resolve: **Preferences > General > External scripting using > Local**

---

## Download

There are two versions — one for each platform. Each zip contains the scripts and a detailed README with step-by-step installation instructions specific to that platform.

| Platform | Download |
|----------|----------|
| **macOS** | [Resolve_Nuke_Bridge_Mac.zip](Resolve_Nuke_Bridge_Mac.zip) |
| **Windows** | [Resolve_Nuke_Bridge_Windows.zip](Resolve_Nuke_Bridge_Windows.zip) |

Each zip includes a `README.txt` with:
- Exact folder paths for that platform
- Step-by-step installation instructions
- How to use the bridge
- Troubleshooting for common issues

---

## How the job folder is structured

Each clip gets its own folder inside the directory you choose when first sending a clip to Nuke:

```
YourChosenFolder/
  ClipName_YYYYMMDD_HHMMSS/
    from_resolve/        <- EXR render from Resolve (Nuke reads from here)
    from_nuke/           <- EXR render from Nuke (sent back to Resolve)
    comp.nk              <- your Nuke composition
    bridge_metadata.json <- internal bookkeeping
```

---

## Notes

- **Colorspace**: the Read and Write nodes in the generated `.nk` are set to `sRGB`, which matches a standard Rec.709 Resolve project. If your project uses a different color space (Log, ACES, Linear...), change the colorspace knob on both nodes before compositing. A sticky note in the `.nk` reminds you of this when you first open it.
- **ReplaceClip behavior**: when the result comes back, Resolve's `ReplaceClip()` updates the clip's MediaPoolItem directly. If the same source material appears in more than one place in your project, all instances update at once. Undo (Ctrl+Z) in Resolve reverts the change.
- **Tested on**: macOS and Windows 10/11, with Resolve Studio 18/19 and NukeX 15/17.

---

*Made by Octavio Alonso*
