from .core import (
    mongo_client, 
    db, 
    redis_client, 
    books_collection, 
    access_logs_collection, 
    login_attempts_collection, 
    settings_collection, 
    lists_collection,
    provider_stats_collection,
    unified_catalog_collection,
    blocked_clients_collection,
    custom_fields_collection,
    logs_collection,
    backup_jobs_collection,
    backup_history_collection,
    nyt_subscriptions_collection,
    nyt_archive_collection,
    nyt_book_archive_collection,
    audible_archive_collection
)
from .nyt import (
    upsert_nyt_subscription,
    get_all_nyt_subscriptions,
    update_nyt_subscription_last_run,
    delete_nyt_subscription,
    archive_nyt_response
)

from .audible import (
    archive_audible_response
)

from .books import (
    init_db_indexes,
    upsert_book_to_db,
    get_book_from_db,
    get_books_from_db_batch,
    get_library_page,
    delete_book_from_library,
    increment_book_access,
    search_library_books,
    get_custom_fields,
    save_custom_fields,
    get_unified_book, 
    find_unified_by_relation, 
    create_unified_book, 
    add_relation_to_unified_book
)

from .cache import (
    get_cache, 
    set_cache, 
    inspect_cache, 
    delete_cache_key, 
    flush_all_cache
)

from .settings import (
    DEFAULT_SETTINGS,
    get_system_settings, 
    save_system_settings,
    get_stored_password_hash,
    set_stored_password_hash,
    clear_stored_password_hash
)

from .logs import (
    get_country_code,
    log_activity, 
    log_provider_stats, 
    get_system_logs, 
    log_request_access, 
    get_access_logs,
    get_traffic_stats,
    get_detailed_stats, 
    get_dashboard_stats,
    log_login_attempt,
    get_login_attempts
)

from .blocklist import (
    get_blocked_clients, 
    add_block, 
    remove_block, 
    is_client_blocked
)

from .lists import (
    save_imported_list,
    create_custom_list,
    get_all_lists,
    get_list_by_id,
    delete_list_by_id,
    update_list_name,
    update_list_metadata,
    set_item_note,
    get_item_note,
    add_item_to_list,
    remove_item_from_list
)

from .admin import (
    get_collection_names,
    execute_admin_query,
    export_collection_to_csv
)
