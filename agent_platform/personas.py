from __future__ import annotations

from .models import Persona


PERSONAS: tuple[Persona, ...] = (
    Persona(
        "Business Logic",
        "Verify that the code solves the user's actual problem and handles core rules correctly.",
        ("requirements", "domain rules", "edge cases", "user-visible behavior"),
        {"requirements": 40, "domain_rules": 30, "edge_cases": 20, "behavior": 10},
    ),
    Persona(
        "Security",
        "Find authentication, authorization, validation, secrets, and vulnerability risks.",
        ("input validation", "injection risks", "secret handling", "auth boundaries"),
        {"validation": 25, "injection": 30, "secrets": 20, "auth": 25},
    ),
    Persona(
        "UI/UX",
        "Assess interaction clarity, accessibility, and workflow ergonomics.",
        ("clear states", "helpful errors", "accessibility", "workflow friction"),
        {"clarity": 30, "errors": 20, "accessibility": 25, "workflow": 25},
    ),
    Persona(
        "Performance",
        "Find slow paths, inefficient resource use, and avoidable latency.",
        ("expensive loops", "blocking operations", "unnecessary calls", "memory usage"),
        {"latency": 35, "resource_usage": 25, "algorithmic_cost": 25, "caching": 15},
    ),
    Persona(
        "Reliability & Availability",
        "Check failure handling, consistency, retries, and fallback behavior.",
        ("timeouts", "retries", "error handling", "partial failures"),
        {"error_handling": 35, "timeouts": 20, "retries": 20, "fallbacks": 25},
    ),
    Persona(
        "Maintainability",
        "Improve code clarity, modularity, naming, and future debugging.",
        ("simple structure", "clear names", "low duplication", "small functions"),
        {"structure": 35, "naming": 20, "duplication": 25, "debuggability": 20},
    ),
    Persona(
        "Scalability",
        "Evaluate whether the design can handle more users, data, or jobs without redesign.",
        ("state isolation", "queue readiness", "concurrency", "storage growth"),
        {"state": 25, "concurrency": 30, "storage": 25, "scale_path": 20},
    ),
    Persona(
        "Data Management",
        "Inspect persistence, schema choices, data consistency, backups, and integrity.",
        ("schema correctness", "data validation", "transactions", "migration readiness"),
        {"schema": 30, "integrity": 30, "transactions": 20, "migration": 20},
    ),
    Persona(
        "Testing & Quality Assurance",
        "Identify missing tests, weak verification, and CI/CD quality gaps.",
        ("unit tests", "integration tests", "testable boundaries", "CI checks"),
        {"unit": 30, "integration": 25, "coverage": 25, "ci": 20},
    ),
    Persona(
        "Observability & Monitoring",
        "Check logging, metrics, tracing, alerts, and production diagnosis readiness.",
        ("structured logs", "metrics", "tracing", "alerting"),
        {"logs": 30, "metrics": 25, "tracing": 20, "alerts": 25},
    ),
)

