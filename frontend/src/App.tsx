import { Suspense, lazy, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import Layout from "./components/Layout";
import { Loading } from "./components/ui";
// Plotly (~1 MB) só é baixado quando a Análise é aberta
const Analytics = lazy(() => import("./pages/Analytics"));
// Painel (Plotly): só baixa o Plotly quando aberto
const Painel2 = lazy(() => import("./pages/Painel2"));
import Audit from "./pages/Audit";
import CapexCostCenter from "./pages/CapexCostCenter";
import Consolidation from "./pages/Consolidation";
import CyclePage from "./pages/CyclePage";
import Findings from "./pages/Findings";
import ImportDetail from "./pages/ImportDetail";
import Imports from "./pages/Imports";
import Login from "./pages/Login";
import MasterData from "./pages/MasterData";
import OpexCostCenter from "./pages/OpexCostCenter";
import PersonnelCostCenter from "./pages/PersonnelCostCenter";
import PersonnelSimulation from "./pages/PersonnelSimulation";
import Structure from "./pages/Structure";
import Users from "./pages/Users";

function Protected({ children, roles }: { children: ReactNode; roles?: string[] }) {
  const { user, loading, can } = useAuth();
  if (loading) return <Loading />;
  if (!user) return <Navigate to="/login" replace />;
  if (roles && !can(...roles)) return <Navigate to="/" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            element={
              <Protected>
                <Layout />
              </Protected>
            }
          >
            <Route index element={<Suspense fallback={<Loading />}><Painel2 /></Suspense>} />
            <Route path="analise" element={<Suspense fallback={<Loading />}><Analytics /></Suspense>} />
            <Route path="painel-2" element={<Navigate to="/" replace />} />
            <Route path="importacoes" element={<Protected roles={["CONTROLLER"]}><Imports /></Protected>} />
            <Route path="importacoes/:id" element={<Protected roles={["CONTROLLER"]}><ImportDetail /></Protected>} />
            <Route path="orcamento" element={<Navigate to="/?tipo=OPEX" replace />} />
            <Route path="orcamento/:ccId" element={<OpexCostCenter />} />
            <Route path="capex" element={<Navigate to="/?tipo=CAPEX" replace />} />
            <Route path="capex/:ccId" element={<CapexCostCenter />} />
            <Route path="pessoal" element={<Navigate to="/?tipo=PERSONNEL" replace />} />
            <Route path="pessoal/simulacao" element={<PersonnelSimulation />} />
            <Route path="pessoal/:ccId" element={<PersonnelCostCenter />} />
            <Route path="consolidacao" element={<Consolidation />} />
            <Route path="apontamentos" element={<Findings />} />
            <Route path="cadastros" element={<MasterData />} />
            <Route path="estrutura" element={<Protected roles={["CONTROLLER"]}><Structure /></Protected>} />
            <Route path="ciclo" element={<CyclePage />} />
            <Route path="usuarios" element={<Protected roles={["CONTROLLER"]}><Users /></Protected>} />
            <Route path="auditoria" element={<Protected roles={["CONTROLLER"]}><Audit /></Protected>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
