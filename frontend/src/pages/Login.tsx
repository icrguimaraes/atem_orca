import { BrandMark } from "../components/Brand";
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";

/**
 * Entrada no desenho da Prévia de Resultados (06/11/2026): painel tinta com a marca, a frase e o arco laranja à
 * esquerda; formulário no creme à direita. No celular só o formulário, com a marca em cima. Mesmo fluxo de antes.
 */
export default function Login() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate("/", { replace: true });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <aside className="auth-brand">
        <div className="brand">
          <img src="/brand/logo-atem-claro.png" alt="Grupo Atem" />
          <div>
            <b>Orçamento</b>
            <span>Grupo ATEM · Controladoria e Tributos</span>
          </div>
        </div>
        <div>
          <p className="auth-tag">
            Do realizado ao orçado, cada número com <em>dono e justificativa</em>.
          </p>
          <p className="auth-sub">Planejamento, acompanhamento e defesa do orçamento.</p>
        </div>
        <div className="auth-foot">Acesso restrito · usuários criados pela Controladoria</div>
      </aside>
      <main className="auth-form">
        <form onSubmit={submit}>
          <div className="brand auth-mobile-brand">
            <BrandMark size={36} compact />
            <div>
              <b>Orçamento</b>
              <span>Grupo ATEM · Controladoria e Tributos</span>
            </div>
          </div>
          <h1>Entrar</h1>
          <p className="lead">Use seu e-mail corporativo para acessar o Orçamento.</p>
          {error && <div className="alert alert-bad">{error}</div>}
          <label>
            E-mail
            <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
          </label>
          <label>
            Senha
            <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Entrando…" : "Entrar"}
          </button>
          <p className="auth-mobile-foot">Acesso restrito · usuários criados pela Controladoria</p>
        </form>
      </main>
    </div>
  );
}
