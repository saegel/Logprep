# pylint: disable=missing-docstring
# pylint: disable=protected-access
# pylint: disable=attribute-defined-outside-init
from copy import deepcopy
from logging import DEBUG, WARNING, getLogger
from multiprocessing import active_children, Lock
from unittest import mock

from _pytest.outcomes import fail
from _pytest.python_api import raises

from logprep._version import get_versions
from logprep.abc import Processor
from logprep.framework.pipeline import (
    MultiprocessingPipeline,
    MustProvideAnMPLogHandlerError,
    Pipeline,
    MustProvideALogHandlerError,
    SharedCounter,
)
from logprep.connector.dummy.input import DummyInput
from logprep.abc.input import (
    SourceDisconnectedError,
    FatalInputError,
    WarningInputError,
    CriticalInputError,
)
from logprep.metrics.metric import MetricTargets
from logprep.connector.dummy.output import DummyOutput
from logprep.abc.output import FatalOutputError, WarningOutputError, CriticalOutputError
from logprep.processor.base.exceptions import ProcessingWarning
from logprep.processor.deleter.processor import Deleter
from logprep.processor.deleter.rule import DeleterRule
from logprep.processor.processor_configuration import ProcessorConfiguration
from logprep.util.multiprocessing_log_handler import MultiprocessingLogHandler


class ConfigurationForTests:
    logprep_config = {
        "version": 1,
        "timeout": 0.001,
        "print_processed_period": 600,
        "connector": {"type": "dummy", "input": [{"test": "empty"}]},
        "pipeline": [{"mock_processor1": {"proc": "conf"}}, {"mock_processor2": {"proc": "conf"}}],
        "metrics": {"period": 300, "enabled": False},
    }
    log_handler = MultiprocessingLogHandler(WARNING)
    lock = Lock()
    shared_dict = {}
    metric_targets = MetricTargets(file_target=getLogger("Mock"), prometheus_target=None)
    counter = SharedCounter()


class NotJsonSerializableMock:
    pass


class ProcessorWarningMockError(ProcessingWarning):
    def __init__(self):
        super().__init__("ProcessorWarningMockError")


@mock.patch("logprep.processor.processor_factory.ProcessorFactory.create")
class TestPipeline(ConfigurationForTests):
    def setup_method(self):
        self._check_failed_stored = None

        self.pipeline = Pipeline(
            pipeline_index=1,
            config=self.logprep_config,
            counter=self.counter,
            log_handler=self.log_handler,
            lock=self.lock,
            shared_dict=self.shared_dict,
            metric_targets=self.metric_targets,
        )

    def test_fails_if_log_handler_is_not_of_type_loghandler(self, _):
        for not_a_log_handler in [None, 123, 45.67, TestPipeline()]:
            with raises(MustProvideALogHandlerError):
                _ = Pipeline(
                    pipeline_index=1,
                    config=self.logprep_config,
                    counter=self.counter,
                    log_handler=not_a_log_handler,
                    lock=self.lock,
                    shared_dict=self.shared_dict,
                    metric_targets=self.metric_targets,
                )

    def test_setup_builds_pipeline(self, mock_create):
        assert len(self.pipeline._pipeline) == 0
        self.pipeline._setup()
        assert len(self.pipeline._pipeline) == 2
        assert mock_create.call_count == 2

    def test_setup_calls_setup_on_pipeline_processors(self, _):
        self.pipeline._setup()
        assert len(self.pipeline._pipeline) == 2
        for processor in self.pipeline._pipeline:
            processor.setup.assert_called()

    def test_shut_down_calls_shut_down_on_pipeline_processors(self, _):
        self.pipeline._setup()
        processors = list(self.pipeline._pipeline)
        self.pipeline._shut_down()
        for processor in processors:
            processor.shut_down.assert_called()

    def test_setup_creates_connectors(self, _):
        assert self.pipeline._input is None
        assert self.pipeline._output is None

        self.pipeline._setup()

        assert isinstance(self.pipeline._input, DummyInput)
        assert isinstance(self.pipeline._output, DummyOutput)

    def test_setup_calls_setup_on_input_and_output(self, _):
        self.pipeline._setup()

        assert self.pipeline._input.setup_called_count == 1
        assert self.pipeline._output.setup_called_count == 1

    def test_passes_timeout_parameter_to_inputs_get_next(self, _):
        self.pipeline._setup()
        assert self.pipeline._input.last_timeout is None

        self.pipeline._retrieve_and_process_data()

        assert self.pipeline._input.last_timeout == self.logprep_config.get("timeout")

    def test_empty_documents_are_not_forwarded_to_other_processors(self, _):
        assert len(self.pipeline._pipeline) == 0
        input_data = [{"do_not_delete": "1"}, {"delete_me": "2"}, {"do_not_delete": "3"}]
        connector_config = {"type": "dummy", "input": input_data}
        self.pipeline._logprep_config["connector"] = connector_config
        self.pipeline._setup()
        deleter_config = {
            "type": "deleter",
            "specific_rules": ["tests/testdata/unit/deleter/rules/specific"],
            "generic_rules": ["tests/testdata/unit/deleter/rules/generic"],
        }
        processor_configuration = ProcessorConfiguration.create("deleter processor", deleter_config)
        processor_configuration.metric_labels = {}
        deleter_processor = Deleter("deleter processor", processor_configuration, mock.MagicMock())
        deleter_rule = DeleterRule._create_from_dict({"filter": "delete_me", "delete": True})
        deleter_processor._specific_tree.add_rule(deleter_rule)
        self.pipeline._pipeline = [mock.MagicMock(), deleter_processor, mock.MagicMock()]
        self.pipeline._create_logger()
        self.pipeline._logger.setLevel(DEBUG)
        while self.pipeline._input._documents:
            self.pipeline._retrieve_and_process_data()
        assert len(input_data) == 0, "all events were processed"
        assert self.pipeline._pipeline[0].process.call_count == 3, "called for all events"
        assert self.pipeline._pipeline[2].process.call_count == 2, "not called for deleted event"
        assert {"delete_me": "2"} not in self.pipeline._output.events
        assert len(self.pipeline._output.events) == 2

    def test_empty_documents_are_not_stored_in_the_output(self, _):
        self.pipeline._process_event = lambda x: x.clear()
        self.pipeline.run()
        assert len(self.pipeline._output.events) == 0, "output is emty after processing events"

    @mock.patch("logprep.connector.dummy.input.DummyInput.setup")
    def test_setup_calls_setup_on_input(self, mock_setup, _):
        self.pipeline.run()
        mock_setup.assert_called()

    @mock.patch("logprep.connector.dummy.output.DummyOutput.setup")
    def test_setup_calls_setup_on_output(self, mock_setup, _):
        self.pipeline.run()
        mock_setup.assert_called()

    @mock.patch("logprep.connector.dummy.input.DummyInput.shut_down")
    def test_shut_down_calls_shut_down_on_input(self, mock_shut_down, _):
        self.pipeline.run()
        mock_shut_down.assert_called()

    @mock.patch("logprep.connector.dummy.output.DummyOutput.shut_down")
    def test_shut_down_calls_shut_down_on_output(self, mock_shut_down, _):
        self.pipeline.run()
        mock_shut_down.assert_called()

    @mock.patch("logging.Logger.warning")
    @mock.patch("logprep.connector.dummy.input.DummyInput.get_next")
    def test_logs_source_disconnected_error_as_warning(self, mock_get_next, mock_warning, _):
        mock_get_next.side_effect = SourceDisconnectedError
        self.pipeline.run()
        mock_warning.assert_called()
        assert "Lost or failed to establish connection to dummy" in mock_warning.call_args[0]

    def test_all_events_provided_by_input_arrive_at_output(self, _):
        input_data = [{"test": "1"}, {"test": "2"}, {"test": "3"}]
        expected_output_data = deepcopy(input_data)
        connector_config = {"type": "dummy", "input": input_data}
        self.pipeline._logprep_config["connector"] = connector_config
        self.pipeline._setup()
        self.pipeline.run()
        assert self.pipeline._output.events == expected_output_data

    def test_enable_iteration_sets_iterate_to_true_stop_to_false(self, _):
        assert not self.pipeline._iterate()

        self.pipeline._enable_iteration()
        assert self.pipeline._iterate()

        self.pipeline.stop()
        assert not self.pipeline._iterate()

    @mock.patch("logging.Logger.error")
    @mock.patch("logprep.connector.dummy.input.DummyInput.get_next")
    def test_critical_input_error_is_logged_and_stored_as_failed(
        self, mock_get_next, mock_error, _
    ):
        def raise_critical_input_error(event):
            raise CriticalInputError("An error message", event)

        mock_get_next.side_effect = raise_critical_input_error
        self.pipeline._setup()
        self.pipeline._retrieve_and_process_data()
        assert len(self.pipeline._output.events) == 0
        mock_error.assert_called()
        assert (
            "A critical error occurred for input dummy: An error message" in mock_error.call_args[0]
        )
        assert len(self.pipeline._output.failed_events) == 1

    @mock.patch("logging.Logger.error")
    @mock.patch("logprep.connector.dummy.output.DummyOutput.store")
    @mock.patch("logprep.connector.dummy.input.DummyInput.get_next")
    def test_critical_output_error_is_logged_and_stored_as_failed(
        self, mock_get_next, mock_store, mock_error, _
    ):
        mock_get_next.return_value = {"order": 1}

        def raise_critical_output_error(event):
            raise CriticalOutputError("An error message", event)

        mock_store.side_effect = raise_critical_output_error
        self.pipeline._setup()
        self.pipeline._retrieve_and_process_data()
        assert len(self.pipeline._output.events) == 0
        mock_error.assert_called()
        assert (
            "A critical error occurred for output dummy: An error message"
            in mock_error.call_args[0]
        )
        assert len(self.pipeline._output.failed_events) == 1

    @mock.patch("logging.Logger.warning")
    @mock.patch("logprep.connector.dummy.input.DummyInput.get_next")
    def test_input_warning_error_is_logged_but_processing_continues(
        self, mock_get_next, mock_warning, _
    ):
        mock_get_next.return_value = {"order": 1}
        self.pipeline._setup()
        self.pipeline._retrieve_and_process_data()
        mock_get_next.side_effect = WarningInputError
        self.pipeline._retrieve_and_process_data()
        mock_get_next.side_effect = None
        self.pipeline._retrieve_and_process_data()
        assert mock_get_next.call_count == 3
        assert mock_warning.call_count == 1
        assert len(self.pipeline._output.events) == 2

    @mock.patch("logging.Logger.warning")
    @mock.patch("logprep.connector.dummy.output.DummyOutput.store")
    @mock.patch("logprep.connector.dummy.input.DummyInput.get_next")
    def test_output_warning_error_is_logged_but_processing_continues(
        self, mock_get_next, mock_store, mock_warning, _
    ):
        mock_get_next.return_value = {"order": 1}
        self.pipeline._setup()
        self.pipeline._retrieve_and_process_data()
        mock_store.side_effect = WarningOutputError
        self.pipeline._retrieve_and_process_data()
        mock_store.side_effect = None
        self.pipeline._retrieve_and_process_data()
        assert mock_get_next.call_count == 3
        assert mock_warning.call_count == 1
        assert mock_store.call_count == 3

    @mock.patch("logging.Logger.warning")
    def test_processor_warning_error_is_logged_but_processing_continues(self, mock_warning, _):
        input_data = [{"order": 0}, {"order": 1}]
        connector_config = {"type": "dummy", "input": input_data}
        self.pipeline._logprep_config["connector"] = connector_config
        self.pipeline._create_logger()
        self.pipeline._create_connectors()
        error_mock = mock.MagicMock()
        error_mock.process = mock.MagicMock()
        error_mock.process.side_effect = ProcessorWarningMockError
        self.pipeline._pipeline = [
            mock.MagicMock(),
            error_mock,
            mock.MagicMock(),
        ]
        self.pipeline._retrieve_and_process_data()
        self.pipeline._retrieve_and_process_data()
        mock_warning.assert_called()
        assert (
            "ProcessorWarningMockError" in mock_warning.call_args[0][0]
        ), "the log message was written"
        assert len(self.pipeline._output.events) == 2, "all events are processed"

    @mock.patch("logging.Logger.error")
    def test_processor_critical_error_is_logged_event_is_stored_in_error_output(
        self, mock_error, _
    ):
        input_data = [{"order": 0}, {"order": 1}]
        connector_config = {"type": "dummy", "input": input_data}
        self.pipeline._logprep_config["connector"] = connector_config
        self.pipeline._create_logger()
        self.pipeline._create_connectors()
        error_mock = mock.MagicMock()
        error_mock.process = mock.MagicMock()
        error_mock.process.side_effect = Exception
        self.pipeline._pipeline = [
            mock.MagicMock(),
            error_mock,
            mock.MagicMock(),
        ]
        self.pipeline._output.store_failed = mock.MagicMock()
        self.pipeline._retrieve_and_process_data()
        self.pipeline._retrieve_and_process_data()
        mock_error.assert_called()
        assert (
            "A critical error occurred for processor" in mock_error.call_args[0][0]
        ), "the log message was written"
        assert len(self.pipeline._output.events) == 0, "no event in output"
        assert (
            self.pipeline._output.store_failed.call_count == 2
        ), "errored events are gone to connector error output handler"

    @mock.patch("logprep.connector.dummy.input.DummyInput.get_next")
    @mock.patch("logging.Logger.error")
    def test_critical_input_error_is_logged_error_is_stored_in_failed_events(
        self, mock_error, mock_get_next, _
    ):
        def raise_critical(args):
            raise CriticalInputError("mock input error", args)

        mock_get_next.side_effect = raise_critical
        self.pipeline._setup()
        self.pipeline._retrieve_and_process_data()
        mock_get_next.assert_called()
        mock_error.assert_called()
        assert (
            "A critical error occurred for input dummy: mock input error" in mock_error.call_args[0]
        ), "error message is logged"
        assert len(self.pipeline._output.failed_events) == 1
        assert len(self.pipeline._output.events) == 0

    @mock.patch("logprep.connector.dummy.input.DummyInput.get_next")
    @mock.patch("logging.Logger.warning")
    def test_input_warning_is_logged(self, mock_warning, mock_get_next, _):
        def raise_warning(args):
            raise WarningInputError("mock input warning", args)

        mock_get_next.side_effect = raise_warning
        self.pipeline._setup()
        self.pipeline._retrieve_and_process_data()
        mock_get_next.assert_called()
        mock_warning.assert_called()
        assert (
            "An error occurred for input dummy:" in mock_warning.call_args[0][0]
        ), "error message is logged"

    @mock.patch("logprep.connector.dummy.input.DummyInput.get_next", return_value={"mock": "event"})
    @mock.patch("logprep.connector.dummy.output.DummyOutput.store")
    @mock.patch("logging.Logger.error")
    def test_critical_output_error_is_logged(self, mock_error, mock_store, _, __):
        def raise_critical(args):
            raise CriticalOutputError("mock output error", args)

        mock_store.side_effect = raise_critical
        self.pipeline._setup()
        self.pipeline._retrieve_and_process_data()
        mock_store.assert_called()
        mock_error.assert_called()
        assert (
            "A critical error occurred for output dummy: mock output error"
            in mock_error.call_args[0]
        ), "error message is logged"

    @mock.patch("logprep.connector.dummy.input.DummyInput.get_next", return_value={"mock": "event"})
    @mock.patch("logprep.connector.dummy.output.DummyOutput.store")
    @mock.patch("logging.Logger.warning")
    def test_warning_output_error_is_logged(self, mock_warning, mock_store, _, __):
        def raise_warning(args):
            raise WarningOutputError("mock output warning", args)

        mock_store.side_effect = raise_warning
        self.pipeline._setup()
        self.pipeline._retrieve_and_process_data()
        mock_store.assert_called()
        mock_warning.assert_called()
        assert (
            "An error occurred for output dummy:" in mock_warning.call_args[0][0]
        ), "error message is logged"

    @mock.patch("logprep.framework.pipeline.Pipeline._shut_down")
    @mock.patch("logprep.connector.dummy.input.DummyInput.get_next")
    @mock.patch("logging.Logger.error")
    def test_processor_fatal_input_error_is_logged_pipeline_is_rebuilt(
        self, mock_error, mock_get_next, mock_shut_down, _
    ):
        mock_get_next.side_effect = FatalInputError
        self.pipeline.run()
        mock_get_next.assert_called()
        mock_error.assert_called()
        assert "Input dummy failed:" in mock_error.call_args[0][0], "error message is logged"
        mock_shut_down.assert_called()

    @mock.patch("logprep.connector.dummy.input.DummyInput.get_next", return_value={"mock": "event"})
    @mock.patch("logprep.framework.pipeline.Pipeline._shut_down")
    @mock.patch("logprep.connector.dummy.output.DummyOutput.store")
    @mock.patch("logging.Logger.error")
    def test_processor_fatal_output_error_is_logged_pipeline_is_rebuilt(
        self, mock_error, mock_store, mock_shut_down, _, __
    ):
        mock_store.side_effect = FatalOutputError
        self.pipeline.run()
        mock_store.assert_called()
        mock_error.assert_called()
        assert "Output dummy failed:" in mock_error.call_args[0][0], "error message is logged"
        mock_shut_down.assert_called()

    @mock.patch("logprep.connector.dummy.output.DummyOutput.store_custom")
    @mock.patch("logprep.connector.dummy.input.DummyInput.get_next", return_value={"mock": "event"})
    def test_extra_dat_tuple_is_passed_to_store_custom(self, mock_get_next, mock_store_custom, _):
        self.pipeline._setup()
        processor_with_exta_data = mock.MagicMock()
        processor_with_exta_data.process = mock.MagicMock()
        processor_with_exta_data.process.return_value = ([{"foo": "bar"}], "target")
        self.pipeline._pipeline = [mock.MagicMock(), processor_with_exta_data, mock.MagicMock()]
        self.pipeline._retrieve_and_process_data()
        mock_get_next.call_count = 1
        mock_store_custom.call_count = 1
        mock_store_custom.assert_called_with({"foo": "bar"}, "target")

    @mock.patch("logprep.connector.dummy.output.DummyOutput.store_custom")
    @mock.patch("logprep.connector.dummy.input.DummyInput.get_next", return_value={"mock": "event"})
    def test_extra_dat_list_is_passed_to_store_custom(self, mock_get_next, mock_store_custom, _):
        self.pipeline._setup()
        processor_with_exta_data = mock.MagicMock()
        processor_with_exta_data.process = mock.MagicMock()
        processor_with_exta_data.process.return_value = [([{"foo": "bar"}], "target")]
        self.pipeline._pipeline = [mock.MagicMock(), processor_with_exta_data, mock.MagicMock()]
        self.pipeline._retrieve_and_process_data()
        mock_get_next.call_count = 1
        mock_store_custom.call_count = 1
        mock_store_custom.assert_called_with({"foo": "bar"}, "target")

    def test_pipeline_metrics_number_of_events_counts_events_of_all_processor_metrics(
        self,
        _,
    ):
        mock_metrics_one = Processor.ProcessorMetrics(
            labels={"any": "label"},
            generic_rule_tree=mock.MagicMock(),
            specific_rule_tree=mock.MagicMock(),
        )
        mock_metrics_one.number_of_processed_events = 1
        mock_metrics_two = Processor.ProcessorMetrics(
            labels={"any_other": "label"},
            generic_rule_tree=mock.MagicMock(),
            specific_rule_tree=mock.MagicMock(),
        )
        mock_metrics_two.number_of_processed_events = 1
        self.pipeline._setup()
        self.pipeline.metrics.pipeline = [mock_metrics_one, mock_metrics_two]
        assert self.pipeline.metrics.number_of_processed_events == 2

    def test_pipeline_metrics_number_of_warnings_counts_warnings_of_all_processor_metrics(
        self,
        _,
    ):
        mock_metrics_one = Processor.ProcessorMetrics(
            labels={"any": "label"},
            generic_rule_tree=mock.MagicMock(),
            specific_rule_tree=mock.MagicMock(),
        )
        mock_metrics_one.number_of_warnings = 1
        mock_metrics_two = Processor.ProcessorMetrics(
            labels={"any_other": "label"},
            generic_rule_tree=mock.MagicMock(),
            specific_rule_tree=mock.MagicMock(),
        )
        mock_metrics_two.number_of_warnings = 1
        self.pipeline._setup()
        self.pipeline.metrics.pipeline = [mock_metrics_one, mock_metrics_two]
        assert self.pipeline.metrics.number_of_warnings == 2

    def test_pipeline_metrics_number_of_errors_counts_errors_of_all_processor_metrics(
        self,
        _,
    ):
        mock_metrics_one = Processor.ProcessorMetrics(
            labels={"any": "label"},
            generic_rule_tree=mock.MagicMock(),
            specific_rule_tree=mock.MagicMock(),
        )
        mock_metrics_one.number_of_errors = 1
        mock_metrics_two = Processor.ProcessorMetrics(
            labels={"any_other": "label"},
            generic_rule_tree=mock.MagicMock(),
            specific_rule_tree=mock.MagicMock(),
        )
        mock_metrics_two.number_of_errors = 1
        self.pipeline._setup()
        self.pipeline.metrics.pipeline = [mock_metrics_one, mock_metrics_two]
        assert self.pipeline.metrics.number_of_errors == 2

    def test_pipeline_preprocessing_adds_versions_if_configured(self, _):
        preprocessing_config = {"version_info_target_field": "version_info"}
        self.pipeline._logprep_config["connector"] = {
            "consumer": {"preprocessing": preprocessing_config}
        }
        test_event = {"any": "content"}
        self.pipeline._preprocess_event(test_event)
        target_field = preprocessing_config.get("version_info_target_field")
        assert target_field in test_event
        assert test_event.get(target_field, {}).get("logprep") == get_versions()["version"]
        expected_config_version = self.logprep_config["version"]
        assert test_event.get(target_field, {}).get("configuration") == expected_config_version

    def test_pipeline_preprocessing_does_not_add_versions_if_not_configured(self, _):
        preprocessing_config = {"something": "random"}
        self.pipeline._logprep_config["connector"] = {
            "consumer": {"preprocessing": preprocessing_config}
        }
        test_event = {"any": "content"}
        self.pipeline._preprocess_event(test_event)
        assert test_event == {"any": "content"}

    def test_pipeline_preprocessing_does_not_add_versions_if_target_field_exists_already(self, _):
        preprocessing_config = {"version_info_target_field": "version_info"}
        self.pipeline._connector_config = {"consumer": {"preprocessing": preprocessing_config}}
        test_event = {"any": "content", "version_info": "something random"}
        self.pipeline._preprocess_event(test_event)
        assert test_event == {"any": "content", "version_info": "something random"}

    @mock.patch("logprep.connector.confluent_kafka.input.Consumer")
    @mock.patch("logprep.connector.confluent_kafka.output.Producer")
    def test_pipeline_kafka_batch_finished_callback_is_called(self, _, __, ___):
        logprep_config = {
            "version": 1,
            "timeout": 0.001,
            "print_processed_period": 600,
            "connector": {
                "type": "confluentkafka",
                "bootstrapservers": "127.0.0.1:9092",
                "consumer": {
                    "topic": "Consumer",
                    "group": "cgroup",
                    "auto_commit": True,
                    "session_timeout": 6000,
                    "offset_reset_policy": "smallest",
                    "enable_auto_offset_store": False,
                },
                "producer": {
                    "topic": "producer",
                    "error_topic": "producer_error",
                    "ack_policy": "all",
                    "compression": "gzip",
                    "maximum_backlog": 10000,
                    "linger_duration": 0,
                    "flush_timeout": 30,
                    "send_timeout": 2,
                },
            },
            "pipeline": [
                {"mock_processor1": {"proc": "conf"}},
            ],
            "metrics": {"period": 300, "enabled": False},
        }
        pipeline = Pipeline(
            pipeline_index=1,
            config=logprep_config,
            counter=self.counter,
            log_handler=self.log_handler,
            lock=self.lock,
            shared_dict=self.shared_dict,
            metric_targets=self.metric_targets,
        )
        pipeline._setup()
        pipeline._input.get_next = mock.MagicMock()
        pipeline._input.get_next.return_value = {"message": "foo"}
        pipeline._input.batch_finished_callback = mock.MagicMock()
        pipeline._retrieve_and_process_data()
        pipeline._input.batch_finished_callback.assert_called()

    @mock.patch("logprep.connector.confluent_kafka.input.Consumer")
    @mock.patch("logprep.connector.confluent_kafka.output.Producer")
    def test_pipeline_kafka_batch_finished_callback_calls_store_offsets(self, _, __, ___):
        logprep_config = {
            "version": 1,
            "timeout": 0.001,
            "print_processed_period": 600,
            "connector": {
                "type": "confluentkafka",
                "bootstrapservers": "127.0.0.1:9092",
                "consumer": {
                    "topic": "Consumer",
                    "group": "cgroup",
                    "auto_commit": True,
                    "session_timeout": 6000,
                    "offset_reset_policy": "smallest",
                    "enable_auto_offset_store": False,
                },
                "producer": {
                    "topic": "producer",
                    "error_topic": "producer_error",
                    "ack_policy": "all",
                    "compression": "gzip",
                    "maximum_backlog": 10000,
                    "linger_duration": 0,
                    "flush_timeout": 30,
                    "send_timeout": 2,
                },
            },
            "pipeline": [
                {"mock_processor1": {"proc": "conf"}},
            ],
            "metrics": {"period": 300, "enabled": False},
        }
        pipeline = Pipeline(
            pipeline_index=1,
            config=logprep_config,
            counter=self.counter,
            log_handler=self.log_handler,
            lock=self.lock,
            shared_dict=self.shared_dict,
            metric_targets=self.metric_targets,
        )
        pipeline._setup()
        pipeline._input.get_next = mock.MagicMock()
        pipeline._input.get_next.return_value = {"message": "foo"}
        pipeline._input._last_valid_records = {"record1": "record_value5"}
        pipeline._input._consumer = mock.MagicMock()
        pipeline._retrieve_and_process_data()
        pipeline._input._consumer.store_offsets.assert_called()

    @mock.patch("logprep.connector.confluent_kafka.input.Consumer")
    @mock.patch("logprep.connector.confluent_kafka.output.Producer")
    def test_pipeline_kafka_batch_finished_callback_calls_store_offsets_with_message(
        self, _, __, ___
    ):
        logprep_config = {
            "version": 1,
            "timeout": 0.001,
            "print_processed_period": 600,
            "connector": {
                "type": "confluentkafka",
                "bootstrapservers": "127.0.0.1:9092",
                "consumer": {
                    "topic": "Consumer",
                    "group": "cgroup",
                    "auto_commit": True,
                    "session_timeout": 6000,
                    "offset_reset_policy": "smallest",
                    "enable_auto_offset_store": False,
                },
                "producer": {
                    "topic": "producer",
                    "error_topic": "producer_error",
                    "ack_policy": "all",
                    "compression": "gzip",
                    "maximum_backlog": 10000,
                    "linger_duration": 0,
                    "flush_timeout": 30,
                    "send_timeout": 2,
                },
            },
            "pipeline": [
                {"mock_processor1": {"proc": "conf"}},
            ],
            "metrics": {"period": 300, "enabled": False},
        }
        pipeline = Pipeline(
            pipeline_index=1,
            config=logprep_config,
            counter=self.counter,
            log_handler=self.log_handler,
            lock=self.lock,
            shared_dict=self.shared_dict,
            metric_targets=self.metric_targets,
        )
        pipeline._setup()
        pipeline._input.get_next = mock.MagicMock()
        pipeline._input.get_next.return_value = {"message": "foo"}
        pipeline._input._last_valid_records = {"record1": "record_value5"}
        pipeline._input._consumer = mock.MagicMock()
        pipeline._retrieve_and_process_data()
        pipeline._input._consumer.store_offsets.assert_called_with(message="record_value5")


class TestMultiprocessingPipeline(ConfigurationForTests):
    def setup_class(self):
        self.log_handler = MultiprocessingLogHandler(DEBUG)

    def test_fails_if_log_handler_is_not_a_multiprocessing_log_handler(self):
        for not_a_log_handler in [None, 123, 45.67, TestMultiprocessingPipeline()]:
            with raises(MustProvideAnMPLogHandlerError):
                MultiprocessingPipeline(
                    pipeline_index=1,
                    config=self.logprep_config,
                    log_handler=not_a_log_handler,
                    lock=self.lock,
                    shared_dict=self.shared_dict,
                )

    def test_does_not_fail_if_log_handler_is_a_multiprocessing_log_handler(self):
        try:
            MultiprocessingPipeline(
                pipeline_index=1,
                config=self.logprep_config,
                log_handler=self.log_handler,
                lock=self.lock,
                shared_dict=self.shared_dict,
            )
        except MustProvideAnMPLogHandlerError:
            fail("Must not raise this error for a correct handler!")

    def test_creates_a_new_process(self):
        children_before = active_children()
        children_running = self.start_and_stop_pipeline(
            MultiprocessingPipeline(
                pipeline_index=1,
                config=self.logprep_config,
                log_handler=self.log_handler,
                lock=self.lock,
                shared_dict=self.shared_dict,
            )
        )

        assert len(children_running) == (len(children_before) + 1)

    def test_stop_terminates_the_process(self):
        children_running = self.start_and_stop_pipeline(
            MultiprocessingPipeline(
                pipeline_index=1,
                config=self.logprep_config,
                log_handler=self.log_handler,
                lock=self.lock,
                shared_dict=self.shared_dict,
            )
        )
        children_after = active_children()

        assert len(children_after) == (len(children_running) - 1)

    def test_enable_iteration_sets_iterate_to_true_stop_to_false(self):
        pipeline = MultiprocessingPipeline(
            pipeline_index=1,
            config=self.logprep_config,
            log_handler=self.log_handler,
            lock=self.lock,
            shared_dict=self.shared_dict,
        )
        assert not pipeline._iterate()

        pipeline._enable_iteration()
        assert pipeline._iterate()

        pipeline.stop()
        assert not pipeline._iterate()

    @staticmethod
    def start_and_stop_pipeline(wrapper):
        wrapper.start()
        children_running = active_children()

        wrapper.stop()
        wrapper.join()

        return children_running
