# -*- coding: utf-8 -*-
"""Ошибки клиента. У каждой есть код (машиночитаемый), код возврата CLI и подсказка, что делать."""

AUTH_HINT = ("Нужен вход. Пользователь запускает в своём терминале: "
             "python tools/consultant-api/consultant.py auth login "
             "(логин и пароль вводит он сам, агенту их не передавать).")
SLOT_HINT = ("На учётной записи занято единственное подключение: скорее всего, открыт КонсультантПлюс в браузере. "
             "Нажмите там «Выйти» (это освобождает слот) или подождите до 30 минут простоя, затем повторите.")


class ConsultantError(Exception):
    code = "error"
    exit_code = 1
    default_hint = None

    def __init__(self, message, code=None, hint=None, data=None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.hint = hint or self.default_hint
        self.data = data

    def to_dict(self):
        err = {"code": self.code, "message": self.message}
        if self.hint:
            err["hint"] = self.hint
        if self.data:
            err["data"] = self.data
        return {"error": err}


class UsageError(ConsultantError):
    code, exit_code = "usage", 2


class AuthRequired(ConsultantError):
    code, exit_code, default_hint = "auth_required", 3, AUTH_HINT


class SlotBusy(ConsultantError):
    code, exit_code, default_hint = "slot_busy", 4, SLOT_HINT


class ServiceError(ConsultantError):
    code, exit_code = "service_error", 5


class NetworkError(ConsultantError):
    code, exit_code = "network_error", 6


class NotFound(ConsultantError):
    code, exit_code = "not_found", 7


class IncompleteExport(ConsultantError):
    code, exit_code = "export_incomplete", 8
