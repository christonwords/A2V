import os
import re
import json
import wave
import uuid
import shutil
import zipfile
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timedelta

import streamlit as st

# Store EVERYTHING in /tmp so Render never keeps user uploads/renders in the repo folder.
BASE_TMP = Path(os.getenv("A2V_TMP_DIR", "/tmp/a2v_renderer"))
LEGACY_DIRS = [Path("inputs"), Path("outputs"), Path("temp"), Path("a2v_mobile"), Path("/content/a2v_mobile")]

AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

PRESETS = {
    "1920x1080 / 16:9 HD": (1920, 1080, 21, "veryfast"),
    "1280x720 / 16:9 Fast": (1280, 720, 23, "veryfast"),
    "1080x1080 / Square": (1080, 1080, 22, "veryfast"),
    "1080x1920 / 9:16 Vertical": (1080, 1920, 22, "veryfast"),
    "854x480 / Small Fast": (854, 480, 25, "ultrafast"),
    "Custom": None,
}


def safe_name(name: str) -> str:
    name = Path(name).stem
    name = re.sub(r"[^\w\s\-().&]+", "", name, flags=re.UNICODE)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "untitled"


def run(cmd, check=True):
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr[-3000:] or result.stdout[-3000:] or "Command failed")
    return result


def cleanup_old_files(max_age_minutes: int = 20):
    """Remove previous sessions plus legacy folders from earlier versions."""
    # Delete old app folders from prior code versions.
    for folder in LEGACY_DIRS:
        try:
            if folder.exists() and folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)
        except Exception:
            pass

    # Delete old /tmp sessions. This also removes any audio/video left from failed jobs.
    BASE_TMP.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now() - timedelta(minutes=max_age_minutes)
    for item in BASE_TMP.iterdir():
        try:
            mtime = datetime.fromtimestamp(item.stat().st_mtime)
            if mtime < cutoff:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
        except Exception:
            pass


def wipe_all_tmp_now():
    """Nuclear cleanup: removes every temp/session file this app created."""
    for folder in LEGACY_DIRS:
        try:
            if folder.exists() and folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)
        except Exception:
            pass
    try:
        if BASE_TMP.exists():
            shutil.rmtree(BASE_TMP, ignore_errors=True)
        BASE_TMP.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def ffprobe_duration(path: Path) -> float:
    result = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ], check=False)

    if result.returncode == 0:
        try:
            duration = float(json.loads(result.stdout)["format"]["duration"])
            if duration > 0:
                return duration
        except Exception:
            pass

    if path.suffix.lower() == ".wav":
        with wave.open(str(path), "rb") as wav:
            return wav.getnframes() / float(wav.getframerate())

    raise RuntimeError(f"Could not read duration for {path.name}")


def audio_codec_args(audio_path: Path):
    # Copying common compressed audio is faster. WAV/FLAC/etc get converted to AAC.
    if audio_path.suffix.lower() in {".m4a", ".aac", ".mp3"}:
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", "192k"]


def build_video_filter(width: int, height: int, bg_mode: str, bg_color: str) -> str:
    # Always pad to the selected output ratio. Black is default and works for square artwork -> 16:9.
    color = "black" if bg_mode == "Black" else bg_color.strip() or "black"
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:{color},"
        "format=yuv420p"
    )


def render_single(image: Path, audio: Path, output_dir: Path, width: int, height: int, crf: int, preset: str, bg_mode: str, bg_color: str) -> Path:
    duration = ffprobe_duration(audio)
    out = output_dir / f"{safe_name(audio.name)}.mp4"
    vf = build_video_filter(width, height, bg_mode, bg_color)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-i", str(image), "-i", str(audio),
        "-t", f"{duration:.3f}", "-vf", vf, "-r", "1",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        *audio_codec_args(audio), "-movflags", "+faststart", "-shortest", str(out),
    ]
    run(cmd)
    return out


def make_concat_audio(audios, temp_dir: Path) -> Path:
    concat_file = temp_dir / "concat.txt"
    with concat_file.open("w", encoding="utf-8") as f:
        for audio in audios:
            escaped = str(audio).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
    joined = temp_dir / "project_audio.m4a"
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c:a", "aac", "-b:a", "192k", str(joined),
    ])
    return joined


def format_timestamp(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def write_timestamps(audios, output_dir: Path, project_title: str) -> Path:
    path = output_dir / f"{safe_name(project_title)} - timestamps.txt"
    cursor = 0.0
    lines = []
    for audio in audios:
        lines.append(f"{format_timestamp(cursor)} {safe_name(audio.name)}")
        cursor += ffprobe_duration(audio)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def render_project(image: Path, audios, output_dir: Path, temp_dir: Path, project_title: str, width: int, height: int, crf: int, preset: str, bg_mode: str, bg_color: str):
    joined_audio = make_concat_audio(audios, temp_dir)
    duration = ffprobe_duration(joined_audio)
    out = output_dir / f"{safe_name(project_title)}.mp4"
    vf = build_video_filter(width, height, bg_mode, bg_color)
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-i", str(image), "-i", str(joined_audio),
        "-t", f"{duration:.3f}", "-vf", vf, "-r", "1",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out),
    ])
    timestamps = write_timestamps(audios, output_dir, project_title)
    return [out, timestamps]


def zip_outputs(output_dir: Path) -> bytes:
    zip_path = output_dir / "a2v_exports.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for item in sorted(output_dir.iterdir()):
            if item.is_file() and item.name != zip_path.name:
                z.write(item, item.name)
    data = zip_path.read_bytes()
    return data


def save_upload(uploaded, dest_dir: Path) -> Path:
    ext = Path(uploaded.name).suffix.lower()
    path = dest_dir / f"{safe_name(uploaded.name)}{ext}"
    path.write_bytes(uploaded.getbuffer())
    return path


st.set_page_config(page_title="A2V Renderer", page_icon="🎧", layout="centered")
cleanup_old_files()

st.title("A2V Renderer")
st.caption("Private temp render: uploads and outputs are wiped after each job and old temp files are cleaned on every app load.")

with st.sidebar:
    st.header("Storage")
    st.write("Files are written only to `/tmp` during rendering.")
    if st.button("Clear all temp files now"):
        wipe_all_tmp_now()
        st.success("Temp files cleared.")

mode = st.radio("Mode", ["Singles", "Full Project"], horizontal=True)
preset_name = st.selectbox("Preset / ratio", list(PRESETS.keys()), index=0)

if preset_name == "Custom":
    c1, c2 = st.columns(2)
    width = c1.number_input("Width", min_value=320, max_value=3840, value=1920, step=2)
    height = c2.number_input("Height", min_value=320, max_value=3840, value=1080, step=2)
    crf = st.slider("Quality CRF (lower = better/larger)", 18, 32, 22)
    ff_preset = st.selectbox("Encoding speed", ["ultrafast", "superfast", "veryfast", "faster", "fast"], index=2)
else:
    width, height, crf, ff_preset = PRESETS[preset_name]
    st.info(f"Output: {width}×{height}. Artwork is fit inside this ratio with padding, so square images can become 1920×1080 with black bars.")

bg_mode = st.radio("Background padding", ["Black", "Custom ffmpeg color"], horizontal=True)
bg_color = "black"
if bg_mode != "Black":
    bg_color = st.text_input("Background color", value="black", help="Examples: black, white, #111111")

project_title = st.text_input("Project title", value="A2V Full Project")
artwork = st.file_uploader("Artwork image", type=[e.replace('.', '') for e in IMAGE_EXTS], accept_multiple_files=False)
audios = st.file_uploader("Audio files", type=[e.replace('.', '') for e in AUDIO_EXTS], accept_multiple_files=True)

render_disabled = not artwork or not audios
if st.button("Render", disabled=render_disabled, type="primary"):
    wipe_all_tmp_now()  # remove any current saved audio/video from failed or older runs before starting
    session_dir = BASE_TMP / uuid.uuid4().hex
    input_dir = session_dir / "inputs"
    output_dir = session_dir / "outputs"
    temp_dir = session_dir / "temp"
    for p in (input_dir, output_dir, temp_dir):
        p.mkdir(parents=True, exist_ok=True)

    zip_bytes = None
    try:
        with st.status("Rendering...", expanded=True) as status:
            image_path = save_upload(artwork, input_dir)
            audio_paths = [save_upload(a, input_dir) for a in audios]
            st.write(f"Loaded 1 image and {len(audio_paths)} audio file(s).")

            if mode == "Singles":
                for idx, audio in enumerate(audio_paths, 1):
                    st.write(f"Rendering {idx}/{len(audio_paths)}: {audio.name}")
                    render_single(image_path, audio, output_dir, int(width), int(height), int(crf), ff_preset, bg_mode, bg_color)
            else:
                st.write("Rendering full project...")
                render_project(image_path, audio_paths, output_dir, temp_dir, project_title, int(width), int(height), int(crf), ff_preset, bg_mode, bg_color)

            zip_bytes = zip_outputs(output_dir)
            status.update(label="Done. Download below.", state="complete")

    except Exception as e:
        st.error(str(e))
    finally:
        # Delete every upload, render, temp audio, temp video, and ZIP path from disk.
        try:
            shutil.rmtree(session_dir, ignore_errors=True)
        except Exception:
            pass
        cleanup_old_files(max_age_minutes=0)

    if zip_bytes:
        st.download_button(
            "Download ZIP",
            data=zip_bytes,
            file_name="a2v_exports.zip",
            mime="application/zip",
        )
        st.success("Disk cleanup completed. The downloadable ZIP is held only in the current page session.")

st.divider()
st.caption("Tip: for square artwork into YouTube-style video, choose 1920x1080 / 16:9 HD and Background padding = Black.")
