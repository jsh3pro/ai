#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vph.py - YouTube 비디오 리스트 텍스트를 파싱해 CSV로 변환

사용법:
    python vph.py input.txt output.csv
"""

import sys
import re
import csv
from datetime import datetime, timedelta

# 기준일 (요청사항: 2026-06-10)
BASE_DATE = datetime(2026, 6, 10)

# 정규식 패턴
VPH_RE = re.compile(r'^\s*([\d.]+\s*[천만억]?|[\d.]+)\s*VPH\s*$')
VIEWS_RE = re.compile(r'^\s*([\d.]+\s*[천만억]?)\s*views?\s*•\s*(.+?)\s*$')
MULTIPLIER_RE = re.compile(r'^\s*(>?\s*[\d.]+\s*x)\s*$', re.IGNORECASE)
# 재생시간 패턴: mm:ss 또는 h:mm:ss (예: 41:22, 1:23:45, 4:01)
DURATION_RE = re.compile(r'^\s*\d{1,2}:\d{2}(:\d{2})?\s*$')


def parse_relative_time(text: str, base: datetime = BASE_DATE) -> str:
    """
    'X hours ago', 'X days ago' 등을 기준일(2026-06-10) 기반 YYYY-MM-DD로 변환.
    - hour/minute/second 단위는 '오늘'로 간주 → 기준일 그대로
    - day/week/month/year 단위만 날짜 차감
    파싱 실패 시 원문 반환.
    """
    text = text.strip().lower()
    m = re.match(r'(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago', text)
    if not m:
        return text

    value = int(m.group(1))
    unit = m.group(2)

    if unit in ('second', 'minute', 'hour'):
        # 오늘 올린 영상으로 간주
        return base.strftime('%Y-%m-%d')

    if unit == 'day':
        delta = timedelta(days=value)
    elif unit == 'week':
        delta = timedelta(weeks=value)
    elif unit == 'month':
        delta = timedelta(days=value * 30)
    elif unit == 'year':
        delta = timedelta(days=value * 365)
    else:
        return text

    return (base - delta).strftime('%Y-%m-%d')


def to_int_number(text: str) -> str:
    """
    한국식 단위가 섞인 숫자 문자열을 천 단위 콤마 포함 정수 문자열로 변환.
    예) '9.3천' -> '9,300', '1.2만' -> '12,000', '235.3' -> '235',
        '19만'  -> '190,000', '1.5억' -> '150,000,000'
    파싱 실패 시 원문 반환.
    """
    if text is None:
        return ''
    s = text.strip().replace(',', '').replace('+', '').replace(' ', '')
    if not s:
        return ''

    # 단위 매핑 (큰 단위부터)
    unit_map = [('억', 100_000_000), ('만', 10_000), ('천', 1_000)]

    multiplier = 1
    num_part = s
    for unit, mult in unit_map:
        if s.endswith(unit):
            multiplier = mult
            num_part = s[:-len(unit)]
            break

    try:
        value = float(num_part) * multiplier
        return f"{int(round(value)):,}"
    except ValueError:
        return text


def is_vph_line(line):     return bool(VPH_RE.match(line))
def is_views_line(line):   return bool(VIEWS_RE.match(line))
def is_multiplier_line(l): return bool(MULTIPLIER_RE.match(l))
def is_duration_line(l):   return bool(DURATION_RE.match(l))


def extract_vph(line):
    m = VPH_RE.match(line)
    return m.group(1).strip() if m else ''


def extract_views_and_time(line):
    m = VIEWS_RE.match(line)
    if not m:
        return None, None
    return m.group(1).strip(), m.group(2).strip()


def extract_multiplier(line):
    m = MULTIPLIER_RE.match(line)
    return m.group(1).replace(' ', '') if m else ''


def find_title(lines, views_idx, block_start):
    """
    views 라인 기준으로 위쪽을 역추적하며 제목을 찾는다.
    전략 1: views_idx 위쪽에서 가장 가까운 '재생시간 라인(mm:ss)'을 찾고,
            그 바로 위 비공백 라인을 제목으로 본다.
    전략 2(폴백): views_idx에서 7칸 위 (블록 구조 고정 패턴).
    전략 3(폴백): 블록 내 첫 비공백 라인 (헤더성 라인 제외).
    """
    # 1차 시도: 재생시간 라인 찾기
    for j in range(views_idx - 1, block_start - 1, -1):
        if is_duration_line(lines[j]):
            for k in range(j - 1, block_start - 1, -1):
                if lines[k]:
                    return lines[k]
            break

    # 2차 시도: views 라인에서 7칸 위
    candidate_idx = views_idx - 7
    if candidate_idx >= block_start and lines[candidate_idx]:
        return lines[candidate_idx]

    # 3차 시도: 블록 내 첫 비공백 라인 (헤더성 라인 제외)
    HEADER_HINTS = {'video type:', 'videos', 'views:', 'subscribers:',
                    'publish date:', 'this month', 'clear all'}
    for j in range(block_start, views_idx):
        ln = lines[j]
        if ln and ln.lower() not in HEADER_HINTS and not is_duration_line(ln):
            return ln
    return None


def parse_file(input_path):
    with open(input_path, 'r', encoding='utf-8') as f:
        lines = [ln.strip() for ln in f.readlines()]
    n = len(lines)

    # 1단계: 모든 VPH 라인 인덱스 수집
    vph_indices = [i for i, ln in enumerate(lines) if is_vph_line(ln)]
    records = []

    # 2단계: 각 VPH 라인을 블록 종결자로 사용
    for idx, vph_idx in enumerate(vph_indices):
        # 블록 시작점 계산
        if idx == 0:
            block_start = 0
        else:
            prev_vph = vph_indices[idx - 1]
            block_start = prev_vph + 1
            # 이전 VPH 직후가 배수 라인이면 한 칸 더 건너뜀
            if block_start < n and is_multiplier_line(lines[block_start]):
                block_start += 1

        block_end = vph_idx

        # 블록 내에서 views 라인 찾기 (가장 마지막에 등장하는 것)
        views_idx = None
        for j in range(block_end - 1, block_start - 1, -1):
            if is_views_line(lines[j]):
                views_idx = j
                break
        if views_idx is None:
            continue

        # 제목 추출
        title = find_title(lines, views_idx, block_start)
        if not title:
            continue

        # 값 추출 및 변환
        vph_value = to_int_number(extract_vph(lines[vph_idx]))
        raw_views, time_text = extract_views_and_time(lines[views_idx])
        views_value = to_int_number(raw_views)
        created_date = parse_relative_time(time_text)

        # 배수 추출 (VPH 다음 라인이 배수 라인이면)
        multiplier = 'NA'
        if vph_idx + 1 < n and is_multiplier_line(lines[vph_idx + 1]):
            multiplier = extract_multiplier(lines[vph_idx + 1])

        records.append({
            '제목': title,
            'VPH': vph_value,
            '생성시간': created_date,
            'view': views_value,
            '배수': multiplier,
        })

    return records


def write_csv(records, output_path):
    fieldnames = ['제목', 'VPH', '생성시간', 'view', '배수']
    # utf-8-sig: Excel에서 한글 깨짐 방지
    with open(output_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            writer.writerow(r)


def main():
    if len(sys.argv) != 3:
        print(f"사용법: python {sys.argv[0]} input.txt output.csv", file=sys.stderr)
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    records = parse_file(input_path)
    write_csv(records, output_path)

    print(f"파싱 완료: {len(records)}개 레코드를 '{output_path}'에 저장했습니다.")


if __name__ == '__main__':
    main()
