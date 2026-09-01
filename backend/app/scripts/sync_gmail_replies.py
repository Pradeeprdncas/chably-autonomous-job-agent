import asyncio
from app.database import SessionLocal
from app.models import GoogleConnection
from app.services.reply_sync import GmailReplySyncService


async def main():
    summary = {"users_processed": 0, "users_failed": 0, "threads_processed": 0, "messages_processed": 0, "errors": [], "results": []}
    with SessionLocal() as db:
        user_ids = [row.user_id for row in db.query(GoogleConnection).filter(GoogleConnection.status == "active").all()]
    for user_id in user_ids:
        with SessionLocal() as db:
            try:
                result = await GmailReplySyncService(db).sync_user(user_id); summary["users_processed"] += 1; summary["threads_processed"] += result["threads_checked"]; summary["messages_processed"] += result["new_messages"]; summary["errors"].extend({"user_id": user_id, **error} for error in result["errors"]); summary["results"].append({"user_id": user_id, **result})
            except Exception as exc:
                error = {"user_id": user_id, "error": type(exc).__name__}; summary["users_failed"] += 1; summary["errors"].append(error); summary["results"].append(error)
    print(summary)
    return summary


if __name__ == "__main__": asyncio.run(main())
