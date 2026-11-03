from copy import deepcopy

from app.services.configuration_delivery import (
    ConfigurationDelivery,
    configuration_delivery_scope,
    delivered_native,
)
from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config


def test_delivered_ibmi_keeps_each_module_authority_without_live_resolution(monkeypatch):
    original = load_ib_market_intelligence_config()
    frozen = dict(original._effective_configurations)
    delivery = ConfigurationDelivery(
        {}, {"contextual.ibmi." + name: config for name, config in frozen.items()}
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("frozen delivery must not read current configuration")

    with configuration_delivery_scope(delivery):
        monkeypatch.setattr("pathlib.Path.read_text", forbidden)
        config = delivered_native("ibmi")
    assert set(config._effective_configurations) == set(frozen)
    for module, effective in config._effective_configurations.items():
        assert effective.snapshot.resolution_hash == frozen[module].snapshot.resolution_hash
        assert config.section(module) == frozen[module].values["config"][module]
    assert (
        config._effective_configuration.snapshot.resolution_hash
        == frozen["liquidity"].snapshot.resolution_hash
    )
    expected_sources = {
        entry.key.removeprefix("config."): entry.source
        for effective in frozen.values()
        for entry in effective.snapshot.entries
        if entry.key.startswith("config.")
    }
    assert dict(config._configuration_sources) == expected_sources
    assert deepcopy(config).config_hash == original.config_hash
