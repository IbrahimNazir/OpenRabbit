# ADR-0036: Trigger Repository Indexing on Installation Events

## Status
Accepted

## Context
`handle_installation_created()` and `handle_repos_added()` in `app/core/installation_service.py` create `Repository` DB records but never dispatch the `index_repository_task` Celery task. Users had to manually trigger indexing.

The `index_repository_task` signature is `(installation_id, repo_full_name, repo_id)` in `app/tasks/index_task.py`.

## Decision
After `session.commit()` in both functions, iterate over the repo list and call:

```python
index_repository_task.apply_async(
    args=[installation_id, repo_data["full_name"], repo_data["id"]],
    queue="default",
)
```

The dispatch loop is wrapped in `try/except Exception` so that a Celery/Redis failure never rolls back the already-committed DB transaction. Each dispatch is logged at INFO level.

## Consequences
- New installations and added repos are automatically indexed in the background.
- DB commits are never affected by task queue failures.
- Existing `handle_repos_removed()` and `handle_installation_deleted()` are unchanged.
