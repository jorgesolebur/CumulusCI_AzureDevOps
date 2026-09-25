from typing import Dict, Iterable, List

from cumulusci.core.config import BaseProjectConfig
from cumulusci.tasks.release_notes.parser import ChangeNotesLinesParser


def _as_parser_dict(cfg) -> Dict | None:
    if cfg is None or isinstance(cfg, str):
        return None
    class_path = (
        cfg.get("class_path")
        if hasattr(cfg, "get")
        else getattr(cfg, "class_path", None)
    )
    title = cfg.get("title") if hasattr(cfg, "get") else getattr(cfg, "title", None)
    if class_path:
        return {"class_path": class_path, "title": title}
    return None


def parser_configs(project_config: BaseProjectConfig) -> List[Dict]:
    """Return the azure_devops parser list, falling back to a flat parsers map."""
    configs = (
        project_config.project__git__release_notes__parsers__azure_devops
        if project_config.project__git__release_notes__parsers__azure_devops
        else None
    )
    if not configs:
        all_parsers = project_config.project__git__release_notes__parsers or {}
        if hasattr(all_parsers, "get") and all_parsers.get("azure_devops"):
            configs = all_parsers.get("azure_devops")
        else:
            configs = all_parsers

    values: Iterable = (
        configs.values() if hasattr(configs, "values") and configs is not None else []
    )
    parsers: List[Dict] = []
    for cfg in values:
        parsed = _as_parser_dict(cfg)
        if parsed:
            parsers.append(parsed)
            continue
        nested = cfg.values() if cfg is not None and hasattr(cfg, "values") else None
        if nested:
            for inner in nested:
                parsed_inner = _as_parser_dict(inner)
                if parsed_inner:
                    parsers.append(parsed_inner)
    return parsers


class ADOLinesParser(ChangeNotesLinesParser):
    """Copies headed sections from an ADO pull request description."""

    def __init__(self, release_notes_generator, title):
        super().__init__(release_notes_generator, title)
        self.link_pr = getattr(release_notes_generator, "link_pr", False)
        self.pr_number = None
        self.pr_url = None

    def _process_change_note(self, pull_request):
        self.pr_number = getattr(pull_request, "number", None)
        self.pr_url = getattr(pull_request, "html_url", None)
        return getattr(pull_request, "body", None) or ""

    def _add_link(self, line):
        if self.link_pr and self.pr_number and self.pr_url:
            line += " [[PR{}]({})]".format(self.pr_number, self.pr_url)
        return line
