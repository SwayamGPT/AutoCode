from __future__ import annotations

from .models import Persona

# The ten quality dimensions. Order matters: planning and implementation run in this
# sequence, so Business Logic lays the foundation and later dimensions build on it.
PERSONAS: tuple[Persona, ...] = (
    Persona(
        "Business Logic",
        "The core functionality and rules that solve the user's problem.",
        ("requirements fully covered", "domain rules enforced", "edge cases handled", "correct user-visible behavior"),
        {"requirements": 40, "domain_rules": 30, "edge_cases": 20, "behavior": 10},
    ),
    Persona(
        "Security",
        "Authentication, authorization, encryption, input validation, and protection against vulnerabilities.",
        (
            "authentication",
            "authorization boundaries",
            "encryption of secrets and data in transit/at rest",
            "input validation",
            "injection and OWASP Top 10 protection",
            "no hardcoded secrets",
        ),
        {"authentication": 15, "authorization": 20, "encryption": 15, "validation": 25, "vulnerabilities": 25},
    ),
    Persona(
        "UI/UX",
        "How users interact with the application and how intuitive and pleasant it is to use.",
        ("clear states and feedback", "helpful error messages", "accessibility", "low workflow friction"),
        {"clarity": 30, "errors": 20, "accessibility": 25, "workflow": 25},
    ),
    Persona(
        "Performance",
        "Fast response times, efficient resource usage, and scalability under load.",
        ("response latency", "efficient CPU and memory use", "algorithmic cost", "caching and batching", "behavior under load"),
        {"latency": 30, "resource_usage": 25, "algorithmic_cost": 25, "load": 20},
    ),
    Persona(
        "Reliability & Availability",
        "The system works consistently, handles failures gracefully, and has high uptime.",
        ("error handling", "timeouts", "retries with backoff", "graceful degradation and fallbacks", "health checks"),
        {"error_handling": 30, "timeouts": 20, "retries": 15, "fallbacks": 20, "uptime": 15},
    ),
    Persona(
        "Maintainability",
        "Clean, modular, and well-documented code that is easy to update and debug.",
        ("modular structure", "clear names", "low duplication", "small functions", "useful documentation"),
        {"structure": 30, "naming": 15, "duplication": 20, "debuggability": 15, "documentation": 20},
    ),
    Persona(
        "Scalability",
        "The ability to handle increasing users, data, and traffic without major redesign.",
        ("stateless or isolated state", "horizontal scaling path", "concurrency safety", "data growth", "queue readiness"),
        {"state": 25, "concurrency": 25, "data_growth": 25, "scale_path": 25},
    ),
    Persona(
        "Data Management",
        "Efficient database design, data integrity, backups, and consistency.",
        ("schema design", "data integrity and validation", "transactions and consistency", "backups and recovery", "migrations"),
        {"schema": 25, "integrity": 25, "consistency": 20, "backups": 15, "migrations": 15},
    ),
    Persona(
        "Testing & Quality Assurance",
        "Unit, integration, end-to-end testing, code reviews, and CI/CD to ensure correctness.",
        ("unit tests", "integration tests", "end-to-end tests", "testable boundaries", "CI/CD pipeline"),
        {"unit": 30, "integration": 20, "e2e": 15, "coverage": 15, "ci_cd": 20},
    ),
    Persona(
        "Observability & Monitoring",
        "Logging, metrics, tracing, and alerting to detect and diagnose issues in production.",
        ("structured logging", "metrics", "tracing and correlation ids", "alerting", "health and readiness endpoints"),
        {"logging": 30, "metrics": 25, "tracing": 20, "alerting": 25},
    ),
)
