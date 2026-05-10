#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube 영상 다운로드 스크립트
사용법: python you.py <YouTube URL>

조건:
  - 화질: 1080p 30fps → 1080p 다른 fps → 1440p → 720p (4K 이상 제외)
  - 출력: mp4 (영상 + 오디오 병합)
  - 파일명: 영상 제목의 첫 5단어 (공백 기준, 특수문자 제거)
  - 저장 경로: 현재 디렉토리
  - 자막: 수동 우선 · 자동 fallback (한국어 → 영어), srt 형식
          자동 자막은 파일명에 .auto 표시
"""

import sys
import re
import os
import yt_dlp


# ────────────────────────────────────────────────────────────
# 유틸리티 함수
# ────────────────────────────────────────────────────────────

def sanitize_filename(name: str) -> str:
    """파일명에 사용할 수 없는 문자 제거 (Windows/macOS/Linux 호환)"""
    name = re.sub(r'[\\/:*?"<>|]', '', name)
    name = re.sub(r'\s+', ' ', name)  # 연속 공백 정리
    return name.strip()


def get_first_5_words(title: str) -> str:
    """제목에서 공백 기준 첫 5단어 추출"""
    words = title.split()
    first5 = ' '.join(words[:5])
    cleaned = sanitize_filename(first5)
    return cleaned or 'video'


def pick_subtitle_lang(info: dict) -> tuple:
    """
    자막 우선순위에 따라 (언어, 자동여부)를 결정.
    1) 수동 ko → 2) 수동 en → 3) 자동 ko → 4) 자동 en → 5) None
    반환: (lang_code, is_auto) 또는 (None, None)
    """
    manual = info.get('subtitles') or {}
    auto = info.get('automatic_captions') or {}

    # 수동 자막 우선
    for lang in ('ko', 'en'):
        if lang in manual:
            return (lang, False)

    # 자동 자막 fallback
    for lang in ('ko', 'en'):
        if lang in auto:
            return (lang, True)

    return (None, None)


# ────────────────────────────────────────────────────────────
# 메인 다운로드 로직
# ────────────────────────────────────────────────────────────

def download(url: str):
    # ── 1단계: 메타데이터 추출 (제목, 자막 목록 확인) ──
    print("[1/3] 영상 정보 분석 중...")
    with yt_dlp.YoutubeDL({'quiet': True, 'skip_download': True}) as ydl:
        info = ydl.extract_info(url, download=False)

    title = info.get('title', 'video')
    filename_base = get_first_5_words(title)
    sub_lang, is_auto = pick_subtitle_lang(info)

    print(f"      원제목  : {title}")
    print(f"      파일명  : {filename_base}.mp4")
    if sub_lang:
        kind = "자동 생성" if is_auto else "수동"
        print(f"      자막    : {sub_lang} ({kind})")
    else:
        print(f"      자막    : 없음 (영상만 다운로드)")

    # ── 2단계: 화질 우선순위 정의 ──
    # 1080p 30fps → 1080p 아무 fps → 1440p → 720p
    # (4K 이상 제외를 위해 height<=1440 제한)
    fmt = (
        'bestvideo[height=1080][fps<=30]+bestaudio/'
        'bestvideo[height=1080]+bestaudio/'
        'bestvideo[height=1440]+bestaudio/'
        'bestvideo[height<=720]+bestaudio/'
        'best[height<=1440]'
    )

    # ── 3단계: yt-dlp 옵션 구성 ──
    ydl_opts = {
        'format': fmt,
        'merge_output_format': 'mp4',
        'outtmpl': f'{filename_base}.%(ext)s',
        'noplaylist': True,
        'quiet': False,
        'no_warnings': False,
        'postprocessors': [
            {
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            },
        ],
    }

    # 자막 옵션 추가 (있을 때만)
    if sub_lang:
        ydl_opts.update({
            'writesubtitles': not is_auto,
            'writeautomaticsub': is_auto,
            'subtitleslangs': [sub_lang],
            'subtitlesformat': 'srt/best',
        })
        # 자막 변환 후처리 (vtt → srt)
        ydl_opts['postprocessors'].append({
            'key': 'FFmpegSubtitlesConvertor',
            'format': 'srt',
        })

    # ── 4단계: 다운로드 실행 ──
    print("[2/3] 다운로드 시작...")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    # ── 5단계: 자동 자막 파일명에 .auto 추가 ──
    print("[3/3] 후처리 중...")
    if sub_lang and is_auto:
        original = f'{filename_base}.{sub_lang}.srt'
        renamed = f'{filename_base}.{sub_lang}.auto.srt'
        if os.path.exists(original):
            # 이미 같은 이름이 있으면 덮어쓰기
            if os.path.exists(renamed):
                os.remove(renamed)
            os.rename(original, renamed)
            print(f"      자막 파일명 변경: {renamed}")

    print(f"\n[완료] {filename_base}.mp4")


# ────────────────────────────────────────────────────────────
# 진입점
# ────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("사용법: python you.py <YouTube URL>")
        sys.exit(1)

    url = sys.argv[1].strip()
    try:
        download(url)
    except yt_dlp.utils.DownloadError as e:
        print(f"\n[오류] 다운로드 실패: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[중단] 사용자가 다운로드를 취소했습니다.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"\n[오류] 예기치 못한 오류: {e}", file=sys.stderr)
        sys.exit(1)
