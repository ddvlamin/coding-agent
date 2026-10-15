"""Unit tests for reporting and email digest dispatch."""

from unittest.mock import MagicMock
import pytest

from src.config import SMTPConfig
from src.pipeline.stage1_comments import Stage1Result
from src.pipeline.stage2_merger import Stage2Result
from src.pipeline.stage3_issues import Stage3Result
from src.pipeline.stage4_ideation import Stage4Result
from src.reporting.email_digest import EmailDigestService, PipelineRunSummary, RepoRunReport


def test_pipeline_summary_metrics():
    summary = PipelineRunSummary(
        duration_seconds=12.5,
        repo_reports=[
            RepoRunReport(
                repo_name="org/repo-a",
                stage1=Stage1Result(prs_fixed=[1, 2], resolved_threads=4),
                stage2=Stage2Result(prs_merged=[10]),
                stage3=Stage3Result(prs_created=[(5, 105)]),
                stage4=Stage4Result(proposals_created=[200, 201]),
            )
        ],
    )
    assert summary.total_fixed == 2
    assert summary.total_merged == 1
    assert summary.total_prs_created == 1
    assert summary.total_proposals == 2


def test_email_digest_rendering():
    cfg = SMTPConfig(user="bot@example.com", password="app-password")
    service = EmailDigestService(cfg)

    summary = PipelineRunSummary(
        duration_seconds=45.2,
        repo_reports=[
            RepoRunReport(
                repo_name="org/service",
                stage1=Stage1Result(prs_fixed=[101]),
                stage2=Stage2Result(prs_merged=[102]),
                stage3=Stage3Result(prs_created=[(7, 103)]),
                stage4=Stage4Result(proposals_created=[104]),
            )
        ],
    )

    html = service.render_html(summary)
    assert "Weekly Maintenance Agent Digest" in html
    assert "org/service" in html
    assert "PR #101" in html

    text = service.render_text(summary)
    assert "WEEKLY MAINTENANCE AGENT RUN DIGEST" in text
    assert "[org/service]" in text


def test_smtp_send_digest(monkeypatch):
    cfg = SMTPConfig(
        host="smtp.gmail.com",
        port=587,
        user="test@gmail.com",
        password="secretpassword",
        to_emails=["maintainer@gmail.com"],
        enabled=True,
    )
    service = EmailDigestService(cfg)
    summary = PipelineRunSummary()

    mock_smtp_instance = MagicMock()
    mock_smtp_class = MagicMock(return_value=mock_smtp_instance)
    mock_smtp_instance.__enter__.return_value = mock_smtp_instance

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", mock_smtp_class)

    sent = service.send_digest(summary)
    assert sent is True
    mock_smtp_instance.starttls.assert_called_once()
    mock_smtp_instance.login.assert_called_once_with("test@gmail.com", "secretpassword")
    mock_smtp_instance.sendmail.assert_called_once()
