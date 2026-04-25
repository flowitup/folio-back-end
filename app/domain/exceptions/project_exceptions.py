"""Project domain exceptions."""


class ProjectError(Exception):
    """Base exception for project domain errors."""

    pass


class ProjectNotFoundError(ProjectError):
    """Raised when project does not exist."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        super().__init__(f"Project not found: {project_id}")


class ProjectAccessDeniedError(ProjectError):
    """Raised when user lacks permission for project operation."""

    def __init__(self, user_id: str, project_id: str, action: str):
        self.user_id = user_id
        self.project_id = project_id
        self.action = action
        super().__init__(f"User {user_id} cannot {action} project {project_id}")


class InvalidProjectDataError(ProjectError):
    """Raised when project data validation fails."""

    def __init__(self, message: str):
        super().__init__(message)
