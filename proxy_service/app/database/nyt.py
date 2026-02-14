import datetime
from .core import nyt_subscriptions_collection

async def upsert_nyt_subscription(list_name_encoded: str, display_name: str, enabled: bool = True):
    """
    Saves or updates a NYT list subscription.
    """
    await nyt_subscriptions_collection.update_one(
        {"list_name_encoded": list_name_encoded},
        {
            "$set": {
                "display_name": display_name,
                "enabled": enabled,
                "updated_at": datetime.datetime.utcnow()
            },
            "$setOnInsert": {
                "created_at": datetime.datetime.utcnow(),
                "last_run": None
            }
        },
        upsert=True
    )

async def get_all_nyt_subscriptions(enabled_only: bool = True):
    query = {"enabled": True} if enabled_only else {}
    return await nyt_subscriptions_collection.find(query).to_list(length=None)

async def update_nyt_subscription_last_run(list_name_encoded: str):
    await nyt_subscriptions_collection.update_one(
        {"list_name_encoded": list_name_encoded},
        {"$set": {"last_run": datetime.datetime.utcnow()}}
    )

async def delete_nyt_subscription(list_name_encoded: str):
    await nyt_subscriptions_collection.delete_one({"list_name_encoded": list_name_encoded})
