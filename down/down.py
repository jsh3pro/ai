#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
down.py - YouTube 영상 다운로더 (시간 구간 지정 가능)

사용법:
    python down.py <URL>
    python down.py <URL> <시작시간> <종료시간>

예시:
    python down.py https://youtu.be/BUjv-FRyhA4
    python down.py https://youtu.be/BUjv-FRyhA4 00:24:01 00:46:07
"""

import os
import sys
import argparse
import subprocess
import shutil
import tempfile
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    print("[오류] yt-dlp 모듈이 설치되어 있지 않습니다.")
    print("       python -m pip install -U yt-dlp")
    sys.exit(1)


def hhmmss_to_seconds(t: str) -> float:
    parts = [float(p) for p in t.strip().split(":")]
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h, m, s = 0.0, parts[0], parts[1]
    elif len(parts) == 1:
        h, m, s = 0.0, 0.0, parts[0]
    else:
        raise ValueError(f"시간 형식이 잘못되었습니다: {t}")
    return h * 3600 + m * 60 + s


def seconds_to_hhmmss(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec//3600:02d}:{(sec%3600)//60:02d}:{sec%60:02d}"


def find_ffmpeg():
    ff = shutil.which("ffmpeg")
    if not ff:
        print("[오류] ffmpeg를 찾을 수 없습니다. PATH에 ffmpeg를 추가하세요.")
        sys.exit(1)
    return ff


def get_cookie_opts(args):
    """쿠키 옵션 dict 반환."""
    opts = {}
    cookie_file = Path(args.cookies) if args.cookies else Path("cookies.txt")
    if cookie_file.exists():
        opts["cookiefile"] = str(cookie_file.resolve())
        print(f"[정보] 쿠키 파일    : {cookie_file.resolve()}")
    else:
        opts["cookiesfrombrowser"] = (args.browser,)
        print(f"[정보] 쿠키 소스    : {args.browser} (브라우저는 완전히 종료된 상태여야 합니다)")
    return opts


def extract_info_and_urls(url, cookie_opts, fmt):
    """영상 메타데이터와 video/audio 스트림 URL 추출."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "format": fmt,
        **cookie_opts,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    # 선택된 포맷 정보 (requested_formats가 있으면 분리 스트림)
    if "requested_formats" in info:
        v_url = None
        a_url = None
        for f in info["requested_formats"]:
            if f.get("vcodec") and f["vcodec"] != "none":
                v_url = f["url"]
            if f.get("acodec") and f["acodec"] != "none" and (not f.get("vcodec") or f["vcodec"] == "none"):
                a_url = f["url"]
        return info, v_url, a_url
    else:
        # 단일 포맷
        return info, info["url"], None


def download_clip_with_ffmpeg(ffmpeg, v_url, a_url, start_sec, end_sec,
                              out_path, http_headers=None):
    """ffmpeg로 구간만 직접 받아 mp4로 저장 (재인코딩 없이 stream copy)."""
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-stats"]

    # 입력 전 -ss/-to (정확도는 약간 떨어지지만 빠르고 서버에서 구간만 받음)
    # YouTube는 HTTP Range를 지원하므로 ffmpeg가 필요한 만큼만 가져옴
    def add_input(u):
        if http_headers:
            hdr = "".join(f"{k}: {v}\r\n" for k, v in http_headers.items())
            cmd.extend(["-headers", hdr])
        cmd.extend(["-ss", seconds_to_hhmmss(start_sec)])
        if end_sec is not None:
            cmd.extend(["-to", seconds_to_hhmmss(end_sec)])
        cmd.extend(["-i", u])

    add_input(v_url)
    if a_url:
        add_input(a_url)

    # 비디오는 가능하면 copy, 안 되면 자동 재인코딩 폴백은 별도 처리
    if a_url:
        cmd.extend(["-map", "0:v:0", "-map", "1:a:0"])
    cmd.extend(["-c", "copy", "-movflags", "+faststart", out_path])

    print(f"[정보] ffmpeg 실행 (stream copy)...")
    r = subprocess.run(cmd)
    return r.returncode


def download_clip_reencode(ffmpeg, v_url, a_url, start_sec, end_sec,
                           out_path, http_headers=None):
    """stream copy가 실패한 경우 재인코딩 폴백."""
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-stats"]

    def add_input(u):
        if http_headers:
            hdr = "".join(f"{k}: {v}\r\n" for k, v in http_headers.items())
            cmd.extend(["-headers", hdr])
        cmd.extend(["-ss", seconds_to_hhmmss(start_sec)])
        if end_sec is not None:
            cmd.extend(["-to", seconds_to_hhmmss(end_sec)])
        cmd.extend(["-i", u])

    add_input(v_url)
    if a_url:
        add_input(a_url)

    if a_url:
        cmd.extend(["-map", "0:v:0", "-map", "1:a:0"])
    cmd.extend([
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        out_path,
    ])

    print(f"[정보] ffmpeg 실행 (재인코딩 폴백)...")
    r = subprocess.run(cmd)
    return r.returncode


def download_subtitles(url, cookie_opts, outdir, base_name, langs=("ko", "en")):
    """자막을 별도로 다운로드 (구간 클립과 함께 사용)."""
    ydl_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": list(langs),
        "subtitlesformat": "vtt",
        "outtmpl": os.path.join(outdir, base_name + ".%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        **cookie_opts,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        print(f"[경고] 자막 다운로드 실패(무시): {e}")


def main():
    parser = argparse.ArgumentParser(description="YouTube 영상 다운로더 (구간 지정)")
    parser.add_argument("url")
    parser.add_argument("start", nargs="?", default=None, help="시작 (HH:MM:SS)")
    parser.add_argument("end", nargs="?", default=None, help="종료 (HH:MM:SS)")
    parser.add_argument("-o", "--outdir", default=".")
    parser.add_argument("-f", "--format",
                        default="bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]/bv*+ba/best",
                        help="yt-dlp format (기본은 h264+aac 우선)")
    parser.add_argument("-b", "--browser", default="chrome",
                        choices=["chrome", "firefox", "edge", "brave",
                                 "opera", "vivaldi", "safari", "chromium"])
    parser.add_argument("-c", "--cookies", default=None)
    parser.add_argument("--no-subs", dest="subs", action="store_false")
    parser.add_argument("--reencode", action="store_true",
                        help="stream copy 대신 처음부터 재인코딩")
    parser.set_defaults(subs=True)
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    ffmpeg = find_ffmpeg()

    start_sec = hhmmss_to_seconds(args.start) if args.start else None
    end_sec = hhmmss_to_seconds(args.end) if args.end else None

    print(f"[정보] URL 분석 중: {args.url}")
    if start_sec is not None or end_sec is not None:
        print(f"[정보] 구간         : {args.start or '처음'} ~ {args.end or '끝'}")

    cookie_opts = get_cookie_opts(args)

    # ---- 메타데이터 + 스트림 URL 추출 ----
    try:
        info, v_url, a_url = extract_info_and_urls(args.url, cookie_opts, args.format)
    except Exception as e:
        print(f"[오류] 영상 정보를 가져올 수 없습니다: {e}")
        sys.exit(2)

    title = info.get("title", "video")
    vid = info.get("id", "")
    duration = info.get("duration", 0)
    print(f"[정보] 제목         : {title}")
    print(f"[정보] 전체 길이    : {int(duration//60)}분 {int(duration%60)}초")

    # 안전한 파일명
    safe_title = "".join(c for c in title if c not in '<>:"/\\|?*').strip()
    base_name = f"{safe_title} [{vid}]"
    if start_sec is not None or end_sec is not None:
        s_tag = seconds_to_hhmmss(start_sec or 0).replace(":", "")
        e_tag = seconds_to_hhmmss(end_sec or duration).replace(":", "")
        base_name += f"_{s_tag}-{e_tag}"

    out_path = os.path.join(args.outdir, base_name + ".mp4")

    # 구간 지정이 없으면 전체 다운로드 (기존 yt-dlp 방식)
    if start_sec is None and end_sec is None:
        print("[정보] 구간 지정 없음 → 전체 다운로드")
        ydl_opts = {
            "outtmpl": out_path,
            "format": args.format,
            "merge_output_format": "mp4",
            "noplaylist": True,
            **cookie_opts,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([args.url])
        print(f"[완료] {out_path}")
        return

    # 종료 시각이 전체 길이를 넘으면 보정
    if end_sec is not None and end_sec > duration:
        end_sec = duration

    # HTTP 헤더 (User-Agent 등) 전달 — 일부 서버는 필요
    http_headers = info.get("http_headers") or {}

    # ---- ffmpeg로 구간 다운로드 ----
    if not args.reencode:
        rc = download_clip_with_ffmpeg(ffmpeg, v_url, a_url,
                                       start_sec, end_sec, out_path,
                                       http_headers=http_headers)
        if rc != 0:
            print("[경고] stream copy 실패 → 재인코딩으로 재시도")
            rc = download_clip_reencode(ffmpeg, v_url, a_url,
                                        start_sec, end_sec, out_path,
                                        http_headers=http_headers)
    else:
        rc = download_clip_reencode(ffmpeg, v_url, a_url,
                                    start_sec, end_sec, out_path,
                                    http_headers=http_headers)

    if rc != 0:
        print(f"[오류] ffmpeg 종료 코드 {rc}")
        sys.exit(rc)

    # ---- 자막 다운로드 후 임베드 ----
    if args.subs:
        tmpdir = tempfile.mkdtemp(prefix="ytsubs_")
        try:
            sub_base = "subs"
            download_subtitles(args.url, cookie_opts, tmpdir, sub_base)
            # 받아진 vtt 파일들 찾기
            vtts = list(Path(tmpdir).glob(sub_base + "*.vtt"))
            if vtts:
                # 구간에 맞춰 자막도 잘라서 임베드
                merged = out_path + ".tmp.mp4"
                cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
                       "-i", out_path]
                for v in vtts:
                    cmd.extend(["-itsoffset", f"-{start_sec}", "-i", str(v)])
                cmd.extend(["-map", "0:v", "-map", "0:a"])
                for i, v in enumerate(vtts):
                    cmd.extend(["-map", f"{i+1}:0"])
                cmd.extend(["-c:v", "copy", "-c:a", "copy",
                            "-c:s", "mov_text"])
                for i, v in enumerate(vtts):
                    # 언어 태그 추출 (subs.ko.vtt → ko)
                    parts = v.stem.split(".")
                    lang = parts[-1] if len(parts) > 1 else "und"
                    cmd.extend([f"-metadata:s:s:{i}", f"language={lang}"])
                cmd.append(merged)
                r = subprocess.run(cmd)
                if r.returncode == 0:
                    os.replace(merged, out_path)
                    print("[정보] 자막 임베드 완료")
                else:
                    print("[경고] 자막 임베드 실패(무시)")
                    if os.path.exists(merged):
                        os.remove(merged)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"[완료] {out_path}")


if __name__ == "__main__":
    main()
