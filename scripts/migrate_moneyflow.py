"""Одноразовая миграция истории операций из Moneyflow (CSV-экспорт) в лист «Учёт»
профиля Pantalone.

Правила мэппинга и все принятые решения задокументированы в
MONEYFLOW_MIGRATE_SPEC.md — этот скрипт реализует их дословно, ничего не
додумывая по ходу. Если реальные данные не укладываются ни в одно из известных
правил, скрипт останавливается с ошибкой, а не угадывает.

Пишет только колонки A:M (13 шт.) — колонки N/O/P (Год/Месяц/Период) в самом
листе уже посчитаны формулами и трогать их не нужно (см. спеку, п.2).

Использование:
    PROFILE=pantalone poetry run python scripts/migrate_moneyflow.py Money_Flow_full.csv
        — сухой прогон (по умолчанию): парсит, валидирует, печатает превью и
          сводку, ничего не пишет в Google Sheets и не требует credentials.json.
    PROFILE=pantalone poetry run python scripts/migrate_moneyflow.py Money_Flow_full.csv --write
        — реальная запись в живую таблицу.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

ADJUSTMENT_NOTE = "Корректировка остатка"
WHO = "Семья"
KZT = "KZT"

# Счёт списания/зачисления — точный список из листа «Счета» (= new_sources.xlsx).
# Не берём из profiles/pantalone.yaml::seed_sources — та секция устарела (см. «Задачу 2»
# про обновление sqlite-счетов бота, которая пока не сделана).
VALID_ACCOUNTS = (
    "Общий счёт",
    "Kaspi Ярослав",
    "BCC Лика",
    "Депозит BCC Лика",
    "Kaspi Лики",
    "Freedom Лика",
    "Наличные",
    "Tinkoff Лика",
    "Tinkoff Ярослав",
    "Tinkoff Семья",
    "Tinkoff Кредитка Лики",
    "Tinkoff Кредитка Ярослава",
    "Tinkoff Кубышка Лики",
    "Tinkoff Кубышка Ярослава",
    "Долг Роме",
    "Долг Артёму",
)

VALID_WHO = ("Лика", "Ярослав", "Семья")

VALID_TYPES = (
    "Расход",
    "Доход",
    "Перевод",
    "Погашение долга / кредита",
    "Коррекция остатка",
)

# Счёт Moneyflow -> счёт в целевой таблице (решения из MONEYFLOW_MIGRATE_SPEC.md, п.1 и допущение про Депозит).
ACCOUNT_MAP = {
    "Кошелёчек": "Общий счёт",
    "Депозит": "Депозит BCC Лика",
}

INCOME_PARENT_CATEGORIES = {"Salary", "Business Income", "Etc."}
INCOME_CATEGORY = "Доходы"
INCOME_SUBCATEGORY = "Прочий доход"

# Переименования подкатегорий Moneyflow -> таблица (спека, таблица категорий).
CATEGORY_RENAMES: dict[tuple[str, str], tuple[str, str]] = {
    ("Еда вне дома", "Доставки"): ("Еда вне дома", "Доставка еды"),
    ("Транспорт", "Доставка"): ("Транспорт", "Курьер и доставка"),
    ("Интернет и связь", "Мобильная"): ("Интернет и связь", "Мобильная связь"),
    ("Одежда и обувь", "Одежда и аксессуары"): ("Киба", "Амуниция и одежда"),
    ("Киба", "Одежда и аксессуары"): ("Киба", "Амуниция и одежда"),
    ("Техника", "Бытовая"): ("Техника", "Бытовая техника"),
}

# Категория-заглушка для строк без подкатегории, которые не подпадают под другое
# специальное правило (спека, «Открытые вопросы», п.3).
FALLBACK_CATEGORY = ("Другое", "Прочий расход")

# Перевод: категория/подкатегория по типу счёта зачисления (спека, «мелкие допущения»).
TRANSFER_CATEGORY = "Переводы"
TRANSFER_SUBCATEGORY_BY_ACCOUNT_TYPE = {
    "Депозит": "Пополнение депозита",
    "Наличные": "Снятие наличных",
}
TRANSFER_SUBCATEGORY_DEFAULT = "Между своими счетами"

# Аккаунты Moneyflow -> "тип счёта" для правила выше (правая часть ACCOUNT_MAP).
ACCOUNT_TYPE_BY_NAME = {
    "Общий счёт": "Накопительный счёт",
    "Депозит BCC Лика": "Депозит",
    "Наличные": "Наличные",
}


@dataclass
class Row:
    date: str  # dd.mm.yyyy hh:mm
    type_: str
    who: str
    category: str
    subcategory: str
    debit_account: str
    credit_account: str
    amount: float
    currency: str
    rate: float
    amount_kzt: float
    comment: str
    op_id: str

    def as_list(self) -> list:
        return [
            self.date,
            self.type_,
            self.who,
            self.category,
            self.subcategory,
            self.debit_account,
            self.credit_account,
            self.amount,
            self.currency,
            self.rate,
            self.amount_kzt,
            self.comment,
            self.op_id,
        ]


class MigrationError(ValueError):
    """Строка CSV не подходит ни под одно известное правило мэппинга."""


def load_valid_category_pairs() -> set[tuple[str, str]]:
    profile_path = _REPO_ROOT / "profiles" / "pantalone.yaml"
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    pairs = set()
    for item in profile["seed_categories"]:
        for sub in item.get("subcategories", []):
            pairs.add((item["name"], sub))
    return pairs


def map_account(moneyflow_account: str) -> str:
    try:
        return ACCOUNT_MAP[moneyflow_account]
    except KeyError:
        raise MigrationError(f"Неизвестный счёт Moneyflow: {moneyflow_account!r}") from None


def map_expense_category(parent: str, sub: str) -> tuple[str, str]:
    if sub:
        return CATEGORY_RENAMES.get((parent, sub), (parent, sub))
    return FALLBACK_CATEGORY


def transfer_subcategory(credit_account: str) -> str:
    account_type = ACCOUNT_TYPE_BY_NAME.get(credit_account)
    return TRANSFER_SUBCATEGORY_BY_ACCOUNT_TYPE.get(account_type, TRANSFER_SUBCATEGORY_DEFAULT)


def parse_row(raw: dict[str, str]) -> Row:
    date_raw = raw["Дата"].strip()
    date = _reformat_date(date_raw)
    amount = float(raw["Сумма"])
    currency = raw["Валюта"].strip()
    if currency != KZT:
        raise MigrationError(f"Неожиданная валюта {currency!r} в строке от {date_raw}")

    parent = raw["Родительская категория"].strip()
    sub = raw["Подкатегория"].strip()
    comment = raw["Примечание"].strip()
    transfer_target_raw = raw["Перевод: Счёт"].strip()
    moneyflow_account = raw["Счёт"].strip()

    op_id = str(uuid.uuid4())

    if comment == ADJUSTMENT_NOTE and not parent:
        account = map_account(moneyflow_account)
        return Row(
            date=date,
            type_="Коррекция остатка",
            who=WHO,
            category="",
            subcategory="",
            debit_account=account,
            credit_account="",
            amount=amount,
            currency=KZT,
            rate=1,
            amount_kzt=amount,
            comment=comment,
            op_id=op_id,
        )

    if parent == "Кредиты":
        # ByBit-обмены — по решению из спеки трактуются как перевод между своими
        # счетами, а не как платёж по кредиту (в «Кредиты и долги» для этого нет
        # подходящей подкатегории). Счёт зачисления пуст — деньги по факту
        # покидают учитываемый периметр счетов.
        account = map_account(moneyflow_account)
        return Row(
            date=date,
            type_="Перевод",
            who=WHO,
            category=TRANSFER_CATEGORY,
            subcategory=TRANSFER_SUBCATEGORY_DEFAULT,
            debit_account=account,
            credit_account="",
            amount=abs(amount),
            currency=KZT,
            rate=1,
            amount_kzt=abs(amount),
            comment=comment,
            op_id=op_id,
        )

    if transfer_target_raw:
        source_account = map_account(moneyflow_account)
        target_account = map_account(transfer_target_raw)
        return Row(
            date=date,
            type_="Перевод",
            who=WHO,
            category=TRANSFER_CATEGORY,
            subcategory=transfer_subcategory(target_account),
            debit_account=source_account,
            credit_account=target_account,
            amount=abs(amount),
            currency=KZT,
            rate=1,
            amount_kzt=abs(amount),
            comment=comment,
            op_id=op_id,
        )

    if parent in INCOME_PARENT_CATEGORIES:
        return Row(
            date=date,
            type_="Доход",
            who=WHO,
            category=INCOME_CATEGORY,
            subcategory=INCOME_SUBCATEGORY,
            debit_account="",
            credit_account="Общий счёт",
            amount=amount,
            currency=KZT,
            rate=1,
            amount_kzt=amount,
            comment=comment,
            op_id=op_id,
        )

    if parent:
        category, subcategory = map_expense_category(parent, sub)
        account = map_account(moneyflow_account)
        return Row(
            date=date,
            type_="Расход",
            who=WHO,
            category=category,
            subcategory=subcategory,
            debit_account=account,
            credit_account="",
            amount=abs(amount),
            currency=KZT,
            rate=1,
            amount_kzt=abs(amount),
            comment=comment,
            op_id=op_id,
        )

    raise MigrationError(
        f"Не удалось классифицировать строку от {date_raw}: "
        f"нет категории, не перевод, не коррекция остатка"
    )


def _reformat_date(raw: str) -> str:
    # "2026-09-13 14:46:38" -> "13.09.2026 14:46"
    # Таймстемпы Moneyflow считаются уже локальными (Asia/Almaty), без сдвига
    # часового пояса — см. спеку, п.8.
    dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    return dt.strftime("%d.%m.%Y %H:%M")


def validate_row(row: Row, valid_category_pairs: set[tuple[str, str]]) -> list[str]:
    problems = []
    if row.type_ not in VALID_TYPES:
        problems.append(f"недопустимый тип операции: {row.type_!r}")
    if row.who not in VALID_WHO:
        problems.append(f"недопустимое значение «Кто»: {row.who!r}")
    if row.category and (row.category, row.subcategory) not in valid_category_pairs:
        problems.append(
            f"пары категория/подкатегория нет в справочнике: "
            f"{row.category!r} / {row.subcategory!r}"
        )
    if row.debit_account and row.debit_account not in VALID_ACCOUNTS:
        problems.append(f"недопустимый счёт списания: {row.debit_account!r}")
    if row.credit_account and row.credit_account not in VALID_ACCOUNTS:
        problems.append(f"недопустимый счёт зачисления: {row.credit_account!r}")
    return problems


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def build_rows(csv_path: Path) -> list[Row]:
    raw_rows = read_csv_rows(csv_path)
    valid_category_pairs = load_valid_category_pairs()

    rows = []
    errors = []
    for i, raw in enumerate(raw_rows, start=2):  # +2: заголовок = строка 1
        try:
            row = parse_row(raw)
        except MigrationError as exc:
            errors.append(f"CSV-строка {i}: {exc}")
            continue
        problems = validate_row(row, valid_category_pairs)
        if problems:
            errors.append(f"CSV-строка {i} ({raw['Дата']}): " + "; ".join(problems))
            continue
        rows.append(row)

    if errors:
        raise MigrationError(
            f"Найдено {len(errors)} проблемных строк, миграция остановлена:\n"
            + "\n".join(errors)
        )

    # CSV идёт от новых к старым — разворачиваем в хронологический порядок.
    rows.reverse()
    return rows


def print_preview(rows: list[Row]) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.type_] = counts.get(row.type_, 0) + 1

    print(f"Всего строк к переносу: {len(rows)}")
    for type_, count in sorted(counts.items()):
        print(f"  {type_}: {count}")
    print()
    print("Первые 5 и последние 5 строк (Дата | Тип | Категория/Подкатегория | Списание -> Зачисление | Сумма):")

    def _fmt(row: Row) -> str:
        return (
            f"  {row.date} | {row.type_} | {row.category}/{row.subcategory} | "
            f"{row.debit_account} -> {row.credit_account} | {row.amount} {row.currency}"
        )

    for row in rows[:5]:
        print(_fmt(row))
    if len(rows) > 10:
        print("  ...")
    for row in rows[-5:]:
        print(_fmt(row))


def write_to_sheet(rows: list[Row]) -> None:
    os.environ.setdefault("PROFILE", "pantalone")
    if os.environ["PROFILE"] != "pantalone":
        raise SystemExit(
            f"PROFILE={os.environ['PROFILE']!r}, а миграция рассчитана только на "
            f"профиль pantalone — прервано ради безопасности."
        )

    import gspread

    from willem.config import load_config

    config = load_config()
    client = gspread.service_account(filename=config.google_sheets_credentials_path)
    spreadsheet = client.open_by_key(config.google_sheets_spreadsheet_id)
    worksheet = spreadsheet.worksheet(config.sheet_name)

    existing = worksheet.col_values(1)  # колонка A
    existing_data_rows = max(len(existing) - 1, 0)  # минус заголовок
    if existing_data_rows > 0:
        raise SystemExit(
            f"В листе «{config.sheet_name}» уже есть {existing_data_rows} строк данных — "
            f"это не первый прогон. Останавливаюсь, чтобы не задвоить операции; "
            f"разберитесь вручную, что там, и при необходимости уберите эту проверку."
        )

    start_row = 2
    end_row = start_row + len(rows) - 1
    values = [row.as_list() for row in rows]
    worksheet.update(
        range_name=f"A{start_row}:M{end_row}",
        values=values,
        value_input_option="USER_ENTERED",
    )
    print(f"Записано {len(rows)} строк в '{config.sheet_name}' (A{start_row}:M{end_row}).")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="Путь к CSV-экспорту Moneyflow")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Реально записать в Google Sheets (по умолчанию — сухой прогон)",
    )
    args = parser.parse_args(argv)

    rows = build_rows(args.csv_path)
    print_preview(rows)

    if args.write:
        write_to_sheet(rows)
    else:
        print()
        print("Сухой прогон — ничего не записано. Запустите с --write для реальной записи.")


if __name__ == "__main__":
    main(sys.argv[1:])
