import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { SessionProvider } from "./hooks/useSession";

import Dashboard from "./pages/Dashboard";
import FormBuilder from "./pages/FormBuilder";
import FormTemplates from "./pages/FormTemplates";
import Login from "./pages/Login";
import NewOnboarding from "./pages/NewOnboarding";
import NotFound from "./pages/NotFound";
import OnboardingDetail from "./pages/OnboardingDetail";
import RunDetail from "./pages/RunDetail";
import VendorForm from "./pages/VendorForm";

/**
 * Two route trees, and the split is the security boundary.
 *
 * Everything nested inside <Layout> requires a session and gets the employee
 * shell. The vendor portal and the login page sit outside it, so a vendor is
 * never one render away from employee navigation, and the token in the URL is
 * the only authorisation they carry.
 */
export default function App() {
  return (
    <SessionProvider>
      <BrowserRouter basename={import.meta.env.BASE_URL}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/vendor/onboard/:token" element={<VendorForm />} />

          <Route element={<Layout />}>
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/onboardings/new" element={<NewOnboarding />} />
            <Route path="/onboardings/:caseId" element={<OnboardingDetail />} />
            <Route path="/forms" element={<FormTemplates />} />
            <Route path="/forms/:templateId" element={<FormBuilder />} />
            <Route path="/run/:runId" element={<RunDetail />} />
          </Route>

          <Route path="*" element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </SessionProvider>
  );
}
