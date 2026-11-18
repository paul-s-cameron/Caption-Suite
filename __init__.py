# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####
#
# Customized from "Subtitle Editor" by tintwotin
# (https://github.com/tin2tin/Subtitle_Editor), reworked around word-level
# timing for short-form captions.

bl_info = {
    "name": "Caption Suite",
    "description": "Word-timed Whisper captions with styling, font picker and pop-in animation",
    "author": "tintwotin, customized",
    "version": (3, 0, 0),
    "blender": (5, 2, 0),
    "location": "Sequencer > Side Bar > Captions Tab",
    "warning": "Disable the original Subtitle Editor add-on before enabling this one",
    "doc_url": "",
    "support": "COMMUNITY",
    "category": "Sequencer",
}

import os
import sys
import re
import gc
import glob
import shutil
import struct
import ctypes
import platform
import importlib
import subprocess
import sysconfig
import traceback

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup, UIList

OS_NAME = platform.system()  # 'Windows', 'Linux', 'Darwin'
TAB = "Captions"

FASTER_WHISPER_VERSION = "1.2.1"
# av is capped below 19: PyAV 19 removed an argument faster-whisper 1.2.1 still passes.
BASE_PACKAGES = [f"faster-whisper=={FASTER_WHISPER_VERSION}", "av<19"]
# CUDA 12 runtime libraries that the NVIDIA driver does not ship.
GPU_PACKAGES = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"]

_fw = None                                  # the imported faster_whisper module
_model_cache = {"key": None, "model": None}
_suspend = False                            # blocks update callbacks during bulk changes
_font_cache = []                            # [(display name, path)]
_font_enum = []                             # enum items, kept alive for Blender

# Example of disfluent speech; nudges Whisper toward keeping stutters and fillers.
VERBATIM_PROMPT = ("Umm, let me think like, hmm... Okay, here's what I'm, like, thinking. "
                   "I- I- I mean, uh, y- you know.")
FILLERS = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "hmm", "mm", "mhm", "eh"}
SPEAKER_COLORS = [(1.0, 0.85, 0.1, 1.0), (0.2, 0.9, 1.0, 1.0), (0.45, 1.0, 0.45, 1.0), (1.0, 0.5, 0.8, 1.0)]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def get_seq_scene(context=None):
    """Scene the Sequencer is editing (Blender 5.0+ has a separate sequencer scene)."""
    ctx = context or bpy.context
    return getattr(ctx, "sequencer_scene", None) or ctx.scene


def scene_fps(scene):
    return scene.render.fps / scene.render.fps_base


def show_system_console():
    if OS_NAME == "Windows":
        try:
            ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 5)
        except Exception:
            pass


def _set(obj, attr, value):
    """Set a property, ignoring ones this Blender version does not have."""
    try:
        setattr(obj, attr, value)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def check_faster_whisper():
    global _fw
    if _fw is not None:
        return True
    try:
        importlib.invalidate_caches()
        import faster_whisper
        _fw = faster_whisper
        return True
    except Exception as e:
        print(f"[Captions] faster-whisper not available: {e}")
        _fw = None
        return False


def _site_packages():
    return sysconfig.get_paths()["purelib"]


def _can_write(folder):
    probe = os.path.join(folder, "_cc_write_test.tmp")
    try:
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
        return True
    except OSError:
        return False


def _pip(args):
    # -s keeps pip away from the per-user site folder, which Blender 5.1+ does not load.
    cmd = [sys.executable, "-s", "-m", "pip"] + args
    print("[Captions] Running:", " ".join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.stdout:
        print(p.stdout)
    if p.stderr:
        print(p.stderr)
    return p.returncode == 0


def _copy_gpu_dlls(site_dir):
    """Copy NVIDIA DLLs next to ctranslate2, the only place it finds them on Windows."""
    target = os.path.join(site_dir, "ctranslate2")
    if not os.path.isdir(target):
        return 0
    copied = 0
    for dll in glob.glob(os.path.join(site_dir, "nvidia", "*", "bin", "*.dll")):
        try:
            shutil.copy2(dll, target)
            copied += 1
        except OSError as e:
            print(f"[Captions] Could not copy {os.path.basename(dll)}: {e}")
    return copied


class SEQUENCER_OT_cc_setup(Operator):
    """Install or repair the transcription libraries inside Blender's Python"""
    bl_idname = "sequencer.cc_setup"
    bl_label = "Install / Repair"
    bl_options = {"REGISTER", "INTERNAL"}

    gpu: BoolProperty(default=False)

    def execute(self, context):
        global _fw
        site_dir = _site_packages()
        if not _can_write(site_dir):
            if OS_NAME == "Windows":
                msg = ("Blender's Python folder is read-only. Close Blender, right-click it, "
                       "choose 'Run as administrator', then click this button again (one time only).")
            else:
                msg = f"Blender's Python folder is not writable: {site_dir}"
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        show_system_console()
        subprocess.run([sys.executable, "-m", "ensurepip"], capture_output=True)
        packages = GPU_PACKAGES if self.gpu else BASE_PACKAGES
        self.report({"INFO"}, "Installing, see the System Console for progress...")
        if not _pip(["install", "--no-warn-script-location"] + packages):
            self.report({"ERROR"}, "Install failed. See Window > Toggle System Console.")
            return {"CANCELLED"}

        if self.gpu:
            if OS_NAME == "Windows":
                n = _copy_gpu_dlls(site_dir)
                if n == 0:
                    self.report({"ERROR"}, "GPU libraries downloaded but no DLLs were copied. See console.")
                    return {"CANCELLED"}
                self.report({"INFO"}, f"GPU libraries installed ({n} files). Restart Blender.")
            else:
                self.report({"INFO"}, "GPU libraries installed. Restart Blender.")
            return {"FINISHED"}

        _fw = None
        if check_faster_whisper():
            self.report({"INFO"}, "Transcription libraries are ready.")
        else:
            self.report({"WARNING"}, "Installed. Restart Blender to finish.")
        return {"FINISHED"}


class SEQUENCER_OT_cc_free_model(Operator):
    """Unload the Whisper model to free memory (it reloads on the next transcription)"""
    bl_idname = "sequencer.cc_free_model"
    bl_label = "Unload Model"

    def execute(self, context):
        _model_cache["model"] = None
        _model_cache["key"] = None
        gc.collect()
        self.report({"INFO"}, "Model unloaded.")
        return {"FINISHED"}


def get_model(size, device, compute):
    key = (size, device, compute)
    if _model_cache["key"] != key or _model_cache["model"] is None:
        _model_cache["model"] = None
        gc.collect()
        print(f"[Captions] Loading model {size} ({device}, {compute})")
        _model_cache["model"] = _fw.WhisperModel(size, device=device, compute_type=compute)
        _model_cache["key"] = key
    return _model_cache["model"]


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

def _font_dirs():
    home = os.path.expanduser("~")
    if OS_NAME == "Windows":
        return [
            os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
        ]
    if OS_NAME == "Darwin":
        return ["/System/Library/Fonts", "/Library/Fonts", os.path.join(home, "Library", "Fonts")]
    return ["/usr/share/fonts", "/usr/local/share/fonts",
            os.path.join(home, ".fonts"), os.path.join(home, ".local", "share", "fonts")]


def _font_full_name(path):
    """Read the human-readable name from a TTF/OTF file's name table."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if len(head) < 12 or head[:4] == b"ttcf":
                return None
            num_tables = struct.unpack(">H", head[4:6])[0]
            name_off = name_len = None
            for _ in range(num_tables):
                rec = f.read(16)
                if len(rec) < 16:
                    return None
                tag, _chk, off, length = struct.unpack(">4sLLL", rec)
                if tag == b"name":
                    name_off, name_len = off, length
                    break
            if name_off is None:
                return None
            f.seek(name_off)
            tbl = f.read(name_len)
        _fmt, count, str_off = struct.unpack(">HHH", tbl[:6])
        fallback = None
        for i in range(count):
            pid, _eid, lid, nid, ln, off = struct.unpack(">HHHHHH", tbl[6 + i * 12:18 + i * 12])
            if nid != 4:
                continue
            raw = tbl[str_off + off:str_off + off + ln]
            if pid in (0, 3):
                name = raw.decode("utf-16-be", "ignore").strip()
                if name and pid == 3 and lid == 0x409:
                    return name
            elif pid == 1:
                name = raw.decode("mac_roman", "ignore").strip()
            else:
                name = ""
            if name and fallback is None:
                fallback = name
        return fallback
    except Exception:
        return None


def scan_fonts(force=False):
    global _font_cache, _font_enum
    if _font_cache and not force:
        return _font_cache
    found = {}
    for folder in _font_dirs():
        if not folder or not os.path.isdir(folder):
            continue
        for root, _dirs, files in os.walk(folder):
            for fn in files:
                if not fn.lower().endswith((".ttf", ".otf")):
                    continue
                path = os.path.join(root, fn)
                name = _font_full_name(path) or os.path.splitext(fn)[0]
                found.setdefault(name, path)
    _font_cache = sorted(found.items(), key=lambda kv: kv[0].lower())
    _font_enum = [(str(i), name, path) for i, (name, path) in enumerate(_font_cache)]
    return _font_cache


def font_label(path):
    if not path:
        return "Blender default"
    ap = os.path.normcase(bpy.path.abspath(path))
    for name, p in _font_cache:
        if os.path.normcase(p) == ap:
            return name
    return os.path.splitext(os.path.basename(path))[0]


def load_font(path):
    if not path:
        return None
    ap = bpy.path.abspath(path)
    if not os.path.isfile(ap):
        return None
    try:
        return bpy.data.fonts.load(ap, check_existing=True)
    except Exception as e:
        print(f"[Captions] Could not load font {ap}: {e}")
        return None


def _font_enum_items(self, context):
    scan_fonts()
    return _font_enum or [("0", "No fonts found", "")]


class SEQUENCER_OT_cc_pick_font(Operator):
    """Search the fonts installed on this computer and apply one to all captions"""
    bl_idname = "sequencer.cc_pick_font"
    bl_label = "Choose Font"
    bl_property = "font"
    bl_options = {"REGISTER", "UNDO"}

    font: EnumProperty(name="Font", items=_font_enum_items)

    def execute(self, context):
        try:
            _name, path = _font_cache[int(self.font)]
        except (ValueError, IndexError):
            return {"CANCELLED"}
        get_seq_scene(context).cc_settings.font_path = path
        return {"FINISHED"}

    def invoke(self, context, event):
        scan_fonts()
        context.window_manager.invoke_search_popup(self)
        return {"FINISHED"}


class SEQUENCER_OT_cc_rescan_fonts(Operator):
    """Rescan the system font folders (after installing a new font)"""
    bl_idname = "sequencer.cc_rescan_fonts"
    bl_label = "Rescan Fonts"

    def execute(self, context):
        n = len(scan_fonts(force=True))
        self.report({"INFO"}, f"Found {n} fonts.")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Caption text, grouping and timing
# ---------------------------------------------------------------------------

EDGE_PUNCT = ".,!?;:\"()[]{}“”…—–-"
SENTENCE_END = ".!?…"
CLAUSE_END = ",;:"


def norm_word(text):
    return text.strip().strip(EDGE_PUNCT).lower()


def hidden_flags(words, s, fps):
    """Which stored words are left out of the captions (fillers, stutter repeats)."""
    n = len(words)
    flags = [False] * n
    if not (s.hide_fillers or s.hide_repeats):
        return flags
    norms = [norm_word(w.text) for w in words]
    for i, w in enumerate(words):
        if s.hide_fillers and norms[i] in FILLERS:
            flags[i] = True
            continue
        if not s.hide_repeats:
            continue
        raw = w.text.strip().rstrip(".,!?;:…")
        if raw.endswith(("-", "—", "–")):      # cut-off fragment such as "th-"
            flags[i] = True
            continue
        j = i + 1
        if (j < n and norms[i] and norms[j] == norms[i]
                and words[j].speaker == w.speaker
                and 0 <= words[j].start - w.end < fps):
            flags[i] = True                               # keep only the last repeat
    return flags


def caption_text(words, a, b, s, hidden):
    parts = []
    for i in range(a, b + 1):
        if hidden[i]:
            continue
        t = words[i].text.strip()
        if s.strip_punctuation:
            t = t.strip(EDGE_PUNCT)
        if t:
            parts.append(t)
    text = " ".join(parts)
    if s.text_case == "UPPER":
        text = text.upper()
    elif s.text_case == "LOWER":
        text = text.lower()
    return text


def build_groups(words, first, last, s, fps, hidden):
    """Split words[first..last] into caption groups. Returns [(first_index, last_index)]."""
    groups, cur, cur_chars = [], [], 0
    gap_frames = s.gap_break * fps
    for i in range(first, last + 1):
        if hidden[i]:
            continue
        w = words[i]
        wlen = len(w.text.strip())
        if cur:
            prev = words[cur[-1]]
            tail = prev.text.strip()[-1:]
            brk = (
                w.brk
                or w.speaker != prev.speaker
                or len(cur) >= s.words_per_group
                or (s.max_chars > 0 and cur_chars + 1 + wlen > s.max_chars)
                or (w.start - prev.end) > gap_frames
                or (s.break_on_sentence and tail in SENTENCE_END)
                or (s.break_on_comma and tail in CLAUSE_END)
            )
            if brk:
                groups.append((cur[0], cur[-1]))
                cur, cur_chars = [], 0
        cur_chars += wlen + (1 if cur else 0)
        cur.append(i)
    if cur:
        groups.append((cur[0], cur[-1]))
    return groups


def group_times(words, groups, s, fps, hidden):
    """Start and end frame per group. Captions hold until the same speaker's next one when the gap is short."""
    hold = s.hold_gap * fps
    tail = int(round(min(hold, 0.2 * fps)))
    n = len(words)
    out, prev_spk = [], None
    for a, b in groups:
        spk = words[a].speaker
        start = int(round(words[a].start))
        end = int(round(words[b].end))
        j = b + 1
        while j < n and hidden[j]:
            j += 1
        if j < n and words[j].speaker == spk:
            nxt = int(round(words[j].start))
            if nxt >= end:
                end = nxt if (nxt - end) <= hold else min(end + tail, nxt)
        else:
            end += tail
        if out and prev_spk == spk and start < out[-1][1]:
            start = out[-1][1]
        if end <= start:
            end = start + 1
        out.append((start, end))
        prev_spk = spk
    return out


# ---------------------------------------------------------------------------
# Strips, style and animation
# ---------------------------------------------------------------------------

def caption_strips(se):
    return [st for st in se.strips if st.type == "TEXT" and st.get("cc_first") is not None]


def strip_for_word(se, index):
    for st in caption_strips(se):
        if st["cc_first"] <= index <= st["cc_last"]:
            return st
    return None


def strip_speaker(scene, strip):
    words = scene.cc_words
    a = strip["cc_first"]
    return words[a].speaker if 0 <= a < len(words) else 0


def speaker_look(scene, index):
    """(color, x, y) for a speaker. Speaker 1 uses the main Caption Look settings."""
    s = scene.cc_settings
    extra = scene.cc_extra_speakers
    if index <= 0 or index - 1 >= len(extra):
        return (s.color, s.pos_x, s.pos_y)
    e = extra[index - 1]
    return (e.color, e.pos_x, e.pos_y)


def ensure_speakers(scene, count):
    """Create look settings for speakers 2 and up as they appear."""
    global _suspend
    extra = scene.cc_extra_speakers
    if len(extra) >= count - 1:
        return
    s = scene.cc_settings
    was = _suspend
    _suspend = True
    try:
        while len(extra) < count - 1:
            k = len(extra)
            e = extra.add()
            e.color = SPEAKER_COLORS[k % len(SPEAKER_COLORS)]
            e.pos_x = s.pos_x
            e.pos_y = min(0.95, s.pos_y + 0.12 * (k + 1))
    finally:
        _suspend = was


def refresh_strip_text(scene, strip, hidden=None):
    words = scene.cc_words
    s = scene.cc_settings
    a, b = strip["cc_first"], strip["cc_last"]
    if 0 <= a <= b < len(words):
        if hidden is None:
            hidden = hidden_flags(words, s, scene_fps(scene))
        strip.text = caption_text(words, a, b, s, hidden)


def apply_style(strip, s, font, look):
    color, x, y = look
    _set(strip, "font", font)
    _set(strip, "font_size", s.font_size)
    _set(strip, "color", color)
    _set(strip, "use_bold", s.use_bold)
    _set(strip, "use_outline", s.use_outline)
    _set(strip, "outline_color", s.outline_color)
    _set(strip, "outline_width", s.outline_width)
    _set(strip, "use_shadow", s.use_shadow)
    _set(strip, "shadow_color", s.shadow_color)
    _set(strip, "use_box", s.use_box)
    _set(strip, "box_color", s.box_color)
    _set(strip, "box_margin", s.box_margin)
    _set(strip, "wrap_width", s.wrap_width)
    _set(strip, "anchor_x", "CENTER")
    _set(strip, "anchor_y", "CENTER")
    _set(strip, "alignment_x", "CENTER")
    _set(strip, "location", (x, y))
    # Scale from the text's own position so the pop does not drift.
    _set(strip.transform, "origin", (x, y))


def new_text_strip(se, name, channel, start, length):
    try:
        return se.strips.new_effect(name=name, type="TEXT", channel=channel,
                                    frame_start=start, length=length)
    except TypeError:
        return se.strips.new_effect(name=name, type="TEXT", channel=channel,
                                    frame_start=start, frame_end=start + length)


def animate_strip(strip, s, start, end):
    """Plain keyframes, so every caption can be reshaped in the Graph Editor."""
    dur = end - start
    if s.use_pop and dur >= 2:
        n = max(1, min(s.pop_frames, dur - 1))
        tr = strip.transform
        for attr in ("scale_x", "scale_y"):
            setattr(tr, attr, s.pop_start_scale)
            tr.keyframe_insert(attr, frame=start)
            setattr(tr, attr, 1.0)
            tr.keyframe_insert(attr, frame=start + n)
    if s.use_fade and dur >= 2:
        n = max(1, min(s.fade_frames, dur - 1))
        strip.blend_alpha = 0.0
        strip.keyframe_insert("blend_alpha", frame=start)
        strip.blend_alpha = 1.0
        strip.keyframe_insert("blend_alpha", frame=start + n)


_PATH_NAME = re.compile(r'(?:strips_all|sequences_all)\["(.+?)"\]')


def _fcurve_collections(scene):
    ad = scene.animation_data
    act = ad.action if ad else None
    if act is None:
        return []
    cols = []
    layers = getattr(act, "layers", None)
    if layers is not None and len(layers):
        for layer in layers:
            for lstrip in layer.strips:
                for bag in getattr(lstrip, "channelbags", []):
                    cols.append(bag.fcurves)
    elif hasattr(act, "fcurves"):
        cols.append(act.fcurves)
    return cols


def remove_fcurves(scene, names):
    if not names:
        return
    try:
        for col in _fcurve_collections(scene):
            for fc in list(col):
                m = _PATH_NAME.search(fc.data_path)
                if m and m.group(1) in names:
                    col.remove(fc)
    except Exception as e:
        print(f"[Captions] Could not clean old keyframes: {e}")


def set_pop_interpolation(scene, names, interpolation):
    if not names:
        return
    try:
        for col in _fcurve_collections(scene):
            for fc in col:
                if not fc.data_path.endswith(("scale_x", "scale_y")):
                    continue
                m = _PATH_NAME.search(fc.data_path)
                if not (m and m.group(1) in names):
                    continue
                for kp in fc.keyframe_points:
                    kp.interpolation = interpolation
                    if interpolation not in {"BEZIER", "LINEAR"}:
                        kp.easing = "EASE_OUT"
                fc.update()
    except Exception as e:
        print(f"[Captions] Could not set keyframe interpolation: {e}")


def pick_channel(scene, se):
    """Channel for speaker 1. Each further speaker goes one channel higher."""
    s = scene.cc_settings
    if s.channel > 0:
        return s.channel
    if scene.cc_channel_used > 0:
        return scene.cc_channel_used
    top = max((st.channel for st in se.strips), default=0)
    return top + 1


def generate(scene, runs=None, doomed=None):
    """Build caption strips from the stored words.

    runs=None rebuilds everything; otherwise only the given (first, last) word
    ranges, replacing the strips in `doomed`.
    """
    se = scene.sequence_editor
    if se is None:
        return 0
    s = scene.cc_settings
    words = scene.cc_words
    fps = scene_fps(scene)

    if runs is None:
        doomed = caption_strips(se)
        runs = [(0, len(words) - 1)] if len(words) else []
    doomed = doomed or []
    old_names = {st.name for st in doomed}
    for st in doomed:
        se.strips.remove(st)
    remove_fcurves(scene, old_names)

    base = pick_channel(scene, se)
    scene.cc_channel_used = base
    scene.cc_generation += 1
    gen = scene.cc_generation
    font = load_font(s.font_path)
    hidden = hidden_flags(words, s, fps)
    ensure_speakers(scene, max((w.speaker for w in words), default=0) + 1)

    made = set()
    for first, last in runs:
        groups = build_groups(words, first, last, s, fps, hidden)
        times = group_times(words, groups, s, fps, hidden)
        for (a, b), (start, end) in zip(groups, times):
            text = caption_text(words, a, b, s, hidden)
            if not text:
                continue
            spk = words[a].speaker
            strip = new_text_strip(se, f"Cap{gen:02d}_{a:04d}", base + spk, start, end - start)
            if strip is None:
                continue
            strip["cc_first"] = a
            strip["cc_last"] = b
            strip.text = text
            apply_style(strip, s, font, speaker_look(scene, spk))
            animate_strip(strip, s, start, end)
            made.add(strip.name)
    if s.use_pop:
        set_pop_interpolation(scene, made, s.pop_interpolation)
    return len(made)


def regen_strips(scene, strips):
    """Rebuild just these caption strips from their words."""
    words = scene.cc_words
    idx = sorted({i for st in strips for i in range(st["cc_first"], st["cc_last"] + 1)
                  if 0 <= i < len(words)})
    if not idx:
        return 0
    runs, run_start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i != prev + 1:
            runs.append((run_start, prev))
            run_start = i
        prev = i
    runs.append((run_start, prev))
    return generate(scene, runs=runs, doomed=list(strips))


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

def style_update(self, context):
    """Live update: restyle every existing caption without rebuilding it."""
    if _suspend:
        return
    scene = self.id_data
    se = getattr(scene, "sequence_editor", None)
    if se is None:
        return
    font = load_font(self.font_path)
    hidden = hidden_flags(scene.cc_words, self, scene_fps(scene))
    for st in caption_strips(se):
        apply_style(st, self, font, speaker_look(scene, strip_speaker(scene, st)))
        refresh_strip_text(scene, st, hidden)


LANGUAGES = [
    ("auto", "Detect automatically", ""),
    ("en", "English", ""), ("es", "Spanish", ""), ("fr", "French", ""),
    ("de", "German", ""), ("it", "Italian", ""), ("pt", "Portuguese", ""),
    ("nl", "Dutch", ""), ("pl", "Polish", ""), ("sv", "Swedish", ""),
    ("da", "Danish", ""), ("fi", "Finnish", ""), ("cs", "Czech", ""),
    ("ro", "Romanian", ""), ("hu", "Hungarian", ""), ("el", "Greek", ""),
    ("tr", "Turkish", ""), ("ru", "Russian", ""), ("uk", "Ukrainian", ""),
    ("id", "Indonesian", ""), ("ja", "Japanese", ""), ("zh", "Chinese", ""),
]


class CaptionSettings(PropertyGroup):
    # --- transcription ---
    model_size: EnumProperty(
        name="Model",
        description="Larger models are more accurate and slower",
        items=[
            ("tiny", "Tiny", ""), ("base", "Base", ""), ("small", "Small", ""),
            ("medium", "Medium", ""),
            ("distil-large-v3", "Distil Large v3 (fast, English)", ""),
            ("large-v2", "Large v2", ""),
            ("large-v3", "Large v3 (most accurate)", ""),
        ],
        default="large-v3",
    )
    device: EnumProperty(
        name="Device",
        items=[("cuda", "GPU (NVIDIA)", ""), ("cpu", "CPU", "")],
        default="cuda",
    )
    compute_type: EnumProperty(
        name="Compute",
        items=[("float16", "float16 (GPU)", ""), ("int8_float16", "int8_float16 (GPU, less memory)", ""),
               ("int8", "int8 (CPU)", ""), ("float32", "float32", "")],
        default="float16",
    )
    language: EnumProperty(name="Language", items=LANGUAGES, default="auto")
    beam_size: IntProperty(name="Beam Size", default=5, min=1, max=20)
    use_vad: BoolProperty(name="Skip Silence", default=True,
                          description="Voice activity detection: ignore silence and music")
    verbatim_hint: BoolProperty(
        name="Keep Stutters & Fillers", default=False,
        description="Hint Whisper to write down um, uh and repeated starts. Their time then stops "
                    "leaking into neighbouring words. Works partly; Whisper still drops some")

    # --- look (live) ---
    font_path: StringProperty(name="Font File", subtype="FILE_PATH", update=style_update,
                              description="Font file used for captions. Empty uses Blender's default")
    font_size: FloatProperty(name="Size", default=96.0, min=4.0, max=2000.0, update=style_update)
    color: FloatVectorProperty(name="Color", subtype="COLOR", size=4, min=0.0, max=1.0,
                               default=(1.0, 1.0, 1.0, 1.0), update=style_update)
    use_bold: BoolProperty(name="Bold", default=False, update=style_update)
    text_case: EnumProperty(
        name="Case",
        items=[("UPPER", "UPPERCASE", ""), ("ORIGINAL", "As Spoken", ""), ("LOWER", "lowercase", "")],
        default="UPPER", update=style_update,
    )
    strip_punctuation: BoolProperty(name="Hide Punctuation", default=True, update=style_update)
    use_outline: BoolProperty(name="Outline", default=True, update=style_update)
    outline_color: FloatVectorProperty(name="Outline Color", subtype="COLOR", size=4, min=0.0, max=1.0,
                                       default=(0.0, 0.0, 0.0, 1.0), update=style_update)
    outline_width: FloatProperty(name="Outline Width", default=0.08, min=0.0, max=1.0, update=style_update)
    use_shadow: BoolProperty(name="Shadow", default=False, update=style_update)
    shadow_color: FloatVectorProperty(name="Shadow Color", subtype="COLOR", size=4, min=0.0, max=1.0,
                                      default=(0.0, 0.0, 0.0, 0.8), update=style_update)
    use_box: BoolProperty(name="Box", default=False, update=style_update)
    box_color: FloatVectorProperty(name="Box Color", subtype="COLOR", size=4, min=0.0, max=1.0,
                                   default=(0.0, 0.0, 0.0, 0.6), update=style_update)
    box_margin: FloatProperty(name="Box Margin", default=0.01, min=0.0, max=1.0, update=style_update)
    pos_x: FloatProperty(name="X", default=0.5, min=0.0, max=1.0, subtype="FACTOR", update=style_update)
    pos_y: FloatProperty(name="Y", default=0.3, min=0.0, max=1.0, subtype="FACTOR", update=style_update,
                         description="0 is the bottom of the frame, 1 is the top")
    wrap_width: FloatProperty(name="Wrap Width", default=0.9, min=0.0, max=1.0, subtype="FACTOR",
                              update=style_update, description="0 turns wrapping off")

    # --- grouping (applied on Regenerate) ---
    words_per_group: IntProperty(name="Words per Caption", default=2, min=1, max=12)
    max_chars: IntProperty(name="Max Characters", default=16, min=0, max=200,
                           description="Start a new caption before exceeding this many characters. 0 is off")
    gap_break: FloatProperty(name="Pause Break (s)", default=0.5, min=0.0, max=5.0,
                             description="Start a new caption after a pause this long")
    break_on_sentence: BoolProperty(name="Break at Sentence End", default=True)
    break_on_comma: BoolProperty(name="Break at Commas", default=False)
    hold_gap: FloatProperty(name="Hold Through Gaps (s)", default=0.4, min=0.0, max=5.0,
                            description="Keep a caption on screen until the next one if the gap is shorter than this")
    channel: IntProperty(name="Channel", default=0, min=0, max=128,
                         description="Channel for caption strips. 0 picks the first channel above everything")

    hide_fillers: BoolProperty(name="Hide Fillers", default=True,
                               description="Leave um, uh and similar out of the captions")
    hide_repeats: BoolProperty(name="Hide Stutter Repeats", default=False,
                               description="Show only the last of immediately repeated words (I I I think) "
                                           "and drop cut-off fragments. Also hides deliberate repeats like 'no no no'")

    # --- animation (applied on Regenerate) ---
    use_pop: BoolProperty(name="Pop-in", default=True)
    pop_frames: IntProperty(name="Frames", default=4, min=1, max=60)
    pop_start_scale: FloatProperty(name="Start Scale", default=0.7, min=0.0, max=2.0)
    pop_interpolation: EnumProperty(
        name="Curve",
        items=[("BACK", "Overshoot", "Passes full size slightly, then settles"),
               ("BEZIER", "Smooth", ""), ("LINEAR", "Linear", ""),
               ("CUBIC", "Ease Out", ""), ("ELASTIC", "Elastic", ""), ("BOUNCE", "Bounce", "")],
        default="BACK",
    )
    use_fade: BoolProperty(name="Fade-in", default=False)
    fade_frames: IntProperty(name="Frames", default=2, min=1, max=60)


def word_text_update(self, context):
    if _suspend:
        return
    scene = self.id_data
    se = getattr(scene, "sequence_editor", None)
    if se is None:
        return
    ptr = self.as_pointer()
    for i, w in enumerate(scene.cc_words):
        if w.as_pointer() == ptr:
            st = strip_for_word(se, i)
            if st:
                refresh_strip_text(scene, st)
            break


class CaptionWord(PropertyGroup):
    text: StringProperty(update=word_text_update, options={"TEXTEDIT_UPDATE"})
    start: FloatProperty()   # timeline frame
    end: FloatProperty()     # timeline frame
    seg: IntProperty()       # sentence number from Whisper
    speaker: IntProperty()   # 0 is speaker 1
    brk: BoolProperty()      # always start a new caption at this word


def speaker_update(self, context):
    if _suspend:
        return
    scene = self.id_data
    style_update(scene.cc_settings, context)


class ExtraSpeaker(PropertyGroup):
    color: FloatVectorProperty(name="Color", subtype="COLOR", size=4, min=0.0, max=1.0,
                               default=(1.0, 0.85, 0.1, 1.0), update=speaker_update)
    pos_x: FloatProperty(name="X", default=0.5, min=0.0, max=1.0, subtype="FACTOR", update=speaker_update)
    pos_y: FloatProperty(name="Y", default=0.42, min=0.0, max=1.0, subtype="FACTOR", update=speaker_update)


def word_index_update(self, context):
    """Clicking a word jumps the playhead to it and selects its caption."""
    if _suspend:
        return
    scene = self
    i = scene.cc_word_index
    if not (0 <= i < len(scene.cc_words)):
        return
    scene.frame_current = int(scene.cc_words[i].start)
    se = scene.sequence_editor
    if se is None:
        return
    st = strip_for_word(se, i)
    if st:
        for other in se.strips:
            other.select = False
        st.select = True
        se.active_strip = st


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def strip_media_path(strip):
    try:
        if strip.type == "SOUND" and strip.sound:
            path = strip.sound.filepath
        elif strip.type == "MOVIE":
            path = strip.filepath
        else:
            return None
        path = bpy.path.abspath(path)
        return path if os.path.isfile(path) else None
    except Exception:
        return None


def get_source_strip(context):
    scene = get_seq_scene(context)
    se = getattr(scene, "sequence_editor", None)
    if se is None:
        return None
    candidates = []
    if se.active_strip and se.active_strip.select:
        candidates.append(se.active_strip)
    candidates += list(getattr(context, "selected_strips", None) or [])
    for st in candidates:
        if st.type in {"SOUND", "MOVIE"} and strip_media_path(st):
            return st
    return None


def whisper_error_message(e):
    low = str(e).lower()
    if "cublas" in low or "cudnn" in low or "cuda" in low:
        return "GPU libraries are missing. Click 'GPU Libraries' (as administrator), or switch Device to CPU."
    if "metadata_errors" in low:
        return "Incompatible audio library. Click 'Install / Repair' (as administrator)."
    if "memory" in low:
        return "Out of memory. Try a smaller model."
    return f"Transcription failed: {e}"


def strip_clip_range(strip, fps):
    """(timeline start frame, start second inside the file, length in seconds) of the visible part."""
    return (int(strip.frame_final_start),
            max(0.0, float(strip.frame_offset_start) / fps),
            float(strip.frame_final_duration) / fps)


def run_whisper(context, s, source, clip_start, clip_len):
    """Transcribe part of a media file. Returns ([(text, start_s, end_s, sentence)], language)."""
    from faster_whisper.audio import decode_audio
    wm = context.window_manager
    audio = decode_audio(source, sampling_rate=16000)
    a = int(clip_start * 16000)
    clip = audio[a:a + int(clip_len * 16000)]
    del audio
    if len(clip) == 0:
        return [], ""
    model = get_model(s.model_size, s.device, s.compute_type)
    found = []
    wm.progress_begin(0, 100)
    try:
        segments, info = model.transcribe(
            clip,
            language=None if s.language == "auto" else s.language,
            task="transcribe",
            beam_size=s.beam_size,
            vad_filter=s.use_vad,
            vad_parameters=dict(min_silence_duration_ms=500),
            word_timestamps=True,
            initial_prompt=VERBATIM_PROMPT if s.verbatim_hint else None,
        )
        for si, seg in enumerate(segments):
            for w in (seg.words or []):
                text = w.word.strip()
                if text:
                    found.append((text, float(w.start), float(w.end), si))
            wm.progress_update(int(min(99, 100 * seg.end / max(clip_len, 0.01))))
    finally:
        wm.progress_end()
    return found, info.language


def store_words(scene, found, base_frame, speaker, replace):
    """Save transcribed words on the scene. Returns the index of the first new word."""
    global _suspend
    fps = scene_fps(scene)
    _suspend = True
    try:
        if replace:
            scene.cc_words.clear()
        first_new = len(scene.cc_words)
        for text, start, end, si in found:
            w = scene.cc_words.add()
            w.name = text
            w.text = text
            w.start = base_frame + start * fps
            w.end = base_frame + end * fps
            w.seg = si
            w.speaker = speaker
        scene.cc_word_index = first_new
    finally:
        _suspend = False
    return first_new


class SEQUENCER_OT_cc_transcribe(Operator):
    """Transcribe the visible part of the selected video or audio strip into animated captions"""
    bl_idname = "sequencer.cc_transcribe"
    bl_label = "Transcribe to Captions"
    bl_options = {"REGISTER", "UNDO"}

    add_speaker: BoolProperty(
        default=False,
        description="Keep the existing captions and add this strip as another speaker",
    )

    @classmethod
    def poll(cls, context):
        if _fw is None:
            cls.poll_message_set("Transcription libraries are not installed.")
            return False
        if get_source_strip(context) is None:
            cls.poll_message_set("Select a video or audio strip first.")
            return False
        return True

    def execute(self, context):
        scene = get_seq_scene(context)
        s = scene.cc_settings
        strip = get_source_strip(context)
        path = strip_media_path(strip)
        base_frame, clip_start, clip_len = strip_clip_range(strip, scene_fps(scene))

        try:
            self.report({"INFO"}, "Transcribing...")
            found, language = run_whisper(context, s, path, clip_start, clip_len)
        except Exception as e:
            traceback.print_exc()
            self.report({"ERROR"}, whisper_error_message(e))
            return {"CANCELLED"}
        if not found:
            self.report({"WARNING"}, "No speech found.")
            return {"CANCELLED"}

        add = self.add_speaker and len(scene.cc_words) > 0
        speaker = max(w.speaker for w in scene.cc_words) + 1 if add else 0
        first_new = store_words(scene, found, base_frame, speaker, replace=not add)
        if add:
            n = generate(scene, runs=[(first_new, len(scene.cc_words) - 1)], doomed=[])
        else:
            scene.cc_channel_used = 0
            n = generate(scene)
        who = f" as speaker {speaker + 1}" if speaker else ""
        self.report({"INFO"}, f"{len(found)} words, {n} captions{who} ({language}).")
        return {"FINISHED"}


class SEQUENCER_OT_cc_regenerate(Operator):
    """Rebuild caption strips from the stored words using the current settings"""
    bl_idname = "sequencer.cc_regenerate"
    bl_label = "Regenerate Captions"
    bl_options = {"REGISTER", "UNDO"}

    only_selected: BoolProperty(
        default=False,
        description="Rebuild only the selected captions and leave hand-edited ones alone",
    )

    @classmethod
    def poll(cls, context):
        scene = get_seq_scene(context)
        return scene.sequence_editor is not None and len(scene.cc_words) > 0

    def execute(self, context):
        scene = get_seq_scene(context)
        se = scene.sequence_editor
        if not self.only_selected:
            n = generate(scene)
            self.report({"INFO"}, f"{n} captions rebuilt.")
            return {"FINISHED"}

        chosen = [st for st in caption_strips(se) if st.select]
        if not chosen:
            self.report({"WARNING"}, "Select some caption strips first.")
            return {"CANCELLED"}
        n = regen_strips(scene, chosen)
        self.report({"INFO"}, f"{n} captions rebuilt.")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------

class _CCPanel:
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = TAB


class SEQUENCER_PT_cc_transcribe(_CCPanel, Panel):
    bl_label = "Transcribe"
    bl_idname = "SEQUENCER_PT_cc_transcribe"

    def draw(self, context):
        layout = self.layout
        s = get_seq_scene(context).cc_settings
        ready = _fw is not None

        row = layout.row(align=True)
        row.label(text="Libraries", icon="CHECKMARK" if ready else "ERROR")
        row.operator("sequencer.cc_setup", text="Install / Repair").gpu = False
        if OS_NAME != "Darwin":
            row.operator("sequencer.cc_setup", text="GPU Libraries").gpu = True

        col = layout.column()
        col.enabled = ready
        col.prop(s, "model_size")
        row = col.row(align=True)
        row.prop(s, "device", text="")
        row.prop(s, "compute_type", text="")
        col.prop(s, "language")
        row = col.row(align=True)
        row.prop(s, "beam_size")
        row.prop(s, "use_vad")
        col.prop(s, "verbatim_hint")

        col.separator()
        src = get_source_strip(context)
        col.label(text=f"Source: {src.name}" if src else "Select a video or audio strip",
                  icon="SOUND" if src else "INFO")
        big = col.row()
        big.scale_y = 1.4
        big.operator("sequencer.cc_transcribe", icon="REC").add_speaker = False
        if _model_cache["model"] is not None:
            col.operator("sequencer.cc_free_model", icon="X")


class SEQUENCER_PT_cc_style(_CCPanel, Panel):
    bl_label = "Caption Look"
    bl_idname = "SEQUENCER_PT_cc_style"

    def draw(self, context):
        layout = self.layout
        s = get_seq_scene(context).cc_settings

        box = layout.box()
        box.label(text=f"Font: {font_label(s.font_path)}", icon="FONT_DATA")
        row = box.row(align=True)
        row.operator("sequencer.cc_pick_font", text="Choose Font", icon="VIEWZOOM")
        row.operator("sequencer.cc_rescan_fonts", text="", icon="FILE_REFRESH")
        box.prop(s, "font_path", text="")
        row = box.row(align=True)
        row.prop(s, "font_size")
        row.prop(s, "use_bold", toggle=True)
        box.prop(s, "color")
        box.prop(s, "text_case")
        box.prop(s, "strip_punctuation")

        box = layout.box()
        box.prop(s, "use_outline")
        sub = box.row(align=True)
        sub.enabled = s.use_outline
        sub.prop(s, "outline_color", text="")
        sub.prop(s, "outline_width", text="Width")
        box.prop(s, "use_shadow")
        sub = box.row()
        sub.enabled = s.use_shadow
        sub.prop(s, "shadow_color", text="")
        box.prop(s, "use_box")
        sub = box.row(align=True)
        sub.enabled = s.use_box
        sub.prop(s, "box_color", text="")
        sub.prop(s, "box_margin", text="Margin")

        box = layout.box()
        box.label(text="Position")
        row = box.row(align=True)
        row.prop(s, "pos_x")
        row.prop(s, "pos_y")
        box.prop(s, "wrap_width")
        layout.label(text="Changes here apply to all captions instantly.", icon="INFO")


class SEQUENCER_PT_cc_build(_CCPanel, Panel):
    bl_label = "Grouping & Animation"
    bl_idname = "SEQUENCER_PT_cc_build"

    def draw(self, context):
        layout = self.layout
        s = get_seq_scene(context).cc_settings

        box = layout.box()
        box.prop(s, "words_per_group")
        box.prop(s, "max_chars")
        box.prop(s, "gap_break")
        box.prop(s, "hold_gap")
        row = box.row(align=True)
        row.prop(s, "break_on_sentence", text="Sentence Break", toggle=True)
        row.prop(s, "break_on_comma", text="Comma Break", toggle=True)
        box.prop(s, "channel")
        row = box.row(align=True)
        row.prop(s, "hide_fillers", toggle=True)
        row.prop(s, "hide_repeats", toggle=True)

        box = layout.box()
        box.prop(s, "use_pop")
        sub = box.column(align=True)
        sub.enabled = s.use_pop
        row = sub.row(align=True)
        row.prop(s, "pop_frames")
        row.prop(s, "pop_start_scale")
        sub.prop(s, "pop_interpolation")
        box.prop(s, "use_fade")
        sub = box.row()
        sub.enabled = s.use_fade
        sub.prop(s, "fade_frames")

        col = layout.column(align=True)
        col.scale_y = 1.3
        col.operator("sequencer.cc_regenerate", text="Regenerate All", icon="FILE_REFRESH").only_selected = False
        col.operator("sequencer.cc_regenerate", text="Regenerate Selected Only").only_selected = True
        layout.label(text="Regenerate All replaces hand-edited keyframes.", icon="INFO")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    CaptionSettings,
    CaptionWord,
    ExtraSpeaker,
    SEQUENCER_OT_cc_setup,
    SEQUENCER_OT_cc_free_model,
    SEQUENCER_OT_cc_pick_font,
    SEQUENCER_OT_cc_rescan_fonts,
    SEQUENCER_OT_cc_transcribe,
    SEQUENCER_OT_cc_regenerate,
    SEQUENCER_PT_cc_transcribe,
    SEQUENCER_PT_cc_style,
    SEQUENCER_PT_cc_build,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.cc_settings = PointerProperty(type=CaptionSettings)
    bpy.types.Scene.cc_words = CollectionProperty(type=CaptionWord)
    bpy.types.Scene.cc_extra_speakers = CollectionProperty(type=ExtraSpeaker)
    bpy.types.Scene.cc_word_index = IntProperty(default=0, update=word_index_update)
    bpy.types.Scene.cc_generation = IntProperty(default=0)
    bpy.types.Scene.cc_channel_used = IntProperty(default=0)
    check_faster_whisper()


def unregister():
    _model_cache["model"] = None
    _model_cache["key"] = None
    for prop in ("cc_settings", "cc_words", "cc_extra_speakers", "cc_word_index", "cc_generation", "cc_channel_used"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
