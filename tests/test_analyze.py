from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.analyze import analyze, projects, skills
from app.contracts import (
    CommitSummary,
    GitHubSnapshot,
    IssueSummary,
    LanguageShare,
    PrSummary,
    RepoActivity,
    ViewerIdentity,
)

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
ALICE = ViewerIdentity(login="alice", aliases=["alice"])


def when(days: int) -> datetime:
    return NOW - timedelta(days=days)


def a_commit(sha: str, message: str, author: str = "alice", days: int = 10) -> CommitSummary:
    return CommitSummary(
        sha=sha,
        message=message,
        author=author,
        author_email=f"{author}@example.com",
        committed_at=when(days),
        mine=author == "alice",
    )


def a_repo(name: str = "demo", **overrides) -> RepoActivity:
    data = {
        "owner": "acme",
        "name": name,
        "default_branch": "main",
        "head_sha": "a" * 12,
        "description": "무언가를 하는 서비스",
        "html_url": f"https://github.com/acme/{name}",
        "topics": ["fastapi", "postgres", "hackathon"],
        "languages": [LanguageShare(name="Python", bytes=9000)],
        "manifest_files": ["pyproject.toml", "Dockerfile"],
        "commits": [a_commit("a" * 12, "feat: 결제 API 구현")],
    }
    data.update(overrides)
    return RepoActivity(**data)


def snapshot(*repos: RepoActivity, viewer: str | None = "alice") -> GitHubSnapshot:
    return GitHubSnapshot(
        collected_at=NOW,
        complete=True,
        snapshot_digest="d" * 64,
        viewer_login=viewer,
        repos=list(repos),
    )


def test_manifest_filename_is_never_a_skill() -> None:
    facts = skills.extract([a_repo()])
    names = {f.name for f in facts}

    assert "pyproject.toml" not in names
    assert "Dockerfile" not in names
    assert "Docker" in names
    assert {"Python", "FastAPI", "PostgreSQL"} <= names
    assert "hackathon" not in names


def test_language_and_topic_of_the_same_tech_collapse() -> None:
    repo = a_repo(topics=["python", "fastapi"])
    facts = skills.extract([repo])
    assert [f.name for f in facts].count("Python") == 1
    python = next(f for f in facts if f.name == "Python")
    assert python.category == "language"
    assert python.measured is True


def test_trace_languages_are_dropped() -> None:
    repo = a_repo(
        languages=[
            LanguageShare(name="Python", bytes=90_000),
            LanguageShare(name="TypeScript", bytes=8_000),
            LanguageShare(name="CSS", bytes=1_500),
            LanguageShare(name="Makefile", bytes=3),
        ]
    )
    names = [f.name for f in skills.extract([repo]) if f.category == "language"]
    assert names == ["Python", "TypeScript"]


def test_a_single_language_repo_still_reports_it() -> None:
    repo = a_repo(languages=[LanguageShare(name="Rust", bytes=12)])
    assert [f.name for f in skills.extract([repo]) if f.category == "language"] == ["Rust"]


def test_only_my_commits_are_counted() -> None:
    repo = a_repo(
        commits=[
            a_commit("a" * 12, "feat: 내 작업"),
            a_commit("b" * 12, "feat: 남의 작업", author="bob"),
        ]
    )
    facts = projects.facts_of(repo, ALICE, now=NOW)

    assert facts.my_commits == 1
    assert facts.total_commits == 2
    assert facts.contributors == 2
    assert facts.team is True


def test_only_my_merged_prs_are_counted() -> None:
    """팀 레포에서 남이 머지한 PR이 내 이력서의 성과로 올라가면 안 된다."""
    repo = a_repo(
        pull_requests=[
            PrSummary(number=1, title="내 PR", state="closed", merged=True, author="alice"),
            PrSummary(number=2, title="남의 PR", state="closed", merged=True, author="bob"),
            PrSummary(number=3, title="내 열린 PR", state="open", merged=False, author="alice"),
        ]
    )
    facts = projects.facts_of(repo, ALICE, now=NOW)

    assert facts.merged_prs == 1


def test_only_issues_i_worked_on_are_counted() -> None:
    """내가 열었거나 나에게 할당된 것만 '해결한 이슈'다."""
    repo = a_repo(
        issues=[
            IssueSummary(number=1, title="내가 연 이슈", state="closed", author="alice"),
            IssueSummary(number=2, title="남의 이슈", state="closed", author="bob"),
            IssueSummary(
                number=3, title="나에게 할당된 이슈", state="closed",
                author="bob", assignees=["alice"],
            ),
            IssueSummary(number=4, title="내 열린 이슈", state="open", author="alice"),
        ]
    )
    facts = projects.facts_of(repo, ALICE, now=NOW)

    assert facts.closed_issues == 2


def test_unattributable_prs_and_issues_are_not_silently_dropped() -> None:
    """작성자 정보가 아예 없으면 구분이 불가능하다. 0으로 만들지 않는다."""
    repo = a_repo(
        pull_requests=[PrSummary(number=1, title="익명 PR", state="closed", merged=True)],
        issues=[IssueSummary(number=2, title="익명 이슈", state="closed")],
    )
    facts = projects.facts_of(repo, ALICE, now=NOW)

    assert facts.merged_prs == 1
    assert facts.closed_issues == 1


def test_without_a_viewer_nothing_is_attributed_away() -> None:
    repo = a_repo(
        pull_requests=[PrSummary(number=1, title="PR", state="closed", merged=True, author="bob")],
        issues=[IssueSummary(number=2, title="이슈", state="closed", author="bob")],
    )
    facts = projects.facts_of(repo, ViewerIdentity(), now=NOW)

    assert facts.merged_prs == 1
    assert facts.closed_issues == 1


def test_unattributable_commits_are_not_silently_dropped() -> None:
    repo = a_repo(
        commits=[
            CommitSummary(sha="c" * 12, message="feat: 익명", committed_at=when(5)),
        ]
    )
    facts = projects.facts_of(repo, ALICE, now=NOW)
    assert facts.my_commits == 1


def test_highlights_prefer_features_over_chores() -> None:
    repo = a_repo(
        commits=[
            a_commit("a" * 12, "chore: 의존성 정리", days=1),
            a_commit("b" * 12, "feat: 결제 API 구현", days=9),
            a_commit("c" * 12, "fix: 잔액 계산 오류 수정", days=5),
        ]
    )
    facts = projects.facts_of(repo, ALICE, now=NOW, max_highlights=3)
    assert [h.change_type for h in facts.highlights][:2] == ["feat", "fix"]
    assert facts.highlights[0].subject == "결제 API 구현"


def test_the_profile_readme_repo_is_not_a_project() -> None:
    """`owner/owner` 는 깃허브 프로필 자기소개다. 커밋이 많아도 후보에 들면 안 된다."""
    profile = a_repo("acme", commits=[a_commit(f"{i:012d}", f"feat: {i}", days=1) for i in range(30)])
    evidence = analyze(snapshot(a_repo("real"), profile), now=NOW)

    assert [p.repo for p in evidence.projects] == ["acme/real"]
    assert "acme/acme" not in [p.repo for p in evidence.selection_candidates]


def test_readme_and_document_commits_are_not_contribution_evidence() -> None:
    """문서 커밋은 기여 근거가 아니다. 파일 이름만 바뀐 커밋도 마찬가지다."""
    repo = a_repo(
        commits=[
            a_commit("a" * 12, "Update README.md", days=1),
            a_commit("b" * 12, "Update 00_대회규정.md", days=2),
            a_commit("c" * 12, "docs: 주석 보강", days=3),
            a_commit("d" * 12, "feat: 결제 API 구현", days=4),
        ]
    )
    facts = projects.facts_of(repo, ALICE, now=NOW)

    assert [h.subject for h in facts.highlights] == ["결제 API 구현"]
    # 개수는 사실이라 그대로 센다. 빼는 것은 내용뿐이다.
    assert facts.my_commits == 4


def test_forks_and_archived_repos_are_excluded() -> None:
    evidence = analyze(
        snapshot(a_repo("real"), a_repo("forked", fork=True), a_repo("old", archived=True)),
        now=NOW,
    )
    assert [p.repo for p in evidence.projects] == ["acme/real"]
    assert any("fork/archived" in w for w in evidence.warnings)


def test_projects_rank_by_contribution_then_truncate() -> None:
    big = a_repo(
        "big",
        commits=[a_commit(f"{i:012d}", f"feat: {i}", days=3) for i in range(10)],
        pull_requests=[PrSummary(number=1, title="merged", state="closed", merged=True)],
        issues=[IssueSummary(number=2, title="done", state="closed")],
    )
    small = a_repo("small", commits=[a_commit("s" * 12, "feat: 하나", days=200)])
    evidence = analyze(snapshot(big, small), max_projects=1, now=NOW)

    assert [p.repo for p in evidence.projects] == ["acme/big"]
    assert any("상위 1개만" in w for w in evidence.warnings)


def test_owned_pr_and_issue_titles_become_work_items() -> None:
    repo = a_repo(
        pull_requests=[
            PrSummary(
                number=4, title="결제 검증", state="closed", merged=True,
                author="alice", updated_at=when(1),
            ),
            PrSummary(
                number=2, title="남의 PR", state="closed", merged=True,
                author="bob", updated_at=when(0),
            ),
            PrSummary(
                number=1, title="예전 작업", state="closed", merged=True,
                author="alice", updated_at=when(20),
            ),
        ],
        issues=[
            IssueSummary(
                number=9, title="내가 닫은 이슈", state="closed",
                author="alice", updated_at=when(2),
            ),
            IssueSummary(
                number=3, title="남의 이슈", state="closed",
                author="bob", updated_at=when(1),
            ),
        ],
    )
    facts = projects.facts_of(repo, ALICE, now=NOW)

    assert [item.id for item in facts.pull_requests] == ["pr:acme/demo#4", "pr:acme/demo#1"]
    assert [item.title for item in facts.pull_requests] == ["결제 검증", "예전 작업"]
    assert [item.id for item in facts.issues] == ["issue:acme/demo#9"]
    assert facts.merged_prs == 2
    assert facts.closed_issues == 1


def test_unattributable_titles_are_not_lifted() -> None:
    repo = a_repo(
        pull_requests=[PrSummary(number=1, title="익명 PR", state="closed", merged=True)],
        issues=[IssueSummary(number=2, title="익명 이슈", state="closed")],
    )
    facts = projects.facts_of(repo, ALICE, now=NOW)

    assert facts.merged_prs == 1
    assert facts.closed_issues == 1
    assert facts.pull_requests == []
    assert facts.issues == []


def test_work_item_ids_are_part_of_evidence() -> None:
    repo = a_repo(
        pull_requests=[
            PrSummary(number=7, title="내 PR", state="closed", merged=True, author="alice"),
        ]
    )
    evidence = analyze(snapshot(repo), now=NOW)
    ids = evidence.ids()

    assert "pr:acme/demo#7" in ids
    assert f"repo:{evidence.projects[0].repo}" in ids
    assert all(s.id in ids for s in evidence.skills)
    assert all(h.id in ids for h in evidence.projects[0].highlights)


def test_work_item_cap_keeps_the_newest_three() -> None:
    repo = a_repo(
        pull_requests=[
            PrSummary(
                number=n, title=f"PR {n}", state="closed", merged=True,
                author="alice", updated_at=when(10 - n),
            )
            for n in range(1, 6)
        ]
    )
    facts = projects.facts_of(repo, ALICE, now=NOW)
    assert [item.number for item in facts.pull_requests] == [5, 4, 3]


def test_evidence_ids_cover_every_rendered_source() -> None:
    evidence = analyze(snapshot(a_repo()), now=NOW)
    ids = evidence.ids()

    assert f"repo:{evidence.projects[0].repo}" in ids
    assert all(s.id in ids for s in evidence.skills)
    assert all(h.id in ids for h in evidence.projects[0].highlights)


def test_missing_viewer_login_is_reported() -> None:
    evidence = analyze(snapshot(a_repo(), viewer=None), now=NOW)
    assert any("사용자 식별" in w for w in evidence.warnings)


def test_period_spans_selected_projects() -> None:
    repo = a_repo(
        commits=[
            a_commit("a" * 12, "feat: 시작", days=120),
            a_commit("b" * 12, "feat: 끝", days=3),
        ]
    )
    evidence = analyze(snapshot(repo), now=NOW)
    assert evidence.period_start == when(120)
    assert evidence.period_end == when(3)
    assert evidence.my_commits == 2
