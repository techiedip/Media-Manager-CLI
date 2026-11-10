"""Read stream details with ffprobe for <fileinfo><streamdetails>."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)
VCODEC = {"hevc": "h265", "h264": "h264", "avc": "h264", "mpeg4": "mpeg4", "av1": "av1",
          "vp9": "vp9", "mpeg2video": "mpeg2", "vc1": "vc1"}
ACODEC = {"eac3": "eac3", "ac3": "ac3", "dts": "dts", "aac": "aac", "truehd": "truehd",
          "flac": "flac", "mp3": "mp3", "opus": "opus", "vorbis": "vorbis", "pcm_s16le": "pcm"}
LANG2 = {"eng": "en", "spa": "es", "fre": "fr", "fra": "fr", "ger": "de", "deu": "de", "ita": "it",
         "jpn": "ja", "kor": "ko", "chi": "zh", "zho": "zh", "hin": "hi", "rus": "ru", "por": "pt"}


def probe(path: Path) -> dict | None:
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", str(path)],
            capture_output=True, timeout=120, check=False,
        )
        data = json.loads(out.stdout or b"{}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        log.warning("ffprobe failed for %s: %s", path.name, e)
        return None
    if not data.get("streams"):
        return None

    fmt = data.get("format") or {}
    duration = float(fmt.get("duration") or 0)
    info: dict = {"video": [], "audio": [], "subtitle": [], "duration": duration}
    for s in data["streams"]:
        t = s.get("codec_type")
        lang = (s.get("tags") or {}).get("language", "")
        if t == "video" and (s.get("disposition") or {}).get("attached_pic"):
            continue
        if t == "video":
            w, h = s.get("width") or 0, s.get("height") or 0
            fr = s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1"
            try:
                num, den = fr.split("/")
                fps = round(float(num) / float(den), 3) if float(den) else 0
            except ValueError:
                fps = 0
            dar = s.get("display_aspect_ratio", "")
            try:
                a, b = dar.split(":")
                aspect = round(float(a) / float(b), 7)
            except (ValueError, ZeroDivisionError):
                aspect = round(w / h, 7) if h else 0
            hdr = s.get("color_transfer") in ("smpte2084", "arib-std-b67")
            info["video"].append({
                "codec": VCODEC.get(s.get("codec_name", ""), s.get("codec_name", "")),
                "aspect": aspect, "width": w, "height": h,
                "framerate": int(fps) if fps == int(fps) else fps,
                "durationinseconds": int(duration),
                "hdrtype": "hdr10" if hdr else "",
                "scantype": "Interlaced" if s.get("field_order") in ("tt", "bb", "tb", "bt") else "Progressive",
            })
        elif t == "audio":
            info["audio"].append({
                "codec": ACODEC.get(s.get("codec_name", ""), s.get("codec_name", "")),
                "language": LANG2.get(lang, lang), "channels": s.get("channels") or 0,
            })
        elif t == "subtitle":
            info["subtitle"].append({"language": LANG2.get(lang, lang)})
    return info
