import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { Toaster } from 'react-hot-toast';

import App from '@/App';
import { AuthProvider } from '@/context/AuthContext';
import { theme } from '@/lib/theme';
import '@/index.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        // Never retry auth or validation failures.
        const status = (error as { status?: number }).status ?? 0;
        if (status >= 400 && status < 500) return false;
        return failureCount < 2;
      },
    },
  },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
          <Toaster
            position="bottom-right"
            toastOptions={{
              duration: 4500,
              style: {
                background: theme.color.surfaceHover,
                color: theme.color.text,
                border: `1px solid ${theme.color.border}`,
                borderRadius: theme.radius.md,
                fontSize: '14px',
                boxShadow: theme.shadow.pop,
              },
              success: {
                iconTheme: { primary: theme.color.success, secondary: theme.color.bg },
              },
              error: { iconTheme: { primary: theme.color.danger, secondary: theme.color.bg } },
            }}
          />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
