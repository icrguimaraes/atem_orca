"""Integração com o ATEM Movimentação de Pessoal: leitura do quadro (matrícula, nome, cargo, CC e salário).

Máquina a máquina, sem usuário: chave própria em `MOVPESSOAL_TOKEN` (cabeçalho `Authorization: Bearer …`). Sem a
variável a rota nem existe (404). Devolve só colaboradores ativos dos CCs do prefixo pedido (padrão 1050101 =
Controladoria e Tributos) e cada leitura fica na auditoria, sem os valores.
"""

import hmac
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Company, CostCenter, Employee, JobPosition
from app.services import audit

router = APIRouter(prefix="/integrations/movpessoal", tags=["integrações"])


def authorize(authorization: str | None = Header(default=None)) -> None:
    token = get_settings().movpessoal_token
    if not token:
        raise HTTPException(404, "Not Found")
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not given or not hmac.compare_digest(given.encode(), token.encode()):
        raise HTTPException(401, "Chave de integração inválida")


@router.get("/employees", summary="Quadro ativo dos CCs do prefixo (para o Movimentação de Pessoal)")
def employees(
    request: Request,
    cc_prefix: str = Query("1050101", pattern=r"^\d{4,10}$", description="Prefixo do código do CC"),
    db: Session = Depends(get_db),
    _: None = Depends(authorize),
):
    rows = db.execute(
        select(Employee, Company.code, CostCenter.code, JobPosition.name)
        .join(Company, Company.id == Employee.company_id)
        .join(CostCenter, CostCenter.id == Employee.cost_center_id)
        .outerjoin(JobPosition, JobPosition.id == Employee.position_id)
        .where(Employee.is_active.is_(True), CostCenter.code.startswith(cc_prefix))
        .order_by(CostCenter.code, Employee.name)
    ).all()
    items = [
        {
            "company_code": company,
            "registration": e.registration,
            "name": e.name,
            "cost_center_code": cc,
            "job_title": position,
            "base_salary": f"{e.base_salary:.2f}" if e.base_salary is not None else None,
            "contract_type": e.contract_type_code,
            "admission_date": e.admission_date.isoformat() if e.admission_date else None,
        }
        for e, company, cc, position in rows
    ]
    audit.record(
        db,
        user_id=None,
        action="INTEGRATION_READ",
        entity_type="employee",
        after={"consumer": "movpessoal", "cc_prefix": cc_prefix, "count": len(items)},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return {"generated_at": datetime.now(UTC).isoformat(), "cc_prefix": cc_prefix, "employees": items}
