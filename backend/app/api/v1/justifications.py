from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.findings import _access, _ctx
from app.core.deps import client_ip, get_current_user, visible_cost_center_ids
from app.db import get_db
from app.models import BudgetSubmission, User
from app.services import audit
from app.services import findings as findings_svc
from app.services import justifications as svc
from app.services import opex as opex_svc

router = APIRouter(prefix="/justifications", tags=["justificativas"])


def _items(db: Session, user: User, cost_center_id: int | None) -> tuple[opex_svc.Context, list[dict]]:
    ctx = _ctx(db)
    scope = visible_cost_center_ids(db, user)
    if cost_center_id and scope is not None and cost_center_id not in scope:
        raise HTTPException(403, "Sem acesso a este centro de custo")
    return ctx, svc.collect(db, ctx, scope, cost_center_id)


@router.get("", summary="Tudo o que precisa de justificativa no orçamento do ciclo (OPEX, Pessoal e CAPEX)")
def list_items(
    cost_center_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    ctx, items = _items(db, user, cost_center_id)
    editable: dict[int, bool] = {}
    for i in items:
        if i["submission_id"] not in editable:
            access, _ = _access(db, ctx, user, db.get(BudgetSubmission, i["submission_id"]))
            editable[i["submission_id"]] = access.edit
        i["editable"] = editable[i["submission_id"]]
    return {
        "version": ctx.version.label,
        "target_year": ctx.target_year,
        "ref_year": ctx.ref_year,
        "items": items,
        "counts": {
            "total": len(items),
            "missing": sum(1 for i in items if not i["justified"]),
            "cost_centers": len({i["cost_center_id"] for i in items}),
        },
    }


class SaveIn(BaseModel):
    key: str
    text: str = Field(max_length=4000)


@router.put("", summary="Grava a justificativa de um item (com registro na auditoria e em Apontamentos)")
def save(body: SaveIn, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx = _ctx(db)
    try:
        sub_id, entity, ident = svc.parse_key(body.key)
    except svc.JustificationError as exc:
        raise HTTPException(422, str(exc)) from exc
    sub = db.get(BudgetSubmission, sub_id)
    if sub is None or sub.version_id != ctx.version.id:
        raise HTTPException(404, "Orçamento não encontrado")
    scope = visible_cost_center_ids(db, user)
    if scope is not None and sub.cost_center_id not in scope:
        raise HTTPException(403, "Sem acesso a este centro de custo")
    access, require_edit = _access(db, ctx, user, sub)
    require_edit(access)
    item = next((i for i in svc.collect(db, ctx, None, sub.cost_center_id) if i["key"] == body.key), None)
    if item is None:
        raise HTTPException(404, "Item não encontrado")
    text = body.text.strip()
    try:
        before, after = svc.save(db, ctx, sub, entity, ident, text, user.id)
    except (svc.JustificationError, opex_svc.OpexError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    opex_svc.mark_in_progress(db, sub, ctx, user.id)
    note = f"Justificativa: {text}" if text else "Justificativa apagada"
    # faltava justificativa: vira correção registrada em Apontamentos (mesma chave do apontamento)
    if not item["justified"] and text:
        finding = {
            "key": body.key,
            "kind": svc.FINDING_KIND[entity],
            "severity": "CRITICAL",
            "subject": item["subject"],
            "message": "Justificativa obrigatória para enviar",
        }
        findings_svc.log(db, sub, finding, "CORRECTED", note, user.id)
    audit.record(
        db,
        user_id=user.id,
        action="JUSTIFICATION",
        entity_type=entity.lower(),
        entity_id=ident,
        before=before,
        after=after,
        reason=f"{item['cost_center']} · {item['subject']}",
        ip=client_ip(request),
    )
    db.commit()
    return {"key": body.key, "text": text, "justified": bool(text or item["line_texts"])}


@router.get("/export.xlsx", summary="Excel das justificativas (resumo por CC + uma aba por módulo)")
def export(cost_center_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx, items = _items(db, user, cost_center_id)
    content = svc.export_workbook(items, ctx.target_year, ctx.ref_year)
    name = f"Justificativas_{ctx.target_year}_{ctx.version.file_tag}.xlsx"
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
