import logging
from datetime import UTC, datetime
from typing import Iterable, Optional

from cumulusci.tasks.release_notes.provider import BaseChangeNotesProvider
from cumulusci.utils.git import is_release_branch
from cumulusci.utils.release_branch import parse_format_config
from cumulusci.utils.version_strings import LooseVersion
from cumulusci.vcs.bootstrap import get_tag_by_name

logger = logging.getLogger(__name__)


class ADOChangeNotesProvider(BaseChangeNotesProvider):
    """Yields pull requests merged to the default branch between two tags."""

    def __init__(self, release_notes_generator, current_tag, last_tag=None):
        super().__init__(release_notes_generator)
        self.current_tag = current_tag
        self._last_tag = last_tag
        self.repo = release_notes_generator.get_repo()
        self.github_info = release_notes_generator.github_info

    def __call__(self):
        logger.info(
            "Release notes window: %s .. %s",
            self.last_tag or "(repository start)",
            self.current_tag,
        )
        for pull_request in self._get_pull_requests():
            yield pull_request

    @property
    def last_tag(self):
        if not self._last_tag:
            self._last_tag = self._get_last_tag()
        return self._last_tag

    @property
    def current_tag_info(self):
        if not hasattr(self, "_current_tag_info"):
            tag = get_tag_by_name(self.repo, self.current_tag)
            self._current_tag_info = {
                "tag": tag,
                "commit": self.repo.get_commit(tag.sha),
            }
        return self._current_tag_info

    @property
    def last_tag_info(self):
        if not hasattr(self, "_last_tag_info"):
            if self.last_tag:
                tag = get_tag_by_name(self.repo, self.last_tag)
                self._last_tag_info = {
                    "tag": tag,
                    "commit": self.repo.get_commit(tag.sha),
                }
            else:
                self._last_tag_info = None
        return self._last_tag_info

    @property
    def start_date(self):
        return self._commit_date(self.current_tag_info["commit"])

    @property
    def end_date(self):
        if self.last_tag_info:
            return self._commit_date(self.last_tag_info["commit"])
        return None

    def _commit_date(self, commit) -> Optional[datetime]:
        date = getattr(commit, "author_date", None)
        if date is None:
            return None
        if date.tzinfo is None:
            return date.replace(tzinfo=UTC)
        return date

    def _get_version_from_tag(self, tag):
        if tag.startswith(self.github_info["prefix_prod"]):
            return tag.replace(self.github_info["prefix_prod"], "")
        elif tag.startswith(self.github_info["prefix_beta"]):
            return tag.replace(self.github_info["prefix_beta"], "")
        raise ValueError("Could not determine version number from tag {}".format(tag))

    def _get_last_tag(self):
        """Previous tag of the same prefix, else the other prefix.

        Beta tags such as corus-beta/2.0.0.3 must bound the window with the
        previous corus-beta/* tag. Looking only at production (release/) tags
        leaves last_tag empty and includes the entire PR history.
        """
        current_tag = self.release_notes_generator.current_tag
        current_version = LooseVersion(self._get_version_from_tag(current_tag))
        prefix_prod = self.github_info.get("prefix_prod") or ""
        prefix_beta = self.github_info.get("prefix_beta") or ""
        names = list(self.repo.list_tag_names() or [])

        if current_tag.startswith(prefix_beta):
            prefixes = [prefix_beta, prefix_prod]
        else:
            prefixes = [prefix_prod, prefix_beta]

        for prefix in prefixes:
            if not prefix:
                continue
            last = self._latest_tag_before(names, current_version, prefix)
            if last:
                return last
        return None

    def _latest_tag_before(self, names, current_version, prefix):
        found = []
        for name in names:
            if not name.startswith(prefix):
                continue
            try:
                version = LooseVersion(self._get_version_from_tag(name))
            except ValueError:
                continue
            if version >= current_version:
                continue
            found.append((version, name))
        if not found:
            return None
        found.sort(key=lambda item: item[0])
        return found[-1][1]

    def _get_pull_requests(self):
        seen = set()
        for pull in self.repo.pull_requests(
            state="closed",
            base=self.github_info["default_branch"],
            direction="asc",
        ):
            if not self._include_pull_request(pull):
                continue
            for item in self._expand_sprint_merge(pull):
                number = getattr(item, "number", None)
                if number is not None and number in seen:
                    continue
                if number is not None:
                    seen.add(number)
                yield item

    def _sprint_branch_prefix_formats(self):
        """(prefix, format_config) pairs for sprint and numeric release branches.

        Uses ``get_release_branch_prefix_and_format_config`` the same way
        dependency resolvers do. That helper follows the current checkout, so
        both ``prefix_feature`` (with the project format, e.g. FYyyQqSn) and
        ``prefix_release`` (numeric ``release/nnn``) are also checked.
        """
        project_config = getattr(self.repo, "project_config", None)
        pairs = []
        feature_prefix = "feature/"
        release_prefix = "release/"
        format_config = None

        if project_config is not None:
            getter = getattr(
                project_config, "get_release_branch_prefix_and_format_config", None
            )
            if callable(getter):
                try:
                    result = getter()
                except Exception:
                    result = None
                if (
                    isinstance(result, (tuple, list))
                    and len(result) == 2
                    and isinstance(result[0], str)
                ):
                    pairs.append((result[0], result[1]))
            feature = getattr(project_config, "project__git__prefix_feature", None)
            release = getattr(project_config, "project__git__prefix_release", None)
            if isinstance(feature, str) and feature:
                feature_prefix = feature
            if isinstance(release, str) and release:
                release_prefix = release
            try:
                format_config = parse_format_config(project_config)
            except Exception:
                format_config = None

        pairs.append((feature_prefix, format_config))
        pairs.append((release_prefix, None))

        seen = set()
        for prefix, fmt in pairs:
            key = (prefix, id(fmt) if fmt is not None else None)
            if key in seen:
                continue
            seen.add(key)
            yield prefix, fmt

    def _is_sprint_merge_branch(self, branch_name: str) -> bool:
        """True when the PR source is a sprint/release parent, not a child.

        ``is_release_branch`` is the root form of ``is_release_branch_or_child``
        (no ``__`` suffix), so ``feature/FY27Q1S3`` and ``release/001`` match
        while ``feature/FY27Q1S3__HZPM-4362`` does not.
        """
        name = (branch_name or "").strip()
        if not name:
            return False
        return any(
            is_release_branch(name, prefix, format_config)
            for prefix, format_config in self._sprint_branch_prefix_formats()
        )

    def _expand_sprint_merge(self, pull) -> Iterable:
        """Yield a main-branch PR and, for sprint merges, its child feature PRs.

        Feature work is merged to feature/FYyyQqSs or release/nnn, then that
        branch is merged to main. Child PRs targeting the sprint branch carry
        the Jira keys and descriptions that would otherwise be missed.
        """
        yield pull
        head = getattr(pull, "head_ref", "") or ""
        if not self._is_sprint_merge_branch(head):
            return

        logger.info("Expanding sprint merge PR #%s from branch %s", pull.number, head)
        children = self.repo.pull_requests(
            state="closed",
            base=head,
            direction="asc",
        )
        default_branch = self.github_info.get("default_branch") or ""
        count = 0
        for child in children or []:
            if not getattr(child, "merged_at", None):
                continue
            child_head = getattr(child, "head_ref", "") or ""
            if child_head == default_branch:
                continue
            if getattr(child, "number", None) == getattr(pull, "number", None):
                continue
            count += 1
            yield child
        logger.info(
            "Sprint merge PR #%s contributed %s child pull request(s)",
            pull.number,
            count,
        )

    def _include_pull_request(self, pull_request):
        merged_date = pull_request.merged_at
        if not merged_date:
            return False
        if merged_date.tzinfo is None:
            merged_date = merged_date.replace(tzinfo=UTC)

        last_tag_sha = None
        if self.last_tag and self.last_tag_info:
            last_tag_sha = self.last_tag_info["commit"].sha
            if pull_request.merge_commit_sha == last_tag_sha:
                return False

        current_tag_sha = self.current_tag_info["commit"].sha
        if pull_request.merge_commit_sha == current_tag_sha:
            return True

        if not self.start_date or merged_date > self.start_date:
            return False

        if self.end_date:
            return (
                merged_date > self.end_date
                and pull_request.merge_commit_sha != last_tag_sha
            )

        # No previous tag: first release on this line, include everything
        # up to the current tag. If a previous tag exists but has no date,
        # do not fall back to the full history.
        return not self.last_tag
