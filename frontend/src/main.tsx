import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import App from './App.tsx'
import { ThemeProvider } from '@/context/ThemeContext'
import { TenantProvider } from '@/context/TenantContext'

const queryClient = new QueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <TenantProvider>
        <QueryClientProvider client={queryClient}>
          <App />
        </QueryClientProvider>
      </TenantProvider>
    </ThemeProvider>
  </StrictMode>,
)
