import logging
from pathlib import Path

from cumulusci.core.utils import import_global
from cumulusci.tasks.release_notes.generator import (
    BaseReleaseNotesGenerator,
    render_empty_pr_section,
)
from cumulusci.tasks.release_notes.parser import ChangeNotesLinesParser  # noqa: F401

from cumulusci_ado.vcs.ado.exceptions import ADOApiNotFoundError
from cumulusci_ado.vcs.ado.release_notes.provider import ADOChangeNotesProvider

logger = logging.getLogger(__name__)

PUBLISH_FILE = "file"
PUBLISH_PULL_REQUEST = "pull_request"
PUBLISH_REPOSITORY = "repository"
DEFAULT_PUBLISH_TARGETS = (PUBLISH_PULL_REQUEST,)


class ADOReleaseNotesGenerator(BaseReleaseNotesGenerator):
    def __init__(
        self,
        repo,
        github_info,
        parser_config,
        current_tag,
        last_tag=None,
        link_pr=False,
        publish=False,
        has_issues=False,
        include_empty=False,
        version_id=None,
        trial_info=False,
        sandbox_date=None,
        production_date=None,
    ):
        self.repo = repo
        self.github = repo
        self.github_info = github_info
        self.parser_config = parser_config
        self.current_tag = current_tag
        self.last_tag = last_tag
        self.link_pr = link_pr
        self.do_publish = publish
        self.has_issues = has_issues
        self.include_empty_pull_requests = include_empty
        super().__init__()
        self.version_id = version_id
        self.trial_info = trial_info
        self.sandbox_date = sandbox_date
        self.production_date = production_date

    def __call__(self):
        content = super().__call__()
        content = self._append_artifact_section(content)
        content = self._append_files_section(content)
        if self.include_empty_pull_requests:
            empty_section = render_empty_pr_section(self.empty_change_notes)
            if empty_section:
                if content:
                    content = content + "\r\n" + "\r\n".join(empty_section)
                else:
                    content = "\r\n".join(empty_section)
        if self.do_publish:
            self._publish(content)
        return content

    def _init_parsers(self):
        for cfg in self.parser_config or []:
            class_path = None
            title = None
            if isinstance(cfg, dict):
                class_path = cfg.get("class_path")
                title = cfg.get("title")
            else:
                class_path = getattr(cfg, "class_path", None)
                title = getattr(cfg, "title", None)
            if not class_path:
                continue
            parser_class = import_global(class_path)
            self.parsers.append(parser_class(self, title))

    def _init_change_notes(self):
        return ADOChangeNotesProvider(self, self.current_tag, self.last_tag)

    def get_repo(self):
        return self.repo

    def _append_artifact_section(self, content: str) -> str:
        try:
            version, _ = self.repo.get_version_package(self.current_tag)
        except ADOApiNotFoundError:
            version = None
        if not version:
            logger.warning(
                "Skipping Artifact section; package version not found for tag %s",
                self.current_tag,
            )
            return content

        url = self.repo.artifact_ui_url(self.current_tag)
        if not url:
            logger.warning(
                "Skipping Artifact section; could not build Artifacts URL for tag %s",
                self.current_tag,
            )
            return content

        from cumulusci_ado.utils.ado import custom_to_semver

        try:
            numeric = custom_to_semver(self.current_tag, self.repo.project_config)
        except (ValueError, TypeError, AttributeError):
            numeric = getattr(version, "version", "") or self.current_tag

        package = self.repo.project_name
        section = f"# Artifact\r\n\r\n[{package} {numeric}]({url})"
        if content:
            return content + "\r\n\r\n" + section
        return section

    def _resolved_last_tag(self):
        if self.last_tag:
            return self.last_tag
        provider = getattr(self, "change_notes", None)
        return getattr(provider, "_last_tag", None)

    def _append_files_section(self, content: str) -> str:
        last_tag = self._resolved_last_tag()
        if not last_tag:
            logger.info("Skipping Files section; no previous tag to compare")
            return content

        builder = getattr(self.repo, "compare_files_url", None)
        url = builder(last_tag, self.current_tag) if callable(builder) else None
        if not isinstance(url, str) or not url:
            logger.warning(
                "Skipping Files section; could not build compare URL for %s .. %s",
                last_tag,
                self.current_tag,
            )
            return content

        section = f"# Files\r\n\r\n[Changed Files]({url})"
        if content:
            return content + "\r\n\r\n" + section
        return section

    def _publish(self, content: str) -> None:
        # provider = getattr(self, "change_notes", None)
        # version = getattr(provider, "_get_version_from_tag", lambda tag: tag)(self.current_tag) if provider else None
        # if not version:
        #     logger.warning("Skipping release notes publish; could not determine version from tag %s in provider %s", self.current_tag, provider)
        #     version = self.current_tag

        path = self.repo.resolve_release_notes_path(self.current_tag)
        comment = f"Add release notes for {self.current_tag}"
        for target in self._publish_targets():
            if target == PUBLISH_FILE:
                self._publish_file(path, content)
            elif target == PUBLISH_PULL_REQUEST:
                self._publish_pull_request(self.repo, path, content, comment)
            elif target == PUBLISH_REPOSITORY:
                self._publish_repository(path, content, comment)
            else:
                logger.warning(
                    "Unknown release_notes_publish target %r; skipping", target
                )
        self.repo.merge_package_description_fields(
            self.current_tag, {"release_notes_path": path}
        )

    def _publish_targets(self) -> list[str]:
        raw = self.repo.config("release_notes_publish")
        if raw is None or raw is False or raw == "":
            return list(DEFAULT_PUBLISH_TARGETS)
        if isinstance(raw, (list, tuple)):
            values = [str(item).strip() for item in raw if str(item).strip()]
        else:
            values = [
                part.strip()
                for part in str(raw).replace(",", " ").split()
                if part.strip()
            ]
        return values or list(DEFAULT_PUBLISH_TARGETS)

    def _publish_file(self, path: str, content: str) -> None:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        logger.info("Wrote release notes to %s", dest)

    def _publish_pull_request(
        self, repo, path: str, content: str, comment: str
    ) -> None:
        source_branch = f"cci/release-notes/{self.current_tag}"
        base_branch = repo.default_branch
        repo.create_branch(source_branch, source_branch=base_branch)
        repo.push_file(path, content, branch=source_branch, comment=comment)
        pr = self._existing_or_create_pull(
            repo,
            title=f"Release notes for {self.current_tag}",
            base=base_branch,
            head=source_branch,
            body=comment,
        )
        if pr is None:
            return
        if pr.can_auto_merge():
            pr.merge()
            logger.info(
                "Published release notes via pull request #%s on %s",
                pr.number,
                source_branch,
            )
        else:
            logger.warning(
                "Pull request #%s for release notes was created but could not be auto-merged",
                pr.number,
            )

    def _existing_or_create_pull(self, repo, title, base, head, body):
        existing = repo.pull_requests(state="active", head=head, base=base) or []
        if existing:
            logger.info(
                "Using existing pull request #%s for %s into %s",
                existing[0].number,
                head,
                base,
            )
            return existing[0]
        return repo.create_pull(title=title, base=base, head=head, body=body)

    def _publish_repository(self, path: str, content: str, comment: str) -> None:
        url = self.repo.config("release_notes_repository")
        if not url:
            logger.warning(
                "release_notes_repository is not set; skipping repository publish"
            )
            return
        from cumulusci_ado.vcs.ado.service import get_ado_service_for_url

        service = get_ado_service_for_url(self.repo.project_config, str(url))
        if not service:
            logger.warning(
                "No Azure DevOps service configured for %s; skipping repository publish",
                url,
            )
            return
        target = service.get_repository(options={"repository_url": str(url)})
        if not target:
            logger.warning(
                "Could not open repository %s; skipping repository publish", url
            )
            return
        branch = self.repo.config("release_notes_branch") or target.default_branch
        target.push_file(
            path,
            content,
            branch=branch,
            comment=comment,
            create_branch_from=target.default_branch,
        )
        logger.info("Published release notes to %s on branch %s", url, branch)


class ADOParentPullRequestNotesGenerator:
    def __init__(self, github, repo, project_config):
        self.github = github
        self.repo = repo
        self.project_config = project_config
