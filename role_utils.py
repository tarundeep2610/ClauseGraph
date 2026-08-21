import os


def extract_roles_from_payload(jwt_payload: dict, project_id: str | None = None) -> list[str] | None:
    """
    Extracts role strings from an AuthNexus/Zitadel JWT payload.

    Priority order:
      1. nexus_projects[<project_id>].roles  — production Zitadel token
      2. Top-level "roles" list              — dev tokens

    Returns None when nexus_projects data exists but no entry matches this
    deployment's project id — callers must treat that as "not authorized
    for this project", not silently fall back to another project's roles.
    """
    effective_pid = project_id or os.environ.get("PROJECT_ID", "")

    nexus_projects: list[dict] = jwt_payload.get("nexus_projects", [])
    for project in nexus_projects:
        if str(project.get("id")) == str(effective_pid):
            return project.get("roles", [])

    if nexus_projects:
        return None

    return jwt_payload.get("roles", [])
