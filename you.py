#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube 영상 다운로드 스크립트 (최종 버전)

사용법:
    python you.py "<URL>"                    # 전체 다운로드
    python you.py "<URL>" <시작> <끝>        # 구간 다운로드

예시:
    python you.py "https://youtube.com/watch?v=abc&t=10s"
    python you.py "https://youtube.com/watch?v=abc" 04:21 19:30
    python you.py "https://youtu.be/abc" 1:04:21 1:19:30

기능:
  - 화질: 1080p 30fps → 1080p 다른 fps → 1440p → 720p (4K 이상 제외)
  - 출력: mp4 (영상 + 오디오 병합)
  - 파일명: 영상 제목의 첫 5단어 (공백 기준, 특수문자 제거)
  - 저장 경로: 현재 디렉토리
  - 자막: 수동 우선 · 자동 fallback (한국어 → 영어), srt 형식
  - 구간 다운로드: 키프레임 기반 (효율 우선)
  - 자막 후처리: 직접 파싱하여 정확한 구간 추출
    · *.srt          → 0부터 재시작 (영상 재생용, 자동 동기화)
    · *.original.srt → 원본 시간 유지 (참고·인용용)
  - URL 잘림 감지 및 친절한 경고
"""

import sys
import re
import os
import yt_dlp


# ════════════════════════════════════════════════════════════
# 1. URL 검증 (셸에서 잘렸는지 감지)
# ════════════════════════════════════════════════════════════

def validate_url(url: str) -> None:
    """URL이 셸에서 잘렸는지 감지하고, 의심되면 경고 후 종료"""
    if not re.search(r'(youtube\.com|youtu\.be)', url):
        print("⚠️  YouTube URL이 아닌 것 같습니다.")
        print(f"    입력값: {url}")
        sys.exit(1)

    # watch?v= 형식의 비디오 ID는 정확히 11자
    match = re.search(r'[?&]v=([^&]*)', url)
    if match:
        video_id = match.group(1)
        if len(video_id) != 11:
            _print_truncation_warning(url, f"비디오 ID가 {len(video_id)}자 (정상: 11자)")
            sys.exit(1)

    # youtu.be 단축 URL
    match = re.search(r'youtu\.be/([^?&/]*)', url)
    if match:
        video_id = match.group(1)
        if len(video_id) != 11:
            _print_truncation_warning(url, f"비디오 ID가 {len(video_id)}자 (정상: 11자)")
            sys.exit(1)


def _print_truncation_warning(url: str, reason: str) -> None:
    print("⚠️  URL이 셸에서 잘렸을 가능성이 있습니다.")
    print(f"    감지 사유: {reason}")
    print(f"    입력하신 URL: {url}")
    print()
    print("    해결 방법: URL을 따옴표(\" \")로 감싸서 다시 실행하세요.")
    print()
    print("    예시:")
    print('      python you.py "https://youtube.com/watch?v=abc&t=10s" 04:21 19:30')


# ════════════════════════════════════════════════════════════
# 2. 시간 파싱 및 포맷팅
# ════════════════════════════════════════════════════════════

def parse_time(s: str) -> int:
    """시간 문자열 → 초. 지원: MM:SS, HH:MM:SS, 숫자(초)"""
    s = s.strip()
    if not s:
        raise ValueError("빈 시간 값")
    if s.isdigit():
        return int(s)

    parts = s.split(':')
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        raise ValueError(f"시간 형식이 잘못되었습니다: '{s}'")

    if len(parts) == 2:
        m, sec = parts
        if not (0 <= m and 0 <= sec < 60):
            raise ValueError(f"시간 값 범위 오류: '{s}'")
        return m * 60 + sec
    elif len(parts) == 3:
        h, m, sec = parts
        if not (0 <= h and 0 <= m < 60 and 0 <= sec < 60):
            raise ValueError(f"시간 값 범위 오류: '{s}'")
        return h * 3600 + m * 60 + sec
    else:
        raise ValueError(f"시간 형식이 잘못되었습니다: '{s}' (MM:SS 또는 HH:MM:SS)")


def format_time_for_filename(seconds: int) -> str:
    """파일명용 시간 (MM-SS 또는 HH-MM-SS)"""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:02d}-{m:02d}-{s:02d}"
    return f"{m:02d}-{s:02d}"


def format_srt_time(seconds: float) -> str:
    """초(소수) → SRT 타임스탬프 (HH:MM:SS,mmm)"""
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt_time(ts: str) -> float:
    """SRT 타임스탬프(HH:MM:SS,mmm) → 초(소수)"""
    ts = ts.strip().replace('.', ',')
    m = re.match(r'(\d+):(\d+):(\d+),(\d+)', ts)
    if not m:
        raise ValueError(f"잘못된 SRT 타임스탬프: {ts}")
    h, mi, s, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000.0


# ════════════════════════════════════════════════════════════
# 3. 파일명 처리
# ════════════════════════════════════════════════════════════

def sanitize_filename(name: str) -> str:
    """파일명 금지 문자 제거"""
    name = re.sub(r'[\\/:*?"<>|]', '', name)
    name = re.sub(r'\s+', ' ', name)
    return name.strip()


def get_first_5_words(title: str) -> str:
    """제목의 첫 5단어 (공백 기준)"""
    words = title.split()
    first5 = ' '.join(words[:5])
    return sanitize_filename(first5) or 'video'


# ════════════════════════════════════════════════════════════
# 4. 자막 언어 선택
# ════════════════════════════════════════════════════════════

def pick_subtitle_lang(info: dict) -> tuple:
    """수동 ko → 수동 en → 자동 ko → 자동 en → None"""
    manual = info.get('subtitles') or {}
    auto = info.get('automatic_captions') or {}

    for lang in ('ko', 'en'):
        if lang in manual:
            return (lang, False)
    for lang in ('ko', 'en'):
        if lang in auto:
            return (lang, True)
    return (None, None)


# ════════════════════════════════════════════════════════════
# 5. SRT 파싱 및 후처리 (구간 자르기)
# ════════════════════════════════════════════════════════════

def parse_srt_blocks(srt_text: str) -> list:
    """
    SRT 텍스트 → 블록 리스트
    각 블록: {'start': float, 'end': float, 'text': str}
    """
    # 줄바꿈 통일
    srt_text = srt_text.replace('\r\n', '\n').replace('\r', '\n')
    blocks = []

    # 빈 줄로 블록 분리
    raw_blocks = re.split(r'\n\s*\n', srt_text.strip())

    for raw in raw_blocks:
        lines = raw.strip().split('\n')
        if len(lines) < 2:
            continue

        # 첫 줄이 번호면 건너뛰기, 아니면 첫 줄부터 검사
        ts_line_idx = 0
        if re.match(r'^\d+$', lines[0].strip()):
            ts_line_idx = 1

        if ts_line_idx >= len(lines):
            continue

        ts_match = re.match(
            r'(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)',
            lines[ts_line_idx]
        )
        if not ts_match:
            continue

        try:
            start = parse_srt_time(ts_match.group(1))
            end = parse_srt_time(ts_match.group(2))
        except ValueError:
            continue

        text = '\n'.join(lines[ts_line_idx + 1:]).strip()
        if text:
            blocks.append({'start': start, 'end': end, 'text': text})

    return blocks


def remove_consecutive_duplicates(blocks: list) -> list:
    """
    자동 생성 자막의 누적 중복 제거.
    이전 블록의 텍스트가 현재 블록 텍스트의 접두사면 이전을 버림.
    """
    if not blocks:
        return blocks

    cleaned = []
    for block in blocks:
        if cleaned:
            prev_text = cleaned[-1]['text'].strip()
            curr_text = block['text'].strip()
            # 현재 텍스트가 이전을 완전히 포함하면 이전 제거 (누적형)
            if curr_text.startswith(prev_text) and curr_text != prev_text:
                cleaned.pop()
        cleaned.append(block)

    return cleaned


def filter_blocks_by_range(blocks: list, start_sec: int, end_sec: int) -> list:
    """지정한 구간과 겹치는 블록만 반환 (경계는 클램핑)"""
    result = []
    for b in blocks:
        # 구간과 전혀 겹치지 않으면 제외
        if b['end'] <= start_sec or b['start'] >= end_sec:
            continue
        # 경계 클램핑
        new_start = max(b['start'], start_sec)
        new_end = min(b['end'], end_sec)
        if new_end <= new_start:
            continue
        result.append({'start': new_start, 'end': new_end, 'text': b['text']})
    return result


def write_srt(blocks: list, output_path: str, time_offset: float = 0.0) -> None:
    """
    블록 리스트를 SRT 파일로 저장.
    time_offset: 각 블록의 시간에서 뺄 값 (0이면 원본 시간 유지)
    """
    lines = []
    for i, b in enumerate(blocks, 1):
        start = b['start'] - time_offset
        end = b['end'] - time_offset
        lines.append(str(i))
        lines.append(f"{format_srt_time(start)} --> {format_srt_time(end)}")
        lines.append(b['text'])
        lines.append('')  # 블록 구분 빈 줄

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


def process_subtitle(srt_path: str, start_sec: int, end_sec: int,
                      is_auto: bool, base_name: str, lang: str) -> None:
    """
    다운로드된 SRT 파일을 후처리하여 두 가지 버전 생성:
      1) base_name.<lang>[.auto].srt          - 0부터 재시작 (재생용)
      2) base_name.<lang>[.auto].original.srt - 원본 시간 유지 (참고용)
    """
    if not os.path.exists(srt_path):
        print(f"      ⚠️  자막 파일을 찾을 수 없음: {srt_path}")
        return

    # SRT 읽기
    with open(srt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    blocks = parse_srt_blocks(content)
    if not blocks:
        print(f"      ⚠️  자막 블록을 파싱할 수 없음")
        return

    # 자동 생성 자막은 누적 중복 제거
    if is_auto:
        before = len(blocks)
        blocks = remove_consecutive_duplicates(blocks)
        if before != len(blocks):
            print(f"      자동 자막 중복 제거: {before} → {len(blocks)}블록")

    # 구간 필터링
    filtered = filter_blocks_by_range(blocks, start_sec, end_sec)
    if not filtered:
        print(f"      ⚠️  지정 구간에 자막이 없습니다.")
        # 원본은 삭제
        try:
            os.remove(srt_path)
        except OSError:
            pass
        return

    # 파일명 결정
    auto_suffix = '.auto' if is_auto else ''
    sync_path = f"{base_name}.{lang}{auto_suffix}.srt"
    orig_path = f"{base_name}.{lang}{auto_suffix}.original.srt"

    # 1) 0부터 재시작 (영상 재생용)
    write_srt(filtered, sync_path, time_offset=start_sec)

    # 2) 원본 시간 유지 (참고용)
    write_srt(filtered, orig_path, time_offset=0)

    # 원본 임시 파일이 다른 이름이면 삭제
    if os.path.abspath(srt_path) != os.path.abspath(sync_path):
        try:
            os.remove(srt_path)
        except OSError:
            pass

    print(f"      자막 생성: {sync_path}  (영상 재생용, 0부터 시작)")
    print(f"      자막 생성: {orig_path}  (원본 시간 유지, 참고용)")


# ════════════════════════════════════════════════════════════
# 6. 메인 다운로드 로직
# ════════════════════════════════════════════════════════════

def download(url: str, start_sec: int = None, end_sec: int = None):
    is_clip = start_sec is not None and end_sec is not None

    # ── 1단계: 메타데이터 추출 ──
    print("[1/3] 영상 정보 분석 중...")
    with yt_dlp.YoutubeDL({'quiet': True, 'skip_download': True}) as ydl:
        info = ydl.extract_info(url, download=False)

    title = info.get('title', 'video')
    duration = info.get('duration', 0)
    sub_lang, is_auto = pick_subtitle_lang(info)

    # 영상 길이 검증
    if is_clip and duration and end_sec > duration:
        print(f"⚠️  지정한 끝 시간({end_sec}초)이 영상 길이({duration}초)를 초과합니다.")
        sys.exit(1)

    # ── 파일명 결정 ──
    base = get_first_5_words(title)
    if is_clip:
        clip_tag = f"[{format_time_for_filename(start_sec)}~{format_time_for_filename(end_sec)}]"
        filename_base = f"{base} {clip_tag}"
    else:
        filename_base = base

    # ── 정보 출력 ──
    print(f"      원제목  : {title}")
    print(f"      파일명  : {filename_base}.mp4")
    if is_clip:
        print(f"      구간    : {start_sec//60:02d}:{start_sec%60:02d} ~ "
              f"{end_sec//60:02d}:{end_sec%60:02d} ({end_sec - start_sec}초)")
        print(f"                ※ 키프레임 기반: 영상은 ±몇 초 오차 가능")
        print(f"                ※ 자막은 직접 파싱하여 정확히 구간만 추출")
    if sub_lang:
        kind = "자동 생성" if is_auto else "수동"
        print(f"      자막    : {sub_lang} ({kind})")
    else:
        print(f"      자막    : 없음 (영상만 다운로드)")

    # ── 2단계: 화질 우선순위 ──
    fmt = (
        'bestvideo[height=1080][fps<=30]+bestaudio/'
        'bestvideo[height=1080]+bestaudio/'
        'bestvideo[height=1440]+bestaudio/'
        'bestvideo[height<=720]+bestaudio/'
        'best[height<=1440]'
    )

    # ── 3단계: yt-dlp 옵션 ──
    ydl_opts = {
        'format': fmt,
        'merge_output_format': 'mp4',
        'outtmpl': f'{filename_base}.%(ext)s',
        'noplaylist': True,
        'quiet': False,
        'no_warnings': False,
        'postprocessors': [
            {'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'},
        ],
    }

    if is_clip:
        ydl_opts['download_ranges'] = yt_dlp.utils.download_range_func(
            None, [(start_sec, end_sec)]
        )
        ydl_opts['force_keyframes_at_cuts'] = True

    if sub_lang:
        ydl_opts.update({
            'writesubtitles': not is_auto,
            'writeautomaticsub': is_auto,
            'subtitleslangs': [sub_lang],
            'subtitlesformat': 'srt/best',
        })
        # 자막은 항상 전체로 받도록 (구간 자르기는 우리가 직접 처리)
        ydl_opts['postprocessors'].append({
            'key': 'FFmpegSubtitlesConvertor',
            'format': 'srt',
        })

    # ── 4단계: 다운로드 실행 ──
    print("[2/3] 다운로드 시작...")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    # ── 5단계: 자막 후처리 ──
    print("[3/3] 후처리 중...")

    if sub_lang:
        # yt-dlp가 생성한 자막 파일 찾기
        possible_paths = [
            f'{filename_base}.{sub_lang}.srt',
            f'{filename_base}.{sub_lang}.vtt',
        ]
        srt_path = None
        for p in possible_paths:
            if os.path.exists(p):
                srt_path = p
                break

        if srt_path:
            if is_clip:
                # 구간 다운로드: 두 가지 버전 생성
                process_subtitle(
                    srt_path,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    is_auto=is_auto,
                    base_name=filename_base,
                    lang=sub_lang,
                )
            else:
                # 전체 다운로드: 자동 자막이면 .auto 추가, 중복 제거
                with open(srt_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                blocks = parse_srt_blocks(content)

                if is_auto and blocks:
                    before = len(blocks)
                    blocks = remove_consecutive_duplicates(blocks)
                    if before != len(blocks):
                        print(f"      자동 자막 중복 제거: {before} → {len(blocks)}블록")

                final_path = f'{filename_base}.{sub_lang}{".auto" if is_auto else ""}.srt'
                write_srt(blocks, final_path, time_offset=0)

                if os.path.abspath(srt_path) != os.path.abspath(final_path):
                    try:
                        os.remove(srt_path)
                    except OSError:
                        pass
                print(f"      자막 생성: {final_path}")
        else:
            print(f"      ⚠️  자막 파일을 찾을 수 없습니다 (다운로드 실패).")

    print(f"\n[완료] {filename_base}.mp4")


# ════════════════════════════════════════════════════════════
# 7. 진입점
# ════════════════════════════════════════════════════════════

def print_usage():
    print("사용법:")
    print('  python you.py "<URL>"                    # 전체 다운로드')
    print('  python you.py "<URL>" <시작> <끝>        # 구간 다운로드')
    print()
    print("예시:")
    print('  python you.py "https://youtu.be/abc123"')
    print('  python you.py "https://youtube.com/watch?v=abc&t=10s" 04:21 19:30')
    print('  python you.py "https://youtu.be/abc123" 1:04:21 1:19:30')
    print()
    print("※ URL은 반드시 따옴표로 감싸주세요 (& 문자가 있을 경우 필수).")


if __name__ == '__main__':
    argc = len(sys.argv) - 1

    if argc == 0:
        print_usage()
        sys.exit(1)
    elif argc == 2:
        print("⚠️  인자 개수가 잘못되었습니다 (시작 또는 끝 시간이 빠짐).")
        print()
        print_usage()
        sys.exit(1)
    elif argc not in (1, 3):
        print(f"⚠️  인자 개수가 잘못되었습니다 ({argc}개 입력됨, 1개 또는 3개 필요).")
        print()
        print_usage()
        sys.exit(1)

    url = sys.argv[1].strip()
    validate_url(url)

    start_sec = end_sec = None
    if argc == 3:
        try:
            start_sec = parse_time(sys.argv[2])
            end_sec = parse_time(sys.argv[3])
        except ValueError as e:
            print(f"⚠️  시간 형식 오류: {e}")
            print("    지원 형식: MM:SS, HH:MM:SS, 또는 초 숫자")
            sys.exit(1)

        if start_sec >= end_sec:
            print(f"⚠️  시작 시간({sys.argv[2]})이 끝 시간({sys.argv[3]})보다 같거나 늦습니다.")
            sys.exit(1)

    try:
        download(url, start_sec, end_sec)
    except yt_dlp.utils.DownloadError as e:
        print(f"\n[오류] 다운로드 실패: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[중단] 사용자가 다운로드를 취소했습니다.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"\n[오류] 예기치 못한 오류: {e}", file=sys.stderr)
        sys.exit(1)
