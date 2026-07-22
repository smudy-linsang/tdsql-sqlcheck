"""
解析器基类
"""
from abc import ABC, abstractmethod
from typing import Any

class BaseParser(ABC):
    @abstractmethod
    def parse(self, sql: str) -> Any:
        pass
