"""리스트 엑셀(.xlsx) 다운로드 공용 유틸."""
from collections.abc import Iterable
from io import BytesIO
from urllib.parse import quote

from fastapi.responses import Response
from openpyxl import Workbook

from app.core.clock import kst_format, utcnow
from app.core.config import default_settings

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# 엑셀 다운로드 1회당 행 상한(감사 Phase 3 — 성능 M2).
# export는 필터 결과 전체를 ORM 객체로 적재한 뒤 BytesIO 버퍼에 워크북을 만들므로,
# 무제한이면 수십만 건 다운로드 1건이 수백 MB를 점유해 워커 OOM을 일으킬 수 있다.
# 상한 도달 시 가장 최근(정렬 기준 상위) 행부터 상한까지만 내려간다 —
# 더 오래된 데이터가 필요하면 기간 필터로 좁혀 받도록 안내한다.
# .env(export_max_rows)로 조정 가능.
EXPORT_MAX_ROWS = default_settings().export_max_rows

_FORMULA_PREFIXES = ("=", "+", "-", "@")


def xlsx_safe(value):
    """수식 주입 방어 — =,+,-,@ 로 시작하는 문자열 셀에 ' 프리픽스."""
    if isinstance(value, str) and value[:1] in _FORMULA_PREFIXES:
        return f"'{value}"
    return value


def xlsx_response(filename_prefix: str, header: list[str],
                  rows: Iterable[list], *, sheet_title: str = "Sheet1") -> Response:
    """헤더 + 행들을 write-only 워크북으로 만들어 첨부 다운로드 응답 생성.

    rows의 각 셀은 호출측이 표시용으로 포맷(시각=KST 문자열, 금액=정수).
    파일명: {prefix}-{YYYYmmdd-HHMM(KST)}.xlsx"""
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(sheet_title)
    ws.append(list(header))
    for row in rows:
        ws.append([xlsx_safe(c) for c in row])
    buf = BytesIO()
    wb.save(buf)
    filename = f"{filename_prefix}-{kst_format(utcnow(), '%Y%m%d-%H%M')}.xlsx"
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
    cd = (f"attachment; filename=\"{ascii_fallback}\"; "
          f"filename*=UTF-8''{quote(filename)}")
    return Response(buf.getvalue(), media_type=XLSX_MEDIA,
                    headers={"Content-Disposition": cd})
