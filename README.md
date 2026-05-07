# A2V Renderer

Mobile-friendly Streamlit renderer for turning artwork + audio into MP4 exports.

## Render settings

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
python -m streamlit run app.py --server.port 10000 --server.address 0.0.0.0
```

`packages.txt` installs ffmpeg on Render.

## Privacy/storage behavior

- Uploads, temp files, rendered videos, and ZIP files are written only under `/tmp/a2v_renderer`.
- The app deletes old temp files on every page load.
- The app clears all temp files before every new render.
- After creating the ZIP bytes for download, it deletes the whole render session folder.
- The ZIP is kept only in Streamlit's current page session so the user can download it.
- Render free filesystems are ephemeral and reset on restart/redeploy, but this app also cleans up during normal use.

## Features

- Singles mode: one MP4 per audio file.
- Full Project mode: concatenates audio into one MP4 plus timestamps file.
- Presets: 1920x1080, 1280x720, square, vertical, small fast.
- Custom width/height ratio.
- Black padding by default for square artwork into 16:9.
- Optional custom ffmpeg padding color.
- Manual "Clear all temp files now" button in the sidebar.
