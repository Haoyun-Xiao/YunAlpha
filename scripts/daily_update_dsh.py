"""Run YunAlpha's single-model, non-interactive daily update decision."""
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen
from uuid import uuid4


MODEL = "opencode-go/deepseek-v4.1-flash"


def wait_for_proxy(url):
    deadline = time.monotonic() + 90
    while True:
        try:
            with urlopen(f"{url}/readyz", timeout=3) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("OpenCodex 未就绪。检查 com.opencodex.proxy 后重试。")
        time.sleep(3)


def profile(state, config):
    if config["model"] != MODEL:
        raise RuntimeError(f"日更模型必须固定为 {MODEL}")
    home = state / "dsh"
    home.mkdir(parents=True, exist_ok=True)
    settings = {
        "agent-default-model": {"provider": "opencodex", "model": MODEL, "reasoningEffort": "high"},
        "llm-pi-ai": {
            "providers": {
                "opencodex": {
                    "api": "openai-responses",
                    "baseURL": config["opencodex_url"] + "/v1",
                    "apiKeyEnv": "YUNALPHA_DSH_PROXY_KEY",
                    "models": [{
                        "id": MODEL, "contextWindow": 262144,
                        "reasoningEfforts": {"high": "high"},
                    }],
                }
            }
        },
    }
    target = home / "settings.yaml"
    target.write_text(json.dumps(settings, indent=2) + "\n")
    return home


def proxy_receipt(config, session, *, started):
    result = subprocess.run(
        [config["opencodex"], "logs", "--conversation", session, "--limit", "15", "--json"],
        capture_output=True, text=True, check=True, timeout=15,
    )
    correlation = sha256(session.encode()).hexdigest()[:32]
    for row in json.loads(result.stdout)["logs"]:
        if row["timestamp"] < started or row.get("conversationId") != correlation:
            continue
        if row["requestedModel"] != MODEL or row["provider"] != "opencode-go":
            raise RuntimeError("实际代理路由与固定的 DS 模型不一致")
        served = row.get("servedModel") or row.get("resolvedModel") or ""
        if served.removeprefix("opencode-go/") != MODEL.split("/", 1)[1] or row["status"] != 200:
            raise RuntimeError(f"DS 路由调用未成功 ({row['status']})")
        return {"request_id": row["requestId"], "provider": row["provider"], "model": served}
    raise RuntimeError("OpenCodex 日志中未找到本次 DS 路由回执")


def decide(root, state, config, context, log):
    wait_for_proxy(config["opencodex_url"])
    marker = f"yunalpha-daily-{uuid4().hex}"
    home = profile(state, config)
    previous_sessions = set(home.glob("sessions/*/session-*"))
    prompt = (
        f"任务 ID {marker}。你是 YunAlpha 日更的执行决策模型。"
        "下面是本轮真实检查结果和预先登记的可执行动作。"
        "按当前日期、已发布数据、尚待披露项和既有故障记录，选择必要动作。"
        "默认选全部 required_actions，除非提供一个具体且可核对的技术原因需要暂缓。"
        "按给定顺序返回动作 ID，不增加命令。desks 动作会由程序在行情前置条件满足后执行。"
        "无需调用工具。只返回 JSON 对象，字段 actions 是动作 ID 数组，note 是简短中文结论。\n"
        + json.dumps(context, ensure_ascii=False)
    )
    started = int(time.time() * 1000)
    dsh = Path(config["dsh"])
    env = {**os.environ, "DSH_HOME": str(home)}
    # The loopback listener currently accepts local callers. An authenticated
    # deployment can inject an actual key into this process without editing Git.
    env.setdefault("YUNALPHA_DSH_PROXY_KEY", "yunalpha-loopback")
    with log.open("a") as output:
        result = subprocess.run(
            [str(dsh), "--profile", "headless", "--patch",
             str(root / "config/daily-update-dsh.patch.yml"), prompt],
            cwd=home, env=env, text=True, stdout=subprocess.PIPE, stderr=output, timeout=180,
        )
    if result.returncode:
        raise RuntimeError(f"DSH 退出码 {result.returncode}，详情见 {log}")
    sessions = set(home.glob("sessions/*/session-*")) - previous_sessions
    if len(sessions) != 1:
        raise RuntimeError("未取得本次 DSH 会话 ID")
    receipt = proxy_receipt(config, sessions.pop().name, started=started)
    raw = result.stdout.strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("\n", 1)[0]
    answer = json.loads(raw)
    actions, note = answer["actions"], answer["note"]
    allowed = context["required_actions"]
    if (not isinstance(actions, list) or not all(isinstance(a, str) for a in actions)
            or actions != [action for action in allowed if action in actions]
            or not isinstance(note, str) or not note.strip()):
        raise RuntimeError("DS 返回的动作不符合本次运行入口约定")
    return {**receipt, "actions": actions, "note": note}
