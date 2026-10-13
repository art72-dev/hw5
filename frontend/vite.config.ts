import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The backend allows exactly this origin (CORS), so the port is fixed: Vite fails loudly instead of drifting to 5174.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
})
