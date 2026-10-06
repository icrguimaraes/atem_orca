import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import Layout from "./components/Layout";
import { Loading } from "./components/ui";
import Audit from "./pages/Audit";
import CapexCostCenter from "./pages/CapexCostCenter";
import CapexList from "./pages/CapexList";
import Consolidation from "./pages/Consolidation";
import CyclePage from "./pages/CyclePage";
import Home from "./pages/Home";
import ImportDetail from "./pages/ImportDetail";
import Imports from "./pages/Imports";
import Login from "./pages/Login";
import MasterData from "./pages/MasterData";
import OpexCostCenter from "./pages/OpexCostCenter";
import OpexList from "./pages/OpexList";
import PersonnelCostCenter from "./pages/PersonnelCostCenter";
import PersonnelList from "./pages/PersonnelList";
import PersonnelSimulation from "./pages/PersonnelSimulation";
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
            <Route index element={<Home />} />
            <Route path="importacoes" element={<Protected roles={["CONTROLLER"]}><Imports /></Protected>} />
            <Route path="importacoes/:id" element={<Protected roles={["CONTROLLER"]}><ImportDetail /></Protected>} />
            <Route path="orcamento" element={<OpexList />} />
            <Route path="orcamento/:ccId" element={<OpexCostCenter />} />
            <Route path="capex" element={<CapexList />} />
            <Route path="capex/:ccId" element={<CapexCostCenter />} />
            <Route path="pessoal" element={<PersonnelList />} />
            <Route path="pessoal/simulacao" element={<PersonnelSimulation />} />
            <Route path="pessoal/:ccId" element={<PersonnelCostCenter />} />
            <Route path="consolidacao" element={<Consolidation />} />
            <Route path="cadastros" element={<MasterData />} />
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
