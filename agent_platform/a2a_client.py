"""Agent2Agent (A2A v1.0) client: each URL in A2A_PEERS becomes one extra reviewer."""

from __future__ import annotations

import json
import urllib.request
import uuid
from typing import Any

from .models import PipelineConfig

A2A_PROTOCOL_VERSION = "1.0"
AGENT_CARD_PATH = "/.well-known/agent-card.json"
A2A_PATH = "/a2a"


class A2AClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.card: dict[str, Any] | None = None

    def get_json(self, url: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json", "A2A-Version": A2A_PROTOCOL_VERSION}
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read())

    def fetch_card(self) -> dict[str, Any]:
        if self.card is None:
            self.card = self.get_json(self.base_url + AGENT_CARD_PATH)
        return self.card

    def endpoint(self) -> str:
        for interface in self.fetch_card().get("supportedInterfaces", []):
            if interface.get("protocolBinding") == "JSONRPC":
                return str(interface["url"])
        raise RuntimeError(f"{self.base_url} has no JSON-RPC interface")

    def send_message(self, text: str, data: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "SendMessage",
            "params": {
                "message": {
                    "messageId": uuid.uuid4().hex,
                    "role": "ROLE_USER",
                    "parts": [{"text": text}, {"data": data, "mediaType": "application/json"}],
                }
            },
        }
        response = self.get_json(self.endpoint(), payload)
        if "error" in response:
            raise RuntimeError(f"A2A error from {self.base_url}: {response['error'].get('message')}")
        result = response.get("result", {})
        if "task" not in result:
            raise RuntimeError(f"A2A peer {self.base_url} returned no task")
        return result["task"]


def review_from_task(name: str, task: dict[str, Any]) -> dict[str, Any]:
    """Collapse a peer's review artifact into one review: lowest score wins, issues and patches merged."""
    state = task.get("status", {}).get("state")
    if state != "TASK_STATE_COMPLETED":
        raise RuntimeError(f"peer task ended in {state}")
    reviews: list[dict[str, Any]] = []
    for artifact in task.get("artifacts", []):
        for part in artifact.get("parts", []):
            if isinstance(part.get("data"), dict):
                reviews.extend(r for r in part["data"].get("reviews", []) if isinstance(r, dict))
    if not reviews:
        raise RuntimeError("peer returned no reviews")
    scores = []
    for review in reviews:
        try:
            scores.append(int(review.get("score", 0)))
        except (TypeError, ValueError):
            scores.append(0)
    return {
        "persona": name,
        "score": min(scores),
        "status": "pass" if all(r.get("status") == "pass" for r in reviews) else "needs_work",
        "confidence": "medium",
        "issues": [issue for r in reviews for issue in r.get("issues", []) if isinstance(issue, dict)],
        "patches": [patch for r in reviews for patch in r.get("patches", []) if isinstance(patch, dict)],
        "peer_reviews": [{"persona": r.get("persona"), "score": r.get("score")} for r in reviews],
    }


class RemoteReviewer:
    """Reviewer backed by another A2A agent (e.g. a second AutoCode on a different model)."""

    def __init__(self, url: str, timeout_seconds: float) -> None:
        self.client = A2AClient(url, timeout_seconds)
        self.name = peer_name(url)

    def run(
        self,
        requirement: str,
        project_memory: dict[str, Any],
        relevant_files: dict[str, str],
        dimension_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = self.client.send_message(f"Review these files for this requirement:\n{requirement}",
                                        {"skill": "review", "files": relevant_files})
        return review_from_task(self.name, task)


def peer_name(url: str) -> str:
    return f"Peer {url.split('://')[-1].rstrip('/')}"


def peer_urls(config: PipelineConfig) -> list[str]:
    return [url.strip() for url in config.a2a_peers.split(",") if url.strip()]

