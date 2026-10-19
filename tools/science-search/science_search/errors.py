# -*- coding: utf-8 -*-
"""Ошибки инструмента: машиночитаемый код, код возврата CLI, подсказка."""


class ScienceError(Exception):
    code = "error"
    exit_code = 1

    def __init__(self, message, code=None, hint=None, data=None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.hint = hint
        self.data = data

    def to_dict(self):
        err = {"code": self.code, "message": self.message}
        if self.hint:
            err["hint"] = self.hint
        if self.data:
            err["data"] = self.data
        return {"error": err}


class UsageError(ScienceError):
    code, exit_code = "usage", 2


class NotFound(ScienceError):
    code, exit_code = "not_found", 7


class NetworkError(ScienceError):
    code, exit_code = "network_error", 6


class SourceSkipped(Exception):
    """Источник временно недоступен (лимит запросов, нет ключа): поиск продолжается по остальным."""
