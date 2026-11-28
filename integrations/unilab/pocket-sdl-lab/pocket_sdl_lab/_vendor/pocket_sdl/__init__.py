"""Pico H + AS7341 + WS2812B 自主光谱实验。"""

__version__ = "1.0.0"


class LabError(Exception):
    """可预期且具有审计分类的实验错误。"""

    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def fail(code, message):
    raise LabError(code, message)
