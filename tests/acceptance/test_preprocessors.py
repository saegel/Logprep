# pylint: disable=missing-docstring
# pylint: disable=wrong-import-order
# pylint: disable=no-self-use
import json
from unittest.mock import patch

import pytest

from logprep._version import get_versions
from logprep.util.json_handling import dump_config_as_file
from tests.acceptance.util import mock_kafka_and_run_pipeline, get_default_logprep_config


@pytest.fixture(name="config")
def fixture_config():
    pipeline = [
        {
            "normalizername": {
                "type": "normalizer",
                "specific_rules": ["tests/testdata/acceptance/normalizer/rules_static/specific"],
                "generic_rules": ["tests/testdata/acceptance/normalizer/rules_static/generic"],
                "regex_mapping": "tests/testdata/acceptance/normalizer/rules_static/"
                "regex_mapping.yml",
            }
        }
    ]
    return get_default_logprep_config(pipeline)


class TestFullHMACPass:
    def test_full_message_pass_with_hmac(self, tmp_path, config):
        config_path = str(tmp_path / "generated_config.yml")
        dump_config_as_file(config_path, config)

        with patch(
            "logprep.connector.connector_factory.ConnectorFactory.create"
        ) as mock_connector_factory:
            input_test_event = {"test": "message"}
            expected_output_event = {
                "test": "message",
                "hmac": {
                    "hmac": "5a77054b5f1d9ea60000520a4e2cf661e7a11de4205ca70c6977fc8040076a6e",
                    "compressed_base64": "eJyrVipJLS5RslLKTS0uTkxPVaoFADwCBmA=",
                },
            }

            kafka_output_file = mock_kafka_and_run_pipeline(
                config, input_test_event, mock_connector_factory, tmp_path
            )

            # read logprep kafka output from mocked kafka file producer
            with open(kafka_output_file, "r", encoding="utf-8") as output_file:
                outputs = output_file.readlines()
                assert len(outputs) == 1, "Expected only one default kafka output"

                target, event = outputs[0].split(" ", maxsplit=1)
                event = json.loads(event)
                assert target == "test_input_processed"
                assert event == expected_output_event

    def test_full_message_pass_with_new_hmac_config_position(self, tmp_path, config):
        config["connector"]["consumer"]["preprocessing"] = {
            "hmac": config["connector"]["consumer"]["hmac"]
        }
        del config["connector"]["consumer"]["hmac"]
        config_path = str(tmp_path / "generated_config.yml")
        dump_config_as_file(config_path, config)

        with patch(
            "logprep.connector.connector_factory.ConnectorFactory.create"
        ) as mock_connector_factory:
            input_test_event = {"test": "message"}
            expected_output_event = {
                "test": "message",
                "hmac": {
                    "hmac": "5a77054b5f1d9ea60000520a4e2cf661e7a11de4205ca70c6977fc8040076a6e",
                    "compressed_base64": "eJyrVipJLS5RslLKTS0uTkxPVaoFADwCBmA=",
                },
            }

            kafka_output_file = mock_kafka_and_run_pipeline(
                config, input_test_event, mock_connector_factory, tmp_path
            )

            # read logprep kafka output from mocked kafka file producer
            with open(kafka_output_file, "r", encoding="utf-8") as output_file:
                outputs = output_file.readlines()
                assert len(outputs) == 1, "Expected only one default kafka output"

                target, event = outputs[0].split(" ", maxsplit=1)
                event = json.loads(event)
                assert target == "test_input_processed"
                assert event == expected_output_event


class TestVersionInfoTargetField:
    def test_version_info_target_field_will_be_added_if_configured(self, tmp_path, config):
        config["connector"]["consumer"]["preprocessing"] = {
            "version_info_target_field": "version_info"
        }
        config_path = str(tmp_path / "generated_config.yml")
        dump_config_as_file(config_path, config)

        with patch(
            "logprep.connector.connector_factory.ConnectorFactory.create"
        ) as mock_connector_factory:
            input_test_event = {"test": "message"}
            expected_output_event = {
                "test": "message",
                "hmac": {
                    "hmac": "5a77054b5f1d9ea60000520a4e2cf661e7a11de4205ca70c6977fc8040076a6e",
                    "compressed_base64": "eJyrVipJLS5RslLKTS0uTkxPVaoFADwCBmA=",
                },
                "version_info": {
                    "logprep": get_versions().get("version"),
                    "configuration": "unset",
                },
            }

            kafka_output_file = mock_kafka_and_run_pipeline(
                config, input_test_event, mock_connector_factory, tmp_path
            )

            # read logprep kafka output from mocked kafka file producer
            with open(kafka_output_file, "r", encoding="utf-8") as output_file:
                outputs = output_file.readlines()
                assert len(outputs) == 1, "Expected only one default kafka output"

                target, event = outputs[0].split(" ", maxsplit=1)
                event = json.loads(event)
                assert target == "test_input_processed"
                assert event == expected_output_event

    def test_version_info_target_field_will_not_be_added_if_not_configured(self, tmp_path, config):
        consumer_config = config.get("connector", {}).get("consumer", {})
        preprocessing_config = consumer_config.get("preprocessing", {})
        assert preprocessing_config.get("version_info_target_field") is None
        config_path = str(tmp_path / "generated_config.yml")
        dump_config_as_file(config_path, config)

        with patch(
            "logprep.connector.connector_factory.ConnectorFactory.create"
        ) as mock_connector_factory:
            input_test_event = {"test": "message"}
            expected_output_event = {
                "test": "message",
                "hmac": {
                    "hmac": "5a77054b5f1d9ea60000520a4e2cf661e7a11de4205ca70c6977fc8040076a6e",
                    "compressed_base64": "eJyrVipJLS5RslLKTS0uTkxPVaoFADwCBmA=",
                },
            }

            kafka_output_file = mock_kafka_and_run_pipeline(
                config, input_test_event, mock_connector_factory, tmp_path
            )

            # read logprep kafka output from mocked kafka file producer
            with open(kafka_output_file, "r", encoding="utf-8") as output_file:
                outputs = output_file.readlines()
                assert len(outputs) == 1, "Expected only one default kafka output"

                target, event = outputs[0].split(" ", maxsplit=1)
                event = json.loads(event)
                assert target == "test_input_processed"
                assert event == expected_output_event
