import { Suspense, useEffect } from 'react';
import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { useAuthStore } from '@/store/useAuthStore';
import AppShell from '@/components/layout/AppShell';
import { routeConfigs } from '@/routes';

function AuthGuard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuthStore();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-surface-950">
        <div className="flex flex-col items-center gap-4">
          <div className="w-10 h-10 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-gray-400 text-sm">加载中…</p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}

function PublicGuard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuthStore();

  if (isLoading) {
    return null;
  }

  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  return <>{children}</>;
}

export default function App() {
  const { checkSession } = useAuthStore();

  useEffect(() => {
    checkSession();
  }, [checkSession]);

  return (
    // Suspense boundary for lazy-loaded pages (routes.tsx uses React.lazy);
    // without it, suspending during a sync navigation crashes the tree.
    <Suspense
      fallback={
        <div className="flex items-center justify-center h-screen bg-surface-950">
          <div className="w-10 h-10 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      }
    >
      <Routes>
        {/* Public routes */}
        {routeConfigs
          .filter((r) => r.public)
          .map((route) => (
            <Route
              key={route.path}
              path={route.path}
              element={<PublicGuard>{route.element}</PublicGuard>}
            />
          ))}

        {/* Authenticated routes */}
        <Route
          element={
            <AuthGuard>
              <AppShell />
            </AuthGuard>
          }
        >
          {routeConfigs
            .filter((r) => !r.public)
            .map((route) => (
              <Route key={route.path} path={route.path} element={route.element} />
            ))}
        </Route>

        {/* Catch all */}
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </Suspense>
  );
}