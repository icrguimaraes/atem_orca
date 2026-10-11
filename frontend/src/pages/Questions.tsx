import { useState } from "react";
import { api } from "../api";
import { FilterBar } from "../components/FilterBar";
import { Alert, Empty, Loading, PageHeader, Stat, useLoad } from "../components/ui";
import { ItemContext, WhyDrawer, type Question } from "../components/WhyPanel";
import { fmtDateTime, fmtInt } from "../labels";
import { usePersistentState } from "../persist";

/* Perguntas da defesa do orçamento: quem analisa questiona um lançamento (linha do OPEX, movimentação de pessoal ou
   solicitação de CAPEX) no "por quê?" do Painel ou no orçamento do CC; o gestor do CC responde aqui (ou no próprio
   painel lateral). Perguntas antigas, sobre um recorte, continuam aqui. Tudo fica registrado na auditoria. */

interface QData { items: Question[]; counts: { to_answer: number; answered_mine: number; open: number } }

const STATUS = [
  { value: "", label: "Todas" },
  { value: "to_answer", label: "Para eu responder" },
  { value: "OPEN", label: "Aguardando resposta" },
  { value: "ANSWERED", label: "Respondidas" },
  { value: "CLOSED", label: "Encerradas" },
];

function Card({ q, onChange }: { q: Question; onChange: (q: Question) => void }) {
  const [answer, setAnswer] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [why, setWhy] = useState(false);
  async function act(path: string, body?: object) {
    setErr(null);
    try {
      onChange(await api<Question>(`/questions/${q.id}/${path}`, { method: "POST", body: body ? JSON.stringify(body) : undefined }));
      setAnswer("");
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  const whyQs = typeof q.scope.why === "string" ? (q.scope.why as string) : null;
  return (
    <li className="question">
      <div className="just-head">
        <div>
          <div className="just-subject">{q.subject}</div>
          <div className="muted small">{q.asked_by ?? "—"} · {fmtDateTime(q.asked_at)}</div>
        </div>
        <span className={`state ${q.status === "OPEN" ? "pending" : q.status === "ANSWERED" ? "answered" : "closed"}`}>{q.status_label}</span>
      </div>
      {q.item && <ItemContext item={q.item} />}
      <div className="question-text">{q.question}</div>
      {q.answer && (
        <div className="answer">
          <div className="muted small">{q.answered_by} · {fmtDateTime(q.answered_at)}</div>
          {q.answer}
        </div>
      )}
      {q.can_answer && (
        <div className="question-form">
          <textarea rows={2} value={answer} onChange={(e) => { setAnswer(e.target.value); setErr(null); }} placeholder={q.answer ? "Complementar a resposta" : "Sua resposta"} aria-label="Resposta" />
          <div className="inline-controls">
            <button type="button" className="btn btn-sm btn-primary" onClick={() => (answer.trim() ? act("answer", { answer }) : setErr("Escreva a resposta antes de enviar"))}>Responder</button>
          </div>
        </div>
      )}
      <div className="inline-controls">
        {whyQs && <button type="button" className="btn btn-sm btn-ghost" onClick={() => setWhy(true)}>Ver o "por quê?"</button>}
        {q.can_close && q.status !== "CLOSED" && <button type="button" className="btn btn-sm btn-ghost" onClick={() => act("close")}>Encerrar</button>}
      </div>
      {err && <div className="error-text">{err}</div>}
      {why && whyQs && <WhyDrawer qs={whyQs} onClose={() => setWhy(false)} />}
    </li>
  );
}

export default function Questions() {
  const { data, error } = useLoad(() => api<QData>("/questions"));
  const [status, setStatus] = usePersistentState("perguntas.status", "");
  const [items, setItems] = useState<Question[] | null>(null);
  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading />;
  const all = items ?? data.items;
  const shown = all.filter((q) =>
    !status ? true : status === "to_answer" ? q.status === "OPEN" && q.can_answer && !q.mine : q.status === status);
  const toAnswer = all.filter((q) => q.status === "OPEN" && q.can_answer && !q.mine).length;
  return (
    <>
      <PageHeader
        title="Perguntas"
        subtitle={'Defesa do orçamento · perguntas sobre lançamentos (OPEX, pessoal ou CAPEX) feitas no "por quê?" do Painel ou no orçamento do CC; quem responde é o gestor do CC, e tudo fica registrado.'}
      />
      <div className="stats">
        <Stat label="Para eu responder" value={fmtInt(toAnswer)} hint={toAnswer ? "aguardam a sua resposta" : "nada pendente"} />
        <Stat label="Aguardando resposta" value={fmtInt(all.filter((q) => q.status === "OPEN").length)} />
        <Stat label="Respondidas" value={fmtInt(all.filter((q) => q.status === "ANSWERED").length)} />
      </div>
      <FilterBar
        onReset={() => setStatus("")}
        resetCount={status ? 1 : 0}
        fields={[{ key: "status", label: "Situação", value: status, onChange: setStatus, options: STATUS, wide: true }]}
      />
      {shown.length === 0 ? (
        <Empty>Nenhuma pergunta nesta situação. Para perguntar, abra o "por quê?" na tabela do Painel e use "Questionar" no lançamento.</Empty>
      ) : (
        <ul className="just-list">
          {shown.map((q) => <Card key={q.id} q={q} onChange={(n) => setItems(all.map((x) => (x.id === n.id ? n : x)))} />)}
        </ul>
      )}
    </>
  );
}
