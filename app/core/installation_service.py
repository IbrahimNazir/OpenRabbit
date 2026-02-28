"""Installation service — handles GitHub App installation lifecycle.

Creates/updates Installation and Repository DB records when the GitHub App
is installed, uninstalled, or has repositories added/removed.

Uses the sync DB engine (called from Celery tasks or webhook handlers).
"""

from __future__ import annotations

import logging

from app.models.database import get_sync_db
from app.models.tenant import Installation, Repository

logger = logging.getLogger(__name__)


def handle_installation_created(payload: dict) -> None:
    """Process a new GitHub App installation.

    Creates an Installation record and Repository records for each repo
    included in the installation payload.

    Args:
        payload: The raw GitHub ``installation`` webhook payload.
    """
    installation_data = payload.get("installation", {})
    installation_id: int = installation_data.get("id", 0)
    account = installation_data.get("account", {})
    account_login: str = account.get("login", "unknown")
    account_type: str = account.get("type", "User")

    repos = payload.get("repositories", [])

    session = get_sync_db()
    try:
        # Upsert Installation — idempotent
        existing = session.get(Installation, installation_id)
        if existing:
            existing.account_login = account_login
            existing.account_type = account_type
            existing.is_active = True
            logger.info(
                "Updated existing installation",
                extra={"installation_id": installation_id, "account": account_login},
            )
        else:
            installation = Installation(
                id=installation_id,
                account_login=account_login,
                account_type=account_type,
                is_active=True,
            )
            session.add(installation)
            logger.info(
                "Created new installation",
                extra={"installation_id": installation_id, "account": account_login},
            )

        # Create Repository records
        for repo_data in repos:
            repo_id: int = repo_data.get("id", 0)
            full_name: str = repo_data.get("full_name", "")

            existing_repo = session.get(Repository, repo_id)
            if existing_repo:
                existing_repo.full_name = full_name
                existing_repo.installation_id = installation_id
            else:
                repo = Repository(
                    id=repo_id,
                    installation_id=installation_id,
                    full_name=full_name,
                    default_branch=repo_data.get("default_branch", "main"),
                )
                session.add(repo)

        session.commit()
        logger.info(
            "Installation created with %d repos",
            len(repos),
            extra={"installation_id": installation_id},
        )

    except Exception:
        session.rollback()
        logger.exception(
            "Failed to handle installation created",
            extra={"installation_id": installation_id},
        )
        raise
    finally:
        session.close()


def handle_installation_deleted(payload: dict) -> None:
    """Mark an installation as inactive when the GitHub App is uninstalled.

    Does NOT delete records — just deactivates to preserve history.

    Args:
        payload: The raw GitHub ``installation`` webhook payload.
    """
    installation_id: int = payload.get("installation", {}).get("id", 0)

    session = get_sync_db()
    try:
        existing = session.get(Installation, installation_id)
        if existing:
            existing.is_active = False
            session.commit()
            logger.info(
                "Installation deactivated",
                extra={"installation_id": installation_id},
            )
        else:
            logger.warning(
                "Attempted to delete unknown installation",
                extra={"installation_id": installation_id},
            )
    except Exception:
        session.rollback()
        logger.exception(
            "Failed to deactivate installation",
            extra={"installation_id": installation_id},
        )
        raise
    finally:
        session.close()


def handle_repos_added(payload: dict) -> None:
    """Add new repositories to an existing installation.

    Args:
        payload: The raw GitHub ``installation_repositories`` webhook payload.
    """
    installation_id: int = payload.get("installation", {}).get("id", 0)
    repos_added = payload.get("repositories_added", [])

    if not repos_added:
        return

    session = get_sync_db()
    try:
        for repo_data in repos_added:
            repo_id: int = repo_data.get("id", 0)
            full_name: str = repo_data.get("full_name", "")

            existing = session.get(Repository, repo_id)
            if existing:
                existing.full_name = full_name
                existing.installation_id = installation_id
            else:
                repo = Repository(
                    id=repo_id,
                    installation_id=installation_id,
                    full_name=full_name,
                    default_branch=repo_data.get("default_branch", "main"),
                )
                session.add(repo)

        session.commit()
        logger.info(
            "Added %d repos to installation",
            len(repos_added),
            extra={"installation_id": installation_id},
        )

    except Exception:
        session.rollback()
        logger.exception("Failed to add repos", extra={"installation_id": installation_id})
        raise
    finally:
        session.close()


def handle_repos_removed(payload: dict) -> None:
    """Remove repositories from an installation.

    Sets index_status to 'removed' rather than deleting records.

    Args:
        payload: The raw GitHub ``installation_repositories`` webhook payload.
    """
    repos_removed = payload.get("repositories_removed", [])

    if not repos_removed:
        return

    session = get_sync_db()
    try:
        for repo_data in repos_removed:
            repo_id: int = repo_data.get("id", 0)
            existing = session.get(Repository, repo_id)
            if existing:
                existing.index_status = "removed"

        session.commit()
        logger.info("Marked %d repos as removed", len(repos_removed))

    except Exception:
        session.rollback()
        logger.exception("Failed to remove repos")
        raise
    finally:
        session.close()
