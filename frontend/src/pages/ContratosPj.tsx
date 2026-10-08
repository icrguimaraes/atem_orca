import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, download, type PjBody, type PjContract, type PjList, type PjOptions, type PjSummary } from "../api";
import { useAuth } from "../auth";
import { FilterBar } from "../components/FilterBar";
import { PhotoEditor } from "../components/pj/PhotoEditor";
import { Alert, Badge, Card, Empty, Loading, Modal, PageHeader, SearchBox, Stat, useLoad } from "../components/ui";
import { PJ_STATUS, fmtDay, fmtInt, fmtMoney, fmtMoneyInput, fmtTenure, maskCnpj, parseMoney } from "../labels";
import { usePersistentState } from "../persist";
import "./pj.css";

/* Contratos PJ (confidencial): prestadores contratados como pessoa jurídica — valores, bonificação anual e devida no
   ano, tempo de casa, foto. Só para quem tem "Vê contratos PJ" (Usuários); "Da área" vê só os CCs das áreas dos próprios
   CCs. Toda consulta e alteração vai para a auditoria, sem os valores. */

const THIS_YEAR = new Date().getFullYear();

function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function query(params: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

function Avatar({ src, blurred, className = "" }: { src?: string; blurred?: boolean; className?: string }) {
  return (
    <span className={`pj-avatar ${blurred ? "blurred" : ""} ${className}`}>
      {src ? (
        <img src={src} alt="" draggable={false} />
      ) : (
        <svg viewBox="0 0 64 64" aria-hidden>
          <rect className="av-bg" width="64" height="64" />
          <circle className="av-fg" cx="32" cy="25" r="11" />
          <path className="av-fg" d="M12 56c2-11 10-17 20-17s18 6 20 17z" />
        </svg>
      )}
    </span>
  );
}

function ConfirmModal({ title, confirmLabel, onClose, onConfirm, children }: {
  title: string; confirmLabel: string; onClose: () => void; onConfirm: () => void | Promise<void>; children: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy}>Cancelar</button>
          <button
            type="button"
            className="btn btn-danger"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onConfirm();
                onClose();
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Aguarde…" : confirmLabel}
          </button>
        </>
      }
    >
      <div className="stack">{children}</div>
    </Modal>
  );
}

/** Regra da bonificação devida (a mesma de `app/domain/rules/pj.py`), para a pessoa conferir. */
function BonusRule({ year }: { year: number }) {
  return (
    <p className="muted small pj-rule">
      <strong>Bonificação devida em {year}</strong> = bonificação anual × meses no ano ÷ 12, arredondada aos centavos.
      Meses no ano: quem entrou antes de {year} conta 12; quem entrou em {year} conta desde o mês da admissão, em
      qualquer dia; com término em {year}, conta até o mês do término (inclusive); limite de 12.
    </p>
  );
}

const months = (n: number) => `${n} ${n === 1 ? "mês" : "meses"}`;

export default function ContratosPj() {
  const { user } = useAuth();
  const areaOnly = user?.pj_access !== "ALL";
  const [year, setYear] = usePersistentState("pj.year", THIS_YEAR);
  const [status, setStatus] = usePersistentState("pj.status", "");
  const [cc, setCc] = usePersistentState("pj.cc", "");
  const [search, setSearch] = usePersistentState("pj.q", "");
  const [privacy, setPrivacy] = usePersistentState("pj.privacy", false);
  const q = useDebounced(search.trim());
  const [editing, setEditing] = useState<PjContract | "new" | null>(null);
  const [notice, setNotice] = useState<{ tone: "good" | "bad"; text: string } | null>(null);

  const options = useLoad(() => api<PjOptions>("/pj/options"));
  const photos = useLoad(() => api<Record<string, string>>("/pj/photos"));
  const { data, error, reload, loading } = useLoad(async () => {
    const [list, summary] = await Promise.all([
      api<PjList>(`/pj${query({ year, status, cost_center_id: cc, q })}`),
      api<PjSummary>(`/pj/summary${query({ year, cost_center_id: cc, q })}`),
    ]);
    return { list, summary };
  }, [year, status, cc, q]);

  const years = useMemo(() => {
    const list = Array.from({ length: 6 }, (_, i) => THIS_YEAR + 1 - i);
    return list.includes(year) ? list : [...list, year].sort((a, b) => b - a);
  }, [year]);

  async function exportXlsx() {
    try {
      await download(`/pj/export.xlsx?year=${year}`, `contratos_pj_${year}.xlsx`);
    } catch (err) {
      setNotice({ tone: "bad", text: (err as Error).message });
    }
  }

  const s = data?.summary;
  const items = data?.list.items ?? [];
  const activeFilters = (status ? 1 : 0) + (cc ? 1 : 0) + (q ? 1 : 0);
  const ccLabel = (c: PjOptions["cost_centers"][number]) => `${c.code} · ${c.name}${c.department ? ` (${c.department})` : ""}`;

  return (
    <div className={privacy ? "pj-private" : ""}>
      <PageHeader
        title="Contratos PJ"
        subtitle={`Prestadores contratados como pessoa jurídica: valores, bonificação e tempo de casa. Página confidencial; alterações e consultas ficam na auditoria, sem os valores.${areaOnly ? " Você vê só os contratos dos centros de custo da sua área." : ""}`}
        actions={
          <>
            <label className="pj-year">
              <span>Ano da bonificação</span>
              <select value={year} onChange={(e) => setYear(Number(e.target.value))} aria-label="Ano da bonificação">
                {years.map((y) => <option key={y} value={y}>{y}</option>)}
              </select>
            </label>
            <button
              type="button"
              className="btn btn-sm pj-eye"
              aria-pressed={privacy}
              onClick={() => setPrivacy(!privacy)}
              title={privacy ? "Mostrar nomes, valores e fotos" : "Embaçar nomes, valores e fotos (para mostrar a tela a terceiros)"}
            >
              {privacy ? "Mostrar dados" : "Embaçar dados"}
            </button>
            <button type="button" className="btn btn-sm" onClick={exportXlsx}>Exportar Excel</button>
            <button type="button" className="btn btn-primary btn-sm" onClick={() => setEditing("new")}>Novo contrato</button>
          </>
        }
      />

      {error && <Alert>{error}</Alert>}
      {notice && <Alert tone={notice.tone}>{notice.text}</Alert>}

      <div className="stack-lg">
        <div className="stats pj-stats">
          <Stat label="Contratos ativos" value={s ? fmtInt(s.active) : "—"} hint={s ? `${fmtInt(s.ended)} encerrado${s.ended === 1 ? "" : "s"}` : undefined} />
          <Stat label="Total mensal" value={<span className="sens">{s ? fmtMoney(s.monthly_total) : "—"}</span>} hint="contratos ativos" />
          <Stat label="Bonificação anual" value={<span className="sens">{s ? fmtMoney(s.annual_bonus_total) : "—"}</span>} hint="contratos ativos" />
          <Stat
            label={`Bonificação devida em ${year}`}
            value={<span className="sens">{s ? fmtMoney(s.bonus_due_total) : "—"}</span>}
            hint="proporcional aos meses no ano (inclui encerrados no ano)"
          />
        </div>

        <Card>
          <div className="stack">
            <FilterBar
              fields={[
                {
                  key: "status",
                  label: "Situação",
                  value: status,
                  onChange: setStatus,
                  options: [
                    { value: "", label: "Todas as situações" },
                    { value: "ACTIVE", label: "Ativos" },
                    { value: "ENDED", label: "Encerrados" },
                  ],
                },
                {
                  key: "cc",
                  label: "Centro de custo",
                  value: cc,
                  onChange: setCc,
                  wide: true,
                  options: [
                    { value: "", label: areaOnly ? "Todos os CCs da sua área" : "Todos os centros de custo" },
                    ...(options.data?.cost_centers ?? []).map((c) => ({ value: String(c.id), label: ccLabel(c) })),
                  ],
                },
              ]}
              extra={<SearchBox value={search} onChange={setSearch} placeholder="Buscar nome, empresa ou CNPJ" />}
              onReset={() => { setStatus(""); setCc(""); setSearch(""); }}
              resetCount={activeFilters}
            />
            <BonusRule year={year} />

            {!data ? (
              !error && <Loading />
            ) : items.length === 0 ? (
              <Empty>
                {activeFilters ? "Nenhum contrato com estes filtros." : "Nenhum contrato PJ cadastrado."}
                {!activeFilters && (
                  <div style={{ marginTop: 12 }}>
                    <button type="button" className="btn btn-primary btn-sm" onClick={() => setEditing("new")}>Novo contrato</button>
                  </div>
                )}
              </Empty>
            ) : (
              <div className={`table-wrap ${loading ? "pj-loading" : ""}`}>
                <table className="table pj-table">
                  <thead>
                    <tr>
                      <th>Contratado</th>
                      <th>Função e centro de custo</th>
                      <th>Admissão</th>
                      <th className="right">Mensal</th>
                      <th className="right">Bonif. anual</th>
                      <th className="right">Devida {year}</th>
                      <th>Situação</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((c) => (
                      <tr key={c.id} className="pj-row" onClick={() => setEditing(c)} title="Abrir o contrato">
                        <td className="pj-who">
                          <button
                            type="button"
                            className="pj-person"
                            onClick={(e) => { e.stopPropagation(); setEditing(c); }}
                            aria-label={`Editar o contrato de ${c.name}`}
                          >
                            <Avatar src={c.has_photo ? photos.data?.[String(c.id)] : undefined} blurred={c.photo_blurred} />
                            <span className="pj-person-text">
                              <strong className="pii">{c.name}</strong>
                              <span className="muted small pii">{c.company_name}</span>
                              <span className="muted small pii nowrap">{c.cnpj_formatted}</span>
                            </span>
                          </button>
                        </td>
                        <td data-label="Função">
                          {c.role ?? <span className="muted">—</span>}
                          <div className="muted small">
                            {c.cost_center ?? "sem centro de custo"}
                            {c.department && ` · ${c.department}${c.sector ? ` / ${c.sector}` : ""}`}
                          </div>
                        </td>
                        <td data-label="Admissão">
                          <span className="nowrap">{fmtDay(c.start_date)}</span>
                          <div className="muted small">{fmtTenure(c.tenure)}</div>
                          {c.end_date && <div className="muted small">término {fmtDay(c.end_date)}</div>}
                        </td>
                        <td data-label="Mensal" className="right nowrap"><span className="sens">{fmtMoney(c.monthly_value)}</span></td>
                        <td data-label="Bonif. anual" className="right nowrap">
                          <span className="sens">{c.annual_bonus ? fmtMoney(c.annual_bonus) : "—"}</span>
                        </td>
                        <td data-label={`Devida ${year}`} className="right nowrap">
                          <span className="sens">{fmtMoney(c.bonus.due)}</span>
                          <div className="muted small">{months(c.bonus.months)}</div>
                        </td>
                        <td data-label="Situação"><Badge tone={PJ_STATUS[c.status].tone}>{PJ_STATUS[c.status].label}</Badge></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </Card>
      </div>

      {editing !== null && (
        <ContractModal
          contract={editing === "new" ? null : editing}
          year={year}
          options={options.data}
          areaOnly={areaOnly}
          photo={editing !== "new" && editing.has_photo ? photos.data?.[String(editing.id)] : undefined}
          onClose={() => setEditing(null)}
          onSaved={(text, photosChanged) => {
            setNotice({ tone: "good", text });
            reload();
            if (photosChanged) photos.reload();
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- formulário

interface Draft {
  name: string; company_name: string; cnpj: string; role: string; cost_center_id: string; email: string; phone: string;
  monthly_value: string; annual_bonus: string; start_date: string; end_date: string; notes: string; photo_blurred: boolean;
}

function toDraft(c: PjContract | null): Draft {
  return {
    name: c?.name ?? "",
    company_name: c?.company_name ?? "",
    cnpj: c ? c.cnpj_formatted : "",
    role: c?.role ?? "",
    cost_center_id: c?.cost_center_id ? String(c.cost_center_id) : "",
    email: c?.email ?? "",
    phone: c?.phone ?? "",
    monthly_value: fmtMoneyInput(c?.monthly_value),
    annual_bonus: fmtMoneyInput(c?.annual_bonus),
    start_date: c?.start_date ?? "",
    end_date: c?.end_date ?? "",
    notes: c?.notes ?? "",
    photo_blurred: c?.photo_blurred ?? false,
  };
}

const orNull = (v: string) => (v.trim() ? v.trim() : null);

function ContractModal({ contract, year, options, areaOnly, photo, onClose, onSaved }: {
  areaOnly: boolean;
  contract: PjContract | null;
  year: number;
  options: PjOptions | null;
  photo?: string;
  onClose: () => void;
  onSaved: (text: string, photosChanged: boolean) => void;
}) {
  const creating = contract === null;
  const [draft, setDraft] = useState<Draft>(() => toDraft(contract));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [cropSrc, setCropSrc] = useState<string | null>(null);
  const [pending, setPending] = useState<{ blob: Blob; url: string } | null>(null); // foto escolhida antes de salvar
  const [current, setCurrent] = useState<string | undefined>(photo); // foto gravada (data URL)
  const [hasPhoto, setHasPhoto] = useState(contract?.has_photo ?? false);
  const [photosChanged, setPhotosChanged] = useState(false);
  const [dialog, setDialog] = useState<"archive" | "removePhoto" | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => ({ ...d, [k]: v }));

  const monthly = parseMoney(draft.monthly_value);
  const bonus = parseMoney(draft.annual_bonus);
  const invalidMoney = monthly === undefined || bonus === undefined;
  const valid =
    draft.name.trim() && draft.company_name.trim() && draft.cnpj.trim() && monthly && draft.start_date && !invalidMoney &&
    (!areaOnly || draft.cost_center_id); // com acesso "Da área", o centro de custo é obrigatório
  const shownPhoto = pending?.url ?? (hasPhoto ? current : undefined);

  useEffect(() => () => {
    if (pending) URL.revokeObjectURL(pending.url);
  }, [pending]);

  function closeCrop() {
    if (cropSrc?.startsWith("blob:") && cropSrc !== pending?.url) URL.revokeObjectURL(cropSrc);
    setCropSrc(null);
  }

  async function uploadPhoto(id: number, blob: Blob) {
    const form = new FormData();
    form.append("file", blob, "foto.jpg");
    await api(`/pj/${id}/photo`, { method: "PUT", body: form });
  }

  async function onCropped(blob: Blob): Promise<boolean> {
    if (creating) {
      setPending({ blob, url: URL.createObjectURL(blob) });
      return true;
    }
    try {
      await uploadPhoto(contract.id, blob);
      setCurrent(await blobToDataUrl(blob));
      setHasPhoto(true);
      setPhotosChanged(true);
      return true;
    } catch (err) {
      setError((err as Error).message);
      return false;
    }
  }

  async function removePhoto() {
    if (creating) {
      setPending(null);
      return;
    }
    try {
      await api(`/pj/${contract.id}/photo`, { method: "DELETE" });
      setHasPhoto(false);
      setCurrent(undefined);
      setPhotosChanged(true);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function save() {
    if (!valid) return;
    setBusy(true);
    setError(null);
    const body: PjBody = {
      name: draft.name.trim(),
      company_name: draft.company_name.trim(),
      cnpj: draft.cnpj.trim(),
      role: orNull(draft.role),
      cost_center_id: draft.cost_center_id ? Number(draft.cost_center_id) : null,
      email: orNull(draft.email),
      phone: orNull(draft.phone),
      monthly_value: monthly ?? null,
      annual_bonus: bonus ?? null,
      start_date: draft.start_date || null,
      end_date: draft.end_date || null,
      notes: orNull(draft.notes),
      photo_blurred: draft.photo_blurred,
    };
    try {
      if (creating) {
        const created = await api<PjContract>("/pj", { method: "POST", body: JSON.stringify(body) });
        let text = "Contrato incluído.";
        if (pending) {
          try {
            await uploadPhoto(created.id, pending.blob);
          } catch (err) {
            text = `Contrato incluído, mas a foto não foi gravada: ${(err as Error).message}`;
          }
        }
        onSaved(text, Boolean(pending));
      } else {
        await api<PjContract>(`/pj/${contract.id}`, { method: "PATCH", body: JSON.stringify(body) });
        onSaved("Contrato atualizado.", photosChanged);
      }
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function close() {
    if (photosChanged) onSaved("Foto atualizada.", true);
    onClose();
  }

  async function archive() {
    if (creating) return;
    try {
      await api(`/pj/${contract.id}`, { method: "DELETE" });
      onSaved("Contrato arquivado: saiu da lista (fica registrado na auditoria).", photosChanged);
      onClose();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const ccOptions = options?.cost_centers ?? [];
  return (
    <Modal
      wide
      title={creating ? "Novo contrato PJ" : "Editar contrato PJ"}
      onClose={() => !dialog && !cropSrc && close()} // Esc com recorte/confirmação aberta fecha só ela
      footer={
        <div className="pj-modal-foot">
          {!creating && (
            <button type="button" className="btn btn-ghost pj-archive" onClick={() => setDialog("archive")}>Arquivar</button>
          )}
          <span className="pj-spacer" />
          <button type="button" className="btn btn-ghost" onClick={close}>Cancelar</button>
          <button type="button" className="btn btn-primary" disabled={!valid || busy} onClick={save}>{busy ? "Salvando…" : "Salvar"}</button>
        </div>
      }
    >
      <div className="stack pj-form">
        {error && <Alert>{error}</Alert>}

        <div className="pj-photo">
          <Avatar src={shownPhoto} blurred={draft.photo_blurred} />
          <div className="pj-photo-actions">
            <div className="pj-photo-row">
              <button type="button" className="btn btn-sm" onClick={() => fileRef.current?.click()}>
                {shownPhoto ? "Trocar foto" : "Incluir foto"}
              </button>
              {shownPhoto && (
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setCropSrc(shownPhoto)}>Ajustar</button>
              )}
              {shownPhoto && (
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDialog("removePhoto")}>Remover</button>
              )}
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                hidden
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  e.target.value = "";
                  if (f) setCropSrc(URL.createObjectURL(f));
                }}
              />
            </div>
            {shownPhoto && (
              <label className="check">
                <input type="checkbox" checked={draft.photo_blurred} onChange={(e) => set("photo_blurred", e.target.checked)} />
                Embaçar foto
              </label>
            )}
          </div>
        </div>

        <div className="form-row">
          <label>Nome da pessoa<input className="pii" value={draft.name} maxLength={200} onChange={(e) => set("name", e.target.value)} autoFocus={creating} /></label>
          <label>Razão social<input className="pii" value={draft.company_name} maxLength={200} onChange={(e) => set("company_name", e.target.value)} /></label>
        </div>
        <div className="form-row">
          <label>
            CNPJ
            <input className="pii" inputMode="text" autoComplete="off" placeholder="00.000.000/0000-00" value={draft.cnpj} onChange={(e) => set("cnpj", maskCnpj(e.target.value))} />
          </label>
          <label>Função<input value={draft.role} maxLength={150} placeholder="Ex.: Consultor tributário" onChange={(e) => set("role", e.target.value)} /></label>
        </div>
        <div className="form-row">
          <label>
            {areaOnly ? "Centro de custo (da sua área)" : "Centro de custo"}
            <select value={draft.cost_center_id} onChange={(e) => set("cost_center_id", e.target.value)}>
              <option value="" disabled={areaOnly}>{areaOnly ? "Escolha o centro de custo" : "Sem centro de custo"}</option>
              {ccOptions.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.code} · {c.name}{c.department ? ` (${c.department}${c.sector ? ` / ${c.sector}` : ""})` : ""}
                </option>
              ))}
            </select>
            <span className="muted small">A área e o setor vêm do cadastro do centro de custo.</span>
          </label>
          <label>E-mail (opcional)<input className="pii" type="email" autoComplete="off" value={draft.email} onChange={(e) => set("email", e.target.value)} /></label>
        </div>
        <div className="form-row">
          <label>Telefone (opcional)<input className="pii" type="tel" autoComplete="off" value={draft.phone} maxLength={30} onChange={(e) => set("phone", e.target.value)} /></label>
          <MoneyInput label="Valor mensal do contrato" value={draft.monthly_value} onChange={(v) => set("monthly_value", v)} />
          <MoneyInput label="Bonificação anual (opcional)" value={draft.annual_bonus} onChange={(v) => set("annual_bonus", v)} />
        </div>
        <div className="form-row">
          <label>Data de admissão<input type="date" value={draft.start_date} onChange={(e) => set("start_date", e.target.value)} /></label>
          <label>
            Data de término (opcional)
            <input type="date" value={draft.end_date} min={draft.start_date || undefined} onChange={(e) => set("end_date", e.target.value)} />
            <span className="muted small">Preencha para encerrar o contrato.</span>
          </label>
        </div>
        <label>Observações<textarea className="pii" rows={3} value={draft.notes} maxLength={2000} onChange={(e) => set("notes", e.target.value)} /></label>

        {contract && (
          <dl className="kv pj-calc">
            <dt>Tempo de casa</dt>
            <dd>{fmtTenure(contract.tenure)}</dd>
            <dt>Bonificação devida em {year}</dt>
            <dd>
              <span className="sens">{fmtMoney(contract.bonus.due)}</span>
              <span className="muted small"> · {months(contract.bonus.months)} (pelos dados salvos)</span>
            </dd>
          </dl>
        )}
      </div>

      {cropSrc && <PhotoEditor source={cropSrc} onClose={closeCrop} onSave={onCropped} />}
      {dialog === "removePhoto" && (
        <ConfirmModal title="Remover foto" confirmLabel="Remover foto" onClose={() => setDialog(null)} onConfirm={removePhoto}>
          <p>A foto deste contrato será removida.</p>
        </ConfirmModal>
      )}
      {dialog === "archive" && contract && (
        <ConfirmModal title="Arquivar contrato" confirmLabel="Arquivar" onClose={() => setDialog(null)} onConfirm={archive}>
          <p>
            O contrato de <strong className="pii">{contract.name}</strong> sai da lista e dos totais. O registro continua na
            auditoria.
          </p>
          <p className="muted small">Para registrar o fim do contrato (e manter o histórico na lista), use a data de término.</p>
        </ConfirmModal>
      )}
    </Modal>
  );
}

/** Valor em R$: digita "12.345,67"; vai para a API como string decimal. */
function MoneyInput({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  const invalid = parseMoney(value) === undefined;
  return (
    <label>
      {label}
      <input
        className="sens"
        inputMode="decimal"
        autoComplete="off"
        placeholder="0,00"
        value={value}
        aria-invalid={invalid}
        onChange={(e) => onChange(e.target.value)}
        onBlur={() => {
          const parsed = parseMoney(value);
          if (parsed) onChange(fmtMoneyInput(parsed));
        }}
      />
      {invalid && <span className="field-error">Valor inválido. Ex.: 12.345,67</span>}
    </label>
  );
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}
