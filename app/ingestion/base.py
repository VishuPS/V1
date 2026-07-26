from abc import ABC, abstractmethod
from collections.abc import Iterator

from typing import Any


class ProductSource(ABC):
    @abstractmethod
    def records(self) -> Iterator[dict[str, Any]]:
        """Stream raw source records without loading the dataset into memory."""
