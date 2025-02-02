"""This module contains a dummy input that can be used for testing purposes."""


from logprep.abc.input import CriticalInputError, Input, SourceDisconnectedError
from logprep.util.json_handling import parse_jsonl


class JsonlInput(Input):
    """A json line input that returns the documents it was initialized with.

    If a "document" is derived from BaseException, that exception will be thrown instead of
    returning a document. The exception will be removed and subsequent calls may return documents or
    throw other exceptions in the given order.

    Parameters
    ----------
    documents_path : string
       A path to a file in json line format.

    """

    def __init__(self, documents_path: str):
        self._documents = parse_jsonl(documents_path)

        self.last_timeout = None
        self.setup_called_count = 0
        self.shut_down_called_count = 0

    def describe_endpoint(self) -> str:
        return "jsonl"

    def setup(self):
        self.setup_called_count += 1

    def get_next(self, timeout: float) -> dict:
        self.last_timeout = timeout
        if not self._documents:
            raise SourceDisconnectedError

        document = self._documents.pop(0)

        if not isinstance(document, dict):
            raise CriticalInputError("not a dict", document)
        return document

    def shut_down(self):
        self.shut_down_called_count += 1
