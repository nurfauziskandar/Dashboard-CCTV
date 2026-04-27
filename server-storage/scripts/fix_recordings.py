#!/usr/bin/env python3
"""Repair existing MP4 recordings for browser playback.

Re-encodes (or remuxes) every .mp4 under RECORDINGS_DIR to ensure:
  - H.264 Main profile, level 3.1
  - pix_fmt yuv420p (required by Firefox/Chrome)
  - moov atom at start (faststart)

Usage:
    python3 scripts/fix_recordings.py            # check + fix all
    python3 scripts/fix_recordings.py --dry-run  # report only, no changes
    python3 scripts/fix_recordings.py --reencode # force full re-encode
"""

import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config import Config


def probe(path):
    """Return ffprobe info or None."""
    try:
        out = subprocess.check_output(
            ['ffprobe', '-v', 'error', '-print_format', 'json',
             '-show_streams', '-show_format', path],
            text=True, timeout=30, stderr=subprocess.DEVNULL,
        )
        return json.loads(out)
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return None


def needs_fix(info):
    """Return (needs_fix, reason)."""
    if info is None:
        return True, 'unreadable'
    streams = info.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video'), None)
    if video is None:
        return True, 'no video stream'
    if video.get('codec_name') != 'h264':
        return True, f'codec={video.get("codec_name")}'
    pix = video.get('pix_fmt')
    if pix not in ('yuv420p', 'yuvj420p'):
        return True, f'pix_fmt={pix}'
    profile = (video.get('profile') or '').lower()
    if 'high' in profile and '10' in profile:
        return True, f'profile={profile}'
    fmt = info.get('format', {})
    tags = fmt.get('tags', {})
    # faststart check via -movflags is hard via ffprobe; rely on format
    # Major brand should be isom/mp42 for browser-friendly MP4
    major_brand = tags.get('major_brand', '')
    if major_brand and major_brand not in ('isom', 'mp42', 'M4V ', 'mp41'):
        return True, f'major_brand={major_brand}'
    return False, 'ok'


def remux(src, dst):
    """Stream copy with faststart. Fast — no re-encoding."""
    return subprocess.run(
        ['ffmpeg', '-y', '-loglevel', 'error',
         '-i', src, '-c', 'copy',
         '-movflags', '+faststart', dst],
        timeout=300,
    ).returncode == 0


def reencode(src, dst):
    """Full re-encode to browser-compatible H.264 main + yuv420p + faststart."""
    return subprocess.run(
        ['ffmpeg', '-y', '-loglevel', 'error',
         '-i', src,
         '-map', '0:v:0',
         '-c:v', 'libx264',
         '-preset', 'veryfast',
         '-profile:v', 'main', '-level', '3.1',
         '-pix_fmt', 'yuv420p',
         '-crf', '23',
         '-movflags', '+faststart',
         dst],
        timeout=600,
    ).returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='Report only')
    ap.add_argument('--reencode', action='store_true', help='Force full re-encode (slower, fixes more)')
    ap.add_argument('--dir', default=Config.RECORDINGS_DIR, help='Recordings directory')
    args = ap.parse_args()

    if not os.path.isdir(args.dir):
        print(f'No recordings directory at {args.dir}')
        return 1

    total = fixed = skipped = failed = 0
    for root, _, files in os.walk(args.dir):
        for f in files:
            if not f.endswith('.mp4'):
                continue
            total += 1
            path = os.path.join(root, f)
            info = probe(path)
            need, reason = needs_fix(info)
            if not need and not args.reencode:
                skipped += 1
                continue

            print(f'  [{reason}] {path}')
            if args.dry_run:
                continue

            tmp = path + '.fix.mp4'
            ok = reencode(path, tmp) if args.reencode else remux(path, tmp)
            if ok and os.path.getsize(tmp) > 1024:
                os.replace(tmp, path)
                fixed += 1
                print(f'    -> fixed')
            else:
                failed += 1
                print(f'    -> FAILED')
                if os.path.exists(tmp):
                    os.remove(tmp)

    print(f'\nTotal: {total} | OK: {skipped} | Fixed: {fixed} | Failed: {failed}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
