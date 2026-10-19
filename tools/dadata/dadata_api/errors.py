# -*- coding: utf-8 -*-
"""Ошибки инструмента: машиночитаемый код, код возврата CLI, подсказка."""


class DadataError(Exception):
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


class UsageError(DadataError):
    code, exit_code = "usage", 2


class AuthError(DadataError):
    """Ключа нет или DaData его не приняла."""
    code, exit_code = "auth", 3


class LimitError(DadataError):
    """403 (почта не подтверждена, лимит суток исчерпан) или 429 (слишком часто)."""
    code, exit_code = "limit", 5


class NetworkError(DadataError):
    code, exit_code = "network_error", 6


class NotFound(DadataError):
    code, exit_code = "not_found", 7
