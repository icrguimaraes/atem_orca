import { BrandMark } from "../components/Brand";
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";

/** Grafismo da entrada (no estilo da capa dos reports da Controladoria): grade, colunas, curva com área, projeção
 *  tracejada, rosca e cartões com minigráficos, em traço fino vermelho e dourado. Só decoração (aria-hidden). */
const BARS = [
  [70, 300, 46], [96, 300, 70], [148, 300, 58], [174, 300, 92], [226, 300, 84], [252, 300, 118],
  [304, 300, 104], [330, 300, 150], [382, 300, 132], [408, 300, 176],
] as const;
const CURVE = "M60 250 C 110 236, 140 214, 180 206 S 250 170, 290 176 S 360 128, 400 120 S 470 96, 500 82";
const PROJ = "M500 82 C 530 72, 560 66, 600 52";

function FlowArt() {
  return (
    <svg className="login-flow" viewBox="0 0 640 520" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <defs>
        <linearGradient id="login-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" style={{ stopColor: "var(--atem-red)", stopOpacity: 0.16 }} />
          <stop offset="1" style={{ stopColor: "var(--atem-red)", stopOpacity: 0 }} />
        </linearGradient>
      </defs>
      <g className="login-grid">
        {[60, 120, 180, 240, 300].map((y) => <path key={y} d={`M40 ${y} H620`} />)}
        {[120, 220, 320, 420, 520].map((x) => <path key={x} d={`M${x} 40 V320`} />)}
      </g>
      <g className="login-bars">
        {BARS.map(([x, base, h], i) => (
          <rect key={x} x={x} y={base - h} width="20" height={h} rx="4" className={i % 2 ? "is-gold" : ""} style={{ animationDelay: `${i * 0.06}s` }} />
        ))}
      </g>
      <path className="login-area" d={`${CURVE} L500 300 L60 300 Z`} />
      <path className="login-curve" d={CURVE} />
      <path className="login-proj" d={PROJ} />
      <g>
        {[[60, 250], [180, 206], [290, 176], [400, 120], [500, 82]].map(([x, y]) => (
          <circle key={x} className="login-node is-red" cx={x} cy={y} r="4.5" />
        ))}
        <circle className="login-node is-gold" cx="600" cy="52" r="8" />
      </g>
      <g className="login-donut" transform="translate(540 220)">
        <circle r="44" className="track" />
        <circle r="44" className="seg-red" pathLength="100" strokeDasharray="58 42" transform="rotate(-90)" />
        <circle r="44" className="seg-gold" pathLength="100" strokeDasharray="24 76" strokeDashoffset="-60" transform="rotate(-90)" />
        <circle r="28" className="track" />
      </g>
      <g className="login-cards">
        <g transform="translate(70 360)">
          <rect width="170" height="84" rx="14" />
          <path className="mini red" d="M16 60 L46 50 L74 54 L102 36 L130 40 L154 22" />
          <path className="mini-bar" d="M16 20 H70" />
          <path className="mini-bar short" d="M16 30 H48" />
        </g>
        <g transform="translate(260 380)">
          <rect width="170" height="84" rx="14" />
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <rect key={i} className={i === 5 ? "mini-col is-gold" : "mini-col"} x={18 + i * 24} y={62 - (14 + i * 6)} width="12" height={14 + i * 6} rx="3" />
          ))}
        </g>
        <g transform="translate(450 360)">
          <rect width="150" height="84" rx="14" />
          <path className="mini gold" d="M16 30 L44 30 L44 44 L74 44 L74 56 L104 56 L104 64 L134 64" />
          <path className="mini-bar" d="M16 18 H64" />
        </g>
      </g>
    </svg>
  );
}

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
    <div className="login-wrap">
      <div className="login-art" aria-hidden="true">
        <FlowArt />
      </div>
      <main className="login-main">
        <BrandMark size={56} />
        <p className="login-eyebrow">Controladoria e Tributos · Grupo Atem</p>
        <h1 className="login-title">Orçamento<em>.</em></h1>
        <p className="login-lede">
          Planejamento, acompanhamento e defesa do orçamento. <span>Do realizado ao orçado, cada número com dono e justificativa.</span>
        </p>
        <form className="login-form" onSubmit={submit}>
          <label>
            E-mail
            <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label>
            Senha
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {error && <div className="alert alert-bad">{error}</div>}
          <button className="btn btn-primary" disabled={busy}>
            {busy ? "Entrando…" : "Entrar"}
          </button>
        </form>
        <p className="login-foot">Acesso restrito · usuários criados pela Controladoria</p>
      </main>
    </div>
  );
}
