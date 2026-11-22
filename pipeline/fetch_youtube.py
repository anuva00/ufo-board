"""Fetch the transcript of every video on a YouTube channel into
data/youtube/<video id>.json.

YouTube refuses requests from cloud servers, so run this from a home
connection (for example on the NAS):

  docker compose run --rm transcripts
  # or: pip install yt-dlp youtube-transcript-api && python3 pipeline/fetch_youtube.py

Safe to re-run: videos already saved are skipped. It waits a few seconds
between videos so YouTube does not start refusing requests.
"""
import json, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/youtube"
CHANNEL = sys.argv[1] if len(sys.argv) > 1 else "https://www.youtube.com/@JesseMichels/videos"   # American Alchemy


def list_videos():
    """Video ids, titles and durations; falls back to the saved list if YouTube cuts the listing short."""
    saved = OUT / "episodes.txt"
    try:
        out = subprocess.run(["yt-dlp", "--flat-playlist", "--print", "%(id)s|%(title)s|%(duration)s", CHANNEL],
                             capture_output=True, text=True, timeout=600).stdout
    except Exception as e:
        print("listing failed:", e)
        out = ""
    rows = {l.split("|", 1)[0]: l for l in (saved.read_text().splitlines() if saved.exists() else []) if l.strip()}
    rows.update({l.split("|", 1)[0]: l for l in out.splitlines() if "|" in l})
    saved.write_text("\n".join(rows.values()) + "\n")
    return [l.split("|") for l in rows.values()]


def main():
    from youtube_transcript_api import YouTubeTranscriptApi
    OUT.mkdir(parents=True, exist_ok=True)
    api = YouTubeTranscriptApi()
    videos = list_videos()
    print(len(videos), "videos listed")
    done = failed = 0
    for vid, title, *rest in videos:
        path = OUT / f"{vid}.json"
        if path.exists():
            continue
        try:
            segs = [[round(s.start, 1), s.text] for s in api.fetch(vid, languages=["en", "en-US", "en-GB"])]
        except Exception as e:
            failed += 1
            print(f"  no transcript for {vid} ({title[:50]}): {type(e).__name__}")
            if "block" in type(e).__name__.lower():
                print("YouTube is refusing requests from this address; stopping. Try again later or from another connection.")
                break
            time.sleep(3)
            continue
        path.write_text(json.dumps({"id": vid, "title": title, "duration": rest[0] if rest else None,
                                    "url": f"https://www.youtube.com/watch?v={vid}", "channel": "American Alchemy",
                                    "segments": segs}, ensure_ascii=False))
        done += 1
        print(f"  saved {vid}  {len(segs):5d} lines  {title[:60]}", flush=True)
        time.sleep(4)
    print(f"{done} transcripts saved, {failed} unavailable, {len(list(OUT.glob('*.json')))} on disk")


if __name__ == "__main__":
    main()
