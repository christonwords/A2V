import io
import json
import re
import shutil
import subprocess
import tempfile
import wave
import zipfile
from pathlib import Path

import streamlit as st

AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

PRESETS = {
    "720p": (1280, 720, 23, "veryfast"),
    "1080p": (1920, 1080, 21, "veryfast"),
    "Square": (1080, 1080, 22, "veryfast"),
    "Small Fast": (854, 480, 25, "ultrafast"),
}


def run(cmd, check=True):
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr[-3000:] or result.stdout[-3000:])
    return result


def safe_name(name):
    name = Path(name).stem
    name = re.sub(r"[^\w\s\-().&]+", "", name, flags=re.UNICODE)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "untitled"


def ffprobe_duration(path):
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

    if Path(path).suffix.lower() == ".wav":
        with wave.open(str(path), "rb") as wav:
            return wav.getnframes() / float(wav.getframerate())

    raise RuntimeError(f"Could not read duration for {Path(path).name}")


def audio_codec_args(audio_path):
    ext = Path(audio_path).suffix.lower()
    if ext in {".m4a", ".aac", ".mp3"}:
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", "192k"]


def video_filter(width, height):
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        "format=yuv420p"
    )


def render_single(image, audio, outputs, width, height, crf, preset):
    duration = ffprobe_duration(audio)
    title = safe_name(audio.name)
    out = outputs / f"{title}.mp4"

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1",
        "-i", str(image),
        "-i", str(audio),
        "-t", f"{duration:.3f}",
        "-vf", video_filter(width, height),
        "-r", "1",
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        *audio_codec_args(audio),
        "-movflags", "+faststart",
        "-shortest",
        str(out),
    ]
    run(cmd)
    return out


def make_concat_audio(audios, temp):
    concat_file = temp / "concat.txt"
    with concat_file.open("w", encoding="utf-8") as f:
        for audio in audios:
            escaped = str(audio).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

    joined = temp / "project_audio.m4a"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c:a", "aac",
        "-b:a", "192k",
        str(joined),
    ]
    run(cmd)
    return joined


def format_timestamp(seconds):
    seconds = int(round(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def write_timestamps(audios, outputs, project_title):
    path = outputs / f"{safe_name(project_title)} - timestamps.txt"
    cursor = 0.0
    lines = []
    for audio in audios:
        lines.append(f"{format_timestamp(cursor)} {safe_name(audio.name)}")
        cursor += ffprobe_duration(audio)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def render_project(image, audios, outputs, temp, project_title, width, height, crf, preset):
    joined_audio = make_concat_audio(audios, temp)
    duration = ffprobe_duration(joined_audio)
    out = outputs / f"{safe_name(project_title)}.mp4"

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1",
        "-i", str(image),
        "-i", str(joined_audio),
        "-t", f"{duration:.3f}",
        "-vf", video_filter(width, height),
        "-r", "1",
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(out),
    ]
    run(cmd)
    timestamps = write_timestamps(audios, outputs, project_title)
    return [out, timestamps]


def make_zip(outputs):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for item in sorted(outputs.iterdir()):
            if item.is_file():
                z.write(item, item.name)
    buffer.seek(0)
    return buffer


def save_upload(upload, folder):
    suffix = Path(upload.name).suffix.lower()
    path = folder / upload.name
    path.write_bytes(upload.getbuffer())
    return path, suffix


st.set_page_config(page_title="A2V Renderer", page_icon="🎵", layout="centered")
st.title("A2V Renderer")
st.caption("Upload one artwork image and one or more audio files, then export MP4s as a ZIP.")

mode = st.radio("Mode", ["Singles", "Full Project"], horizontal=True)
preset_name = st.selectbox("Preset", list(PRESETS.keys()), index=1)
project_title = st.text_input("Project title", "A2V Full Project")

artwork = st.file_uploader("Artwork image", type=[ext[1:] for ext in sorted(IMAGE_EXTS)])
audio_files = st.file_uploader(
    "Audio files",
    type=[ext[1:] for ext in sorted(AUDIO_EXTS)],
    accept_multiple_files=True,
)

if st.button("Render", type="primary", disabled=not artwork or not audio_files):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        inputs = root / "inputs"
        outputs = root / "outputs"
        temp = root / "temp"
        for folder in (inputs, outputs, temp):
            folder.mkdir(parents=True, exist_ok=True)

        try:
            width, height, crf, ff_preset = PRESETS[preset_name]
            image_path, image_ext = save_upload(artwork, inputs)
            if image_ext not in IMAGE_EXTS:
                st.error("Please upload a supported artwork image.")
                st.stop()

            audio_paths = []
            for audio in audio_files:
                path, ext = save_upload(audio, inputs)
                if ext in AUDIO_EXTS:
                    audio_paths.append(path)

            audio_paths = sorted(audio_paths, key=lambda p: p.name.lower())
            if not audio_paths:
                st.error("Please upload at least one supported audio file.")
                st.stop()

            progress = st.progress(0)
            status = st.empty()

            if mode == "Singles":
                rendered = []
                for index, audio in enumerate(audio_paths, start=1):
                    status.write(f"Rendering {index}/{len(audio_paths)}: {audio.name}")
                    rendered.append(render_single(image_path, audio, outputs, width, height, crf, ff_preset))
                    progress.progress(index / len(audio_paths))
            else:
                status.write("Rendering full project...")
                rendered = render_project(image_path, audio_paths, outputs, temp, project_title, width, height, crf, ff_preset)
                progress.progress(1.0)

            zip_buffer = make_zip(outputs)
            st.success(f"Done. Created {len(rendered)} file(s).")
            st.download_button(
                "Download ZIP",
                data=zip_buffer,
                file_name="a2v_exports.zip",
                mime="application/zip",
            )

        except FileNotFoundError as e:
            st.error("ffmpeg/ffprobe was not found. Install ffmpeg in your environment.")
            st.exception(e)
        except Exception as e:
            st.error("Render failed.")
            st.exception(e)
