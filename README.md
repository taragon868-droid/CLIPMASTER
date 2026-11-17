# Clipmaster

Local shorts maker for this PC. Paste a YouTube, Twitch, or Kick link (a normal video or a live one), or upload a file. Clipmaster transcribes the speech, asks a local Ollama model which moments are worth posting, and writes vertical 9:16 clips with captions and a hook line.

## This machine

- GPU: GTX 1060 6GB, used only for the language model
- Model: `qwen2.5:latest` (Qwen 2.5 7B, about 4.7 GB). A 14B model does not fit in 6 GB, and the 7B model follows the clip format more reliably than a 3B model.
- Speech: faster-whisper `small` on the CPU, so it does not fight the 1060 for VRAM
- Encode: CPU `libx264`. This FFmpeg build's NVENC needs a newer NVIDIA driver than the one installed (582; NVENC here wants 610+).

## Run

1. Ollama has to be running (`ollama serve`) and `qwen2.5` has to be pulled. It already is on this PC.
2. Double-click `run.bat`, or from this folder:

```
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8787
```

3. Open http://127.0.0.1:8787

## What a job does

1. Download the video, or record a live link for the number of minutes you set.
2. For a live link, recording starts from the moment you click, unless you check "from the beginning".
3. Transcribe with word timestamps.
4. Score sections and pick 15–60s moments.
5. Reframe to 9:16. One face is tracked, two faces are stacked, a small corner facecam keeps the screen on top and the face below.
6. Burn captions and a short hook.

Change the start, end, title, or hook on a finished clip and re-render it.

Long videos are cut to **Max minutes** (default 30) so a multi-hour VOD does not fill the disk. Raise that field if you want more.

Clips need speech. A silent gameplay capture has no transcript to cut on.
