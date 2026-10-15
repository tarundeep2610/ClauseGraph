import json, time, sys
import httpx

with open('/app/data/chunk1_payload.json', encoding='utf-8') as f:
    payload = json.load(f)

system_prompt = payload['data'][0]['value']
user_prompt = payload['query']
model = payload['model_name']

approx_input_tokens = (len(system_prompt) + len(user_prompt)) // 4
num_ctx = max(32768, approx_input_tokens + 4096)
print(f"system_prompt len={len(system_prompt)} user_prompt len={len(user_prompt)} model={model}")
print(f"approx_input_tokens={approx_input_tokens} num_ctx={num_ctx}")

ollama_payload = {
    "model": model,
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    "stream": False,
    "options": {"temperature": 0.0, "num_ctx": num_ctx},
}

base_url = "http://100.124.212.108:11600"
t0 = time.monotonic()
resp = httpx.post(f"{base_url}/api/chat", json=ollama_payload, timeout=900.0)
elapsed = time.monotonic() - t0
data = resp.json()
content = data.get("message", {}).get("content", "")
print(f"status_code={resp.status_code} elapsed={elapsed:.1f}s")
print(f"response content length: {len(content)}")
print(f"prompt_eval_count={data.get('prompt_eval_count')} eval_count={data.get('eval_count')} done_reason={data.get('done_reason')}")

with open('/app/data/ollama_chunk1_response.json', 'w', encoding='utf-8') as f:
    f.write(content)
print("saved full response to /app/data/ollama_chunk1_response.json")
