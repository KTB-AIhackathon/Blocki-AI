"""Skill extraction.

Only three things count as evidence: measured language bytes, repository
topics that name a known technology, and manifest files that imply a toolchain.
A manifest *filename* is never itself a skill.
"""

from __future__ import annotations

from app.contracts import RepoActivity, SkillCategory, SkillFact

CATEGORY_ORDER: tuple[SkillCategory, ...] = (
    "language",
    "framework",
    "database",
    "infra",
    "tool",
)
CATEGORY_LABELS: dict[SkillCategory, str] = {
    "language": "Languages",
    "framework": "Frameworks",
    "database": "Database",
    "infra": "Infrastructure",
    "tool": "Tools",
}

# Topics are free-form user tags. Only names we recognise become skills;
# "hackathon", "study", "toy-project" and friends are dropped on purpose.
TOPIC_TECH: dict[str, tuple[str, SkillCategory]] = {
    "fastapi": ("FastAPI", "framework"),
    "django": ("Django", "framework"),
    "flask": ("Flask", "framework"),
    "spring": ("Spring", "framework"),
    "springboot": ("Spring Boot", "framework"),
    "spring-boot": ("Spring Boot", "framework"),
    "spring-security": ("Spring Security", "framework"),
    "jpa": ("JPA", "framework"),
    "hibernate": ("Hibernate", "framework"),
    "react": ("React", "framework"),
    "reactjs": ("React", "framework"),
    "nextjs": ("Next.js", "framework"),
    "next-js": ("Next.js", "framework"),
    "vue": ("Vue", "framework"),
    "vuejs": ("Vue", "framework"),
    "svelte": ("Svelte", "framework"),
    "angular": ("Angular", "framework"),
    "express": ("Express", "framework"),
    "nestjs": ("NestJS", "framework"),
    "tailwindcss": ("Tailwind CSS", "framework"),
    "langchain": ("LangChain", "framework"),
    "langgraph": ("LangGraph", "framework"),
    "pytorch": ("PyTorch", "framework"),
    "tensorflow": ("TensorFlow", "framework"),
    "postgres": ("PostgreSQL", "database"),
    "postgresql": ("PostgreSQL", "database"),
    "mysql": ("MySQL", "database"),
    "mariadb": ("MariaDB", "database"),
    "mongodb": ("MongoDB", "database"),
    "redis": ("Redis", "database"),
    "sqlite": ("SQLite", "database"),
    "elasticsearch": ("Elasticsearch", "database"),
    "docker": ("Docker", "infra"),
    "kubernetes": ("Kubernetes", "infra"),
    "k8s": ("Kubernetes", "infra"),
    "aws": ("AWS", "infra"),
    "gcp": ("GCP", "infra"),
    "azure": ("Azure", "infra"),
    "terraform": ("Terraform", "infra"),
    "nginx": ("Nginx", "infra"),
    "github-actions": ("GitHub Actions", "infra"),
    "ci-cd": ("CI/CD", "infra"),
    "cicd": ("CI/CD", "infra"),
    "kafka": ("Kafka", "infra"),
    "rabbitmq": ("RabbitMQ", "infra"),
    "graphql": ("GraphQL", "tool"),
    "grpc": ("gRPC", "tool"),
    "websocket": ("WebSocket", "tool"),
    "stomp": ("STOMP", "tool"),
    "rest-api": ("REST API", "tool"),
    "restapi": ("REST API", "tool"),
    "jwt": ("JWT", "tool"),
    "oauth": ("OAuth", "tool"),
    "oauth2": ("OAuth", "tool"),
    "pytest": ("pytest", "tool"),
    "jest": ("Jest", "tool"),
    "junit": ("JUnit", "tool"),
    "mcp": ("MCP", "tool"),
}

# A manifest proves a toolchain, not a language we did not already measure.
MANIFEST_TECH: dict[str, tuple[str, SkillCategory]] = {
    "package.json": ("Node.js", "framework"),
    "pom.xml": ("Maven", "tool"),
    "build.gradle": ("Gradle", "tool"),
    "build.gradle.kts": ("Gradle", "tool"),
    "Dockerfile": ("Docker", "infra"),
    "docker-compose.yml": ("Docker Compose", "infra"),
}

LANGUAGE_ALIASES: dict[str, str] = {
    "c#": "C#",
    "c++": "C++",
    "objective-c": "Objective-C",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "python": "Python",
    "java": "Java",
    "kotlin": "Kotlin",
    "go": "Go",
    "golang": "Go",
    "rust": "Rust",
    "ruby": "Ruby",
    "php": "PHP",
    "swift": "Swift",
    "html": "HTML",
    "css": "CSS",
    "shell": "Shell",
    "dockerfile": "Docker",
}
# A few hundred bytes of HTML in a Python service is not a skill. Below this
# share a language is dropped, except for the single largest one so that a
# one-language repository still reports something.
MINOR_LANGUAGE_SHARE = 0.05
KEEP_AT_LEAST = 1


class _Bucket:
    __slots__ = ("name", "category", "byte_count", "repos", "sources")

    def __init__(self, name: str, category: SkillCategory) -> None:
        self.name = name
        self.category = category
        self.byte_count = 0
        self.repos: list[str] = []
        self.sources: list[str] = []

    def add(self, repo: str, source: str, byte_count: int = 0) -> None:
        self.byte_count += byte_count
        if repo not in self.repos:
            self.repos.append(repo)
        if source not in self.sources:
            self.sources.append(source)


def extract(repos: list[RepoActivity]) -> list[SkillFact]:
    buckets: dict[str, _Bucket] = {}
    for repo in repos:
        for language in repo.languages:
            name = canonical_language(language.name)
            if name:
                _bucket(buckets, name, "language").add(
                    repo.full_name, "language", max(language.bytes, 0)
                )
        for topic in repo.topics:
            mapped = TOPIC_TECH.get(topic.strip().casefold())
            if mapped:
                _bucket(buckets, mapped[0], mapped[1]).add(repo.full_name, "topic")
        for manifest in repo.manifest_files:
            mapped = MANIFEST_TECH.get(manifest)
            if mapped:
                _bucket(buckets, mapped[0], mapped[1]).add(
                    repo.full_name, f"manifest:{manifest}"
                )

    facts = _weigh(list(buckets.values()), repo_count=max(len(repos), 1))
    facts.sort(key=lambda s: (CATEGORY_ORDER.index(s.category), -s.weight, s.name.casefold()))
    return facts


def group_by_category(skills: list[SkillFact]) -> list[tuple[str, list[SkillFact]]]:
    grouped: list[tuple[str, list[SkillFact]]] = []
    for category in CATEGORY_ORDER:
        members = [s for s in skills if s.category == category]
        if members:
            grouped.append((CATEGORY_LABELS[category], members))
    return grouped


def canonical_language(raw: str) -> str | None:
    name = (raw or "").strip()
    if not name:
        return None
    return LANGUAGE_ALIASES.get(name.casefold(), name)


def _bucket(buckets: dict[str, _Bucket], name: str, category: SkillCategory) -> _Bucket:
    # Dedupe on the display name so a "Python" language and a "python" topic
    # collapse into one entry instead of two bullets.
    key = name.casefold()
    existing = buckets.get(key)
    if existing is None:
        existing = _Bucket(name, category)
        buckets[key] = existing
    elif category == "language":
        existing.category = "language"
    return existing


def _weigh(buckets: list[_Bucket], *, repo_count: int) -> list[SkillFact]:
    languages = [b for b in buckets if b.category == "language"]
    total_bytes = sum(b.byte_count for b in languages)
    facts: list[SkillFact] = []
    for bucket in buckets:
        measured = bucket.category == "language" and total_bytes > 0
        if measured:
            weight = bucket.byte_count / total_bytes
        else:
            weight = len(bucket.repos) / repo_count
            if bucket.category != "language":
                weight *= 0.8
        facts.append(
            SkillFact(
                id=f"skill:{bucket.name.casefold()}",
                name=bucket.name,
                category=bucket.category,
                weight=round(weight, 4),
                measured=measured,
                repos=list(bucket.repos),
                sources=list(bucket.sources),
            )
        )
    return _drop_trace_languages(facts, total_bytes)


def _drop_trace_languages(facts: list[SkillFact], total_bytes: int) -> list[SkillFact]:
    if total_bytes <= 0:
        return facts
    languages = [f for f in facts if f.category == "language"]
    if len(languages) <= KEEP_AT_LEAST:
        return facts
    ranked = sorted(languages, key=lambda f: -f.weight)
    survivors = {f.id for f in ranked[:KEEP_AT_LEAST]}
    survivors |= {f.id for f in ranked if f.weight >= MINOR_LANGUAGE_SHARE}
    return [f for f in facts if f.category != "language" or f.id in survivors]
