"""汉化词库与多级回退测试。"""

from __future__ import annotations

from pathlib import Path

from korabli_minimap.config import DEFAULT_DATA_DIR
from korabli_minimap.extractor.localization import LocalizationBundle
from korabli_minimap.extractor.store import list_synced_versions, load_version_store


def test_localization_fallback_and_map_slash_key() -> None:
    bundle = LocalizationBundle(
        zh_catalog={
            "IDS_PFSC211": "拉罗谢尔",
            "IDS_PFSC211_FULL": "拉罗谢尔",
            "IDS_SPACES/47_SLEEPING_GIANT": "沉睡的渤海湾",
        },
        ru_catalog={
            "IDS_PFSC211": "La Rochelle",
            "IDS_RU_ONLY": "Крейсер Тест",
            "IDS_SPACES/47_SLEEPING_GIANT": "Сонный Бохайвань",
        },
    )
    assert bundle.ship_name_zh("PFSC211") == "拉罗谢尔"
    assert bundle.ship_name_zh("RU_ONLY") == "Крейсер Тест"
    assert bundle.ship_name_zh("NON_EXISTENT", fallback="FallbackShip") == "FallbackShip"
    assert bundle.map_name_zh("spaces/47_Sleeping_Giant") == "沉睡的渤海湾"


def test_synced_version_store_ships_and_maps() -> None:
    versions = list_synced_versions(DEFAULT_DATA_DIR)
    if not versions:
        return
    store = load_version_store(versions[0], data_dir=DEFAULT_DATA_DIR)
    la_rochelle = store.get_ship(4073633616)
    assert la_rochelle is not None
    assert la_rochelle["zh_name"] == "拉罗谢尔"
    assert la_rochelle["species"] == "Cruiser"
    assert store.get_map_name_zh("spaces/47_Sleeping_Giant") == "沉睡的渤海湾"
    meta = store.get_map_meta("spaces/47_Sleeping_Giant")
    assert meta["space_width"] == 1400.0
