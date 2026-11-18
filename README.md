# Caption Suite

Word-timed captions for the Blender Video Sequence Editor (VSE).

Caption Suite transcribes a video or audio strip with **Faster Whisper** and keeps the timing of every word. From those words it builds short, styled, animated caption strips, and it gives you tools to restyle, regroup and correct them without transcribing again.

It is a fork of [Subtitle Editor](https://github.com/tin2tin/Subtitle_Editor) by tin2tin, rewritten around word-level timing. See [What changed from the original](#what-changed-from-the-original).

## Disclosure: AI:W

My changes to this add-on are labelled **AI:W** under the [0rwx framework](https://0rwx.com/): AI writes the code and I read it.

*   The code I add to this fork is written by an AI model (Claude), not by hand.
*   I decide what gets built, read the changes, and test them in my own editing work in Blender before they are committed.
*   The label covers my changes only. The original add-on is [Subtitle Editor](https://github.com/tin2tin/Subtitle_Editor) by tin2tin.
*   I test on Windows with an NVIDIA GPU in Blender 5.3. Expect bugs, and save your project before trying it.

## Features

### Transcription
*   **Word-level timing**: Every word is stored with its own start and end, so captions can be regrouped at any time without transcribing again.
*   **Visible part only**: Transcribes just the part of the selected strip that is on the timeline, and places the captions where the speech is.
*   **Models**: `tiny`, `base`, `small`, `medium`, `distil-large-v3`, `large-v2` and `large-v3`.
*   **GPU or CPU**: CUDA on NVIDIA GPUs, or CPU, with a choice of compute type (`float16`, `int8_float16`, `int8`, `float32`).
*   **Language**: Automatic detection, or pick one of 21 languages.
*   **Skip Silence**: Voice activity detection ignores silence and music.
*   **Keep Stutters & Fillers**: Hints Whisper to write down "um", "uh" and repeated starts. It works partly; Whisper still drops some.

### Caption look
*   **Live styling**: Font, size, colour, bold, case, outline, shadow, box, position and wrap width apply to every caption as you change them.
*   **Font picker**: Search the fonts installed on your computer by name.
*   **Presets**: Save, load and delete presets for the look, grouping and animation settings.

### Grouping and animation
*   **Grouping**: Words per caption, maximum characters, a break after a pause, and optional breaks at sentence ends and commas.
*   **Hold Through Gaps**: Keeps a caption on screen until the next one when the gap between them is short.
*   **Hide Fillers / Hide Stutter Repeats**: Leave "um" and "uh", or immediately repeated words, out of the captions without deleting them from the transcript.
*   **Pop-in and fade-in**: Added as plain keyframes, so any caption can be reshaped in the Graph Editor.
*   **Regenerate**: Rebuild all captions from the stored words, or only the selected ones so hand-edited captions are left alone.

### Speakers
*   **Add as Another Speaker**: Transcribe a second strip and keep the existing captions. Each extra speaker goes one channel higher.
*   **Per-speaker look**: Colour and position for each extra speaker.
*   **Reassign**: Give the selected captions to another speaker.

### Word editing
*   **Word list**: Every transcribed word in order. Clicking a word moves the playhead to it and selects its caption. Double-click to fix the text.
*   **Insert and delete**: Add a word Whisper missed, or remove one.
*   **Timing nudge**: Move a word's start, end or both by one frame.
*   **Split Caption Here**: Force a new caption at a word. The split survives Regenerate.
*   **Keep Moved Captions**: Store the position of caption strips you dragged on the timeline, so Regenerate keeps them there.

### Export
*   **SRT**: Export full sentences from the transcript, or one line per caption strip as shown.

## Installation

1.  **Blender version**: Blender **5.2** or newer.
2.  **Download**: [Download the add-on as a .zip file](https://github.com/paul-s-cameron/Caption-Suite/archive/refs/heads/main.zip). Do not unzip it.
3.  **Original add-on**: If you have tin2tin's Subtitle Editor enabled, disable it first.
4.  **Install the add-on**: In Blender, go to `Edit > Preferences > Add-ons`, open the menu in the top right corner and choose `Install from Disk...`. Select the downloaded `.zip` file.
5.  **Enable the add-on**: If it is not already enabled, find "Caption Suite" in the list and check the box.
6.  **Install the libraries**:
    *   Make sure `Preferences > System > Network > Allow Online Access` is on.
    *   Go to the Video Sequencer workspace and open the Sidebar (`N` key).
    *   Go to the `Captions` tab and find the **Transcribe** panel.
    *   Click `Install / Repair`. This downloads `Faster Whisper` (about 270 MB installed) and can take several minutes. Progress is shown in the System Console (`Window > Toggle System Console`).
    *   For an NVIDIA GPU, also click `GPU Libraries` (about 2 GB more).

You do not need to run Blender as administrator, and Blender stays usable while the libraries download. The panel shows how long the install has been running.

*   **Transcription libraries** go into the extension's own user folder, which Blender keeps separately for each Blender version. Each Blender version needs its own install, and Blender removes the folder when you uninstall Caption Suite.
*   **GPU libraries** go into `%LOCALAPPDATA%\CaptionSuite\gpu-cu12` on Windows and are shared by every Blender version. To remove them, click the folder button next to `GPU Libraries`, close Blender and delete the folder.

Blender's own libraries are left as they are. `Install / Repair` replaces the libraries with a fresh copy; if they are in use, restart Blender and click it before transcribing.

### Upgrading from 3.0 or 3.1

From version 3.2, Caption Suite is a Blender extension (it has a `blender_manifest.toml`). Blender installs extensions under a different name from older add-ons, so the new version does not replace an old one. Remove the old version in `Preferences > Add-ons` first, then install the new zip and click `Install / Repair` again. Your presets and the captions saved in your `.blend` files are kept.

Version 3.1 kept its libraries in `%LOCALAPPDATA%\CaptionSuite\py313`. That folder is no longer used and can be deleted.

The first transcription with each model downloads that model. The larger models are several gigabytes.

## Quick start

1.  Select a video or audio strip in the Sequencer.
2.  In the `Captions` tab, click **Transcribe to Captions**.
3.  Adjust the style in **Caption Look**. Changes show at once.
4.  Change the grouping or animation in **Grouping & Animation**, then click **Regenerate All**.
5.  Fix any wrong words in **Words**.
6.  Optional: click **Export SRT** in the Words panel.

## Where to find it

In the `Sequencer`, open the Sidebar (`N` key) and click the **Captions** tab. It has five panels: **Transcribe**, **Caption Look**, **Grouping & Animation**, **Speakers** and **Words**.

## What changed from the original

This fork replaces the original add-on's code. It adds word-level timing, live styling, pop-in animation, speakers, word editing and presets.

These features of the original are not in this fork:

*   Importing subtitle files (`.srt`, `.ass`, `.vtt`, `.fountain`).
*   Translation to English.
*   The list editor for existing text strips, and copying a style to selected strips.
*   Export formats other than `.srt`.

If you need them, use [tin2tin's Subtitle Editor](https://github.com/tin2tin/Subtitle_Editor).

## Modules used

This add-on relies on the following open-source library, installed by the `Install / Repair` button:

*   [Faster Whisper](https://github.com/SYSTRAN/faster-whisper): A reimplementation of OpenAI's Whisper model for faster and more efficient transcription.

## License and credits

GPL version 2 or later, the same as the original. Based on [Subtitle Editor](https://github.com/tin2tin/Subtitle_Editor) by tin2tin.
