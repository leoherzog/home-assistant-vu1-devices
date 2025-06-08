"""Text platform for VU1 Devices."""
import logging
from typing import Any, Dict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_entities import VU1DialNameText
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up VU1 text entities from a config entry."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    
    if not coordinator.data:
        return

    entities = []
    dials_data = coordinator.data.get("dials", {})
    
    for dial_uid, dial_data in dials_data.items():
        entities.append(VU1DialNameText(coordinator, dial_uid, dial_data))

    async_add_entities(entities)