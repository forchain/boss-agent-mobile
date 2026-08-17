"""Unit tests for the idempotent EnvironmentProvisioner."""

from unittest.mock import patch

from scripts.bootstrap import (
    ComponentStatus,
    EnvironmentProvisioner,
    ProvisioningReport,
)


def test_component_status_model():
    status = ComponentStatus(name="java", installed=True, version="17.0.2", details="OpenJDK 17")
    assert status.installed is True
    assert status.version == "17.0.2"


def test_check_all_reports_statuses():
    provisioner = EnvironmentProvisioner()
    with (
        patch.object(provisioner, "check_java") as mock_java,
        patch.object(provisioner, "check_android_sdk") as mock_sdk,
        patch.object(provisioner, "check_avd") as mock_avd,
        patch.object(provisioner, "check_appium") as mock_appium,
        patch.object(provisioner, "check_apk") as mock_apk,
    ):
        mock_java.return_value = ComponentStatus("java", True, "17.0.10", "OK")
        mock_sdk.return_value = ComponentStatus("android_sdk", True, "33.0.0", "OK")
        mock_avd.return_value = ComponentStatus("avd", True, "boss_avd_arm64", "OK")
        mock_appium.return_value = ComponentStatus("appium", True, "2.5.1", "OK")
        mock_apk.return_value = ComponentStatus("boss_apk", True, "12.0", "OK")

        report = provisioner.check_all()
        assert isinstance(report, ProvisioningReport)
        assert report.all_ready is True
        assert len(report.components) == 5


def test_provision_all_is_idempotent_when_already_installed():
    provisioner = EnvironmentProvisioner()
    with (
        patch.object(provisioner, "check_all") as mock_check,
        patch.object(provisioner, "provision_java") as mock_p_java,
    ):
        mock_check.return_value = ProvisioningReport(
            components=[
                ComponentStatus("java", True, "17"),
                ComponentStatus("android_sdk", True, "33"),
                ComponentStatus("avd", True, "boss_avd_arm64"),
                ComponentStatus("appium", True, "2.5"),
                ComponentStatus("boss_apk", True, "latest"),
            ],
            all_ready=True,
        )

        result = provisioner.provision_all()
        assert result is True
        mock_p_java.assert_not_called()
