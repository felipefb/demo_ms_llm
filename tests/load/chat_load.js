// Teste de carga básico do POST /v1/chat com k6 (https://k6.io).
// Uso: veja tests/load/README.md. Requer o serviço rodando (make up + make run
// ou docker compose) e uma API key válida em API_KEY.

import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const API_KEY = __ENV.API_KEY || "test-api-key";

export const options = {
  stages: [
    { duration: "30s", target: 10 }, // ramp-up
    { duration: "1m", target: 10 }, // carga sustentada
    { duration: "15s", target: 0 }, // ramp-down
  ],
  thresholds: {
    // Ajuste conforme o provider real: com EchoLLMClient (dev) p95 < 300ms;
    // com OpenRouter free tier espere segundos e aumente o limite.
    http_req_duration: ["p(95)<3000"],
    http_req_failed: ["rate<0.05"],
    checks: ["rate>0.95"],
  },
};

export default function () {
  const payload = JSON.stringify({
    user_id: `load-user-${__VU}`,
    prompt: `k6 load test message ${__ITER}`,
  });
  const res = http.post(`${BASE_URL}/v1/chat`, payload, {
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
    },
  });
  check(res, {
    "status is 200": (r) => r.status === 200,
    "has response body": (r) => r.status !== 200 || !!r.json("response"),
  });
  // 429 é esperado se RATE_LIMIT_REQUESTS for baixo — ajuste o env do serviço.
  sleep(1);
}
