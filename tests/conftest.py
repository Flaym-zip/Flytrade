import pytest


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    monkeypatch.setenv('FLYTRADE_FEED', 'off')
