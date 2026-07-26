# API publication.pravo.gov.ru — выжимка спецификации

Официальный, открытый, только для чтения API раздела "Официальное опубликование"
(publication.pravo.gov.ru) "Официального интернет-портала правовой информации"
(pravo.gov.ru). Официальная документация: http://publication.pravo.gov.ru/help.

Проверено вживую 24.07.2026: `/api/Document`, `/api/PublicBlocks`,
`/api/Categories`, `/api/BlockStatistics`, `/api/Documents`, `/file/pdf` — все
вернули реальные данные, ровно в описанном формате.

## Доступ

- Базовый URL: `http://publication.pravo.gov.ru`
- Авторизация **не требуется** — открытый публичный API, только режим чтения.
- Формат ответа — всегда JSON (кроме `/file/pdf`, который отдаёт сам файл).
- HTTPS (`https://publication.pravo.gov.ru`) в этом окружении зависает при
  подключении (curl не получил ответа за 120 сек) — используется HTTP.
  При работе из другого окружения стоит попробовать HTTPS повторно.

## Эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/PublicBlocks?parent={code}` | Список Блоков публикации и подблоков |
| GET | `/api/Categories?block={code}` | Категории принявших органов по блоку |
| GET | `/api/SignatoryAuthorities?block=&category=` | Список принявших органов |
| GET | `/api/DocumentTypes?block=&category=&SignatoryAuthorityId=` | Виды документов |
| GET | `/api/Documents?<фильтры>` | Поиск документов (НПА), с пагинацией |
| GET | `/api/Document?eoNumber={eoNumber}` | Расширенные метаданные одного документа |
| GET | `/api/BlockStatistics/{period}` | Статистика опубликования (daily/weekly/monthly) |
| GET | `/file/pdf?eoNumber={eoNumber}` | **Сам файл документа**, PDF (вне `/api`) |

### GET /api/Documents (поиск) — основные параметры

Все параметры необязательны, можно комбинировать:

- `Block`, `Category`, `SignatoryAuthorityId`, `DocumentTypeId` (можно несколько) — фильтры по справочникам выше
- `Name`, `ComplexName`, `DocumentText` — текстовый поиск по названию/составному названию/тексту
- `Number`, `NumberSearchType` (0 точно, 1 начинается с, 2 заканчивается на, 3 содержит)
- `JdRegNumber`, `JdRegDateFrom`, `JdRegDateTo` — по регистрации в Минюсте
- `DocumentDateFrom`, `DocumentDateTo` — по дате подписания
- `PublishDateFrom`, `PublishDateTo` — по дате публикации
- `PeriodType` (`daily`/`weekly`/`monthly`/`day`) + `Date` — фиксированные периоды публикации
- `PageSize` (10/30/100/200), `Index` (номер страницы, с 1)
- `SortedBy` (0 дата подписания, 1 вид документа, 2 принявший орган, 3 номер, 4 дата опубликования, 5 номер опубликования), `SortDestination` (asc/desc)

Ответ: `{"items": [{...}], "itemsTotalCount", "itemsPerPage", "currentPage", "pagesTotalCount"}`.
Каждый `items[]` содержит `eoNumber`, `name`, `number`, `documentDate`, `complexName`,
`pdfFileLength`, `pagesCount` и т.д. — `eoNumber` используется дальше в `/api/Document`
и `/file/pdf`.

### GET /api/Document?eoNumber=

Расширенная карточка документа: `documentType`, `signatoryAuthorities[]`,
`jdRegNumber`, `pdfFileLength`, `zipFileLength` (приложения, если есть) и т.д.

### GET /file/pdf?eoNumber=

Отдаёт сам файл (`Content-Type: application/octet-stream`,
`Content-Disposition: attachment; filename=...pdf`). Размер совпадает с
`pdfFileLength` из карточки документа. Проверено: 200 OK, без авторизации.

## Пример полного цикла

```
GET /api/Documents?Number=3&NumberSearchType=0&PageSize=10
  → eoNumber = "2600202104190001"
GET /api/Document?eoNumber=2600202104190001
  → метаданные (название, орган, дата, размер файла)
GET /file/pdf?eoNumber=2600202104190001
  → сам PDF (123731 байт — совпало с pdfFileLength)
```

## Источник

http://publication.pravo.gov.ru/help — официальная страница документации API.
