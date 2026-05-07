# A2V Renderer

Mobile-friendly Python app for turning one artwork image plus one or more audio files into MP4 videos.

## Run in GitHub Codespaces

1. Create a new GitHub repo and upload these files.
2. Open the repo on GitHub.
3. Tap **Code → Codespaces → Create codespace on main**.
4. In the Codespaces terminal, run:

```bash
streamlit run app.py
```

5. Open the forwarded port `8501`.
6. Upload artwork + audio files, render, then download the ZIP.

## Run locally

Install ffmpeg, then:

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy notes

This app needs ffmpeg. Streamlit Community Cloud can install it from `packages.txt`, but large/long renders may hit resource limits. Codespaces is usually better for rendering.
