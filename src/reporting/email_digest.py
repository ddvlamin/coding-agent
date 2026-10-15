"""Email digest generator and Gmail SMTP dispatcher."""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

from src.config import SMTPConfig
from src.pipeline.stage1_comments import Stage1Result
from src.pipeline.stage2_merger import Stage2Result
from src.pipeline.stage3_issues import Stage3Result
from src.pipeline.stage4_ideation import Stage4Result

logger = logging.getLogger(__name__)


@dataclass
class RepoRunReport:
    repo_name: str
    stage1: Stage1Result = field(default_factory=Stage1Result)
    stage2: Stage2Result = field(default_factory=Stage2Result)
    stage3: Stage3Result = field(default_factory=Stage3Result)
    stage4: Stage4Result = field(default_factory=Stage4Result)
    errors: list[str] = field(default_factory=list)


@dataclass
class PipelineRunSummary:
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    duration_seconds: float = 0.0
    repo_reports: list[RepoRunReport] = field(default_factory=list)
    dry_run: bool = False

    @property
    def total_fixed(self) -> int:
        return sum(len(r.stage1.prs_fixed) for r in self.repo_reports)

    @property
    def total_merged(self) -> int:
        return sum(len(r.stage2.prs_merged) for r in self.repo_reports)

    @property
    def total_prs_created(self) -> int:
        return sum(len(r.stage3.prs_created) for r in self.repo_reports)

    @property
    def total_proposals(self) -> int:
        return sum(len(r.stage4.proposals_created) for r in self.repo_reports)


class EmailDigestService:
    """Renders and delivers run reports to Gmail via SMTP with TLS."""

    def __init__(self, config: SMTPConfig) -> None:
        self.config = config
        template_dir = Path(__file__).parent
        self.jinja_env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)

    def render_html(self, summary: PipelineRunSummary) -> str:
        template = self.jinja_env.get_template("template.html")
        return template.render(
            timestamp=summary.timestamp,
            duration_seconds=round(summary.duration_seconds, 1),
            repo_reports=summary.repo_reports,
            dry_run=summary.dry_run,
            total_fixed=summary.total_fixed,
            total_merged=summary.total_merged,
            total_prs_created=summary.total_prs_created,
            total_proposals=summary.total_proposals,
        )

    def render_text(self, summary: PipelineRunSummary) -> str:
        lines = [
            "=" * 60,
            "WEEKLY MAINTENANCE AGENT RUN DIGEST",
            f"Timestamp: {summary.timestamp}",
            f"Duration: {round(summary.duration_seconds, 1)}s | Repos: {len(summary.repo_reports)}",
            f"Dry Run: {summary.dry_run}",
            "=" * 60,
            f"PRs Fixed: {summary.total_fixed} | PRs Merged: {summary.total_merged} | PRs Created: {summary.total_prs_created} | Proposals: {summary.total_proposals}",
            "-" * 60,
        ]
        for r in summary.repo_reports:
            lines.append(f"\n[{r.repo_name}]")
            if r.stage1.prs_fixed:
                lines.append(f"  Fixed PRs: {r.stage1.prs_fixed} ({r.stage1.resolved_threads} threads)")
            if r.stage2.prs_merged:
                lines.append(f"  Merged PRs: {r.stage2.prs_merged}")
            if r.stage3.prs_created:
                lines.append(f"  Implemented Issues -> PRs: {r.stage3.prs_created}")
            if r.stage4.proposals_created:
                lines.append(f"  Proposals Filed: {r.stage4.proposals_created}")
            if r.errors:
                lines.append(f"  Errors: {r.errors}")
        lines.append("\n" + "=" * 60)
        return "\n".join(lines)

    def send_digest(self, summary: PipelineRunSummary) -> bool:
        if not self.config.enabled:
            logger.info("Email notifications disabled via config (SMTP_ENABLED=false).")
            return False

        if not self.config.user or not self.config.password:
            logger.warning("SMTP user or password not configured. Skipping email dispatch.")
            return False

        to_addrs = self.config.to_emails or [self.config.user]
        from_addr = self.config.from_email or self.config.user

        subject = (
            f"🤖 [Agent Report] Maintenance Run Summary - {summary.total_merged} Merged, "
            f"{summary.total_prs_created} PRs Opened ({'DRY RUN' if summary.dry_run else 'SUCCESS'})"
        )

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(to_addrs)

        part_text = MIMEText(self.render_text(summary), "plain", "utf-8")
        part_html = MIMEText(self.render_html(summary), "html", "utf-8")
        msg.attach(part_text)
        msg.attach(part_html)

        try:
            logger.info("Connecting to SMTP %s:%d...", self.config.host, self.config.port)
            with smtplib.SMTP(self.config.host, self.config.port, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self.config.user, self.config.password)
                server.sendmail(from_addr, to_addrs, msg.as_string())
            logger.info("Successfully sent weekly digest to %s", to_addrs)
            return True
        except Exception as e:
            logger.error("Failed to send email digest via SMTP: %s", e)
            return False
