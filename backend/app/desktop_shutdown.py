from .film_pipeline_service import stop_pipeline
from .film_scene_state_store import (
    append_run_log,
    list_active_runs,
    update_run,
)


def shutdown_status() -> dict:
    runs = list_active_runs()
    return {
        "safe_to_exit": len(runs) == 0,
        "active_count": len(runs),
        "active_runs": [
            {
                "id": run.get("id"),
                "project_id": run.get("project_id"),
                "status": run.get("status"),
                "current_scene_id": run.get("current_scene_id"),
                "current_scene_index": run.get("current_scene_index"),
                "stop_after_current": bool(run.get("stop_after_current")),
            }
            for run in runs
        ],
    }


def request_safe_shutdown() -> dict:
    for run in list_active_runs():
        project_id = run.get("project_id")
        if not project_id:
            continue
        try:
            stop_pipeline(project_id)
        except ValueError:
            continue
    result = shutdown_status()
    result["requested"] = "stop_after_current_scene"
    return result


def prepare_force_shutdown() -> dict:
    checkpointed = []
    for run in list_active_runs():
        run_id = run.get("id")
        if not run_id:
            continue
        status = str(run.get("status") or "")
        if status != "paused":
            update_run(
                run_id,
                status="paused",
                stop_after_current=False,
            )
        append_run_log(
            run_id,
            {
                "action": "desktop_force_exit_checkpoint",
                "previous_status": status,
                "current_scene_id": run.get("current_scene_id"),
            },
        )
        checkpointed.append(run_id)

    result = shutdown_status()
    result["checkpointed_run_ids"] = checkpointed
    result["requested"] = "force_exit_checkpoint"
    return result
