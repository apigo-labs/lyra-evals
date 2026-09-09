"""Official tau2 Gym on a private network. Hidden state never leaves this service."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import tau2.evaluator.evaluator_nl_assertions as nl
from tau2.gym.gym_agent import AgentGymEnv

config = json.loads(Path("/config/task.json").read_text())
args = {
    "api_base": "http://simulator-gateway:8080",
    "api_key": "relay-only",
    "num_retries": 0,
    "timeout": config["deadline_seconds"],
    "max_tokens": 4096,
}
if config["protocol"] == "openai_chat_completions":
    args["api_base"] += "/v1"
model = ("anthropic/" if config["protocol"] == "anthropic_messages" else "openai/") + config[
    "model"
]
# Freeze the judge to the same explicitly selected reference model; prevent vendor defaults.
nl.DEFAULT_LLM_NL_ASSERTIONS = model
nl.DEFAULT_LLM_NL_ASSERTIONS_ARGS = args.copy()
env = AgentGymEnv(
    domain=config["domain"],
    task_id=config["task_id"],
    max_steps=50,
    solo_mode=config.get("synthetic", False),
    user_llm=model,
    user_llm_args=args,
)
initial = None
finished = False
steps = 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        global initial, finished, steps
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 100000:
                raise ValueError("Action too large")
            body = json.loads(self.rfile.read(size) or "{}")
            if self.path == "/reset":
                if initial is None:
                    observation, info = env.reset(seed=config["seed"])
                    if not observation and not config.get("synthetic"):
                        raise RuntimeError("Simulator failed to initialize")
                    initial = {
                        "observation": observation,
                        "policy": info["policy"],
                        "tools": [t.openai_schema for t in info["tools"]],
                    }
                result = initial
            elif self.path == "/step" and initial is not None and not finished:
                steps += 1
                if steps > 50:
                    raise RuntimeError("Step limit exceeded")
                observation, reward, terminated, truncated, info = env.step(body["action"])
                finished = terminated or truncated
                if finished:
                    simulation = json.loads(info.get("simulation_run") or "{}")
                    if not simulation:
                        raise RuntimeError("Missing official simulation evidence")
                    evidence = {
                        "correct": reward == 1,
                        "value": reward,
                        "metric": "success_rate",
                        "steps": steps,
                        "simulation": simulation,
                        "truncated": truncated,
                    }
                    Path("/evidence/result.json").write_text(json.dumps(evidence))
                result = {
                    "observation": observation,
                    "terminated": terminated,
                    "truncated": truncated,
                }
            else:
                raise ValueError("Unavailable operation")
            payload = json.dumps(result).encode()
            self.send_response(200)
        except Exception as exc:
            payload = json.dumps({"error": type(exc).__name__}).encode()
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)


HTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
