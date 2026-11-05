"""Sensor platform for Veolia."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from homeassistant.components.recorder import get_instance

from homeassistant.components.recorder.statistics import (
    StatisticMeanType,
    StatisticMetaData,
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.unit_conversion import VolumeConverter

from . import VeoliaConfigEntry
from .const import LOGGER
from .entity import VeoliaEntity


@dataclass(frozen=True, kw_only=True)
class VeoliaMesurementsDescription(SensorEntityDescription):
    """Represents a Veolia sensor."""

    attribute: str | None = None
    icon_off: str | None = None
    stats_attribute: str | None = None
    fiability_attribute: str | None = None
    include_last_report: bool = False
    import_stats_on_add: bool = False


SENSOR_TYPES: Final[tuple[VeoliaMesurementsDescription, ...]] = (
    VeoliaMesurementsDescription(
        key="annual_consumption",
        name="Annual Consumption",
        translation_key="annual_consumption",
        icon="mdi:water",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        icon_off="mdi:water",
        suggested_display_precision=3,
        attribute="annual_total_m3",
    ),
    # Consommation du dernier jour publié par Veolia (J-1 ou plus selon la zone).
    # « Conso journalière » ne donne que la consommation d'aujourd'hui, que Veolia
    # ne publie jamais le jour même : elle reste donc vide. Celui-ci a toujours une valeur.
    VeoliaMesurementsDescription(
        key="last_day_consumption",
        name="Last Day Consumption",
        translation_key="last_day_consumption",
        icon="mdi:water-outline",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        icon_off="mdi:water-outline",
        suggested_display_precision=0,
        attribute="last_daily_liters",
    ),
    VeoliaMesurementsDescription(
        key="last_date",
        name="Last Consumption Date",
        translation_key="last_consumption_date",
        icon="mdi:calendar",
        device_class=SensorDeviceClass.DATE,
        icon_off="mdi:calendar",
        attribute="last_date",
    ),
)

HISTORICAL_SENSOR_TYPES: Final[tuple[VeoliaMesurementsDescription, ...]] = (
    VeoliaMesurementsDescription(
        key="last_index",
        name="Last Index",
        translation_key="veolia_index",
        icon="mdi:counter",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        icon_off="mdi:counter",
        suggested_display_precision=3,
        attribute="last_index_m3",
        stats_attribute="index_stats_m3",
        fiability_attribute="daily_fiability",
        include_last_report=True,
        import_stats_on_add=False,
    ),
    VeoliaMesurementsDescription(
        key="daily_consumption",
        name="Daily Consumption",
        translation_key="daily_consumption",
        icon="mdi:water",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        icon_off="mdi:water",
        suggested_display_precision=0,
        attribute="daily_today_liters",
        stats_attribute="daily_stats_liters",
        fiability_attribute="daily_today_fiability",
        include_last_report=True,
        import_stats_on_add=True,
    ),
    VeoliaMesurementsDescription(
        key="monthly_consumption",
        name="Monthly Consumption",
        translation_key="monthly_consumption",
        icon="mdi:water",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        icon_off="mdi:water",
        suggested_display_precision=3,
        attribute="monthly_latest_m3",
        stats_attribute="monthly_stats_cubic_meters",
        fiability_attribute="monthly_fiability",
        include_last_report=False,
        import_stats_on_add=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeoliaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""
    LOGGER.debug("Setting up sensor platform")
    coordinator = entry.runtime_data
    sensors = [
        VeoliaMesurements(coordinator, description) for description in SENSOR_TYPES
    ] + [
        VeoliaHistoricalSensor(coordinator, description)
        for description in HISTORICAL_SENSOR_TYPES
    ]
    async_add_entities(sensors)


class VeoliaMesurements(VeoliaEntity, SensorEntity):
    """Base class for Veolia measurements sensors."""

    entity_description: VeoliaMesurementsDescription

    @property
    def native_value(self) -> float | int | str | date | None:
        """Return sensor value."""
        if self.entity_description.attribute is None:
            return None
        value = getattr(
            self.coordinator.data.computed, self.entity_description.attribute
        )
        LOGGER.debug("Sensor %s value : %s", self.__class__.__name__, value)
        return value


    @property
    def extra_state_attributes(self) -> dict | None:
        """Date du jour mesuré pour la consommation du dernier jour."""
        if self.entity_description.key != "last_day_consumption":
            return None
        comp = self.coordinator.data.computed
        return {
            "date": comp.last_date.isoformat() if comp.last_date else None,
            "data_type": comp.daily_fiability,
        }


class VeoliaHistoricalSensor(VeoliaMesurements):
    """Veolia sensor that also imports historical statistics."""

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state."""
        comp = self.coordinator.data.computed
        attributes = {
            "data_type": getattr(comp, self.entity_description.fiability_attribute)
        }
        if self.entity_description.include_last_report:
            attributes["last_report"] = (
                comp.last_date.isoformat() if comp.last_date else None
            )
        return attributes

    async def async_added_to_hass(self) -> None:
        """Start historical update on HA add."""
        if self.entity_description.import_stats_on_add:
            await self._update_historical_data()
        await super().async_added_to_hass()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Nouvelles données Veolia : on met aussi à jour les statistiques.

        Avant, les statistiques n'étaient importées qu'au démarrage de Home Assistant :
        les jours publiés ensuite par Veolia n'apparaissaient qu'après un redémarrage.
        """
        super()._handle_coordinator_update()
        if self.entity_description.import_stats_on_add:
            self.hass.async_create_task(self._update_historical_data())

    async def _sum_before(self, start) -> float:
        """Cumul déjà enregistré juste avant le début des nouvelles données.

        Les données relues ne couvrent que les derniers mois : leur cumul repart de 0.
        On le décale de ce qui est déjà enregistré, pour que le cumul ne recule jamais
        (sinon le tableau de bord Énergie afficherait une consommation négative).
        """
        try:
            res = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
                start - timedelta(days=400),
                start,
                {self.entity_id},
                "hour",
                None,
                {"sum"},
            )
        except Exception as err:  # noqa: BLE001
            LOGGER.debug("Lecture des statistiques impossible : %s", err)
            return 0.0
        lignes = res.get(self.entity_id) or []
        return float(lignes[-1].get("sum") or 0.0) if lignes else 0.0

    async def _update_historical_data(self) -> None:
        """Update historical values."""
        LOGGER.debug("Update_historical_data for %s", self.__class__.__name__)
        stats = getattr(
            self.coordinator.data.computed, self.entity_description.stats_attribute
        )
        if not stats:
            LOGGER.debug("No data update for %s", self.__class__.__name__)
            return
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=None,
            source="recorder",
            statistic_id=self.entity_id,
            unit_class=VolumeConverter.UNIT_CLASS,
            unit_of_measurement=self.entity_description.native_unit_of_measurement,
        )
        decalage = await self._sum_before(stats[0]["start"])
        if decalage:
            stats = [{**s, "sum": s["sum"] + decalage} for s in stats]
        LOGGER.debug("-> StatisticMetaData %s Data : %s", metadata, stats)
        async_import_statistics(self.hass, metadata, stats)
