from datetime import datetime, timezone

from app.artifacts import build_artifact
from app.contracts import (
    CommitSummary,
    GitHubSnapshot,
    IssueSummary,
    JobRequest,
    PrSummary,
    RepoActivity,
)


def _snapshot(*, complete: bool = True, repos: list[RepoActivity] | None = None) -> GitHubSnapshot:
    return GitHubSnapshot(
        collected_at=datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc),
        complete=complete,
        snapshot_digest="a" * 64,
        viewer_login="alice",
        repos=list(repos or []),
    )


def _job() -> JobRequest:
    return JobRequest(job_id="job-progress", user_id="user-1", job_type="progress_summary")


def test_progress_no_change_on_empty_complete_snapshot() -> None:
    proposal = build_artifact(_snapshot(complete=True, repos=[]), _job())

    assert proposal.status == "no_change"
    assert proposal.kind == "progress"
    assert proposal.body_markdown == ""
    assert proposal.proposed_action is None
    assert proposal.action_digest is None
    assert proposal.proposal_digest
    assert proposal.job_id == "job-progress"
    assert proposal.error is None


def test_progress_proposed_korean_memo_from_dated_activity() -> None:
    repos = [
        RepoActivity(
            owner="acme",
            name="demo",
            commits=[
                CommitSummary(
                    sha="abc1234deadbeef",
                    message="로그인 수정\n\n상세",
                    committed_at=datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc),
                )
            ],
            issues=[
                IssueSummary(
                    number=3,
                    title="버그 제보",
                    state="open",
                    updated_at=datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc),
                )
            ],
            pull_requests=[
                PrSummary(
                    number=2,
                    title="기능 추가",
                    state="merged",
                    updated_at=datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc),
                )
            ],
        )
    ]
    proposal = build_artifact(_snapshot(repos=repos), _job())

    assert proposal.status == "proposed"
    assert proposal.kind == "progress"
    assert proposal.proposed_action is None
    assert "2026-08-18" in proposal.body_markdown
    assert "2026-08-17" in proposal.body_markdown
    assert "로그인 수정" in proposal.body_markdown
    assert "버그 제보" in proposal.body_markdown
    assert "기능 추가" in proposal.body_markdown
    assert proposal.body_markdown.index("2026-08-18") < proposal.body_markdown.index("2026-08-17")


def test_progress_partial_when_incomplete_with_activity() -> None:
    repos = [
        RepoActivity(
            owner="acme",
            name="demo",
            commits=[
                CommitSummary(
                    sha="fff",
                    message="부분 수집",
                    committed_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
                )
            ],
        )
    ]
    proposal = build_artifact(_snapshot(complete=False, repos=repos), _job())

    assert proposal.status == "partial"
    assert "부분 수집" in proposal.body_markdown
    assert proposal.proposed_action is None
