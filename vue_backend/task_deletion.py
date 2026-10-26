"""Delete one task copy without changing the other copy or collection rules."""


class TaskDeletion:
    def __init__(self, tasks, uploader):
        self.tasks, self.uploader = tasks, uploader

    def delete(self, task_id: str, scope: str = "local") -> dict:
        task_id = str(task_id).strip()
        if scope not in ("local", "cloud"):
            raise ValueError("删除范围无效")
        task = next((item for item in self.tasks.list() if item["task_id"] == task_id), None)
        if task is not None and task.get("status") == "collecting":
            raise ValueError("进行中的采集任务不能删除，请先结束采集")
        if scope == "cloud":
            self.uploader.delete_cloud(task_id)
        else:
            if task is None:
                raise ValueError("未找到采集任务")
            self.uploader.prepare_delete(task_id)
            self.tasks.delete(task_id)
        return {"ok": True, "task_id": task_id, "scope": scope}
