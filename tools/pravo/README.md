# Интеграция с publication.pravo.gov.ru

## Статус

Рабочее и проверенное вживую (24.07.2026). Авторизация не нужна — открытый
публичный API. `search.py`, `document_info.py` и `download.py` прогнаны
против реального сервера и вернули корректные данные (совпадение
`pdfFileLength` из карточки документа с реальным размером скачанного файла).

## На основе чего написан код

Официальная документация: http://publication.pravo.gov.ru/help — прозаическое
описание методов (не OpenAPI/Swagger) с примерами запросов/ответов. Выжимка —
`docs/api-spec-summary.md`. Метод скачивания файла (`/file/pdf?eoNumber=`) в
документации не описан — найден вручную через браузер (кнопка "Загрузить
документ" на странице документа) и подтверждён: размер файла совпадает с
полем `pdfFileLength` из `/api/Document`.

Особенность окружения: HTTPS (`https://publication.pravo.gov.ru`) не отвечает
(зависает / пустой ответ), поэтому клиент по умолчанию использует HTTP —
сайт отдаёт тот же контент по обоим протоколам, просто HTTPS недоступен
именно из этой сети/окружения.

## Структура

```
tools/pravo/
  docs/
    api-spec-summary.md      эндпоинты, параметры, проверенные примеры
  scripts/
    pravo_client.py           клиент — вся логика вызовов API
    _common.py                 общие мелочи для CLI (JSON-вывод, обработка ошибок)
    search.py                  поиск документов (НПА) с фильтрами
    document_info.py            расширенные метаданные документа по eoNumber
    download.py                  скачать PDF документа по eoNumber
    blocks.py                     справочник: Блоки публикации
    categories.py                  справочник: Категории принявших органов
    authorities.py                  справочник: Принявшие органы
    document_types.py                справочник: Виды документов
    stats.py                          статистика опубликования по периодам
    requirements.txt
```

## Как использовать

```bash
pip install -r scripts/requirements.txt
```

```bash
# найти документ (число — короче диапазон результатов)
python scripts/search.py --number 44-ФЗ --number-search-type 3 --page-size 10

# по конкретному блоку/органу/дате
python scripts/search.py --block government --date-from 2024-01-01 --date-to 2024-01-31

# карточка документа по eoNumber из результатов поиска
python scripts/document_info.py 2600202104190001

# скачать сам файл
python scripts/download.py 2600202104190001 --out ./2600202104190001.pdf

# справочники (коды для параметров --block/--category/--authority-id/--document-type-id)
python scripts/blocks.py
python scripts/categories.py --block government
python scripts/authorities.py --block government
python scripts/document_types.py --authority-id 8005d8c9-4b6d-48d3-861a-2a37e69fccb3
```

`--page-size` принимает только 10, 30, 100 или 200 — сервер отклоняет любые
другие значения (проверено: `400 The value 'N' is invalid`).

## Важно

Скрипты не пишут в `docs/refs` автоматически. По правилу из `AGENTS.md`
сохранение первоисточника локально требует подтверждения пользователя —
скачанный через `download.py` файл переносится в `docs/refs` вручную, только
после этого подтверждения.
