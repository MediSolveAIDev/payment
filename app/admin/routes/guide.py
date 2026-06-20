from fastapi import APIRouter, Depends, Request, HTTPException
from pathlib import Path
import markdown

from app.admin import render
from app.admin.deps import AdminContext, require_any

router = APIRouter(prefix="/guide")

# 프로젝트 루트 기준 경로를 관리자 전용 매뉴얼(admin_manual)로 변경
DOCS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "docs" / "admin_manual"

GUIDES = [
    {"id": "dashboard", "title": "대시보드", "file": "01-dashboard.md"},
    {"id": "subscriptions", "title": "구독 관리", "file": "02-subscriptions.md"},
    {"id": "payments", "title": "결제 관리", "file": "03-payments.md"},
    {"id": "plans", "title": "요금제 관리", "file": "04-plans.md"},
    {"id": "services", "title": "서비스 관리", "file": "05-services.md"},
    {"id": "settlement", "title": "정산 관리", "file": "06-settlement.md"},
    {"id": "audit", "title": "감사 및 계정", "file": "07-audit-and-users.md"},
]

@router.get("/{guide_id}")
async def guide_page(guide_id: str, request: Request, ctx: AdminContext = Depends(require_any)):
    guide = next((g for g in GUIDES if g["id"] == guide_id), None)
    if not guide:
        raise HTTPException(status_code=404, detail="가이드를 찾을 수 없습니다")
    
    file_path = DOCS_DIR / guide["file"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="가이드 파일을 찾을 수 없습니다")
    
    content = file_path.read_text(encoding="utf-8")
    # 관리자 매뉴얼이므로 개발적인 표기보다는 깔끔한 렌더링에 집중
    md = markdown.Markdown(extensions=["extra", "toc", "sane_lists", "attr_list"])
    html_content = md.convert(content)
    
    return render(request, "guide.html", ctx=ctx, 
                  content=html_content, title=guide["title"], 
                  guide_id=guide_id, guides=GUIDES)

@router.get("")
async def guide_index(request: Request, ctx: AdminContext = Depends(require_any)):
    # 기본값으로 대시보드 가이드 표시
    return await guide_page("dashboard", request, ctx)
