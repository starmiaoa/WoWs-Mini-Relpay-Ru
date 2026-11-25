"""俄服客户端资产、实体定义与汉化 mod 提取层。"""

from .client_locator import (
    DiscoveredClient,
    KorabliClientInfo,
    discover_korabli_clients,
    locate_client,
    normalize_short_version,
)
from .idx_vfs import IdxFileEntry, KorabliVFS
from .localization import LocalizationBundle, load_localization
from .store import VersionDataStore, list_synced_versions, load_version_store
from .sync import SyncResult, sync_client_data

__all__ = [
    "DiscoveredClient",
    "IdxFileEntry",
    "KorabliClientInfo",
    "KorabliVFS",
    "LocalizationBundle",
    "SyncResult",
    "VersionDataStore",
    "discover_korabli_clients",
    "list_synced_versions",
    "load_localization",
    "load_version_store",
    "locate_client",
    "normalize_short_version",
    "sync_client_data",
]
