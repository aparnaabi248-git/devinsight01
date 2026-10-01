import { Navigate, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { AppLayout } from '@/components/AppLayout';
import { ProtectedRoute } from '@/components/ProtectedRoute';
import AnalyzePage from '@/pages/AnalyzePage';
import AnalyticsPage from '@/pages/AnalyticsPage';
import ContributorsPage from '@/pages/ContributorsPage';
import DashboardPage from '@/pages/DashboardPage';
import LoginPage from '@/pages/LoginPage';
import ModelsPage from '@/pages/ModelsPage';
import PredictionsPage from '@/pages/PredictionsPage';
import TeamsPage from '@/pages/TeamsPage';
import { useAuth } from '@/context/AuthContext';
import type { ReactNode } from 'react';

function LoginGate({ children }: { children: ReactNode }) {
  const { authenticated, initialising } = useAuth();
  if (initialising) {
    return (
      <div style={{ display: 'grid', placeItems: 'center', minHeight: '100vh' }}>
        <div className="spinner" style={{ width: 26, height: 26, borderWidth: 3 }} />
      </div>
    );
  }
  return authenticated ? <Navigate to="/" replace /> : <>{children}</>;
}

export default function App() {
  return (
    <QueryClientProvider client={new QueryClient()}>
      <Routes>
        <Route
          path="/login"
          element={
            <LoginGate>
              <LoginPage />
            </LoginGate>
          }
        />
        <Route
          element={
            <ProtectedRoute>
              <AppLayout />
            </ProtectedRoute>
          }
        >
          <Route index element={<DashboardPage />} />
          <Route path="analyze" element={<AnalyzePage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="contributors" element={<ContributorsPage />} />
          <Route path="predictions" element={<PredictionsPage />} />
          <Route path="models" element={<ModelsPage />} />
          <Route path="teams" element={<TeamsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </QueryClientProvider>
  );
}
