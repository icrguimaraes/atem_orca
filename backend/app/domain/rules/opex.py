"""Calculadoras dos pacotes OPEX que possuem fórmula no template (Viagens e Eventos)."""

from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.rules.common import empty_months, money

# Contas geradas por uma viagem (aba I - Viagens, linha 60)
TRAVEL_TICKET_ACCOUNT = "6010301011"  # Despesas com Passagens
TRAVEL_PER_DIEM_ACCOUNT = "6010301036"  # Diária de Viagem
TRAVEL_LODGING_ACCOUNT = "6010301001"  # Hospedagem


@dataclass(frozen=True)
class TravelRates:
    """Parâmetros resolvidos para (tipo de viagem, cargo)."""

    round_trip_fare: Decimal | None  # matriz destino × origem; None = não cadastrada
    per_diem_daily: Decimal
    lodging_daily: Decimal
    one_way_factor: Decimal = Decimal("0.5")


@dataclass(frozen=True)
class TravelInput:
    departure_month: int
    return_month: int | None
    days: int


@dataclass
class TravelResult:
    ticket: Decimal
    per_diem: Decimal
    lodging: Decimal
    month: int
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return money(self.ticket + self.per_diem + self.lodging)

    def by_account(self) -> dict[str, dict[int, Decimal]]:
        """Distribuição mensal por conta — tudo no mês de ida (Consolidador do template)."""
        result = {}
        for account, amount in (
            (TRAVEL_TICKET_ACCOUNT, self.ticket),
            (TRAVEL_PER_DIEM_ACCOUNT, self.per_diem),
            (TRAVEL_LODGING_ACCOUNT, self.lodging),
        ):
            months = empty_months()
            months[self.month] = amount
            result[account] = months
        return result


def calculate_travel(data: TravelInput, rates: TravelRates) -> TravelResult:
    """Excel (I - Viagens):
    Passagem   = IF(volta<>"", matriz[destino, origem], matriz[destino, origem] / 2)
    Diária     = SUMIFS(diária; cargo; tipo) × dias
    Hospedagem = SUMIFS(hospedagem; cargo; tipo) × dias
    """
    if not 1 <= data.departure_month <= 12:
        raise ValueError("Mês de ida deve estar entre 1 e 12")
    if data.return_month is not None and not 1 <= data.return_month <= 12:
        raise ValueError("Mês de volta deve estar entre 1 e 12")
    if data.days < 0:
        raise ValueError("Período (nº de dias) não pode ser negativo")

    warnings: list[str] = []
    if rates.round_trip_fare is None:
        warnings.append("Tarifa de passagem não cadastrada para origem/destino")
        ticket = Decimal("0")
    elif data.return_month is None:
        ticket = rates.round_trip_fare * rates.one_way_factor
    else:
        ticket = rates.round_trip_fare
    if data.return_month is not None and data.return_month < data.departure_month:
        warnings.append("Mês de volta anterior ao mês de ida")

    return TravelResult(
        ticket=money(ticket),
        per_diem=money(rates.per_diem_daily * data.days),
        lodging=money(rates.lodging_daily * data.days),
        month=data.departure_month,
        warnings=warnings,
    )


@dataclass(frozen=True)
class EventInput:
    month: int
    people: int
    meal_per_person: Decimal  # Interno = 60; Externo = 120 (lookup EVENT_TYPE)
    graphic_material: Decimal = Decimal("0")
    structure: Decimal = Decimal("0")
    gifts: Decimal = Decimal("0")
    transport: Decimal = Decimal("0")


def calculate_event(data: EventInput) -> tuple[Decimal, dict[int, Decimal]]:
    """Excel (III - Comunic e Mkt):
    Alimentação = VLOOKUP(Interno/Externo) × QTD PESSOAS
    TOTAL       = SUM(Alimentação : Deslocamento)
    Mês         = VLOOKUP(mês, MÊS DO EVENTO, TOTAL) → total integral no mês do evento
    """
    if not 1 <= data.month <= 12:
        raise ValueError("Mês do evento deve estar entre 1 e 12")
    if data.people < 0:
        raise ValueError("Quantidade de pessoas não pode ser negativa")
    meal = data.meal_per_person * data.people
    event_total = money(meal + data.graphic_material + data.structure + data.gifts + data.transport)
    months = empty_months()
    months[data.month] = event_total
    return event_total, months
