"""mavesp8266 parsing and the write query (03 §9)."""

import pytest
from fake_vehicle import FakeVehicle

from relay.errors import RelayError
from relay.mav import bridge
from relay.mav.discovery import identify_bridge_page


def fake_pages(**settings) -> FakeVehicle:
    fake = FakeVehicle(http=None, bind=("127.0.0.9", 14555))
    fake.bridge.update(settings)
    return fake


def test_identify_mavesp_index():
    fake = fake_pages()
    found = identify_bridge_page(fake.page_root())
    assert found == {"version": "1.2.2", "title": "MavLink Bridge"}
    assert identify_bridge_page("<html><title>Router</title></html>") is None


def test_parameters_table_unpacks_strings_and_ips():
    fake = fake_pages(ssidsta="anet4d", ipsta="10.7.7.36", mode=1)
    table = bridge.parse_parameters_table(fake.page_parameters())
    assert bridge.unpack_text(table, "WIFI_SSIDSTA") == "anet4d"
    assert bridge.unpack_text(table, "WIFI_SSID") == "anet4d-a01"
    assert bridge.unpack_ip(table["WIFI_IPSTA"]) == "10.7.7.36"
    assert bridge.unpack_version(table["SW_VER"]) == "1.2.2"
    settings = bridge.describe_mavesp(table, {})
    assert settings["mode"] == "station" and settings["host_port"] == 14550 and settings["baud"] == 921600
    assert settings["has_ap_password"] and "pwd" not in settings


def test_setup_form_values():
    fake = fake_pages()
    values = bridge.form_values(bridge.parse_first_form(fake.page_setup()))
    assert values["mode"] == "0" and values["ssid"] == "anet4d-a01" and values["cport"] == "14555"
    settings = bridge.describe_mavesp({}, values)
    assert settings["mode"] == "ap" and settings["dhcp"] is True


def test_configure_query_station_dhcp():
    args = bridge.configure_query(
        {"host_port": 14550, "client_port": 14555, "baud": 921600, "channel": 11},
        {"mode": "station", "ssid": "anet4d", "password": "fake-bridge-pass", "baud": 921600},
    )
    assert args == {
        "mode": "1",
        "ssidsta": "anet4d",
        "pwdsta": "fake-bridge-pass",
        "ipsta": "0.0.0.0",
        "gatewaysta": "0.0.0.0",
        "subnetsta": "0.0.0.0",
        "hport": "14550",
        "cport": "14555",
        "reboot": "1",
    }
    assert bridge.redact(args)["pwdsta"] == "***"


@pytest.mark.parametrize(
    "desired",
    [
        {"mode": "station", "ssid": ""},
        {"mode": "station", "ssid": "x" * 17},
        {"mode": "station", "ssid": "anet4d", "password": "short"},
        {"mode": "station", "ssid": "anet4d", "ip": "10.7.7"},
        {"mode": "mesh"},
        {"mode": "ap", "baud": 9600},
    ],
)
def test_configure_query_rejects(desired):
    with pytest.raises(RelayError):
        bridge.configure_query({}, desired)


def test_fallback_form_parse_masks_passwords():
    page = (
        "<form action='/save' method='post'>Name <input name='ssid' value='x'> "
        "Key <input type='password' name='key' value='secret'></form>"
    )
    form = bridge._mask_form(bridge.parse_first_form(page))
    assert form["action"] == "/save" and form["fields"][1]["value"] == "" and form["fields"][1]["secret"]
