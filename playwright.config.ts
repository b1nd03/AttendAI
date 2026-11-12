import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests/e2e', fullyParallel:false, workers:1, timeout:45000,
  use: { baseURL:'http://127.0.0.1:8001', trace:'retain-on-failure', screenshot:'only-on-failure' },
  webServer: { command:'.venv\\Scripts\\python.exe tests\\serve_e2e.py', url:'http://127.0.0.1:8001/api/health', reuseExistingServer:false, timeout:60000 },
  reporter:[['list'],['html',{open:'never'}]],
});
